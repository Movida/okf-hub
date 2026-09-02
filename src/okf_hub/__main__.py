"""Point d'entrée : serveur MCP en transport stdio (§ 4.3)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
from mcp.server.stdio import stdio_server

from . import bootstrap, hublog, remote_sync
from .config import HubConfig
from .server import HubServer


def _default_hub_root() -> Path:
    env = os.environ.get("OKF_HUB_ROOT")
    if env:
        return Path(env)
    # Par défaut : la racine du dépôt du hub (src/okf_hub/__main__.py → ../../..).
    return Path(__file__).resolve().parents[2]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="okf-hub",
        description="Serveur MCP du OKF Bundle Hub (transport stdio).",
    )
    parser.add_argument(
        "--hub-root",
        type=Path,
        default=None,
        help="Racine du hub (contenant hub-config.yaml). Défaut : $OKF_HUB_ROOT "
        "ou la racine du dépôt du hub.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Duplique le journal sur la sortie d'erreur (stdout est réservé au protocole).",
    )
    sous_commandes = parser.add_subparsers(dest="command")
    setup_parser = sous_commandes.add_parser(
        "setup",
        help="Point d'entrée d'installation unique : identité git, clé(s) SSH, "
        "détection/configuration du client MCP installé, bootstrap des bases "
        "livrées. N'écrase aucune procédure documentée au README, ne fait que "
        "les enchaîner quand elles sont détectables sans deviner de secret. "
        "Les options globales (--hub-root, --verbose) se placent avant "
        "« setup » : `okf-hub --hub-root <chemin> setup`.",
    )
    setup_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Non interactif : ne demande pas l'identité git si elle est "
        "encore inconnue, saute l'étape plutôt que d'attendre une saisie.",
    )
    catalog_parser = sous_commandes.add_parser(
        "catalog",
        help="Catalogue des bases connues : list, show, add, remove, import, "
        "retire. Ajouter une base devient « en choisir une dans une liste » "
        "plutôt que connaître et taper son URL git exacte ; retire son cycle "
        "de retrait, jamais outillé jusqu'ici. Voir `okf-hub catalog -h` "
        "(après ce premier mot) pour le détail de chaque sous-action. Les "
        "options globales (--hub-root, --verbose) se placent avant "
        "« catalog », comme pour « setup ».",
    )
    catalog_parser.add_argument("catalog_argv", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


async def _serve(server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    hub_root = (args.hub_root or _default_hub_root()).resolve()

    if getattr(args, "command", None) == "setup":
        from .setup_cmd import run_setup

        return run_setup(hub_root, interactive=not args.yes)

    if getattr(args, "command", None) == "catalog":
        from .catalog_cmd import main as catalog_main

        return catalog_main(hub_root, args.catalog_argv)

    try:
        config = HubConfig.load(hub_root)
    except (OSError, ValueError) as exc:
        print(f"okf-hub: configuration invalide ({hub_root}) : {exc}", file=sys.stderr)
        return 2

    hublog.configure(config.log_file, echo_stderr=args.verbose)
    hublog.info(
        f"démarrage — hub_root={config.hub_root} bases_dir={config.bases_dir} "
        f"read_toc_threshold={config.read_toc_threshold}"
    )

    # Les bases livrées avec le hub (bundles/) sont installées avant la
    # découverte, pour qu'une installation neuve ne démarre pas muette. Ne crée
    # que ce qui manque, ne lève jamais, et supporte des instances concurrentes
    # (§ 4.4) — voir bootstrap.py.
    if config.bootstrap_bundles:
        for nom in bootstrap.deploy_missing(config):
            hublog.info(f"première installation de la base livrée '{nom}'")

    # Synchronisation fast-forward-only des bases ayant un remote, avant la
    # première découverte (§ 4.5). Point unique et explicite du cycle de vie
    # de cette instance : aucun état partagé, aucun démon (§ 4.4). Ne lève
    # jamais — remote absent/injoignable ou divergence sont journalisés sans
    # empêcher le démarrage, voir remote_sync.py.
    if config.sync_on_start:
        remote_sync.sync_all(config)

    hub = HubServer(config)
    try:
        anyio.run(_serve, hub.build())
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        hub.stop()
        hublog.info("arrêt")
        hublog.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
