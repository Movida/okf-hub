"""Résolution `name` de manifeste → répertoire de bundle, pour le script okf-lock.

Le paramètre `base` désigne partout le champ `name` du manifeste, pas le nom du
répertoire dans bases/ (§ 3.3) : les deux diffèrent dès qu'un clone est renommé.
La correspondance passe donc par le même chargement de manifeste que le serveur,
et par une bibliothèque YAML — jamais par un grep sur le fichier.

Usage : ``python -m okf_hub.resolve <base> [--what root|lock|corpus]``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import hublog
from .config import HubConfig
from .locking import LOCK_FILENAME, ensure_git_exclude
from .registry import Registry
from .__main__ import _default_hub_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="okf-hub-resolve",
        description="Résout le nom d'une base en chemin de bundle sur ce hub.",
    )
    parser.add_argument("base", help="Nom de la base (champ `name` du manifeste).")
    parser.add_argument(
        "--what",
        choices=["root", "lock", "corpus"],
        default="root",
        help="Chemin à imprimer : racine du bundle (défaut), fichier de verrou, ou corpus.",
    )
    parser.add_argument("--hub-root", type=Path, default=None)
    args = parser.parse_args(argv)

    hub_root = (args.hub_root or _default_hub_root()).resolve()
    try:
        config = HubConfig.load(hub_root)
    except (OSError, ValueError) as exc:
        print(f"okf-hub-resolve: configuration invalide ({hub_root}) : {exc}", file=sys.stderr)
        return 2

    # Le journal ne doit pas polluer stdout : il sert de canal de sortie ici.
    hublog.configure(config.log_file, echo_stderr=False)
    registry = Registry(config)
    registry.scan()

    base = registry.bases.get(args.base)
    if base is None:
        known = ", ".join(registry.names()) or "aucune"
        print(
            f"okf-hub-resolve: base '{args.base}' inconnue. Bases disponibles : {known}",
            file=sys.stderr,
        )
        return 3

    if args.what == "lock":
        # Le fichier de verrou est créé s'il manque, et exclu du suivi git,
        # exactement comme le fait le serveur (§ 4.4.b.1) — sans quoi
        # l'étape de réconciliation (§ 7.1) le verrait en untracked.
        lock_path = base.root / LOCK_FILENAME
        lock_path.touch(exist_ok=True)
        ensure_git_exclude(base.root)
        print(lock_path)
    elif args.what == "corpus":
        print(base.corpus_dir)
    else:
        print(base.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
