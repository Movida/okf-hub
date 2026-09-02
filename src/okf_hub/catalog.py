"""Catalogue des bases connues (`bundles/upstreams.yaml`, format étendu).

Avant ce module, `upstreams.yaml` ne servait qu'à une chose : dire à
`bootstrap.deploy_missing` qu'une base *livrée avec le hub* (présente dans
`bundles/<nom>/`) devait être clonée depuis un dépôt canonique plutôt que
semée. Une seule entrée y a jamais figuré (`okf-hub-feedback`), tapée à la
main, sans nom lisible ni description ni tag : rien n'aidait à *découvrir* une
base au-delà de celles dont on connaît déjà l'URL git exacte.

Ce module étend le même fichier en un vrai catalogue, sans changer son usage
existant : une valeur simple (chaîne) reste une URL nue, exactement comme
avant. Une valeur objet ajoute des métadonnées de découverte
(`title`, `description`, `tags`). `bootstrap.upstreams()` continue de
retourner `{nom: url}` en déléguant son analyse ici — le comportement de
`bootstrap.deploy_missing` est inchangé.

**Ce que ce module n'est pas.** Il ne remplace ni n'étend l'invariant du
produit (spec § 4.2, README « Importer une base ») : « importer une base =
`git clone <url> bases/<nom>` + rescan, aucune autre étape ». `import_entry`
exécute exactement ce clone ; choisir un nom dans `catalog list` remplace
seulement le besoin de connaître ou taper l'URL exacte, rien de plus.

**Le retrait d'une base** n'était, avant ce module, qu'un paragraphe de prose
dans `okf-hub-guide` (§5 : « supprimer le répertoire, puis laisser la
découverte constater »), jamais outillé. `retire` ajoute deux garde-fous
avant cette même suppression — propositions en attente, confirmation
best-effort que le dépôt a été poussé — sans inventer de nouveau mécanisme.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import hublog
from .config import HubConfig
from .manifest import DESCRIPTION_MAX, NAME_PATTERN, TITLE_MAX

CATALOG_RELATIVE_PATH = ("bundles", "upstreams.yaml")

GIT_TIMEOUT_S = 60


class CatalogError(Exception):
    """Une opération de catalogue est refusée — jamais une exception interne."""


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    url: str
    title: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()


@dataclass
class RetireReport:
    removed: bool
    blocked_reasons: list[str] = field(default_factory=list)


def path(hub_root: Path) -> Path:
    p = hub_root
    for part in CATALOG_RELATIVE_PATH:
        p = p / part
    return p


def load(hub_root: Path) -> dict[str, CatalogEntry]:
    """Catalogue connu, tolérant aux entrées invalides (avertissement, jamais
    bloquant — même philosophie que la découverte de bundles, § 3.3).

    Une valeur chaîne est une URL nue (format historique, préservé à
    l'identique pour la compatibilité de `bootstrap.upstreams`). Une valeur
    objet ajoute `url` (requis) et `title`/`description`/`tags` (optionnels).
    """
    chemin = path(hub_root)
    if not chemin.is_file():
        return {}
    try:
        charge = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        hublog.warning(f"{chemin} illisible, catalogue ignoré : {exc}")
        return {}
    if not isinstance(charge, dict):
        return {}

    entries: dict[str, CatalogEntry] = {}
    for nom_brut, valeur in charge.items():
        nom = str(nom_brut)
        if isinstance(valeur, str):
            if valeur.strip():
                entries[nom] = CatalogEntry(name=nom, url=valeur)
            continue
        if isinstance(valeur, dict):
            url = valeur.get("url")
            if not isinstance(url, str) or not url.strip():
                hublog.warning(f"catalogue : entrée '{nom}' sans url valide, ignorée")
                continue
            title = valeur.get("title")
            description = valeur.get("description")
            tags_raw = valeur.get("tags")
            tags = (
                tuple(str(t) for t in tags_raw)
                if isinstance(tags_raw, list)
                else ()
            )
            entries[nom] = CatalogEntry(
                name=nom,
                url=url,
                title=str(title) if isinstance(title, str) else None,
                description=str(description) if isinstance(description, str) else None,
                tags=tags,
            )
            continue
        hublog.warning(f"catalogue : entrée '{nom}' de forme inattendue, ignorée")
    return entries


def _read_raw(chemin: Path) -> dict:
    if not chemin.is_file():
        return {}
    charge = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    return charge if isinstance(charge, dict) else {}


def add(
    hub_root: Path,
    name: str,
    url: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: tuple[str, ...] = (),
    overwrite: bool = False,
) -> None:
    """Ajoute (ou remplace, avec `overwrite`) une entrée du catalogue.

    Sérialisation exclusivement via `yaml.safe_dump` (§ 1.7 de la spec) :
    jamais de templating de chaînes, même pour un fichier local non exposé aux
    sessions MCP.
    """
    if not NAME_PATTERN.match(name):
        raise CatalogError(f"nom invalide '{name}' : attendu {NAME_PATTERN.pattern}")
    if not url.strip() or "\n" in url or "\r" in url:
        raise CatalogError("url requise, non vide, sans retour à la ligne")
    if title is not None:
        if "\n" in title or "\r" in title:
            raise CatalogError("title : sans retour à la ligne")
        if len(title) > TITLE_MAX:
            raise CatalogError(f"title : {TITLE_MAX} caractères maximum")
    if description is not None and len(description) > DESCRIPTION_MAX:
        raise CatalogError(f"description : {DESCRIPTION_MAX} caractères maximum")

    chemin = path(hub_root)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    brut = _read_raw(chemin)

    if name in brut and not overwrite:
        raise CatalogError(
            f"'{name}' existe déjà dans le catalogue (--overwrite pour remplacer)"
        )

    if title is None and description is None and not tags:
        brut[name] = url
    else:
        entree: dict = {"url": url}
        if title is not None:
            entree["title"] = title
        if description is not None:
            entree["description"] = description
        if tags:
            entree["tags"] = list(tags)
        brut[name] = entree

    chemin.write_text(
        yaml.safe_dump(brut, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )


def remove(hub_root: Path, name: str) -> bool:
    """Oublie une entrée du catalogue. Ne touche jamais à `bases/` — c'est le
    rôle distinct de `retire`."""
    chemin = path(hub_root)
    brut = _read_raw(chemin)
    if name not in brut:
        return False
    del brut[name]
    chemin.write_text(
        yaml.safe_dump(brut, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    return True


def import_entry(
    config: HubConfig, entries: dict[str, CatalogEntry], name: str
) -> Path:
    """`git clone <url> bases/<nom>` — exactement l'invariant du produit
    (spec § 4.2.3), rien de plus. Choisir `name` dans le catalogue remplace le
    besoin de connaître ou taper l'URL exacte."""
    entree = entries.get(name)
    if entree is None:
        raise CatalogError(f"'{name}' inconnu du catalogue")
    cible = config.bases_dir / name
    if cible.exists():
        raise CatalogError(f"'{cible}' existe déjà")
    config.bases_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--", entree.url, str(cible)],
        capture_output=True,
        check=True,
        timeout=GIT_TIMEOUT_S,
    )
    return cible


# --- retrait --------------------------------------------------------------


def _pending_proposals(base_root: Path) -> list[Path]:
    pending = base_root / "proposals" / "pending"
    if not pending.is_dir():
        return []
    return sorted(p for p in pending.iterdir() if p.is_file() and p.suffix == ".md")


def _remotes(base_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(base_root), "remote"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )
    if result.returncode != 0:
        return []
    return [ligne for ligne in result.stdout.splitlines() if ligne.strip()]


def _push_status(base_root: Path) -> tuple[str, int]:
    """État de synchronisation avec l'amont, **sans aucun accès réseau** (pas de
    `git fetch`) : uniquement ce que ce dépôt sait déjà localement.

    - `"aucun-remote"` : dépôt purement local, rien à pousser nulle part —
      aucun avertissement pertinent.
    - `"sans-suivi"` : un remote existe mais aucune branche amont n'est
      configurée (`@{u}` indéfini) — impossible de confirmer un push.
    - `"en-avance"` (avec le compte) : des commits locaux ne sont pas sur
      l'amont suivi.
    - `"a-jour"` : rien de local en avance sur l'amont suivi.
    """
    if not _remotes(base_root):
        return ("aucun-remote", 0)
    amont = subprocess.run(
        ["git", "-C", str(base_root), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )
    if amont.returncode != 0:
        return ("sans-suivi", 0)
    upstream = amont.stdout.strip()
    compte = subprocess.run(
        ["git", "-C", str(base_root), "rev-list", "--count", f"{upstream}..HEAD"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )
    if compte.returncode != 0:
        return ("sans-suivi", 0)
    try:
        n = int(compte.stdout.strip())
    except ValueError:
        return ("sans-suivi", 0)
    return ("en-avance", n) if n > 0 else ("a-jour", 0)


def retire(config: HubConfig, name: str, *, force: bool = False) -> RetireReport:
    """Cycle de retrait explicite d'une base déployée.

    Avant cette fonction, retirer une base n'était qu'une suppression manuelle
    de répertoire (documentée en prose dans `okf-hub-guide` § 5, jamais
    outillée). Deux garde-fous, jamais bloquants avec `force=True` :

    - aucune proposition ne doit dormir dans `proposals/pending/` — la revue
      passe avant le retrait ;
    - si le dépôt a un remote configuré, sa branche amont suivie ne doit pas
      être en retard sur `HEAD` (vérification **locale uniquement**, aucun
      nouvel accès réseau — un dépôt purement local n'est jamais concerné).

    La suppression elle-même reste ce qu'elle a toujours été : le répertoire du
    bundle sous `bases/`. Rien n'est perdu tant que le dépôt existe ailleurs.
    """
    cible = config.bases_dir / name
    if not cible.is_dir():
        raise CatalogError(f"'{name}' n'est pas déployée dans {config.bases_dir}")

    raisons: list[str] = []

    pendantes = _pending_proposals(cible)
    if pendantes:
        raisons.append(
            f"{len(pendantes)} proposition(s) en attente dans proposals/pending/ — "
            f"la revue passe avant le retrait"
        )

    statut, n = _push_status(cible)
    if statut == "en-avance":
        raisons.append(f"{n} commit(s) local(aux) non poussé(s) vers l'amont suivi")
    elif statut == "sans-suivi":
        raisons.append(
            "un remote est configuré mais aucune branche amont n'est suivie — "
            "impossible de confirmer que le dépôt a été poussé"
        )

    if raisons and not force:
        return RetireReport(removed=False, blocked_reasons=raisons)

    shutil.rmtree(cible)
    return RetireReport(removed=True, blocked_reasons=raisons)
