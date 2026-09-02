"""CLI `okf-hub catalog` : découvrir, importer et retirer une base par son nom
plutôt que par une URL git tapée à la main.

Chaque sous-action délègue son travail à `catalog.py` ; ce module n'est que la
mise en forme argparse + texte, dans le même esprit que `bootstrap.main` et
`setup_cmd.run_setup`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import catalog, hublog
from .config import HubConfig


def _load_config(hub_root: Path) -> HubConfig | None:
    try:
        return HubConfig.load(hub_root)
    except (OSError, ValueError) as exc:
        print(f"okf-hub catalog: configuration invalide ({hub_root}) : {exc}", file=sys.stderr)
        return None


def _cmd_list(hub_root: Path, config: HubConfig, args: argparse.Namespace) -> int:
    entries = catalog.load(hub_root)
    if args.tag:
        entries = {nom: e for nom, e in entries.items() if args.tag in e.tags}
    if not entries:
        suffixe = f" (tag '{args.tag}')" if args.tag else ""
        print(f"Aucune base connue dans le catalogue{suffixe}.")
        print("Ajouter : okf-hub catalog add <nom> <url>")
        return 0
    for nom, entree in sorted(entries.items()):
        etat = "déployée" if (config.bases_dir / nom).exists() else "absente"
        titre = f" — {entree.title}" if entree.title else ""
        tags = f" [{', '.join(entree.tags)}]" if entree.tags else ""
        print(f"{nom:24} {etat:10}{titre}{tags}")
        if entree.description:
            print(f"    {entree.description}")
    return 0


def _cmd_show(hub_root: Path, config: HubConfig, args: argparse.Namespace) -> int:
    entries = catalog.load(hub_root)
    entree = entries.get(args.nom)
    if entree is None:
        print(f"okf-hub catalog: '{args.nom}' inconnu du catalogue.", file=sys.stderr)
        return 3
    print(f"nom         : {entree.name}")
    print(f"url         : {entree.url}")
    print(f"titre       : {entree.title or '(non renseigné)'}")
    print(f"description : {entree.description or '(non renseignée)'}")
    print(f"tags        : {', '.join(entree.tags) or '(aucun)'}")
    print(f"déployée    : {'oui' if (config.bases_dir / args.nom).exists() else 'non'}")
    return 0


def _cmd_add(hub_root: Path, config: HubConfig, args: argparse.Namespace) -> int:
    try:
        catalog.add(
            hub_root,
            args.nom,
            args.url,
            title=args.title,
            description=args.description,
            tags=tuple(args.tag or ()),
            overwrite=args.overwrite,
        )
    except catalog.CatalogError as exc:
        print(f"okf-hub catalog: {exc}", file=sys.stderr)
        return 3
    print(f"+ '{args.nom}' ajouté au catalogue ({catalog.path(hub_root)}).")
    return 0


def _cmd_remove(hub_root: Path, config: HubConfig, args: argparse.Namespace) -> int:
    if catalog.remove(hub_root, args.nom):
        print(f"- '{args.nom}' retiré du catalogue (bases/ non touché).")
        return 0
    print(f"okf-hub catalog: '{args.nom}' n'était pas dans le catalogue.", file=sys.stderr)
    return 3


def _cmd_import(hub_root: Path, config: HubConfig, args: argparse.Namespace) -> int:
    entries = catalog.load(hub_root)
    try:
        cible = catalog.import_entry(config, entries, args.nom)
    except catalog.CatalogError as exc:
        print(f"okf-hub catalog: {exc}", file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", "replace").strip()
        print(f"okf-hub catalog: échec du clone de '{args.nom}' : {detail}", file=sys.stderr)
        return 4
    print(f"+ '{args.nom}' clonée dans {cible}.")
    print("Découverte au prochain kb_list (kb_hub_rescan pour le rapport d'import détaillé).")
    return 0


def _cmd_retire(hub_root: Path, config: HubConfig, args: argparse.Namespace) -> int:
    try:
        rapport = catalog.retire(config, args.nom, force=args.force)
    except catalog.CatalogError as exc:
        print(f"okf-hub catalog: {exc}", file=sys.stderr)
        return 3
    if not rapport.removed:
        print(f"okf-hub catalog: retrait de '{args.nom}' refusé :", file=sys.stderr)
        for raison in rapport.blocked_reasons:
            print(f"  - {raison}", file=sys.stderr)
        print("  (--force pour passer outre)", file=sys.stderr)
        return 5
    for raison in rapport.blocked_reasons:
        print(f"! {raison} (retirée quand même : --force)")
    print(f"- '{args.nom}' retirée de {config.bases_dir}.")
    if args.forget and catalog.remove(hub_root, args.nom):
        print(f"- '{args.nom}' oubliée du catalogue.")
    print("Le retrait sera visible au prochain kb_list (ou kb_hub_rescan).")
    return 0


_ACTIONS = {
    "list": _cmd_list,
    "show": _cmd_show,
    "add": _cmd_add,
    "remove": _cmd_remove,
    "import": _cmd_import,
    "retire": _cmd_retire,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="okf-hub catalog",
        description="Catalogue des bases connues : lister, ajouter, importer, "
        "retirer — sans connaître ni retaper une URL git exacte à chaque fois.",
    )
    sous = parser.add_subparsers(dest="action", required=True)

    p_list = sous.add_parser("list", help="Liste les bases connues du catalogue.")
    p_list.add_argument("--tag", help="Filtre par tag.")

    p_show = sous.add_parser("show", help="Détail d'une entrée du catalogue.")
    p_show.add_argument("nom")

    p_add = sous.add_parser("add", help="Ajoute une base au catalogue.")
    p_add.add_argument("nom")
    p_add.add_argument("url")
    p_add.add_argument("--title")
    p_add.add_argument("--description")
    p_add.add_argument("--tag", action="append", help="Répétable.")
    p_add.add_argument("--overwrite", action="store_true", help="Remplace une entrée existante.")

    p_remove = sous.add_parser(
        "remove", help="Oublie une base du catalogue (ne touche jamais bases/)."
    )
    p_remove.add_argument("nom")

    p_import = sous.add_parser(
        "import",
        help="Clone une base du catalogue dans bases/<nom> (git clone + rescan, "
        "rien d'autre — l'invariant du produit, automatisé).",
    )
    p_import.add_argument("nom")

    p_retire = sous.add_parser(
        "retire", help="Cycle de retrait explicite d'une base déployée."
    )
    p_retire.add_argument("nom")
    p_retire.add_argument(
        "--force", action="store_true", help="Passe outre les garde-fous (propositions en attente, push non confirmé)."
    )
    p_retire.add_argument(
        "--forget", action="store_true", help="Oublie aussi l'entrée du catalogue, si elle existe."
    )

    return parser


def main(hub_root: Path, argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _load_config(hub_root)
    if config is None:
        return 2
    hublog.configure(config.log_file, echo_stderr=False)
    try:
        return _ACTIONS[args.action](hub_root, config, args)
    finally:
        hublog.close()
