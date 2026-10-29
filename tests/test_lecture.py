"""Moteur de lecture (§ 3.5, § 3.6, § 5 bis.1, rév. 4.5) : liens, profil, modèle,
fraîcheur, graphe des bases."""

from __future__ import annotations

import datetime as dt
import os
import time

import pytest

from conftest import git

from okf_hub.config import HubConfig
from okf_hub.lecture import liens
from okf_hub.lecture.confiance import COMMIT, SOURCES, VERIFIED, confiance, date_declaree, mois_avant
from okf_hub.lecture.graphe import analyser, cycles
from okf_hub.lecture.modele import BASE_ABSENTE, CASSE, DOC, REP, Lecteur, resoudre
from okf_hub.lecture.profil import charger_profil
from okf_hub.mdutil import parse_headings
from okf_hub.registry import Registry


def _registre(hub) -> Registry:
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    return reg


# --- classement des cibles ------------------------------------------------------


@pytest.mark.parametrize(
    ("brut", "sorte", "base", "chemin", "repertoire"),
    [
        ("/a/b.md", liens.INTERNE, "x", "a/b.md", False),
        ("b.md", liens.INTERNE, "x", "d/b.md", False),
        ("../e.md", liens.INTERNE, "x", "e.md", False),
        ("/a/", liens.INTERNE, "x", "a", True),
        ("/", liens.INTERNE, "x", "", True),
        ("produit-x:/m/s.md#sec", liens.INTERBASE, "produit-x", "m/s.md", False),
        ("produit-x:/m/", liens.INTERBASE, "produit-x", "m", True),
        ("/d%C3%A9cision.md", liens.INTERNE, "x", "décision.md", False),
        ("<b.md>", liens.INTERNE, "x", "d/b.md", False),
    ],
)
def test_classement_des_cibles_du_corpus(brut, sorte, base, chemin, repertoire):
    cible = liens.classer(brut, "x", "d/doc.md")
    assert (cible.sorte, cible.base, cible.chemin, cible.repertoire) == (sorte, base, chemin, repertoire)


@pytest.mark.parametrize(
    ("brut", "sorte"),
    [
        ("https://exemple.org/p", liens.EXTERNE),
        ("mailto:a@b.c", liens.EXTERNE),
        ("#ancre", liens.ANCRE),
        ("javascript:alert(1)", liens.REFUSE),
        ("data:text/html,x", liens.REFUSE),
        ("file:///etc/passwd", liens.REFUSE),
        ("//hote/chemin", liens.REFUSE),
        ("ftp://hote/x", liens.REFUSE),
        ("../../hors.md", liens.HORS_CORPUS),
        ("/../hors.md", liens.HORS_CORPUS),
        ("produit-x:/../hors.md", liens.HORS_CORPUS),
    ],
)
def test_cibles_hors_corpus_et_schemas(brut, sorte):
    assert liens.classer(brut, "x", "d/doc.md").sorte == sorte


def _extraire(corps: str):
    return liens.extraire(corps, parse_headings(corps), "x", "doc.md")


def test_extraction_ignore_le_code_et_les_images():
    corps = (
        "# Titre\n\n[vrai](/a.md) et `[exemple](/faux.md)` et ![img](/i.png)\n\n"
        "```\n[dans un bloc](/bloc.md)\n```\n"
    )
    assert [l.cible.chemin for l in _extraire(corps).liens] == ["a.md"]


def test_extraction_label_sur_deux_lignes_et_liens_automatiques():
    corps = "Voir [un libellé\nsur deux lignes](/a.md), <https://exemple.org> et <qui@exemple.org>.\n"
    cibles = [l.cible.brut for l in _extraire(corps).liens]
    assert cibles == ["/a.md", "https://exemple.org", "mailto:qui@exemple.org"]


def test_extraction_definitions_de_liens_et_notes():
    corps = "Texte[^s1] et[^def].\n\n[ref]: /cible.md\n\n[^def]: définie ici\n"
    extraction = _extraire(corps)
    assert [l.cible.chemin for l in extraction.liens] == ["cible.md"]
    assert extraction.notes == {"s1", "def"}
    assert extraction.notes_definies == {"def"}


def test_extraction_attribue_la_section_et_la_ligne():
    corps = "Préambule [p](/p.md)\n\n# Amont\n\nDécline [m](produit-x:/m.md).\n"
    trouves = _extraire(corps).liens
    assert [(l.section, l.ligne) for l in trouves] == [(None, 0), ("amont", 4)]


# --- profil ---------------------------------------------------------------------


PROFIL = """
required:
  - name: type
    type: string
  - name: provenance
    type: enum
    role: provenance
    values:
      interne: {label: Document interne, evidence: resource}
      terrain: {label: Constat d'usage}
optional:
  - name: aliases
    role: aliases
  - name: debut
    role: start
  - name: bizarre
    role: inexistant
types:
  Procédure: {description: Un mode opératoire, paths: [/exploitation/]}
facets:
  separator: ":"
  env: {label: Environnement, values: {prod: Production, test: Test}}
sections:
  Pièges: warnings
  Amont: upstream
  Autre: role-inconnu
views:
  - title: Pièges
    filter: {section: warnings}
    group: {facet: env}
  - title: Mauvaise
    kind: camembert
cle-propre-a-la-base: ignoree
"""


def test_profil_lu_avec_ses_roles(tmp_path):
    chemin = tmp_path / "schema.yaml"
    chemin.write_text(PROFIL, encoding="utf-8")
    profil = charger_profil(chemin)
    assert profil.champ_du_role("provenance").valeurs["interne"].preuve == "resource"
    assert profil.champ_du_role("aliases").nom == "aliases"
    assert profil.facette_de("env:prod") == ("env", "prod")
    assert profil.facette_de("libre") is None
    assert profil.role_de_section("pièges") == "warnings"
    assert [v.titre for v in profil.vues] == ["Pièges"]
    assert profil.vues[0].groupe == ("facet", "env")
    assert profil.enrichi
    # Ce qui est mal formé est signalé et ignoré, jamais le profil entier.
    assert any("inexistant" in a for a in profil.avertissements)
    assert any("role-inconnu" in a for a in profil.avertissements)
    assert any("camembert" in a for a in profil.avertissements)


def test_un_schema_v0_est_un_profil_sans_enrichissement(tmp_path):
    chemin = tmp_path / "schema.yaml"
    chemin.write_text("required:\n  - name: title\n    type: string\n", encoding="utf-8")
    profil = charger_profil(chemin)
    assert not profil.enrichi and not profil.avertissements
    assert profil.facette_de("app:x") is None  # sans séparateur déclaré, rien n'est découpé


def test_un_profil_illisible_ne_leve_jamais(tmp_path):
    chemin = tmp_path / "schema.yaml"
    chemin.write_text("required: [\n", encoding="utf-8")
    assert charger_profil(chemin).avertissements
    assert charger_profil(None).avertissements == ()


# --- modèle et cache --------------------------------------------------------------


def _base(make_bundle, nom="base-a", docs=None, **manifeste):
    b = make_bundle(nom, git_init=False, create_corpus=False, name=nom, **manifeste)
    for chemin, texte in (docs or {}).items():
        b.doc(chemin, texte)
    b.init_git()
    return b


def test_modele_documents_repertoires_et_reserves(hub, make_bundle):
    _base(make_bundle, docs={
        "index.md": "# Sommaire\n",
        "a/b/c.md": "---\ntype: Procédure\ntags: [env:prod]\n---\n# C\n",
        "a/x.md": "---\ntitle: X\n---\ntexte\n",
    })
    modele = Lecteur(_registre(hub)).modele("base-a")
    assert set(modele.docs) == {"index.md", "a/b/c.md", "a/x.md"}
    assert modele.repertoires == {"", "a", "a/b"}
    assert [d.chemin for d in modele.concepts()] == ["a/b/c.md", "a/x.md"]
    assert modele.docs["a/b/c.md"].titre == "C" and modele.docs["a/x.md"].titre == "X"
    assert modele.trouver("a/b/c") == (DOC, "a/b/c.md")  # identifiant OKF, sans extension
    assert modele.trouver("a", repertoire=True) == (REP, "a")
    assert modele.contenu("a") == (["a/b"], [modele.docs["a/x.md"]])


def test_cache_invalide_par_une_seconde_modification(hub, make_bundle):
    """Le cas que `git status` ne voit pas : un fichier déjà modifié qui l'est encore."""
    b = _base(make_bundle, docs={"d.md": "---\ntype: A\n---\n"})
    lecteur = Lecteur(_registre(hub), intervalle=0)
    chemin = b.root / "knowledge" / "d.md"
    chemin.write_text("---\ntype: B\n---\n", encoding="utf-8")
    assert lecteur.modele("base-a").docs["d.md"].type == "B"
    chemin.write_text("---\ntype: C\n---\n", encoding="utf-8")
    os.utime(chemin, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    assert lecteur.modele("base-a").docs["d.md"].type == "C"


def test_cache_garde_le_modele_tant_que_rien_ne_change(hub, make_bundle):
    _base(make_bundle, docs={"d.md": "x\n"})
    lecteur = Lecteur(_registre(hub), intervalle=0)
    premier = lecteur.modele("base-a")
    assert lecteur.modele("base-a") is premier
    (hub[1] / "base-a" / "knowledge" / "nouveau.md").write_text("y\n", encoding="utf-8")
    second = lecteur.modele("base-a")
    assert second is not premier and "nouveau.md" in second.docs
    assert second.generation > premier.generation


def test_cache_espace_ses_verifications(hub, make_bundle):
    _base(make_bundle, docs={"d.md": "x\n"})
    lecteur = Lecteur(_registre(hub), intervalle=3600)
    premier = lecteur.modele("base-a")
    (hub[1] / "base-a" / "knowledge" / "nouveau.md").write_text("y\n", encoding="utf-8")
    assert lecteur.modele("base-a") is premier  # pas re-vérifié avant l'intervalle


def test_resolution_et_liens_entrants_entre_bases(hub, make_bundle):
    _base(make_bundle, "produit-x", docs={"m/s.md": "# S\n", "m/index.md": "# M\n"})
    _base(make_bundle, "installation-x", docs={
        "f.md": "# Amont\n\n[s](produit-x:/m/s.md) [rep](produit-x:/m/) [cassé](produit-x:/rien.md)\n"
                "[absente](autre-base:/x.md) [moi](/f.md)\n",
    })
    lecteur = Lecteur(_registre(hub))
    modeles = lecteur.modeles()
    sortes = [resoudre(l.cible, modeles).sorte for l in modeles["installation-x"].docs["f.md"].liens]
    assert sortes == [DOC, REP, CASSE, BASE_ABSENTE, DOC]
    entrants = lecteur.entrants("produit-x", "m/s.md")
    assert [(e.base, e.chemin, e.lien.section) for e in entrants] == [("installation-x", "f.md", "amont")]
    # Un répertoire vaut son index ; un lien vers soi-même n'est pas un lien entrant.
    assert [e.chemin for e in lecteur.entrants("produit-x", "m/index.md")] == ["f.md"]
    assert lecteur.entrants("installation-x", "f.md") == ()


def test_une_base_installee_apres_coup_resout_les_references(hub, make_bundle):
    _base(make_bundle, "installation-x", docs={"f.md": "[s](produit-x:/s.md)\n"})
    reg = _registre(hub)
    lecteur = Lecteur(reg, intervalle=0)
    assert lecteur.entrants("installation-x", "f.md") == ()
    _base(make_bundle, "produit-x", docs={"s.md": "# S\n"})
    reg.scan()
    assert [e.chemin for e in lecteur.entrants("produit-x", "s.md")] == ["f.md"]


# --- fraîcheur et confiance ---------------------------------------------------------


def test_fraicheur_dans_l_ordre_declare():
    assert date_declaree({"verified": [{"by": "human:a", "at": "2026-01-02T00:00:00Z"}],
                          "sources": [{"last_modified": "2026-05-01T00:00:00Z"}]})[1] == VERIFIED
    # Un mapping seul vaut liste à un élément (OKF § 5.2).
    assert date_declaree({"verified": {"by": "x", "at": dt.date(2026, 1, 2)}})[1] == VERIFIED
    assert date_declaree({"generated": {"by": "process:x"}, "sources": [
        {"last_modified": "2025-01-01T00:00:00Z"}, {"last_modified": dt.datetime(2026, 3, 1)}]}) == (
        dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc), SOURCES)
    assert date_declaree({"title": "rien"}) is None


def test_fraicheur_retombe_sur_le_dernier_commit(hub, make_bundle):
    _base(make_bundle, docs={"d.md": "# D\n"})
    lecteur = Lecteur(_registre(hub))
    modele = lecteur.modele("base-a")
    fraicheur = lecteur.fraicheur(modele, modele.docs["d.md"])
    assert fraicheur.origine == COMMIT
    assert lecteur.dates_commit("base-a")["d.md"] == fraicheur.date


def test_confiance_palier_statut_peremption():
    maintenant = dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)
    c = confiance({"verified": [{"by": "agent/1"}, {"by": "human:a"}], "status": "draft",
                   "stale_after": "2026-09-01T00:00:00Z", "generated": {"by": "process:gen"}}, maintenant)
    assert (c.palier, c.statut, c.perime, c.genere) == ("humain", "draft", True, True)
    c = confiance({"verified": {"by": "agent/1"}, "status": "stable"}, maintenant)
    assert (c.palier, c.statut, c.perime, c.genere) == ("machine", None, False, False)


def test_mois_avant_fin_de_mois():
    assert mois_avant(dt.datetime(2026, 3, 31), 1) == dt.datetime(2026, 2, 28)
    assert mois_avant(dt.datetime(2026, 1, 15), 13) == dt.datetime(2024, 12, 15)


# --- graphe des bases ----------------------------------------------------------------


def test_cycles_des_cites():
    assert cycles({"a": ("b",), "b": ("c",), "c": ("a",), "d": ("a",)}) == [("a", "b", "c")]
    assert cycles({"a": ("b",), "b": ()}) == []


def test_graphe_signale_ce_qui_sort_des_cites(hub, make_bundle):
    _base(make_bundle, "produit-x", docs={"s.md": "# S\n"}, cites=["installation-x"])
    _base(make_bundle, "installation-x", docs={
        "f.md": "[s](produit-x:/s.md) [r](referentiel-x:/o.md) [c](/rien.md)\n"}, cites=["produit-x"])
    _base(make_bundle, "libre", docs={"g.md": "[s](produit-x:/s.md)\n"})
    graphe = analyser(Lecteur(_registre(hub)), ["installation-x", "libre", "produit-x"])
    assert graphe.cycles == (("installation-x", "produit-x"),)
    assert graphe.hors_cites == (("installation-x", "referentiel-x", 1),)  # `libre` ne déclare rien
    assert graphe.hors_perimetre == (("installation-x", "referentiel-x", 1),)
    assert graphe.cassees == {"installation-x": 1}
    assert len(graphe.avertissements) == 2


def test_graphe_ne_revele_pas_une_base_hors_du_perimetre(hub, make_bundle):
    _base(make_bundle, "fermee", docs={"s.md": "# S\n"})
    _base(make_bundle, "ouverte", docs={"f.md": "[s](fermee:/s.md)\n"})
    graphe = analyser(Lecteur(_registre(hub)), ["ouverte"])
    assert graphe.bases == ("ouverte",) and graphe.aretes == ()
    assert graphe.hors_perimetre == (("ouverte", "fermee", 1),)
    assert graphe.cassees == {}


def test_cites_mal_forme_ignore_avec_avertissement(hub, make_bundle):
    _base(make_bundle, docs={"d.md": "x\n"}, cites="pas-une-liste")
    _base(make_bundle, "base-b", docs={"d.md": "x\n"}, cites=["base-b", "base-a"])
    reg = _registre(hub)
    assert reg.get("base-a").manifest.cites is None
    assert reg.get("base-b").manifest.cites == ("base-a",)
    assert any("cites" in w for _, w in reg.last_report.compat_warnings)


def test_le_moteur_ne_cree_rien_dans_les_depots(hub, make_bundle):
    """Lecture seule : ni fichier non suivi, ni verrou, ni modification."""
    b = _base(make_bundle, docs={"d.md": "[x](/e.md)\n", "e.md": "# E\n"})
    lecteur = Lecteur(_registre(hub))
    modele = lecteur.modele("base-a")
    lecteur.entrants("base-a", "e.md")
    lecteur.dates_commit("base-a")
    lecteur.fraicheur(modele, modele.docs["d.md"])
    lecteur.historique("base-a")
    assert git(b.root, "status", "--porcelain", "--ignored") == ""
