"""Rendu d'un document en HTML, côté serveur (§ 5 bis.1, rév. 4.5).

Il n'existe qu'un rendu : une autre interface qui veut montrer un document
renvoie vers sa page, elle ne le rend pas elle-même (§ 5 bis.3).

**Le corps est une donnée non fiable** : il vient en partie de propositions
d'agents. HTML brut désactivé (il s'affiche échappé), schémas de lien filtrés
par `liens.classer` — le même classement que l'index des liens —, images non
chargées. La politique de contenu de l'interface (`default-src 'self'`) est la
seconde barrière.

**Imbrication.** markdown-it abandonne *en silence* tout ce qui suit une
imbrication plus profonde que `maxNesting` (20 par défaut). Constaté le
2026-10-29 sur un corpus réel : une fiche aux listes imbriquées sur 12 niveaux
aurait été affichée à 14 Ko sur 154 Ko. La limite est relevée, et une
troncature restante est détectée et signalée (`Rendu.tronque`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping

from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdown_it.common.utils import escapeHtml
from markupsafe import Markup
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin

from ..mdutil import normalize_heading
from .liens import EXTERNE, Cible, classer

#: Garde contre un document pathologique, pas contre les corpus réels.
MAX_IMBRICATION = 200

_NON_SLUG = re.compile(r"[^\w-]+")


def ancre(texte: str) -> str:
    """Identifiant d'ancre d'un heading : sa forme normalisée (§ 11.4), en tirets.

    La même normalisation que la correspondance de `section` : l'extrait d'une
    recherche annoté « § titre » mène à `#ancre(titre)`.
    """
    return _NON_SLUG.sub("-", normalize_heading(texte)).strip("-") or "section"


def ancre_de_source(identifiant: str) -> str:
    return "source-" + (_NON_SLUG.sub("-", identifiant.casefold()).strip("-") or "x")


@dataclass(frozen=True)
class Lien:
    """Ce que l'interface fait d'une cible : où elle mène, et comment l'afficher."""

    href: str | None
    """None : le lien n'est pas cliquable (cassé, refusé, base non visible)."""
    classe: str
    titre: str | None = None


@dataclass(frozen=True)
class Contexte:
    base: str
    chemin: str
    """Document rendu, relatif au corpus : les liens relatifs partent de son dossier."""
    lien_vers: Callable[[Cible], Lien]
    sources: frozenset[str] = frozenset()
    """`sources[].id` du frontmatter : une note `[^id]` sans définition y renvoie."""
    image_vers: Callable[[Cible], str | None] | None = None
    """Adresse d'une image du corpus ; None : l'image n'est pas affichée."""
    proposition_vers: Callable[[str], str | None] | None = None
    """Adresse de la page d'une proposition d'après son id ; un id écrit dans le
    texte devient alors un lien."""


@dataclass(frozen=True)
class Rendu:
    html: Markup
    sommaire: tuple[tuple[int, str, str], ...]
    """(niveau, texte, ancre) des headings de niveau 1 à 3."""
    tronque: bool


def _regle_source(state, silent: bool) -> bool:
    """`[^id]` sans définition dans le corps, dont l'id est celui d'une source.

    C'est l'attribution d'OKF § 5.1 : la note renvoie à une entrée de `sources`
    du frontmatter. Une note définie dans le corps est rendue par le greffon des
    notes, qui passe avant.
    """
    debut = state.pos
    if state.src.startswith("[^", debut) is False:
        return False
    fin = state.src.find("]", debut + 2)
    if fin < 0:
        return False
    etiquette = state.src[debut + 2 : fin]
    contexte = state.env.get("okf")
    if not etiquette or any(c.isspace() for c in etiquette) or contexte is None:
        return False
    if etiquette not in contexte.sources:
        return False
    if not silent:
        jeton = state.push("source_ref", "", 0)
        jeton.meta = {"id": etiquette}
    state.pos = fin + 1
    return True


def _rendre_source(self, tokens, idx, options, env) -> str:
    etiquette = tokens[idx].meta["id"]
    return (
        f'<sup class="source"><a href="#{escapeHtml(ancre_de_source(etiquette))}">'
        f"[{escapeHtml(etiquette)}]</a></sup>"
    )


def _rendre_lien(self, tokens, idx, options, env) -> str:
    jeton = tokens[idx]
    if jeton.meta.get("moteur"):
        # Lien posé par le moteur lui-même (id de proposition) : déjà résolu.
        return self.renderToken(tokens, idx, options, env)
    href = jeton.attrGet("href") or ""
    contexte: Contexte = env["okf"]
    cible = classer(str(href), contexte.base, contexte.chemin)
    lien = contexte.lien_vers(cible)
    attrs: dict[str, str] = {}
    if lien.href is not None:
        attrs["href"] = str(href) if cible.sorte == EXTERNE else lien.href
    if cible.sorte == EXTERNE:
        attrs["rel"] = "noopener noreferrer nofollow"
    attrs["class"] = lien.classe
    if lien.titre:
        attrs["title"] = lien.titre
    jeton.attrs = attrs
    return self.renderToken(tokens, idx, options, env)


def _rendre_image(self, tokens, idx, options, env) -> str:
    """Une image du corpus est affichée si l'interface la sert (`image_vers`) ;
    toute autre — externe, absente, d'un type non servi — devient un repère qui
    dit ce qu'elle est et où elle vit. Rien n'est perdu, rien n'est chargé
    d'ailleurs (la politique de contenu l'interdirait de toute façon)."""
    jeton = tokens[idx]
    texte = self.renderInlineAsText(jeton.children or [], options, env)
    cible = jeton.attrGet("src") or ""
    contexte: Contexte = env["okf"]
    if contexte.image_vers is not None:
        url = contexte.image_vers(classer(str(cible), contexte.base, contexte.chemin))
        if url is not None:
            titre = jeton.attrGet("title")
            return (
                f'<img src="{escapeHtml(url)}" alt="{escapeHtml(texte)}" loading="lazy"'
                + (f' title="{escapeHtml(str(titre))}"' if titre else "") + ">"
            )
    return (
        f'<span class="image" title="{escapeHtml(str(cible))}">'
        f"[image : {escapeHtml(texte) or 'sans texte'}]</span>"
    )


#: Un id de proposition (§ 5.5), et un chemin de fiche écrit en code.
_ID_PROPOSITION = re.compile(r"\bprop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4}\b")
_SECTION_EN_CODE = re.compile(r"^#{1,6}\s+(\S.*)$")
_CHEMIN_EN_CODE = re.compile(r"^(?:[a-z0-9-]+:/[^\s`]+|/?[\w.-]+(?:/[\w.-]+)*\.md|/[\w.-]+(?:/[\w.-]+)*/)$")


def _rendre_code(self, tokens, idx, options, env) -> str:
    """Un chemin de fiche écrit en code (`dossier/fiche.md`), s'il existe,
    devient un lien : c'est ainsi que les propositions et bien des fiches
    désignent un document."""
    jeton = tokens[idx]
    code = f"<code>{escapeHtml(jeton.content)}</code>"
    contexte: Contexte | None = env.get("okf")
    ouverts = sum(1 if t.type == "link_open" else -1 if t.type == "link_close" else 0 for t in tokens[:idx])
    if contexte is None or ouverts > 0:
        return code  # déjà dans un lien : pas de lien dans un lien
    # « `# Terrain` » : un renvoi à une section de la fiche, écrit comme son titre —
    # sauf si le paragraphe cite un autre document, dont ce peut être la section.
    section = _SECTION_EN_CODE.match(jeton.content)
    ailleurs = any(t.type == "link_open" and not str(t.attrGet("href") or "").startswith("#") for t in tokens)
    if section and not ailleurs and ancre(section.group(1)) in env.get("ancres", ()):
        return f'<a class="ancre" href="#{escapeHtml(ancre(section.group(1)))}">{code}</a>'
    if not _CHEMIN_EN_CODE.match(jeton.content):
        return code
    brut = jeton.content if (jeton.content.startswith("/") or ":/" in jeton.content) else "/" + jeton.content
    cible = classer(brut, contexte.base, contexte.chemin)
    if cible.sorte not in ("interne", "interbase"):
        return code
    lien = contexte.lien_vers(cible)
    if lien.href is None:
        return code
    return f'<a class="{escapeHtml(lien.classe)}" href="{escapeHtml(lien.href)}">{code}</a>'


def _liens_automatiques(state) -> None:
    """Règle de cœur : un id de proposition écrit dans le texte (hors lien et hors
    code) devient un lien vers sa page, si l'interface la connaît."""
    contexte: Contexte | None = state.env.get("okf")
    if contexte is None or contexte.proposition_vers is None:
        return
    for bloc in state.tokens:
        if bloc.type != "inline" or not bloc.children:
            continue
        enfants, dans_un_lien = [], 0
        for jeton in bloc.children:
            if jeton.type == "link_open":
                dans_un_lien += 1
            elif jeton.type == "link_close":
                dans_un_lien -= 1
            if jeton.type != "text" or dans_un_lien or "prop-" not in jeton.content:
                enfants.append(jeton)
                continue
            position = 0
            for m in _ID_PROPOSITION.finditer(jeton.content):
                url = contexte.proposition_vers(m.group(0))
                if url is None:
                    continue
                if m.start() > position:
                    enfants.append(_texte(jeton.content[position:m.start()]))
                ouvre = Token("link_open", "a", 1)
                ouvre.attrs = {"href": url, "class": "proposition"}
                ouvre.meta = {"moteur": True}
                enfants += [ouvre, _texte(m.group(0)), Token("link_close", "a", -1)]
                position = m.end()
            if position == 0:
                enfants.append(jeton)
            elif position < len(jeton.content):
                enfants.append(_texte(jeton.content[position:]))
        bloc.children = enfants


def _marquer_le_chapo(jetons: list[Token]) -> None:
    """Un premier paragraphe tout entier en italique est un chapô : la page le
    met en avant (classe `chapo`) au lieu d'en faire un long bloc penché."""
    if len(jetons) < 3 or jetons[0].type != "paragraph_open" or jetons[1].type != "inline":
        return
    enfants = [e for e in (jetons[1].children or ()) if not (e.type == "text" and not e.content.strip())]
    if len(enfants) >= 3 and enfants[0].type == "em_open" and enfants[-1].type == "em_close" and \
            sum(1 for e in enfants if e.type == "em_open") == 1:
        jetons[0].attrSet("class", "chapo")


def _rendre_fin_de_titre(self, tokens, idx, options, env) -> str:
    """Un permalien après chaque titre : `#` à côté du texte, pour copier
    l'adresse d'une section sans passer par le sommaire."""
    ouverture = next((tokens[j] for j in range(idx - 1, -1, -1) if tokens[j].type == "heading_open"), None)
    ident = ouverture.attrGet("id") if ouverture is not None else None
    lien = (f' <a class="permalien" href="#{escapeHtml(str(ident))}" aria-label="Lien vers cette section">#</a>'
            if ident else "")
    return f"{lien}</{tokens[idx].tag}>\n"


def _texte_du_titre(jeton: Token) -> str:
    """Le texte d'un titre tel qu'il se lit, sans sa syntaxe (`code`, *emphase*,
    liens) : celui du sommaire."""
    morceaux = [c.content for c in (jeton.children or ()) if c.type in ("text", "code_inline")]
    return "".join(morceaux).strip() or jeton.content


def _texte(contenu: str) -> Token:
    jeton = Token("text", "", 0)
    jeton.content = contenu
    return jeton


def _construire() -> MarkdownIt:
    md = (
        MarkdownIt("commonmark", {"html": False, "maxNesting": MAX_IMBRICATION})
        .enable(["table", "strikethrough"])
        .use(footnote_plugin)
        .use(anchors_plugin, min_level=1, max_level=6, slug_func=ancre)
    )
    md.inline.ruler.after("footnote_ref", "source_ref", _regle_source)
    md.add_render_rule("link_open", _rendre_lien)
    md.add_render_rule("image", _rendre_image)
    md.add_render_rule("source_ref", _rendre_source)
    md.add_render_rule("code_inline", _rendre_code)
    md.add_render_rule("heading_close", _rendre_fin_de_titre)
    md.core.ruler.push("liens_automatiques", _liens_automatiques)
    # Compile les chaînes de règles maintenant, une fois, plutôt qu'au premier
    # rendu de plusieurs fils à la fois.
    md.parse("", {"okf": None})
    return md


_MD = _construire()


def _niveau_atteint(jetons) -> int:
    """Imbrication la plus profonde atteinte, blocs et contenu en ligne compris.

    La carte des jetons ne dit pas si markdown-it a abandonné la suite : à la
    limite, il étend celle du bloc englobant jusqu'à la fin du document. Le
    niveau, lui, plafonne à `MAX_IMBRICATION - 1` exactement quand la limite
    est touchée (vérifié le 2026-10-29) — au pire un faux positif à la limite
    même, jamais un faux négatif.
    """
    niveau = 0
    for jeton in jetons:
        niveau = max(niveau, jeton.level)
        for enfant in jeton.children or ():
            niveau = max(niveau, jeton.level + enfant.level)
    return niveau


def _balise(html: str) -> Token:
    """Un jeton de HTML produit par le moteur, jamais par le document : le rendu
    de `html_block` le recopie tel quel, que le HTML brut soit permis ou non."""
    return Token("html_block", "", 0, content=html)


def _encadrer(jetons: list[Token], roles: Mapping[str, str]) -> list[Token]:
    """Entoure chaque section dont le titre tient un rôle (§ 3.5) d'un
    `<section class="role role-<rôle>">`, jusqu'au heading suivant de niveau
    inférieur ou égal."""
    out: list[Token] = []
    ouverts: list[int] = []

    def fermer(jusqu_a: int) -> None:
        while ouverts and ouverts[-1] >= jusqu_a:
            ouverts.pop()
            out.append(_balise("</section>\n"))

    for i, jeton in enumerate(jetons):
        if jeton.type == "heading_open":
            niveau = int(jeton.tag[1])
            fermer(niveau)
            role = roles.get(normalize_heading(jetons[i + 1].content)) if i + 1 < len(jetons) else None
            if role:
                out.append(_balise(f'<section class="role role-{escapeHtml(role)}">\n'))
                ouverts.append(niveau)
        elif jeton.type == "footnote_block_open":
            fermer(0)
        out.append(jeton)
    fermer(0)
    return out


#: Au-delà, la liste d'une section `upstream` se replie : vingt fiches amont en
#: tête de page cachent ce que la fiche dit elle-même.
REPLI_AMONT = 10


def _replier(jetons: list[Token], roles: Mapping[str, str]) -> list[Token]:
    """Replie dans un `<details>` toute liste de premier niveau de plus de
    `REPLI_AMONT` éléments, dans une section de rôle `upstream`."""
    out: list[Token] = []
    section: int | None = None  # niveau du titre de la section amont en cours
    i = 0
    while i < len(jetons):
        jeton = jetons[i]
        if jeton.type == "heading_open":
            niveau = int(jeton.tag[1])
            if section is not None and niveau <= section:
                section = None
            if i + 1 < len(jetons) and roles.get(normalize_heading(jetons[i + 1].content)) == "upstream":
                section = niveau
        elif section is not None and jeton.type in ("bullet_list_open", "ordered_list_open") and jeton.level == 0:
            fin = next(j for j in range(i + 1, len(jetons))
                       if jetons[j].type == jeton.type.replace("_open", "_close") and jetons[j].level == 0)
            elements = sum(1 for t in jetons[i:fin] if t.type == "list_item_open" and t.level == 1)
            if elements > REPLI_AMONT:
                out.append(_balise(f'<details class="repli"><summary>{elements} éléments</summary>\n'))
                out.extend(jetons[i:fin + 1])
                out.append(_balise("</details>\n"))
                i = fin + 1
                continue
        out.append(jeton)
        i += 1
    return out


def rendre(
    corps: str,
    contexte: Contexte,
    *,
    titre: str | None = None,
    roles: Mapping[str, str] | None = None,
) -> Rendu:
    """Rend le corps d'un document (sans son frontmatter).

    `titre` : celui de l'en-tête de page. Un premier heading de niveau 1 qui le
    répète n'est pas rendu une seconde fois. `roles` : titre de section
    normalisé → rôle, tiré du profil de la base ; ces sections sont encadrées.
    """
    env = {"okf": contexte}
    jetons = _MD.parse(corps, env)
    if (
        titre
        and len(jetons) >= 3
        and jetons[0].type == "heading_open"
        and jetons[0].tag == "h1"
        and normalize_heading(jetons[1].content) == normalize_heading(titre)
    ):
        jetons = jetons[3:]
    env["ancres"] = frozenset(str(j.attrGet("id")) for j in jetons if j.type == "heading_open" and j.attrGet("id"))
    _marquer_le_chapo(jetons)
    if roles:
        jetons = _encadrer(_replier(jetons, roles), roles)
    sommaire = []
    for i, j in enumerate(jetons):
        if j.type == "heading_open" and j.tag in ("h1", "h2", "h3") and i + 1 < len(jetons):
            sommaire.append((int(j.tag[1]), _texte_du_titre(jetons[i + 1]), str(j.attrGet("id") or "")))
    html = _MD.renderer.render(jetons, _MD.options, env)
    return Rendu(Markup(html), tuple(sommaire), _niveau_atteint(jetons) >= MAX_IMBRICATION - 1)
