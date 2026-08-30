"""kb_read (§ 5.3)."""

from __future__ import annotations

from ..errors import NOT_FOUND, ToolError
from ..mdutil import extract_section, headings_table, parse_document
from ..registry import Registry
from .common import optional_bool, read_text, require_str

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "string", "description": "Nom de la base (champ `name` du manifeste)."},
        "path": {
            "type": "string",
            "description": "Chemin du document, relatif au corpus, séparateur `/`.",
        },
        "section": {
            "type": "string",
            "description": (
                "Titre du heading à extraire. La correspondance ignore la casse "
                "et le formatage markdown inline."
            ),
        },
        "force": {
            "type": "boolean",
            "default": False,
            "description": (
                "Retourne le document entier même s'il est volumineux, au lieu "
                "de sa table des headings."
            ),
        },
    },
    "required": ["base", "path"],
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    return (
        "Lit un document du corpus d'une base : document complet, ou une seule "
        "section si `section` est fourni. Au-delà d'un certain volume, un "
        "document lu sans `section` retourne sa table des headings plutôt que "
        "son contenu — rappelez alors kb_read avec la section voulue, ou "
        "`force: true` pour tout obtenir. "
        "Bases disponibles : " + (", ".join(registry.names()) or "aucune") + "."
    )


def _headings_listing(text: str) -> list[str]:
    rows = headings_table(text)
    if not rows:
        return ["(aucun heading dans ce document)"]
    out = []
    for heading, size in rows:
        indent = "  " * (heading.level - 1)
        out.append(f"{indent}- {heading.text}  (~{size} octets)")
    return out


def run(registry: Registry, arguments: dict) -> str:
    base = registry.get(require_str(arguments, "base"))
    rel = require_str(arguments, "path")
    section = arguments.get("section")
    if section is not None and (not isinstance(section, str) or not section.strip()):
        section = None
    force = optional_bool(arguments, "force", False)

    full_path = base.resolve_document(rel)
    try:
        text = read_text(full_path)
    except OSError as exc:
        from ..errors import IO_ERROR

        raise ToolError(IO_ERROR, f"lecture de '{rel}' impossible : {exc}") from exc

    rel_norm = base.rel_path(full_path)

    # --- extraction d'une section -------------------------------------------
    if section:
        match = extract_section(text, section)
        if match is None:
            listing = "\n".join(_headings_listing(text))
            raise ToolError(
                NOT_FOUND,
                f"section '{section}' introuvable dans '{rel_norm}'. "
                f"Headings disponibles :\n{listing}",
            )
        parts = [f"# {rel_norm} — section « {match.heading.text} »"]
        if match.duplicates:
            parts.append(f"[{match.duplicates} autre(s) section(s) portent ce titre]")
        parts.append("")
        parts.append(match.content)
        return "\n".join(parts)

    # --- mode table des headings (gros document) ----------------------------
    size = len(text.encode("utf-8"))
    threshold = registry.config.read_toc_threshold
    if size > threshold and not force:
        parsed = parse_document(text)
        parts = [
            f"# {rel_norm}",
            f"[document volumineux : {size} octets > seuil {threshold} — "
            f"table des headings retournée à la place du contenu. "
            f"Rappelez kb_read avec `section: \"<titre>\"`, ou `force: true` "
            f"pour le document entier.]",
        ]
        if parsed.frontmatter_raw:
            parts += ["", parsed.frontmatter_raw]
        parts += ["", "## Headings"]
        parts += _headings_listing(text)
        return "\n".join(parts)

    return text
