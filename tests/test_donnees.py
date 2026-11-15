"""Données d'une fiche et projection `select` de kb_find (spec rév. 4.8, § 3.5, § 5.8)."""

from __future__ import annotations

import pytest

from okf_hub.errors import INVALID_INPUT, ToolError
from okf_hub.lecture.donnees import extraire
from okf_hub.lecture.profil import BlocDonnees, charger_profil
from okf_hub.tools import find_tool

PROFIL = """\
required:
  - name: title
    type: string
optional:
  - name: statut
    type: string
  - name: echeance
    type: date
data:
  Jalons:
    format: table
    columns: [date, jalon, statut]
  Mesures:
    format: yaml
"""

FICHE = """\
---
type: Projet
title: Projet X
statut: en cours
echeance: 2026-12-01
---

# Projet X

Un paragraphe d'introduction.

## Jalons

Les jalons connus.

| date | jalon | statut |
|---|---|---|
| 2026-11-01 | Cadrage | fait |
| 2026-12-01 | Livraison \\| recette | à venir |

## Mesures

```yaml
- {nom: latence, valeur: 120, unite: ms}
- {nom: debit, valeur: 40, releve: 2026-11-10}
```
"""


@pytest.fixture
def base_donnees(make_bundle, registry):
    b = make_bundle("base-donnees", name="base-donnees", git_init=False)
    b.schema(PROFIL)
    b.manifest(name="base-donnees", governance={"rules": "./GOVERNANCE.md",
                                                "frontmatter-schema": "./schema.yaml"})
    b.doc("projets/x.md", FICHE)
    b.doc("projets/y.md", "---\ntype: Projet\ntitle: Projet Y\n---\n\n# Projet Y\n\n## Jalons\n\nÀ définir.\n")
    b.init_git()
    registry.scan()
    return registry


# --- profil et extraction --------------------------------------------------------


def test_profil_declare_les_blocs(tmp_path):
    chemin = tmp_path / "schema.yaml"
    chemin.write_text(PROFIL + "  Mauvais:\n    format: csv\n", encoding="utf-8")
    profil = charger_profil(chemin)
    assert set(profil.donnees) == {"jalons", "mesures"}
    assert profil.donnees["jalons"].colonnes == ("date", "jalon", "statut")
    assert any("Mauvais" in a for a in profil.avertissements)
    assert profil.enrichi


def test_tableau_lignes_et_pipe_echappe():
    e = extraire(FICHE, BlocDonnees("Jalons", "table", ("date", "jalon", "statut")))
    assert e.lignes == [
        {"date": "2026-11-01", "jalon": "Cadrage", "statut": "fait"},
        {"date": "2026-12-01", "jalon": "Livraison | recette", "statut": "à venir"},
    ]
    assert e.avertissements == []


def test_yaml_liste_d_objets_dates_en_texte():
    e = extraire(FICHE, BlocDonnees("Mesures", "yaml"))
    assert e.lignes[0] == {"nom": "latence", "valeur": 120, "unite": "ms"}
    assert e.lignes[1]["releve"] == "2026-11-10"


def test_colonne_manquante_signalee_sans_rien_ecarter():
    e = extraire(FICHE, BlocDonnees("Jalons", "table", ("date", "jalon", "responsable")))
    assert len(e.lignes) == 2
    assert any("responsable" in a for a in e.avertissements)


def test_section_absente_ou_sans_bloc():
    assert "absente" in extraire("# T\n\nRien.\n", BlocDonnees("Jalons", "table")).avertissements[0]
    sans = extraire("# T\n\n## Jalons\n\nÀ définir.\n", BlocDonnees("Jalons", "table"))
    assert sans.lignes == [] and "aucun tableau" in sans.avertissements[0]


def test_yaml_qui_n_est_pas_une_liste_d_objets():
    texte = "# T\n\n## Mesures\n\n```yaml\nnom: seul\n```\n"
    e = extraire(texte, BlocDonnees("Mesures", "yaml"))
    assert e.lignes == [] and "liste d'objets" in e.avertissements[0]


# --- kb_find select ----------------------------------------------------------------


def _docs(sortie):
    return {d["path"]: d for b in sortie.donnees["bases"] for d in b["documents"]}


def test_select_rend_champs_et_lignes(base_donnees):
    sortie = find_tool.run_structured(base_donnees, {
        "base": "base-donnees", "type": "Projet",
        "select": {"fields": ["statut", "echeance"], "data": ["Jalons", "mesures"]},
    })
    docs = _docs(sortie)
    x = docs["projets/x.md"]
    assert x["fields"] == {"statut": "en cours", "echeance": "2026-12-01"}
    assert len(x["data"]["Jalons"]) == 2 and x["data"]["Mesures"][0]["nom"] == "latence"
    y = docs["projets/y.md"]
    assert y["fields"] == {"statut": None, "echeance": None}
    assert y["data"]["Jalons"] == [] and y["warnings"]
    # Le texte ne porte que des effectifs.
    assert "Jalons : 2 ligne(s)" in sortie.texte
    assert "Cadrage" not in sortie.texte


def test_select_inconnu_refuse(base_donnees):
    with pytest.raises(ToolError) as exc:
        find_tool.run_structured(base_donnees, {"base": "base-donnees", "select": {"data": ["Budget"]}})
    assert exc.value.code == INVALID_INPUT and "Budget" in exc.value.message


def test_select_et_count_by_exclusifs(base_donnees):
    with pytest.raises(ToolError):
        find_tool.run_structured(base_donnees, {"base": "base-donnees", "count_by": "type",
                                                "select": {"fields": ["statut"]}})


def test_select_mal_forme(base_donnees):
    for mauvais in ({}, {"fields": []}, {"colonnes": ["x"]}, "statut"):
        with pytest.raises(ToolError):
            find_tool.run_structured(base_donnees, {"base": "base-donnees", "select": mauvais})


def test_select_plafonne_les_lignes(base_donnees, monkeypatch):
    monkeypatch.setattr(find_tool, "BUDGET_DONNEES", 80)
    sortie = find_tool.run_structured(base_donnees, {
        "base": "base-donnees", "under": "projets", "select": {"data": ["Jalons"]},
    })
    x = _docs(sortie)["projets/x.md"]
    assert x.get("data_truncated") is True and len(x["data"]["Jalons"]) < 2
    assert "données tronquées" in sortie.texte


def test_sortie_structuree_valide_contre_le_schema(base_donnees):
    jsonschema = pytest.importorskip("jsonschema")
    sortie = find_tool.run_structured(base_donnees, {
        "base": "base-donnees", "select": {"fields": ["statut"], "data": ["Jalons"]},
    })
    jsonschema.validate(sortie.donnees, find_tool.OUTPUT_SCHEMA)
