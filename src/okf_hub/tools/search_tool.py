"""kb_search (§ 5.2)."""

from __future__ import annotations

from ..errors import INVALID_INPUT, ToolError
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

#: Valeur spéciale de `base` désignant toutes les bases enregistrées (§ 10.3).
ALL_BASES = "*"

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {
            "description": (
                "Nom de la base (champ `name` du manifeste), une liste de noms "
                'pour interroger plusieurs bases, ou "*" pour toutes les bases '
                "enregistrées."
            ),
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}, "minItems": 1},
            ],
        },
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
        "Recherche plein texte dans le corpus d'une ou plusieurs bases (`base` "
        'accepte un nom, une liste de noms, ou "*" pour toutes les bases — '
        "résultats groupés par base, sous un plafond de sortie global réparti "
        "entre elles). Retourne les chemins, titres et extraits pertinents — "
        "jamais les documents entiers. Chaque extrait est annoté du heading de "
        "sa section, après « § » : reportez-le tel quel dans "
        "kb_read(path, section) pour lire la section entière sans rapatrier tout "
        "le document."
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


def _resolve_base_names(registry: Registry, arguments: dict) -> list[str]:
    """Normalise `base` : nom unique, liste de noms, ou `"*"` (§ 10.3).

    Valide l'existence de chaque nom explicite *avant* toute recherche — un nom
    inconnu dans une liste échoue tout l'appel, comme le ferait déjà un nom
    unique inconnu ; pas de repli silencieux qui ignorerait une faute de frappe.
    """
    value = arguments.get("base")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ToolError(INVALID_INPUT, "paramètre 'base' requis (nom, liste, ou \"*\")")
        names = registry.names() if value == ALL_BASES else [value]
    elif isinstance(value, list):
        if not value or not all(isinstance(v, str) and v.strip() for v in value):
            raise ToolError(
                INVALID_INPUT, "paramètre 'base' : liste de noms non vides attendue"
            )
        seen: dict[str, None] = {}
        for v in value:
            seen.setdefault(v.strip(), None)
        names = list(seen)
    else:
        raise ToolError(INVALID_INPUT, "paramètre 'base' requis (nom, liste, ou \"*\")")

    for name in names:
        registry.get(name)  # lève UNKNOWN_BASE si absent — validé avant toute recherche.
    return names


def _allocate_quota(available: list[int], total: int) -> list[int]:
    """Répartit `total` entre positions selon leur disponibilité (§ 10.3) :
    parts égales entre bases encore actives, sans jamais allouer à une base plus
    qu'elle n'a de résultats — le reliquat profite aux autres bases plutôt que
    d'être perdu. C'est un plafond global unique, pas un plafond par base.
    """
    alloc = [0] * len(available)
    active = [i for i, n in enumerate(available) if n > 0]
    remaining = total
    while remaining > 0 and active:
        share, extra = divmod(remaining, len(active))
        still_active = []
        for idx, pos in enumerate(active):
            want = share + (1 if idx < extra else 0)
            take = min(want, available[pos] - alloc[pos])
            alloc[pos] += take
            remaining -= take
            if alloc[pos] < available[pos]:
                still_active.append(pos)
        active = still_active
    return alloc


def run(registry: Registry, arguments: dict) -> str:
    base_names = _resolve_base_names(registry, arguments)
    query = require_str(arguments, "query")
    mode = arguments.get("mode") or "keyword"
    if mode not in ("keyword", "regex"):
        raise ToolError(INVALID_INPUT, f"mode inconnu : '{mode}' (attendu keyword ou regex)")
    max_results = optional_int(
        arguments, "max_results", DEFAULT_MAX_RESULTS, 1, HARD_MAX_RESULTS
    )

    if not base_names:
        return "Aucune base n'est actuellement enregistrée."

    outcomes = {
        name: run_search(registry.get(name), query, mode, max_results) for name in base_names
    }
    alloc = _allocate_quota([len(outcomes[n].docs) for n in base_names], max_results)

    if not any(alloc):
        if len(base_names) == 1:
            return f"Aucun résultat dans la base '{base_names[0]}' pour : {query}"
        bases_txt = ", ".join(f"'{n}'" for n in base_names)
        return f"Aucun résultat dans les bases {bases_txt} pour : {query}"

    writer = BudgetedWriter()
    grouped = len(base_names) > 1
    if grouped:
        writer.add_forced(f"{sum(alloc)} résultat(s) dans {len(base_names)} base(s) pour : {query}")

    for name, quota in zip(base_names, alloc):
        if quota == 0:
            continue
        base = registry.get(name)
        docs = outcomes[name].docs[:quota]
        header = f"{len(docs)} résultat(s) dans '{base.name}' pour : {query}"
        if outcomes[name].partial:
            header += "\n[aucun document ne contient tous les termes — résultats partiels]"
        reserved_shown = sum(1 for d in docs if d.reserved)
        if reserved_shown:
            header += (
                f"\n[{reserved_shown} sommaire(s) index.md/log.md en fin de liste — "
                f"déclassés car ce sont des tables de matières, pas de la connaissance]"
            )
        if grouped:
            writer.add_forced(f"\n## Base : {base.name}")
        writer.add_forced(header)

        for doc in docs:
            try:
                writer.add(_render(base, doc))
            except OSError:
                continue

    return writer.render()
