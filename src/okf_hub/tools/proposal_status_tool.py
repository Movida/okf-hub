"""kb_proposal_status (amendement rév. 4.1, § B1).

Lève la limitation v0 du § 6.2 : un contributeur MCP-only pouvait voir ce qui
était en attente, jamais la résolution de ses propositions. Cet outil rend
lisibles `proposals/accepted/` et `proposals/rejected/`.

Lecture pure : aucun verrou, aucun état nouveau, git reste canonique. C'est
aussi la **seule exception** à la liste d'exclusions transverse du § 5.2, qui
retire `proposals/` des lectures — exception limitée à cet outil et en lecture
seule ; le confinement passe par `Base.proposal_files` (§ 5.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import hublog
from ..errors import INVALID_INPUT, NOT_FOUND, ToolError
from ..mdutil import parse_document
from ..registry import PROPOSAL_SUBDIRS, Base, Registry
from ..textutil import BudgetedWriter, truncate_chars
from .common import optional_int, read_text, require_str

STATUSES = tuple(PROPOSAL_SUBDIRS)

DEFAULT_LIMIT = 20
HARD_MAX_LIMIT = 50

CONCERNS_DISPLAY_MAX = 200
REASON_DISPLAY_MAX = 500

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "string", "description": "Nom de la base (champ `name` du manifeste)."},
        "id": {
            "type": "string",
            "description": "Identifiant exact d'une proposition, tel que retourné par kb_propose.",
        },
        "submitted_by": {
            "type": "string",
            "description": (
                "Filtre par contributeur déclaré. Correspondance exacte, casse "
                "ignorée. Ce champ n'étant pas authentifié, le filtre retrouve "
                "les propositions déclarées sous ce nom — sans garantie d'identité."
            ),
        },
        "status": {
            "type": "string",
            "enum": list(STATUSES),
            "description": "Restreint aux propositions de ce statut.",
        },
        "limit": {
            "type": "integer",
            "default": DEFAULT_LIMIT,
            "minimum": 1,
            "maximum": HARD_MAX_LIMIT,
            "description": "Nombre de propositions retournées, les plus récentes d'abord.",
        },
    },
    "required": ["base"],
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    head = (
        "Consulte l'état et la résolution des propositions déposées via "
        "kb_propose : en attente, intégrée (avec les documents modifiés), ou "
        "rejetée (avec le motif). C'est ainsi qu'un contributeur retrouve le "
        "verdict de sa proposition sans accès git au dépôt. "
        "Fournissez `id` (une proposition précise) ou `submitted_by` (tout ce "
        "qu'un contributeur a déposé) — au moins l'un des deux est requis. "
        "`submitted_by` étant déclaratif et non authentifié, le filtre retrouve "
        "les propositions déclarées sous ce nom, sans garantie d'identité. "
        "Le corps des propositions n'est pas retourné : pour lire ce qui a été "
        "intégré, suivez `integrated-into` avec kb_read."
    )
    return head + " Bases disponibles : " + (", ".join(registry.names()) or "aucune") + "."


# --- collecte -----------------------------------------------------------------


@dataclass
class Entry:
    """Une proposition telle que lue sur disque."""

    id: str
    status: str
    """Statut déduit de l'emplacement — c'est lui qui fait foi (§ 6.2)."""
    declared_status: str
    """Champ `status` du frontmatter, affiché mais jamais utilisé pour décider."""
    frontmatter: dict
    path: Path

    @property
    def submitted_at(self) -> str:
        return str(self.frontmatter.get("submitted-at") or "")

    @property
    def submitted_by(self) -> str:
        return str(self.frontmatter.get("submitted-by") or "")

    @property
    def inconsistent(self) -> bool:
        """`status` du frontmatter en désaccord avec l'emplacement.

        Un frontmatter dépourvu de `status` n'est pas une incohérence : le champ
        est optionnel à la lecture, l'emplacement suffit.
        """
        return bool(self.declared_status) and self.declared_status != self.status


@dataclass
class Collected:
    entries: list[Entry]
    unreadable: int


def _collect(base: Base, statuses: tuple[str, ...]) -> Collected:
    entries: list[Entry] = []
    unreadable = 0
    for status in statuses:
        for path in base.proposal_files(status):
            try:
                doc = parse_document(read_text(path))
            except OSError as exc:
                unreadable += 1
                hublog.warning(f"proposition illisible ignorée : {path} ({exc})")
                continue
            if not isinstance(doc.frontmatter, dict) or not doc.frontmatter:
                # Frontmatter absent ou non parseable : la proposition n'a pas
                # d'identité exploitable, on ne devine pas.
                unreadable += 1
                hublog.warning(f"frontmatter illisible ignoré : {path}")
                continue
            fm = doc.frontmatter
            entries.append(
                Entry(
                    id=str(fm.get("id") or path.stem),
                    status=status,
                    declared_status=str(fm.get("status") or "").strip(),
                    frontmatter=fm,
                    path=path,
                )
            )
    # Les plus récentes d'abord ; une soumission sans date passe en fin de liste.
    entries.sort(key=lambda e: (e.submitted_at, e.id), reverse=True)
    return Collected(entries=entries, unreadable=unreadable)


# --- rendu --------------------------------------------------------------------


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _render(entry: Entry) -> str:
    fm = entry.frontmatter
    lines = [f"### {entry.id} — {entry.status}"]
    if entry.inconsistent:
        lines.append(
            f"[incohérence status/emplacement : frontmatter `status: "
            f"{entry.declared_status}`, emplacement `{entry.status}` — "
            f"l'emplacement fait foi]"
        )
    lines.append(
        f"type : {fm.get('type') or '?'} | concerns : "
        f"{truncate_chars(str(fm.get('concerns') or '(non renseigné)'), CONCERNS_DISPLAY_MAX)}"
    )
    lines.append(
        f"soumise : {entry.submitted_at or '(date inconnue)'} "
        f"par {entry.submitted_by or '(contributeur non renseigné)'}"
    )

    if entry.status == "pending":
        lines.append("résolution : en attente de revue par le gestionnaire")
        return "\n".join(lines)

    resolved_at = str(fm.get("resolved-at") or "(date inconnue)")
    resolution = str(fm.get("resolution") or entry.status)
    lines.append(f"résolue : {resolved_at} — {resolution}")

    if entry.status == "accepted":
        into = _as_list(fm.get("integrated-into"))
        if into:
            lines.append(
                "integrated-into : " + ", ".join(into) + "  (lisibles via kb_read)"
            )
        else:
            lines.append("integrated-into : (non renseigné)")
    else:
        reason = str(fm.get("rejection-reason") or "").strip()
        lines.append(
            "rejection-reason : "
            + (truncate_chars(reason, REASON_DISPLAY_MAX) if reason else "(non renseigné)")
        )
    return "\n".join(lines)


# --- outil --------------------------------------------------------------------


def _optional_str(arguments: dict, key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(INVALID_INPUT, f"paramètre '{key}' doit être une chaîne")
    value = value.strip()
    return value or None


def run(registry: Registry, arguments: dict) -> str:
    base = registry.get(require_str(arguments, "base"))
    prop_id = _optional_str(arguments, "id")
    submitted_by = _optional_str(arguments, "submitted_by")
    status = _optional_str(arguments, "status")
    limit = optional_int(arguments, "limit", DEFAULT_LIMIT, 1, HARD_MAX_LIMIT)

    if prop_id is None and submitted_by is None:
        # Sans filtre, l'outil déverserait l'intégralité de proposals/ : c'est
        # exactement ce que le plafond de sortie est censé éviter.
        raise ToolError(
            INVALID_INPUT,
            "fournissez au moins 'id' (une proposition précise) ou "
            "'submitted_by' (tout ce qu'un contributeur a déposé)",
        )
    if status is not None and status not in STATUSES:
        raise ToolError(
            INVALID_INPUT,
            f"status '{status}' invalide (attendu : {', '.join(STATUSES)})",
        )

    statuses = (status,) if status else STATUSES
    collected = _collect(base, statuses)

    entries = collected.entries
    if prop_id is not None:
        entries = [e for e in entries if e.id == prop_id]
    if submitted_by is not None:
        cible = submitted_by.casefold()
        entries = [e for e in entries if e.submitted_by.strip().casefold() == cible]

    if prop_id is not None and not entries:
        # NOT_FOUND ne vaut que pour un id : un contributeur sans proposition
        # n'est pas une erreur, c'est un résultat vide.
        portee = (
            f"le répertoire '{status}'" if status else "aucun des trois répertoires"
        )
        raise ToolError(
            NOT_FOUND,
            f"proposition '{prop_id}' introuvable dans {portee} de "
            f"proposals/ pour la base '{base.name}'",
        )

    filtres = []
    if prop_id:
        filtres.append(f"id={prop_id}")
    if submitted_by:
        filtres.append(f"submitted_by={submitted_by}")
    if status:
        filtres.append(f"status={status}")
    entete_filtres = ", ".join(filtres)

    if not entries:
        note = ""
        if collected.unreadable:
            note = f"\n[{collected.unreadable} fichier(s) illisible(s) ignoré(s)]"
        return (
            f"Aucune proposition dans '{base.name}' pour : {entete_filtres}."
            f"{note}"
        )

    total = len(entries)
    retenues = entries[:limit]

    writer = BudgetedWriter()
    entete = f"{total} proposition(s) dans '{base.name}' pour : {entete_filtres}"
    if total > limit:
        entete += f"\n[{total - limit} plus ancienne(s) non listée(s) — augmentez limit]"
    if collected.unreadable:
        entete += f"\n[{collected.unreadable} fichier(s) illisible(s) ignoré(s)]"
    writer.add_forced(entete)

    for entry in retenues:
        writer.add(_render(entry))

    return writer.render(
        "[résultats tronqués — affinez les filtres ou réduisez limit]"
    )
