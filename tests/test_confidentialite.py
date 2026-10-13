"""Aucun terme privé dans un dépôt public (spec rév. 4.4, § 5.5, § 8).

Les termes privés se déduisent des bases installées qui ne sont pas livrées
avec le hub, et de `private-terms` ; une base est publique si elle est livrée
avec un dépôt canonique, ou déclarée dans `public-bases`.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from okf_hub import confidentialite, setup_cmd
from okf_hub.config import HubConfig
from okf_hub.errors import INVALID_INPUT, ToolError
from okf_hub.registry import Registry
from okf_hub.tools import propose_tool

from conftest import git


def _local(hub_root, **cles):
    (hub_root / "hub-config.local.yaml").write_text(yaml.safe_dump(cles), encoding="utf-8")


@pytest.fixture
def hub_mixte(hub, make_bundle):
    """Une base publique (`retours`), une base privée (`base-privee`, corpus
    `prive-kb`), et un terme déclaré privé (`ACME`)."""
    hub_root, _ = hub
    make_bundle("retours", name="retours")
    make_bundle("base-privee", name="base-privee", corpus_dir="prive-kb")
    _local(hub_root, **{"public-bases": ["retours"], "private-terms": ["ACME"]})
    reg = Registry(HubConfig.load(hub_root))
    reg.scan()
    return hub_root, reg


def _args(base, **champs):
    return {
        "base": base, "type": "observation", "concerns": "kb_search tronque tôt",
        "content": "Le plafond de sortie coupe au troisième résultat.",
        "sources": ["constat de session"], "confidence": "medium",
        "submitted_by": "claude-code/opus", **champs,
    }


# --- calcul des termes ----------------------------------------------------------


def test_termes_prives_deduits_des_bases_installees(hub_mixte):
    _, reg = hub_mixte
    termes = confidentialite.termes_prives(reg)
    assert {"base-privee", "prive-kb", "ACME"} <= termes
    assert "knowledge" not in termes            # générique
    assert confidentialite.bases_publiques(reg.config) == {"retours"}


@pytest.mark.parametrize("texte", [
    "vu sur base-privee hier", "BASE-PRIVEE", "cowork-acme", "table acme_vw", "(acme)",
])
def test_le_motif_voit_un_terme_colle_par_un_separateur(hub_mixte, texte):
    _, reg = hub_mixte
    rx = confidentialite.motif(confidentialite.termes_prives(reg))
    assert confidentialite.trouver(rx, texte)


@pytest.mark.parametrize("texte", ["macmeX", "acme2", "base-priveeur"])
def test_le_motif_ignore_un_terme_pris_dans_un_mot(hub_mixte, texte):
    _, reg = hub_mixte
    rx = confidentialite.motif(confidentialite.termes_prives(reg))
    assert confidentialite.trouver(rx, texte) == []


# --- kb_propose (§ 5.5) ---------------------------------------------------------


@pytest.mark.parametrize("champ, valeur", [
    ("content", "Constaté en revue de base-privee."),
    ("concerns", "troncature sur prive-kb"),
    ("sources", ["session cowork-acme du 13/10"]),
    ("submitted_by", "claude-sonnet/cowork-acme"),
])
def test_base_publique_refuse_un_terme_prive_sans_rien_deposer(hub_mixte, champ, valeur):
    hub_root, reg = hub_mixte
    base = reg.get("retours")
    head = git(base.root, "rev-parse", "HEAD")

    with pytest.raises(ToolError) as exc:
        propose_tool.run(reg, _args("retours", **{champ: valeur}))

    assert exc.value.code == INVALID_INPUT
    assert "publique" in exc.value.message and "nom neutre" in exc.value.message
    assert git(base.root, "rev-parse", "HEAD") == head
    assert not list((base.root / "proposals" / "pending").glob("prop-*.md")) \
        if (base.root / "proposals" / "pending").is_dir() else True


def test_base_publique_accepte_un_texte_neutre(hub_mixte):
    _, reg = hub_mixte
    assert "Proposition déposée." in propose_tool.run(reg, _args("retours"))


def test_base_privee_accepte_ses_propres_noms(hub_mixte):
    _, reg = hub_mixte
    out = propose_tool.run(reg, _args("base-privee", content="Vu sur base-privee, chez ACME."))
    assert "Proposition déposée." in out


def test_la_description_annonce_les_bases_publiques(hub_mixte):
    _, reg = hub_mixte
    lignes = propose_tool.description(reg).splitlines()
    retours = next(l for l in lignes if l.startswith("- retours"))
    privee = next(l for l in lignes if l.startswith("- base-privee"))
    assert "DÉPÔT PUBLIC" in retours and "DÉPÔT PUBLIC" not in privee


# --- hooks git ------------------------------------------------------------------


@pytest.fixture
def depot(tmp_path, hub_mixte, monkeypatch):
    """Un dépôt public jetable où l'on commite, vu depuis le hub mixte."""
    hub_root, _ = hub_mixte
    d = tmp_path / "depot-public"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    (d / "a.md").write_text("neutre\n", encoding="utf-8")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "init")
    monkeypatch.chdir(d)
    monkeypatch.setenv("OKF_HUB_ROOT", str(hub_root))
    return d


def test_hook_index_refuse_un_ajout_prive(depot, capsys):
    (depot / "b.md").write_text("Rien à signaler.\n", encoding="utf-8")
    git(depot, "add", "b.md")
    assert confidentialite.main(["index"]) == 0

    (depot / "b.md").write_text("Rien à signaler.\nSauf sur base-privee.\n", encoding="utf-8")
    git(depot, "add", "b.md")
    assert confidentialite.main(["index"]) == 1
    assert "b.md : « base-privee »" in capsys.readouterr().err


def test_hook_index_voit_un_nom_de_fichier(depot):
    (depot / "notes-acme.md").write_text("neutre\n", encoding="utf-8")
    git(depot, "add", "-A")
    assert confidentialite.main(["index"]) == 1


def test_hook_message_ignore_les_commentaires(depot, tmp_path):
    msg = tmp_path / "MSG"
    msg.write_text("Corrige la troncature\n# base-privee (commentaire de git)\n", encoding="utf-8")
    assert confidentialite.main(["message", str(msg)]) == 0
    msg.write_text("Corrige la troncature vue sur base-privee\n", encoding="utf-8")
    assert confidentialite.main(["message", str(msg)]) == 1


def test_hook_poussee_examine_messages_auteurs_et_contenus(depot, monkeypatch, capsys):
    base_sha = git(depot, "rev-parse", "HEAD").strip()
    (depot / "c.md").write_text("neutre\n", encoding="utf-8")
    git(depot, "add", "-A")
    git(depot, "commit", "-q", "-m", "Vu chez ACME")
    tete = git(depot, "rev-parse", "HEAD").strip()

    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(f"refs/heads/main {tete} refs/heads/main {base_sha}\n"))
    assert confidentialite.main(["poussee"]) == 1
    assert "(auteur, message) : « ACME »" in capsys.readouterr().err

    # Une branche nouvelle pour le remote est examinée sur tout ce qu'aucun
    # remote ne connaît : une vieille branche locale ne repasse pas.
    monkeypatch.setattr("sys.stdin", io.StringIO(f"refs/heads/vieille {tete} refs/heads/vieille {confidentialite.ZERO}\n"))
    assert confidentialite.main(["poussee"]) == 1

    # Une suppression de branche ne publie rien.
    monkeypatch.setattr("sys.stdin", io.StringIO(f"(delete) {confidentialite.ZERO} refs/heads/x {tete}\n"))
    assert confidentialite.main(["poussee"]) == 0


def test_le_vrai_hook_bloque_le_commit(depot, hub_mixte):
    """Bout en bout : `.githooks/` du hub, activé par core.hooksPath."""
    from conftest import HUB_ROOT
    git(depot, "config", "core.hooksPath", str(HUB_ROOT / ".githooks"))
    (depot / "d.md").write_text("Constaté sur base-privee.\n", encoding="utf-8")
    git(depot, "add", "-A")
    proc = subprocess.run(
        ["git", "-C", str(depot), "-c", "user.name=t", "-c", "user.email=t@l",
         "commit", "-q", "-m", "ajout"],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "base-privee" in proc.stderr


# --- activation -----------------------------------------------------------------


def test_setup_active_les_hooks_du_hub_et_des_bases_publiques(hub_mixte):
    hub_root, reg = hub_mixte
    (hub_root / ".githooks").mkdir()
    git(hub_root, "init", "-q", "-b", "main")

    etape = setup_cmd.activate_hooks(hub_root)

    assert etape.status == "ok"
    attendu = str(hub_root / ".githooks")
    assert git(hub_root, "config", "core.hooksPath").strip() == attendu
    assert git(reg.get("retours").root, "config", "core.hooksPath").strip() == attendu
    proc = subprocess.run(
        ["git", "-C", str(reg.get("base-privee").root), "config", "core.hooksPath"],
        capture_output=True, text=True,
    )
    assert proc.stdout.strip() == ""           # base privée : pas concernée
