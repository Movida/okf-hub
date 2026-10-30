"""Pages de gouvernance, de manques, de chronologie, d'état et « à propos »
(§ 5 bis.2, rév. 4.6).

Chacune rend visible ce que la structure des bases porte déjà, sans rien
calculer qui fasse autorité : les règles d'une base et son profil, ce qu'elle
devrait contenir et ne contient pas, ce qui s'est passé sur tout le hub dans un
mois, l'état de la découverte et des dépôts, et ce que cette interface est.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from importlib import metadata

from starlette.responses import Response

from .. import bootstrap, governance
from ..lecture.confiance import UTC, instant
from ..lecture.graphe import analyser
from ..lecture.liens import INTERBASE, INTERNE
from ..lecture.modele import BASE_ABSENTE, CASSE, CHARGEUR, etat_depot, resoudre, tete
from ..lecture.rendu import Contexte, ancre, rendre
from ..lecture.requete import compter
from ..mdutil import parse_document, parse_headings
from .commun import Requete, lignes_du_journal, synchros_en_echec

#: Une proposition nommée dans un message de commit (§ 6.2).
_ID_PROPOSITION = re.compile(r"\bprop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4}\b")
_SEMAINE = re.compile(r"^(\d{4})-W(\d{2})$")
COMMITS_PAR_SEMAINE_MAX = 2000
JOURNAL_LIGNES = 40

#: Préfixe d'un sujet de commit du hub → ce qu'il raconte.
SORTES_DE_COMMIT = (
    ("proposal:", "proposition déposée"),
    ("integrate:", "proposition intégrée"),
    ("reject:", "proposition rejetée"),
    ("regen:", "vues régénérées"),
)


_PLURIEL_ENTRE_PARENTHESES = re.compile(r"\b(\d+) (\w+)\(s\)")


def sujet_lisible(sujet: str, *, garder_id: bool = True) -> str:
    """Le sujet d'un commit, pour un humain : sans le préfixe du hub, sans
    « résolution(s) », et sans l'identifiant répété quand la page le lie déjà
    (`garder_id=False`) — « reject: prop-x — Rejet de prop-x : motif » devient
    « motif »."""
    _, texte = decrire_commit(sujet)
    texte = _PLURIEL_ENTRE_PARENTHESES.sub(lambda m: f"{m[1]} {m[2]}{'' if m[1] == '1' else 's'}", texte)
    # « prop-x — Rejet de prop-x : motif » : l'identifiant n'a pas à se répéter.
    texte = re.sub(r"^(prop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4})((?: \([^)]*\))? — )(?:Rejet|Intégration) de \1 : ",
                   r"\1\2", texte)
    if not garder_id:
        texte = re.sub(r"^prop-\d{4}-\d{2}-\d{2}-[0-9a-f]{4}(?: \([^)]*\))? — ", "", texte)
    return texte if texte.startswith("prop-") else texte[:1].upper() + texte[1:]


def decrire_commit(sujet: str) -> tuple[str, str]:
    """(ce que raconte le commit, son sujet sans le préfixe du hub)."""
    for prefixe, libelle in SORTES_DE_COMMIT:
        if sujet.startswith(prefixe):
            return libelle, sujet[len(prefixe):].strip()
    return "modification", sujet


# --- gouvernance ---------------------------------------------------------------------


def gouvernance(r: Requete) -> Response:
    """L'équivalent web de `kb_governance` : `GOVERNANCE.md` et `schema.yaml`, plus
    ce que le moteur comprend du profil. Ces fichiers sont hors corpus : `kb_read`
    ne les sert pas (§ 5.2), seul `kb_governance` le fait."""
    modele = r.modele(r.nom_de_base())
    manifeste = modele.base.manifest
    texte = manifeste.governance_rules.read_text(encoding="utf-8", errors="replace")
    corps = parse_document(texte, loader=CHARGEUR).body
    # Le titre du fichier devient celui de la page, au lieu de s'y répéter.
    titres = parse_headings(corps)
    titre = titres[0].text if titres and titres[0].level == 1 else None
    rendu = rendre(corps, Contexte(modele.nom, "", r.lien_vers, image_vers=r.image_vers), titre=titre)
    schema = None
    if manifeste.frontmatter_schema is not None:
        schema = manifeste.frontmatter_schema.read_text(encoding="utf-8", errors="replace")
    profil = modele.profil
    _, types = compter(modele.concepts(), "type", profil)
    return r.page(
        "gouvernance.html", nom=modele.nom, base=manifeste,
        statut=governance.status_of_file(manifeste.governance_rules),
        titre=titre, rendu=rendu, schema=schema, profil=profil, types=types,
        roles_champs=[c for c in profil.champs.values() if c.role],
        checks=[" ".join(c) for c in manifeste.checks],
        approuvee=None if r.hub.config.equipe else modele.nom in r.hub.registry.config.trusted_checks,
    )


# --- manques -------------------------------------------------------------------------


def _documents_attendus(r: Requete, nom: str) -> tuple[dict[str, list], dict[str, int]]:
    """(cible manquante → documents qui y renvoient, base hors vue → références)."""
    modele = r.lecteur.modele(nom)
    modeles = r.lecteur.modeles(sorted(r.visibles))
    attendus: dict[str, list] = defaultdict(list)
    hors_vue: dict[str, int] = defaultdict(int)
    for doc in modele.docs.values():
        for lien in doc.liens:
            cible = lien.cible
            if cible.sorte not in (INTERNE, INTERBASE):
                continue
            if cible.sorte == INTERBASE and cible.base not in r.visibles:
                hors_vue[cible.base] += 1
                continue
            resolution = resoudre(cible, modeles)
            if resolution.sorte in (CASSE, BASE_ABSENTE):
                prefixe = f"{cible.base}:/" if cible.sorte == INTERBASE else "/"
                attendus[prefixe + (cible.chemin or "") + ("/" if cible.repertoire else "")].append(doc)
    return attendus, hors_vue


def nombre_de_manques(r: Requete, nom: str) -> int:
    """Le compteur de l'onglet : documents attendus et questions en attente."""
    attendus, _ = _documents_attendus(r, nom)
    collecte, _ = r.hub.propositions(nom)
    questions = sum(1 for e in collecte.entries if e.status == "pending" and e.frontmatter.get("type") == "question")
    return len(attendus) + questions


def manques(r: Requete) -> Response:
    """Ce que la base devrait contenir et ne contient pas.

    Un lien cassé est le plus franc des manques : un document attendu, au point
    qu'on y renvoie, qui n'existe pas. S'y ajoutent ce que le profil déclare sans
    l'avoir (types, valeurs de facette), le croisement de deux facettes, les
    questions déposées sans réponse, et les sections de rôle `gaps`.
    """
    nom = r.nom_de_base()
    modele = r.modele(nom)
    profil = modele.profil
    attendus, hors_vue = _documents_attendus(r, nom)
    attendus_tries = sorted(
        ((cible, list({d.chemin: d for d in docs}.values())) for cible, docs in attendus.items()),
        key=lambda x: (-len(x[1]), x[0]),
    )
    _, types = compter(modele.concepts(), "type", profil)
    facettes_vides = []
    for nom_facette, facette in profil.facettes.items():
        _, effectifs = compter(modele.concepts(), nom_facette, profil)
        vides = [e.valeur for e in effectifs if e.nombre == 0 and e.declare]
        if vides:
            facettes_vides.append((facette.libelle or nom_facette, vides))
    matrice = _matrice(r, modele)
    collecte, _ = r.hub.propositions(nom)
    questions = [e for e in collecte.entries if e.status == "pending" and e.frontmatter.get("type") == "question"]
    lacunes = []
    titres_gaps = profil.titres_du_role("gaps")
    if titres_gaps:
        for doc in modele.concepts():
            for h in doc.sections:
                if h.normalized in titres_gaps:
                    lacunes.append((doc, h.text, ancre(h.text)))
    return r.page(
        "manques.html", nom=nom, base=modele.base.manifest,
        attendus=attendus_tries, hors_vue=sorted(hors_vue.items()),
        types_vides=[e for e in types if e.nombre == 0 and e.declare],
        facettes_vides=facettes_vides, matrice=matrice, questions=questions,
        lacunes=lacunes, gaps_declare=bool(titres_gaps), profil_enrichi=profil.enrichi,
    )


def _matrice(r: Requete, modele) -> dict | None:
    """Effectifs croisés de deux facettes déclarées : une case vide est un sujet
    documenté pour l'une des valeurs et pas pour l'autre."""
    profil = modele.profil
    noms = [n for n in profil.facettes if profil.separateur]
    if len(noms) < 2:
        return None
    params = r.request.query_params
    fa = params.get("lignes") if params.get("lignes") in noms else noms[0]
    fb = params.get("colonnes") if params.get("colonnes") in noms and params.get("colonnes") != fa else next(n for n in noms if n != fa)
    cases: dict[tuple[str, str], int] = defaultdict(int)
    valeurs_a, valeurs_b = list(profil.facettes[fa].valeurs), list(profil.facettes[fb].valeurs)
    for doc in modele.concepts():
        va = [f[1] for t in doc.tags if (f := profil.facette_de(t)) and f[0] == fa]
        vb = [f[1] for t in doc.tags if (f := profil.facette_de(t)) and f[0] == fb]
        for a in va:
            if a not in valeurs_a:
                valeurs_a.append(a)
            for b in vb:
                if b not in valeurs_b:
                    valeurs_b.append(b)
                cases[(a, b)] += 1
    return {"lignes": fa, "colonnes": fb, "noms": noms, "va": valeurs_a, "vb": valeurs_b, "cases": cases,
            "separateur": profil.separateur}


# --- chronologie ----------------------------------------------------------------------


def _semaine_demandee(r: Requete) -> tuple[dt.datetime, dt.datetime]:
    """La semaine ISO demandée (`AAAA-Wss`), ou la semaine courante."""
    brut = r.request.query_params.get("semaine") or ""
    m = _SEMAINE.match(brut)
    aujourd_hui = dt.datetime.now(UTC).date()
    if brut and not m:
        r.avis.append(f"semaine « {brut} » non comprise (AAAA-Wss, ex. 2026-W44) : semaine courante")
    try:
        lundi = dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1) if m else None
    except ValueError:
        r.avis.append(f"semaine « {brut} » inexistante : semaine courante")
        lundi = None
    if lundi is None:
        lundi = aujourd_hui - dt.timedelta(days=aujourd_hui.weekday())
    debut = dt.datetime(lundi.year, lundi.month, lundi.day, tzinfo=UTC)
    return debut, debut + dt.timedelta(days=7)


def _iso(jour: dt.datetime) -> str:
    annee, semaine, _ = jour.isocalendar()
    return f"{annee}-W{semaine:02d}"


def chronologie(r: Requete) -> Response:
    """Une semaine de tout le hub : propositions intégrées et rejetées,
    modifications, vues régénérées, documents dont la période (rôle `start`)
    commence — et, sur demande, les dépôts de propositions, de loin les plus
    nombreux (chacun reparaît à sa résolution)."""
    debut, fin = _semaine_demandee(r)
    params = r.request.query_params
    demandees = [b for b in params.getlist("base") if b]
    for b in demandees:
        if b not in r.visibles:
            r.avis.append(f"base « {b} » inconnue ou non ouverte : ignorée")
    noms = [b for b in demandees if b in r.visibles] or r.visibles_tries()
    sortes_connues = [libelle for _, libelle in SORTES_DE_COMMIT] + ["modification", "début"]
    voulues = [x for x in params.getlist("sorte") if x]
    for x in voulues:
        if x not in sortes_connues:
            r.avis.append(f"sorte « {x} » inconnue : ignorée")
    voulues = [x for x in voulues if x in sortes_connues] or [x for x in sortes_connues if x != "proposition déposée"]
    evenements = []
    for nom in noms:
        for c in r.lecteur.historique(nom, COMMITS_PAR_SEMAINE_MAX, depuis=debut, jusqu_a=fin):
            sorte = decrire_commit(c.sujet)[0]
            ids = list(dict.fromkeys(_ID_PROPOSITION.findall(c.sujet)))
            evenements.append({"date": c.date, "base": nom, "sorte": sorte, "texte": sujet_lisible(c.sujet, garder_id=False), "ids": ids,
                               "court": c.court, "doc": None})
        modele = r.lecteur.modele(nom)
        champ = modele.profil.champ_du_role("start")
        if champ is not None:
            for doc in modele.concepts():
                date = instant(doc.frontmatter.get(champ.nom))
                if date is not None and debut <= date < fin:
                    evenements.append({"date": date, "base": nom, "sorte": "début", "texte": doc.titre, "ids": [],
                                       "court": None, "doc": doc.chemin})
    comptes = defaultdict(int)
    for e in evenements:
        comptes[e["sorte"]] += 1
    montres = sorted((e for e in evenements if e["sorte"] in voulues), key=lambda e: e["date"], reverse=True)
    par_jour: dict[str, list] = defaultdict(list)
    for e in montres:
        par_jour[e["date"].astimezone().date().isoformat()].append(e)  # le jour du poste qui sert la page
    jours = [(jour, _regrouper(evenements_du_jour)) for jour, evenements_du_jour in sorted(par_jour.items(), reverse=True)]
    suivante = fin if fin <= dt.datetime.now(UTC) else None
    return r.page(
        "chronologie.html", semaine=_iso(debut), debut=debut, fin=fin - dt.timedelta(days=1),
        jours=jours, carte=_carte_d_activite(r, noms, voulues, debut),
        precedente=_iso(debut - dt.timedelta(days=7)), suivante=_iso(suivante) if suivante else None,
        toutes=r.visibles_tries(), noms_demandes=demandees, sortes=sortes_connues, voulues=voulues,
        comptes=comptes, total=len(evenements), montres=len(montres),
    )


#: Au-delà de ce nombre d'événements de même sorte, dans une base, un même
#: jour, ils se replient en une ligne dépliable.
REGROUPEMENT = 4


def _regrouper(evenements: list[dict]) -> list[dict]:
    """Les lignes d'un jour : un événement seul, ou un groupe d'événements de
    même sorte et de même base — « 12 propositions intégrées », dépliable —,
    à la place du premier de ses membres."""
    groupes: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in evenements:
        groupes[(e["base"], e["sorte"])].append(e)
    lignes, places = [], set()
    for e in evenements:
        cle = (e["base"], e["sorte"])
        membres = groupes[cle]
        if len(membres) < REGROUPEMENT:
            lignes.append({"seul": e})
        elif cle not in places:
            places.add(cle)
            lignes.append({"groupe": membres, "base": e["base"], "sorte": e["sorte"], "date": e["date"]})
    return lignes


SEMAINES_CARTE = 26


def _carte_d_activite(r: Requete, noms: list[str], voulues: list[str], semaine: dt.datetime) -> dict:
    """Une case par jour sur les 26 dernières semaines, intensité selon le nombre
    d'événements retenus ; chaque colonne mène à sa semaine. Calculée une fois
    par état des dépôts (leurs HEADs)."""
    aujourd_hui = dt.datetime.now(UTC)
    lundi = aujourd_hui - dt.timedelta(days=aujourd_hui.weekday())
    debut = dt.datetime(lundi.year, lundi.month, lundi.day, tzinfo=UTC) - dt.timedelta(weeks=SEMAINES_CARTE - 1)
    heads = tuple(tete(r.lecteur.modele(n).base.root) for n in noms)
    cle = ("carte", tuple(noms), tuple(voulues), heads, debut.date())

    def calcul():
        jours: dict[dt.date, int] = defaultdict(int)
        for nom in noms:
            for c in r.lecteur.historique(nom, 50_000, depuis=debut):
                sorte = decrire_commit(c.sujet)[0]
                if sorte in voulues:
                    jours[c.date.astimezone().date()] += 1
        return dict(jours)

    jours = r.hub.memo(cle, calcul)
    maximum = max(jours.values(), default=0)
    cases, colonnes = [], []
    for k in range(SEMAINES_CARTE):
        lundi_k = debut + dt.timedelta(weeks=k)
        colonnes.append({"x": 30 + k * 14, "iso": _iso(lundi_k), "courante": _iso(lundi_k) == _iso(semaine),
                         "mois": lundi_k.strftime("%m/%y") if lundi_k.day <= 7 else None})
        for j in range(7):
            jour = (lundi_k + dt.timedelta(days=j)).date()
            if jour > aujourd_hui.date():
                # À venir : dessiné, mais autrement qu'un jour sans activité.
                cases.append({"x": 30 + k * 14, "y": 14 + j * 14, "n": 0, "niveau": 0, "jour": jour.isoformat(),
                              "iso": _iso(lundi_k), "futur": True})
                continue
            n = jours.get(jour, 0)
            niveau = 0 if n == 0 else min(4, 1 + int(3 * n / maximum)) if maximum else 0
            cases.append({"x": 30 + k * 14, "y": 14 + j * 14, "n": n, "niveau": niveau, "jour": jour.isoformat(),
                          "iso": _iso(lundi_k), "futur": False})
    return {"cases": cases, "colonnes": colonnes, "largeur": 30 + SEMAINES_CARTE * 14, "hauteur": 14 + 7 * 14 + 4}


# --- état ---------------------------------------------------------------------------


def _journal(r: Requete) -> list[dict]:
    """Les avertissements et erreurs récents du journal partagé (§ 4.1), dont
    les synchronisations remote qui échouent au démarrage d'une instance.
    Un même message, répété par chaque instance qui démarre, n'est montré
    qu'une fois, avec son nombre d'occurrences et la dernière."""
    groupes: dict[tuple[str, str], dict] = {}
    for m in lignes_du_journal(r.hub.registry.config):
        cle = (m.group("niveau"), m.group("message"))
        g = groupes.setdefault(cle, {"niveau": cle[0], "message": cle[1], "fois": 0, "pids": set()})
        g["fois"] += 1
        g["dernier"] = m.group("date")
        g["pids"].add(m.group("pid"))
    retenus = sorted(groupes.values(), key=lambda g: g["dernier"], reverse=True)
    return retenus[:JOURNAL_LIGNES]


def etat(r: Requete) -> Response:
    """Ce que ce processus voit du hub. En mode équipe, seulement les bases
    ouvertes au visiteur : le rapport de découverte et le journal nomment des
    répertoires et des bases qui ne lui sont pas ouverts."""
    config = r.hub.registry.config
    rapport = r.hub.registry.last_report
    local = not r.hub.config.equipe
    bases = []
    for nom in r.visibles_tries():
        modele = r.lecteur.modele(nom)
        bases.append({
            "nom": nom,
            "fichiers": len(modele.docs),
            "fiches": len(modele.concepts()),
            "repertoire": modele.base.dir_name,
            "depot": etat_depot(modele.base.root),
            "avertissements": list(modele.base.manifest.warnings) + [f"profil : {a}" for a in modele.profil.avertissements],
            "derives": len(r.lecteur.derives(nom)),
        })
    # Une base livrée avec le hub et sans dépôt canonique a sa source dans
    # `bundles/` : n'avoir aucun remote est normal pour elle, pas pour une autre.
    livrees = set(bootstrap.available(config.hub_root)) - set(bootstrap.upstreams(config.hub_root))
    echecs = synchros_en_echec(config, r.visibles) if local else []
    return r.page(
        "etat.html", local=local, version=metadata.version("okf-hub"),
        echecs=echecs, bases_en_echec={b for e in echecs for b in e.bases}, livrees=livrees,
        hub_root=str(config.hub_root) if local else None, bases_dir=str(config.bases_dir) if local else None,
        bases=bases, rapport=rapport if local else None, journal=_journal(r) if local else [],
        graphe=analyser(r.lecteur, r.visibles_tries(), references=True),
        depot_hub=etat_depot(config.hub_root) if local else None,
    )


# --- à propos -------------------------------------------------------------------------


def a_propos(r: Requete) -> Response:
    """Le périmètre de cette interface, comme chaque base doit dire le sien."""
    return r.page(
        "a-propos.html", version=metadata.version("okf-hub"), nb_visibles=len(r.visibles),
        nb_installees=len(r.hub.registry.names()) if not r.hub.config.equipe else None,
        web=r.hub.config,
    )
