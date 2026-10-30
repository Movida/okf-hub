"""kb_find (§ 5.8, rév. 4.5) : critères structurés, total exact, effectifs."""

from __future__ import annotations

import pytest

from okf_hub.config import HubConfig
from okf_hub.errors import INVALID_INPUT, NOT_FOUND, UNKNOWN_BASE, ToolError
from okf_hub.registry import Registry
from okf_hub.tools import find_tool, rescan_tool

PROFIL = """
required:
  - name: type
  - name: provenance
    role: provenance
    values: {interne: {evidence: resource}, terrain: {}}
types:
  Procédure: {}
  Incident: {}
  Décision: {}
facets:
  separator: ":"
  env: {values: [prod, test]}
sections:
  Pièges: warnings
  Amont: upstream
"""


@pytest.fixture
def bases(hub, make_bundle):
    produit = make_bundle("produit-x", git_init=False, create_corpus=False, name="produit-x",
                          governance={"rules": "./GOVERNANCE.md"})
    produit.doc("m/survivance.md", "---\ntype: Concept\n---\n# Survivance\n")
    produit.init_git()

    installation = make_bundle(
        "installation-x", git_init=False, create_corpus=False, name="installation-x",
        governance={"rules": "./GOVERNANCE.md", "frontmatter-schema": "./schema.yaml"},
    )
    installation.schema(PROFIL)
    installation.doc("index.md", "# Sommaire\n\n[p](/exploitation/p1.md)\n")
    installation.doc("exploitation/p1.md",
                     "---\ntype: Procédure\nprovenance: interne\nresource: doc\ntags: [env:prod]\n"
                     "sources: [{id: s, last_modified: 2020-01-01T00:00:00Z}]\n---\n"
                     "# Procédure\n\n# Pièges\n\nAttention.\n\n# Amont\n\n[s](produit-x:/m/survivance.md)\n")
    installation.doc("exploitation/p2.md",
                     "---\ntype: Procédure\nprovenance: terrain\ntags: [env:test, env:recette]\n---\n"
                     "Voir [s](produit-x:/m/survivance.md).\n")
    installation.doc("exploitation-ancien/p3.md", "---\ntype: Procédure\n---\n# Procédure\n")
    installation.doc("incidents/i1.md", "---\ntype: Incident\n---\n## Pièges\n")
    installation.init_git()
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    return reg


def _run(reg, **args):
    return find_tool.run(reg, args)


def test_liste_avec_total_exact(bases):
    sortie = _run(bases, base="installation-x", type="Procédure")
    assert sortie.splitlines()[0] == "3 document(s) dans 'installation-x' — critères : type=Procédure"
    assert "- exploitation/p1.md — Procédure [Procédure]" in sortie
    # Les sommaires ne sont pas des concepts.
    assert "index.md" not in sortie


def test_zero_est_une_reponse_et_rappelle_les_types(bases):
    sortie = _run(bases, base="installation-x", type="Procedure")
    assert sortie.startswith("0 document(s) dans 'installation-x'")
    assert "types présents : Procédure (3), Incident (1)" in sortie


def test_le_rappel_des_types_se_tait_si_le_type_existe_ailleurs(bases):
    sortie = _run(bases, base="*", type="Concept")
    assert sortie.startswith("1 document(s) dans 2 base(s)")
    assert "types présents" not in sortie


def test_criteres_cumules(bases):
    assert "1 document(s)" in _run(bases, base="installation-x", tags=["env:prod"], type="Procédure")
    assert "exploitation/p2.md" in _run(bases, base="installation-x", fields={"provenance": "terrain"})
    sans_evidence = _run(bases, base="installation-x", type="Procédure", fields={"resource": False})
    assert "2 document(s)" in sans_evidence and "p1.md" not in sans_evidence
    assert "1 document(s)" in _run(bases, base="installation-x", fields={"resource": True})


def test_section_par_titre_ou_par_role(bases):
    par_role = _run(bases, base="installation-x", section="warnings")
    assert "2 document(s)" in par_role and "incidents/i1.md" in par_role
    assert _run(bases, base="installation-x", section="PIÈGES") == par_role.replace(
        "section=warnings", "section=PIÈGES")
    sans = _run(bases, base="installation-x", type="Procédure", without_section="warnings")
    assert "2 document(s)" in sans and "p1.md" not in sans


def test_under_est_un_repertoire(bases):
    sortie = _run(bases, base="installation-x", under="exploitation")
    assert "2 document(s)" in sortie and "exploitation-ancien" not in sortie
    assert "1 document(s)" in _run(bases, base="installation-x", under="/exploitation-ancien/")


def test_links_to_entre_bases_marque_l_amont(bases):
    sortie = _run(bases, base="installation-x", links_to="produit-x:/m/survivance.md")
    assert "2 document(s)" in sortie
    lignes = {l.split(" — ")[0]: l for l in sortie.splitlines() if l.startswith("- ")}
    assert lignes["- exploitation/p1.md"].endswith("— amont")
    assert not lignes["- exploitation/p2.md"].endswith("— amont")


def test_links_to_dans_une_seule_base_accepte_un_chemin(bases):
    # index.md cite p1, mais un sommaire n'est pas listé : aucun concept ne le cite.
    assert _run(bases, base="installation-x", links_to="/exploitation/p1.md").startswith("0 document(s)")


def test_links_to_fautif(bases):
    with pytest.raises(ToolError) as exc:
        _run(bases, base="*", links_to="exploitation/p1.md")
    assert exc.value.code == INVALID_INPUT
    with pytest.raises(ToolError) as exc:
        _run(bases, base="installation-x", links_to="produit-x:/rien.md")
    assert exc.value.code == NOT_FOUND
    with pytest.raises(ToolError) as exc:
        _run(bases, base="installation-x", links_to="fantome:/x.md")
    assert exc.value.code == UNKNOWN_BASE


def test_older_than_months_donne_la_date_et_son_origine(bases):
    sortie = _run(bases, base="installation-x", older_than_months=12)
    assert "- exploitation/p1.md — Procédure [Procédure] — 2020-01-01 (sources)" in sortie
    assert "p2.md" not in sortie  # dernier commit : aujourd'hui


def test_count_by_type_facette_champ_section(bases):
    types = _run(bases, base="installation-x", count_by="type")
    assert "- Procédure : 3" in types and "- Décision : 0" in types  # déclaré, effectif nul
    env = _run(bases, base="installation-x", count_by="env")
    assert "par facette « env »" in env
    assert "- recette : 1 (hors vocabulaire déclaré)" in env and "- (absent) : 2" in env
    assert "- terrain : 1" in _run(bases, base="installation-x", count_by="provenance")
    assert "- Pièges : 2" in _run(bases, base="installation-x", count_by="section")


def test_plafond_global_et_total_exact(hub, make_bundle):
    b = make_bundle("grosse", git_init=False, create_corpus=False, name="grosse")
    for i in range(30):
        b.doc(f"d{i:02}.md", "---\ntype: T\n---\n")
    b.init_git()
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    sortie = _run(reg, base="grosse", max_results=5)
    assert sortie.startswith("30 document(s)")
    assert sum(1 for l in sortie.splitlines() if l.startswith("- ")) == 5
    assert "25 document(s) non affiché(s)" in sortie


def test_entrees_invalides(bases):
    for args in ({"tags": "x" * 0}, {"fields": {"x": None}}, {"older_than_months": 0},
                 {"count_by": ""}, {"type": " "}):
        with pytest.raises(ToolError) as exc:
            _run(bases, base="installation-x", **args)
        assert exc.value.code == INVALID_INPUT, args


def test_le_schema_n_annonce_que_des_parametres_lus():
    from okf_hub.lecture.requete import CLES_CRITERES

    assert set(find_tool.SCHEMA["properties"]) == {"base", "count_by", "max_results", *CLES_CRITERES}
    assert "query_regex" not in CLES_CRITERES  # propre à l'interface web


def test_rescan_rapporte_les_dependances_declarees(hub, make_bundle):
    a = make_bundle("a", git_init=False, create_corpus=False, name="a", cites=["b"])
    a.doc("x.md", "[y](b:/y.md) [z](c:/z.md)\n").init_git()
    make_bundle("b", git_init=False, create_corpus=False, name="b", cites=["a", "absente"]).doc(
        "y.md", "# Y\n").init_git()
    reg = Registry(HubConfig.load(hub[0]))
    sortie = rescan_tool.run(reg, {})
    assert "Dépendances entre bases (cites) :" in sortie
    assert "cycle dans les cites déclarés : a → b → a" in sortie
    assert "b déclare citer absente, qui n'est pas installée" in sortie
    assert "a cite c (1 référence(s)) sans le déclarer dans cites" in sortie


def test_rescan_sans_cites_ne_charge_aucun_corpus(registry, make_bundle, monkeypatch):
    from okf_hub.lecture import modele

    monkeypatch.setattr(modele.Lecteur, "modele", lambda *a: pytest.fail("corpus chargé"))
    make_bundle()
    assert "Dépendances" not in rescan_tool.run(registry, {})


# --- rév. 4.6 : texte, dérive, degrés, tris --------------------------------------------


def test_query_filtre_sans_repli_en_ou(bases):
    assert "1 document(s)" in _run(bases, base="installation-x", query="Attention", type="Procédure")
    # Aucun document ne contient les deux mots : kb_search se replierait en OU, pas kb_find.
    assert _run(bases, base="installation-x", query="Attention introuvable").startswith("0 document(s)")


def test_tris(bases):
    cites = _run(bases, base="produit-x", sort="most_cited")
    assert "— cité par 2" in cites
    oldest = _run(bases, base="installation-x", type="Procédure", sort="oldest").splitlines()
    assert oldest[1].startswith("- exploitation/p1.md")  # source de 2020 : la plus ancienne
    reaudit = _run(bases, base="installation-x", sort="reaudit")
    assert "priorité" in reaudit
    for args in ({"sort": "n-importe"}, {"sort": "relevance"}, {"links_to_depth": 4}, {"upstream_changed": "oui"}):
        with pytest.raises(ToolError) as exc:
            _run(bases, base="installation-x", **args)
        assert exc.value.code == INVALID_INPUT, args


def test_links_to_depth_et_ordre_par_degre(hub, make_bundle):
    b = make_bundle("chaine", git_init=False, create_corpus=False, name="chaine")
    b.doc("a.md", "# A\n").doc("b.md", "[a](/a.md)\n").doc("c.md", "[b](/b.md)\n").init_git()
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    sortie = _run(reg, base="chaine", links_to="/a.md", links_to_depth=2)
    lignes = [l for l in sortie.splitlines() if l.startswith("- ")]
    assert lignes[0].startswith("- b.md") and lignes[1].startswith("- c.md") and "degré 2" in lignes[1]
    donnees = find_tool.run_structured(reg, {"base": "chaine", "links_to": "/a.md", "links_to_depth": 2}).donnees
    assert [d["depth"] for d in donnees["bases"][0]["documents"]] == [1, 2]


def test_upstream_changed(hub, make_bundle):
    import os
    import subprocess

    amont = make_bundle("produit-x", git_init=False, create_corpus=False, name="produit-x")
    amont.doc("s.md", "# S\n").init_git()
    aval = make_bundle("installation-x", git_init=False, create_corpus=False, name="installation-x")
    aval.doc("f.md", "[s](produit-x:/s.md)\n").doc("g.md", "# G\n").init_git()
    (amont.root / "knowledge" / "s.md").write_text("# S changé\n", encoding="utf-8")
    env = {**os.environ, "GIT_COMMITTER_DATE": "2099-01-01T00:00:00Z"}
    subprocess.run(["git", "-C", str(amont.root), "-c", "user.name=t", "-c", "user.email=t@l",
                    "commit", "-qam", "s"], check=True, env=env)
    reg = Registry(HubConfig.load(hub[0]))
    reg.scan()
    sortie = _run(reg, base="installation-x", upstream_changed=True)
    assert sortie.startswith("1 document(s)") and "amont modifié : produit-x:/s.md le 2099-01-01" in sortie
    donnees = find_tool.run_structured(reg, {"base": "installation-x", "upstream_changed": True}).donnees
    assert donnees["bases"][0]["documents"][0]["upstream_changed"][0]["path"] == "s.md"
