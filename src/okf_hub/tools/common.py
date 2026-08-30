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
    if not kept:
        return None
    text = yaml.safe_dump(
        kept, allow_unicode=True, default_flow_style=True, sort_keys=False, width=10_000
    ).strip()
    return text


def require_str(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(INVALID_INPUT, f"paramètre '{key}' requis (chaîne non vide)")
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
