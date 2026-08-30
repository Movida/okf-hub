"""Déploiement des bases livrées avec le hub (`bundles/` → `bases/`).

Le hub embarque la **source** de quelques bases — le guide d'usage, la base de
retours — dans `bundles/`. Ce module les installe dans `bases-dir` au premier
lancement, et le `bin/okf-bootstrap` fait la même chose à la demande.

## Pourquoi ne pas simplement versionner la base dans `bases/`

Parce que ça casse le circuit de contribution, silencieusement.
``gitops.commit_paths`` exécute ``git -C <racine du bundle>``. Si cette racine
n'est qu'un sous-répertoire du dépôt du hub, git remonte au dépôt englobant :
`kb_propose` **commite alors sur la branche `main` du hub**, sans erreur ni
avertissement. Et si le répertoire reste ignoré par git, c'est l'inverse —
``git add`` échoue et tout `kb_propose` retourne `IO_ERROR`.

Une base déployée doit donc être **son propre dépôt git**. La source, elle,
gagne à vivre dans le dépôt du hub : elle change en même temps que le code
qu'elle décrit, la CI la vérifie, et un `git clone` du hub suffit à disposer du
guide.

## Pourquoi c'est écrit avec autant de précautions

« Premier lancement » n'est pas un événement unique : il y a **une instance du
serveur par client connecté** (§ 4.4). Deux clients qui démarrent ensemble sur
une installation neuve exécutent ce code en même temps, sur le même répertoire.

Le verrou de base (§ 4.4.b.1) ne peut rien ici : son fichier vit *dans* le
bundle, qui n'existe pas encore. La sérialisation repose donc sur le système de
fichiers :

* l'arbre est construit dans un répertoire temporaire **préfixé d'un point**,
  au sein de `bases-dir` — donc sur le même système de fichiers, et ignoré par
  la découverte (§ 4.2), qui saute les répertoires cachés ;
* la publication est un ``os.rename()``, atomique. Le perdant de la course
  reçoit une erreur, constate que la cible existe, et s'efface.

Aucune base existante n'est jamais touchée : ce module ne crée que ce qui
manque. L'écrasement est réservé à un `--force` explicite en ligne de commande.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from . import hublog
from .config import HubConfig
from .manifest import MANIFEST_FILENAME

BUNDLES_DIRNAME = "bundles"
UPSTREAMS_FILENAME = "upstreams.yaml"

#: Identité des commits de déploiement — jamais celle de la config git locale
#: (§ 4.4.e).
_GIT_IDENTITY = [
    "-c", "user.name=okf-hub",
    "-c", "user.email=hub@local",
    "-c", "commit.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
]

GIT_TIMEOUT_S = 60


def sources_dir(hub_root: Path) -> Path:
    return hub_root / BUNDLES_DIRNAME


def available(hub_root: Path) -> dict[str, Path]:
    """Bundles livrés avec le hub, par nom de répertoire."""
    racine = sources_dir(hub_root)
    if not racine.is_dir():
        return {}
    return {
        entry.name: entry
        for entry in sorted(racine.iterdir())
        if entry.is_dir() and (entry / MANIFEST_FILENAME).is_file()
    }


def upstreams(hub_root: Path) -> dict[str, str]:
    """Dépôts canoniques déclarés dans `bundles/upstreams.yaml`.

    Une base qui en a un est **clonée**, jamais semée : semer produirait une
    histoire git sans rapport avec la sienne, et toute proposition déposée sur
    cette base orpheline serait irrécupérable.
    """
    chemin = sources_dir(hub_root) / UPSTREAMS_FILENAME
    if not chemin.is_file():
        return {}
    try:
        charge = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        hublog.warning(f"{chemin} illisible, amonts ignorés : {exc}")
        return {}
    if not isinstance(charge, dict):
        return {}
    return {
        str(nom): str(url)
        for nom, url in charge.items()
        if isinstance(url, str) and url.strip()
    }


def _git(repo: Path, args: list[str]) -> None:
    subprocess.run(
        ["git", "-C", str(repo)] + _GIT_IDENTITY + args,
        capture_output=True,
        check=True,
        timeout=GIT_TIMEOUT_S,
    )


def _clone(url: str, destination: Path) -> None:
    """Clone `url` dans `destination`, qui existe et est vide."""
    subprocess.run(
        ["git", "clone", "--quiet", "--", url, str(destination)],
        capture_output=True,
        check=True,
        timeout=GIT_TIMEOUT_S,
    )


def deploy_one(source: Path, target: Path, upstream: str | None = None) -> bool:
    """Installe la base `target`. False si déjà présente.

    Avec `upstream`, la base est **clonée** depuis son dépôt canonique ; sans,
    elle est semée depuis `source`. Un clone qui échoue n'est jamais remplacé
    par un semis : mieux vaut une base absente qu'une base à l'histoire
    orpheline, sur laquelle toute contribution serait perdue.

    Publication atomique dans les deux cas : l'arbre est bâti à côté puis
    renommé. Deux processus concurrents ne peuvent pas produire un dépôt à
    moitié construit — le second échoue au `rename` et repart.
    """
    if target.exists():
        return False

    bases_dir = target.parent
    bases_dir.mkdir(parents=True, exist_ok=True)

    # Préfixe `.` : la découverte (§ 4.2) saute les répertoires cachés, donc un
    # scan concurrent ne peut pas enregistrer un bundle en cours de copie.
    chantier = Path(tempfile.mkdtemp(dir=bases_dir, prefix=".okf-deploy-"))
    try:
        if upstream:
            _clone(upstream, chantier)
        else:
            shutil.copytree(source, chantier, dirs_exist_ok=True)
            _git(chantier, ["init", "-q", "-b", "main"])
            _git(chantier, ["add", "-A"])
            _git(
                chantier,
                ["commit", "-q", "-m", f"Déploiement de {target.name} depuis bundles/"],
            )
        try:
            os.rename(chantier, target)
        except OSError:
            # Un autre processus a publié le premier. Sa version fait foi.
            return False
        return True
    except BaseException:
        shutil.rmtree(chantier, ignore_errors=True)
        raise
    finally:
        # Le chantier n'existe plus après un rename réussi ; sinon on nettoie.
        if chantier.exists():
            shutil.rmtree(chantier, ignore_errors=True)


def deploy_missing(config: HubConfig) -> list[str]:
    """Installe les bases livrées qui manquent dans `bases-dir`.

    Ne lève jamais : un hub sans ses bases livrées fonctionne. Un échec est
    journalisé et n'empêche pas le serveur de démarrer.
    """
    amonts = upstreams(config.hub_root)
    deployes: list[str] = []
    for nom, source in available(config.hub_root).items():
        cible = config.bases_dir / nom
        amont = amonts.get(nom)
        try:
            if deploy_one(source, cible, amont):
                deployes.append(nom)
                origine = f"clonée depuis {amont}" if amont else "semée depuis bundles/"
                hublog.info(f"base livrée installée : {nom} → {cible} ({origine})")
        except (OSError, subprocess.SubprocessError) as exc:
            if amont:
                hublog.warning(
                    f"clone de '{nom}' depuis {amont} impossible ({exc}) — base non "
                    f"installée. La semer localement produirait une histoire git "
                    f"orpheline : `git clone {amont} {cible}` quand le réseau revient."
                )
            else:
                hublog.warning(f"installation de '{nom}' impossible : {exc}")
    return deployes


# --- CLI ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from .__main__ import _default_hub_root

    parser = argparse.ArgumentParser(
        prog="okf-bootstrap",
        description="Installe dans bases/ les bases livrées avec le hub (bundles/).",
    )
    parser.add_argument("noms", nargs="*", help="Bundles à installer. Défaut : tous.")
    parser.add_argument("--list", action="store_true", help="Liste les bundles livrés.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Réécrit une base déjà déployée. DESTRUCTIF : son historique git, "
        "ses propositions en attente et ses résolutions sont perdus.",
    )
    parser.add_argument("--hub-root", type=Path, default=None)
    args = parser.parse_args(argv)

    hub_root = (args.hub_root or _default_hub_root()).resolve()
    try:
        config = HubConfig.load(hub_root)
    except (OSError, ValueError) as exc:
        print(f"okf-bootstrap: configuration invalide ({hub_root}) : {exc}", file=sys.stderr)
        return 2

    hublog.configure(config.log_file, echo_stderr=False)
    livres = available(hub_root)
    amonts = upstreams(hub_root)

    if args.list:
        if not livres:
            print(f"Aucun bundle livré dans {sources_dir(hub_root)}.")
            return 0
        for nom in livres:
            etat = "déployée" if (config.bases_dir / nom).exists() else "absente"
            origine = f"clone de {amonts[nom]}" if nom in amonts else "semée depuis bundles/"
            print(f"{nom:24} {etat:10} {origine}")
        return 0

    noms = args.noms or list(livres)
    inconnus = [n for n in noms if n not in livres]
    if inconnus:
        print(
            f"okf-bootstrap: bundle(s) inconnu(s) dans {sources_dir(hub_root)} : "
            f"{', '.join(inconnus)}",
            file=sys.stderr,
        )
        return 3

    installees = 0
    for nom in noms:
        cible = config.bases_dir / nom
        if cible.exists():
            if not args.force:
                print(f"= {nom} déjà déployée, ignorée (--force pour réécrire)")
                continue
            # Destructif et assumé : l'opérateur l'a demandé explicitement.
            print(f"! {nom} réécrite — historique, propositions et résolutions perdus")
            shutil.rmtree(cible)
        try:
            if deploy_one(livres[nom], cible, amonts.get(nom)):
                print(f"+ {nom} déployée dans {cible}")
                installees += 1
            else:
                print(f"= {nom} déjà déployée, ignorée")
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"okf-bootstrap: échec sur '{nom}' : {exc}", file=sys.stderr)
            if nom in amonts:
                print(
                    f"  '{nom}' a un dépôt canonique : elle n'est jamais semée "
                    f"localement, ce qui produirait une histoire git orpheline.\n"
                    f"  Une fois le réseau ou les droits rétablis : "
                    f"git clone {amonts[nom]} {cible}",
                    file=sys.stderr,
                )
            return 4

    if installees:
        print(f"\n{installees} base(s) déployée(s). Découvertes au prochain kb_list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
