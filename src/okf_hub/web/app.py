"""Interface web de consultation, en lecture seule (§ 5 bis.2).

Processus distinct des instances MCP (`okf-hub web`), sur le même moteur de
lecture : il lit le disque par le même registre et n'a aucun état qui fasse
autorité (§ 4.4.a). **Toutes ses routes sont en GET**, aucune n'écrit — ni
corpus, ni proposition, ni même un verrou d'index git.

Les points d'entrée sont synchrones : Starlette les exécute dans son pool de
fils, ce qui garde git et ripgrep hors de la boucle d'événements.

Adresses stables — ce sont elles que citent les autres interfaces (§ 5 bis.3) :

| Adresse | Page |
|---|---|
| `/` | Accueil : les bases visibles |
| `/b/<base>/` | Une base : son `index.md`, ses vues, ses effectifs, ses fiches piliers |
| `/b/<base>/<chemin>.md` | Un document, avec sa généalogie, son impact et sa dérive amont |
| `/b/<base>/<dossier>/` | Un répertoire du corpus |
| `/b/<base>/-/explorer`, `/-/vues/<n>`, `/-/manques`, `/-/gouvernance`, `/-/propositions[/<id>]`, `/-/journal` | Pages d'une base |
| `/explorer`, `/chronologie`, `/graphe`, `/etat`, `/a-propos` | Pages du hub |

`/trouver`, `/recherche` et `/b/<base>/-/trouver` (rév. 4.5) redirigent vers
l'Explorer. Le segment `-` sépare les pages d'une base de son corpus : un dossier
de corpus nommé `propositions/` reste ainsi adressable.
"""

from __future__ import annotations

import datetime as dt
import math
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import yaml
from starlette.applications import Starlette
from starlette.responses import FileResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import governance
from ..errors import ToolError
from ..lecture.confiance import confiance, instant
from ..lecture.graphe import analyser, cycles
from ..lecture.liens import ANCRE, EXTERNE, INTERBASE, INTERNE
from ..lecture.modele import CHARGEUR, DOC, BaseModele, Doc, resoudre
from ..lecture.profil import Vue
from ..lecture.rendu import Contexte, ancre_de_source, rendre
from ..lecture.requete import CLES_CRITERES, SANS_TYPE, Criteres, compter, filtrer, lire_criteres
from ..mdutil import normalize_heading, parse_document
from ..search import RESERVED_NAMES
from ..server import META_BASES
from ..tools.proposal_status_tool import STATUSES
from . import explorer, pages, revue
from .commun import (
    jour_lisible,
    CSP,
    ICI,
    PROPOSITIONS_PAR_PAGE,
    TYPES_IMAGES,
    CSP_SVG,
    HISTORIQUE,
    Hub,
    Introuvable,
    Requete,
    _EnTetesDeSecurite,
    chemin_d_image,
    hors_route,
    page_demandee,
    page_html,
    url_base,
    url_doc,
    url_page,
    url_rep,
)
from .figure import disposer, epaisseur_de

__all__ = ["CSP", "Hub", "creer_app"]


def creer_app(hub: Hub) -> Starlette:
    routes = [
        Route("/", page_html(accueil)),
        Route("/explorer", page_html(explorer.explorer)),
        Route("/propositions", page_html(boite)),
        Route("/aller", page_html(aller)),
        Route("/chronologie", page_html(pages.chronologie)),
        Route("/graphe", page_html(graphe)),
        Route("/etat", page_html(pages.etat)),
        Route("/a-propos", page_html(pages.a_propos)),
        # Adresses de la rév. 4.5, conservées : elles mènent à l'Explorer.
        Route("/trouver", page_html(explorer.ancienne_adresse)),
        Route("/recherche", page_html(explorer.ancienne_adresse)),
        Mount("/static", StaticFiles(directory=ICI / "static"), name="static"),
        Route("/b/{base}", page_html(base_sans_barre)),
        Route("/b/{base}/", page_html(base_accueil)),
        Route("/b/{base}/-/explorer", page_html(explorer.explorer)),
        Route("/b/{base}/-/trouver", page_html(explorer.ancienne_adresse)),
        Route("/b/{base}/-/vues/{n:int}", page_html(vue)),
        Route("/b/{base}/-/manques", page_html(pages.manques)),
        Route("/b/{base}/-/gouvernance", page_html(pages.gouvernance)),
        Route("/b/{base}/-/propositions", page_html(propositions)),
        Route("/b/{base}/-/propositions/{id}", page_html(proposition)),
        Route("/b/{base}/-/journal", page_html(journal)),
        Route("/b/{base}/{chemin:path}", page_html(document_ou_repertoire)),
    ]
    app = Starlette(routes=routes, exception_handlers={404: hors_route})
    app.state.hub = hub
    app.add_middleware(_EnTetesDeSecurite)
    return app




def _derniere_entree_du_journal(modele: BaseModele) -> str | None:
    log = modele.docs.get("log.md")
    if log is None:
        return None
    for h in log.sections:
        if h.level == 2:
            return h.text
    return None


_FIN_DE_PHRASE = re.compile(r"(?<=[.!?»])\s+(?=[A-ZÀ-ÝÉÈ«])")


def _phrases(texte: str) -> list[str]:
    return [p.strip() for p in _FIN_DE_PHRASE.split(texte) if p.strip()]


def _regles_communes(r: Requete, descriptions: dict[str, str]) -> list[dict]:
    """Les phrases que plusieurs descriptions de bases répètent mot pour mot.

    Une description de manifeste est écrite pour le routage d'un agent (§ 3.3),
    qui la lit base par base : une règle d'aiguillage s'y recopie donc dans
    chaque base concernée. Sur une page qui les montre toutes, elle se lit une
    fois. Un segment « condition → base » devient une étape de l'aiguillage : un
    « sinon, » en tête le rattache au « non » de l'étape d'avant, et ce qui précède
    un « : » est le contexte de l'étape, pas sa question — d'où un arbre de
    décision, sans rien changer au texte des bases.
    """
    occurrences: dict[str, list[str]] = {}
    for nom, texte in descriptions.items():
        for phrase in dict.fromkeys(_phrases(texte)):
            occurrences.setdefault(phrase, []).append(nom)
    regles = []
    for phrase, bases in occurrences.items():
        if len(bases) < 2:
            continue
        lignes = []
        for segment in phrase.rstrip(".").split(" ; "):
            condition, fleche, cible = segment.partition("→")
            if not fleche:
                lignes.append({"condition": segment.strip(), "cibles": []})
                continue
            noms = [n for n in re.findall(r"[a-z0-9-]+", cible) if n in r.hub.registry.names()]
            texte = condition.strip()
            sinon = bool(re.match(r"(?i)^sinon\s*,\s*", texte))
            texte = re.sub(r"(?i)^sinon\s*,\s*", "", texte)
            contexte, deux_points, question = texte.partition(" : ")
            if not deux_points:
                contexte, question = "", texte
            lignes.append({"condition": condition.strip(), "cibles": noms, "reste": cible.strip(),
                           "sinon": sinon, "contexte": contexte.strip(), "question": question.strip()})
        regles.append({"phrase": phrase, "bases": bases, "lignes": lignes})
    return regles


def _resume_base(r: Requete, nom: str, communes: set[str]) -> dict:
    modele = r.modele(nom)
    base = modele.base
    en_attente = [e for e in r.hub.propositions(nom)[0].entries if e.status == "pending"]
    dates = sorted(e.submitted_at for e in en_attente if e.submitted_at)
    propres = [p for p in _phrases(base.manifest.description) if p not in communes]
    return {
        "nom": nom,
        "titre": base.manifest.title,
        "phrase": propres[0] if propres else base.manifest.description,
        # Des fiches, partout : les fichiers réservés (index, journal) ne sont pas
        # de la connaissance, et l'Explorer ne les compte pas non plus.
        "fiches": len(modele.concepts()),
        "en_attente": len(en_attente),
        "plus_ancienne": dates[0] if dates else None,
        "a_reverifier": sum(1 for d in r.lecteur.derives(nom).values() if any(x.base in r.visibles for x in d)),
        "gouvernance": governance.status_of_file(base.manifest.governance_rules),
        "journal": _derniere_entree_du_journal(modele),
        # La dernière activité réelle : le titre du journal peut dater d'un mois
        # quand les commits datent d'hier.
        "dernier_commit": next(iter(r.lecteur.historique(nom, 1)), None),
        "du_hub": nom in META_BASES,
    }


#: Les ordres des cartes de l'accueil. Par défaut, ce qui demande une action
#: d'abord, le plus chargé en tête ; les autres par nom.
ORDRES_DE_L_ACCUEIL = {
    "action": lambda b: (-(b["en_attente"] + b["a_reverifier"]) if (b["en_attente"] or b["a_reverifier"]) else 1, b["titre"].casefold()),
    "nom": lambda b: b["titre"].casefold(),
    "taille": lambda b: (-b["fiches"], b["titre"].casefold()),
    "activite": lambda b: (-(b["dernier_commit"].date.timestamp() if b["dernier_commit"] else 0), b["titre"].casefold()),
}


def accueil(r: Requete) -> Response:
    noms = r.visibles_tries()
    regles = _regles_communes(r, {n: r.hub.registry.get(n).manifest.description for n in noms})
    communes = {regle["phrase"] for regle in regles}
    bases = [_resume_base(r, n, communes) for n in noms]
    dates = sorted(b["plus_ancienne"] for b in bases if b["plus_ancienne"])
    ordre = r.request.query_params.get("ordre") or "action"
    if ordre not in ORDRES_DE_L_ACCUEIL:
        r.avis.append(f"ordre « {ordre} » inconnu ({', '.join(ORDRES_DE_L_ACCUEIL)}) : à traiter d'abord")
        ordre = "action"
    domaines = sorted((b for b in bases if not b["du_hub"]), key=ORDRES_DE_L_ACCUEIL[ordre])
    plus_ancienne = instant(dates[0]) if dates else None
    return r.page(
        "accueil.html", bases=domaines, bases_du_hub=[b for b in bases if b["du_hub"]], regles=regles, ordre=ordre,
        attente_jours=(dt.datetime.now(dt.timezone.utc) - plus_ancienne).days if plus_ancienne else None,
        en_attente=sum(b["en_attente"] for b in bases), plus_ancienne=dates[0] if dates else None,
    )


def boite(r: Requete) -> Response:
    """Toutes les propositions en attente des bases visibles, les plus anciennes
    d'abord : le travail de revue, en une liste."""
    params = r.request.query_params
    demandees = [b for b in params.getlist("base") if b]
    for b in demandees:
        if b not in r.visibles:
            r.avis.append(f"base « {b} » inconnue ou non ouverte : ignorée")
    noms = [b for b in demandees if b in r.visibles] or r.visibles_tries()
    type_voulu = params.get("type") or None
    toutes = []
    for nom in r.visibles_tries():
        for e in r.hub.propositions(nom)[0].entries:
            if e.status == "pending":
                toutes.append((nom, e))
    par_base = Counter(nom for nom, _ in toutes)
    par_type = Counter(str(e.frontmatter.get("type") or "?") for nom, e in toutes if nom in noms)
    if type_voulu and type_voulu not in par_type:
        r.avis.append(f"type « {type_voulu} » : aucune proposition en attente de ce type")
    retenues = sorted(
        ((nom, e) for nom, e in toutes if nom in noms and (not type_voulu or e.frontmatter.get("type") == type_voulu)),
        key=lambda x: (x[1].submitted_at or "9999", x[1].id),
    )
    ordre = _tri_des_propositions(r, "anciennes")
    if ordre == "recentes":
        retenues.reverse()
    pages = max(1, -(-len(retenues) // BOITE_PAR_PAGE))
    page = min(page_demandee(params), pages)
    maintenant = dt.datetime.now(dt.timezone.utc)
    rel = revue.relations(r, r.visibles_tries())
    lignes = []
    jour_courant = None
    for nom, e, profondeur, sortantes in _fils(retenues[(page - 1) * BOITE_PAR_PAGE : page * BOITE_PAR_PAGE], rel):
        depose = instant(e.frontmatter.get("submitted-at"))
        local = depose.astimezone() if depose else None  # l'heure du poste qui sert la page
        jour = local.date() if local else None
        lignes.append({
            "base": nom, "e": e, "profondeur": profondeur, "sortantes": sortantes,
            "heures": int((maintenant - depose).total_seconds() // 3600) if depose else None,
            "heure": local.strftime("%H:%M") if local else "",
            # Un titre de jour à chaque changement de jour, entre les fils seulement.
            "nouveau_jour": jour_lisible(jour) if profondeur == 0 and jour != jour_courant else None,
            "fil": [],
        })
        if profondeur == 0:
            jour_courant = jour
    # Une correction, une suite se lisent dans le fil de ce qu'elles visent, replié
    # sous lui ; seules les têtes de fil sont des lignes du tableau.
    tetes: list[dict] = []
    for ligne in lignes:
        if ligne["profondeur"] and tetes:
            tetes[-1]["fil"].append(ligne)
        else:
            tetes.append(ligne)
    lignes = tetes
    return r.page(
        "boite.html", lignes=lignes, total=len(retenues), par_base=sorted(par_base.items()),
        par_type=par_type.most_common(), noms_demandes=demandees, type_voulu=type_voulu,
        page=page, pages=pages, par_page=BOITE_PAR_PAGE, ordre=ordre,
    )


#: La boîte tient sur une page dans l'usage courant : on la parcourt et on la
#: filtre, on n'y feuillette pas.
BOITE_PAR_PAGE = 200



def aller(r: Requete) -> Response:
    """Le saut de l'en-tête (Ctrl+K) : une base par son nom, un document par sa
    référence `<base>:/<chemin>` ou son titre exact, une proposition par son
    identifiant ; sinon, les candidats groupés, ou l'Explorer si rien ne répond.

    `liste=1` rend toujours la liste, sans redirection : c'est ce que lit la
    palette de `okf.js`, qui l'insère telle quelle — la page servie, pas une API
    (§ 1.2)."""
    q = (r.request.query_params.get("q") or "").strip()
    liste = r.request.query_params.get("liste") == "1"
    if not q and not liste:
        return RedirectResponse("/explorer", 303)
    if not liste:
        if q in r.visibles:
            return RedirectResponse(url_base(q), 303)
        m = re.match(r"^([a-z0-9-]+):/(.+)$", q)
        if m and m.group(1) in r.visibles:
            modele = r.lecteur.modele(m.group(1))
            trouve = modele.trouver(m.group(2).strip("/"), m.group(2).endswith("/"))
            if trouve is not None:
                return RedirectResponse(url_doc(m.group(1), trouve[1]) if trouve[0] == DOC else url_rep(m.group(1), trouve[1]), 303)
        if revue.ID_PROPOSITION.fullmatch(q) and (adresse := r.proposition_vers(q)):
            return RedirectResponse(adresse, 303)
    candidats = _candidats_du_saut(r, q) if q else []
    exacts = [c for c in candidats if c[0] == 0]
    if not liste and len(exacts) == 1:
        return RedirectResponse(url_doc(exacts[0][1], exacts[0][2].chemin), 303)
    bases = [n for n in r.visibles_tries() if q.casefold() in n or q.casefold() in r.hub.registry.get(n).manifest.title.casefold()]
    propositions = _propositions_du_saut(r, q) if q else []
    if not liste and not candidats and not bases and not propositions:
        return RedirectResponse(url_page(None, "explorer", q=q), 303)
    return r.page("aller.html", q=q, bases=bases, candidats=candidats[:SAUT_MAX],
                  plus=max(0, len(candidats) - SAUT_MAX), propositions=propositions,
                  titres={n: r.hub.registry.get(n).manifest.title for n in bases})


def _propositions_du_saut(r: Requete, q: str) -> list[tuple[str, object]]:
    """Les propositions dont l'identifiant ou l'objet contient `q`, en attente
    d'abord, les plus récentes ensuite."""
    cible = q.casefold()
    trouvees = []
    for nom in r.visibles_tries():
        for e in r.hub.propositions(nom)[0].entries:
            if cible in e.id or cible in str(e.frontmatter.get("concerns") or "").casefold():
                trouvees.append((nom, e))
    trouvees.sort(key=lambda x: (x[1].status != "pending", -(instant(x[1].frontmatter.get("submitted-at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)).timestamp()))
    return trouvees[:SAUT_PROPOSITIONS]


#: Ce que la palette du saut montre au plus ; au-delà, l'Explorer.
SAUT_MAX = 30
SAUT_PROPOSITIONS = 8
_RANGS_DU_SAUT = ("titre exact", "début du titre", "dans le titre", "tous les mots du titre", "dans le chemin")


def _candidats_du_saut(r: Requete, q: str) -> list[tuple[int, str, Doc, str]]:
    """Documents dont le titre ou un synonyme répond à `q`, du plus sûr au moins
    sûr : (rang, base, document, ce qui a répondu). Le titre exact d'abord — c'est
    ce qu'on tape quand on sait où l'on va."""
    cible = q.casefold()
    mots = cible.split()
    out = []
    for nom in r.visibles_tries():
        modele = r.lecteur.modele(nom)
        champ = modele.profil.champ_du_role("aliases")
        for d in modele.concepts():
            noms = [d.titre]
            if champ is not None:
                synonymes = d.frontmatter.get(champ.nom)
                noms += [str(x) for x in (synonymes if isinstance(synonymes, list) else [synonymes] if synonymes else [])]
            meilleur = None
            for i, libelle in enumerate(noms):
                replie = libelle.casefold()
                rang = (0 if replie == cible else 1 if replie.startswith(cible) else 2 if cible in replie
                        else 3 if len(mots) > 1 and all(m in replie for m in mots) else None)
                if rang is not None and (meilleur is None or rang < meilleur[0]):
                    meilleur = (rang, libelle if i else "")
            if meilleur is None and cible in d.chemin.casefold():
                meilleur = (4, "")
            if meilleur is not None:
                out.append((meilleur[0], nom, d, meilleur[1]))
    return sorted(out, key=lambda c: (c[0], len(c[2].titre), c[2].titre.casefold(), c[1]))


def base_sans_barre(r: Requete) -> Response:
    return RedirectResponse(url_base(r.nom_de_base()), 308)


def base_accueil(r: Requete) -> Response:
    return _repertoire(r, r.modele(r.nom_de_base()), "")


def document_ou_repertoire(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    chemin = r.request.path_params["chemin"]
    if chemin.endswith("/"):
        dossier = chemin.strip("/")
        if dossier not in modele.repertoires:
            raise Introuvable(chemin)
        return _repertoire(r, modele, dossier)
    if chemin in modele.docs:
        return _document(r, modele, modele.docs[chemin])
    if chemin in modele.repertoires:
        return RedirectResponse(url_rep(modele.nom, chemin), 308)
    return _image(modele, chemin)


def _image(modele: BaseModele, chemin: str) -> Response:
    fichier = chemin_d_image(modele, chemin)
    if fichier is None:
        raise Introuvable(chemin)
    reponse = FileResponse(fichier, media_type=TYPES_IMAGES[fichier.suffix.lower()])
    if fichier.suffix.lower() == ".svg":
        reponse.headers["Content-Security-Policy"] = CSP_SVG
    return reponse


def _fil(modele: BaseModele, chemin: str) -> list[tuple[str, str]]:
    """(libellé, adresse) de la base — par son titre, comme sur sa page — jusqu'au
    dossier qui contient `chemin`."""
    nom = modele.nom
    fil = [(modele.base.manifest.title, url_base(nom))]
    parties = [p for p in chemin.split("/") if p]
    for i in range(len(parties) - (0 if chemin.endswith("/") else 1)):
        fil.append((parties[i], url_rep(nom, "/".join(parties[: i + 1]))))
    return fil


def _panneau(r: Requete, modele: BaseModele) -> dict:
    """Ce que la colonne d'une base montre partout : effectifs, rôles, vues."""
    profil = modele.profil
    _, types = compter(modele.concepts(), "type", profil)
    roles = []
    for role in ("warnings", "observations"):
        if profil.titres_du_role(role):
            n = len(filtrer(r.lecteur, modele, Criteres(section=role)))
            roles.append((role, n))
    return {
        "types": types,
        "roles": roles,
        "vues": list(enumerate(profil.vues)),
        "avertissements": profil.avertissements,
        "piliers": _piliers(r, modele),
        "a_reverifier": sum(1 for d in r.lecteur.derives(modele.nom).values() if any(x.base in r.visibles for x in d)),
    }


#: Fiches piliers montrées dans la colonne d'une base.
PILIERS = 8


def _piliers(r: Requete, modele: BaseModele) -> list[tuple[Doc, int]]:
    """Les fiches les plus citées de la base — par des documents que le visiteur
    peut lire, sommaires et journaux exclus des deux côtés : un sommaire cite
    tout et n'apprend rien sur ce qui compte."""
    comptes = []
    for (nom, chemin), liens in r.lecteur.index_entrants().items():
        if nom != modele.nom or posixpath.basename(chemin) in ("index.md", "log.md") or chemin not in modele.docs:
            continue
        sources = {(e.base, e.chemin) for e in liens
                   if e.base in r.visibles and posixpath.basename(e.chemin) not in ("index.md", "log.md")}
        if sources:
            comptes.append((len(sources), chemin))
    comptes.sort(key=lambda x: (-x[0], x[1]))
    return [(modele.docs[c], n) for n, c in comptes[:PILIERS]]


#: Fiches listées par page d'un dossier ; liens entrants listés par base sur une fiche.
REPERTOIRE_PAR_PAGE = 200
ENTRANTS_PAR_BASE = 50
INDEX_EN_LIGNE_MAX = 40_000  # octets de markdown


def _repertoire(r: Requete, modele: BaseModele, dossier: str) -> Response:
    sous, docs = modele.contenu(dossier)
    index = modele.index_de(dossier)
    rendu = None
    index_long = False
    if index is not None:
        # Un index généré peut lister des milliers de fiches (240 Ko de markdown
        # mesurés) : il se lit alors sur sa page, pas en tête du dossier.
        index_long = modele.docs[index].taille > INDEX_EN_LIGNE_MAX
        if not index_long:
            rendu = _rendu_document(r, modele, modele.docs[index])
    compte = defaultdict(int)
    for d in modele.concepts():
        for s in sous:
            if d.chemin.startswith(s + "/"):
                compte[s] += 1
    chapeau, suite = None, None
    if not dossier:
        # La description d'un manifeste est écrite pour le routage d'un agent
        # (§ 3.3) : sa première phrase propre suffit en tête, le reste se déplie.
        noms = r.visibles_tries()
        communes = {regle["phrase"] for regle in _regles_communes(
            r, {n: r.hub.registry.get(n).manifest.description for n in noms})}
        description = modele.base.manifest.description
        propres = [x for x in _phrases(description) if x not in communes]
        chapeau = propres[0] if propres else description
        suite = description if len(_phrases(description)) > 1 else None
    dates = r.lecteur.dates_commit(modele.nom)
    # Un dossier de deux mille fiches faisait une page de 900 Ko, lente à afficher :
    # la liste se pagine, et l'Explorer filtre le dossier.
    docs = [d for d in docs if d.chemin != index]
    pages = max(1, -(-len(docs) // REPERTOIRE_PAR_PAGE))
    page = min(page_demandee(r.request.query_params), pages)
    total_docs = len(docs)
    docs = docs[(page - 1) * REPERTOIRE_PAR_PAGE: page * REPERTOIRE_PAR_PAGE]
    return r.page(
        "repertoire.html", page=page, pages=pages, total_docs=total_docs, par_page=REPERTOIRE_PAR_PAGE,
        index_long=index if index_long else None,
        base=modele.base.manifest,
        nom=modele.nom,
        dossier=dossier,
        titre_dossier=modele.docs[index].titre if dossier and index is not None else None,
        chapeau=chapeau, suite=suite,
        dates={d.chemin: r.lecteur.fraicheur(modele, d, dates).date for d in docs},
        fil=_fil(modele, dossier + "/" if dossier else ""),
        sous=[(s, posixpath.basename(s), compte[s]) for s in sous],
        docs=docs,
        rendu=rendu,
        panneau=_panneau(r, modele),
        en_attente=sum(1 for e in r.hub.propositions(modele.nom)[0].entries if e.status == "pending"),
    )


@dataclass
class _RenduDocument:
    html: object
    sommaire: tuple
    tronque: bool
    source: str


def _rendu_document(r: Requete, modele: BaseModele, doc: Doc) -> _RenduDocument:
    chemin = modele.base.resolve_document(doc.chemin)  # confinement (§ 5.3), en défense
    texte = chemin.read_text(encoding="utf-8", errors="replace")
    corps = parse_document(texte, loader=CHARGEUR).body
    ids = frozenset(str(s["id"]) for s in _sources(doc.frontmatter) if s.get("id"))
    rendu = rendre(
        corps,
        Contexte(modele.nom, doc.chemin, r.lien_vers, ids, r.image_vers, r.proposition_vers),
        titre=doc.titre,
        roles=modele.profil.sections,
    )
    return _RenduDocument(rendu.html, rendu.sommaire, rendu.tronque, corps if rendu.tronque else "")


def _sources(frontmatter) -> list[dict]:
    valeur = frontmatter.get("sources")
    if isinstance(valeur, dict):
        valeur = [valeur]
    if not isinstance(valeur, list):
        return []
    out = []
    for s in valeur:
        if not isinstance(s, dict):
            continue
        ressource = str(s.get("resource") or "")
        out.append({
            "id": str(s["id"]) if s.get("id") is not None else None,
            "ancre": ancre_de_source(str(s["id"])) if s.get("id") is not None else None,
            "titre": s.get("title"),
            "ressource": ressource,
            "url": ressource if ressource.startswith(("http://", "https://")) else None,
            "date": s.get("last_modified"),
        })
    return out


#: Champs affichés dans l'en-tête, donc retirés du frontmatter « complet ».
_CHAMPS_EN_TETE = ("title", "type", "description", "tags", "sources")


def _en_tete(r: Requete, modele: BaseModele, doc: Doc) -> dict:
    profil = modele.profil
    fm = doc.frontmatter
    montres = set(_CHAMPS_EN_TETE)

    provenance = None
    champ = profil.champ_du_role("provenance")
    if champ and fm.get(champ.nom) is not None:
        valeur = str(fm.get(champ.nom))
        declaree = champ.valeurs.get(valeur)
        preuve_nom = declaree.preuve if declaree else None
        provenance = {
            "valeur": valeur,
            "libelle": (declaree.libelle if declaree else None) or valeur,
            "hors_vocabulaire": bool(champ.valeurs) and declaree is None,
            "preuve_nom": preuve_nom,
            "preuve": fm.get(preuve_nom) if preuve_nom else None,
        }
        montres.add(champ.nom)
        if preuve_nom:
            montres.add(preuve_nom)

    def role(nom_role: str):
        c = profil.champ_du_role(nom_role)
        if c is None or fm.get(c.nom) in (None, "", []):
            return None
        montres.add(c.nom)
        return fm.get(c.nom)

    synonymes = role("aliases")
    if isinstance(synonymes, str):
        synonymes = [synonymes]

    facettes: dict[str, dict] = {}
    libres = []
    for tag in doc.tags:
        f = profil.facette_de(tag)
        if f is None:
            libres.append(tag)
            continue
        nom, valeur = f
        declaree = profil.facettes.get(nom)
        groupe = facettes.setdefault(nom, {"libelle": (declaree.libelle if declaree else None) or nom, "valeurs": []})
        groupe["valeurs"].append({
            "tag": tag,
            "valeur": valeur,
            "libelle": (declaree.valeurs.get(valeur) if declaree else None) or valeur,
            "hors_vocabulaire": bool(declaree and declaree.valeurs) and valeur not in declaree.valeurs,
        })

    autres = {k: v for k, v in fm.items() if k not in montres}
    return {
        "provenance": provenance,
        "synonymes": synonymes,
        "debut": role("start"),
        "fin": role("end"),
        "version": role("version"),
        "identifiant": role("identifier"),
        "facettes": list(facettes.values()),
        "tags_libres": libres,
        "confiance": confiance(fm),
        "type_declare": doc.type is None or not profil.types or doc.type in profil.types,
        "autres": yaml.safe_dump(dict(autres), allow_unicode=True, sort_keys=False, default_flow_style=False).strip() if autres else "",
    }


def _liens_sortants(r: Requete, modele: BaseModele, doc: Doc) -> dict:
    groupes: dict[str, list] = {"internes": [], "interbases": [], "externes": [], "casses": []}
    vus = set()
    for lien in doc.liens:
        cible = lien.cible
        if cible.sorte == ANCRE or cible.brut in vus:
            continue
        vus.add(cible.brut)
        rendu = r.lien_vers(cible)
        entree = {"brut": cible.brut, "href": rendu.href, "titre": rendu.titre or cible.brut, "base": cible.base}
        if cible.sorte == EXTERNE:
            groupes["externes"].append(entree)
        elif rendu.href is None:
            groupes["casses"].append({**entree, "motif": rendu.titre})
        elif cible.sorte == INTERBASE:
            groupes["interbases"].append(entree)
        else:
            groupes["internes"].append(entree)
    return groupes


def _liens_entrants(r: Requete, modele: BaseModele, doc: Doc) -> dict:
    """Liens entrants visibles du lecteur seulement (§ 5 bis.4) : sinon la page
    d'une base ouverte listerait des titres d'une base fermée.

    Les fiches sont comptées comme l'impact les compte (`Lecteur.dependants`) :
    sans la fiche elle-même, et sans les sommaires et journaux, qui citent sans
    dépendre — ils sont listés à part. Le même nombre partout sur la page."""
    par_source: dict[tuple[str, str], bool] = {}
    sommaires: set[tuple[str, str]] = set()
    liens = 0
    for e in r.lecteur.entrants(modele.nom, doc.chemin):
        if e.base not in r.visibles or (e.base, e.chemin) == (modele.nom, doc.chemin):
            continue
        liens += 1
        if posixpath.basename(e.chemin) in RESERVED_NAMES:
            sommaires.add((e.base, e.chemin))
            continue
        source = r.lecteur.modele(e.base)
        en_amont = source.profil.role_de_section(e.lien.section) == "upstream"
        par_source[(e.base, e.chemin)] = par_source.get((e.base, e.chemin), False) or en_amont
    declinee, citee = [], defaultdict(list)
    for (nom, chemin), en_amont in sorted(par_source.items()):
        entree = {"base": nom, "chemin": chemin, "titre": r.lecteur.modele(nom).docs[chemin].titre}
        (declinee.append(entree) if en_amont else citee[nom].append(entree))
    return {"declinee": declinee, "citee": sorted(citee.items()), "total": len(par_source), "liens": liens,
            "sommaires": [{"base": b, "chemin": c, "titre": r.lecteur.modele(b).docs[c].titre} for b, c in sorted(sommaires)]}


def _liste(valeur) -> list[str]:
    if isinstance(valeur, list):
        return [str(v) for v in valeur]
    return [str(valeur)] if valeur else []


def _genealogie(r: Requete, modele: BaseModele, doc: Doc) -> dict:
    """D'où vient ce que dit la fiche : les propositions qu'elle a intégrées,
    les commits qui l'ont touchée, et ce qui l'attend — les propositions en
    attente qui la nomment, non revues."""
    collecte, corps = r.hub.propositions(modele.nom)
    integrees = [e for e in collecte.entries
                 if e.status == "accepted" and doc.chemin in _liste(e.frontmatter.get("integrated-into"))]
    sans_extension = doc.chemin[:-3] if doc.chemin.endswith(".md") else doc.chemin
    marques = {doc.chemin, f"{modele.nom}:/{sans_extension}", f"/{sans_extension}"}
    if len(doc.titre) >= 12:
        marques.add(doc.titre.casefold())
    mentions = []
    for e in collecte.entries:
        if e.status != "pending":
            continue
        texte = corps.get(e.id, "")
        replie = texte.casefold()
        if any((m in texte) if m != doc.titre.casefold() else (m in replie) for m in marques):
            mentions.append(e)
    return {
        "integrees": integrees,
        "en_attente": mentions,
        "commits": r.lecteur.historique(modele.nom, HISTORIQUE_FICHE, chemin=doc.chemin),
    }


#: Commits montrés dans l'historique d'une fiche.
HISTORIQUE_FICHE = 12


def _document(r: Requete, modele: BaseModele, doc: Doc) -> Response:
    rendu = _rendu_document(r, modele, doc)
    fraicheur = r.lecteur.fraicheur(modele, doc)
    # La date déclarée dit quand le contenu a été confronté ; le commit, quand le
    # fichier a changé. Les deux répondent à des questions différentes.
    commit = r.lecteur.date_commit(modele.nom, doc.chemin) if fraicheur.origine != "commit" else None
    derives = [d for d in r.lecteur.derives(modele.nom).get(doc.chemin, ()) if d.base in r.visibles]
    impact = r.lecteur.dependants(modele.nom, doc.chemin, 3, r.visibles)
    mode_operatoire = None
    for niveau, texte, id_ancre in rendu.sommaire:
        if modele.profil.role_de_section(normalize_heading(texte)) == "procedure":
            mode_operatoire = id_ancre
            break
    return r.page(
        "document.html",
        nom=modele.nom,
        base=modele.base.manifest,
        doc=doc,
        fil=_fil(modele, doc.chemin),
        description=doc.frontmatter.get("description"),
        en_tete=_en_tete(r, modele, doc),
        fraicheur=fraicheur,
        commit=commit, entrants_par_base=ENTRANTS_PAR_BASE,
        rendu=rendu,
        mode_operatoire=mode_operatoire,
        sources=_sources(doc.frontmatter),
        sortants=_liens_sortants(r, modele, doc),
        entrants=_liens_entrants(r, modele, doc),
        derives=derives,
        titres_amont={(d.base, d.chemin): r.lecteur.modele(d.base).docs[d.chemin].titre for d in derives},
        impact=[len(degre) for degre in impact],
        genealogie=_genealogie(r, modele, doc),
        sans_type=SANS_TYPE,
    )


def _groupes(trouves, vue: Vue, profil) -> list[tuple[str, list]]:
    if vue.groupe is None:
        return [("", trouves)]
    sorte, nom = vue.groupe
    groupes: dict[str, list] = defaultdict(list)
    for t in trouves:
        if sorte == "type":
            cles = [t.doc.type or SANS_TYPE]
        elif sorte == "facet":
            cles = [f[1] for tag in t.doc.tags if (f := profil.facette_de(tag)) and f[0] == nom] or ["(sans)"]
        else:
            valeur = t.doc.frontmatter.get(nom)
            cles = [str(v) for v in valeur] if isinstance(valeur, list) else [str(valeur) if valeur is not None else "(sans)"]
        for cle in dict.fromkeys(cles):
            groupes[cle].append(t)
    return sorted(groupes.items())


def vue(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    n = r.request.path_params["n"]
    if not 0 <= n < len(modele.profil.vues):
        raise Introuvable(n)
    declaree = modele.profil.vues[n]
    filtre = dict(declaree.filtre)
    inconnues = sorted(set(filtre) - set(CLES_CRITERES))
    erreur = None
    try:
        criteres = lire_criteres({k: filtre.get(k) for k in CLES_CRITERES})
        trouves = filtrer(r.lecteur, modele, criteres, cible=explorer._cible(r, [modele.nom], criteres), bases=r.visibles)
    except ToolError as exc:
        criteres, trouves, erreur = Criteres(), [], exc.message
    contexte = {"groupes": [], "chronologie": [], "graphe": [], "fraicheur": [], "sans_date": [],
                "figure": None, "etiquettes": {}}
    profil = modele.profil
    if declaree.sorte == "list":
        contexte["groupes"] = _groupes(trouves, declaree, profil)
    elif declaree.sorte == "timeline":
        debut = profil.champ_du_role("start")
        fin = profil.champ_du_role("end")
        if debut is None:
            erreur = erreur or "la base ne déclare aucun champ de rôle « start »"
        else:
            datees = []
            for t in trouves:
                d = instant(t.doc.frontmatter.get(debut.nom))
                if d is None:
                    contexte["sans_date"].append(t)
                    continue
                f = instant(t.doc.frontmatter.get(fin.nom)) if fin else None
                datees.append((d, f, t))
            datees.sort(key=lambda x: (x[0], x[2].doc.chemin), reverse=True)
            par_mois: dict[str, list] = defaultdict(list)
            for d, f, t in datees:
                par_mois[d.strftime("%Y-%m")].append((d, f, t))
            contexte["chronologie"] = sorted(par_mois.items(), reverse=True)
    elif declaree.sorte == "graph":
        dans = {t.doc.chemin for t in trouves}
        modeles = {modele.nom: modele}
        for t in trouves:
            voisins = []
            for lien in t.doc.liens:
                if lien.cible.sorte == INTERNE:
                    res = resoudre(lien.cible, modeles)
                    if res.sorte == DOC and res.chemin in dans and res.chemin != t.doc.chemin:
                        voisins.append(modele.docs[res.chemin])
            contexte["graphe"].append((t, list({d.chemin: d for d in voisins}.values())))
        if 0 < len(trouves) <= GRAPHE_FIGURE_MAX:
            # L'ordre des chemins regroupe les documents par dossier : on le garde.
            contexte["figure"] = disposer(
                [t.doc.chemin for t in trouves],
                [(t.doc.chemin, v.chemin, 1, None) for t, voisins in contexte["graphe"] for v in voisins],
                rayon_noeud=8, ordonne=False,
            )
            contexte["etiquettes"] = {
                c: posixpath.splitext(posixpath.basename(c))[0][:24] for c in (t.doc.chemin for t in trouves)
            }
    elif declaree.sorte == "freshness":
        champ = profil.champ_du_role("provenance")
        a_preuve = {v for v, d in (champ.valeurs.items() if champ else []) if d.preuve}
        dates = r.lecteur.dates_commit(modele.nom)
        for t in trouves:
            # Une base qui déclare des preuves ne re-vérifie que ce qui en a une :
            # le reste n'a pas de source à confronter (§ 5 bis.1).
            if a_preuve and str(t.doc.frontmatter.get(champ.nom)) not in a_preuve:
                continue
            f = t.fraicheur or r.lecteur.fraicheur(modele, t.doc, dates)
            (contexte["fraicheur"] if f.origine in ("verified", "generated", "sources") or not a_preuve
             else contexte["sans_date"]).append((t, f))
        contexte["fraicheur"].sort(key=lambda x: (x[1].date, x[0].doc.chemin))
    return r.page(
        "vue.html",
        nom=modele.nom,
        base=modele.base.manifest,
        vue=declaree,
        criteres=criteres,
        total=len(trouves),
        erreur=erreur,
        inconnues=inconnues,
        panneau=_panneau(r, modele),
        maintenant=dt.datetime.now(dt.timezone.utc),
        **contexte,
    )


def _fils(entrees: list, rel: dict, nom: str | None = None) -> list[tuple[object, str, int, list]]:
    """(base, entrée, profondeur, relations sortantes) : une proposition qui en
    corrige, suit ou annule une autre de la liste se range sous elle, indentée."""
    entrees = list(entrees)
    presents = {(b, e.id) for b, e in entrees}
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    for b, e in entrees:
        for rr in rel["sortantes"].get((b, e.id), []):
            if rr.nature in ("corrige", "suite de", "annule") and (rr.base, rr.id) in presents:
                parent.setdefault((b, e.id), (rr.base, rr.id))
                break
    enfants: dict[tuple[str, str], list] = defaultdict(list)
    for b, e in entrees:
        if (b, e.id) in parent:
            enfants[parent[(b, e.id)]].append((b, e))
    ordre = []

    def poser(b, e, profondeur):
        ordre.append((b, e, profondeur, rel["sortantes"].get((b, e.id), [])))
        for fb, fe in enfants.get((b, e.id), []):
            poser(fb, fe, profondeur + 1)

    for b, e in entrees:
        if (b, e.id) not in parent:
            poser(b, e, 0)
    return ordre


def _tri_des_propositions(r: Requete, defaut: str) -> str:
    tri = r.request.query_params.get("ordre") or defaut
    if tri not in ("anciennes", "recentes"):
        r.avis.append(f"ordre « {tri} » inconnu (anciennes ou recentes) : {defaut}")
        tri = defaut
    return tri


def propositions(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    statut = r.request.query_params.get("statut")
    if statut and statut not in STATUSES:
        r.avis.append(f"statut « {statut} » inconnu ({', '.join(STATUSES)}) : toutes les propositions sont montrées")
    statut = statut if statut in STATUSES else None
    # En attente : le travail, les plus anciennes d'abord ; sinon l'histoire, les plus récentes.
    ordre = _tri_des_propositions(r, "anciennes" if statut == "pending" else "recentes")
    collecte, _ = r.hub.propositions(modele.nom)
    comptes = Counter(e.status for e in collecte.entries)
    entrees = [e for e in collecte.entries if statut is None or e.status == statut]
    entrees.sort(key=lambda e: (e.submitted_at or "", e.id), reverse=(ordre == "recentes"))
    pages = max(1, -(-len(entrees) // PROPOSITIONS_PAR_PAGE))
    page = min(page_demandee(r.request.query_params), pages)
    tranche = entrees[(page - 1) * PROPOSITIONS_PAR_PAGE : page * PROPOSITIONS_PAR_PAGE]
    rel = revue.relations(r, r.visibles_tries())
    return r.page(
        "propositions.html", nom=modele.nom, base=modele.base.manifest,
        lignes=_fils([(modele.nom, e) for e in tranche], rel),
        total=len(entrees), comptes=comptes, page=page, pages=pages, ordre=ordre,
        par_page=PROPOSITIONS_PAR_PAGE, illisibles=collecte.unreadable, statut=statut,
        statuts=STATUSES, liste=_liste,
    )


def proposition(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    ident = r.request.path_params["id"]
    collecte, textes = r.hub.propositions(modele.nom)
    for entree in collecte.entries:
        if entree.id == ident:
            break
    else:
        raise Introuvable(ident)
    texte = textes.get(ident, "")
    corps = parse_document(texte, loader=CHARGEUR).body
    # Une proposition est une donnée non fiable, rendue sous la même politique
    # que le corpus ; ses liens se lisent depuis la racine du corpus.
    rendu = rendre(corps, Contexte(modele.nom, "", r.lien_vers, image_vers=r.image_vers,
                                   proposition_vers=r.proposition_vers))
    rel = revue.relations(r, r.visibles_tries())
    visees = revue.fiches_visees(r, modele.nom, entree, texte)
    ordre = revue.ordre_de_revue(r)
    rang = next((i for i, (b, e) in enumerate(ordre) if b == modele.nom and e.id == ident), None)
    return r.page(
        "proposition.html", nom=modele.nom, base=modele.base.manifest, entree=entree,
        rendu=rendu, liste=_liste,
        sortantes=rel["sortantes"].get((modele.nom, ident), []),
        entrantes=rel["entrantes"].get((modele.nom, ident), []),
        statuts_de={(b, e.id): e.status for b in r.visibles_tries() for e in r.hub.propositions(b)[0].entries},
        visees=visees,
        proches=revue.fiches_proches(r, modele.nom, entree, {cle for cle, _ in visees}),
        precedente=ordre[rang - 1] if rang else None,
        suivante=ordre[rang + 1] if rang is not None and rang + 1 < len(ordre) else None,
        rang=rang, en_attente=len(ordre),
        segments=lambda texte: revue.segments(r, modele.nom, str(texte)),
    )


def journal(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    log = modele.docs.get("log.md")
    rendu = _rendu_document(r, modele, log) if log else None
    return r.page(
        "journal.html", nom=modele.nom, base=modele.base.manifest, rendu=rendu,
        commits=[(c, pages.decrire_commit(c.sujet)[0], pages.sujet_lisible(c.sujet))
                 for c in r.lecteur.historique(modele.nom, HISTORIQUE)],
        historique=HISTORIQUE, segments=lambda texte: revue.segments(r, modele.nom, str(texte)),
    )


#: Au-delà, la vue graphe d'une base n'est plus qu'une liste d'adjacence : une
#: figure en cercle de cent nœuds ne se lit plus.
GRAPHE_FIGURE_MAX = 40


def _non_declarees(r: Requete, analyse) -> dict | None:
    """Références qu'aucune déclaration ne couvre : celles des bases qui ne
    déclarent pas `cites`. Le § 3.3 ne les signale pas base par base — la
    déclaration est volontaire — mais leur somme dit ce que rien ne contrôle,
    et les références observées donnent la déclaration à écrire."""
    sans_cites = {b for b in analyse.bases if r.hub.registry.get(b).manifest.cites is None}
    aretes = [a for a in analyse.aretes if a.source in sans_cites and a.references]
    if not aretes:
        return None
    proposees: dict[str, list[str]] = defaultdict(list)
    for a in aretes:
        proposees[a.source].append(a.cible)
    deja = {b: r.hub.registry.get(b).manifest.cites or () for b in analyse.bases if b not in sans_cites}
    return {
        "references": sum(a.references for a in aretes),
        "relations": len(aretes),
        "bases": len(proposees),
        "toutes": len(sans_cites) == len(analyse.bases),
        "declarations": sorted((b, sorted(c)) for b, c in proposees.items()),
        # Déclarer ce qui est observé ferait-il apparaître un cycle ?
        "cycles": cycles({**deja, **{b: tuple(c) for b, c in proposees.items()}}),
    }


def graphe(r: Requete) -> Response:
    analyse = analyser(r.lecteur, r.visibles_tries())
    # Surface du nœud proportionnelle au logarithme du nombre de documents : une
    # base de 2 500 fiches ne doit pas écraser celle de six.
    tailles = {nom: len(r.lecteur.modele(nom).concepts()) for nom in analyse.bases}
    rayons = {nom: round(16 + 6 * math.log10(1 + n), 1) for nom, n in tailles.items()}
    figure = disposer(list(analyse.bases), [(a.source, a.cible, a.references, a) for a in analyse.aretes], rayons=rayons)
    # Seules les contradictions sont colorées : une référence hors des `cites`
    # qu'une base déclare, ou un cycle de déclarations. Une référence observée
    # chez une base qui ne déclare rien n'en est pas une.
    contradictions = {(b, c) for b, c, _ in analyse.hors_cites} | {
        (cycle[i], cycle[(i + 1) % len(cycle)]) for cycle in analyse.cycles for i in range(len(cycle))}
    titres = {nom: r.hub.registry.get(nom).manifest.title for nom in analyse.bases}
    return r.page(
        "graphe.html", analyse=analyse, figure=figure, titres=titres, tailles=tailles,
        contradictions=contradictions, indices={n: i for i, n in enumerate(analyse.bases)},
        legende_epaisseurs=[(n, epaisseur_de(n)) for n in (10, 100, 500)],
        legende_rayons=[(n, round(16 + 6 * math.log10(1 + n), 1)) for n in (10, 100, 1000)],
        non_declarees=_non_declarees(r, analyse),
    )