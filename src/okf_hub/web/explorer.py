"""Explorer : la recherche plein texte et les critères de `kb_find`, réunis
(§ 5 bis.2, rév. 4.6).

Deux questions que l'interface posait sur deux pages qui se ressemblaient :
*où en parle-t-on* (le texte, par ripgrep) et *qu'est-ce qui existe* (les
métadonnées, total exact). Un humain veut souvent les deux à la fois — « les
procédures de tel dossier qui parlent de sauvegarde » — : ici, le texte est un
critère parmi d'autres. La répartition du résultat se lit et se clique à côté
de la liste, les critères actifs se retirent d'un clic, et la page donne la
même requête en MCP, où les deux outils restent distincts (§ 1.5).

Les propositions **en attente** sont cherchées à part et marquées non revues :
ce qu'une session a appris est ainsi visible de la suivante avant la revue,
sans toucher à la liste d'exclusions du § 5.2, qui ne vaut que pour MCP.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict

from starlette.responses import RedirectResponse, Response

from ..errors import INVALID_INPUT, ToolError
from ..lecture.liens import INTERBASE, INTERNE, classer
from ..lecture.modele import CHARGEUR, cible_document, resoudre
from ..lecture.requete import SANS_TYPE, TRIS, Criteres, filtrer, lire_criteres
from ..mdutil import PREAMBLE_LABEL, heading_at_line, parse_document, parse_headings
from ..search import split_terms
from ..tools.search_tool import CONTEXT_LINES, MAX_EXCERPTS_PER_DOC
from ..lecture.rendu import ancre
from .commun import SUGGESTIONS_MAX, Requete, page_demandee, url_page

#: Documents par page de résultats.
PAR_PAGE = 25

#: Paramètres de l'adresse qui portent un critère (et non la pagination).
CRITERES_WEB = ("q", "mode", "type", "tag", "champ", "section", "sans_section", "sous",
                "liant", "profondeur", "amont", "mois", "tri")
LIBELLES_TRI = {
    "path": "chemin", "relevance": "pertinence", "oldest": "les plus anciens",
    "most_cited": "les plus cités", "reaudit": "priorité de ré-audit",
}


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


_CLE_FM = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")


_LIEN_MD = re.compile(r"!?\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")


_MARQUES_MD = re.compile(r"\*\*|__|`|~~")
#: Emphase simple `*texte*` ou `_texte_` — pas un `*` isolé, ni un `_` dans un identifiant.
_EMPHASE_MD = re.compile(r"(?<![\w*])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])")


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
    texte = _EMPHASE_MD.sub(lambda m: m.group(2), texte)
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
            out.append({"champ": cle, "morceaux": _surligner(_autour(valeur if valeur is not None else brut, termes), termes)})
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


#: Longueur d'un champ de frontmatter montré dans un extrait ; au-delà, la
#: fenêtre se centre sur le premier terme trouvé.
CHAMP_MAX = 320


def _autour(texte: str, termes: list[str]) -> str:
    if len(texte) <= CHAMP_MAX:
        return texte
    replie = texte.casefold()
    positions = [replie.find(t.casefold()) for t in termes if t and replie.find(t.casefold()) >= 0]
    centre = min(positions) if positions else 0
    debut = max(0, centre - CHAMP_MAX // 2)
    fin = min(len(texte), debut + CHAMP_MAX)
    return ("…" if debut else "") + texte[debut:fin].strip() + ("…" if fin < len(texte) else "")


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


def criteres_du_formulaire(params, avis: list[str]) -> Criteres:
    """Paramètres de l'adresse → critères de `kb_find` : une seule validation.
    Ce qui n'est pas compris est signalé dans `avis`, jamais avalé en silence."""
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
    def entier(cle: str, libelle: str):
        brut = (params.get(cle) or "").strip()
        if not brut:
            return None
        try:
            return int(brut)
        except ValueError:
            raise ToolError(INVALID_INPUT, f"{libelle} : un nombre entier est attendu (« {brut} »)")
    mode = params.get("mode") or "keyword"
    if mode not in ("keyword", "regex"):
        avis.append(f"mode « {mode} » inconnu (keyword ou regex) : mots retenus")
        mode = "keyword"
    q = (params.get("q") or "").strip() or None
    tri = (params.get("tri") or "").strip() or None
    if tri is not None and tri not in TRIS:
        avis.append(f"tri « {tri} » inconnu ({', '.join(TRIS)}) : ignoré")
        tri = None
    if tri == "relevance" and not q:
        avis.append("tri par pertinence sans texte cherché : ignoré")
        tri = None
    amont = (params.get("amont") or "").strip().lower()
    if amont and amont not in ("1", "oui", "true"):
        avis.append(f"amont « {amont} » non compris (1 pour l'exiger) : ignoré")
    return lire_criteres({
        "query": q,
        "query_regex": mode == "regex",
        "type": params.get("type") or None,
        "tags": [t for t in params.getlist("tag") if t.strip()],
        "fields": champs,
        "section": params.get("section") or None,
        "without_section": params.get("sans_section") or None,
        "under": params.get("sous") or None,
        "links_to": params.get("liant") or None,
        "links_to_depth": entier("profondeur", "profondeur") or 1,
        "upstream_changed": amont in ("1", "oui", "true"),
        "older_than_months": entier("mois", "ancienneté"),
        "sort": tri,
    })


def _cible(r: Requete, noms: list[str], criteres: Criteres) -> tuple[str, str] | None:
    if not criteres.links_to:
        return None
    cible = classer(criteres.links_to, noms[0] if len(noms) == 1 else "", "")
    if cible.sorte not in (INTERNE, INTERBASE) or not cible.base or cible.base not in r.visibles:
        raise ToolError(INVALID_INPUT, f"« cite le document » : référence illisible ou base non ouverte — {criteres.links_to}")
    modeles = r.lecteur.modeles([cible.base])
    trouve = cible_document(resoudre(cible, modeles), modeles)
    if trouve is None:
        raise ToolError(INVALID_INPUT, f"« cite le document » : aucun document {criteres.links_to}")
    return trouve


class _Adresses:
    """Adresses dérivées de la requête courante : ajouter ou retirer un critère
    sans toucher aux autres, et toujours revenir en page 1."""

    def __init__(self, base: str | None, params) -> None:
        self.base = base
        self.paires = [(k, v) for k, v in params.multi_items() if k != "page" and v != ""]

    def _url(self, paires) -> str:
        params: dict[str, list[str]] = defaultdict(list)
        for k, v in paires:
            params[k].append(v)
        return url_page(self.base, "explorer", **params)

    def avec(self, cle: str, valeur: str, multiple: bool = False) -> str:
        paires = [(k, v) for k, v in self.paires if k != cle or (multiple and v != valeur)]
        return self._url(paires + [(cle, valeur)])

    def sans(self, cle: str, valeur: str | None = None) -> str:
        return self._url([(k, v) for k, v in self.paires if not (k == cle and (valeur is None or v == valeur))])

    def page(self, n: int) -> str:
        return self._url(self.paires + [("page", str(n))])


def _actifs(adresses: _Adresses, params) -> list[tuple[str, str]]:
    """(libellé, adresse sans ce critère) de chaque critère posé."""
    libelles = {"q": "texte", "type": "type", "tag": "tag", "champ": "champ", "section": "section",
                "sans_section": "sans section", "sous": "dossier", "liant": "cite", "profondeur": "degré",
                "mois": "plus de … mois", "base": "base"}
    actifs = []
    for k, v in adresses.paires:
        if k == "mode" or (k == "profondeur" and v == "1"):
            continue
        if k == "amont":
            actifs.append(("amont modifié depuis", adresses.sans("amont")))
        elif k == "tri":
            actifs.append((f"tri : {LIBELLES_TRI.get(v, v)}", adresses.sans("tri")))
        elif k == "mois":
            actifs.append((f"plus de {v} mois", adresses.sans("mois")))
        else:
            actifs.append((f"{libelles.get(k, k)} : {v}", adresses.sans(k, v)))
    return actifs


def _repartition(r: Requete, items, noms: list[str], adresses: _Adresses) -> list[dict]:
    """Répartition du résultat entier — pas de la seule page —, chaque valeur
    liée à la requête qui l'ajoute."""
    groupes: list[dict] = []
    if len(noms) > 1:
        par_base = Counter(nom for nom, _ in items)
        groupes.append({"titre": "Base", "valeurs": [
            (b, n, adresses.avec("base", b, multiple=False)) for b, n in par_base.most_common()]})
    types = Counter(t.doc.type or SANS_TYPE for _, t in items)
    groupes.append({"titre": "Type", "valeurs": [
        (v, n, adresses.avec("type", v) if v != SANS_TYPE else None) for v, n in types.most_common(30)]})
    facettes: dict[str, Counter] = defaultdict(Counter)
    separateurs: dict[str, str] = {}
    provenances: Counter = Counter()
    champ_provenance = None
    for nom, t in items:
        profil = r.lecteur.modele(nom).profil
        for tag in t.doc.tags:
            f = profil.facette_de(tag)
            if f and f[0] in profil.facettes:
                facettes[f[0]][f[1]] += 1
                separateurs[f[0]] = profil.separateur or ":"
        champ = profil.champ_du_role("provenance")
        if champ is not None and t.doc.frontmatter.get(champ.nom) is not None:
            champ_provenance = champ.nom
            provenances[str(t.doc.frontmatter.get(champ.nom))] += 1
    for nom_facette, compte in sorted(facettes.items()):
        sep = separateurs[nom_facette]
        groupes.append({"titre": nom_facette, "valeurs": [
            (v, n, adresses.avec("tag", f"{nom_facette}{sep}{v}", multiple=True)) for v, n in compte.most_common(30)]})
    if provenances:
        groupes.append({"titre": "Provenance", "valeurs": [
            (v, n, adresses.avec("champ", f"{champ_provenance}={v}", multiple=True)) for v, n in provenances.most_common()]})
    if len(noms) == 1:
        dossiers = Counter(t.doc.chemin.split("/")[0] if "/" in t.doc.chemin else "(racine)" for _, t in items)
        groupes.append({"titre": "Dossier", "valeurs": [
            (v, n, adresses.avec("sous", v) if v != "(racine)" else None) for v, n in dossiers.most_common(30)]})
    derives = sum(1 for _, t in items if t.derives)
    if derives:
        groupes.append({"titre": "À revérifier", "valeurs": [("amont modifié depuis", derives, adresses.avec("amont", "1"))]})
    return groupes


def _propositions_en_attente(r: Requete, noms: list[str], criteres: Criteres) -> tuple[list[dict], int]:
    """Propositions en attente dont le texte contient la recherche — ce que des
    sessions ont appris et qu'aucune revue n'a encore intégré. (résultats, nombre
    de propositions en attente examinées)."""
    if not criteres.query:
        return [], 0
    if criteres.query_regex:
        try:
            motif = re.compile(criteres.query, re.IGNORECASE)
        except re.error:
            r.avis.append("expression non comprise pour les propositions en attente (syntaxe Python) : non cherchées")
            return [], 0
        touche = lambda texte: bool(motif.search(texte))
    else:
        termes = [t.casefold() for t in split_terms(criteres.query)]
        touche = lambda texte: all(t in texte.casefold() for t in termes)
    trouvees, examinees = [], 0
    for nom in noms:
        collecte, corps = r.hub.propositions(nom)
        for e in collecte.entries:
            if e.status != "pending":
                continue
            examinees += 1
            texte = corps.get(e.id, "")
            if touche(texte):
                trouvees.append({"base": nom, "entree": e})
    return trouvees, examinees


def _appel_mcp(noms: list[str], criteres: Criteres, une_base: bool) -> str:
    """La même requête, écrite pour une session MCP."""
    args = [f'base="{noms[0]}"' if len(noms) == 1 else ("base=" + str(noms) if len(noms) < 4 else 'base="*"')]
    if criteres.query and not criteres.query_regex:
        args.append(f'query="{criteres.query}"')
    for cle, valeur in (("type", criteres.type), ("section", criteres.section),
                        ("without_section", criteres.without_section), ("under", criteres.under),
                        ("links_to", criteres.links_to), ("older_than_months", criteres.older_than_months),
                        ("sort", criteres.sort)):
        if valeur:
            args.append(f'{cle}="{valeur}"' if isinstance(valeur, str) else f"{cle}={valeur}")
    if criteres.tags:
        args.append(f"tags={list(criteres.tags)}")
    if criteres.fields:
        args.append("fields={" + ", ".join(f'"{k}": {str(v).lower() if isinstance(v, bool) else repr(v)}' for k, v in criteres.fields) + "}")
    if criteres.links_to_depth > 1:
        args.append(f"links_to_depth={criteres.links_to_depth}")
    if criteres.upstream_changed:
        args.append("upstream_changed=true")
    appel = "kb_find(" + ", ".join(args) + ")"
    if criteres.query_regex:
        appel = f'kb_search(base=…, query="{criteres.query}", mode="regex") puis {appel}'
    return appel


def ancienne_adresse(r: Requete) -> Response:
    """`/trouver` et `/recherche` (rév. 4.5) mènent à l'Explorer, requête comprise."""
    base = r.request.path_params.get("base")
    if base is not None:
        r.nom_de_base()
    query = r.request.url.query
    return RedirectResponse(url_page(base, "explorer") + (f"?{query}" if query else ""), 308)


def explorer(r: Requete) -> Response:
    une_base = r.request.path_params.get("base")
    params = r.request.query_params
    if une_base is not None:
        noms = [r.nom_de_base()]
    else:
        demandees = [b for b in params.getlist("base") if b]
        for b in demandees:
            if b not in r.visibles:
                r.avis.append(f"base « {b} » inconnue ou non ouverte : ignorée")
        noms = [b for b in dict.fromkeys(demandees) if b in r.visibles] or r.visibles_tries()
    criteres = criteres_du_formulaire(params, r.avis)
    adresses = _Adresses(une_base, params)
    soumis = any(params.get(k) for k in CRITERES_WEB) or bool(params.getlist("base"))
    montrer = soumis or une_base is not None
    cible = _cible(r, noms, criteres)
    items = []
    for nom in noms:
        for t in filtrer(r.lecteur, r.modele(nom), criteres, cible=cible, bases=r.visibles):
            items.append((nom, t))
    groupe = params.get("groupe") == "base"
    par_base = len(noms) == 1 or groupe or criteres.tri == "path"
    if not par_base:
        items = _ordre_global(r, items, criteres, noms)
    pages = max(1, -(-len(items) // PAR_PAGE))
    page = min(page_demandee(params), pages)
    tranche = items[(page - 1) * PAR_PAGE : page * PAR_PAGE] if montrer else []
    termes = split_terms(criteres.query) if criteres.query and not criteres.query_regex else []
    resultats = []
    for nom, t in tranche:
        extraits = []
        if criteres.query and t.lignes:
            try:
                texte = r.lecteur.modele(nom).base.resolve_document(t.doc.chemin).read_text(encoding="utf-8", errors="replace")
                extraits = _extraits(texte, list(t.lignes), termes)
            except (OSError, ToolError):
                pass
        resultats.append({"base": nom, "t": t, "extraits": extraits})
    en_attente, examinees = _propositions_en_attente(r, noms, criteres)
    suggestions = _suggestions(r, noms)
    return r.page(
        "explorer.html",
        nom=une_base,
        base=r.modele(une_base).base.manifest if une_base else None,
        toutes=r.visibles_tries(), noms_demandes=params.getlist("base"),
        params=params, criteres=criteres, montrer=montrer, soumis=soumis,
        total=len(items), bases_touchees=len({n for n, _ in items}), nb_bases=len(noms),
        resultats=resultats, page=page, pages=pages, par_page=PAR_PAGE, adresses=adresses,
        actifs=_actifs(adresses, params), repartition=_repartition(r, items, noms, adresses),
        en_attente=en_attente, examinees=examinees, appel=_appel_mcp(noms, criteres, une_base is not None),
        suggestions=suggestions, exemples=_exemples(r, noms, suggestions),
        tris=LIBELLES_TRI, sans_type=SANS_TYPE, par_base=par_base, groupe=groupe,
        regrouper=adresses.avec("groupe", "base") if not groupe else adresses.sans("groupe", "base"),
    )


def _ordre_global(r: Requete, items, criteres, noms: list[str]):
    """Un seul classement pour toutes les bases, au lieu de base après base : le
    meilleur résultat d'une base ne doit pas attendre la fin d'une autre. Avec du
    texte, un titre (ou un synonyme) égal à la requête passe d'abord, puis un
    titre qui la contient, puis le nombre d'occurrences."""
    ordre_des_bases = {n: i for i, n in enumerate(noms)}
    tri = criteres.tri
    if tri == "relevance":
        cible = criteres.query.casefold()

        def titre(nom, t):
            champ = r.lecteur.modele(nom).profil.champ_du_role("aliases")
            synonymes = t.doc.frontmatter.get(champ.nom) if champ is not None else None
            libelles = [t.doc.titre.casefold()] + [str(x).casefold() for x in (
                synonymes if isinstance(synonymes, list) else [synonymes] if synonymes else [])]
            return 0 if cible in libelles else 1 if any(cible in x for x in libelles) else 2

        cle = lambda x: (titre(*x), -(x[1].occurrences or 0), ordre_des_bases[x[0]], x[1].doc.chemin)
    elif tri == "oldest":
        cle = lambda x: (x[1].fraicheur.date, ordre_des_bases[x[0]], x[1].doc.chemin)
    elif tri == "most_cited":
        cle = lambda x: (-x[1].citations, ordre_des_bases[x[0]], x[1].doc.chemin)
    elif tri == "reaudit":
        cle = lambda x: (-x[1].score, ordre_des_bases[x[0]], x[1].doc.chemin)
    else:
        return items
    return sorted(items, key=cle)
