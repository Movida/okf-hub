"""kb_governance (§ 5.4)."""

from __future__ import annotations

from ..errors import IO_ERROR, ToolError
from ..governance import DRAFT_BANNER, status_of_text
from ..registry import Registry
from .common import read_text, require_str

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "string", "description": "Nom de la base (champ `name` du manifeste)."}
    },
    "required": ["base"],
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    return (
        "Retourne les règles de gouvernance d'une base (GOVERNANCE.md) et son "
        "schéma de frontmatter (schema.yaml) s'il existe. À lire avant de "
        "soumettre une proposition via kb_propose, et indispensable au rôle "
        "gestionnaire. Une base dont les règles ne sont pas encore validées le "
        "signale par un bandeau en tête de sortie ; les propositions y restent "
        "acceptées. "
        "Bases disponibles : " + (", ".join(registry.names()) or "aucune") + "."
    )


def run(registry: Registry, arguments: dict) -> str:
    base = registry.get(require_str(arguments, "base"))
    m = base.manifest

    try:
        rules = read_text(m.governance_rules)
    except OSError as exc:
        raise ToolError(
            IO_ERROR, f"GOVERNANCE.md illisible pour la base '{base.name}' : {exc}"
        ) from exc

    parts: list[str] = []
    # Le bandeau vient en tête, avant le titre : c'est le premier élément lu par
    # une session, et il conditionne la confiance à accorder aux règles (§ B5).
    if status_of_text(rules, source=str(m.governance_rules)) == "draft":
        parts += [DRAFT_BANNER, ""]
    parts += [f"# Gouvernance — {m.name} ({m.title})", "", f"## {m.governance_rules.name}", ""]
    parts.append(rules)

    if m.frontmatter_schema is not None:
        parts += ["", f"## {m.frontmatter_schema.name} (schéma de frontmatter)", ""]
        try:
            parts.append("```yaml\n" + read_text(m.frontmatter_schema).rstrip() + "\n```")
        except OSError as exc:
            parts.append(f"[schéma illisible : {exc}]")
    else:
        parts += ["", "[aucun schema.yaml déclaré pour cette base]"]

    return "\n".join(parts)
