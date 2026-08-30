"""kb_proposal_status : filtres, statuts, cas limites (amendement rév. 4.1, § B1)."""

from __future__ import annotations

import pytest

from okf_hub import review
from okf_hub.errors import INVALID_INPUT, NOT_FOUND, ToolError
from okf_hub.tools import proposal_status_tool, propose_tool

VALID = {
    "type": "correction",
    "concerns": "procédure de reconnexion SSO",
    "content": "Depuis la 3.2, le bouton a été déplacé dans le menu profil.",
    "sources": ["constat terrain, incident #4521"],
    "confidence": "high",
    "submitted_by": "session-support-client",
}


@pytest.fixture
def base(make_bundle, registry):
    make_bundle("ma-base", name="ma-base")
    registry.scan()
    return registry.get("ma-base")


def propose(registry, **overrides) -> str:
    out = propose_tool.run(registry, {"base": "ma-base", **VALID, **overrides})
    return next(
        line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("id :")
    )


def status(registry, **arguments) -> str:
    return proposal_status_tool.run(registry, {"base": "ma-base", **arguments})


def resolve(base, registry, **resolution) -> None:
    """Fait passer une proposition par le moteur de revue réel."""
    plan = review.parse_plan(
        {
            "summary": "revue de test",
            "reviewed_by": "human:testeur",
            "resolutions": [resolution],
            "edits": (
                [{"path": p, "append": "Contenu intégré."} for p in resolution["integrated_into"]]
                if resolution.get("integrated_into")
                else []
            ),
        }
    )
    review.apply_plan(base, plan)


# --- contrainte d'entrée ------------------------------------------------------


def test_sans_filtre_refuse(base, registry):
    """Sans id ni submitted_by, l'outil déverserait proposals/ en entier."""
    with pytest.raises(ToolError) as exc:
        status(registry)
    assert exc.value.code == INVALID_INPUT
    assert "submitted_by" in exc.value.message


def test_status_invalide_refuse(base, registry):
    propose(registry)
    with pytest.raises(ToolError) as exc:
        status(registry, submitted_by=VALID["submitted_by"], status="archived")
    assert exc.value.code == INVALID_INPUT


@pytest.mark.parametrize("limite", [0, 51])
def test_limit_hors_bornes_refuse(base, registry, limite):
    with pytest.raises(ToolError) as exc:
        status(registry, submitted_by=VALID["submitted_by"], limit=limite)
    assert exc.value.code == INVALID_INPUT


# --- lecture des trois répertoires --------------------------------------------


def test_proposition_en_attente(base, registry):
    pid = propose(registry)
    out = status(registry, id=pid)
    assert f"### {pid} — pending" in out
    assert "en attente de revue" in out
    assert VALID["concerns"] in out
    assert VALID["submitted_by"] in out


def test_proposition_integree_expose_integrated_into(base, registry):
    pid = propose(registry)
    resolve(base, registry, id=pid, resolution="accepted", integrated_into=["exemple.md"])

    out = status(registry, id=pid)
    assert f"### {pid} — accepted" in out
    assert "integrated-into : exemple.md" in out
    assert "kb_read" in out


def test_proposition_rejetee_expose_le_motif(base, registry):
    pid = propose(registry)
    resolve(
        base,
        registry,
        id=pid,
        resolution="rejected",
        reason="hors périmètre de la base",
    )

    out = status(registry, id=pid)
    assert f"### {pid} — rejected" in out
    assert "rejection-reason : hors périmètre de la base" in out


def test_le_corps_de_la_proposition_n_est_pas_retourne(base, registry):
    """Économie de tokens (§ B1) : l'id et integrated-into suffisent."""
    pid = propose(registry)
    out = status(registry, id=pid)
    assert VALID["content"] not in out


# --- filtres ------------------------------------------------------------------


def test_filtre_par_contributeur(base, registry):
    mien = propose(registry, submitted_by="human:alice")
    autre = propose(registry, submitted_by="human:bob")

    out = status(registry, submitted_by="human:alice")
    assert mien in out
    assert autre not in out


def test_filtre_par_contributeur_insensible_a_la_casse(base, registry):
    pid = propose(registry, submitted_by="Human:Alice")
    assert pid in status(registry, submitted_by="human:alice")


def test_contributeur_sans_proposition_n_est_pas_une_erreur(base, registry):
    propose(registry)
    out = status(registry, submitted_by="human:inconnu")
    assert "Aucune proposition" in out


def test_filtre_par_statut(base, registry):
    garde = propose(registry, concerns="reste en attente")
    resolu = propose(registry, concerns="sera rejetée")
    resolve(base, registry, id=resolu, resolution="rejected", reason="doublon")

    en_attente = status(registry, submitted_by=VALID["submitted_by"], status="pending")
    assert garde in en_attente
    assert resolu not in en_attente

    rejetees = status(registry, submitted_by=VALID["submitted_by"], status="rejected")
    assert resolu in rejetees
    assert garde not in rejetees


def test_tri_du_plus_recent_au_plus_ancien(base, registry):
    """Le tri porte sur submitted-at, pas sur l'ordre de lecture du disque."""
    ids = [propose(registry, concerns=f"sujet {i}") for i in range(3)]
    for i, pid in enumerate(ids):
        chemin = base.root / "proposals" / "pending" / f"{pid}.md"
        texte = chemin.read_text(encoding="utf-8")
        chemin.write_text(
            texte.replace(
                next(l for l in texte.splitlines() if l.startswith("submitted-at:")),
                f"submitted-at: '2026-08-{10 + i:02d}T09:00:00Z'",
            ),
            encoding="utf-8",
        )

    out = status(registry, submitted_by=VALID["submitted_by"])
    positions = [out.index(pid) for pid in ids]
    assert positions == sorted(positions, reverse=True)


def test_limit_borne_la_sortie_et_signale_le_reste(base, registry):
    for i in range(4):
        propose(registry, concerns=f"sujet {i}")
    out = status(registry, submitted_by=VALID["submitted_by"], limit=2)
    assert "4 proposition(s)" in out
    assert "2 plus ancienne(s) non listée(s)" in out
    assert out.count("### prop-") == 2


# --- cas limites --------------------------------------------------------------


def test_id_introuvable_est_un_not_found(base, registry):
    propose(registry)
    with pytest.raises(ToolError) as exc:
        status(registry, id="prop-2026-01-01-0000")
    assert exc.value.code == NOT_FOUND


def test_incoherence_status_emplacement_signalee_sans_echouer(base, registry):
    """L'emplacement fait foi (§ 6.2) ; la divergence est signalée, pas fatale."""
    pid = propose(registry)
    resolve(base, registry, id=pid, resolution="accepted", integrated_into=["exemple.md"])

    chemin = base.root / "proposals" / "accepted" / f"{pid}.md"
    chemin.write_text(
        chemin.read_text(encoding="utf-8").replace("status: accepted", "status: pending"),
        encoding="utf-8",
    )

    out = status(registry, id=pid)
    assert f"### {pid} — accepted" in out
    assert "incohérence status/emplacement" in out
    assert "l'emplacement fait foi" in out


def test_frontmatter_illisible_ignore_et_signale(base, registry):
    pid = propose(registry)
    casse = base.root / "proposals" / "pending" / "prop-2026-08-30-ffff.md"
    casse.write_text("---\n: : : pas du YAML\n---\n\ncorps\n", encoding="utf-8")

    out = status(registry, submitted_by=VALID["submitted_by"])
    assert pid in out
    assert "1 fichier(s) illisible(s) ignoré(s)" in out


def test_plafond_de_sortie_respecte(base, registry):
    # Chaque entrée pèse ~380 caractères : il en faut une cinquantaine pour
    # dépasser les 16 000 caractères du plafond transverse.
    for i in range(50):
        propose(registry, concerns=f"sujet volumineux numéro {i} " + "détail " * 24)
    out = status(registry, submitted_by=VALID["submitted_by"], limit=50)
    assert len(out) <= proposal_status_tool.BudgetedWriter().char_cap + 2000
    assert "résultats tronqués" in out


def test_base_inconnue(base, registry):
    with pytest.raises(ToolError) as exc:
        proposal_status_tool.run(registry, {"base": "absente", "id": "prop-x"})
    assert exc.value.code == "UNKNOWN_BASE"


# --- confinement --------------------------------------------------------------


def test_lien_symbolique_sortant_ignore(base, registry, tmp_path):
    """Le confinement de § 5.3 s'applique aussi à la lecture de proposals/."""
    pid = propose(registry)
    dehors = tmp_path / "secret.md"
    dehors.write_text("---\nid: secret\nsubmitted-by: intrus\n---\n\nfuite\n", encoding="utf-8")
    (base.root / "proposals" / "pending" / "evade.md").symlink_to(dehors)

    out = status(registry, submitted_by=VALID["submitted_by"])
    assert pid in out
    assert "secret" not in out

    with pytest.raises(ToolError) as exc:
        status(registry, id="secret")
    assert exc.value.code == NOT_FOUND


def test_proposals_reste_exclu_des_autres_outils(base, registry):
    """B1 est la seule exception à la liste d'exclusions du § 5.2."""
    from okf_hub.tools import read_tool, search_tool

    pid = propose(registry)
    assert "proposals" not in search_tool.run(
        registry, {"base": "ma-base", "query": "reconnexion"}
    )
    with pytest.raises(ToolError) as exc:
        read_tool.run(registry, {"base": "ma-base", "path": f"../proposals/pending/{pid}.md"})
    assert exc.value.code == NOT_FOUND
