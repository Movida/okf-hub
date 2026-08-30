"""Statut de maturité d'un GOVERNANCE.md (amendement rév. 4.1, § B5).

Un `GOVERNANCE.md` peut porter un frontmatter optionnel :

```yaml
---
status: draft        # draft | stable — défaut si absent : stable
---
```

C'est une **convention documentée, pas une machine à états** : rien n'est
interdit en `draft`, les propositions restent acceptées et la revue reste
possible. Le statut sert uniquement à avertir — la session consommatrice via
`kb_governance`, l'humain via la skill `kb-review` — que les règles appliquées
ne sont pas encore validées par le propriétaire de la base.

Le défaut est `stable` : une base antérieure à cette convention, ou un
`GOVERNANCE.md` sans frontmatter, ne devient pas subitement un brouillon.
"""

from __future__ import annotations

from pathlib import Path

from . import hublog
from .mdutil import parse_document

DRAFT = "draft"
STABLE = "stable"
KNOWN_STATUSES = (DRAFT, STABLE)
DEFAULT_STATUS = STABLE

#: Bandeau préfixé à la sortie de `kb_governance` (§ B5).
DRAFT_BANNER = (
    "[GOUVERNANCE EN BROUILLON — les règles peuvent évoluer, "
    "les propositions restent acceptées]"
)


def status_of_text(text: str, *, source: str = "GOVERNANCE.md") -> str:
    """Statut déclaré par le frontmatter d'un GOVERNANCE.md.

    Toute valeur inconnue est traitée comme `stable` et journalisée : un
    avertissement mal orthographié ne doit pas silencieusement basculer une base
    en brouillon, ni faire échouer la lecture des règles (principe § 1.4).
    """
    frontmatter = parse_document(text).frontmatter
    if not isinstance(frontmatter, dict):
        return DEFAULT_STATUS
    raw = frontmatter.get("status")
    if raw is None:
        return DEFAULT_STATUS
    value = str(raw).strip().casefold()
    if value in KNOWN_STATUSES:
        return value
    hublog.warning(
        f"{source} : `status: {raw}` non reconnu (attendu {' ou '.join(KNOWN_STATUSES)}) "
        f"— traité comme {DEFAULT_STATUS}"
    )
    return DEFAULT_STATUS


def status_of_file(path: Path) -> str:
    """Idem, depuis un fichier. Un fichier illisible ne vaut pas brouillon."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        hublog.warning(f"statut de gouvernance indéterminé ({path}) : {exc}")
        return DEFAULT_STATUS
    return status_of_text(text, source=str(path))


def is_draft(path: Path) -> bool:
    return status_of_file(path) == DRAFT
