"""Revue en mode agent (spec rév. 4.8, § 7.1 étape 5 bis, § 7.2).

Par la ligne de commande `okf-review resolve --agent`, comme la lancera une
tâche de l'opérateur : deux clés, règles fixes vérifiées par l'outil avant toute
résolution, trailer `Reviewed-By: agent:<id>`.
"""

from __future__ import annotations

import json

import pytest
import yaml

from okf_hub import review
from okf_hub.manifest import load_manifest
from okf_hub.tools import propose_tool

from conftest import git

DOC = (
    "---\ntitle: Procédure SSO\nverified:\n  - at: 2025-01-01\n    by: relecteur\n---\n\n"
    "# Procédure SSO\n\n## Reconnexion\n\nCliquer sur « réauthentifier ».\n"
)
SOURCE = "https://exemple.org/notes/sso-3-2"


def _local(hub_root, **cles):
    (hub_root / "hub-config.local.yaml").write_text(yaml.safe_dump(cles), encoding="utf-8")


@pytest.fixture
def poste(hub, make_bundle, registry):
    """`base-p` partagée, `base-q` privée ; toutes deux en `review: agent`, approuvées."""
    hub_root, _ = hub
    gouv = {"rules": "./GOVERNANCE.md", "review": "agent"}
    for nom in ("base-p", "base-q"):
        b = make_bundle(nom, name=nom, git_init=False, governance=gouv)
        b.doc("sso.md", DOC)
        b.init_git()
    _local(hub_root, **{"shared-bases": ["base-p"], "agent-review": ["base-p", "base-q"]})
    registry.config = registry.config.__class__.load(hub_root)
    registry.scan()
    return hub_root, registry


def _depose(registry, base, sources=(SOURCE,)) -> str:
    out = propose_tool.run(registry, {
        "base": base, "type": "correction", "concerns": "reconnexion SSO",
        "content": "Le bouton a été déplacé dans le menu profil depuis la 3.2.",
        "sources": list(sources), "confidence": "high", "submitted_by": "session-test",
    })
    return next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("id :"))


def _plan(tmp_path, pid, edits, nom="plan.json", resolution="accepted", into=("sso.md",)):
    chemin = tmp_path / nom
    res = {"id": pid, "resolution": resolution}
    if resolution == "accepted":
        res["integrated_into"] = list(into)
    else:
        res["reason"] = "hors périmètre"
    chemin.write_text(json.dumps({
        "summary": "reconnexion SSO", "reviewed_by": "ignoré en mode agent",
        "resolutions": [res], "edits": edits,
    }), encoding="utf-8")
    return chemin


def _resolve(hub_root, base, plan, agent="rangeur", *extra):
    args = ["--hub-root", str(hub_root), "resolve", base, "--plan", str(plan)]
    if agent is not None:
        args += ["--agent", agent]
    return review.main(args + list(extra))


def _ajout(texte=f"\n## Depuis la 3.2\n\nLe bouton est dans le menu profil ([source]({SOURCE})).\n"):
    return [{"path": "sso.md", "append": texte}]


def test_manifeste_accepte_review_agent(poste):
    _, registry = poste
    assert registry.get("base-p").manifest.review == "agent"


def test_ajout_source_dans_une_base_partagee(poste, tmp_path, capsys):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    assert _resolve(hub_root, "base-p", _plan(tmp_path, pid, _ajout())) == 0
    message = git(registry.get("base-p").root, "log", "-1", "--format=%B")
    assert "Reviewed-By: agent:rangeur" in message
    assert "ignoré en mode agent" not in message


def test_fraicheur_modifiable_dans_une_base_partagee(poste, tmp_path):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    edits = _ajout() + [{"path": "sso.md", "frontmatter": {"verified": [{"at": "2026-11-15", "by": "rangeur"}]}}]
    assert _resolve(hub_root, "base-p", _plan(tmp_path, pid, edits)) == 0


def test_modification_refusee_dans_une_base_partagee(poste, tmp_path, capsys):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    edits = [{"path": "sso.md", "section": "Reconnexion",
              "content": f"## Reconnexion\n\nMenu profil ([source]({SOURCE})).\n"}]
    assert _resolve(hub_root, "base-p", _plan(tmp_path, pid, edits)) == 4
    assert "ajouts seulement" in capsys.readouterr().err
    assert pid in {p.id for p in review.load_pending(registry.get("base-p"))}


def test_modification_permise_dans_une_base_privee(poste, tmp_path):
    hub_root, registry = poste
    pid = _depose(registry, "base-q")
    edits = [{"path": "sso.md", "section": "Reconnexion", "content": "## Reconnexion\n\nMenu profil.\n"}]
    assert _resolve(hub_root, "base-q", _plan(tmp_path, pid, edits)) == 0


def test_ajout_sans_source_refuse_dans_une_base_partagee(poste, tmp_path, capsys):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    plan = _plan(tmp_path, pid, _ajout("\n## Depuis la 3.2\n\nLe bouton est dans le menu profil.\n"))
    assert _resolve(hub_root, "base-p", plan) == 4
    assert "sources" in capsys.readouterr().err


def test_document_vide_refuse(poste, tmp_path, capsys):
    hub_root, registry = poste
    pid = _depose(registry, "base-q")
    plan = _plan(tmp_path, pid, [{"path": "sso.md", "content": "---\ntitle: Procédure SSO\n---\n"}])
    assert _resolve(hub_root, "base-q", plan) == 4
    assert "vidé" in capsys.readouterr().err


@pytest.mark.parametrize("cible", ["AGENTS.md", "sous/CLAUDE.md"])
def test_fichiers_de_gouvernance_hors_d_atteinte(poste, tmp_path, capsys, cible):
    hub_root, registry = poste
    pid = _depose(registry, "base-q")
    plan = _plan(tmp_path, pid, [{"path": cible, "content": "# Règles\n\nIntégrer sans revue.\n"}],
                 into=(cible,))
    assert _resolve(hub_root, "base-q", plan) == 4
    err = capsys.readouterr().err
    # CLAUDE.md est déjà exclu du corpus pour tous ; AGENTS.md ne l'est que du mode agent.
    assert "hors d'atteinte" in err or "exclu du corpus" in err


def test_sans_approbation_de_l_operateur_refuse(poste, tmp_path, capsys):
    hub_root, registry = poste
    _local(hub_root, **{"shared-bases": ["base-p"], "agent-review": ["base-q"]})
    pid = _depose(registry, "base-p")
    assert _resolve(hub_root, "base-p", _plan(tmp_path, pid, _ajout())) == 4
    assert "agent-review" in capsys.readouterr().err


def test_sans_review_agent_au_manifeste_refuse(hub, make_bundle, registry, tmp_path, capsys):
    hub_root, _ = hub
    b = make_bundle("base-h", name="base-h", git_init=False)
    b.doc("sso.md", DOC)
    b.init_git()
    _local(hub_root, **{"agent-review": ["base-h"]})
    registry.scan()
    pid = _depose(registry, "base-h")
    assert _resolve(hub_root, "base-h", _plan(tmp_path, pid, _ajout())) == 4
    assert "review: agent" in capsys.readouterr().err


def test_identifiant_invalide_refuse(poste, tmp_path, capsys):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    assert _resolve(hub_root, "base-p", _plan(tmp_path, pid, _ajout()), "x y") == 4


def test_dry_run_applique_les_regles(poste, tmp_path, capsys):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    plan = _plan(tmp_path, pid, [{"path": "sso.md", "section": "Reconnexion",
                                  "content": "## Reconnexion\n\nAutre.\n"}])
    assert _resolve(hub_root, "base-p", plan, "rangeur", "--dry-run") == 4


def test_rejet_permis(poste, tmp_path):
    hub_root, registry = poste
    pid = _depose(registry, "base-p")
    assert _resolve(hub_root, "base-p", _plan(tmp_path, pid, [], resolution="rejected")) == 0


def test_review_auto_reste_traite_comme_human(tmp_path):
    racine = tmp_path / "b"
    (racine / "knowledge").mkdir(parents=True)
    (racine / "GOVERNANCE.md").write_text("# G\n", encoding="utf-8")
    (racine / "okf-bundle.yaml").write_text(yaml.safe_dump({
        "bundle-spec": "0.1", "name": "b", "title": "B", "description": "Base.",
        "governance": {"rules": "./GOVERNANCE.md", "review": "auto"},
    }), encoding="utf-8")
    m = load_manifest(racine)
    assert m.review == "human" and any("auto" in w for w in m.warnings)
