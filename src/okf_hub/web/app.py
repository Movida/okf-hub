"""Interface web de consultation, en lecture seule (§ 5 bis.2, rév. 4.5).

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
| `/b/<base>/` | Une base : son `index.md`, ses vues, ses effectifs |
| `/b/<base>/<chemin>.md` | Un document |
| `/b/<base>/<dossier>/` | Un répertoire du corpus |
| `/b/<base>/-/trouver`, `/-/vues/<n>`, `/-/propositions[/<id>]`, `/-/journal` | Pages d'une base |
| `/trouver`, `/recherche`, `/graphe` | Pages du hub |

Le segment `-` sépare les pages d'une base de son corpus : un dossier de
corpus nommé `propositions/` reste ainsi adressable.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlencode

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import governance, hublog
from ..errors import INVALID_INPUT, NOT_FOUND, UNKNOWN_BASE, ToolError
from ..lecture.confiance import confiance, instant
from ..lecture.graphe import analyser, cycles
from ..lecture.liens import ANCRE, EXTERNE, HORS_CORPUS, INTERBASE, INTERNE, Cible, classer
from ..lecture.modele import (
    CHARGEUR,
    DOC,
    REP,
    BaseModele,
    Doc,
    Entrant,
    Lecteur,
    cible_document,
    resoudre,
)
from ..lecture.profil import Vue
from ..lecture.rendu import Lien, Contexte, ancre, ancre_de_source, rendre
from ..lecture.requete import (
    CLES_CRITERES,
    SANS_TYPE,
    Criteres,
    compter,
    filtrer,
    lire_criteres,
)
from ..mdutil import (
    PREAMBLE_LABEL,
    heading_at_line,
    normalize_heading,
    parse_document,
    parse_headings,
)
from ..registry import Registry
from ..search import run_search, split_terms
from ..tools.proposal_status_tool import STATUSES, collect
from ..tools.search_tool import CONTEXT_LINES, MAX_EXCERPTS_PER_DOC
from .acces import AccesRefuse, Visiteur, WebConfig, bases_visibles, visiteur_de
from .figure import disposer

ICI = Path(__file__).resolve().parent

#: Politique de contenu : rien d'extérieur, aucun script, aucun cadre. Le corps
#: des documents vient en partie de propositions d'agents (§ 8).
CSP = (
    "default-src 'self'; script-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)

#: Fichiers du corpus servis tels quels : des images, rien d'autre. Un SVG peut
#: porter du script ; il est servi sous une politique `sandbox` qui l'empêche de
#: s'exécuter, même ouvert seul dans un onglet.
TYPES_IMAGES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".avif": "image/avif", ".svg": "image/svg+xml",
}
CSP_SVG = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"

#: Propositions par page : une base en accumule des centaines.
PROPOSITIONS_PAR_PAGE = 50

#: Valeurs proposées au plus par champ du formulaire Trouver.
SUGGESTIONS_MAX = 80

#: Documents listés au plus par base sur une page de résultats.
LISTE_MAX = 500
#: Recherche : documents par page quand une base est choisie ; aperçu par base
#: quand plusieurs sont interrogées, avec un lien vers la liste complète.
RECHERCHE_PAR_PAGE = 25
RECHERCHE_APERCU = 8
RECHERCHE_TOUT = 100_000
HISTORIQUE = 30


class Introuvable(Exception):
    """404 : base inconnue, non visible, ou chemin absent — indiscernables."""


# --- application ----------------------------------------------------------------


@dataclass
class Hub:
    registry: Registry
    config: WebConfig
    rafraichir: Callable[[], object] = lambda: None
    """Re-scan de la découverte, sous cooldown (`HubServer._silent_rescan("web")`)."""
    lecteur: Lecteur = field(init=False)

    def __post_init__(self) -> None:
        self.lecteur = Lecteur.de(self.registry)


class _EnTetesDeSecurite:
    """Pose la politique de contenu et ses compagnes sur toute réponse."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def envoyer(message) -> None:
            if message["type"] == "http.response.start":
                en_tetes = MutableHeaders(scope=message)
                # Une réponse qui pose sa propre politique la garde : celle d'un
                # SVG est plus stricte que celle des pages.
                en_tetes.setdefault("Content-Security-Policy", CSP)
                en_tetes["X-Content-Type-Options"] = "nosniff"
                en_tetes["Referrer-Policy"] = "same-origin"
                en_tetes["X-Frame-Options"] = "DENY"
            await send(message)

        await self.app(scope, receive, envoyer)


def creer_app(hub: Hub) -> Starlette:
    routes = [
        Route("/", _page(accueil)),
        Route("/trouver", _page(trouver)),
        Route("/recherche", _page(recherche)),
        Route("/graphe", _page(graphe)),
        Mount("/static", StaticFiles(directory=ICI / "static"), name="static"),
        Route("/b/{base}", _page(base_sans_barre)),
        Route("/b/{base}/", _page(base_accueil)),
        Route("/b/{base}/-/trouver", _page(trouver)),
        Route("/b/{base}/-/vues/{n:int}", _page(vue)),
        Route("/b/{base}/-/propositions", _page(propositions)),
        Route("/b/{base}/-/propositions/{id}", _page(proposition)),
        Route("/b/{base}/-/journal", _page(journal)),
        Route("/b/{base}/{chemin:path}", _page(document_ou_repertoire)),
    ]
    app = Starlette(routes=routes, exception_handlers={404: _hors_route})
    app.state.hub = hub
    app.add_middleware(_EnTetesDeSecurite)
    return app


def _hors_route(request: Request, exc) -> Response:
    """Adresse qui ne correspond à aucune route : la même page que tout 404,
    avec les mêmes suggestions — sous le même contrôle d'accès."""
    requete = _requete(request)
    return requete if isinstance(requete, Response) else _introuvable(requete)


# --- gabarits -------------------------------------------------------------------


def url_base(nom: str) -> str:
    return f"/b/{nom}/"


def url_doc(nom: str, chemin: str, fragment: str | None = None) -> str:
    return f"/b/{nom}/{quote(chemin)}" + (f"#{quote(fragment)}" if fragment else "")


def url_rep(nom: str, chemin: str) -> str:
    return f"/b/{nom}/{quote(chemin)}/" if chemin else url_base(nom)


def url_page(nom: str | None, quelle: str, /, **params) -> str:
    """Une page d'une base (`/b/<nom>/-/<quelle>`) ou du hub (`/<quelle>`) ;
    `params` devient la chaîne de requête — `page` compris, d'où les deux
    premiers paramètres positionnels seulement."""
    racine = f"/b/{nom}/-/{quelle}" if nom else f"/{quelle}"
    params = {k: v for k, v in params.items() if v not in (None, "", [])}
    return racine + ("?" + urlencode(params, doseq=True) if params else "")


def _date(valeur) -> str:
    if isinstance(valeur, dt.datetime):
        return valeur.astimezone(dt.timezone.utc).date().isoformat()
    if isinstance(valeur, dt.date):
        return valeur.isoformat()
    lu = instant(valeur)
    return lu.date().isoformat() if lu else str(valeur or "")


_ENV = Environment(
    loader=FileSystemLoader(ICI / "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENV.globals.update(url_base=url_base, url_doc=url_doc, url_rep=url_rep, url_page=url_page)
_ENV.filters["date"] = _date


# --- requête ---------------------------------------------------------------------


@dataclass
class Requete:
    request: Request
    hub: Hub
    visiteur: Visiteur
    visibles: frozenset[str]

    @property
    def lecteur(self) -> Lecteur:
        return self.hub.lecteur

    def nom_de_base(self) -> str:
        nom = self.request.path_params.get("base", "")
        if nom not in self.visibles:
            raise Introuvable(nom)
        return nom

    def modele(self, nom: str) -> BaseModele:
        if nom not in self.visibles:
            raise Introuvable(nom)
        return self.lecteur.modele(nom)

    def visibles_tries(self) -> list[str]:
        return sorted(self.visibles)

    def page(self, gabarit: str, code: int = 200, **contexte) -> HTMLResponse:
        html = _ENV.get_template(gabarit).render(
            visiteur=self.visiteur, equipe=self.hub.config.equipe, **contexte
        )
        return HTMLResponse(html, status_code=code)

    def lien_vers(self, cible: Cible) -> Lien:
        """Ce que devient une cible de lien dans une page (`rendu.Contexte`)."""
        if cible.sorte == EXTERNE:
            return Lien(cible.brut.strip().strip("<>"), "externe")
        if cible.sorte == ANCRE:
            return Lien("#" + quote(cible.ancre or ""), "ancre")
        if cible.sorte == HORS_CORPUS:
            return Lien(None, "casse", f"lien hors du corpus : {cible.brut}")
        if cible.sorte not in (INTERNE, INTERBASE):
            return Lien(None, "refuse", f"lien non suivi : {cible.brut}")
        if cible.base not in self.visibles:
            if not self.hub.config.equipe and cible.base not in self.hub.registry.names():
                return Lien(None, "casse", f"base « {cible.base} » absente de ce hub")
            # En équipe, une base fermée et une base absente sont indiscernables.
            return Lien(None, "masque", "lien vers une base qui ne vous est pas ouverte")
        modeles = self.lecteur.modeles([cible.base])
        resolution = resoudre(cible, modeles)
        classe = "interbase" if cible.sorte == INTERBASE else "interne"
        if resolution.sorte == DOC:
            doc = modeles[cible.base].docs[resolution.chemin]
            return Lien(url_doc(cible.base, resolution.chemin, resolution.ancre), classe, doc.titre)
        if resolution.sorte == REP:
            return Lien(url_rep(cible.base, resolution.chemin or ""), classe + " repertoire")
        return Lien(None, "casse", f"lien cassé : {cible.brut}")

    def image_vers(self, cible: Cible) -> str | None:
        """Adresse d'une image du corpus d'une base visible, ou None."""
        if cible.sorte not in (INTERNE, INTERBASE) or cible.base not in self.visibles or cible.repertoire:
            return None
        if chemin_d_image(self.lecteur.modele(cible.base), cible.chemin or "") is None:
            return None
        return url_doc(cible.base, cible.chemin or "")


def _requete(request: Request) -> Requete | Response:
    """La requête et ce que son visiteur peut lire, ou le refus du mode équipe."""
    hub: Hub = request.app.state.hub
    adresse = request.client.host if request.client else None
    try:
        visiteur = visiteur_de(hub.config, request.headers, adresse)
    except AccesRefuse as exc:
        requete = Requete(request, hub, Visiteur(None), frozenset())
        return requete.page("erreur.html", 403, titre="Accès refusé", message=str(exc))
    return Requete(request, hub, visiteur, bases_visibles(hub.config, visiteur, hub.registry.names()))


def _introuvable(r: Requete) -> Response:
    """404, avec la bonne adresse quand l'URL nomme une base ouverte au visiteur
    (`/base/x`, `/x` : le schéma `/b/<base>/` ne se devine pas). Une base
    fermée n'est jamais suggérée : rien ne la distingue d'une base absente."""
    segments = {s for s in r.request.url.path.split("/") if s}
    proches = [b for b in r.visibles_tries() if b in segments]
    return r.page(
        "erreur.html", 404, titre="Introuvable",
        message="Cette page n'existe pas, ou ne vous est pas ouverte.",
        proches=proches, bases=r.visibles_tries(),
    )


def _page(fonction: Callable[[Requete], Response]) -> Callable[[Request], Response]:
    def point_d_entree(request: Request) -> Response:
        request.app.state.hub.rafraichir()
        requete = _requete(request)
        if isinstance(requete, Response):
            return requete
        try:
            return fonction(requete)
        except Introuvable:
            return _introuvable(requete)
        except ToolError as exc:
            if exc.code in (UNKNOWN_BASE, NOT_FOUND):
                return _introuvable(requete)
            if exc.code == INVALID_INPUT:
                return requete.page("erreur.html", 400, titre="Requête invalide", message=exc.message)
            hublog.error(f"web : {request.url.path} — {exc.code}: {exc.message}")
            return requete.page("erreur.html", 500, titre="Erreur", message=exc.message)

    point_d_entree.__name__ = fonction.__name__
    return point_d_entree


# --- accueil -----------------------------------------------------------------------


def _derniere_entree_du_journal(modele: BaseModele) -> str | None:
    log = modele.docs.get("log.md")
    if log is None:
        return None
    for h in log.sections:
        if h.level == 2:
            return h.text
    return None


def _resume_base(r: Requete, nom: str) -> dict:
    modele = r.modele(nom)
    base = modele.base
    en_attente = collect(base, ("pending",), loader=CHARGEUR).entries
    dates = sorted(e.submitted_at for e in en_attente if e.submitted_at)
    return {
        "nom": nom,
        "titre": base.manifest.title,
        "description": base.manifest.description,
        "documents": len(modele.docs),
        "en_attente": len(en_attente),
        "plus_ancienne": dates[0] if dates else None,
        "gouvernance": governance.status_of_file(base.manifest.governance_rules),
        "journal": _derniere_entree_du_journal(modele),
    }


def accueil(r: Requete) -> Response:
    return r.page("accueil.html", bases=[_resume_base(r, n) for n in r.visibles_tries()])


# --- base, répertoire, document ---------------------------------------------------


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


def chemin_d_image(modele: BaseModele, chemin: str) -> Path | None:
    """Fichier image du corpus désigné par `chemin`, confiné comme un document (§ 5.3)."""
    extension = posixpath.splitext(chemin)[1].lower()
    if extension not in TYPES_IMAGES:
        return None
    base = modele.base
    candidat = (base.corpus_dir / chemin).resolve()
    if base.corpus_dir not in candidat.parents or base.is_excluded(candidat, is_dir=False):
        return None
    return candidat if candidat.is_file() else None


def _image(modele: BaseModele, chemin: str) -> Response:
    fichier = chemin_d_image(modele, chemin)
    if fichier is None:
        raise Introuvable(chemin)
    reponse = FileResponse(fichier, media_type=TYPES_IMAGES[fichier.suffix.lower()])
    if fichier.suffix.lower() == ".svg":
        reponse.headers["Content-Security-Policy"] = CSP_SVG
    return reponse


def _fil(nom: str, chemin: str) -> list[tuple[str, str]]:
    """(libellé, adresse) de la base jusqu'au dossier qui contient `chemin`."""
    fil = [(nom, url_base(nom))]
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
    }


def _repertoire(r: Requete, modele: BaseModele, dossier: str) -> Response:
    sous, docs = modele.contenu(dossier)
    index = modele.index_de(dossier)
    rendu = None
    if index is not None:
        rendu = _rendu_document(r, modele, modele.docs[index])
    compte = defaultdict(int)
    for d in modele.docs.values():
        for s in sous:
            if d.chemin.startswith(s + "/"):
                compte[s] += 1
    return r.page(
        "repertoire.html",
        base=modele.base.manifest,
        nom=modele.nom,
        dossier=dossier,
        fil=_fil(modele.nom, dossier + "/" if dossier else ""),
        sous=[(s, posixpath.basename(s), compte[s]) for s in sous],
        docs=[d for d in docs if d.chemin != index],
        rendu=rendu,
        panneau=_panneau(r, modele),
        en_attente=len(collect(modele.base, ("pending",), loader=CHARGEUR).entries),
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
        Contexte(modele.nom, doc.chemin, r.lien_vers, ids, r.image_vers),
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
    d'une base ouverte listerait des titres d'une base fermée."""
    par_source: dict[tuple[str, str], bool] = {}
    for e in r.lecteur.entrants(modele.nom, doc.chemin):
        if e.base not in r.visibles:
            continue
        source = r.lecteur.modele(e.base)
        en_amont = source.profil.role_de_section(e.lien.section) == "upstream"
        par_source[(e.base, e.chemin)] = par_source.get((e.base, e.chemin), False) or en_amont
    declinee, citee = [], defaultdict(list)
    for (nom, chemin), en_amont in sorted(par_source.items()):
        entree = {"base": nom, "chemin": chemin, "titre": r.lecteur.modele(nom).docs[chemin].titre}
        (declinee.append(entree) if en_amont else citee[nom].append(entree))
    return {"declinee": declinee, "citee": sorted(citee.items()), "total": len(par_source)}


def _document(r: Requete, modele: BaseModele, doc: Doc) -> Response:
    rendu = _rendu_document(r, modele, doc)
    fraicheur = r.lecteur.fraicheur(modele, doc)
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
        fil=_fil(modele.nom, doc.chemin),
        description=doc.frontmatter.get("description"),
        en_tete=_en_tete(r, modele, doc),
        fraicheur=fraicheur,
        rendu=rendu,
        mode_operatoire=mode_operatoire,
        sources=_sources(doc.frontmatter),
        sortants=_liens_sortants(r, modele, doc),
        entrants=_liens_entrants(r, modele, doc),
        sans_type=SANS_TYPE,
    )


# --- trouver --------------------------------------------------------------------


#: Paramètres du formulaire → paramètres de `kb_find` : une seule validation.
def _criteres_du_formulaire(params) -> Criteres:
    champs: dict[str, str | bool] = {}
    for brut in params.getlist("champ"):
        brut = brut.strip()
        if not brut:
            continue
        if brut.startswith("!"):
            champs[brut[1:].strip()] = False
        elif "=" in brut:
            nom, _, valeur = brut.partition("=")
            champs[nom.strip()] = valeur.strip()
        else:
            champs[brut] = True
    mois = params.get("mois") or None
    if mois is not None:
        try:
            mois = int(mois)
        except ValueError:
            raise ToolError(INVALID_INPUT, "ancienneté : un nombre de mois est attendu")
    return lire_criteres({
        "type": params.get("type") or None,
        "tags": [t for t in params.getlist("tag") if t.strip()],
        "fields": champs,
        "section": params.get("section") or None,
        "without_section": params.get("sans_section") or None,
        "under": params.get("sous") or None,
        "links_to": params.get("liant") or None,
        "older_than_months": mois,
    })


def _entrants_de(r: Requete, noms: list[str], criteres: Criteres) -> tuple[Entrant, ...] | None:
    """Liens qui arrivent sur la cible de `links_to`, ou None sans ce critère."""
    if not criteres.links_to:
        return None
    cible = classer(criteres.links_to, noms[0] if len(noms) == 1 else "", "")
    if cible.sorte not in (INTERNE, INTERBASE) or not cible.base or cible.base not in r.visibles:
        raise ToolError(INVALID_INPUT, f"« liant » : référence illisible ou base non ouverte — {criteres.links_to}")
    modeles = r.lecteur.modeles([cible.base])
    trouve = cible_document(resoudre(cible, modeles), modeles)
    if trouve is None:
        raise ToolError(INVALID_INPUT, f"« liant » : aucun document {criteres.links_to}")
    return r.lecteur.entrants(*trouve)


def _executer(r: Requete, noms: list[str], criteres: Criteres) -> list[dict]:
    entrants = _entrants_de(r, noms, criteres)
    resultats = []
    for nom in noms:
        modele = r.modele(nom)
        trouves = filtrer(r.lecteur, modele, criteres, entrants=entrants)
        docs = [t.doc for t in trouves]
        facettes = []
        _, types = compter(docs, "type", modele.profil)
        for nom_facette, facette in modele.profil.facettes.items():
            if modele.profil.separateur:
                _, effectifs = compter(docs, nom_facette, modele.profil)
                facettes.append((facette.libelle or nom_facette, nom_facette, effectifs))
        resultats.append({
            "nom": nom,
            "total": len(trouves),
            "trouves": trouves[:LISTE_MAX],
            "types": types,
            "facettes": facettes,
            "separateur": modele.profil.separateur,
        })
    return resultats


def _suggestions(r: Requete, noms: list[str]) -> dict:
    """Valeurs réellement présentes, proposées par les champs du formulaire
    (`<datalist>` : une saisie assistée sans script)."""
    types, tags, sections = Counter(), Counter(), Counter()
    dossiers: set[str] = set()
    roles: set[str] = set()
    for nom in noms:
        modele = r.modele(nom)
        for d in modele.concepts():
            if d.type:
                types[d.type] += 1
            tags.update(d.tags)
            sections.update({h.text for h in d.sections if len(h.text) <= 60})
        dossiers.update(x for x in modele.repertoires if x)
        roles.update(modele.profil.sections.values())
    return {
        "types": types,
        "tags": [t for t, _ in tags.most_common(SUGGESTIONS_MAX)],
        "sections": sorted(roles) + [t for t, _ in sections.most_common(SUGGESTIONS_MAX)],
        "dossiers": sorted(dossiers)[: SUGGESTIONS_MAX * 2],
        "roles": roles,
    }


def _exemples(r: Requete, noms: list[str], suggestions: dict) -> list[tuple[str, dict]]:
    """Quelques requêtes cliquables, tirées des données : elles montrent ce que
    chaque critère fait mieux qu'une explication."""
    exemples: list[tuple[str, dict]] = []
    types = suggestions["types"]
    if types:
        (t, n), = types.most_common(1)
        exemples.append((f"Les {n} documents de type « {t} »", {"type": t}))
    if "warnings" in suggestions["roles"]:
        exemples.append(("Ceux qui ont une section de pièges (rôle warnings)", {"section": "warnings"}))
        if types:
            exemples.append((f"Les « {t} » sans section de pièges", {"type": t, "sans_section": "warnings"}))
    elif suggestions["sections"]:
        s = suggestions["sections"][0]
        exemples.append((f"Ceux qui ont une section « {s} »", {"section": s}))
    exemples.append(("Pas mis à jour depuis plus de 12 mois", {"mois": 12}))
    dans = set(noms)
    cites = [
        (len({(e.base, e.chemin) for e in liens if e.base in r.visibles}), cle)
        for cle, liens in r.lecteur.index_entrants().items()
        if cle[0] in dans and posixpath.basename(cle[1]) not in ("index.md", "log.md")
    ]
    if cites:
        n, (base, chemin) = max(cites, key=lambda x: (x[0], x[1]))
        if n:
            titre = r.lecteur.modele(base).docs[chemin].titre
            exemples.append((f"Les {n} documents qui citent « {titre} »", {"liant": f"{base}:/{chemin}"}))
    return exemples


def trouver(r: Requete) -> Response:
    une_base = r.request.path_params.get("base")
    if une_base is not None:
        noms = [r.nom_de_base()]
    else:
        demandees = [b for b in r.request.query_params.getlist("base") if b]
        noms = [b for b in demandees if b in r.visibles] or r.visibles_tries()
    params = r.request.query_params
    criteres = _criteres_du_formulaire(params)
    soumis = any(params.get(k) for k in ("type", "tag", "champ", "section", "sans_section", "sous", "liant", "mois"))
    resultats = _executer(r, noms, criteres) if (soumis or une_base) else []
    suggestions = _suggestions(r, noms)
    return r.page(
        "trouver.html",
        suggestions=suggestions,
        exemples=_exemples(r, noms, suggestions),
        nom=une_base,
        base=r.modele(une_base).base.manifest if une_base else None,
        noms=noms,
        toutes=r.visibles_tries(),
        params=params,
        criteres=criteres,
        resultats=resultats,
        soumis=soumis or bool(une_base),
        liste_max=LISTE_MAX,
        sans_type=SANS_TYPE,
    )


# --- vues déclarées ---------------------------------------------------------------


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
        trouves = filtrer(r.lecteur, modele, criteres, entrants=_entrants_de(r, [modele.nom], criteres))
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


# --- recherche ---------------------------------------------------------------------


_CLE_FM = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")
_LIEN_MD = re.compile(r"!?\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")
_MARQUES_MD = re.compile(r"\*\*|__|`|~~")
_TITRE_MD = re.compile(r"^\s{0,3}#{1,6}\s+")
_PUCE_MD = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+")
_CLOTURE_MD = re.compile(r"^\s{0,3}(```|~~~)")


def _texte_simple(ligne: str) -> str | None:
    """La ligne débarrassée de sa syntaxe markdown — titres, liens, emphase,
    code, puces, cellules de tableau — pour un extrait qui se lit comme du
    texte. None pour une clôture de bloc de code, qui n'apprend rien."""
    if _CLOTURE_MD.match(ligne):
        return None
    texte = _TITRE_MD.sub("", ligne)
    texte = _LIEN_MD.sub(lambda m: m.group(1), texte)
    texte = _MARQUES_MD.sub("", texte)
    texte = _PUCE_MD.sub(lambda m: m.group(1) + "• ", texte)
    if texte.lstrip().startswith("|"):
        cellules = [c.strip() for c in texte.strip().strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cellules):
            return None  # ligne de séparation d'un tableau
        texte = " · ".join(c for c in cellules if c)
    return texte.rstrip()


def _valeur_de_champ(valeur) -> str | None:
    if isinstance(valeur, str):
        return valeur
    if isinstance(valeur, list) and all(isinstance(v, (str, int, float)) for v in valeur):
        return ", ".join(str(v) for v in valeur)
    return None


def _extraits(texte: str, lignes: list[int], termes: list[str]) -> list[dict]:
    """Les fenêtres de kb_search (ligne touchée ± 2, trois au plus, fusionnées),
    lisibles par un humain : une ligne touchée dans le frontmatter devient le
    champ qui la porte et sa valeur ; une fenêtre du corps ne déborde pas dans
    le frontmatter, et perd sa syntaxe markdown."""
    parse = parse_document(texte, loader=CHARGEUR)
    toutes = texte.split("\n")
    corps = parse.body_offset  # lignes 1..corps : le frontmatter, délimiteurs compris
    headings = parse_headings(texte)
    out: list[dict] = []
    champs_vus: set[str] = set()
    fenetres: list[list[int]] = []
    for n in lignes:
        if n <= corps:
            for i in range(n - 1, 0, -1):
                m = _CLE_FM.match(toutes[i])
                if m:
                    break
            else:
                continue
            cle = m.group(1)
            # `title` est déjà l'intitulé du résultat ; le répéter n'apprend rien.
            if cle in champs_vus or cle == "title":
                continue
            champs_vus.add(cle)
            valeur = _valeur_de_champ((parse.frontmatter or {}).get(cle))
            brut = toutes[i].split(":", 1)[1].strip()
            out.append({"champ": cle, "morceaux": _surligner(valeur if valeur is not None else brut, termes)})
            continue
        debut, fin = max(corps + 1, n - CONTEXT_LINES), min(len(toutes), n + CONTEXT_LINES)
        if fenetres and debut <= fenetres[-1][1] + 1:
            fenetres[-1][1] = max(fenetres[-1][1], fin)
        else:
            fenetres.append([debut, fin, n])
    for debut, fin, n in fenetres:
        simples = [l for l in (_texte_simple(x) for x in toutes[debut - 1 : fin]) if l is not None]
        extrait = "\n".join(simples).strip("\n")
        if not extrait.strip():
            continue
        heading = heading_at_line(headings, n - 1)
        out.append({
            "champ": None,
            "debut": debut,
            "section": heading.text if heading else PREAMBLE_LABEL,
            "ancre": ancre(heading.text) if heading else None,
            "morceaux": _surligner(extrait, termes),
        })
    return out[:MAX_EXCERPTS_PER_DOC]


def _surligner(texte: str, termes: list[str]) -> list[tuple[str, bool]]:
    """(morceau, touché ?) : le gabarit échappe chaque morceau, jamais le moteur."""
    if not termes:
        return [(texte, False)]
    motif = re.compile("|".join(re.escape(t) for t in sorted(termes, key=len, reverse=True)), re.I)
    out, pos = [], 0
    for m in motif.finditer(texte):
        if m.start() > pos:
            out.append((texte[pos : m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(texte):
        out.append((texte[pos:], False))
    return out


def _page_demandee(params) -> int:
    try:
        return max(1, int(params.get("page") or 1))
    except ValueError:
        return 1


def recherche(r: Requete) -> Response:
    params = r.request.query_params
    q = (params.get("q") or "").strip()
    mode = params.get("mode") if params.get("mode") in ("keyword", "regex") else "keyword"
    demandees = [b for b in params.getlist("base") if b in r.visibles]
    noms = demandees or r.visibles_tries()
    une_base = len(noms) == 1
    page = _page_demandee(params) if une_base else 1
    resultats = []
    if q:
        termes = split_terms(q) if mode == "keyword" else []
        for nom in noms:
            modele = r.modele(nom)
            # Tout le classement : kb_search le coupe à 25, la page le pagine.
            issue = run_search(modele.base, q, mode, RECHERCHE_TOUT)
            total = len(issue.docs)
            if une_base:
                # Une page au-delà de la dernière montre la dernière.
                page = min(page, max(1, -(-total // RECHERCHE_PAR_PAGE)))
                tranche = issue.docs[(page - 1) * RECHERCHE_PAR_PAGE : page * RECHERCHE_PAR_PAGE]
            else:
                tranche = issue.docs[:RECHERCHE_APERCU]
            docs = []
            for hits in tranche:
                rel = modele.base.rel_path(hits.path)
                doc = modele.docs.get(rel)
                try:
                    texte = hits.path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                docs.append({
                    "chemin": rel,
                    "titre": doc.titre if doc else rel,
                    "type": doc.type if doc else None,
                    "extraits": _extraits(texte, hits.lines, termes),
                })
            if total:
                resultats.append({
                    "nom": nom, "partiel": issue.partial, "total": total, "docs": docs,
                    "pages": -(-total // RECHERCHE_PAR_PAGE),
                })
    return r.page(
        "recherche.html", q=q, mode=mode, noms=demandees, toutes=r.visibles_tries(),
        resultats=resultats, une_base=une_base, page=page, par_page=RECHERCHE_PAR_PAGE,
        apercu=RECHERCHE_APERCU, total=sum(x["total"] for x in resultats),
    )


# --- propositions, journal --------------------------------------------------------


def propositions(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    statut = r.request.query_params.get("statut")
    statut = statut if statut in STATUSES else None
    collecte = collect(modele.base, STATUSES, loader=CHARGEUR)
    comptes = Counter(e.status for e in collecte.entries)
    entrees = [e for e in collecte.entries if statut is None or e.status == statut]
    pages = max(1, -(-len(entrees) // PROPOSITIONS_PAR_PAGE))
    page = min(_page_demandee(r.request.query_params), pages)
    return r.page(
        "propositions.html", nom=modele.nom, base=modele.base.manifest,
        entrees=entrees[(page - 1) * PROPOSITIONS_PAR_PAGE : page * PROPOSITIONS_PAR_PAGE],
        total=len(entrees), comptes=comptes, page=page, pages=pages,
        par_page=PROPOSITIONS_PAR_PAGE, illisibles=collecte.unreadable, statut=statut,
        statuts=STATUSES,
    )


def proposition(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    ident = r.request.path_params["id"]
    for entree in collect(modele.base, STATUSES, loader=CHARGEUR).entries:
        if entree.id == ident:
            break
    else:
        raise Introuvable(ident)
    texte = entree.path.read_text(encoding="utf-8", errors="replace")
    corps = parse_document(texte, loader=CHARGEUR).body
    # Une proposition est une donnée non fiable, rendue sous la même politique
    # que le corpus ; ses liens se lisent depuis la racine du corpus.
    rendu = rendre(corps, Contexte(modele.nom, "", r.lien_vers, image_vers=r.image_vers))
    return r.page(
        "proposition.html", nom=modele.nom, base=modele.base.manifest, entree=entree,
        rendu=rendu, liste=lambda v: v if isinstance(v, list) else ([v] if v else []),
    )


def journal(r: Requete) -> Response:
    modele = r.modele(r.nom_de_base())
    log = modele.docs.get("log.md")
    rendu = _rendu_document(r, modele, log) if log else None
    return r.page(
        "journal.html", nom=modele.nom, base=modele.base.manifest, rendu=rendu,
        commits=r.lecteur.historique(modele.nom, HISTORIQUE), historique=HISTORIQUE,
    )


# --- graphe ------------------------------------------------------------------------


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
    figure = disposer(list(analyse.bases), [(a.source, a.cible, a.references, a) for a in analyse.aretes])
    titres = {nom: r.hub.registry.get(nom).manifest.title for nom in analyse.bases}
    return r.page(
        "graphe.html", analyse=analyse, figure=figure, titres=titres,
        non_declarees=_non_declarees(r, analyse),
    )
