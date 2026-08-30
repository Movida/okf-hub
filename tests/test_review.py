"""Rôle gestionnaire : réconciliation et résolution (§ 6.2, § 7). Jalon J3."""

from __future__ import annotations

import json

import pytest

from okf_hub import review
from okf_hub.errors import INVALID_INPUT, NOT_FOUND, ToolError
from okf_hub.mdutil import parse_document
from okf_hub.tools import propose_tool

from conftest import git

BASE_DOC = (
    "---\ntitle: Procédure SSO\nlast-verified: 2025-01-01\n---\n\n"
    "# Procédure SSO\n\n## Reconnexion\n\nCliquer sur « réauthentifier » dans la barre.\n\n"
    "## Déconnexion\n\nFermer la session.\n"
)


@pytest.fixture
def base(make_bundle, registry):
    b = make_bundle("ma-base", name="ma-base", git_init=False)
    b.doc("sso.md", BASE_DOC)
    b.init_git()
    registry.scan()
    return registry.get("ma-base")


def depose(registry, **overrides) -> str:
    args = {
        "base": "ma-base",
        "type": "correction",
        "concerns": "procédure de reconnexion SSO",
        "content": "Le bouton a été déplacé dans le menu profil depuis la 3.2.",
        "sources": ["incident #4521"],
        "confidence": "high",
        "submitted_by": "session-support",
        **overrides,
    }
    out = propose_tool.run(registry, args)
    return next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("id :"))


# --- étape 0 : réconciliation (§ 7.1.0) --------------------------------------


def test_fichier_valide_non_suivi_est_recupere(base, registry):
    """Rattrapage de la fenêtre de crash du § 5.5."""
    depose(registry)  # crée les répertoires et une proposition normale
    orphelin = base.root / "proposals" / "pending" / "prop-2026-01-02-cafe.md"
    orphelin.write_text(
        "---\nid: prop-2026-01-02-cafe\nsubmitted-by: session-orpheline\n"
        "submitted-at: 2026-01-02T10:00:00Z\ntype: observation\n"
        "concerns: sujet orphelin\nsources: [constat]\nconfidence: low\n"
        "status: pending\n---\n\nCorps de la proposition orpheline.\n",
        encoding="utf-8",
    )

    apercu = review.reconcile(base, apply=False)
    assert apercu.recovered == ["prop-2026-01-02-cafe"]
    assert apercu.already_tracked == 1

    rapport = review.reconcile(base, apply=True)
    assert rapport.recovered == ["prop-2026-01-02-cafe"]

    message = git(base.root, "log", "-1", "--format=%B")
    assert "(recovered)" in message
    # Submitted-By est repris du frontmatter, pas inventé (§ 7.1.0).
    assert "Submitted-By: session-orpheline" in message
    assert git(base.root, "status", "--porcelain").strip() == ""

    # L'invariant d'audit est restauré : le commit de récupération tient lieu
    # de commit de soumission (§ 6.2).
    commits = git(
        base.root, "log", "--grep", "proposal: prop-2026-01-02-cafe", "--format=%H"
    ).split()
    assert len(commits) == 1


def test_fichier_malforme_signale_sans_commit(base, registry):
    depose(registry)
    malforme = base.root / "proposals" / "pending" / "brouillon.md"
    malforme.write_text("pas de frontmatter du tout\n", encoding="utf-8")

    rapport = review.reconcile(base, apply=True)
    assert rapport.malformed == ["brouillon.md"]
    assert rapport.recovered == []
    # Toujours non suivi : on ne fabrique pas une identité de contributeur.
    assert "brouillon.md" in git(base.root, "status", "--porcelain")


def test_reconciliation_idempotente(base, registry):
    depose(registry)
    assert review.reconcile(base, apply=True).recovered == []
    assert review.reconcile(base, apply=True).recovered == []


# --- résolution simple : intégration ------------------------------------------


def test_integration_simple(base, registry):
    pid = depose(registry)
    plan = review.parse_plan(
        {
            "summary": "reconnexion SSO déplacée dans le menu profil",
            "reviewed_by": "human:morva",
            "resolutions": [{"id": pid, "resolution": "accepted", "integrated_into": ["sso.md"]}],
            "edits": [
                {
                    "path": "sso.md",
                    "section": "Reconnexion",
                    "content": "## Reconnexion\n\nCliquer sur « réauthentifier » "
                               "dans le menu profil (depuis la 3.2).",
                    "frontmatter": {"last-verified": "2026-08-30"},
                }
            ],
        }
    )
    review.apply_plan(base, plan)

    # Le corpus porte l'affirmation, la section voisine est intacte.
    doc = (base.corpus_dir / "sso.md").read_text(encoding="utf-8")
    assert "menu profil" in doc
    assert "## Déconnexion" in doc and "Fermer la session." in doc
    assert parse_document(doc).frontmatter["last-verified"] == "2026-08-30"

    # La proposition a migré, frontmatter enrichi (§ 6.2).
    assert not (base.root / "proposals" / "pending" / f"{pid}.md").exists()
    resolue = parse_document(
        (base.root / "proposals" / "accepted" / f"{pid}.md").read_text(encoding="utf-8")
    ).frontmatter
    assert resolue["status"] == "accepted"
    assert resolue["resolution"] == "accepted"
    assert resolue["integrated-into"] == ["sso.md"]
    assert resolue["resolved-at"].endswith("Z")

    # Un seul commit couvrant corpus + déplacement.
    message = git(base.root, "log", "-1", "--format=%B")
    assert message.startswith(f"integrate: {pid} — reconnexion SSO déplacée")
    assert f"Proposal: {pid}" in message
    assert "Submitted-By: session-support" in message
    assert "Reviewed-By: human:morva" in message

    touches = git(base.root, "diff", "HEAD~1", "HEAD", "--name-only").split()
    assert "knowledge/sso.md" in touches
    assert f"proposals/accepted/{pid}.md" in touches
    assert git(base.root, "status", "--porcelain").strip() == ""


def test_rejet_avec_motif(base, registry):
    pid = depose(registry)
    plan = review.parse_plan(
        {
            "summary": "hors périmètre de la base",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": pid, "resolution": "rejected", "reason": "concerne l'outil B, pas cette base"}
            ],
        }
    )
    review.apply_plan(base, plan)

    resolue = parse_document(
        (base.root / "proposals" / "rejected" / f"{pid}.md").read_text(encoding="utf-8")
    ).frontmatter
    assert resolue["status"] == "rejected"
    assert resolue["rejection-reason"] == "concerne l'outil B, pas cette base"
    assert "integrated-into" not in resolue

    message = git(base.root, "log", "-1", "--format=%B")
    assert message.startswith(f"reject: {pid} — hors périmètre")
    assert f"Proposal: {pid}" in message
    # Le corpus n'a pas bougé.
    assert git(base.root, "diff", "HEAD~1", "HEAD", "--name-only", "--", "knowledge/").strip() == ""


def test_rejet_sans_motif_refuse(base, registry):
    pid = depose(registry)
    with pytest.raises(ToolError) as exc:
        review.parse_plan(
            {
                "summary": "x",
                "reviewed_by": "human:morva",
                "resolutions": [{"id": pid, "resolution": "rejected"}],
            }
        )
    assert exc.value.code == INVALID_INPUT
    assert "motif" in exc.value.message


# --- résolution par lot (§ 6.2) -----------------------------------------------


def test_lot_mele_integration_et_rejet_en_un_seul_commit(base, registry):
    """§ 6.2 : jamais des commits séparés, qui laisseraient des états incohérents."""
    p1 = depose(registry, submitted_by="session-a", sources=["incident #4521, capture d'écran"])
    p2 = depose(registry, submitted_by="session-b", content="Le bouton a bougé.", confidence="low")

    avant = git(base.root, "rev-parse", "HEAD").strip()
    plan = review.parse_plan(
        {
            "summary": "reconnexion SSO — doublon fusionné",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": p1, "resolution": "accepted", "integrated_into": ["sso.md"]},
                {"id": p2, "resolution": "rejected", "reason": f"doublon de {p1}, moins bien sourcé"},
            ],
            "edits": [{"path": "sso.md", "append": "Depuis la 3.2, le bouton est dans le menu profil."}],
        }
    )
    review.apply_plan(base, plan)

    # UN seul commit pour les deux propositions.
    assert len(git(base.root, "log", f"{avant}..HEAD", "--format=%H").split()) == 1

    message = git(base.root, "log", "-1", "--format=%B")
    assert message.startswith("integrate: 2 proposals — reconnexion SSO")
    assert f"Proposal: {p1}" in message and f"Proposal: {p2}" in message
    assert "Submitted-By: session-a" in message and "Submitted-By: session-b" in message
    assert message.count("Reviewed-By:") == 1

    assert (base.root / "proposals" / "accepted" / f"{p1}.md").is_file()
    assert (base.root / "proposals" / "rejected" / f"{p2}.md").is_file()

    # Chaque proposition apparaît dans exactement deux commits (§ 6.2).
    for pid in (p1, p2):
        histoire = git(base.root, "log", "--grep", pid, "--format=%H").split()
        assert len(histoire) == 2, pid


def test_lot_uniquement_de_rejets(base, registry):
    p1, p2 = depose(registry), depose(registry, concerns="autre sujet")
    plan = review.parse_plan(
        {
            "summary": "hors périmètre",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": p1, "resolution": "rejected", "reason": "hors périmètre"},
                {"id": p2, "resolution": "rejected", "reason": "hors périmètre"},
            ],
        }
    )
    review.apply_plan(base, plan)
    assert git(base.root, "log", "-1", "--format=%s").startswith("reject: 2 proposals")


# --- robustesse ---------------------------------------------------------------


def test_proposition_inconnue_refusee_sans_effet(base, registry):
    pid = depose(registry)
    avant = git(base.root, "rev-parse", "HEAD").strip()
    plan = review.parse_plan(
        {
            "summary": "x",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": pid, "resolution": "accepted", "integrated_into": []},
                {"id": "prop-2020-01-01-dead", "resolution": "accepted"},
            ],
        }
    )
    with pytest.raises(ToolError) as exc:
        review.apply_plan(base, plan)
    assert exc.value.code == NOT_FOUND
    # Rien n'a été commité : la vérification a lieu avant toute écriture.
    assert git(base.root, "rev-parse", "HEAD").strip() == avant
    assert (base.root / "proposals" / "pending" / f"{pid}.md").is_file()


@pytest.mark.parametrize(
    "chemin", ["../GOVERNANCE.md", "/etc/passwd", "sous/../../okf-bundle.yaml"]
)
def test_edition_hors_corpus_refusee(base, registry, chemin):
    pid = depose(registry)
    plan = review.parse_plan(
        {
            "summary": "x",
            "reviewed_by": "human:morva",
            "resolutions": [{"id": pid, "resolution": "accepted", "integrated_into": []}],
            "edits": [{"path": chemin, "content": "compromis"}],
        }
    )
    with pytest.raises(ToolError) as exc:
        review.apply_plan(base, plan)
    assert exc.value.code == INVALID_INPUT


def test_reviewed_by_avec_retour_a_la_ligne_refuse(base, registry):
    with pytest.raises(ToolError) as exc:
        review.parse_plan(
            {
                "summary": "x",
                "reviewed_by": "moi\nProposal: prop-2020-01-01-dead",
                "resolutions": [{"id": "prop-x", "resolution": "accepted"}],
            }
        )
    assert exc.value.code == INVALID_INPUT


def test_creation_d_un_nouveau_document(base, registry):
    pid = depose(registry, type="addition", concerns="sujet inédit")
    plan = review.parse_plan(
        {
            "summary": "nouveau document sur les incidents",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": pid, "resolution": "accepted", "integrated_into": ["incidents/2026.md"]}
            ],
            "edits": [
                {
                    "path": "incidents/2026.md",
                    "content": "# Incidents 2026\n\nLe bouton a été déplacé.\n",
                    "frontmatter": {"title": "Incidents 2026", "type": "Reference"},
                }
            ],
        }
    )
    review.apply_plan(base, plan)
    nouveau = base.corpus_dir / "incidents" / "2026.md"
    assert nouveau.is_file()
    doc = parse_document(nouveau.read_text(encoding="utf-8"))
    assert doc.frontmatter["title"] == "Incidents 2026"
    assert "déplacé" in doc.body
    assert "knowledge/incidents/2026.md" in git(base.root, "ls-tree", "-r", "HEAD", "--name-only")


def test_cli_dry_run_ne_modifie_rien(base, registry, tmp_path, capsys):
    pid = depose(registry)
    avant = git(base.root, "rev-parse", "HEAD").strip()
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(
        json.dumps(
            {
                "summary": "essai",
                "reviewed_by": "human:morva",
                "resolutions": [{"id": pid, "resolution": "accepted", "integrated_into": ["sso.md"]}],
            }
        ),
        encoding="utf-8",
    )
    code = review.main(
        ["--hub-root", str(registry.config.hub_root), "resolve", "ma-base",
         "--plan", str(plan_file), "--dry-run"]
    )
    assert code == 0
    assert "integrate:" in capsys.readouterr().out
    assert git(base.root, "rev-parse", "HEAD").strip() == avant


def test_cli_inventory_et_context(base, registry, capsys):
    depose(registry)
    review.main(["--hub-root", str(registry.config.hub_root), "inventory", "ma-base"])
    out = capsys.readouterr().out
    assert "1 proposition(s) en attente" in out
    assert "procédure de reconnexion SSO" in out

    review.main(["--hub-root", str(registry.config.hub_root), "context", "ma-base"])
    out = capsys.readouterr().out
    assert "Gouvernance" in out
    assert "sso.md — Procédure SSO" in out
