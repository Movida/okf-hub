"""kb_search (§ 5.2)."""

from __future__ import annotations

from ..mdutil import heading_at_line, parse_document, parse_headings
from ..registry import Base, Registry
from ..search import DocHits, run_search
from ..textutil import BudgetedWriter, truncate_chars
from .common import frontmatter_digest, optional_int, read_text, require_str

DEFAULT_MAX_RESULTS = 8
HARD_MAX_RESULTS = 25
CONTEXT_LINES = 2
MAX_EXCERPTS_PER_DOC = 3

#: Libellé de section d'un extrait situé avant tout heading (§ B3).
PREAMBLE_LABEL = "(préambule)"
SECTION_LABEL_MAX = 120

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "string", "description": "Nom de la base (champ `name` du manifeste)."},
        "query": {
            "type": "string",
            "description": (
                "Termes recherchés. En mode keyword, les termes sont séparés "
                "par des espaces et combinés en ET."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["keyword", "regex"],
            "default": "keyword",
            "description": (
                "keyword : ET strict sur les termes, repli automatique en OU si "
                "aucun document ne les contient tous. regex : dialecte ripgrep "
                "(syntaxe Rust regex)."
            ),
        },
        "max_results": {
            "type": "integer",
            "default": DEFAULT_MAX_RESULTS,
            "minimum": 1,
            "maximum": HARD_MAX_RESULTS,
        },
    },
    "required": ["base", "query"],
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    bases = registry.ordered()
    head = (
        "Recherche plein texte dans le corpus d'une base. Retourne les chemins, "
        "titres et extraits pertinents — jamais les documents entiers. "
        "Chaque extrait est annoté du heading de sa section, après « § » : "
        "reportez-le tel quel dans kb_read(path, section) pour lire la section "
        "entière sans rapatrier tout le document."
    )
    if not bases:
        return head + " Aucune base n'est actuellement enregistrée."
    lines = [head, "", "Bases interrogeables :"]
    for base in bases:
        lines.append(f"- {base.name} — {base.manifest.title} : {base.manifest.description}")
    return "\n".join(lines)


def _excerpts(text: str, hit_lines: list[int]) -> list[str]:
    """Extraits : ligne touchée ± 2 lignes, au plus 3 par document (§ 5.2).

    Les fenêtres qui se recouvrent sont fusionnées, sinon la même ligne serait
    répétée dans plusieurs extraits.

    Chaque extrait porte le heading de la section contenant **la ligne touchée**
    (§ B3) — et non celui du début de la fenêtre de contexte, qui peut déborder
    sur la section précédente. Le libellé est la forme normalisée du § 11.4,
    celle que `kb_read` attend dans `section` : le chaînage
    `kb_search` → `kb_read(path, section)` se fait donc sans détour par la table
    des headings.
    """
    lines = text.split("\n")
    headings = parse_headings(text)

    # [début, fin, ligne touchée servant d'ancre de section]
    windows: list[list[int]] = []
    for line_no in hit_lines:
        start = max(1, line_no - CONTEXT_LINES)
        end = min(len(lines), line_no + CONTEXT_LINES)
        if windows and start <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end, line_no])
        if len(windows) > MAX_EXCERPTS_PER_DOC:
            break

    out: list[str] = []
    for start, end, anchor in windows[:MAX_EXCERPTS_PER_DOC]:
        # `anchor` est en base 1 (ripgrep), les headings en base 0.
        heading = heading_at_line(headings, anchor - 1)
        label = truncate_chars(
            heading.normalized if heading else PREAMBLE_LABEL, SECTION_LABEL_MAX
        )
        body = "\n".join(lines[start - 1 : end])
        out.append(
            f"  L{start}-{end} § {label}\n           | "
            + body.replace("\n", "\n           | ")
        )
    return out


def _render(base: Base, doc: DocHits) -> str:
    text = read_text(doc.path)
    parsed = parse_document(text)
    rel = base.rel_path(doc.path)
    title = parsed.title or "(sans titre)"
    parts = [f"### {rel} — {title}"]
    digest = frontmatter_digest(parsed.frontmatter)
    if digest:
        parts.append(f"frontmatter : {digest}")
    parts.extend(_excerpts(text, doc.lines))
    return "\n".join(parts)


def run(registry: Registry, arguments: dict) -> str:
    base = registry.get(require_str(arguments, "base"))
    query = require_str(arguments, "query")
    mode = arguments.get("mode") or "keyword"
    if mode not in ("keyword", "regex"):
        from ..errors import INVALID_INPUT, ToolError

        raise ToolError(INVALID_INPUT, f"mode inconnu : '{mode}' (attendu keyword ou regex)")
    max_results = optional_int(
        arguments, "max_results", DEFAULT_MAX_RESULTS, 1, HARD_MAX_RESULTS
    )

    outcome = run_search(base, query, mode, max_results)
    if not outcome.docs:
        return f"Aucun résultat dans la base '{base.name}' pour : {query}"

    writer = BudgetedWriter()
    header = f"{len(outcome.docs)} résultat(s) dans '{base.name}' pour : {query}"
    if outcome.partial:
        header += "\n[aucun document ne contient tous les termes — résultats partiels]"
    if outcome.reserved_count:
        header += (
            f"\n[{outcome.reserved_count} sommaire(s) index.md/log.md en fin de liste — "
            f"déclassés car ce sont des tables de matières, pas de la connaissance]"
        )
    writer.add_forced(header)

    for doc in outcome.docs:
        try:
            writer.add(_render(base, doc))
        except OSError:
            continue

    return writer.render()
