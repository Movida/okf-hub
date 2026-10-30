"""Interface web de consultation (§ 5 bis.2 et § 5 bis.4, rév. 4.5)."""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from conftest import git

from okf_hub.config import HubConfig
from okf_hub.registry import Registry
from okf_hub.web.acces import ConfigWebInvalide, WebConfig
from okf_hub.web.app import CSP, Hub, creer_app

PROFIL = """
required:
  - name: type
optional:
  - name: provenance
    role: provenance
    values: {interne: {label: Document interne, evidence: resource}, terrain: {label: Constat}}
  - name: debut
    role: start
facets:
  separator: ":"
  env: {label: Environnement, values: {prod: Production}}
sections:
  Pièges: warnings
  Amont: upstream
views:
  - {title: Par environnement, kind: list, group: {facet: env}}
  - {title: Chronologie, kind: timeline}
  - {title: Liens, kind: graph}
  - {title: Fraîcheur, kind: freshness, filter: {older_than_months: 1}}
"""

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")


@pytest.fixture
def bases(hub, make_bundle):
    produit = make_bundle("produit-x", git_init=False, create_corpus=False, name="produit-x")
    produit.doc("index.md", "# Produit X\n\nSommaire du produit.\n")
    produit.doc("m/survivance.md", "---\ntype: Concept\ntitle: Survivance\n---\n# Survivance\n\nTexte.\n")
    produit.init_git()

    inst = make_bundle(
        "installation-x", git_init=False, create_corpus=False, name="installation-x",
        governance={"rules": "./GOVERNANCE.md", "frontmatter-schema": "./schema.yaml"},
    )
    inst.schema(PROFIL)
    inst.doc("f.md",
             "---\ntype: Procédure\ntitle: Fiche F\nprovenance: interne\nresource: doc-interne\n"
             "tags: [env:prod, env:recette]\ndebut: 2026-05-02\n"
             "sources: [{id: releve, resource: 'https://exemple.org/s', last_modified: 2020-01-01T00:00:00Z}]\n"
             "autre: valeur\n---\n"
             "# Fiche F\n\nCorps <script>alert(1)</script> [js](javascript:alert(1)) selon[^releve].\n\n"
             "# Pièges\n\nAttention.\n\n# Amont\n\nDécline [la survivance](produit-x:/m/survivance.md), "
             "voir [g](/g.md), [cassé](/rien.md), [fermée](referentiel-x:/o.md).\n\n"
             "![schéma](img/schema.png) ![dessin](img/dessin.svg) ![loin](https://exemple.org/i.png)\n")
    inst.doc("g.md", "---\ntype: Incident\n---\n# G\n\nRetour vers [f](f.md).\n")
    inst.doc("profond.md", "".join("  " * i + "* n\n" for i in range(110)) + "\nfin\n")
    (inst.root / "knowledge" / "img").mkdir()
    (inst.root / "knowledge" / "img" / "schema.png").write_bytes(PNG)
    (inst.root / "knowledge" / "img" / "dessin.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
    (inst.root / "knowledge" / "img" / "notes.txt").write_text("pas une image", encoding="utf-8")
    inst.init_git()

    ref = make_bundle("referentiel-x", git_init=False, create_corpus=False, name="referentiel-x")
    ref.doc("o.md", "# O\n\nCite [la fiche](installation-x:/f.md).\n").init_git()

    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    return reg


def _client(reg, web: WebConfig | None = None, adresse: str = "127.0.0.1") -> TestClient:
    return TestClient(creer_app(Hub(reg, web or WebConfig())), client=(adresse, 50000))


# --- mode local --------------------------------------------------------------------


def test_accueil_liste_les_bases_avec_en_tetes_de_securite(bases):
    rep = _client(bases).get("/")
    assert rep.status_code == 200
    assert "Produit X" not in rep.text  # titre du manifeste, pas de l'index
    for nom in ("produit-x", "installation-x", "referentiel-x"):
        assert f"/b/{nom}/" in rep.text
    assert rep.headers["content-security-policy"] == CSP
    # Un seul script, servi par le hub ; aucun en ligne.
    assert "script-src 'self'" in CSP and "unsafe-inline" not in CSP
    assert '<script src="/static/okf.js" defer></script>' in rep.text
    assert _client(bases).get("/static/okf.js").status_code == 200
    assert rep.headers["x-content-type-options"] == "nosniff"


def test_toute_ecriture_est_refusee(bases):
    client = _client(bases)
    for url in ("/", "/b/installation-x/f.md", "/explorer"):
        assert client.post(url).status_code == 405


def test_document_rendu_sans_html_ni_lien_dangereux(bases):
    html = _client(bases).get("/b/installation-x/f.md").text
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="javascript:' not in html
    assert 'href="/b/produit-x/m/survivance.md"' in html  # référence entre bases réécrite
    assert 'href="/b/installation-x/g.md"' in html
    assert 'class="casse" title="lien cassé : /rien.md"' in html
    assert '<a href="#source-releve">[releve]</a>' in html and 'id="source-releve"' in html
    assert '<section class="role role-warnings">' in html
    # En-tête : provenance et sa preuve, facettes, champ hors vocabulaire.
    assert "Document interne" in html and "resource : doc-interne" in html
    assert "Production" in html and "hors vocabulaire" in html
    assert "autre: valeur" in html
    # Le premier heading, qui répète le titre de l'en-tête, n'est pas rendu deux fois.
    assert html.count("Fiche F</h1>") == 1


def test_liens_entrants_dont_le_role_amont(bases):
    html = _client(bases).get("/b/produit-x/m/survivance.md").text
    assert "Déclinée par" in html and "/b/installation-x/f.md" in html
    html = _client(bases).get("/b/installation-x/f.md").text
    assert re.search(r"Citée par <span class=\"puce-base[^>]*>.*?referentiel-x</span></h3>", html)


def test_images_servies_confinees_et_svg_sandboxe(bases):
    client = _client(bases)
    html = client.get("/b/installation-x/f.md").text
    assert '<img src="/b/installation-x/img/schema.png" alt="schéma"' in html
    assert "[image : loin]" in html  # une image externe n'est jamais chargée
    png = client.get("/b/installation-x/img/schema.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    svg = client.get("/b/installation-x/img/dessin.svg")
    assert svg.headers["content-security-policy"].startswith("sandbox")
    assert client.get("/b/installation-x/img/notes.txt").status_code == 404
    assert client.get("/b/installation-x/okf-bundle.yaml").status_code == 404
    assert client.get("/b/installation-x/%2E%2E/GOVERNANCE.md").status_code == 404


def test_repertoire_et_index(bases):
    client = _client(bases)
    base = client.get("/b/produit-x/").text
    assert "Sommaire du produit" in base and "/b/produit-x/m/" in base
    assert "Survivance" in client.get("/b/produit-x/m/").text
    assert client.get("/b/produit-x/m", follow_redirects=False).status_code == 308
    assert client.get("/b/produit-x", follow_redirects=False).headers["location"] == "/b/produit-x/"
    assert client.get("/b/inconnue/").status_code == 404
    assert client.get("/b/produit-x/rien.md").status_code == 404


def test_rendu_tronque_signale_avec_sa_source(bases):
    html = _client(bases).get("/b/installation-x/profond.md").text
    assert "Rendu possiblement incomplet" in html and 'class="source"' in html


def test_explorer_critères_et_anciennes_adresses(bases):
    client = _client(bases)
    html = client.get("/b/installation-x/-/explorer?section=warnings").text
    assert "<strong>1 fiche</strong>" in html and "Fiche F" in html
    html = client.get("/explorer?type=Concept").text
    assert "/b/produit-x/m/survivance.md" in html
    assert client.get("/explorer?mois=abc").status_code == 400
    # Les adresses de la rév. 4.5 mènent à l'Explorer, requête comprise.
    for ancienne, nouvelle in (("/trouver?type=Concept", "/explorer?type=Concept"),
                               ("/recherche?q=x", "/explorer?q=x"),
                               ("/b/produit-x/-/trouver?type=Concept", "/b/produit-x/-/explorer?type=Concept")):
        rep = client.get(ancienne, follow_redirects=False)
        assert rep.status_code == 308 and rep.headers["location"] == nouvelle


def test_vues_declarees(bases):
    client = _client(bases)
    liste = client.get("/b/installation-x/-/vues/0").text
    assert "prod" in liste and "Fiche F" in liste
    assert "2026-05" in client.get("/b/installation-x/-/vues/1").text
    graphe = client.get("/b/installation-x/-/vues/2").text
    assert "<svg" in graphe and "/b/installation-x/g.md" in graphe
    fraicheur = client.get("/b/installation-x/-/vues/3").text.split('<aside')[0]
    # f.md (provenance à preuve, source de 2020) est listée ; g.md n'a pas de preuve à confronter.
    assert "Fiche F" in fraicheur and "/b/installation-x/g.md" not in fraicheur
    assert client.get("/b/installation-x/-/vues/9").status_code == 404


def test_recherche_plein_texte(bases):
    html = _client(bases).get("/explorer?q=attention").text
    assert "<mark>Attention</mark>" in html and "§" in html


def test_propositions_et_journal(bases, hub):
    from okf_hub.tools import propose_tool

    propose_tool.run(bases, {
        "base": "installation-x", "type": "question", "concerns": "un <b>objet</b>",
        "content": "Corps <script>x</script>", "sources": ["s"], "confidence": "low",
        "submitted_by": "human:test",
    })
    client = _client(bases)
    liste = client.get("/b/installation-x/-/propositions").text
    assert "un &lt;b&gt;objet&lt;/b&gt;" in liste
    ident = liste.split("/-/propositions/prop-")[1].split('"')[0]
    detail = client.get(f"/b/installation-x/-/propositions/prop-{ident}").text
    assert "&lt;script&gt;" in detail and "<script>x" not in detail
    assert "Retour vers" not in client.get("/b/installation-x/-/journal").text
    assert "proposal: prop-" in client.get("/b/installation-x/-/journal").text
    accueil = client.get("/").text
    assert "1 proposition attend une revue" in accueil and 'href="/propositions"' in accueil


def test_graphe_des_bases(bases):
    html = _client(bases).get("/graphe").text
    assert "<svg" in html and "installation-x" in html and "produit-x" in html


def test_l_interface_n_ecrit_rien(bases, hub):
    client = _client(bases)
    for url in ("/", "/b/installation-x/f.md", "/b/installation-x/-/journal", "/graphe",
                "/b/installation-x/-/vues/3", "/explorer?q=x", "/etat", "/chronologie",
                "/b/installation-x/-/gouvernance", "/b/installation-x/-/manques"):
        client.get(url)
    for nom in ("produit-x", "installation-x", "referentiel-x"):
        assert git(hub[1] / nom, "status", "--porcelain", "--ignored") == ""


# --- mode équipe ---------------------------------------------------------------------


EQUIPE = {
    "identity-header": "X-Forwarded-User",
    "groups-header": "X-Forwarded-Groups",
    "visibility": {"produit-x": ["*"], "installation-x": ["equipe-a"]},
}


def test_equipe_refuse_la_connexion_directe(bases):
    web = WebConfig.depuis(EQUIPE)
    rep = _client(bases, web, adresse="203.0.113.9").get("/", headers={"X-Forwarded-User": "a"})
    assert rep.status_code == 403 and "Connexion directe refusée" in rep.text


def test_equipe_exige_l_identite(bases):
    rep = _client(bases, WebConfig.depuis(EQUIPE)).get("/")
    assert rep.status_code == 403 and "X-Forwarded-User" in rep.text


def test_equipe_ne_sert_que_les_bases_ouvertes(bases):
    client = _client(bases, WebConfig.depuis(EQUIPE))
    anonyme = {"X-Forwarded-User": "quelqu-un"}
    accueil = client.get("/", headers=anonyme).text
    assert "/b/produit-x/" in accueil and "/b/installation-x/" not in accueil
    assert client.get("/b/installation-x/", headers=anonyme).status_code == 404
    # Une base sans ligne de visibilité n'est servie à personne.
    membre = {"X-Forwarded-User": "b", "X-Forwarded-Groups": "equipe-a, autre"}
    assert client.get("/b/referentiel-x/", headers=membre).status_code == 404
    assert client.get("/b/installation-x/f.md", headers=membre).status_code == 200


def test_equipe_masque_les_liens_vers_et_depuis_une_base_fermee(bases):
    client = _client(bases, WebConfig.depuis(EQUIPE))
    membre = {"X-Forwarded-User": "b", "X-Forwarded-Groups": "equipe-a"}
    html = client.get("/b/installation-x/f.md", headers=membre).text
    # Lien vers une base fermée : ni cliquable, ni distingué d'une base absente.
    assert 'class="masque" title="lien vers une base qui ne vous est pas ouverte"' in html
    # Lien entrant depuis une base fermée : absent.
    assert "referentiel-x" not in html.split("<strong>Entrants</strong>")[1].split("<strong>Sortants</strong>")[0]
    # Le graphe nomme la base citée (son nom est écrit dans le texte) sans dire
    # si elle est installée.
    graphe = client.get("/graphe", headers=membre).text
    assert "referentiel-x <em class=\"hors\">hors de ce hub ou de votre vue</em>" in graphe
    assert "/b/referentiel-x/" not in graphe


def test_equipe_derriere_un_proxy_distant_nomme(bases):
    web = WebConfig.depuis({**EQUIPE, "host": "0.0.0.0", "trusted-proxies": ["172.18.0.0/24"]})
    client = _client(bases, web, adresse="172.18.0.5")
    assert client.get("/", headers={"X-Forwarded-User": "a"}).status_code == 200
    assert _client(bases, web, adresse="127.0.0.1").get("/", headers={"X-Forwarded-User": "a"}).status_code == 403


@pytest.mark.parametrize(
    "brut",
    [
        {"host": "0.0.0.0"},  # local sans authentification, hors bouclage
        {"visibility": {"a": ["*"]}},
        {"trusted-proxies": ["127.0.0.1"]},
        {"identity-header": "X-User", "trusted-proxies": ["pas-une-adresse"]},
        {"identity-header": "mauvais en-tête"},
        {"port": 0},
        {"inconnue": 1},
        "pas une correspondance",
    ],
)
def test_configuration_web_invalide(brut):
    with pytest.raises(ConfigWebInvalide):
        WebConfig.depuis(brut)


def test_configuration_web_par_defaut():
    web = WebConfig.depuis(None)
    assert (web.host, web.port, web.equipe) == ("127.0.0.1", 8077, False)
    assert WebConfig.depuis({}, host="::1", port=9000).host == "::1"


# --- relecture du 29/10 : graphe, recherche, Trouver, 404, propositions ---------------


def test_figure_ordre_sans_croisement_et_noeuds_isoles():
    from okf_hub.web.figure import _croisements, disposer, ordonner

    # Un carré dont les diagonales a-c et b-d se croisent dans l'ordre alphabétique.
    paires = (("a", "c"), ("b", "d"))
    assert _croisements(("a", "b", "c", "d"), paires) == 1
    assert _croisements(ordonner(("a", "b", "c", "d"), paires), paires) == 0
    figure = disposer(["a", "b", "seule"], [("a", "b", 10, None), ("b", "a", 1, None)])
    isoles = [n.cle for n in figure.noeuds if n.isole]
    assert isoles == ["seule"] and figure.isoles_y is not None
    # Deux arêtes opposées ne se superposent pas : leurs étiquettes diffèrent.
    aller, retour = figure.arcs
    assert (aller.etiquette_x, aller.etiquette_y) != (retour.etiquette_x, retour.etiquette_y)
    assert aller.epaisseur > retour.epaisseur


def test_graphe_alerte_les_references_sans_declaration(bases):
    html = _client(bases).get("/graphe").text
    assert "références entre bases ne sont couvertes par aucune déclaration" in html
    assert "installation-x/okf-bundle.yaml   cites: [produit-x, referentiel-x]" in html
    # installation-x → referentiel-x → installation-x : déclarer l'observé ferait un cycle.
    assert "formeraient un cycle" in html


def test_recherche_extraits_lisibles(bases, hub):
    b = hub[1] / "installation-x" / "knowledge"
    (b / "h.md").write_text(
        "---\ntitle: Messagerie interne\ndescription: La messagerie de test\n---\n\n"
        "# Section\n\nVoir **la messagerie** et [son guide](/f.md).\n\n| Clé | messagerie |\n|---|---|\n",
        encoding="utf-8",
    )
    html = _client(bases).get("/b/installation-x/-/explorer?q=messagerie").text
    assert "frontmatter · <code>description</code>" in html
    assert "frontmatter · <code>title</code>" not in html  # déjà l'intitulé du résultat
    assert "Voir la <mark>messagerie</mark> et son guide." in html
    assert "**" not in html.split('class="texte-extrait"')[1] and "|---|" not in html
    assert "<strong>1 fiche</strong>" in html


def test_explorer_pagine_et_groupe_par_base(bases, monkeypatch):
    from okf_hub.web import explorer

    monkeypatch.setattr(explorer, "PAR_PAGE", 1)
    client = _client(bases)
    une = client.get("/b/installation-x/-/explorer?q=md&page=99").text
    assert "page 2 sur 2" in une and "← précédents" in une  # au-delà : la dernière page
    plusieurs = client.get("/explorer?q=md").text
    # Par défaut, un seul classement : chaque résultat porte sa base.
    assert "page 1 sur 3" in plusieurs and 'class="base-resultats' not in plusieurs
    assert "Grouper par base" in plusieurs
    groupes = client.get("/explorer?q=md&groupe=base").text
    assert "page 1 sur 3" in groupes and 'class="base-resultats' in groupes


def test_explorer_aide_suggestions_et_exemples(bases):
    html = _client(bases).get("/b/installation-x/-/explorer").text
    assert '<datalist id="l-types">' in html and '<option value="Procédure">' in html
    assert '<option value="env:prod">' in html
    assert "Exemples :" in html and "?section=warnings" in html
    assert "Ne contient pas la section" in html


def test_introuvable_suggere_l_adresse_d_une_base_ouverte(bases):
    rep = _client(bases).get("/base/produit-x")
    assert rep.status_code == 404 and 'href="/b/produit-x/"' in rep.text
    # En équipe, une base fermée n'est jamais suggérée.
    web = WebConfig.depuis(EQUIPE)
    rep = _client(bases, web).get("/base/installation-x", headers={"X-Forwarded-User": "a"})
    assert rep.status_code == 404 and "/b/installation-x/" not in rep.text


def test_propositions_paginees_avec_effectifs(bases, monkeypatch):
    from okf_hub.tools import propose_tool
    from okf_hub.web import app as module

    for i in range(3):
        propose_tool.run(bases, {
            "base": "produit-x", "type": "question", "concerns": f"sujet {i}", "content": "x",
            "sources": ["s"], "confidence": "low", "submitted_by": "human:test",
        })
    monkeypatch.setattr(module, "PROPOSITIONS_PAR_PAGE", 2)
    client = _client(bases)
    html = client.get("/b/produit-x/-/propositions").text
    assert "en attente (3)" in html and "page 1 sur 2" in html
    assert "page suivante →" in html and "page=2" in html
    assert "page 2 sur 2" in client.get("/b/produit-x/-/propositions?page=9").text



# --- rév. 4.6 : Explorer, gouvernance, manques, chronologie, état, généalogie --------


def test_explorer_croise_texte_et_criteres_et_les_retire_d_un_clic(bases):
    client = _client(bases)
    html = client.get("/b/installation-x/-/explorer?q=attention&type=Procédure").text
    assert "<strong>1 fiche</strong>" in html and "Fiche F" in html
    import html as h
    assert 'query="attention"' in h.unescape(html) and 'type="Procédure"' in h.unescape(html)  # même requête par MCP
    # Chaque critère actif se retire sans toucher à l'autre.
    assert 'href="/b/installation-x/-/explorer?q=attention" title="Retirer ce critère"' in html
    assert client.get("/b/installation-x/-/explorer?q=attention&type=Incident").text.count('class="resultat-doc"') == 0
    # La répartition du résultat se clique.
    assert "/b/installation-x/-/explorer?q=attention&amp;type=Proc%C3%A9dure" in html


def test_explorer_cherche_aussi_les_propositions_en_attente(bases):
    from okf_hub.tools import propose_tool

    propose_tool.run(bases, {
        "base": "installation-x", "type": "observation", "concerns": "le serveur XZ42 redémarre",
        "content": "Constaté : XZ42 redémarre chaque nuit.", "sources": ["s"], "confidence": "low",
        "submitted_by": "human:test",
    })
    html = _client(bases).get("/explorer?q=xz42").text
    assert "Aucun document du <strong>corpus</strong> ne répond" in html
    assert "Dans les propositions en attente" in html and "le serveur XZ42 redémarre" in html


def test_un_filtre_incompris_est_signale(bases):
    client = _client(bases)
    html = client.get("/b/installation-x/-/propositions?statut=rejetees").text
    assert 'class="avis"' in html and "statut « rejetees » inconnu" in html
    html = client.get("/explorer?base=fantome&q=fiche").text
    assert "base « fantome » inconnue ou non ouverte : ignorée" in html
    assert "tri « n-importe » inconnu" in client.get("/explorer?tri=n-importe").text


def test_gouvernance_rend_les_regles_et_le_profil(bases):
    html = _client(bases).get("/b/installation-x/-/gouvernance").text
    assert "Règles de test." in html and 'kb_governance(base="installation-x")' in __import__("html").unescape(html)
    assert "« pièges » → warnings" in html and "Par environnement" in html
    assert "schema.yaml" in html


def test_manques_documents_attendus_et_questions(bases):
    from okf_hub.tools import propose_tool

    propose_tool.run(bases, {
        "base": "installation-x", "type": "question", "concerns": "quelle est la procédure de reprise ?",
        "content": "?", "sources": ["s"], "confidence": "low", "submitted_by": "human:test",
    })
    html = _client(bases).get("/b/installation-x/-/manques").text
    assert "<code>/rien.md</code>" in html and "quelle est la procédure de reprise ?" in html


def test_chronologie_de_la_semaine(bases):
    client = _client(bases)
    html = client.get("/chronologie").text
    assert "modification" in html and "installation-x" in html
    assert "semaine « x » non comprise" in client.get("/chronologie?semaine=x").text


def test_etat_dit_ce_que_voit_le_processus_et_masque_en_equipe(bases):
    html = _client(bases).get("/etat").text
    assert "Tous les bundles de <code>bases-dir</code> sont chargés." in html
    assert "sans remote" in html and "Journal" in html
    web = WebConfig.depuis(EQUIPE)
    equipe = _client(bases, web).get("/etat", headers={"X-Forwarded-User": "a"}).text
    assert "installation-x" not in equipe and "Journal <span" not in equipe


def test_a_propos_dit_le_mode_d_acces(bases):
    assert "Mode <strong>local</strong>" in _client(bases).get("/a-propos").text
    web = WebConfig.depuis(EQUIPE)
    assert "Mode <strong>équipe</strong>" in _client(bases, web).get("/a-propos", headers={"X-Forwarded-User": "a"}).text


def test_fiche_genealogie_impact_et_propositions_qui_la_nomment(bases, hub):
    from okf_hub.tools import propose_tool

    racine = hub[1] / "installation-x"
    (racine / "proposals" / "accepted").mkdir(parents=True, exist_ok=True)
    (racine / "proposals" / "accepted" / "prop-2026-10-01-abcd.md").write_text(
        "---\nid: prop-2026-10-01-abcd\nstatus: accepted\ntype: addition\nconcerns: la fiche G\n"
        "submitted-at: '2026-10-01T00:00:00Z'\nintegrated-into: [g.md]\n---\nTexte.\n", encoding="utf-8")
    propose_tool.run(bases, {
        "base": "installation-x", "type": "correction", "concerns": "erreur dans g.md",
        "content": "La fiche installation-x:/g.md se trompe.", "sources": ["s"], "confidence": "low",
        "submitted_by": "human:test",
    })
    html = _client(bases).get("/b/installation-x/g.md").text
    assert "prop-2026-10-01-abcd" in html.split("Histoire de la fiche")[1]
    assert "1 proposition en attente la mentionne" in html and "erreur dans g.md" in html
    assert "Si cette fiche est fausse" in html and "1 fiche</a> la cite" in html



# --- relecture visuelle du 29/10 : accueil, boîte, saut, santé, onglets ----------------


def test_pluriels():
    from okf_hub.web.commun import _nb

    assert (_nb(0, "document"), _nb(1, "document"), _nb(64, "document")) == ("0 document", "1 document", "64 documents")
    assert _nb(3, "proposition en attente") == "3 propositions en attente"
    assert _nb(2, "commit non poussé", "commits non poussés") == "2 commits non poussés"


def test_accueil_en_cartes_avec_l_aiguillage_commun(hub, make_bundle):
    regle = "Un constat : chez nous → base-b ; ailleurs → base-c."
    for nom, debut in (("base-b", "La base B."), ("base-c", "La base C.")):
        make_bundle(nom, name=nom, description=f"{debut} {regle}")
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    html = _client(reg).get("/").text
    assert html.count('class="carte b') == 2
    assert '<p class="phrase">La base B.</p>' in html  # entière : pas d'infobulle  # la règle n'est plus dans la carte
    assert "Où va une information ?" in html and "<code>base-b</code> et <code>base-c</code>" in html
    # Un arbre de décision : le contexte, puis chaque question et sa branche « oui ».
    arbre = html.split('<ol class="arbre">')[1].split("</ol>")[0]
    assert '<li class="contexte">Un constat</li>' in arbre
    assert '<span class="question">chez nous ?</span>' in arbre and '<span class="question">ailleurs ?</span>' in arbre
    assert arbre.index("chez nous") < arbre.index('<li class="non"') < arbre.index("ailleurs")
    assert 'class="puce-base b0" href="/b/base-b/"' in html
    assert regle in html  # le texte d'origine reste lisible


def test_boite_de_reception_des_propositions(bases, hub):
    from okf_hub.tools import propose_tool

    propose_tool.run(bases, {
        "base": "installation-x", "type": "question", "concerns": "second sujet", "content": "?",
        "sources": ["s"], "confidence": "low", "submitted_by": "human:test",
    })
    # Une proposition plus ancienne, datée explicitement : deux dépôts dans la même
    # seconde ne s'ordonnent que par leur identifiant, tiré au hasard.
    attente = hub[1] / "produit-x" / "proposals" / "pending"
    attente.mkdir(parents=True, exist_ok=True)
    (attente / "prop-2026-01-01-0001.md").write_text(
        "---\nid: prop-2026-01-01-0001\nstatus: pending\ntype: question\nconcerns: premier sujet\n"
        "submitted-at: '2026-01-01T00:00:00Z'\nsubmitted-by: human:test\n---\n?\n", encoding="utf-8")
    client = _client(bases)
    html = client.get("/propositions").text
    assert "<strong>2 propositions</strong>" in html
    assert html.index("premier sujet") < html.index("second sujet")  # les plus anciennes d'abord
    filtree = client.get("/propositions?base=installation-x").text
    assert "second sujet" in filtree and "premier sujet" not in filtree


def test_aller_a_une_base_un_document_ou_l_explorer(bases):
    client = _client(bases)
    def cible(q):
        return client.get(f"/aller?q={q}", follow_redirects=False).headers["location"]
    assert cible("produit-x") == "/b/produit-x/"
    assert cible("produit-x:/m/survivance.md") == "/b/produit-x/m/survivance.md"
    assert cible("Survivance") == "/b/produit-x/m/survivance.md"  # titre exact, unique
    assert cible("introuvable") == "/explorer?q=introuvable"


def test_voyant_de_sante(bases, hub):
    client = _client(bases)
    assert 'class="voyant bon"' in client.get("/").text
    (hub[1] / "cassee").mkdir()
    (hub[1] / "cassee" / "okf-bundle.yaml").write_text("name: [\n", encoding="utf-8")
    bases.scan()
    nouveau = _client(bases)  # un nouveau processus : pas de voyant en cache
    html = nouveau.get("/etat").text
    assert 'class="voyant critique"' in html and "1 bundle non chargé" in html


def test_onglets_avec_compteurs(bases):
    from okf_hub.tools import propose_tool

    propose_tool.run(bases, {
        "base": "installation-x", "type": "question", "concerns": "q", "content": "?",
        "sources": ["s"], "confidence": "low", "submitted_by": "human:test",
    })
    html = _client(bases).get("/b/installation-x/").text
    assert 'Propositions <span class="compte a-faire">1</span>' in html
    assert 'Manques <span class="compte">2</span>' in html  # /rien.md attendu, et la question
    assert 'aria-current="page"' in html


def test_figure_rayons_variables():
    from okf_hub.web.figure import disposer

    figure = disposer(["a", "b"], [("a", "b", 1, None)], rayons={"a": 40.0, "b": 10.0})
    assert {n.cle: n.rayon for n in figure.noeuds} == {"a": 40.0, "b": 10.0}


# --- relecture du 29/10 (2) : revue d'une proposition, page de document ---------


def _deposer(hub, base: str, ident: str, quand: str, **champs) -> None:
    import yaml

    corps = champs.pop("corps", "?")
    entete = {"id": ident, "status": "pending", "type": "question", "submitted-at": quand,
              "submitted-by": "human:test", **champs}
    dossier = hub[1] / base / "proposals" / "pending"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / f"{ident}.md").write_text(
        "---\n" + yaml.safe_dump(entete, allow_unicode=True) + "---\n" + corps + "\n", encoding="utf-8")


def test_revue_relations_fiche_visee_et_suivante(bases, hub):
    _deposer(hub, "produit-x", "prop-2026-01-01-0001", "2026-01-01T00:00:00Z", type="addition",
             concerns="m/survivance.md est incomplète", corps="Ajouter un exemple.")
    _deposer(hub, "produit-x", "prop-2026-01-02-0002", "2026-01-02T00:00:00Z", type="correction",
             concerns="Précision (corrige prop-2026-01-01-0001)", corps="Voir prop-2026-01-01-0001.")
    client = _client(bases)
    page = client.get("/b/produit-x/-/propositions/prop-2026-01-01-0001").text
    # Une correction en attente se lit avant de trancher la proposition qu'elle vise.
    assert "À lire avant de trancher" in page
    assert 'Corrigée par <a href="/b/produit-x/-/propositions/prop-2026-01-02-0002">' in page
    # La fiche nommée dans l'objet : liée dans le titre, et montrée à côté.
    assert '<a href="/b/produit-x/m/survivance.md">m/survivance.md</a>' in page
    assert '<h3><a href="/b/produit-x/m/survivance.md">Survivance</a></h3>' in page.split("Fiche visée")[1]
    assert 'rel="next" href="/b/produit-x/-/propositions/prop-2026-01-02-0002"' in page
    assert "1 sur 2" in page
    suite = client.get("/b/produit-x/-/propositions/prop-2026-01-02-0002").text
    assert 'rel="prev" href="/b/produit-x/-/propositions/prop-2026-01-01-0001"' in suite
    corps = suite.split('<article class="corps proposition">')[1].split("</article>")[0]
    assert 'href="/b/produit-x/-/propositions/prop-2026-01-01-0001"' in corps  # l'id cité devient un lien
    # La boîte range la correction sous ce qu'elle corrige, repliée dans son fil.
    boite = client.get("/propositions").text
    fil = boite.split('<details class="fil-corrections">')[1].split("</details>")[0]
    assert "1 réponse en attente : corrige" in fil and "Précision" in fil
    assert boite.index("est incomplète") < boite.index('<details class="fil-corrections">')
    assert "<th>Résolution</th>" not in client.get("/b/produit-x/-/propositions?statut=pending").text


def test_document_sommaire_permaliens_et_liens_en_bas(bases, hub):
    _deposer(hub, "installation-x", "prop-2026-01-01-0003", "2026-01-01T00:00:00Z",
             concerns="f.md : un piège manque")
    html = _client(bases).get("/b/installation-x/f.md").text
    assert '<nav class="toc" aria-label="Sommaire">' in html
    assert 'class="permalien" href="#pièges"' in html
    # Les propositions qui contestent la fiche passent avant son texte.
    assert html.index("1 proposition en attente la mentionne") < html.index('<article class="corps">')
    assert "2 liens depuis 2 fiches" in html  # g.md et o.md
    assert html.index('<article class="corps">') < html.index('id="liens"') < html.index("<aside")
    assert '<a href="#haut">' in html


def test_longue_liste_amont_repliee(bases, hub):
    elements = "".join(f"- [{i}](produit-x:/m/survivance.md)\n" for i in range(12))
    (hub[1] / "installation-x" / "knowledge" / "longue.md").write_text(
        "---\ntype: Procédure\n---\n# Longue\n\nTexte.\n\n# Amont\n\n" + elements + "\n# Suite\n\n- a\n",
        encoding="utf-8")
    html = _client(bases).get("/b/installation-x/longue.md").text
    assert '<details class="repli"><summary>12 éléments</summary>' in html
    assert html.count('<details class="repli">') == 1  # la section suivante n'est pas repliée


def test_saut_palette_et_titre_exact_d_abord(bases, hub):
    (hub[1] / "produit-x" / "knowledge" / "m" / "survivance-longue.md").write_text(
        "---\ntype: Concept\n---\n# Survivance longue\n\nSurvivance, survivance, survivance.\n", encoding="utf-8")
    client = _client(bases)

    def cible(q):
        return client.get(f"/aller?q={q}", follow_redirects=False)

    # Un titre exact unique mène au document, même si d'autres titres le contiennent.
    assert cible("Survivance").headers["location"] == "/b/produit-x/m/survivance.md"
    palette = cible("surviv")
    assert palette.status_code == 200 and '<div class="palette-groupes">' in palette.text
    groupes = palette.text.split('<div class="palette-groupes">')[1]
    assert groupes.index(">Fiches<") < groupes.index(">Survivance<") < groupes.index(">Survivance longue<")  # le plus court d'abord
    assert groupes.index(">Survivance longue<") < groupes.index("Chercher « surviv »")
    # Ce que lit la palette : la liste, toujours, même pour un titre exact unique.
    liste = client.get("/aller?q=Survivance&liste=1", follow_redirects=False)
    assert liste.status_code == 200 and ">Survivance<" in liste.text
    assert ">Bases<" in client.get("/aller?q=&liste=1").text
    # L'Explorer, lui, place le titre exact avant le document qui répète le mot.
    html = client.get("/explorer?q=survivance&base=produit-x&base=installation-x").text
    assert html.index('href="/b/produit-x/m/survivance.md"') < html.index('href="/b/produit-x/m/survivance-longue.md"')


def test_synchro_en_echec_signalee_par_instance_avec_sa_cause(bases):
    import datetime as dt

    maintenant = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    with bases.config.log_file.open("a", encoding="utf-8") as f:
        f.write(f"{maintenant} [pid:7] WARNING sync remote 'produit-x' : remote injoignable, "
                "ignorée pour ce démarrage (error: cannot run ssh)\n")
        # Hors de la fenêtre de 24 heures : ne compte plus.
        f.write("2020-01-01T00:00:00.000Z [pid:7] WARNING sync remote 'installation-x' : remote injoignable, "
                "ignorée pour ce démarrage (x)\n")
    html = _client(bases).get("/etat").text
    # À surveiller, pas à corriger : la base sert sa copie locale, qui fait foi.
    assert 'class="voyant attention"' in html and ">synchro remote en échec (1 instance)" in html
    section = html.split('id="synchro"')[1].split("</section>")[0]
    assert "Processus 7" in section and "ssh est absent" in section and "Que faire" in section
    assert "produit-x" in section and "installation-x" not in section


# --- relecture du 29/10 (3) : lisibilité, comptes, poids des pages ----------------


def test_sujet_de_commit_lisible():
    from okf_hub.web.pages import sujet_lisible

    assert sujet_lisible("regen: vues régénérées après 5 résolution(s)") == "Vues régénérées après 5 résolutions"
    assert sujet_lisible("regen: vues régénérées après 1 résolution(s)") == "Vues régénérées après 1 résolution"
    rejet = "reject: prop-2026-01-01-abcd — Rejet de prop-2026-01-01-abcd : déjà consigné"
    assert sujet_lisible(rejet) == "prop-2026-01-01-abcd — déjà consigné"  # l'id reste, une fois
    assert sujet_lisible(rejet, garder_id=False) == "Déjà consigné"  # la page le lie déjà


def test_dossier_pagine_et_index_long_renvoye_a_sa_page(bases, hub, monkeypatch):
    from okf_hub.web import app as module

    dossier = hub[1] / "installation-x" / "knowledge" / "lot"
    dossier.mkdir()
    for i in range(3):
        (dossier / f"d{i}.md").write_text(f"---\ntype: Incident\n---\n# D{i}\n", encoding="utf-8")
    (dossier / "index.md").write_text("# Lot\n\n" + "Une ligne d'index.\n" * 20, encoding="utf-8")
    monkeypatch.setattr(module, "REPERTOIRE_PAR_PAGE", 2)
    monkeypatch.setattr(module, "INDEX_EN_LIGNE_MAX", 100)
    client = _client(bases)
    html = client.get("/b/installation-x/lot/").text
    assert "page 1 sur 2" in html and 'href="/b/installation-x/-/explorer?sous=lot"' in html
    assert "Une ligne d'index." not in html and 'href="/b/installation-x/lot/index.md"' in html
    assert "fiches 3 à 3 sur 3" in client.get("/b/installation-x/lot/?page=2").text


def test_entrants_comptent_les_fiches_comme_l_impact(bases, hub):
    (hub[1] / "installation-x" / "knowledge" / "index.md").write_text("# Index\n\n- [F](f.md)\n", encoding="utf-8")
    html = _client(bases).get("/b/installation-x/f.md").text
    # L'index cite sans dépendre : listé à part, compté ni dans les fiches ni dans l'impact.
    assert "3 liens depuis 2 fiches et 1 sommaire ou journal" in html
    assert "Citée par 2 fiches" in html and "2 fiches</a> la citent" in html


def test_etiquettes_d_arcs_ecartees():
    import math

    from okf_hub.web.figure import ECART_ETIQUETTES, disposer, epaisseur_de

    # Deux arcs qui se croisent au centre : leurs étiquettes ne s'y superposent pas.
    figure = disposer(["a", "b", "c", "d"], [("a", "c", 10, None), ("b", "d", 10, None)], ordonne=False)
    (x1, y1), (x2, y2) = [(a.etiquette_x, a.etiquette_y) for a in figure.arcs]
    assert math.hypot(x1 - x2, y1 - y2) >= ECART_ETIQUETTES
    # Une échelle absolue, marquée : 440 références se voient à côté de 6.
    assert epaisseur_de(440) > 4 * epaisseur_de(6) and epaisseur_de(10**6) == 14.0


def test_accueil_ordre_des_cartes(hub, make_bundle):
    make_bundle("base-a", name="base-a", description="La base A.").doc("x.md", "# X\n").doc("y.md", "# Y\n")
    make_bundle("base-z", name="base-z", description="La base Z.")
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    client = _client(reg)
    taille = client.get("/?ordre=taille").text
    assert taille.index("/b/base-a/") < taille.index("/b/base-z/")
    assert "ordre « n'importe » inconnu" in __import__("html").unescape(client.get("/?ordre=n'importe").text)


# --- relecture du 29/10 (4) : affordances, glossaire, dates -----------------------


def test_dates_relatives_et_format_unique():
    import datetime as dt

    from okf_hub.web.commun import _date, _quand

    maintenant = dt.datetime(2026, 10, 29, 15, 0, tzinfo=dt.timezone.utc)
    recent = str(_quand(maintenant - dt.timedelta(hours=3), maintenant))
    assert ">il y a 3 h</time>" in recent and 'datetime="' in recent and 'title="' in recent
    ancien = str(_quand(maintenant - dt.timedelta(days=30), maintenant))
    assert ">29 sept. 2026</time>" in ancien  # au-delà de sept jours, la date
    assert _date(dt.date(2026, 5, 2)) == "2 mai 2026"


def test_resume_coupe_a_une_fin_de_phrase():
    from okf_hub.web.commun import resume

    texte = "Première phrase assez longue pour compter. " + "Seconde phrase qui déborde " * 10
    assert resume(texte, 60) == "Première phrase assez longue pour compter. …"
    assert resume("Court.", 60) == "Court."
    assert resume("un " * 50, 30).endswith(" …") and len(resume("un " * 50, 30)) <= 32  # à un mot, en dernier recours


def test_accueil_glossaire_voyant_et_commande(bases, hub):
    from okf_hub.tools import propose_tool

    propose_tool.run(bases, {
        "base": "installation-x", "type": "question", "concerns": "q", "content": "?",
        "sources": ["s"], "confidence": "low", "submitted_by": "human:test",
    })
    (hub[1] / "installation-x" / "knowledge" / "modifie.md").write_text("# M\n", encoding="utf-8")
    html = _client(bases).get("/").text
    # Les termes mènent à leur définition, et la définition existe à cette ancre.
    assert 'class="terme" href="/a-propos#g-a-trancher"' in html and 'href="/a-propos#glossaire">Glossaire</a>' in html
    a_propos = _client(bases).get("/a-propos").text
    assert 'id="g-a-trancher"' in a_propos and 'id="g-gouvernance-brouillon"' in a_propos
    # Une carte qui a de quoi trancher donne sa commande de revue.
    assert 'data-copier="okf-review inventory installation-x"' in html
    # Le voyant à surveiller est un badge qui compte ses points.
    assert 'class="voyant attention"' in html and '<strong class="nombre">' in html
