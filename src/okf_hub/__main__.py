"""Point d'entrée : serveur MCP en transport stdio (§ 4.3)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
from mcp.server.stdio import stdio_server

from . import hublog
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
    return parser.parse_args(argv)


async def _serve(server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    hub_root = (args.hub_root or _default_hub_root()).resolve()

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

    hub = HubServer(config)
    try:
        anyio.run(_serve, hub.build())
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        hublog.info("arrêt")
        hublog.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
