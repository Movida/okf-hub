"""kb_propose (§ 5.5) — seul outil d'écriture du noyau, confiné à proposals/pending/."""

from __future__ import annotations

import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .. import gitops, hublog
from ..errors import INVALID_INPUT, IO_ERROR, ToolError
from ..locking import base_lock, ensure_git_exclude
from ..registry import ACCEPTED_SUBDIR, PENDING_SUBDIR, REJECTED_SUBDIR, Base, Registry
from .common import require_str

TYPES = ("observation", "correction", "addition", "question")
CONFIDENCES = ("high", "medium", "low")

CONCERNS_MAX = 200
CONTENT_MAX_BYTES = 16 * 1024
SOURCES_MIN = 1
SOURCES_MAX = 20
SOURCE_MAX = 300
SUBMITTED_BY_MAX = 100
SUBJECT_CONCERNS_MAX = 60

ID_RETRIES = 16

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "string", "description": "Nom de la base (champ `name` du manifeste)."},
        "type": {
            "type": "string",
            "enum": list(TYPES),
            "description": (
                "observation : fait constaté, sans présumer d'un document existant. "
                "correction : contredit un contenu actuel. "
                "addition : complète un sujet déjà couvert. "
                "question : lacune identifiée, sans réponse fournie."
            ),
        },
        "concerns": {
            "type": "string",
            "maxLength": CONCERNS_MAX,
            "description": "Sujet concerné, en une ligne. Sans retour à la ligne.",
        },
        "content": {
            "type": "string",
            "description": "L'affirmation elle-même, en markdown. 16 Ko maximum.",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string", "maxLength": SOURCE_MAX},
            "minItems": SOURCES_MIN,
            "maxItems": SOURCES_MAX,
            "description": (
                "D'où vient l'affirmation : URL, référence d'incident, constat "
                "terrain… Une entrée par ligne, sans retour à la ligne."
            ),
        },
        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
        "submitted_by": {
            "type": "string",
            "maxLength": SUBMITTED_BY_MAX,
            "description": (
                "Identité déclarée du contributeur — non authentifiée. "
                "Convention recommandée : `human:<id>`, `<agent>/<version>` ou "
                "`process:<id>`."
            ),
        },
    },
    "required": ["base", "type", "concerns", "content", "sources", "confidence", "submitted_by"],
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    bases = registry.ordered()
    head = (
        "Dépose une proposition d'ajout ou de correction dans une base. "
        "La proposition n'est PAS intégrée automatiquement : elle est déposée "
        "dans proposals/pending/ et attend la revue du gestionnaire, qui "
        "l'intègre ou la rejette selon les règles de la base (kb_governance). "
        "Le verdict — intégration et documents modifiés, ou motif de rejet — se "
        "consulte ensuite avec kb_proposal_status, en lui donnant l'id retourné "
        "ici. kb_list avec include_pending_concerns permet de vérifier avant "
        "soumission qu'une proposition proche n'est pas déjà en attente.\n\n"
        "Le `schema.yaml` d'une base décrit le frontmatter de son CORPUS, pas "
        "celui des propositions. Une proposition n'a pas à s'y conformer : "
        "soumettez l'information, sa mise en forme conforme au schéma relève du "
        "gestionnaire à l'intégration. Les champs de cet outil sont le seul "
        "format requis."
    )
    if not bases:
        return head + " Aucune base n'est actuellement enregistrée."
    lines = [head, "", "Bases contributables :"]
    for base in bases:
        lines.append(f"- {base.name} — {base.manifest.title} : {base.manifest.description}")
    return "\n".join(lines)


# --- validation --------------------------------------------------------------


def _no_newline(value: str, field: str) -> str:
    """Validation anti-injection normative (§ 5.5).

    Ces champs sont injectés dans le sujet et les trailers du message de commit.
    Un retour à la ligne y permettrait de forger de faux trailers et de corrompre
    les invariants d'audit basés sur `git log --grep` (§ 6.2).
    """
    if "\n" in value or "\r" in value:
        raise ToolError(
            INVALID_INPUT,
            f"le champ '{field}' ne doit pas contenir de retour à la ligne",
        )
    return value


def _validate(arguments: dict) -> dict:
    ptype = require_str(arguments, "type")
    if ptype not in TYPES:
        raise ToolError(INVALID_INPUT, f"type '{ptype}' invalide (attendu : {', '.join(TYPES)})")

    confidence = require_str(arguments, "confidence")
    if confidence not in CONFIDENCES:
        raise ToolError(
            INVALID_INPUT,
            f"confidence '{confidence}' invalide (attendu : {', '.join(CONFIDENCES)})",
        )

    concerns = _no_newline(require_str(arguments, "concerns"), "concerns")
    if len(concerns) > CONCERNS_MAX:
        raise ToolError(
            INVALID_INPUT, f"concerns dépasse {CONCERNS_MAX} caractères ({len(concerns)})"
        )

    submitted_by = _no_newline(require_str(arguments, "submitted_by"), "submitted_by")
    if len(submitted_by) > SUBMITTED_BY_MAX:
        raise ToolError(
            INVALID_INPUT,
            f"submitted_by dépasse {SUBMITTED_BY_MAX} caractères ({len(submitted_by)})",
        )

    content = arguments.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ToolError(INVALID_INPUT, "paramètre 'content' requis (chaîne non vide)")
    size = len(content.encode("utf-8"))
    if size > CONTENT_MAX_BYTES:
        raise ToolError(
            INVALID_INPUT, f"content dépasse {CONTENT_MAX_BYTES} octets ({size})"
        )

    raw_sources = arguments.get("sources")
    if not isinstance(raw_sources, list):
        raise ToolError(INVALID_INPUT, "paramètre 'sources' requis (liste de chaînes)")
    if not (SOURCES_MIN <= len(raw_sources) <= SOURCES_MAX):
        raise ToolError(
            INVALID_INPUT,
            f"sources doit contenir entre {SOURCES_MIN} et {SOURCES_MAX} entrées "
            f"({len(raw_sources)} reçues)",
        )
    sources: list[str] = []
    for i, item in enumerate(raw_sources):
        if not isinstance(item, str) or not item.strip():
            raise ToolError(INVALID_INPUT, f"sources[{i}] doit être une chaîne non vide")
        item = _no_newline(item.strip(), f"sources[{i}]")
        if len(item) > SOURCE_MAX:
            raise ToolError(
                INVALID_INPUT, f"sources[{i}] dépasse {SOURCE_MAX} caractères ({len(item)})"
            )
        sources.append(item)

    return {
        "type": ptype,
        "concerns": concerns,
        "content": content,
        "sources": sources,
        "confidence": confidence,
        "submitted_by": submitted_by,
    }


# --- identifiant -------------------------------------------------------------


def _existing_ids(base: Base) -> set[str]:
    """Identifiants déjà utilisés, tous statuts confondus.

    Une proposition résolue conserve son id en migrant vers accepted/ ou
    rejected/ : la vérification doit couvrir les trois répertoires.
    """
    ids: set[str] = set()
    for sub in (PENDING_SUBDIR, ACCEPTED_SUBDIR, REJECTED_SUBDIR):
        d = base.root / sub
        if d.is_dir():
            ids.update(p.stem for p in d.iterdir() if p.suffix == ".md")
    return ids


def _new_id(base: Base, day: str) -> str:
    taken = _existing_ids(base)
    for _ in range(ID_RETRIES):
        candidate = f"prop-{day}-{secrets.token_hex(2)}"
        if candidate not in taken:
            return candidate
        hublog.warning(f"collision d'id {candidate} — nouveau tirage")
    raise ToolError(
        IO_ERROR,
        f"impossible de générer un identifiant libre après {ID_RETRIES} tirages",
    )


# --- rendu du fichier --------------------------------------------------------


def render_proposal(fields: dict, prop_id: str, submitted_at: str) -> str:
    """Sérialise la proposition au format § 6.1.

    Le frontmatter passe exclusivement par la bibliothèque YAML (principe
    § 1.7) : les caractères spéciaux (`---`, `:`, guillemets) sont neutralisés
    par l'échappement standard, jamais par un filtrage manuel.
    """
    frontmatter = {
        "id": prop_id,
        "submitted-by": fields["submitted_by"],
        "submitted-at": submitted_at,
        "type": fields["type"],
        "concerns": fields["concerns"],
        "sources": list(fields["sources"]),
        "confidence": fields["confidence"],
        "status": "pending",
    }
    block = yaml.safe_dump(
        frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False, width=10_000
    )
    body = fields["content"].strip()
    return f"---\n{block}---\n\n{body}\n"


def _write_atomic(target: Path, text: str) -> None:
    """Écrit via un temporaire du même répertoire puis `rename()` (§ 5.5.3).

    Un crash en cours d'écriture ne laisse jamais de `.md` tronqué dans
    pending/ : `rename()` est atomique au sein d'un même système de fichiers.
    Le temporaire est préfixé d'un point pour ne pas ressembler à une
    proposition si le processus meurt avant le rename.
    """
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".okf-tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_proposal_dirs(base: Base) -> list[Path]:
    """Crée proposals/pending|accepted|rejected avec .gitkeep (§ 3.1).

    Créés au premier kb_propose, jamais à la découverte — qui reste sans effet
    de bord. Retourne les .gitkeep nouvellement créés, à inclure dans le commit.
    """
    created: list[Path] = []
    for sub in (PENDING_SUBDIR, ACCEPTED_SUBDIR, REJECTED_SUBDIR):
        d = base.root / sub
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not keep.exists():
            keep.touch()
            created.append(keep)
    return created


# --- outil -------------------------------------------------------------------


def run(registry: Registry, arguments: dict) -> str:
    base = registry.get(require_str(arguments, "base"))
    fields = _validate(arguments)

    now = datetime.now(timezone.utc)
    submitted_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    day = now.strftime("%Y-%m-%d")

    # Toute la séquence — création des répertoires, écriture atomique, commit —
    # sous une seule acquisition du verrou (§ 4.4.b).
    with base_lock(base.root):
        ensure_git_exclude(base.root)
        keepers = _ensure_proposal_dirs(base)
        prop_id = _new_id(base, day)
        target = base.root / PENDING_SUBDIR / f"{prop_id}.md"

        try:
            _write_atomic(target, render_proposal(fields, prop_id, submitted_at))
        except OSError as exc:
            raise ToolError(IO_ERROR, f"écriture de la proposition impossible : {exc}") from exc

        # `rstrip` : git dépouille lui-même les blancs de fin du sujet, autant
        # que le message écrit corresponde à ce que `git log` restituera.
        subject = (
            f"proposal: {prop_id} ({fields['type']}) — "
            f"{fields['concerns'][:SUBJECT_CONCERNS_MAX].rstrip()}"
        )
        message = gitops.build_message(subject, [("Submitted-By", fields["submitted_by"])])
        gitops.commit_paths(base.root, [target] + keepers, message)

    rel = f"{PENDING_SUBDIR}/{prop_id}.md"
    return (
        f"Proposition déposée.\n"
        f"id : {prop_id}\n"
        f"chemin : {rel}\n"
        f"base : {base.name}\n\n"
        f"Elle est en attente de revue par le gestionnaire de la base. "
        f"Le corpus n'a pas été modifié.\n"
        f"Pour connaître le verdict plus tard : "
        f"kb_proposal_status(base: \"{base.name}\", id: \"{prop_id}\")."
    )
