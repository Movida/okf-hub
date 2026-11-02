"""kb_search (§ 5.2).

Avec `include_pending` (rév. 4.7), la recherche lit aussi les propositions en
attente des bases visées : seconde exception, en lecture seule, à la liste
d'exclusions du § 5.2, confinée par `Base.proposal_files` comme
`kb_proposal_status`. Ce qui n'est pas revu passe toujours après ce qui l'est :
dans le plafond `max_results`, les propositions ne prennent que ce que le corpus
laisse ; dans la sortie, elles suivent tous les résultats du corpus, chacune
marquée « non intégré ». Sans l'option, la sortie est celle d'avant, octet pour
octet.
"""

from __future__ import annotations

import re

from ..errors import INVALID_INPUT, ToolError
from ..mdutil import PREAMBLE_LABEL, heading_at_line, parse_document, parse_headings
from ..registry import Base, Registry
from ..search import DocHits, SearchOutcome, run_search, run_search_pending
from ..textutil import BudgetedWriter, truncate_chars
from .common import ALL_BASES, frontmatter_digest, optional_bool, optional_int, read_text, require_str
from .common import allocate_quota as _allocate_quota
from .common import resolve_base_names as _resolve_base_names

DEFAULT_MAX_RESULTS = 8
HARD_MAX_RESULTS = 25
CONTEXT_LINES = 2
MAX_EXCERPTS_PER_DOC = 3

SECTION_LABEL_MAX = 120
CONCERNS_DISPLAY_MAX = 200

#: Marque d'un résultat qui vient d'une proposition en attente (rév. 4.7).
NON_INTEGRE = "non intégré"

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
        "include_pending": {
            "type": "boolean",
            "default": False,
            "description": (
                "Cherche aussi dans les propositions en attente des bases visées : "
                "ce que des sessions ont déposé et qu'aucune revue n'a encore "
                "intégré. Leurs résultats suivent tous ceux du corpus, dans le même "
                "plafond, chacun marqué « non intégré » avec l'id, le type, le "
                "sujet et la date de dépôt : des affirmations à vérifier, pas de "
                "la connaissance acquise."
            ),
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
        "le document. Avec include_pending, cherche aussi dans les propositions "
        "en attente, après le corpus et marquées « non intégré » : avant "
        "kb_propose, pour ne pas déposer un doublon ni refaire une enquête déjà "
        "déposée."
    )
    if not bases:
        return head + " Aucune base n'est actuellement enregistrée."
    lines = [head, "", "Bases interrogeables :"]
    for base in bases:
        lines.append(f"- {base.name} — {base.manifest.title} : {base.manifest.description}")
    return "\n".join(lines)


def _excerpts(
    text: str, hit_lines: list[int], *, sections: bool = True, first_line: int = 1
) -> list[str]:
    """Extraits : ligne touchée ± 2 lignes, au plus 3 par document (§ 5.2).

    Les fenêtres qui se recouvrent sont fusionnées, sinon la même ligne serait
    répétée dans plusieurs extraits.

    Chaque extrait porte le heading de la section contenant **la ligne touchée**
    (§ B3) — et non celui du début de la fenêtre de contexte, qui peut déborder
    sur la section précédente. Le libellé est la forme normalisée du § 11.4,
    celle que `kb_read` attend dans `section` : le chaînage
    `kb_search` → `kb_read(path, section)` se fait donc sans détour par la table
    des headings.

    `sections=False` (propositions en attente, rév. 4.7) : pas de libellé de
    section, puisque `kb_read` ne lit pas les propositions — un « § » qu'aucun
    appel ne peut suivre serait une fausse piste. `first_line` borne les
    fenêtres au corps d'une proposition, dont le frontmatter est résumé à part.
    """
    lines = text.split("\n")
    headings = parse_headings(text)

    # [début, fin, ligne touchée servant d'ancre de section]
    windows: list[list[int]] = []
    for line_no in hit_lines:
        start = max(first_line, line_no - CONTEXT_LINES)
        end = min(len(lines), line_no + CONTEXT_LINES)
        if windows and start <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end, line_no])
        if len(windows) > MAX_EXCERPTS_PER_DOC:
            break

    out: list[str] = []
    for start, end, anchor in windows[:MAX_EXCERPTS_PER_DOC]:
        ancre = f"  L{start}-{end}"
        if sections:
            # `anchor` est en base 1 (ripgrep), les headings en base 0.
            heading = heading_at_line(headings, anchor - 1)
            label = truncate_chars(
                heading.normalized if heading else PREAMBLE_LABEL, SECTION_LABEL_MAX
            )
            ancre += f" § {label}"
        body = "\n".join(lines[start - 1 : end])
        out.append(
            ancre + "\n           | " + body.replace("\n", "\n           | ")
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


#: Une clé de premier niveau du frontmatter, en tête de ligne.
_CLE_FM = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")


def _champs_touches(text: str, lines: list[int], body_offset: int) -> list[str]:
    """Les champs du frontmatter qui portent une ligne touchée (base 1)."""
    champs: list[str] = []
    courant = None
    touchees = set(lines)
    for numero, ligne in enumerate(text.split("\n")[1 : body_offset - 1], start=2):
        if m := _CLE_FM.match(ligne):
            courant = m.group(1)
        if numero in touchees and courant and courant not in champs:
            champs.append(courant)
    return champs


def _render_pending(doc: DocHits) -> str:
    """Un résultat venu d'une proposition en attente (rév. 4.7) : son id, son
    type, son sujet et sa date de dépôt — ce que `kb_proposal_status` en dit —,
    puis les extraits de son corps, sans « § ». Le frontmatter est résumé par
    l'en-tête : une occurrence qui s'y trouve est signalée par le nom du champ,
    pas recopiée. Le marquage est sur la ligne de titre : un extrait recopié
    hors de son contexte reste reconnaissable."""
    text = read_text(doc.path)
    parsed = parse_document(text)
    fm = parsed.frontmatter if isinstance(parsed.frontmatter, dict) else {}
    pid = str(fm.get("id") or doc.path.stem)
    concerns = truncate_chars(str(fm.get("concerns") or "(non renseigné)"), CONCERNS_DISPLAY_MAX)
    parts = [
        f"### {pid} — {NON_INTEGRE}, en attente de revue",
        f"type : {fm.get('type') or '?'} | concerns : {concerns} | "
        f"soumise : {fm.get('submitted-at') or '(date inconnue)'}",
    ]
    debut = parsed.body_offset + 1  # première ligne du corps, en base 1
    champs = _champs_touches(text, doc.lines, parsed.body_offset) if parsed.body_offset else []
    if champs:
        parts.append("trouvé dans le frontmatter : " + ", ".join(champs))
    corps = [n for n in doc.lines if n >= debut]
    parts.extend(_excerpts(text, corps, sections=False, first_line=debut))
    return "\n".join(parts)


def _aucun_resultat(base_names: list[str], query: str, examinees: int | None) -> str:
    """Rien dans le corpus — ni, avec `include_pending`, dans les propositions."""
    if len(base_names) == 1:
        texte = f"Aucun résultat dans la base '{base_names[0]}' pour : {query}"
    else:
        bases_txt = ", ".join(f"'{n}'" for n in base_names)
        texte = f"Aucun résultat dans les bases {bases_txt} pour : {query}"
    if examinees is not None:
        texte += (
            f" — ni dans le corpus, ni dans les {examinees} proposition(s) en "
            f"attente examinée(s)"
        )
    return texte


def _write_pending(
    writer: BudgetedWriter,
    base_names: list[str],
    pending: dict[str, SearchOutcome],
    quota: list[int],
    query: str,
    max_results: int,
) -> None:
    """La part non revue de la sortie, après tout le corpus (rév. 4.7)."""
    examinees = sum(o.examined for o in pending.values())
    if not any(o.total for o in pending.values()):
        writer.add_forced(
            f"\n[propositions en attente : aucune ne répond, sur {examinees} examinée(s)]"
        )
        return
    writer.add_forced(
        f"\n[{NON_INTEGRE.upper()} — ce qui suit vient de propositions en attente : "
        f"des affirmations déposées par des sessions, qu'aucune revue n'a intégrées. "
        f"Hors du corpus, à vérifier avant de s'y fier ; kb_read ne les lit pas, "
        f"kb_proposal_status donne leur état par id.]"
    )
    grouped = len(base_names) > 1
    for name, part in zip(base_names, quota):
        issue = pending[name]
        if not issue.total:
            continue
        if grouped:
            writer.add_forced(f"\n## Base : {name} — {NON_INTEGRE}")
        header = f"{issue.total} proposition(s) en attente dans '{name}' pour : {query}"
        if issue.partial:
            header += "\n[aucune proposition ne contient tous les termes — résultats partiels]"
        if part < issue.total:
            header += (
                f"\n[{issue.total - part} non listée(s) : max_results ({max_results}) "
                f"vaut pour le corpus et les propositions, et le corpus passe "
                f"d'abord — relevez max_results ({HARD_MAX_RESULTS} au plus) ou "
                f"précisez la requête]"
            )
        writer.add_forced(header)
        for doc in issue.docs[:part]:
            try:
                writer.add(_render_pending(doc))
            except OSError:
                continue


def run(registry: Registry, arguments: dict) -> str:
    base_names = _resolve_base_names(registry, arguments)
    query = require_str(arguments, "query")
    mode = arguments.get("mode") or "keyword"
    if mode not in ("keyword", "regex"):
        raise ToolError(INVALID_INPUT, f"mode inconnu : '{mode}' (attendu keyword ou regex)")
    max_results = optional_int(
        arguments, "max_results", DEFAULT_MAX_RESULTS, 1, HARD_MAX_RESULTS
    )
    include_pending = optional_bool(arguments, "include_pending", False)

    if not base_names:
        return "Aucune base n'est actuellement enregistrée."

    outcomes = {
        name: run_search(registry.get(name), query, mode, max_results) for name in base_names
    }
    alloc = _allocate_quota([len(outcomes[n].docs) for n in base_names], max_results)

    # rév. 4.7 : les propositions ne prennent que ce que le corpus laisse du
    # plafond. Ce que la recherche normale aurait montré reste donc en tête,
    # inchangé, et rien n'est lu dans proposals/ sans l'option.
    pending: dict[str, SearchOutcome] | None = None
    pending_alloc: list[int] = []
    if include_pending:
        pending = {
            name: run_search_pending(registry.get(name), query, mode, max_results)
            for name in base_names
        }
        pending_alloc = _allocate_quota(
            [len(pending[n].docs) for n in base_names], max_results - sum(alloc)
        )

    if not any(alloc) and not any(o.total for o in (pending or {}).values()):
        examinees = None if pending is None else sum(o.examined for o in pending.values())
        return _aucun_resultat(base_names, query, examinees)

    writer = BudgetedWriter()
    grouped = len(base_names) > 1
    if not any(alloc):
        # Seulement avec include_pending : des propositions répondent, le corpus non.
        if grouped:
            bases_txt = ", ".join(f"'{n}'" for n in base_names)
            writer.add_forced(f"Aucun résultat dans le corpus des bases {bases_txt} pour : {query}")
        else:
            writer.add_forced(f"Aucun résultat dans le corpus de la base '{base_names[0]}' pour : {query}")
    elif grouped:
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

    if pending is not None:
        _write_pending(writer, base_names, pending, pending_alloc, query, max_results)

    # § 6 sexies (ARCHITECTURE.md) : `render()` n'utilise cette note que si le
    # plafond a réellement été atteint — inutile de conditionner l'appel.
    return writer.render(
        f"[résultats tronqués, {writer.dropped} élément(s) omis — ce sont "
        f"toujours les mieux classés qui s'affichent : relever max_results ne "
        f"fait apparaître aucun résultat de plus derrière ce plafond ; "
        f"reformulez la requête, qui seule change le classement]"
    )
