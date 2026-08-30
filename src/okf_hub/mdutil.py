"""Lecture des documents markdown : frontmatter, headings, sections.

Couvre § 3.2 (frontmatter YAML optionnel délimité par `---` en première
ligne), § 5.3 (correspondance de section, table des headings) et la question
ouverte § 11.4 (périmètre du strip markdown inline, normalisé des deux côtés
par la même fonction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

FM_DELIM = "---"


@dataclass(frozen=True)
class Document:
    frontmatter: dict | None
    """Frontmatter parsé, ou None s'il est absent ou illisible."""
    frontmatter_raw: str | None
    """Bloc frontmatter brut, délimiteurs inclus."""
    body: str
    """Corps du document, après le frontmatter."""
    body_offset: int
    """Index (base 0) de la première ligne du corps dans le fichier complet."""
    text: str
    """Contenu intégral du fichier."""

    @property
    def title(self) -> str | None:
        """Titre : frontmatter `title`, sinon premier `#` du corps (§ 5.2)."""
        if isinstance(self.frontmatter, dict):
            value = self.frontmatter.get("title")
            if isinstance(value, str) and value.strip():
                return value.strip()
        for h in parse_headings(self.body):
            return h.text
        return None


def parse_document(text: str) -> Document:
    """Découpe frontmatter et corps.

    Le frontmatter n'est reconnu que délimité par `---` en *première* ligne
    (§ 3.2). Un frontmatter non parseable n'invalide pas le document : il est
    conservé en brut et `frontmatter` vaut None — un document du corpus reste
    lisible même mal formé (principe § 1.4).
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != FM_DELIM:
        return Document(None, None, text, 0, text)

    for i in range(1, len(lines)):
        if lines[i].strip() == FM_DELIM:
            raw_block = "\n".join(lines[: i + 1])
            inner = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            try:
                data = yaml.safe_load(inner)
            except yaml.YAMLError:
                data = None
            if not isinstance(data, dict):
                data = None
            return Document(data, raw_block, body, i + 1, text)

    # Délimiteur ouvrant sans fermeture : tout est corps.
    return Document(None, None, text, 0, text)


# --- Headings ---------------------------------------------------------------

_ATX = re.compile(r"^(?P<indent> {0,3})(?P<hashes>#{1,6})(?P<rest>\s+.*|\s*)$")
_FENCE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_TRAILING_HASHES = re.compile(r"\s+#+\s*$")


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    """Texte du heading, débarrassé des `#` mais avec son formatage inline."""
    normalized: str
    """Forme normalisée servant à la correspondance (§ 11.4)."""
    line: int
    """Index (base 0) de la ligne du heading dans le texte analysé."""


def parse_headings(text: str) -> list[Heading]:
    """Extrait les headings ATX, en ignorant ceux des blocs de code clôturés.

    Sans cette exclusion, un `# Schema` à l'intérieur d'un bloc ``` serait pris
    pour une section — cas fréquent dans un corpus technique.
    """
    headings: list[Heading] = []
    fence: str | None = None
    for idx, line in enumerate(text.split("\n")):
        m_fence = _FENCE.match(line)
        if m_fence:
            marker = m_fence.group("fence")
            if fence is None:
                fence = marker[0] * 3
                continue
            if marker[0] * 3 == fence and not m_fence.group("info").strip():
                fence = None
            continue
        if fence is not None:
            continue
        m = _ATX.match(line)
        if not m:
            continue
        rest = m.group("rest")
        # `#foo` n'est pas un heading ATX : il faut une espace (ou rien).
        if rest and not rest[0].isspace():
            continue
        raw = _TRAILING_HASHES.sub("", rest.strip()).strip()
        headings.append(
            Heading(
                level=len(m.group("hashes")),
                text=raw,
                normalized=normalize_heading(raw),
                line=idx,
            )
        )
    return headings


_IMAGE = re.compile(r"!\[(?P<alt>[^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[(?P<label>[^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_REF_LINK = re.compile(r"\[(?P<label>[^\]]*)\]\[[^\]]*\]")
_CODE_SPAN = re.compile(r"`+")
_EMPHASIS_STAR = re.compile(r"\*{1,3}|~~")
#: Un `_` entouré de caractères alphanumériques des deux côtés n'est pas de
#: l'emphase (CommonMark) : c'est un blanc souligné dans un identifiant. Sans
#: cette règle, un heading « `kb_read` » se normaliserait en « kbread » et ne
#: correspondrait plus à lui-même.
_EMPHASIS_UNDERSCORE = re.compile(r"(?<![^\W_])_{1,3}|_{1,3}(?![^\W_])")
_WS = re.compile(r"\s+")


def normalize_heading(text: str) -> str:
    """Normalise un heading pour la correspondance de section (§ 5.3, § 11.4).

    Périmètre retenu, appliqué identiquement au heading du document et au
    paramètre `section` (recommandation § 11.4) :

    * images `![alt](url)` → `alt`, liens `[texte](url)` et `[texte][ref]` → `texte` ;
    * backticks de code inline supprimés ;
    * marqueurs d'emphase `*`, `**`, `***`, `~~` supprimés, ainsi que `_`,
      `__`, `___` sauf en position intra-mot (`kb_read` reste `kb_read`) ;
    * espaces successifs réduits, trim, passage en minuscules (casse ignorée).
    """
    s = _IMAGE.sub(lambda m: m.group("alt"), text)
    # Deux passes : un lien peut contenir un libellé lui-même formaté.
    for _ in range(2):
        s = _LINK.sub(lambda m: m.group("label"), s)
    s = _REF_LINK.sub(lambda m: m.group("label"), s)
    s = _CODE_SPAN.sub("", s)
    s = _EMPHASIS_STAR.sub("", s)
    s = _EMPHASIS_UNDERSCORE.sub("", s)
    s = _WS.sub(" ", s)
    return s.strip().casefold()


@dataclass
class SectionMatch:
    heading: Heading
    content: str
    duplicates: int
    """Nombre d'autres headings portant le même titre normalisé."""


def extract_section(text: str, section: str) -> SectionMatch | None:
    """Extrait la section dont le heading correspond à `section`.

    Correspondance insensible à la casse après normalisation. Extraction du
    heading jusqu'au prochain heading de niveau inférieur ou égal (§ 5.3).
    En cas de doublons, la première occurrence est retournée.
    """
    target = normalize_heading(section)
    if not target:
        return None
    headings = parse_headings(text)
    matches = [h for h in headings if h.normalized == target]
    if not matches:
        return None
    first = matches[0]

    lines = text.split("\n")
    end = len(lines)
    for h in headings:
        if h.line > first.line and h.level <= first.level:
            end = h.line
            break
    content = "\n".join(lines[first.line : end]).rstrip()
    return SectionMatch(heading=first, content=content, duplicates=len(matches) - 1)


def headings_table(text: str, body_offset: int = 0) -> list[tuple[Heading, int]]:
    """Table des headings avec la taille approximative de chaque section.

    Format libre (question ouverte § 11.2) ; la contrainte est qu'elle permette
    un appel `section` immédiat, donc on retourne le texte *brut* du heading.
    """
    headings = parse_headings(text)
    lines = text.split("\n")
    out: list[tuple[Heading, int]] = []
    for i, h in enumerate(headings):
        end = len(lines)
        for nxt in headings[i + 1 :]:
            if nxt.level <= h.level:
                end = nxt.line
                break
        size = sum(len(line) + 1 for line in lines[h.line : end])
        out.append((h, size))
    return out
