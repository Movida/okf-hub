"""Liens d'un document : extraction, classement, résolution (§ 3.6, rév. 4.5).

Trois étapes, séparées parce qu'elles n'ont pas le même rythme :

* l'**extraction** lit le corps d'un document, au chargement ;
* le **classement** ne dépend que du texte de la cible et du document qui la
  porte : il est fait une fois, au chargement, et ne connaît aucune autre base ;
* la **résolution** confronte la cible aux bases présentes, au moment où on la
  demande — une base installée après le chargement d'une autre résout donc ses
  références sans rien recharger.

Formes reconnues, celles d'OKF § 6.1 plus la référence entre bases :

| Cible | Sorte |
|---|---|
| `/dossier/fiche.md` | `interne`, depuis la racine du corpus (bundle-relatif, OKF) |
| `fiche.md`, `../autre/fiche.md` | `interne`, depuis le dossier du document |
| `<name>:/dossier/fiche.md` | `interbase`, depuis la racine du corpus de la base `name` |
| `http:`, `https:`, `mailto:` | `externe` |
| `#ancre` | `ancre`, dans le même document |
| tout autre schéma, `//hôte/…` | `refuse` : jamais rendu cliquable |

Un chemin terminé par `/` désigne un répertoire, donc son `index.md`. Un
chemin sans `.md` est un identifiant OKF (le chemin sans extension).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import unquote

from ..mdutil import Heading, heading_at_line, lines_outside_fences

INTERNE = "interne"
INTERBASE = "interbase"
EXTERNE = "externe"
ANCRE = "ancre"
REFUSE = "refuse"
HORS_CORPUS = "hors-corpus"

#: Le préfixe d'une référence entre bases reprend le motif du `name` de
#: manifeste (§ 3.3) — le même que `read_tool._INTERBASE_LINK`. Un second `/`
#: (`ftp://hôte`) en fait une URL à autorité, pas une référence.
_INTERBASE = re.compile(r"^([a-z0-9-]+):/(?!/)(.*)$", re.DOTALL)
_EXTERNE = re.compile(r"^(?:https?|mailto):", re.IGNORECASE)
_SCHEMA = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class Cible:
    """Une cible de lien, classée sans rien savoir des autres bases."""

    sorte: str
    brut: str
    base: str | None = None
    """Base visée : celle du document pour `interne`, le préfixe pour `interbase`."""
    chemin: str | None = None
    """Chemin normalisé, relatif au corpus visé ; '' pour la racine."""
    repertoire: bool = False
    """La cible se termine par `/` : un répertoire, donc son `index.md`."""
    ancre: str | None = None


@dataclass(frozen=True)
class Lien:
    cible: Cible
    ligne: int
    """Index (base 0) de la ligne dans le corps du document."""
    section: str | None
    """Heading normalisé de la section qui contient le lien ; None : préambule."""


def classer(brut: str, base: str, chemin_doc: str) -> Cible:
    """Classe la cible `brut` d'un lien porté par `chemin_doc` dans `base`."""
    cible = brut.strip()
    if cible.startswith("<") and cible.endswith(">"):
        cible = cible[1:-1].strip()
    if not cible:
        return Cible(REFUSE, brut)
    if cible.startswith("#"):
        return Cible(ANCRE, brut, ancre=unquote(cible[1:]) or None)
    if _EXTERNE.match(cible):
        return Cible(EXTERNE, brut)

    chemin, _, ancre = cible.partition("#")
    ancre_ou_rien = unquote(ancre) or None

    m = _INTERBASE.match(chemin)
    if m is not None:
        normal = _normaliser(m.group(2))
        if normal is None:
            return Cible(HORS_CORPUS, brut, base=m.group(1))
        return Cible(INTERBASE, brut, m.group(1), normal[0], normal[1], ancre_ou_rien)
    if _SCHEMA.match(chemin) or chemin.startswith("//"):
        return Cible(REFUSE, brut)

    if chemin.startswith("/"):
        normal = _normaliser(chemin)
    else:
        dossier = posixpath.dirname(chemin_doc)
        normal = _normaliser(posixpath.join(dossier, chemin) if dossier else chemin)
    if normal is None:
        return Cible(HORS_CORPUS, brut, base=base)
    return Cible(INTERNE, brut, base, normal[0], normal[1], ancre_ou_rien)


def _normaliser(chemin: str) -> tuple[str, bool] | None:
    """(chemin relatif au corpus, répertoire ?), ou None s'il en sort.

    `posixpath.normpath` sur un chemin *relatif* : sur un chemin absolu, il
    ramènerait `/../x` à `/x` sans rien dire, et une évasion passerait pour une
    cible du corpus.
    """
    texte = unquote(chemin)
    repertoire = texte.endswith("/") or texte.strip("/") == ""
    relatif = texte.lstrip("/")
    if not relatif:
        return "", True
    normal = posixpath.normpath(relatif)
    if normal == ".":
        return "", True
    if normal == ".." or normal.startswith("../"):
        return None
    return normal, repertoire


# --- extraction ---------------------------------------------------------------

#: Lien en ligne `[libellé](cible "titre")`, hors image. Le libellé admet un
#: niveau de crochets imbriqués ; la cible, une paire de parenthèses (CommonMark).
_LIEN_EN_LIGNE = re.compile(
    r"(?<!!)\[(?:[^\[\]\\]|\\.|\[[^\[\]]*\])*\]"
    r"\(\s*(?P<cible><[^>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)"
)
#: Définition de lien par référence `[nom]: cible` — pas une note `[^id]:`.
_DEFINITION = re.compile(r"^ {0,3}\[(?!\^)[^\]\n]+\]:[ \t]*(?P<cible><[^>\n]*>|\S+)", re.M)
#: Lien automatique `<https://…>` : CommonMark n'en reconnaît pas d'autre forme.
_AUTOLIEN = re.compile(r"<(?P<cible>(?:https?://|mailto:)[^\s<>]+)>", re.I)
_AUTOLIEN_COURRIEL = re.compile(r"<(?P<cible>[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+)>")
_NOTE = re.compile(r"\[\^(?P<id>[^\]\s]+)\]")
_DEFINITION_NOTE = re.compile(r"^ {0,3}(?P<etiquette>\[\^(?P<id>[^\]\s]+)\]):", re.M)
#: Un span de code peut couvrir plusieurs lignes d'un même paragraphe.
_CODE_EN_LIGNE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)


def _blanc(m: re.Match) -> str:
    span = m.group(0)
    if "\n" not in span:
        return " " * len(span)
    return "\n".join(" " * len(morceau) for morceau in span.split("\n"))


def _sans_code(texte: str) -> str:
    """Le texte, ses spans de code remplacés par des espaces (positions et
    retours à la ligne gardés)."""
    if "`" not in texte:
        return texte
    return _CODE_EN_LIGNE.sub(_blanc, texte)


def _paragraphes(corps: str):
    """(index de la première ligne, texte) de chaque suite de lignes non vides,
    hors blocs de code clôturés.

    Le paragraphe, et non la ligne, est l'unité d'extraction : un libellé de
    lien peut passer à la ligne, un span de code aussi. Mesuré sur un corpus
    réel : à la ligne, trois liens passaient inaperçus.
    """
    bloc: list[str] = []
    debut = precedent = -2
    for idx, ligne in lines_outside_fences(corps):
        if not ligne.strip() or idx != precedent + 1:
            if bloc:
                yield debut, "\n".join(bloc)
            bloc = []
        precedent = idx
        if ligne.strip():
            if not bloc:
                debut = idx
            bloc.append(ligne)
    if bloc:
        yield debut, "\n".join(bloc)


@dataclass(frozen=True)
class Extraction:
    liens: tuple[Lien, ...]
    notes: frozenset[str]
    """Étiquettes `[^id]` appelées dans le corps."""
    notes_definies: frozenset[str]
    """Étiquettes définies dans le corps par `[^id]: …`."""


def extraire(corps: str, headings: list[Heading], base: str, chemin_doc: str) -> Extraction:
    """Liens et notes du corps, hors blocs et spans de code.

    Un exemple de syntaxe écrit en code n'est pas un lien : c'est la convention
    que les bases suivent pour documenter leurs propres formats.

    Une expression régulière, et non le parseur du rendu : 1,8 s pour tout un
    hub de 3 733 documents, contre 17,4 s (mesure du 2026-10-29). Les deux
    trouvent exactement les mêmes 32 356 liens sur ce hub, une fois levée la
    limite d'imbrication du parseur (`rendu.py`) ; restent des cas limites (un
    lien dans un bloc de code indenté est compté ici) ; le classement des cibles, lui, est le même
    (`classer`), pour l'index comme pour la page.
    """
    liens: list[Lien] = []
    notes: set[str] = set()
    definies: set[str] = set()
    for debut, bloc in _paragraphes(corps):
        texte = _sans_code(bloc)
        if "[" not in texte and "<" not in texte:
            continue
        etiquettes_de_definition = set()
        for m in _DEFINITION_NOTE.finditer(texte):
            definies.add(m.group("id"))
            # L'étiquette d'une définition `[^id]: …` n'est pas un appel de note.
            etiquettes_de_definition.add(m.start("etiquette"))
        trouves = [(m.start(), m.group("cible")) for m in _LIEN_EN_LIGNE.finditer(texte)]
        trouves += [(m.start(), m.group("cible")) for m in _DEFINITION.finditer(texte)]
        trouves += [(m.start(), m.group("cible")) for m in _AUTOLIEN.finditer(texte)]
        trouves += [(m.start(), f"mailto:{m.group('cible')}") for m in _AUTOLIEN_COURRIEL.finditer(texte)]
        for position, brut in sorted(trouves):
            ligne = debut + texte.count("\n", 0, position)
            heading = heading_at_line(headings, ligne)
            section = heading.normalized if heading else None
            liens.append(Lien(classer(brut, base, chemin_doc), ligne, section))
        for m in _NOTE.finditer(texte):
            if m.start() not in etiquettes_de_definition:
                notes.add(m.group("id"))
    return Extraction(tuple(liens), frozenset(notes), frozenset(definies))
