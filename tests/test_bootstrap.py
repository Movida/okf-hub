"""Déploiement des bases livrées avec le hub (`bundles/` → `bases/`).

Le test central est `test_deux_deploiements_concurrents_ne_produisent_qu_une_base` :
« premier lancement » n'est pas un événement unique, il y a une instance par
client connecté (§ 4.4). Deux clients qui démarrent ensemble sur une
installation neuve exécutent ce code en même temps sur le même répertoire.
"""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from conftest import HUB_ROOT, git

from okf_hub import bootstrap, hublog
from okf_hub.config import HubConfig
from okf_hub.registry import Registry


@pytest.fixture
def source(tmp_path) -> Path:
    """Un bundle livré minimal, dans un `bundles/` jetable."""
    racine = tmp_path / "bundles" / "livree"
    (racine / "knowledge").mkdir(parents=True)
    (racine / "okf-bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "bundle-spec": "0.1",
                "name": "livree",
                "title": "Base livrée",
                "description": "Bundle livré avec le hub pour les tests.",
                "corpus-dir": "knowledge",
                "governance": {"rules": "./GOVERNANCE.md"},
            }
        ),
        encoding="utf-8",
    )
    (racine / "GOVERNANCE.md").write_text("# Gouvernance\n", encoding="utf-8")
    (racine / "knowledge" / "a.md").write_text("# A\n\nmot-temoin\n", encoding="utf-8")
    return racine


@pytest.fixture
def hub_avec_bundles(tmp_path, source):
    """Un hub dont `bundles/` contient le bundle livré, et `bases/` est vide."""
    hub_root = tmp_path
    (hub_root / "bases").mkdir()
    (hub_root / "hub-config.yaml").write_text(
        yaml.safe_dump({"bases-dir": "./bases", "log-file": "./hub.log"}), encoding="utf-8"
    )
    return hub_root


# --- déploiement --------------------------------------------------------------


def test_la_base_deployee_est_un_depot_git_autonome(hub_avec_bundles):
    """L'exigence qui justifie tout ce module.

    Si la base n'était qu'un sous-répertoire du dépôt du hub, `git -C` remonterait
    au dépôt englobant et kb_propose commiterait sur la branche du hub.
    """
    config = HubConfig.load(hub_avec_bundles)
    assert bootstrap.deploy_missing(config) == ["livree"]

    cible = config.bases_dir / "livree"
    assert (cible / ".git").is_dir()
    # `--show-toplevel` remonte au dépôt englobant s'il n'y en a pas ici.
    toplevel = git(cible, "rev-parse", "--show-toplevel").strip()
    assert Path(toplevel).resolve() == cible.resolve()
    assert git(cible, "log", "-1", "--format=%s").startswith("Déploiement de livree")
    assert git(cible, "status", "--porcelain").strip() == ""


def test_le_corpus_deploye_est_celui_de_la_source(hub_avec_bundles, source):
    config = HubConfig.load(hub_avec_bundles)
    bootstrap.deploy_missing(config)
    depuis = (source / "knowledge" / "a.md").read_text(encoding="utf-8")
    vers = (config.bases_dir / "livree" / "knowledge" / "a.md").read_text(encoding="utf-8")
    assert depuis == vers


def test_la_base_deployee_est_decouverte(hub_avec_bundles):
    config = HubConfig.load(hub_avec_bundles)
    bootstrap.deploy_missing(config)
    registre = Registry(config)
    registre.scan()
    assert registre.names() == ["livree"]


def test_deuxieme_appel_sans_effet(hub_avec_bundles):
    """Idempotence : le déploiement tourne à chaque lancement du serveur."""
    config = HubConfig.load(hub_avec_bundles)
    assert bootstrap.deploy_missing(config) == ["livree"]
    sha = git(config.bases_dir / "livree", "rev-parse", "HEAD")

    assert bootstrap.deploy_missing(config) == []
    assert git(config.bases_dir / "livree", "rev-parse", "HEAD") == sha


def test_une_base_existante_n_est_jamais_ecrasee(hub_avec_bundles):
    """Elle a pu recevoir des propositions et des résolutions."""
    config = HubConfig.load(hub_avec_bundles)
    bootstrap.deploy_missing(config)

    temoin = config.bases_dir / "livree" / "knowledge" / "ajoute-apres.md"
    temoin.write_text("# Ajouté après déploiement\n", encoding="utf-8")

    assert bootstrap.deploy_missing(config) == []
    assert temoin.is_file()


def test_echec_de_deploiement_non_bloquant(hub_avec_bundles, monkeypatch):
    """Un hub sans sa base livrée fonctionne : on journalise, on ne lève pas."""
    config = HubConfig.load(hub_avec_bundles)

    def explose(*_args, **_kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr(bootstrap, "deploy_one", explose)
    assert bootstrap.deploy_missing(config) == []


def test_bundles_absent_ne_gene_pas(tmp_path):
    (tmp_path / "bases").mkdir()
    (tmp_path / "hub-config.yaml").write_text("bases-dir: ./bases\n", encoding="utf-8")
    config = HubConfig.load(tmp_path)
    assert bootstrap.available(tmp_path) == {}
    assert bootstrap.deploy_missing(config) == []


# --- concurrence --------------------------------------------------------------


def test_deux_deploiements_concurrents_ne_produisent_qu_une_base(hub_avec_bundles, source):
    """Deux clients qui démarrent ensemble sur une installation neuve.

    Le verrou de base ne peut rien : son fichier vit dans le bundle, qui n'existe
    pas encore. La sérialisation repose sur `os.rename()`, atomique.
    """
    config = HubConfig.load(hub_avec_bundles)
    cible = config.bases_dir / "livree"

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultats = list(
            pool.map(lambda _: bootstrap.deploy_one(source, cible), range(8))
        )

    # Un seul gagnant ; les sept autres constatent et s'effacent.
    assert sum(resultats) == 1
    assert git(cible, "status", "--porcelain").strip() == ""
    assert len(git(cible, "log", "--format=%H").split()) == 1


def test_aucun_chantier_ne_survit(hub_avec_bundles, source):
    """Les répertoires de construction sont nettoyés, gagnant ou perdant."""
    config = HubConfig.load(hub_avec_bundles)
    cible = config.bases_dir / "livree"

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: bootstrap.deploy_one(source, cible), range(6)))

    restes = [p for p in config.bases_dir.iterdir() if p.name.startswith(".okf-deploy-")]
    assert restes == []


def test_un_chantier_en_cours_n_est_pas_decouvert(hub_avec_bundles, source):
    """Un bundle à moitié copié ne doit pas être enregistré par un scan concurrent.

    C'est ce que garantit le préfixe `.` du répertoire de construction, couplé au
    saut des répertoires cachés par la découverte (§ 4.2).
    """
    config = HubConfig.load(hub_avec_bundles)
    chantier = config.bases_dir / ".okf-deploy-en-cours"
    chantier.mkdir()
    (chantier / "okf-bundle.yaml").write_text(
        (source / "okf-bundle.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (chantier / "GOVERNANCE.md").write_text("# G\n", encoding="utf-8")
    (chantier / "knowledge").mkdir()

    registre = Registry(config)
    rapport = registre.scan()
    assert registre.names() == []
    assert rapport.invalid == []  # ni enregistré, ni signalé comme invalide


# --- intégration au démarrage du serveur --------------------------------------


def _lancer_le_serveur(hub_root: Path, arguments: str) -> subprocess.CompletedProcess:
    """Démarre le vrai point d'entrée, qui rend la main sur stdin fermé."""
    return subprocess.run(
        [sys.executable, "-m", "okf_hub", "--hub-root", str(hub_root)],
        input=arguments,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(HUB_ROOT / "src"), "PATH": __import__("os").environ["PATH"]},
    )


def test_le_serveur_deploie_au_demarrage(hub_avec_bundles):
    """Une installation neuve ne démarre pas muette."""
    config = HubConfig.load(hub_avec_bundles)
    assert not (config.bases_dir / "livree").exists()

    _lancer_le_serveur(hub_avec_bundles, "")

    assert (config.bases_dir / "livree" / ".git").is_dir()
    registre = Registry(config)
    registre.scan()
    assert "livree" in registre.names()


def test_bootstrap_bundles_false_desactive_le_deploiement(hub_avec_bundles):
    """Un opérateur peut vouloir maîtriser entièrement le contenu de bases-dir."""
    (hub_avec_bundles / "hub-config.yaml").write_text(
        yaml.safe_dump(
            {"bases-dir": "./bases", "log-file": "./hub.log", "bootstrap-bundles": False}
        ),
        encoding="utf-8",
    )
    config = HubConfig.load(hub_avec_bundles)
    assert config.bootstrap_bundles is False

    _lancer_le_serveur(hub_avec_bundles, "")
    assert not (config.bases_dir / "livree").exists()


def test_bootstrap_bundles_non_booleen_rejete(hub_avec_bundles):
    (hub_avec_bundles / "hub-config.yaml").write_text(
        yaml.safe_dump({"bases-dir": "./bases", "bootstrap-bundles": "oui"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bootstrap-bundles"):
        HubConfig.load(hub_avec_bundles)


# --- CLI ----------------------------------------------------------------------


def _bootstrap_cli(hub_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "okf_hub.bootstrap", "--hub-root", str(hub_root), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(HUB_ROOT / "src"), "PATH": __import__("os").environ["PATH"]},
    )


def test_cli_list(hub_avec_bundles):
    sortie = _bootstrap_cli(hub_avec_bundles, "--list")
    assert sortie.returncode == 0
    assert "livree" in sortie.stdout and "absente" in sortie.stdout


def test_cli_deploie_puis_ignore(hub_avec_bundles):
    premier = _bootstrap_cli(hub_avec_bundles)
    assert premier.returncode == 0
    assert "+ livree déployée" in premier.stdout

    second = _bootstrap_cli(hub_avec_bundles)
    assert "déjà déployée" in second.stdout


def test_cli_force_reecrit(hub_avec_bundles):
    config = HubConfig.load(hub_avec_bundles)
    _bootstrap_cli(hub_avec_bundles)
    temoin = config.bases_dir / "livree" / "knowledge" / "local.md"
    temoin.write_text("# local\n", encoding="utf-8")

    sortie = _bootstrap_cli(hub_avec_bundles, "--force")
    assert sortie.returncode == 0
    assert "réécrite" in sortie.stdout
    assert not temoin.exists()


def test_cli_bundle_inconnu(hub_avec_bundles):
    sortie = _bootstrap_cli(hub_avec_bundles, "fantome")
    assert sortie.returncode == 3
    assert "inconnu" in sortie.stderr


# --- bases à dépôt canonique : clonées, jamais semées --------------------------


@pytest.fixture
def amont(tmp_path) -> str:
    """Un dépôt canonique local, qui tient lieu de GitHub."""
    origine = tmp_path / "canonique.git"
    travail = tmp_path / "travail"
    (travail / "knowledge").mkdir(parents=True)
    (travail / "okf-bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "bundle-spec": "0.1",
                "name": "livree",
                "title": "Base livrée",
                "description": "Version canonique, enrichie par les sessions.",
                "corpus-dir": "knowledge",
                "governance": {"rules": "./GOVERNANCE.md"},
            }
        ),
        encoding="utf-8",
    )
    (travail / "GOVERNANCE.md").write_text("# Gouvernance\n", encoding="utf-8")
    (travail / "knowledge" / "a.md").write_text(
        "# A\n\ncontenu venu du depot canonique\n", encoding="utf-8"
    )
    git(travail, "init", "-q", "-b", "main")
    git(travail, "add", "-A")
    git(travail, "commit", "-q", "-m", "contenu canonique")
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(travail), str(origine)], check=True
    )
    return str(origine)


def _declarer_amont(hub_root: Path, url: str) -> None:
    (hub_root / "bundles" / "upstreams.yaml").write_text(
        yaml.safe_dump({"livree": url}), encoding="utf-8"
    )


def test_une_base_avec_amont_est_clonee_pas_semee(hub_avec_bundles, amont):
    """C'est le contenu du dépôt canonique qui doit arriver, pas la graine."""
    _declarer_amont(hub_avec_bundles, amont)
    config = HubConfig.load(hub_avec_bundles)

    assert bootstrap.deploy_missing(config) == ["livree"]
    cible = config.bases_dir / "livree"
    assert "contenu venu du depot canonique" in (cible / "knowledge" / "a.md").read_text(
        encoding="utf-8"
    )
    # L'histoire est celle de l'amont : une contribution locale est remontable.
    assert git(cible, "log", "-1", "--format=%s").strip() == "contenu canonique"
    assert git(cible, "remote", "get-url", "origin").strip() == amont


def test_un_clone_impossible_ne_seme_jamais(hub_avec_bundles):
    """Absente vaut mieux qu'orpheline.

    Semer une base qui a un dépôt canonique produirait une histoire sans rapport
    avec la sienne : toute proposition déposée dessus serait irrécupérable
    (« refusing to merge unrelated histories »).
    """
    _declarer_amont(hub_avec_bundles, str(hub_avec_bundles / "depot-inexistant.git"))
    config = HubConfig.load(hub_avec_bundles)

    assert bootstrap.deploy_missing(config) == []
    assert not (config.bases_dir / "livree").exists()
    assert [p for p in config.bases_dir.iterdir() if p.name.startswith(".okf-deploy-")] == []


def test_le_journal_dit_comment_rattraper(hub_avec_bundles):
    """Un échec silencieux laisserait l'opérateur sans base et sans explication."""
    _declarer_amont(hub_avec_bundles, str(hub_avec_bundles / "absent.git"))
    config = HubConfig.load(hub_avec_bundles)

    # Le journal du hub n'est pas ouvert par défaut dans le processus de test.
    hublog.configure(config.log_file)
    try:
        bootstrap.deploy_missing(config)
    finally:
        hublog.close()

    journal = config.log_file.read_text(encoding="utf-8")
    assert "orpheline" in journal
    assert "git clone" in journal


def test_sans_amont_declare_la_base_est_semee(hub_avec_bundles):
    """Le cas d'okf-hub-guide : source de vérité dans le dépôt du hub."""
    config = HubConfig.load(hub_avec_bundles)
    assert bootstrap.upstreams(hub_avec_bundles) == {}
    assert bootstrap.deploy_missing(config) == ["livree"]
    assert git(config.bases_dir / "livree", "log", "-1", "--format=%s").startswith(
        "Déploiement de livree"
    )


def test_upstreams_illisible_n_empeche_pas_le_semis(hub_avec_bundles):
    (hub_avec_bundles / "bundles" / "upstreams.yaml").write_text(
        ": : pas du YAML\n", encoding="utf-8"
    )
    config = HubConfig.load(hub_avec_bundles)
    assert bootstrap.upstreams(hub_avec_bundles) == {}
    assert bootstrap.deploy_missing(config) == ["livree"]


def test_cli_list_montre_l_origine(hub_avec_bundles, amont):
    _declarer_amont(hub_avec_bundles, amont)
    sortie = _bootstrap_cli(hub_avec_bundles, "--list")
    assert "clone de" in sortie.stdout
