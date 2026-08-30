"""Validation de bout en bout : client MCP réel parlant au serveur en stdio.

Ce test instancie le template de bundle en base réelle, lance le serveur comme
le ferait Claude Desktop ou Claude Code, et déroule le cycle complet du § 10.2
(J5) : lecture, proposition, revue, intégration, rejet et résolution par lot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
import yaml

from okf_hub import review
from okf_hub.config import HubConfig
from okf_hub.registry import Registry

from conftest import HUB_ROOT, git

def _find_template() -> Path | None:
    """Localise le dépôt template, livrable distinct du hub.

    Ordre : variable d'environnement, puis répertoire voisin du hub (disposition
    de développement), puis le home de l'exécutant (disposition de la CI).
    """
    candidats = []
    if env := os.environ.get("OKF_BUNDLE_TEMPLATE"):
        candidats.append(Path(env))
    candidats += [
        HUB_ROOT.parent / "okf-bundle-template",
        Path.home() / "okf-bundle-template",
    ]
    for c in candidats:
        if (c / "okf-bundle.yaml").is_file():
            return c
    return None


TEMPLATE = _find_template()

pytestmark = pytest.mark.skipif(
    TEMPLATE is None,
    reason=(
        "dépôt okf-bundle-template introuvable — le cloner à côté du hub, "
        "ou pointer OKF_BUNDLE_TEMPLATE dessus"
    ),
)


@pytest.fixture
def hub_avec_base(hub):
    """Un hub contenant une base issue du template, instanciée."""
    hub_root, bases = hub
    cible = bases / "base-demo"
    subprocess.run(["git", "clone", "-q", str(TEMPLATE), str(cible)], check=True)

    manifeste = cible / "okf-bundle.yaml"
    data = yaml.safe_load(manifeste.read_text(encoding="utf-8"))
    data["name"] = "base-demo"
    data["title"] = "Base de démonstration"
    data["description"] = (
        "Base d'essai : procédures d'exploitation,\n incidents connus,   "
        "et conventions de rédaction."
    )
    manifeste.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8"
    )
    git(cible, "add", "-A")
    git(cible, "commit", "-q", "-m", "Instanciation base-demo")
    return hub_root, cible


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


def test_cycle_mcp_complet(hub_avec_base):
    """Poignée de main, liste d'outils, lecture, recherche, proposition."""
    hub_root, bundle = hub_avec_base

    async def scenario(session):
        outils = await session.list_tools()
        noms = {t.name for t in outils.tools}
        assert noms == {
            "kb_list", "kb_search", "kb_read", "kb_governance", "kb_propose",
            "kb_proposal_status", "kb_hub_rescan",
        }
        # La description énumère les bases connues : c'est ce qui permet le
        # routage sans appel préalable (§ 5.1).
        desc = next(t for t in outils.tools if t.name == "kb_list").description
        assert "base-demo" in desc and "Base de démonstration" in desc
        # La description du manifeste a été normalisée (§ 3.3).
        assert "\n" not in desc.split("base-demo — ")[1].split("\n")[0]

        resultats = {}
        resultats["list"] = _texte(await session.call_tool("kb_list", {}))
        resultats["gov"] = _texte(
            await session.call_tool("kb_governance", {"base": "base-demo"})
        )
        resultats["search"] = _texte(
            await session.call_tool(
                "kb_search", {"base": "base-demo", "query": "reconnexion session"}
            )
        )
        resultats["read"] = _texte(
            await session.call_tool(
                "kb_read",
                {
                    "base": "base-demo",
                    "path": "exemple-document.md",
                    "section": "Procédure",
                },
            )
        )
        resultats["propose"] = _texte(
            await session.call_tool(
                "kb_propose",
                {
                    "base": "base-demo",
                    "type": "correction",
                    "concerns": "emplacement du bouton de réauthentification",
                    "content": "En 3.3, le bouton revient dans la barre supérieure.",
                    "sources": ["note de version 3.3"],
                    "confidence": "high",
                    "submitted_by": "human:testeur",
                },
            )
        )

        erreur = await session.call_tool("kb_read", {"base": "fantome", "path": "x.md"})
        assert erreur.is_error
        resultats["erreur"] = _texte(erreur)
        return resultats

    r = anyio.run(_dialogue, hub_root, scenario)

    assert "base-demo" in r["list"] and "documents :" in r["list"]
    assert "Golden rules" in r["gov"] and "schema.yaml" in r["gov"]
    assert "exemple-document.md" in r["search"]
    assert "réauthentifier" in r["read"] and "Notes de rédaction" not in r["read"]
    assert "Proposition déposée" in r["propose"]
    assert r["erreur"].startswith("ERROR: UNKNOWN_BASE:")
    assert "base-demo" in r["erreur"]  # la liste des bases valides est rendue

    # La proposition est bien commitée dans le dépôt de la base.
    pending = list((bundle / "proposals" / "pending").glob("prop-*.md"))
    assert len(pending) == 1
    assert git(bundle, "status", "--porcelain").strip() == ""
    assert git(bundle, "log", "-1", "--format=%s").startswith("proposal: prop-")


def test_import_a_chaud_et_rescan_silencieux(hub, tmp_path):
    """Une base importée pendant qu'une session est ouverte devient joignable."""
    hub_root, bases = hub

    async def scenario(session):
        vide = _texte(await session.call_tool("kb_list", {}))
        assert "Aucune base enregistrée" in vide

        # Import à chaud, session déjà ouverte.
        cible = bases / "tardive"
        await anyio.to_thread.run_sync(
            lambda: subprocess.run(["git", "clone", "-q", str(TEMPLATE), str(cible)], check=True)
        )
        manifeste = cible / "okf-bundle.yaml"
        data = yaml.safe_load(manifeste.read_text(encoding="utf-8"))
        data["name"] = "tardive"
        data["title"] = "Base importée à chaud"
        data["description"] = "Importée pendant qu'une session était déjà ouverte."
        manifeste.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")

        # § 4.4.c : UNKNOWN_BASE déclenche un re-scan silencieux, puis retente.
        # L'appel réussit donc sans rescan explicite.
        trouve = await session.call_tool(
            "kb_read", {"base": "tardive", "path": "exemple-document.md", "section": "Procédure"}
        )
        assert not trouve.is_error, _texte(trouve)

        rescan = _texte(await session.call_tool("kb_hub_rescan", {}))
        return _texte(trouve), rescan

    lecture, rescan = anyio.run(_dialogue, hub_root, scenario)
    assert "réauthentifier" in lecture
    assert "tardive" in rescan


# --- cycle de vie complet d'une base (critère d'acceptation J5) --------------


def test_cycle_de_vie_complet(hub_avec_base):
    """Intégration simple, rejet, et résolution par lot sur le même sujet."""
    hub_root, bundle = hub_avec_base
    registry = Registry(HubConfig.load(hub_root))
    registry.scan()
    base = registry.get("base-demo")

    from okf_hub.tools import propose_tool

    def depose(**kw):
        out = propose_tool.run(registry, {"base": "base-demo", **kw})
        return next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("id :"))

    # Trois contributeurs, dont deux sur le même sujet.
    p_simple = depose(
        type="addition", concerns="cas de la boucle de redirection",
        content="Vider les cookies du domaine suffit dans 9 cas sur 10.",
        sources=["incident #4702"], confidence="high", submitted_by="human:alice",
    )
    p_hors_perimetre = depose(
        type="observation", concerns="tarification de la solution Y",
        content="La solution Y passe à 12 € par poste.",
        sources=["grille tarifaire 2026"], confidence="medium", submitted_by="human:bob",
    )
    p_lot_a = depose(
        type="correction", concerns="emplacement du bouton de réauthentification",
        content="En 3.3 le bouton revient dans la barre supérieure.",
        sources=["note de version 3.3", "vérifié sur 4 postes le 28/08"],
        confidence="high", submitted_by="human:alice",
    )
    p_lot_b = depose(
        type="correction", concerns="bouton de réauthentification déplacé",
        content="Le bouton a encore bougé.",
        sources=["de mémoire"], confidence="low", submitted_by="human:carol",
    )

    depart = git(bundle, "rev-parse", "HEAD").strip()

    # 1. Intégration simple.
    review.apply_plan(base, review.parse_plan({
        "summary": "boucle de redirection : vider les cookies du domaine",
        "reviewed_by": "human:morva",
        "resolutions": [
            {"id": p_simple, "resolution": "accepted", "integrated_into": ["exemple-document.md"]}
        ],
        "edits": [{
            "path": "exemple-document.md",
            "append": "## Boucle de redirection\n\nVider les cookies du domaine, "
                      "et uniquement ceux-là.",
            "frontmatter": {"verified": [{"by": "human:morva", "at": "2026-08-30T10:00:00Z"}]},
        }],
    }))

    # 2. Rejet motivé.
    review.apply_plan(base, review.parse_plan({
        "summary": "hors périmètre de cette base",
        "reviewed_by": "human:morva",
        "resolutions": [{
            "id": p_hors_perimetre, "resolution": "rejected",
            "reason": "concerne la solution Y, qui a sa propre base",
        }],
    }))

    # 3. Résolution par lot : deux propositions, même sujet, un seul commit.
    review.apply_plan(base, review.parse_plan({
        "summary": "bouton de réauthentification revenu dans la barre en 3.3",
        "reviewed_by": "human:morva",
        "resolutions": [
            {"id": p_lot_a, "resolution": "accepted", "integrated_into": ["exemple-document.md"]},
            {"id": p_lot_b, "resolution": "rejected",
             "reason": f"doublon de {p_lot_a}, sans source vérifiable"},
        ],
        "edits": [{
            "path": "exemple-document.md",
            "append": "> Depuis la 3.3, le bouton **réauthentifier** est de nouveau "
                      "dans la barre supérieure.",
        }],
    }))

    # --- vérifications ---
    commits = git(bundle, "log", f"{depart}..HEAD", "--format=%s").splitlines()
    assert len(commits) == 3, commits
    assert commits[0].startswith("integrate: 2 proposals —")
    assert commits[1].startswith("reject:")
    assert commits[2].startswith("integrate:")

    # Invariant § 6.2 : exactement deux commits par proposition.
    for pid in (p_simple, p_hors_perimetre, p_lot_a, p_lot_b):
        assert len(git(bundle, "log", "--grep", pid, "--format=%H").split()) == 2, pid

    assert not list((bundle / "proposals" / "pending").glob("prop-*.md"))
    assert len(list((bundle / "proposals" / "accepted").glob("prop-*.md"))) == 2
    assert len(list((bundle / "proposals" / "rejected").glob("prop-*.md"))) == 2

    doc = (base.corpus_dir / "exemple-document.md").read_text(encoding="utf-8")
    assert "Boucle de redirection" in doc
    assert "de nouveau" in doc

    # Le dépôt est propre et sain à l'issue du cycle.
    assert git(bundle, "status", "--porcelain").strip() == ""
    git(bundle, "fsck", "--no-progress")

    # Le contributeur retrouve le motif de son rejet par accès git direct
    # (limitation v0 assumée, § 6.2).
    rejetee = (bundle / "proposals" / "rejected" / f"{p_lot_b}.md").read_text(encoding="utf-8")
    assert "sans source vérifiable" in rejetee


def test_okf_review_en_ligne_de_commande(hub_avec_base, tmp_path):
    """Le moteur de revue est utilisable tel que la skill l'appelle."""
    hub_root, bundle = hub_avec_base
    env = {
        **os.environ,
        "OKF_HUB_ROOT": str(hub_root),
        "OKF_HUB_PYTHON": sys.executable,
        "PYTHONPATH": str(HUB_ROOT / "src"),
    }
    okf_review = HUB_ROOT / "bin" / "okf-review"

    def lancer(*args):
        p = subprocess.run(
            [str(okf_review), *args], capture_output=True, text=True, env=env, timeout=120
        )
        assert p.returncode == 0, p.stderr
        return p.stdout

    assert "Aucune proposition en attente" in lancer("inventory", "base-demo")

    registry = Registry(HubConfig.load(hub_root))
    registry.scan()
    from okf_hub.tools import propose_tool

    out = propose_tool.run(registry, {
        "base": "base-demo", "type": "question",
        "concerns": "durée exacte d'expiration de session",
        "content": "Le document dit 8 heures. Est-ce toujours vrai en 3.3 ?",
        "sources": ["lecture du document"], "confidence": "medium",
        "submitted_by": "claude-code/opus-5",
    })
    pid = next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("id :"))

    contexte = lancer("context", "base-demo")
    assert "Golden rules" in contexte and "exemple-document.md" in contexte

    inventaire = lancer("inventory", "base-demo", "--full")
    assert pid in inventaire and "durée exacte" in inventaire

    assert "à récupérer : 0" in lancer("reconcile", "base-demo").lower()

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "summary": "question sur l'expiration — enquête nécessaire",
        "reviewed_by": "human:morva",
        "resolutions": [{
            "id": pid, "resolution": "rejected",
            "reason": "question légitime mais sans réponse disponible ; à rouvrir "
                      "après vérification côté éditeur",
        }],
    }), encoding="utf-8")

    assert "reject:" in lancer("resolve", "base-demo", "--plan", str(plan), "--dry-run")
    assert git(bundle, "log", "-1", "--format=%s").startswith("proposal:")

    assert "Résolution commitée" in lancer("resolve", "base-demo", "--plan", str(plan))
    assert git(bundle, "log", "-1", "--format=%s").startswith(f"reject: {pid}")
