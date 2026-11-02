"""kb_list (§ 5.1)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from ..lecture.confiance import instant
from ..mdutil import parse_document
from ..registry import Registry
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
        "objet, leur nombre de documents, le nombre de propositions en attente "
        "et la date de la plus ancienne."
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
    submitted_at: dt.datetime | None


def _pending_entries(files: list[Path]) -> list[_Pending]:
    out: list[_Pending] = []
    for path in files:
        try:
            doc = parse_document(read_text(path))
        except OSError:
            continue
        fm = doc.frontmatter if isinstance(doc.frontmatter, dict) else {}
        out.append(
            _Pending(
                id=str(fm.get("id") or path.stem),
                type=str(fm.get("type") or "?"),
                concerns=str(fm.get("concerns") or "(non renseigné)"),
                submitted_at=instant(fm.get("submitted-at")),
            )
        )
    return out


def _oldest(entries: list[_Pending]) -> str | None:
    """Date (UTC) de la plus ancienne proposition en attente (rév. 4.7), d'après
    `submitted-at` (§ 6.1). Une proposition sans date lisible est comptée, pas
    datée."""
    dates = [e.submitted_at for e in entries if e.submitted_at is not None]
    if not dates:
        return None
    return min(dates).astimezone(dt.timezone.utc).date().isoformat()


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
        entries = _pending_entries(pending)
        pending_by_base[base.name] = entries
        counts = f"documents : {base.count_documents()} | propositions en attente : {len(pending)}"
        oldest = _oldest(entries)
        if oldest:
            counts += f" (la plus ancienne : {oldest})"
        block = [f"## {m.name} — {m.title}", m.description, counts]
        if m.version:
            block.insert(2, f"version : {m.version}")
        writer.add("\n".join(block))

    if include_concerns:
        # Les listes de concerns sont tronquées en priorité (§ 5.1) : elles ne
        # sont ajoutées qu'une fois tous les résumés de bases émis.
        for base in bases:
            entries = pending_by_base[base.name]
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
