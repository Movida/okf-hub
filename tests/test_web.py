"""Interface web de consultation (§ 5 bis.2 et § 5 bis.4, rév. 4.5)."""

from __future__ import annotations

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
    assert rep.headers["x-content-type-options"] == "nosniff"


def test_toute_ecriture_est_refusee(bases):
    client = _client(bases)
    for url in ("/", "/b/installation-x/f.md", "/trouver"):
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
    assert "Citée par <code>referentiel-x</code>" in html


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


def test_trouver_et_effectifs(bases):
    client = _client(bases)
    html = client.get("/b/installation-x/-/trouver?section=warnings").text
    assert "1 document(s)" in html and "Fiche F" in html
    html = client.get("/trouver?type=Concept").text
    assert "/b/produit-x/m/survivance.md" in html
    assert client.get("/trouver?mois=abc").status_code == 400


def test_vues_declarees(bases):
    client = _client(bases)
    liste = client.get("/b/installation-x/-/vues/0").text
    assert "prod" in liste and "Fiche F" in liste
    assert "2026-05" in client.get("/b/installation-x/-/vues/1").text
    graphe = client.get("/b/installation-x/-/vues/2").text
    assert "<svg" in graphe and "/b/installation-x/g.md" in graphe
    fraicheur = client.get("/b/installation-x/-/vues/3").text
    # f.md (provenance à preuve, source de 2020) est listée ; g.md n'a pas de preuve à confronter.
    assert "Fiche F" in fraicheur and "/b/installation-x/g.md" not in fraicheur
    assert client.get("/b/installation-x/-/vues/9").status_code == 404


def test_recherche_plein_texte(bases):
    html = _client(bases).get("/recherche?q=attention").text
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
    assert "1 proposition(s) en attente" in accueil


def test_graphe_des_bases(bases):
    html = _client(bases).get("/graphe").text
    assert "<svg" in html and "installation-x" in html and "produit-x" in html


def test_l_interface_n_ecrit_rien(bases, hub):
    client = _client(bases)
    for url in ("/", "/b/installation-x/f.md", "/b/installation-x/-/journal", "/graphe",
                "/b/installation-x/-/vues/3", "/recherche?q=x"):
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
    assert "referentiel-x" not in html.split("Liens entrants")[1].split("Liens sortants")[0]
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
    html = _client(bases).get("/recherche?q=messagerie&base=installation-x").text
    assert "frontmatter · <code>description</code>" in html
    assert "frontmatter · <code>title</code>" not in html  # déjà l'intitulé du résultat
    assert "Voir la <mark>messagerie</mark> et son guide." in html
    assert "**" not in html.split('class="texte-extrait"')[1] and "|---|" not in html
    assert "1 document(s)</strong>" in html


def test_recherche_pagine_une_base_et_resume_plusieurs(bases, hub, monkeypatch):
    from okf_hub.web import app as module

    monkeypatch.setattr(module, "RECHERCHE_PAR_PAGE", 1)
    monkeypatch.setattr(module, "RECHERCHE_APERCU", 1)
    client = _client(bases)
    une = client.get("/recherche?q=md&base=installation-x&page=99").text
    assert "page 2 sur 2" in une and "← précédents" in une  # au-delà : la dernière page
    plusieurs = client.get("/recherche?q=md").text
    assert "Les 2 résultats de installation-x →" in plusieurs


def test_trouver_aide_suggestions_et_exemples(bases):
    html = _client(bases).get("/b/installation-x/-/trouver").text
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
    assert "plus anciennes →" in html and "?page=2" in html
    assert "page 2 sur 2" in client.get("/b/produit-x/-/propositions?page=9").text
