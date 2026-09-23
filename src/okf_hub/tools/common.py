"""Aides partagées par les outils kb_*."""

from __future__ import annotations

import datetime as _dt
import re

import yaml

from ..errors import INVALID_INPUT, ToolError

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
