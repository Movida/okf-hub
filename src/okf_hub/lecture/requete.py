"""Requêtes structurées sur le modèle : `kb_find` (§ 5.8) et vues déclarées (§ 3.5).

Une vue déclarée par un profil est une requête `kb_find` enregistrée : les deux
passent par `lire_criteres` et `filtrer`, et répondent donc la même chose.
Les filtres généralisent ceux des outils en ligne de commande que les bases
tenaient chacune pour elle-même : type, tags, champ et valeur, présence ou
absence d'une section (par rôle ou par titre), sous-chemin, liens entrants,
fraîcheur.

Les noms réservés (`index.md`, `log.md`) ne sont pas des concepts (OKF § 3.1) :
ils ne sont ni listés ni comptés.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping

from ..errors import INVALID_INPUT, ToolError
from ..mdutil import normalize_heading
from ..search import run_search
from .confiance import UTC, date_declaree, mois_avant
from .modele import BaseModele, Derive, Doc, Fraicheur, Lecteur
from .profil import Profil

SANS_TYPE = "(sans type)"
SANS_TAG = "(sans tag)"
ABSENT = "(absent)"

#: Plafond de `older_than_months` : cinquante ans, au-delà rien n'a de sens.
MOIS_MAX = 600

#: Paramètres de `kb_find` qui sont des critères — ceux qu'une vue peut porter.
CLES_CRITERES = (
    "query", "type", "tags", "fields", "section", "without_section", "under", "links_to",
    "links_to_depth", "upstream_changed", "older_than_months", "sort",
)

#: Ordres de la liste (rév. 4.6). Défaut : par chemin ; les plus anciens d'abord
#: sous `older_than_months` ; par pertinence sous `query`.
TRIS = ("path", "relevance", "oldest", "most_cited", "reaudit")
PROFONDEUR_MAX = 3


@dataclass(frozen=True)
class Criteres:
    type: str | None = None
    tags: tuple[str, ...] = ()
    fields: tuple[tuple[str, str | bool], ...] = ()
    section: str | None = None
    without_section: str | None = None
    under: str | None = None
    links_to: str | None = None
    """Référence brute `<base>:/<chemin>` ou chemin ; résolue par l'appelant."""
    older_than_months: int | None = None
    query: str | None = None
    """Termes que le texte du document contient tous (comme `kb_search` en
    mode keyword, sans son repli en OU : un filtre ne devine pas)."""
    query_regex: bool = False
    """Interface web seulement : `query` est une expression ripgrep."""
    links_to_depth: int = 1
    """Avec `links_to` : documents qui citent la cible jusqu'à ce degré."""
    upstream_changed: bool = False
    sort: str | None = None

    def decrire(self) -> str:
        parties = []
        if self.query:
            parties.append(f"texte « {self.query} »" + (" (regex)" if self.query_regex else ""))
        if self.type is not None:
            parties.append(f"type={self.type}")
        for tag in self.tags:
            parties.append(f"tag={tag}")
        for nom, valeur in self.fields:
            if valeur is True:
                parties.append(f"{nom} présent")
            elif valeur is False:
                parties.append(f"{nom} absent")
            else:
                parties.append(f"{nom}={valeur}")
        if self.section:
            parties.append(f"section={self.section}")
        if self.without_section:
            parties.append(f"sans section={self.without_section}")
        if self.under:
            parties.append(f"sous {self.under}")
        if self.links_to:
            degre = f", jusqu'au degré {self.links_to_depth}" if self.links_to_depth > 1 else ""
            parties.append(f"citant {self.links_to}{degre}")
        if self.upstream_changed:
            parties.append("amont modifié depuis")
        if self.older_than_months:
            parties.append(f"plus de {self.older_than_months} mois")
        if self.sort:
            parties.append(f"tri {self.sort}")
        return " ; ".join(parties) or "aucun critère (tous les documents)"

    @property
    def tri(self) -> str:
        if self.sort:
            return self.sort
        if self.older_than_months:
            return "oldest"
        return "relevance" if self.query else "path"


def lire_criteres(arguments: Mapping) -> Criteres:
    """Valide les critères ; `INVALID_INPUT` sur la première entrée fautive."""

    def chaine(cle: str) -> str | None:
        valeur = arguments.get(cle)
        if valeur is None:
            return None
        if not isinstance(valeur, str) or not valeur.strip():
            raise ToolError(INVALID_INPUT, f"paramètre '{cle}' : chaîne non vide attendue")
        return valeur.strip()

    tags = arguments.get("tags")
    if tags is None:
        tags = []
    elif isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise ToolError(INVALID_INPUT, "paramètre 'tags' : liste de chaînes non vides attendue")

    champs = arguments.get("fields")
    if champs is None:
        champs = {}
    if not isinstance(champs, Mapping):
        raise ToolError(INVALID_INPUT, "paramètre 'fields' : objet {champ: valeur} attendu")
    lus: list[tuple[str, str | bool]] = []
    for nom, valeur in champs.items():
        if not isinstance(nom, str) or not nom.strip():
            raise ToolError(INVALID_INPUT, "paramètre 'fields' : nom de champ vide")
        if isinstance(valeur, bool):
            lus.append((nom.strip(), valeur))
        elif isinstance(valeur, (str, int, float)) and str(valeur).strip():
            lus.append((nom.strip(), str(valeur).strip()))
        else:
            raise ToolError(
                INVALID_INPUT,
                f"paramètre 'fields' : la valeur de '{nom}' doit être une chaîne, "
                f"ou true (présent) / false (absent)",
            )

    mois = arguments.get("older_than_months")
    if mois is not None and (
        isinstance(mois, bool) or not isinstance(mois, int) or not 1 <= mois <= MOIS_MAX
    ):
        raise ToolError(
            INVALID_INPUT, f"paramètre 'older_than_months' : entier entre 1 et {MOIS_MAX} attendu"
        )

    profondeur = arguments.get("links_to_depth")
    if profondeur is None:
        profondeur = 1
    if isinstance(profondeur, bool) or not isinstance(profondeur, int) or not 1 <= profondeur <= PROFONDEUR_MAX:
        raise ToolError(INVALID_INPUT, f"paramètre 'links_to_depth' : entier entre 1 et {PROFONDEUR_MAX} attendu")
    amont = arguments.get("upstream_changed")
    if amont is not None and not isinstance(amont, bool):
        raise ToolError(INVALID_INPUT, "paramètre 'upstream_changed' : booléen attendu")
    tri = chaine("sort")
    if tri is not None and tri not in TRIS:
        raise ToolError(INVALID_INPUT, f"paramètre 'sort' : une valeur parmi {', '.join(TRIS)}")
    query = chaine("query")
    if tri == "relevance" and not query:
        raise ToolError(INVALID_INPUT, "paramètre 'sort' : « relevance » suppose `query`")

    return Criteres(
        query=query,
        query_regex=bool(arguments.get("query_regex")),
        links_to_depth=profondeur,
        upstream_changed=bool(amont),
        sort=tri,
        type=chaine("type"),
        tags=tuple(t.strip() for t in tags),
        fields=tuple(lus),
        section=chaine("section"),
        without_section=chaine("without_section"),
        under=chaine("under"),
        links_to=chaine("links_to"),
        older_than_months=mois,
    )


@dataclass(frozen=True)
class Trouve:
    doc: Doc
    fraicheur: Fraicheur | None = None
    """Calculée quand la requête porte sur l'ancienneté ou la trie par elle."""
    amont: bool = False
    """Avec `links_to` : le lien est dans une section de rôle `upstream` du
    document qui cite — une relation « décline », pas une simple mention."""
    derives: tuple[Derive, ...] = ()
    """Documents d'autres bases cités par celui-ci et modifiés après lui."""
    citations: int | None = None
    """Documents qui le citent ; calculé pour les tris qui en dépendent."""
    score: float | None = None
    """Tri `reaudit` : âge en mois × (1 + citations), doublé par une dérive."""
    degre: int | None = None
    """Avec `links_to` : 1 si le document cite la cible, 2 s'il cite un document
    qui la cite…"""
    occurrences: int | None = None
    """Avec `query` : occurrences des termes dans le document — comparables d'une
    base à l'autre, contrairement au rang."""
    lignes: tuple[int, ...] = ()
    """Avec `query` : lignes touchées (base 1), pour les extraits."""


def titres_de_section(profil: Profil, section: str) -> frozenset[str]:
    """Titres normalisés qui répondent à `section` : le titre exact, plus ceux
    que le profil range sous ce nom s'il s'agit d'un rôle."""
    return frozenset({normalize_heading(section)} | profil.titres_du_role(section.strip()))


def a_une_section(doc: Doc, titres: frozenset[str]) -> bool:
    return any(h.normalized in titres for h in doc.sections)


def valeur_texte(valeur) -> str:
    """Forme textuelle d'une valeur de frontmatter, pour comparer et compter."""
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, (dt.date, dt.datetime)):
        return valeur.isoformat()
    return str(valeur)


def _present(valeur) -> bool:
    return valeur is not None and valeur != "" and valeur != [] and valeur != {}


def correspond_au_champ(frontmatter: Mapping, nom: str, attendu: str | bool) -> bool:
    valeur = frontmatter.get(nom)
    if attendu is True:
        return _present(valeur)
    if attendu is False:
        return not _present(valeur)
    if isinstance(valeur, list):
        return any(valeur_texte(v) == attendu for v in valeur)
    return valeur is not None and valeur_texte(valeur) == attendu


def prefixe_de(under: str) -> str:
    """Un sous-chemin se lit comme un répertoire : `exploitation` ne couvre pas
    `exploitation-ancien/`."""
    prefixe = under.strip().strip("/")
    if not prefixe:
        return ""
    return prefixe if prefixe.endswith(".md") else prefixe + "/"


def filtrer(
    lecteur: Lecteur,
    modele: BaseModele,
    criteres: Criteres,
    *,
    cible: tuple[str, str] | None = None,
    bases: frozenset[str] | None = None,
    maintenant: dt.datetime | None = None,
) -> list[Trouve]:
    """Documents de `modele` qui satisfont tous les critères, dans l'ordre de
    `criteres.tri`.

    `cible` : le document de `links_to`, résolu par l'appelant — obligatoire si
    le critère est posé. `bases` : celles dont les citations comptent (impact,
    tri par citations) ; None : toutes.
    """
    profil = modele.profil
    docs = modele.concepts()
    if criteres.type is not None:
        docs = [d for d in docs if d.type == criteres.type]
    for tag in criteres.tags:
        docs = [d for d in docs if tag in d.tags]
    for nom, attendu in criteres.fields:
        docs = [d for d in docs if correspond_au_champ(d.frontmatter, nom, attendu)]
    if criteres.section:
        titres = titres_de_section(profil, criteres.section)
        docs = [d for d in docs if a_une_section(d, titres)]
    if criteres.without_section:
        titres = titres_de_section(profil, criteres.without_section)
        docs = [d for d in docs if not a_une_section(d, titres)]
    if criteres.under:
        prefixe = prefixe_de(criteres.under)
        docs = [d for d in docs if d.chemin == prefixe or d.chemin.startswith(prefixe)]

    rang: dict[str, int] = {}
    touchees: dict[str, tuple[int, ...]] = {}
    occurrences: dict[str, int] = {}
    if criteres.query:
        issue = run_search(modele.base, criteres.query, "regex" if criteres.query_regex else "keyword", 10**9)
        if not issue.partial:  # le repli en OU de kb_search n'est pas un filtre
            for i, h in enumerate(issue.docs):
                rel = modele.base.rel_path(h.path)
                rang[rel] = i
                touchees[rel] = tuple(h.lines)
                occurrences[rel] = h.match_count
        docs = [d for d in docs if d.chemin in rang]

    amont: dict[str, bool] = {}
    degres: dict[str, int] = {}
    if criteres.links_to:
        if cible is None:
            raise ValueError("links_to sans cible résolue")
        for e in lecteur.entrants(*cible):
            if e.base == modele.nom:
                # La section est celle du document qui cite : c'est le profil de sa
                # base, donc celui de `modele`, qui dit si elle tient le rôle upstream.
                en_amont = profil.role_de_section(e.lien.section) == "upstream"
                amont[e.chemin] = amont.get(e.chemin, False) or en_amont
        for degre, sources in enumerate(
            lecteur.dependants(*cible, criteres.links_to_depth, bases), start=1
        ):
            for base, chemin in sources:
                if base == modele.nom:
                    degres.setdefault(chemin, degre)
        docs = [d for d in docs if d.chemin in degres]

    derives = lecteur.derives(modele.nom) if criteres.upstream_changed or criteres.tri == "reaudit" else {}
    if criteres.upstream_changed:
        docs = [d for d in docs if d.chemin in derives]

    tri = criteres.tri
    fraicheurs: dict[str, Fraicheur] = {}
    if criteres.older_than_months or tri in ("oldest", "reaudit"):
        dates = None
        if any(date_declaree(d.frontmatter) is None for d in docs):
            dates = lecteur.dates_commit(modele.nom)  # un seul `git log` pour tous
        fraicheurs = {d.chemin: lecteur.fraicheur(modele, d, dates if dates is not None else {}) for d in docs}
    if criteres.older_than_months:
        seuil = mois_avant(maintenant or dt.datetime.now(UTC), criteres.older_than_months)
        docs = [d for d in docs if fraicheurs[d.chemin].date < seuil]

    citations: dict[str, int] = {}
    if tri in ("most_cited", "reaudit"):
        citations = {d.chemin: lecteur.citations(modele.nom, d.chemin, bases) for d in docs}

    maintenant = maintenant or dt.datetime.now(UTC)
    trouves = []
    for d in docs:
        score = None
        if tri == "reaudit":
            age = max(0.0, (maintenant - fraicheurs[d.chemin].date).days / 30.44)
            score = round(age * (1 + citations[d.chemin]) * (2 if d.chemin in derives else 1), 1)
        trouves.append(Trouve(
            d,
            fraicheur=fraicheurs.get(d.chemin),
            amont=amont.get(d.chemin, False),
            derives=derives.get(d.chemin, ()),
            citations=citations.get(d.chemin),
            score=score,
            degre=degres.get(d.chemin),
            lignes=touchees.get(d.chemin, ()),
            occurrences=occurrences.get(d.chemin),
        ))
    if tri == "path" and degres:
        trouves.sort(key=lambda t: (t.degre, t.doc.chemin))  # le degré 1 d'abord
    elif tri == "relevance":
        trouves.sort(key=lambda t: rang[t.doc.chemin])
    elif tri == "oldest":
        trouves.sort(key=lambda t: (t.fraicheur.date, t.doc.chemin))
    elif tri == "most_cited":
        trouves.sort(key=lambda t: (-t.citations, t.doc.chemin))
    elif tri == "reaudit":
        trouves.sort(key=lambda t: (-t.score, t.doc.chemin))
    return trouves


# --- effectifs ------------------------------------------------------------------


@dataclass(frozen=True)
class Effectif:
    valeur: str
    nombre: int
    declare: bool | None
    """True/False si la base déclare un vocabulaire pour cette clé ; None sinon."""


def compter(docs: list[Doc], cle: str, profil: Profil) -> tuple[str, list[Effectif]]:
    """(nature de la clé, effectifs par valeur), les plus fréquentes d'abord.

    Clés : `type`, `tags`, `section`, une facette déclarée par le profil, ou un
    nom de champ. Les valeurs déclarées s'affichent même à effectif nul ; une
    valeur observée hors du vocabulaire déclaré est marquée.
    """
    comptes: dict[str, int] = {}
    vocabulaire: list[str] | None = None
    affichage: dict[str, str] = {}

    def ajouter(valeur: str) -> None:
        comptes[valeur] = comptes.get(valeur, 0) + 1

    if cle == "type":
        nature = "type"
        vocabulaire = list(profil.types) or None
        for d in docs:
            ajouter(d.type or SANS_TYPE)
    elif cle == "tags":
        nature = "tag"
        for d in docs:
            for tag in dict.fromkeys(d.tags) or [SANS_TAG]:
                ajouter(tag)
    elif cle == "section":
        nature = "section"
        for d in docs:
            for h in {h.normalized: h for h in reversed(d.sections)}.values():
                affichage.setdefault(h.normalized, h.text)
                ajouter(h.normalized)
    elif profil.separateur and cle in profil.facettes:
        nature = "facette"
        vocabulaire = list(profil.facettes[cle].valeurs) or None
        for d in docs:
            valeurs = [f[1] for t in d.tags if (f := profil.facette_de(t)) and f[0] == cle]
            for v in dict.fromkeys(valeurs) or [ABSENT]:
                ajouter(v)
    else:
        nature = "champ"
        champ = profil.champs.get(cle)
        vocabulaire = list(champ.valeurs) if champ and champ.valeurs else None
        for d in docs:
            valeur = d.frontmatter.get(cle)
            if isinstance(valeur, list):
                valeurs = [valeur_texte(v) for v in valeur] or [ABSENT]
            else:
                valeurs = [valeur_texte(valeur)] if _present(valeur) else [ABSENT]
            for v in dict.fromkeys(valeurs):
                ajouter(v)

    for v in vocabulaire or []:
        comptes.setdefault(v, 0)
    connus = set(vocabulaire or [])
    effectifs = [
        Effectif(
            affichage.get(v, v),
            n,
            None if vocabulaire is None or v in (SANS_TYPE, ABSENT) else v in connus,
        )
        for v, n in comptes.items()
    ]
    effectifs.sort(key=lambda e: (-e.nombre, e.valeur))
    return nature, effectifs
