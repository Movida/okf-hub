"""Synchronisation remote au démarrage (§ 4.5, « [v1+] »).

Le test central est `test_divergence_signalee_jamais_ecrasee` : une proposition
locale commitée par `kb_propose` mais jamais poussée, pendant qu'un tiers fait
évoluer le dépôt canonique, ne doit **jamais** être écrasée par la
synchronisation — seulement signalée.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import HUB_ROOT, git

from okf_hub import hublog, remote_sync
from okf_hub.config import HubConfig
from okf_hub.errors import BASE_BUSY, ToolError

MANIFEST = {
    "bundle-spec": "0.1",
    "name": "distante",
    "title": "Base distante",
    "description": "Base avec un remote, pour les tests de synchronisation.",
    "corpus-dir": "knowledge",
    "governance": {"rules": "./GOVERNANCE.md"},
}


def _ecrire_bundle(root: Path, contenu: str) -> None:
    (root / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / "okf-bundle.yaml").write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")
    (root / "GOVERNANCE.md").write_text("# Gouvernance\n", encoding="utf-8")
    (root / "knowledge" / "a.md").write_text(contenu, encoding="utf-8")


@pytest.fixture
def canonique(tmp_path) -> Path:
    """Un dépôt canonique nu, avec un commit initial."""
    travail = tmp_path / "travail-canonique"
    _ecrire_bundle(travail, "# A\n\nversion initiale\n")
    git(travail, "init", "-q", "-b", "main")
    git(travail, "add", "-A")
    git(travail, "commit", "-q", "-m", "version initiale")

    bare = tmp_path / "canonique.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(travail), str(bare)], check=True
    )
    return bare


@pytest.fixture
def bare_vide(tmp_path) -> Path:
    bare = tmp_path / "vide.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True
    )
    return bare


def _pousser_nouveau_commit(
    canonique: Path, tmp_path: Path, contenu: str, nom: str = "nouvelle-version"
) -> None:
    """Simule une évolution du dépôt canonique par un tiers, hors du hub."""
    clone = tmp_path / f"tiers-{nom}"
    subprocess.run(["git", "clone", "--quiet", str(canonique), str(clone)], check=True)
    (clone / "knowledge" / "a.md").write_text(contenu, encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", nom)
    git(clone, "push", "-q", "origin", "HEAD")


@pytest.fixture
def base_clonee(hub, canonique) -> Path:
    """La base `distante`, clonée dans bases_dir depuis le dépôt canonique."""
    _, bases = hub
    cible = bases / "distante"
    subprocess.run(["git", "clone", "--quiet", str(canonique), str(cible)], check=True)
    return cible


# --- cas de base : pas de remote, ou rien à faire ------------------------------


def test_base_sans_remote_est_ignoree(hub, make_bundle):
    """Le cas majoritaire : une base semée depuis bundles/ n'a pas de remote."""
    _, bases = hub
    make_bundle("locale")
    sha_avant = git(bases / "locale", "rev-parse", "HEAD").strip()

    remote_sync.sync_one(bases / "locale")

    assert git(bases / "locale", "rev-parse", "HEAD").strip() == sha_avant


def test_deja_synchronisee_ne_fait_rien(base_clonee):
    sha_avant = git(base_clonee, "rev-parse", "HEAD").strip()
    remote_sync.sync_one(base_clonee)
    assert git(base_clonee, "rev-parse", "HEAD").strip() == sha_avant


def test_aucune_branche_amont_configuree(hub, make_bundle, bare_vide):
    """Un remote sans tracking configuré (`@{u}` absent) : fetch réussit, rien de plus."""
    _, bases = hub
    b = make_bundle("sans-amont")
    git(b.root, "remote", "add", "origin", str(bare_vide))
    sha_avant = git(b.root, "rev-parse", "HEAD").strip()

    remote_sync.sync_one(b.root)

    assert git(b.root, "rev-parse", "HEAD").strip() == sha_avant


# --- fast-forward ---------------------------------------------------------------


def test_fast_forward_simple(base_clonee, canonique, tmp_path):
    sha_avant = git(base_clonee, "rev-parse", "HEAD").strip()
    _pousser_nouveau_commit(canonique, tmp_path, "# A\n\nversion mise a jour\n")

    remote_sync.sync_one(base_clonee)

    sha_apres = git(base_clonee, "rev-parse", "HEAD").strip()
    assert sha_apres != sha_avant
    assert (base_clonee / "knowledge" / "a.md").read_text(
        encoding="utf-8"
    ) == "# A\n\nversion mise a jour\n"
    assert git(base_clonee, "status", "--porcelain").strip() == ""


def test_local_en_avance_rien_a_tirer(base_clonee):
    """Une proposition locale commitée : jamais de push, en v0 (§ 4.5)."""
    (base_clonee / "knowledge" / "b.md").write_text("# B\n\nlocal\n", encoding="utf-8")
    git(base_clonee, "add", "-A")
    git(base_clonee, "commit", "-q", "-m", "proposition locale")
    sha_avant = git(base_clonee, "rev-parse", "HEAD").strip()

    remote_sync.sync_one(base_clonee)

    assert git(base_clonee, "rev-parse", "HEAD").strip() == sha_avant


def test_divergence_signalee_jamais_ecrasee(hub, base_clonee, canonique, tmp_path):
    """Le scénario que la contrainte héritée interdit de mal traiter."""
    hub_root, _ = hub
    config = HubConfig.load(hub_root)

    (base_clonee / "knowledge" / "local.md").write_text("# Local\n", encoding="utf-8")
    git(base_clonee, "add", "-A")
    git(base_clonee, "commit", "-q", "-m", "proposition locale")
    sha_local = git(base_clonee, "rev-parse", "HEAD").strip()

    _pousser_nouveau_commit(canonique, tmp_path, "# A\n\nversion du tiers\n")

    hublog.configure(config.log_file)
    try:
        remote_sync.sync_one(base_clonee)
    finally:
        hublog.close()

    assert git(base_clonee, "rev-parse", "HEAD").strip() == sha_local
    assert git(base_clonee, "status", "--porcelain").strip() == ""
    assert "divergence" in config.log_file.read_text(encoding="utf-8")


# --- pannes non bloquantes ------------------------------------------------------


def test_remote_injoignable_ne_bloque_pas(hub, base_clonee, canonique):
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    canonique.rename(canonique.with_name("disparu.git"))

    hublog.configure(config.log_file)
    try:
        remote_sync.sync_one(base_clonee)  # ne doit jamais lever
    finally:
        hublog.close()

    assert "injoignable" in config.log_file.read_text(encoding="utf-8")


def test_base_occupee_ne_bloque_pas_le_demarrage(
    hub, base_clonee, canonique, tmp_path, monkeypatch
):
    """Une session `kb_propose` en cours (verrou tenu) : on ignore, on ne bloque pas."""
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    _pousser_nouveau_commit(canonique, tmp_path, "# A\n\nversion mise a jour\n")
    sha_avant = git(base_clonee, "rev-parse", "HEAD").strip()

    @contextlib.contextmanager
    def _toujours_occupe(repo, timeout=None):
        raise ToolError(BASE_BUSY, "base occupée par une autre écriture, réessayez")
        yield  # pragma: no cover

    monkeypatch.setattr(remote_sync, "base_lock", _toujours_occupe)

    hublog.configure(config.log_file)
    try:
        remote_sync.sync_one(base_clonee)
    finally:
        hublog.close()

    assert git(base_clonee, "rev-parse", "HEAD").strip() == sha_avant
    assert "occupée" in config.log_file.read_text(encoding="utf-8")


# --- sync_all : parcours de toutes les bases installées ------------------------


def test_sync_all_traite_chaque_base_selon_son_cas(
    hub, make_bundle, base_clonee, canonique, tmp_path
):
    make_bundle("locale")  # sans remote : ne doit pas faire échouer le parcours
    _pousser_nouveau_commit(canonique, tmp_path, "# A\n\nversion mise a jour\n")
    hub_root, _ = hub
    config = HubConfig.load(hub_root)

    remote_sync.sync_all(config)

    assert (base_clonee / "knowledge" / "a.md").read_text(
        encoding="utf-8"
    ) == "# A\n\nversion mise a jour\n"


def test_sync_all_bases_dir_absent_ne_gene_pas(tmp_path):
    (tmp_path / "hub-config.yaml").write_text("bases-dir: ./bases\n", encoding="utf-8")
    config = HubConfig.load(tmp_path)
    remote_sync.sync_all(config)  # ne doit pas lever


# --- configuration ---------------------------------------------------------------


def test_sync_on_start_defaut_active(hub):
    hub_root, _ = hub
    assert HubConfig.load(hub_root).sync_on_start is True


def test_sync_on_start_non_booleen_rejete(hub):
    hub_root, _ = hub
    (hub_root / "hub-config.yaml").write_text(
        yaml.safe_dump({"bases-dir": "./bases", "sync-on-start": "oui"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="sync-on-start"):
        HubConfig.load(hub_root)


# --- intégration au démarrage du serveur ----------------------------------------


def _lancer_le_serveur(hub_root: Path) -> subprocess.CompletedProcess:
    """Démarre le vrai point d'entrée, qui rend la main sur stdin fermé."""
    return subprocess.run(
        [sys.executable, "-m", "okf_hub", "--hub-root", str(hub_root)],
        input="",
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(HUB_ROOT / "src"), "PATH": os.environ["PATH"]},
    )


def test_le_serveur_synchronise_au_demarrage(hub, base_clonee, canonique, tmp_path):
    hub_root, _ = hub
    _pousser_nouveau_commit(canonique, tmp_path, "# A\n\nversion mise a jour\n")

    _lancer_le_serveur(hub_root)

    assert (base_clonee / "knowledge" / "a.md").read_text(
        encoding="utf-8"
    ) == "# A\n\nversion mise a jour\n"


def test_sync_on_start_false_desactive(hub, base_clonee, canonique, tmp_path):
    hub_root, _ = hub
    (hub_root / "hub-config.yaml").write_text(
        yaml.safe_dump(
            {"bases-dir": "./bases", "log-file": "./hub.log", "sync-on-start": False}
        ),
        encoding="utf-8",
    )
    sha_avant = git(base_clonee, "rev-parse", "HEAD").strip()
    _pousser_nouveau_commit(canonique, tmp_path, "# A\n\nversion mise a jour\n")

    _lancer_le_serveur(hub_root)

    assert git(base_clonee, "rev-parse", "HEAD").strip() == sha_avant
