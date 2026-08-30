"""Boucle contribution → revue → consultation du verdict (rév. 4.1, § D4).

Critère d'acceptation de l'amendement : un contributeur qui n'a que l'accès MCP
dépose une proposition, un gestionnaire la résout, et le contributeur retrouve
le verdict — **sans aucun accès git direct de son côté**. C'était précisément la
lacune signalée par le premier retour d'usage réel.

Le scénario tourne sur une copie du bundle réellement déployé
(`bases/okf-hub-feedback`) quand il est présent, sinon sur un bundle jetable :
le contrat testé est celui du hub, il ne dépend pas du contenu du corpus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from conftest import HUB_ROOT, git

#: Le bundle de dogfooding créé par le § B6, s'il est déployé sur ce hub.
FEEDBACK_BUNDLE = HUB_ROOT / "bases" / "okf-hub-feedback"


# --- client MCP réel ---------------------------------------------------------


async def _dialogue(hub_root: Path, scenario):
    """Lance le serveur exactement comme le ferait un client Claude, en stdio."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "okf_hub", "--hub-root", str(hub_root)],
        env={"PYTHONPATH": str(HUB_ROOT / "src"), "PATH": os.environ["PATH"]},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await scenario(session)


def _texte(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")


@pytest.fixture
def base_deployee(hub, make_bundle):
    """Copie du bundle okf-hub-feedback déployé, ou bundle jetable à défaut.

    On travaille sur une copie : la boucle de validation ne doit pas laisser une
    proposition d'essai dans l'historique permanent d'une base réelle.
    """
    _, bases = hub
    cible = bases / "okf-hub-feedback"
    if (FEEDBACK_BUNDLE / "okf-bundle.yaml").is_file():
        subprocess.run(
            ["git", "clone", "-q", str(FEEDBACK_BUNDLE), str(cible)], check=True
        )
        return cible, "okf-hub-feedback"
    make_bundle("okf-hub-feedback", name="okf-hub-feedback")
    return cible, "okf-hub-feedback"


def test_boucle_complete_sans_acces_git_du_contributeur(hub, base_deployee, tmp_path):
    hub_root, _ = hub
    bundle, nom = base_deployee

    # --- 1. Le contributeur : découverte, règles, dépôt ----------------------

    async def contribuer(session):
        outils = await session.list_tools()
        assert "kb_proposal_status" in {t.name for t in outils.tools}

        sorties = {
            "list": _texte(await session.call_tool("kb_list", {})),
            "gov": _texte(await session.call_tool("kb_governance", {"base": nom})),
        }
        sorties["propose"] = _texte(
            await session.call_tool(
                "kb_propose",
                {
                    "base": nom,
                    "type": "observation",
                    "concerns": "chaînage kb_search vers kb_read sur un gros document",
                    "content": (
                        "Constaté en session : un résultat de kb_search sur un "
                        "document dépassant read-toc-threshold obligeait à un "
                        "appel intermédiaire pour obtenir la table des headings."
                    ),
                    "sources": ["session consommatrice, 2026-08-30"],
                    "confidence": "high",
                    "submitted_by": "claude-code/opus-5",
                },
            )
        )
        return sorties

    r = anyio.run(_dialogue, hub_root, contribuer)
    assert nom in r["list"]
    assert "Proposition déposée" in r["propose"]

    pid = next(
        l.split(":", 1)[1].strip() for l in r["propose"].splitlines() if l.startswith("id :")
    )
    # La sortie de kb_propose indique elle-même par où relire le verdict.
    assert "kb_proposal_status" in r["propose"] and pid in r["propose"]

    # --- 2. Le contributeur consulte avant revue : pending -------------------

    async def consulter(session):
        return _texte(
            await session.call_tool("kb_proposal_status", {"base": nom, "id": pid})
        )

    avant = anyio.run(_dialogue, hub_root, consulter)
    assert f"{pid} — pending" in avant
    assert "en attente de revue" in avant

    # --- 3. Le gestionnaire résout, hors MCP, par okf-review ----------------

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "summary": "chaînage kb_search → kb_read documenté",
                "reviewed_by": "human:testeur",
                "resolutions": [
                    {
                        "id": pid,
                        "resolution": "accepted",
                        "integrated_into": ["limitations-connues.md"],
                    }
                ],
                "edits": [
                    {
                        "path": "limitations-connues.md",
                        "append": "Constat de session repris ici.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    resolution = subprocess.run(
        [
            sys.executable, "-m", "okf_hub.review", "--hub-root", str(hub_root),
            "resolve", nom, "--plan", str(plan),
        ],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(HUB_ROOT / "src")},
    )
    assert resolution.returncode == 0, resolution.stderr
    assert "Résolution commitée" in resolution.stdout

    # --- 4. Le contributeur relit le verdict, toujours en MCP seul ----------

    apres = anyio.run(_dialogue, hub_root, consulter)
    assert f"{pid} — accepted" in apres
    assert "integrated-into : limitations-connues.md" in apres

    # Et il peut aller lire ce qui a été intégré, en suivant integrated-into.
    async def relire(session):
        return _texte(
            await session.call_tool(
                "kb_read", {"base": nom, "path": "limitations-connues.md"}
            )
        )

    assert "Constat de session repris ici." in anyio.run(_dialogue, hub_root, relire)

    # --- 5. Les invariants d'audit du § 6.2 tiennent ------------------------

    assert len(git(bundle, "log", "--grep", f"Proposal: {pid}", "--format=%H").split()) == 1
    assert len(git(bundle, "log", "--grep", pid, "--format=%H").split()) == 2
    assert git(bundle, "status", "--porcelain").strip() == ""


def test_le_rejet_est_lisible_avec_son_motif(hub, base_deployee, tmp_path):
    """L'autre moitié du critère : un rejet doit rendre son motif au contributeur."""
    hub_root, _ = hub
    _, nom = base_deployee

    async def deposer(session):
        return _texte(
            await session.call_tool(
                "kb_propose",
                {
                    "base": nom,
                    "type": "observation",
                    "concerns": "la recherche est décevante",
                    "content": "Rien de précis, juste une impression.",
                    "sources": ["ressenti"],
                    "confidence": "low",
                    "submitted_by": "human:pressé",
                },
            )
        )

    sortie = anyio.run(_dialogue, hub_root, deposer)
    pid = next(
        l.split(":", 1)[1].strip() for l in sortie.splitlines() if l.startswith("id :")
    )

    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "summary": "retour sans outil nommé ni comportement observé",
                "reviewed_by": "human:testeur",
                "resolutions": [
                    {
                        "id": pid,
                        "resolution": "rejected",
                        "reason": "golden rule 1 : aucun outil nommé, aucun comportement observé",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable, "-m", "okf_hub.review", "--hub-root", str(hub_root),
            "resolve", nom, "--plan", str(plan),
        ],
        check=True, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(HUB_ROOT / "src")},
    )

    async def consulter(session):
        return _texte(
            await session.call_tool(
                "kb_proposal_status", {"base": nom, "submitted_by": "human:pressé"}
            )
        )

    verdict = anyio.run(_dialogue, hub_root, consulter)
    assert f"{pid} — rejected" in verdict
    assert "golden rule 1" in verdict


@pytest.mark.skipif(
    not (FEEDBACK_BUNDLE / "okf-bundle.yaml").is_file(),
    reason="bundle okf-hub-feedback non déployé sur ce hub",
)
def test_le_bundle_de_dogfooding_est_conforme():
    """Le § B6 impose un bundle standard, pas un cas particulier du hub."""
    from okf_hub.governance import STABLE, status_of_file
    from okf_hub.manifest import load_manifest

    manifeste = load_manifest(FEEDBACK_BUNDLE)
    assert manifeste.name == "okf-hub-feedback"
    assert manifeste.warnings == []
    assert manifeste.frontmatter_schema is not None
    # Description orientée routage : elle doit nommer les outils concernés.
    assert "kb_propose" in manifeste.description
    # § B6 : status stable d'emblée, contrairement au template.
    assert status_of_file(manifeste.governance_rules) == STABLE
    assert (FEEDBACK_BUNDLE / "knowledge" / "roadmap.md").is_file()
    assert (FEEDBACK_BUNDLE / "knowledge" / "limitations-connues.md").is_file()
