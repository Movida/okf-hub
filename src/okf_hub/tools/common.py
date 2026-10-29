"""Aides partagées par les outils kb_*."""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

import yaml

from ..errors import INVALID_INPUT, ToolError
from ..registry import Registry

#: Clés de frontmatter retenues par kb_search (§ 5.2 : « frontmatter limité à
#: title, dates, tags »). Le motif couvre les familles de dates d'OKF v0.2
#: (`generated`, `verified`, `stale_after`, `last_modified`) comme les
#: conventions locales (`last-verified`, `updated`, `created`).
_DATE_KEY = re.compile(
    r"(date|^at$|_at$|-at$|verified|modified|stale|generated|updated|created|timestamp|window)",
    re.IGNORECASE,
)
_KEEP_KEYS = ("title", "tags")


def _is_dateish(value) -> bool:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return True
    if isinstance(value, list):
        return bool(value) and all(_is_dateish(v) for v in value)
    return False


def _dated_entries(value) -> list | None:
    """Réduit une liste de mappings à leur `id` et à leurs dates, si elle en porte.

    C'est la forme de `sources` en OKF 0.2 : la date d'une fiche qui reflète une
    source vit dans `sources[].last_modified` (§ 5.1), plus au premier niveau
    comme l'ancien `timestamp`. Sans cette descente, une base migrée en 0.2
    perdait toute date dans les résultats de kb_search. Les autres clés de
    l'entrée (`resource`, `title`…) ne remontent pas : le résumé reste limité à
    title, dates, tags.
    """
    if not isinstance(value, list):
        return None
    reduites = []
    for entree in value:
        if not isinstance(entree, dict):
            continue
        dates = {
            k: v for k, v in entree.items()
            if isinstance(k, str) and (_DATE_KEY.search(k) or _is_dateish(v))
        }
        if dates:
            reduites.append({**({"id": entree["id"]} if "id" in entree else {}), **dates})
    return reduites or None


def frontmatter_digest(frontmatter: dict | None) -> str | None:
    """Rend un sous-ensemble de frontmatter : title, dates, tags (§ 5.2)."""
    if not isinstance(frontmatter, dict):
        return None
    kept: dict = {}
    for key, value in frontmatter.items():
        if not isinstance(key, str):
            continue
        if key in _KEEP_KEYS or _DATE_KEY.search(key) or _is_dateish(value):
            kept[key] = value
        elif (reduites := _dated_entries(value)) is not None:
            kept[key] = reduites
    if not kept:
        return None
    text = yaml.safe_dump(
        kept, allow_unicode=True, default_flow_style=True, sort_keys=False, width=10_000
    ).strip()
    return text


@dataclass(frozen=True)
class Sortie:
    """Sortie d'un outil qui déclare un `output_schema` : le texte, destiné au
    modèle, et les mêmes données en `structuredContent`, destinées à un
    programme. Un outil sans schéma de sortie rend une simple chaîne."""

    texte: str
    donnees: dict


def received_keys(arguments: dict) -> str:
    """Décrit ce que l'outil a reçu — les clés seulement, jamais les valeurs.

    Un paramètre absent a plusieurs causes très différentes côté client : le
    modèle l'a oublié, il l'a mal nommé, il a imbriqué tous les champs sous une
    clé unique, ou le client a transmis des arguments vides. Le message
    d'erreur doit permettre de les distinguer — au lecteur du journal comme à
    la session appelante, qui n'a que ce texte pour se corriger. Les valeurs
    ne sont pas rendues : elles peuvent être longues et sont le contenu même de
    la contribution.
    """
    keys = [str(k) for k in arguments]
    if not keys:
        return "aucun paramètre reçu"
    return "paramètres reçus : " + ", ".join(keys)


def _describe_bad_value(key: str, arguments: dict) -> str:
    if key not in arguments:
        return f"paramètre '{key}' absent"
    value = arguments[key]
    if value is None:
        return f"paramètre '{key}' reçu null"
    if isinstance(value, str):
        return f"paramètre '{key}' reçu vide"
    return f"paramètre '{key}' reçu de type {type(value).__name__}"


def require_str(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(
            INVALID_INPUT,
            f"paramètre '{key}' requis (chaîne non vide) — "
            f"{_describe_bad_value(key, arguments)} ; {received_keys(arguments)}",
        )
    return value.strip()


def optional_bool(arguments: dict, key: str, default: bool = False) -> bool:
    value = arguments.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolError(INVALID_INPUT, f"paramètre '{key}' doit être un booléen")
    return value


def optional_int(arguments: dict, key: str, default: int, minimum: int, maximum: int) -> int:
    value = arguments.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(INVALID_INPUT, f"paramètre '{key}' doit être un entier")
    if value < minimum or value > maximum:
        raise ToolError(
            INVALID_INPUT, f"paramètre '{key}' hors bornes [{minimum}, {maximum}] : {value}"
        )
    return value


def read_text(path) -> str:
    """Lecture UTF-8 (§ 1.6), tolérante aux octets invalides isolés."""
    return path.read_text(encoding="utf-8", errors="replace")


# --- plusieurs bases -----------------------------------------------------------
# Partagé par kb_search et kb_find : même paramètre `base`, même plafond global
# réparti (§ 10.3).

#: Valeur spéciale de `base` désignant toutes les bases enregistrées (§ 10.3).
ALL_BASES = "*"


def resolve_base_names(registry: Registry, arguments: dict) -> list[str]:
    """Normalise `base` : nom unique, liste de noms, ou `"*"` (§ 10.3).

    Valide l'existence de chaque nom explicite *avant* toute recherche — un nom
    inconnu dans une liste échoue tout l'appel, comme le ferait déjà un nom
    unique inconnu ; pas de repli silencieux qui ignorerait une faute de frappe.
    """
    value = arguments.get("base")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ToolError(INVALID_INPUT, "paramètre 'base' requis (nom, liste, ou \"*\")")
        names = registry.names() if value == ALL_BASES else [value]
    elif isinstance(value, list):
        if not value or not all(isinstance(v, str) and v.strip() for v in value):
            raise ToolError(
                INVALID_INPUT, "paramètre 'base' : liste de noms non vides attendue"
            )
        seen: dict[str, None] = {}
        for v in value:
            seen.setdefault(v.strip(), None)
        names = list(seen)
    else:
        raise ToolError(INVALID_INPUT, "paramètre 'base' requis (nom, liste, ou \"*\")")

    for name in names:
        registry.get(name)  # lève UNKNOWN_BASE si absent — validé avant toute recherche.
    return names


def allocate_quota(available: list[int], total: int) -> list[int]:
    """Répartit `total` entre positions selon leur disponibilité (§ 10.3) :
    parts égales entre bases encore actives, sans jamais allouer à une base plus
    qu'elle n'a de résultats — le reliquat profite aux autres bases plutôt que
    d'être perdu. C'est un plafond global unique, pas un plafond par base.
    """
    alloc = [0] * len(available)
    active = [i for i, n in enumerate(available) if n > 0]
    remaining = total
    while remaining > 0 and active:
        share, extra = divmod(remaining, len(active))
        still_active = []
        for idx, pos in enumerate(active):
            want = share + (1 if idx < extra else 0)
            take = min(want, available[pos] - alloc[pos])
            alloc[pos] += take
            remaining -= take
            if alloc[pos] < available[pos]:
                still_active.append(pos)
        active = still_active
    return alloc
