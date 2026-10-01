"""Dépose des propositions par le vrai serveur du hub, en client MCP stdio.

Pour une session qui n'a pas les outils `kb_*` (hub non connecté) : le serveur
est lancé exactement comme par un client Claude, et chaque proposition passe par
l'outil `kb_propose` réel — schéma, confinement à proposals/pending/, verrou et
commit compris. Rien n'est écrit dans une base par ce script lui-même.

    uv run python skills/kb-redepot/deposer.py propositions.json            # dépose
    uv run python skills/kb-redepot/deposer.py propositions.json --check    # valide seulement

`propositions.json` : une liste d'objets, chacun étant exactement les arguments
de `kb_propose`.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[2]


def _texte(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")


async def _session(scenario):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "okf_hub", "--hub-root", str(HUB_ROOT)],
        env={"PYTHONPATH": str(HUB_ROOT / "src"), "PATH": os.environ["PATH"]},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await scenario(session)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fichier", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="valider contre le schéma annoncé par le serveur, sans déposer")
    args = parser.parse_args()
    props = json.loads(args.fichier.read_text(encoding="utf-8"))

    async def scenario(session):
        import jsonschema

        outils = {t.name: t for t in (await session.list_tools()).tools}
        schema = outils["kb_propose"].input_schema
        erreurs = 0
        for k, prop in enumerate(props):
            try:
                jsonschema.validate(prop, schema)
            except jsonschema.ValidationError as e:
                print(f"[{k}] INVALIDE : {e.message}")
                erreurs += 1
        if erreurs or args.check:
            print(f"{len(props)} proposition(s), {erreurs} invalide(s).")
            return 1 if erreurs else 0
        echecs = 0
        for k, prop in enumerate(props):
            res = await session.call_tool("kb_propose", prop)
            etat = "ERREUR" if res.is_error else "déposée"
            echecs += bool(res.is_error)
            print(f"[{k}] {prop['base']} — {etat} — {prop['concerns'][:70]}")
            print("    " + _texte(res).replace("\n", "\n    "))
        return 1 if echecs else 0

    return asyncio.run(_session(scenario))


if __name__ == "__main__":
    sys.exit(main())
