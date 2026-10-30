"""Socle commun des pages de l'interface web (§ 5 bis.2) : application, requête,
contrôle d'accès, adresses, gabarits.

Les pages vivent dans `app.py` (corpus, document, propositions, journal, graphe),
`explorer.py` (l'exploration, qui réunit recherche et critères) et `pages.py`
(gouvernance, manques, chronologie, état, à propos). Toutes passent par
`page` : rafraîchissement de la découverte, visiteur, bases visibles, erreurs.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlencode

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from .. import hublog
from ..errors import INVALID_INPUT, NOT_FOUND, UNKNOWN_BASE, ToolError
from ..lecture.confiance import instant
from ..lecture.liens import ANCRE, EXTERNE, HORS_CORPUS, INTERBASE, INTERNE, Cible
from ..lecture.modele import CHARGEUR, DOC, REP, BaseModele, Lecteur, resoudre
from ..lecture.rendu import Lien
from ..registry import Registry
from ..server import META_BASES
from ..tools.proposal_status_tool import STATUSES, Collected, collect
from .acces import AccesRefuse, Visiteur, WebConfig, bases_visibles, visiteur_de


ICI = Path(__file__).resolve().parent


#: Politique de contenu : rien d'extérieur, aucun cadre, et pour script le seul
#: fichier du hub (`static/okf.js`, le raccourci Ctrl+K) — jamais de script en
#: ligne. Le corps des documents vient en partie de propositions d'agents (§ 8).
CSP = (
    "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
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


@dataclass
class Hub:
    registry: Registry
    config: WebConfig
    rafraichir: Callable[[], object] = lambda: None
    """Re-scan de la découverte, sous cooldown (`HubServer._silent_rescan("web")`)."""
    lecteur: Lecteur = field(init=False)
    _verrou: threading.Lock = field(init=False, default_factory=threading.Lock)
    _propositions: dict = field(init=False, default_factory=dict)

    _sante: tuple | None = field(init=False, default=None)
    _compteurs: dict = field(init=False, default_factory=dict)
    _memo: dict = field(init=False, default_factory=dict)

    def memo(self, cle, calcul: Callable[[], object]):
        """Un résultat dérivé, gardé tant que sa clé (HEADs, générations…) ne
        change pas. Borné : au-delà de quelques dizaines de clés, tout repart."""
        with self._verrou:
            if cle in self._memo:
                return self._memo[cle]
        valeur = calcul()
        with self._verrou:
            if len(self._memo) > 64:
                self._memo.clear()
            self._memo[cle] = valeur
        return valeur

    def __post_init__(self) -> None:
        self.lecteur = Lecteur.de(self.registry)

    def couleur(self, nom: str) -> str:
        """Classe de couleur d'une base : sa place parmi les bases *installées*,
        pas parmi celles qu'un visiteur voit — un filtre ne repeint jamais les
        autres. Les bases propres au hub (`server.META_BASES`) restent grises :
        elles décrivent l'outil, pas un domaine. Huit couleurs catégorielles
        validées (daltonisme, contraste) ; au-delà, un gris neutre : une neuvième teinte inventée ne se
        distinguerait plus. La couleur ne porte jamais seule l'identité : le nom
        de la base l'accompagne partout."""
        if nom in META_BASES:
            # Grises, mais distinctes entre elles : chacune sa forme creuse.
            du_hub = [n for n in self.registry.names() if n in META_BASES]
            return f"b-autre h{du_hub.index(nom)}" if nom in du_hub else "b-autre"
        noms = [n for n in self.registry.names() if n not in META_BASES]
        rang = noms.index(nom) if nom in noms else len(noms)
        return f"b{rang}" if rang < COULEURS_DE_BASE else "b-autre"

    def propositions(self, nom: str) -> tuple[Collected, dict[str, str]]:
        """Les propositions d'une base, et le texte de chacune.

        Mises en cache comme le modèle : invalidées par un parcours `stat` de
        `proposals/` (une base en accumule des centaines, que chaque page de
        fiche consulte pour sa généalogie). Lecture confinée par
        `Base.proposal_files`, comme `kb_proposal_status`.
        """
        base = self.registry.get(nom)
        signature = []
        for statut in STATUSES:
            for chemin in base.proposal_files(statut):
                try:
                    st = chemin.stat()
                except OSError:
                    continue
                signature.append((statut, chemin.name, st.st_mtime_ns, st.st_size))
        cle = (str(base.root), tuple(signature))
        with self._verrou:
            deja = self._propositions.get(nom)
            if deja is not None and deja[0] == cle:
                return deja[1]
        collecte = collect(base, STATUSES, loader=CHARGEUR)
        corps = {}
        for e in collecte.entries:
            try:
                corps[e.id] = e.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                corps[e.id] = ""
        with self._verrou:
            self._propositions[nom] = (cle, (collecte, corps))
        return collecte, corps


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


def hors_route(request: Request, exc) -> Response:
    """Adresse qui ne correspond à aucune route : la même page que tout 404,
    avec les mêmes suggestions — sous le même contrôle d'accès."""
    requete = requete_de(request)
    return requete if isinstance(requete, Response) else introuvable(requete)


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


#: Couleurs catégorielles distinctes (`static/okf.css`, classes `b0` à `b7`).
COULEURS_DE_BASE = 8


def _nb(n, singulier: str, pluriel: str | None = None) -> str:
    """« 1 document », « 64 documents », « 0 document » : en français, zéro et un
    prennent le singulier. Le pluriel par défaut accorde le premier mot."""
    if n is None:
        n = 0
    if n < 2:
        return f"{n} {singulier}"
    if pluriel is None:
        premier, _, reste = singulier.partition(" ")
        pluriel = premier + "s" + (" " + reste if reste else "")
    return f"{n} {pluriel}"


_MOIS_COURTS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def _jour_de(valeur) -> "dt.date | None":
    if isinstance(valeur, dt.datetime):
        return valeur.astimezone().date()
    if isinstance(valeur, dt.date):
        return valeur
    lu = instant(valeur)
    return lu.astimezone().date() if lu else None


def _date(valeur) -> str:
    """Une date, en texte : « 29 oct. 2026 » — le format de toute l'interface.
    Du texte seulement : il sert aussi dans des attributs et des libellés."""
    jour = _jour_de(valeur)
    if jour is None:
        return str(valeur or "")
    return f"{jour.day} {_MOIS_COURTS[jour.month - 1]} {jour.year}"


#: En deçà, un événement se date relativement (« il y a 3 h ») ; au-delà, par sa date.
RELATIF_JUSQU_A = dt.timedelta(days=7)


def _quand(valeur, maintenant: "dt.datetime | None" = None) -> Markup:
    """Un événement passé : `<time datetime>`, relatif s'il a moins de sept jours,
    la date exacte (et l'heure, si elle est connue) au survol. Calculé au rendu,
    pour que la page reste juste sans script."""
    if isinstance(valeur, dt.datetime):
        moment = valeur if valeur.tzinfo else valeur.replace(tzinfo=dt.timezone.utc)
    elif isinstance(valeur, dt.date):
        moment = None
    else:
        moment = instant(valeur)
    jour = _jour_de(valeur)
    if jour is None:
        return Markup(escape(str(valeur or "")))
    if moment is None:
        return Markup(f'<time datetime="{jour.isoformat()}" title="{escape(jour_lisible(jour))}">{escape(_date(jour))}</time>')
    local = moment.astimezone()
    ecart = (maintenant or dt.datetime.now(dt.timezone.utc)) - moment
    if dt.timedelta(0) <= ecart < RELATIF_JUSQU_A:
        minutes = int(ecart.total_seconds() // 60)
        if minutes < 60:
            texte = "à l'instant" if minutes < 1 else f"il y a {minutes} min"
        elif minutes < 24 * 60:
            texte = f"il y a {minutes // 60} h"
        else:
            jours = ((maintenant or dt.datetime.now(dt.timezone.utc)).astimezone().date() - local.date()).days
            texte = "hier" if jours <= 1 else f"il y a {jours} jours"
    else:
        texte = _date(local)
    titre = f"{jour_lisible(local.date())}, {local:%H:%M}"
    return Markup(f'<time datetime="{escape(local.isoformat(timespec="minutes"))}" title="{escape(titre)}">{escape(texte)}</time>')


_ENV = Environment(
    loader=FileSystemLoader(ICI / "templates"),
    autoescape=select_autoescape(default=True, default_for_string=True),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

from ..lecture.rendu import ancre as _ancre  # noqa: E402

from . import revue as _revue  # noqa: E402

#: D'où vient une date de fraîcheur (§ 5 bis.1), dite en clair partout où elle s'affiche.
ORIGINES = {"verified": "dernière vérification", "generated": "production du contenu", "sources": "date de la source",
            "commit": "dernier commit", "fichier": "date du fichier"}

#: Les termes de l'interface, avec leur définition courte (infobulle) ; la page
#: à propos les définit en entier, sous l'ancre `g-<clé>`.
GLOSSAIRE = {
    "fiche": ("fiche", "Un document de connaissance du corpus — ni index ni journal."),
    "a-trancher": ("à trancher", "Une proposition déposée par une session, qu'aucune revue n'a encore intégrée ni rejetée."),
    "a-reverifier": ("à revérifier", "Une fiche qui cite un document d'une autre base modifié après elle."),
    "gouvernance-brouillon": ("gouvernance en brouillon",
                              "Les règles de la base peuvent encore évoluer ; les propositions restent acceptées et revues."),
    "manque": ("manque", "Ce que la base devrait contenir et ne contient pas."),
    "base-du-hub": ("base du hub", "Une base qui décrit le hub lui-même, et non un domaine."),
}

_ENV.globals.update(glossaire=GLOSSAIRE, url_base=url_base, url_doc=url_doc, url_rep=url_rep, url_page=url_page, ancre=_ancre,
                    libelles=_revue, origines=ORIGINES)
_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
         "novembre", "décembre")


def jour_lisible(jour: "dt.date | str | None") -> str:
    """« hier, mercredi 28 octobre 2026 » : un jour pour un humain."""
    if isinstance(jour, str):
        jour = dt.date.fromisoformat(jour[:10])
    if jour is None:
        return "date inconnue"
    aujourd_hui = dt.date.today()
    relatif = {0: "aujourd'hui, ", 1: "hier, "}.get((aujourd_hui - jour).days, "")
    return f"{relatif}{_JOURS[jour.weekday()]} {jour.day} {_MOIS[jour.month - 1]} {jour.year}"


_FIN_DE_PROPOSITION = re.compile(r"(?<=[.!?»;])\s+|\s+(?=—\s)|(?<=\s—)\s+|(?<=:)\s+")


def resume(texte: str, limite: int = 180) -> str:
    """Un texte coupé à une fin de phrase — à défaut, de membre de phrase (« ; »,
    « : », « — ») — qui tient dans `limite` caractères ; au milieu d'une phrase
    seulement en dernier recours, et alors à un mot."""
    texte = " ".join(str(texte or "").split())
    if len(texte) <= limite:
        return texte
    coupe = 0
    for m in _FIN_DE_PROPOSITION.finditer(texte):
        if m.start() > limite:
            break
        coupe = m.start()
    if coupe >= limite // 3:
        return texte[:coupe].rstrip(" ;:—,") + " …"
    mot = texte.rfind(" ", 0, limite)
    return texte[: mot if mot > 0 else limite].rstrip(" ,;:") + " …"


_ENV.filters["date"] = _date
_ENV.filters["resume"] = resume
_ENV.filters["quand"] = _quand
_ENV.filters["jour"] = jour_lisible
_ENV.filters["nb"] = _nb


@dataclass
class Requete:
    request: Request
    hub: Hub
    visiteur: Visiteur
    visibles: frozenset[str]
    avis: list[str] = field(default_factory=list)
    """Ce que la page n'a pas compris et a ignoré : un filtre ne s'avale pas en
    silence (affiché en tête de page)."""

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
        nom = contexte.get("nom")
        html = _ENV.get_template(gabarit).render(
            visiteur=self.visiteur, equipe=self.hub.config.equipe, avis=self.avis,
            couleur=self.hub.couleur, sante=sante(self),
            compteurs=compteurs(self, nom) if nom else {}, rubrique=rubrique(self.request.url.path), **contexte
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

    def proposition_vers(self, ident: str) -> str | None:
        """Adresse de la page d'une proposition d'une base visible, d'après son id."""
        bases = self.hub.memo(("ou-sont-les-propositions", self.visibles, tuple(
            len(self.hub.propositions(n)[0].entries) for n in sorted(self.visibles))),
            lambda: {e.id: n for n in sorted(self.visibles) for e in self.hub.propositions(n)[0].entries})
        nom = bases.get(ident)
        return url_page(nom, f"propositions/{ident}") if nom else None

    def image_vers(self, cible: Cible) -> str | None:
        """Adresse d'une image du corpus d'une base visible, ou None."""
        if cible.sorte not in (INTERNE, INTERBASE) or cible.base not in self.visibles or cible.repertoire:
            return None
        if chemin_d_image(self.lecteur.modele(cible.base), cible.chemin or "") is None:
            return None
        return url_doc(cible.base, cible.chemin or "")


def requete_de(request: Request) -> Requete | Response:
    """La requête et ce que son visiteur peut lire, ou le refus du mode équipe."""
    hub: Hub = request.app.state.hub
    adresse = request.client.host if request.client else None
    try:
        visiteur = visiteur_de(hub.config, request.headers, adresse)
    except AccesRefuse as exc:
        requete = Requete(request, hub, Visiteur(None), frozenset())
        return requete.page("erreur.html", 403, titre="Accès refusé", message=str(exc))
    return Requete(request, hub, visiteur, bases_visibles(hub.config, visiteur, hub.registry.names()))


def introuvable(r: Requete) -> Response:
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


def rubrique(chemin: str) -> str:
    """L'entrée de la navigation principale où l'on se trouve : une page d'une
    base relève de « Bases », sauf son Explorer et ses propositions."""
    for segment, nom in (("explorer", "explorer"), ("propositions", "propositions"),
                         ("chronologie", "chronologie"), ("graphe", "graphe")):
        if chemin == f"/{segment}" or (chemin.startswith("/b/") and f"/-/{segment}" in chemin):
            return nom
    return "bases" if chemin == "/" or chemin.startswith("/b/") else ""


def page_html(fonction: Callable[[Requete], Response]) -> Callable[[Request], Response]:
    def point_d_entree(request: Request) -> Response:
        request.app.state.hub.rafraichir()
        requete = requete_de(request)
        if isinstance(requete, Response):
            return requete
        try:
            return fonction(requete)
        except Introuvable:
            return introuvable(requete)
        except ToolError as exc:
            if exc.code in (UNKNOWN_BASE, NOT_FOUND):
                return introuvable(requete)
            if exc.code == INVALID_INPUT:
                return requete.page("erreur.html", 400, titre="Requête invalide", message=exc.message)
            hublog.error(f"web : {request.url.path} — {exc.code}: {exc.message}")
            return requete.page("erreur.html", 500, titre="Erreur", message=exc.message)

    point_d_entree.__name__ = fonction.__name__
    return point_d_entree


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


def page_demandee(params) -> int:
    try:
        return max(1, int(params.get("page") or 1))
    except ValueError:
        return 1


# --- santé du hub ----------------------------------------------------------------------

#: La santé se recalcule au plus toutes les … secondes : elle s'affiche dans
#: l'en-tête de chaque page, et lit l'état git de chaque dépôt.
SANTE_INTERVALLE_S = 30.0


JOURNAL_OCTETS = 512 * 1024
_LIGNE_JOURNAL = re.compile(r"^(?P<date>\S+) \[pid:(?P<pid>\d+)\] (?P<niveau>WARNING|ERROR) +(?P<message>.*)$")
_SYNCHRO = re.compile(r"^sync remote '(?P<base>[^']+)' : (?P<motif>[^(]+?)(?: \((?P<detail>.*)\))?$")
#: Une synchronisation qui a échoué compte dans la santé pendant ce délai : un
#: démarrage d'hier qui n'a pas pu tirer le remote sert peut-être encore.
SYNCHRO_FENETRE = dt.timedelta(hours=24)


def lignes_du_journal(config) -> list[re.Match]:
    """Les avertissements et erreurs de la fin du journal partagé (§ 4.1)."""
    chemin = config.log_file
    if chemin is None or not chemin.is_file():
        return []
    with chemin.open("rb") as f:
        f.seek(max(0, chemin.stat().st_size - JOURNAL_OCTETS))
        lignes = f.read().decode("utf-8", errors="replace").splitlines()
    return [m for m in map(_LIGNE_JOURNAL.match, lignes) if m is not None]


#: Détail d'un échec de synchronisation → (ce qui manque, ce qui y remédie).
_CAUSES_DE_SYNCHRO = (
    ("cannot run ssh", "ssh est absent de l'environnement de cette instance",
     "lancer l'instance là où ssh est installé, ou y donner aux bases un remote HTTPS avec un jeton"),
    ("could not read Username", "aucun identifiant pour un remote HTTPS",
     "configurer un gestionnaire d'identifiants git pour l'utilisateur de l'instance, ou passer le remote en SSH"),
    ("Permission denied (publickey)", "la clé SSH de l'instance est refusée",
     "ajouter la clé au compte qui a accès aux dépôts"),
    ("Could not resolve host", "le réseau ou le DNS est injoignable depuis l'instance", "vérifier sa connexion"),
)


@dataclass(frozen=True)
class EchecDeSynchro:
    """Des instances du hub qui n'ont pas pu se synchroniser avec des remotes,
    pour une même cause."""
    pids: tuple[str, ...]
    motif: str
    cause: str
    remede: str | None
    bases: tuple[str, ...]
    dernier: str
    fois: int


def synchros_en_echec(config, bases, maintenant: dt.datetime | None = None) -> list[EchecDeSynchro]:
    """Les synchronisations remote qui ont échoué dans les dernières 24 heures,
    par cause, avec les instances (pid) concernées. Le journal est commun à toutes les
    instances : celle qui échoue n'est pas forcément celle-ci. Un échec laisse
    les bases servir leur copie locale, qui fait foi : il dit qu'elles peuvent
    être en retard sur leur remote, pas qu'elles sont fausses."""
    seuil = (maintenant or dt.datetime.now(dt.timezone.utc)) - SYNCHRO_FENETRE
    groupes: dict[tuple[str, str, str], dict] = {}
    for m in lignes_du_journal(config):
        s = _SYNCHRO.match(m.group("message"))
        quand = instant(m.group("date"))
        if not s or s.group("base") not in bases or quand is None or quand < seuil:
            continue
        detail = s.group("detail") or ""
        cause, remede = next(((c, x) for cle, c, x in _CAUSES_DE_SYNCHRO if cle in detail),
                             (detail.split("\\n")[0] or s.group("motif").strip(), None))
        cle = (s.group("motif").strip(), cause)
        g = groupes.setdefault(cle, {"remede": remede, "bases": set(), "pids": set(), "fois": 0})
        g["bases"].add(s.group("base"))
        g["pids"].add(m.group("pid"))
        g["dernier"] = m.group("date")
        g["fois"] += 1
    return [EchecDeSynchro(tuple(sorted(g["pids"], key=int)), motif, cause, g["remede"], tuple(sorted(g["bases"])),
                           g["dernier"], g["fois"])
            for (motif, cause), g in sorted(groupes.items(), key=lambda x: x[1]["dernier"], reverse=True)]


@dataclass(frozen=True)
class Sante:
    niveau: str
    """`bon`, `attention` ou `critique` — toujours accompagné d'un libellé."""
    libelle: str
    raisons: tuple[str, ...]


def sante(r: "Requete") -> Sante:
    """Voyant d'ensemble, pour l'en-tête et la page d'état : critique si un
    bundle n'a pas été chargé — du contenu manque —, attention si une instance
    n'a pas pu se synchroniser avec un remote dans les 24 heures, si un dépôt a
    des commits non poussés ou des fichiers modifiés, ou si une déclaration
    `cites` est contredite. Des
    fiches à revérifier relèvent du contenu, pas de la santé du hub : elles n'y
    entrent pas. En mode équipe, seulement sur les bases visibles."""
    import time

    from ..lecture.graphe import analyser
    from ..lecture.modele import etat_depot

    hub = r.hub
    cle = (r.visibles, hub.config.equipe)
    maintenant = time.monotonic()
    with hub._verrou:
        if hub._sante is not None and hub._sante[0] == cle and maintenant - hub._sante[1] < SANTE_INTERVALLE_S:
            return hub._sante[2]
    critiques, attentions = [], []
    if not hub.config.equipe:
        rapport = hub.registry.last_report
        if rapport.invalid or rapport.collisions:
            critiques.append(_nb(len(rapport.invalid) + len(rapport.collisions), "bundle non chargé", "bundles non chargés"))
        echecs = synchros_en_echec(hub.registry.config, r.visibles)
        if echecs:
            # À surveiller, pas à corriger : les bases servent leur copie locale.
            instances = len({pid for e in echecs for pid in e.pids})
            attentions.append(f"synchro remote en échec ({_nb(instances, 'instance')})")
    non_pousses = modifies = 0
    for nom in sorted(r.visibles):
        depot = etat_depot(hub.lecteur.modele(nom).base.root)
        if depot is not None:
            non_pousses += depot.en_avance
            modifies += depot.modifies
    if non_pousses:
        attentions.append(_nb(non_pousses, "commit non poussé", "commits non poussés"))
    if modifies:
        attentions.append(_nb(modifies, "fichier modifié dans les bases", "fichiers modifiés dans les bases"))
    avertissements = analyser(hub.lecteur, sorted(r.visibles), references=True).avertissements
    if avertissements:
        attentions.append(_nb(len(avertissements), "avertissement de dépendance", "avertissements de dépendance"))
    if critiques:
        resultat = Sante("critique", "à corriger", tuple(critiques + attentions))
    elif attentions:
        resultat = Sante("attention", "à surveiller", tuple(attentions))
    else:
        resultat = Sante("bon", "en ordre", ())
    with hub._verrou:
        hub._sante = (cle, maintenant, resultat)
    return resultat


def compteurs(r: "Requete", nom: str) -> dict:
    """Les nombres des onglets d'une base : propositions en attente, fiches à
    revérifier, manques. En cache tant qu'aucun modèle ni aucune proposition ne
    change — chaque page d'une base les affiche."""
    from .pages import nombre_de_manques

    modeles = r.lecteur.modeles()
    collecte, _ = r.hub.propositions(nom)
    cle = (tuple((n, m.generation) for n, m in modeles.items()), r.visibles,
           tuple((e.id, e.status) for e in collecte.entries))
    with r.hub._verrou:
        deja = r.hub._compteurs.get(nom)
        if deja is not None and deja[0] == cle:
            return deja[1]
    valeurs = {
        "propositions": sum(1 for e in collecte.entries if e.status == "pending"),
        "reaudit": sum(1 for d in r.lecteur.derives(nom).values() if any(x.base in r.visibles for x in d)),
        "manques": nombre_de_manques(r, nom),
    }
    with r.hub._verrou:
        r.hub._compteurs[nom] = (cle, valeurs)
    return valeurs
