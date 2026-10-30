"""Ce qu'il faut pour juger une proposition, réuni sur sa page (§ 5 bis.2).

L'interface ne tranche pas — c'est le rôle gestionnaire, hors d'elle (§ 7) —,
mais celui qui tranche décide mieux s'il voit, au même endroit :

* les **relations** entre propositions : une correction déposée pour une autre,
  une suite, une annulation. Sans elles, on intègre une erreur qu'une
  correction en attente signalait déjà ;
* la **fiche visée**, désignée dans le texte par un chemin ou un lien ;
* les **fiches proches**, pour repérer un doublon de ce qui existe ;
* la suivante et la précédente, dans l'ordre de la boîte de réception.

Tout se déduit du texte des propositions et du corpus, rien n'est déclaré.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ..lecture.liens import INTERBASE, INTERNE, classer, extraire
from ..lecture.modele import DOC, REP, Doc, cible_document, resoudre
from ..mdutil import parse_document, parse_headings
from ..search import run_search
from ..lecture.modele import CHARGEUR

ID_PROPOSITION = re.compile(r"\bprop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4}\b")
#: « (corrige prop-…) », « (suite de prop-…) » : le verbe qui précède l'id.
_RELATION = re.compile(r"\((?P<verbe>[^()]{0,30}?)\s*(?P<id>prop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4})\)")
_CHEMIN = re.compile(r"(?<![\w/:.-])(?P<chemin>(?:[a-z0-9-]+:/)?/?[\w.-]+(?:/[\w.-]+)*\.md)\b")

LIBELLES_STATUT = {"pending": "en attente", "accepted": "intégrée", "rejected": "rejetée"}
LIBELLES_CONFIANCE = {"high": "haute", "medium": "moyenne", "low": "basse"}
LIBELLES_TYPE = {"observation": "observation", "correction": "correction", "addition": "ajout", "question": "question"}

#: Verbe écrit dans l'objet → nature de la relation, vue de la proposition qui écrit.
NATURES = {"corrige": "corrige", "suite de": "suite de", "issue de": "suite de", "annule": "annule",
           "remplace": "annule", "complète": "suite de"}
#: Et vue de la proposition visée.
INVERSES = {"corrige": "corrigée par", "suite de": "suivie de", "annule": "annulée par", "cite": "citée par"}


@dataclass(frozen=True)
class Relation:
    nature: str
    base: str
    id: str


def relations(r, noms: list[str]) -> dict:
    """Relations entre les propositions des bases `noms` : pour chaque (base, id),
    ses relations sortantes (ce qu'elle corrige, suit, annule, cite) et entrantes
    (qui la corrige, la suit…). En cache par état des propositions."""
    cle = ("relations", tuple(noms), tuple(
        tuple((e.id, e.status) for e in r.hub.propositions(n)[0].entries) for n in noms))

    def calcul():
        ou: dict[str, str] = {}
        entrees: dict[tuple[str, str], object] = {}
        for nom in noms:
            collecte, textes = r.hub.propositions(nom)
            for e in collecte.entries:
                ou[e.id] = nom
                entrees[(nom, e.id)] = (e, textes.get(e.id, ""))
        sortantes: dict[tuple[str, str], list[Relation]] = defaultdict(list)
        entrantes: dict[tuple[str, str], list[Relation]] = defaultdict(list)
        for (nom, ident), (e, texte) in entrees.items():
            concerns = str(e.frontmatter.get("concerns") or "")
            verbes = {m.group("id"): m.group("verbe").strip().lower() for m in _RELATION.finditer(concerns)}
            for autre in dict.fromkeys(ID_PROPOSITION.findall(concerns + "\n" + texte)):
                if autre == ident or autre not in ou:
                    continue
                verbe = verbes.get(autre, "")
                nature = next((n for v, n in NATURES.items() if verbe.startswith(v)), None)
                if nature is None:
                    nature = "corrige" if e.frontmatter.get("type") == "correction" else "cite"
                sortantes[(nom, ident)].append(Relation(nature, ou[autre], autre))
                entrantes[(ou[autre], autre)].append(Relation(INVERSES[nature], nom, ident))
        return dict(sortantes), dict(entrantes)

    sortantes, entrantes = r.hub.memo(cle, calcul)
    return {"sortantes": sortantes, "entrantes": entrantes}


def fiches_visees(r, nom: str, entree, texte: str) -> list[tuple[str, Doc]]:
    """Les documents que la proposition désigne : ses liens, les chemins qu'elle
    écrit (en code ou non), et, une fois intégrée, `integrated-into`."""
    modeles = r.lecteur.modeles(sorted(r.visibles))
    corps = parse_document(texte, loader=CHARGEUR).body
    concerns = str(entree.frontmatter.get("concerns") or "")
    cibles = [l.cible for l in extraire(corps, parse_headings(corps), nom, "").liens]
    for m in _CHEMIN.finditer(concerns + "\n" + corps):
        brut = m.group("chemin")
        if not re.match(r"^[a-z0-9-]+:/", brut) and not brut.startswith("/"):
            brut = "/" + brut  # un chemin cité nu part de la racine du corpus
        cibles.append(classer(brut, nom, ""))
    integre = entree.frontmatter.get("integrated-into") or []
    for chemin in (integre if isinstance(integre, list) else [integre]):
        cibles.append(classer("/" + str(chemin).lstrip("/"), nom, ""))
    trouvees: dict[tuple[str, str], Doc] = {}
    for cible in cibles:
        if cible.sorte not in (INTERNE, INTERBASE) or cible.base not in r.visibles:
            continue
        resolution = resoudre(cible, modeles)
        if resolution.sorte not in (DOC, REP):
            continue
        doc = cible_document(resolution, modeles)
        if doc is not None and doc not in trouvees:
            trouvees[doc] = modeles[doc[0]].docs[doc[1]]
    return list(trouvees.items())[:5]


def fiches_proches(r, nom: str, entree, exclues: set) -> list[Doc]:
    """Les fiches dont le texte touche le plus l'objet de la proposition : de quoi
    repérer qu'une « addition » répète l'existant. Recherche en mots, avec le
    repli en OU de `kb_search` — ici on cherche ce qui ressemble, pas une preuve."""
    concerns = str(entree.frontmatter.get("concerns") or "")
    mots = [m for m in re.findall(r"[\wÀ-ÿ'-]{4,}", concerns) if not ID_PROPOSITION.match(m)][:8]
    if not mots:
        return []
    modele = r.lecteur.modele(nom)
    issue = run_search(modele.base, " ".join(mots), "keyword", 12)
    proches = []
    for hits in issue.docs:
        rel = modele.base.rel_path(hits.path)
        doc = modele.docs.get(rel)
        if doc is None or doc.reserve or (nom, rel) in exclues:
            continue
        proches.append(doc)
        if len(proches) == 5:
            break
    return proches


def ordre_de_revue(r) -> list[tuple[str, object]]:
    """Les propositions en attente des bases visibles, les plus anciennes d'abord :
    l'ordre de la boîte de réception, et celui de « précédente / suivante »."""
    en_attente = []
    for nom in r.visibles_tries():
        for e in r.hub.propositions(nom)[0].entries:
            if e.status == "pending":
                en_attente.append((nom, e))
    return sorted(en_attente, key=lambda x: (x[1].submitted_at or "9999", x[1].id))


_REPERE = re.compile(r"(?P<id>\bprop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4}\b)|(?<![\w/:.-])(?P<chemin>(?:[a-z0-9-]+:/)?/?[\w.-]+(?:/[\w.-]+)+\.md)\b")


def segments(r, nom: str, texte: str) -> list[tuple[str, str | None]]:
    """Un texte libre (objet, source déclarée) découpé en (morceau, adresse) : un
    id de proposition connu ou un chemin de fiche qui existe y deviennent des
    liens ; le reste reste du texte."""
    out, position = [], 0
    modeles = None
    for m in _REPERE.finditer(texte):
        url = None
        if m.group("id"):
            url = r.proposition_vers(m.group("id"))
        else:
            brut = m.group("chemin")
            brut = brut if (brut.startswith("/") or ":/" in brut) else "/" + brut
            cible = classer(brut, nom, "")
            if cible.sorte in (INTERNE, INTERBASE) and cible.base in r.visibles:
                modeles = modeles or r.lecteur.modeles(sorted(r.visibles))
                doc = cible_document(resoudre(cible, modeles), modeles)
                if doc is not None:
                    from .commun import url_doc
                    url = url_doc(*doc)
        if url is None:
            continue
        if m.start() > position:
            out.append((texte[position:m.start()], None))
        out.append((m.group(0), url))
        position = m.end()
    if position < len(texte):
        out.append((texte[position:], None))
    return out
