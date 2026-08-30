"""kb_list (§ 5.1)."""

from __future__ import annotations

from dataclasses import dataclass

from ..mdutil import parse_document
from ..registry import Base, Registry
from ..textutil import BudgetedWriter, truncate_chars
from .common import optional_bool, read_text

SCHEMA = {
    "type": "object",
    "properties": {
        "include_pending_concerns": {
            "type": "boolean",
            "default": False,
            "description": (
                "Ajoute, pour chaque base, la liste (id, type, concerns) des "
                "propositions en attente. Utile pour éviter de soumettre un "
                "doublon, et pour inventorier à moindre coût."
            ),
        }
    },
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    """Description de l'outil, régénérée à chaque rescan (§ 5.1).

    Elle énumère les bases connues : c'est ce qui permet à une session de router
    sa requête sans appel préalable.
    """
    bases = registry.ordered()
    head = (
        "Liste les bases de connaissance disponibles sur ce hub, avec leur "
        "objet, leur nombre de documents et le nombre de propositions en attente."
    )
    if not bases:
        return head + " Aucune base n'est actuellement enregistrée."
    lines = [head, "", "Bases connues :"]
    for base in bases:
        lines.append(f"- {base.name} — {base.manifest.title} : {base.manifest.description}")
    return "\n".join(lines)


@dataclass
class _Pending:
    id: str
    type: str
    concerns: str


def _pending_entries(base: Base) -> list[_Pending]:
    out: list[_Pending] = []
    for path in base.pending_files():
        try:
            doc = parse_document(read_text(path))
        except OSError:
            continue
        fm = doc.frontmatter or {}
        out.append(
            _Pending(
                id=str(fm.get("id") or path.stem),
                type=str(fm.get("type") or "?"),
                concerns=str(fm.get("concerns") or "(non renseigné)"),
            )
        )
    return out


def run(registry: Registry, arguments: dict) -> str:
    include_concerns = optional_bool(arguments, "include_pending_concerns", False)
    bases = registry.ordered()
    if not bases:
        return (
            "Aucune base enregistrée. Importez un bundle avec "
            "`git clone <url> bases/<nom>` puis appelez kb_hub_rescan."
        )

    writer = BudgetedWriter()
    pending_by_base: dict[str, list[_Pending]] = {}

    for base in bases:
        m = base.manifest
        pending = base.pending_files()
        pending_by_base[base.name] = []
        block = [
            f"## {m.name} — {m.title}",
            m.description,
            f"documents : {base.count_documents()} | propositions en attente : {len(pending)}",
        ]
        if m.version:
            block.insert(2, f"version : {m.version}")
        writer.add("\n".join(block))

    if include_concerns:
        # Les listes de concerns sont tronquées en priorité (§ 5.1) : elles ne
        # sont ajoutées qu'une fois tous les résumés de bases émis.
        for base in bases:
            entries = _pending_entries(base)
            if not entries:
                continue
            lines = [f"### propositions en attente — {base.name}"]
            for e in entries:
                lines.append(
                    f"- {e.id} ({e.type}) — {truncate_chars(e.concerns, 200)}"
                )
            writer.add("\n".join(lines))

    return writer.render(
        "[résultats tronqués : listes de propositions en attente incomplètes — "
        "utilisez kb_list par base ou consultez proposals/pending/]"
    )
