"""Catalogue des bases connues (`bundles/upstreams.yaml` étendu) et
`okf-hub catalog {list,show,add,remove,import,retire}`.

`catalog.load` reste tolérant aux entrées invalides (même philosophie que la
découverte de bundles, § 3.3) : jamais une exception, un avertissement puis
l'entrée ignorée. `bootstrap.upstreams` délègue entièrement à `catalog.load`
(voir `test_bootstrap_upstreams_delegue_et_ne_garde_que_l_url`) : ces deux
modules ne doivent jamais diverger sur le format historique (valeur chaîne).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import git

from okf_hub import bootstrap, catalog, catalog_cmd
from okf_hub.config import HubConfig


def _upstreams_path(hub_root: Path) -> Path:
    return hub_root / "bundles" / "upstreams.yaml"


def _write_raw(hub_root: Path, data: dict) -> None:
    chemin = _upstreams_path(hub_root)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


# --- load -------------------------------------------------------------------


def test_load_sans_fichier_renvoie_vide(hub):
    hub_root, _ = hub
    assert catalog.load(hub_root) == {}


def test_load_valeur_chaine_est_le_format_historique(hub):
    hub_root, _ = hub
    _write_raw(hub_root, {"okf-hub-feedback": "https://example.invalid/feedback.git"})
    entries = catalog.load(hub_root)
    assert entries.keys() == {"okf-hub-feedback"}
    entree = entries["okf-hub-feedback"]
    assert entree.url == "https://example.invalid/feedback.git"
    assert entree.title is None
    assert entree.description is None
    assert entree.tags == ()


def test_load_valeur_objet_porte_les_metadonnees(hub):
    hub_root, _ = hub
    _write_raw(
        hub_root,
        {
            "droit-travail": {
                "url": "https://example.invalid/droit.git",
                "title": "Droit du travail",
                "description": "Corpus de référence RH.",
                "tags": ["rh", "juridique"],
            }
        },
    )
    entree = catalog.load(hub_root)["droit-travail"]
    assert entree.url == "https://example.invalid/droit.git"
    assert entree.title == "Droit du travail"
    assert entree.description == "Corpus de référence RH."
    assert entree.tags == ("rh", "juridique")


def test_load_entree_objet_sans_url_est_ignoree(hub):
    hub_root, _ = hub
    _write_raw(hub_root, {"cassee": {"title": "Sans url"}, "bonne": "https://x.invalid/b.git"})
    assert catalog.load(hub_root).keys() == {"bonne"}


def test_load_entree_de_forme_inattendue_est_ignoree(hub):
    hub_root, _ = hub
    _write_raw(hub_root, {"liste": ["pas", "une", "url"], "bonne": "https://x.invalid/b.git"})
    assert catalog.load(hub_root).keys() == {"bonne"}


def test_load_fichier_illisible_renvoie_vide(hub):
    hub_root, _ = hub
    chemin = _upstreams_path(hub_root)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(": : pas du YAML\n", encoding="utf-8")
    assert catalog.load(hub_root) == {}


def test_load_racine_non_objet_renvoie_vide(hub):
    hub_root, _ = hub
    chemin = _upstreams_path(hub_root)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(yaml.safe_dump(["a", "b"]), encoding="utf-8")
    assert catalog.load(hub_root) == {}


# --- bootstrap.upstreams : contrat de délégation -----------------------------


def test_bootstrap_upstreams_delegue_et_ne_garde_que_l_url(hub):
    hub_root, _ = hub
    _write_raw(
        hub_root,
        {
            "simple": "https://x.invalid/simple.git",
            "enrichie": {
                "url": "https://x.invalid/enrichie.git",
                "title": "Titre",
                "tags": ["a"],
            },
        },
    )
    assert bootstrap.upstreams(hub_root) == {
        "simple": "https://x.invalid/simple.git",
        "enrichie": "https://x.invalid/enrichie.git",
    }


# --- add ----------------------------------------------------------------------


def test_add_sans_metadonnees_ecrit_une_chaine_nue(hub):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    brut = yaml.safe_load(_upstreams_path(hub_root).read_text(encoding="utf-8"))
    assert brut == {"base-x": "https://x.invalid/x.git"}


def test_add_avec_metadonnees_ecrit_un_objet(hub):
    hub_root, _ = hub
    catalog.add(
        hub_root,
        "base-x",
        "https://x.invalid/x.git",
        title="Base X",
        description="Une base.",
        tags=("t1", "t2"),
    )
    brut = yaml.safe_load(_upstreams_path(hub_root).read_text(encoding="utf-8"))
    assert brut["base-x"] == {
        "url": "https://x.invalid/x.git",
        "title": "Base X",
        "description": "Une base.",
        "tags": ["t1", "t2"],
    }


def test_add_preserve_les_entrees_existantes(hub):
    hub_root, _ = hub
    catalog.add(hub_root, "premiere", "https://x.invalid/p.git")
    catalog.add(hub_root, "seconde", "https://x.invalid/s.git")
    assert catalog.load(hub_root).keys() == {"premiere", "seconde"}


def test_add_nom_invalide_refuse(hub):
    hub_root, _ = hub
    with pytest.raises(catalog.CatalogError):
        catalog.add(hub_root, "Nom Invalide", "https://x.invalid/x.git")


@pytest.mark.parametrize("url", ["", "   ", "https://x.invalid/\nx.git"])
def test_add_url_invalide_refuse(hub, url):
    hub_root, _ = hub
    with pytest.raises(catalog.CatalogError):
        catalog.add(hub_root, "base-x", url)


def test_add_title_trop_long_refuse(hub):
    hub_root, _ = hub
    with pytest.raises(catalog.CatalogError):
        catalog.add(hub_root, "base-x", "https://x.invalid/x.git", title="t" * 101)


def test_add_description_trop_longue_refuse(hub):
    hub_root, _ = hub
    with pytest.raises(catalog.CatalogError):
        catalog.add(hub_root, "base-x", "https://x.invalid/x.git", description="d" * 501)


def test_add_doublon_sans_overwrite_refuse(hub):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    with pytest.raises(catalog.CatalogError):
        catalog.add(hub_root, "base-x", "https://x.invalid/autre.git")
    assert catalog.load(hub_root)["base-x"].url == "https://x.invalid/x.git"


def test_add_doublon_avec_overwrite_remplace(hub):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    catalog.add(hub_root, "base-x", "https://x.invalid/autre.git", overwrite=True)
    assert catalog.load(hub_root)["base-x"].url == "https://x.invalid/autre.git"


# --- remove ---------------------------------------------------------------------


def test_remove_entree_existante(hub):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    assert catalog.remove(hub_root, "base-x") is True
    assert catalog.load(hub_root) == {}


def test_remove_entree_absente_ne_touche_rien(hub):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    assert catalog.remove(hub_root, "inconnue") is False
    assert catalog.load(hub_root).keys() == {"base-x"}


def test_remove_ne_touche_jamais_bases(hub):
    hub_root, bases = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    (bases / "base-x").mkdir()
    catalog.remove(hub_root, "base-x")
    assert (bases / "base-x").is_dir()


# --- import_entry -----------------------------------------------------------------


@pytest.fixture
def depot_source(tmp_path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    (source / "a.md").write_text("# A\n", encoding="utf-8")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "init")
    return source


def test_import_entry_clone_dans_bases(hub, depot_source):
    hub_root, bases = hub
    config = HubConfig.load(hub_root)
    entries = {"base-x": catalog.CatalogEntry(name="base-x", url=str(depot_source))}
    cible = catalog.import_entry(config, entries, "base-x")
    assert cible == bases / "base-x"
    assert (cible / "a.md").is_file()
    assert (cible / ".git").is_dir()


def test_import_entry_nom_inconnu_refuse(hub):
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    with pytest.raises(catalog.CatalogError):
        catalog.import_entry(config, {}, "inconnue")


def test_import_entry_cible_deja_existante_refuse(hub, depot_source):
    hub_root, bases = hub
    config = HubConfig.load(hub_root)
    (bases / "base-x").mkdir()
    entries = {"base-x": catalog.CatalogEntry(name="base-x", url=str(depot_source))}
    with pytest.raises(catalog.CatalogError):
        catalog.import_entry(config, entries, "base-x")


def test_import_entry_echec_de_clone_leve_lerreur_git(hub):
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    entries = {"base-x": catalog.CatalogEntry(name="base-x", url=str(hub_root / "absent.git"))}
    with pytest.raises(subprocess.CalledProcessError):
        catalog.import_entry(config, entries, "base-x")


# --- retire -------------------------------------------------------------------


@pytest.fixture
def base_deployee(hub) -> Path:
    """Une base déployée, dépôt git local sans remote."""
    _, bases = hub
    cible = bases / "base-x"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    (cible / "a.md").write_text("# A\n", encoding="utf-8")
    git(cible, "add", "-A")
    git(cible, "commit", "-q", "-m", "init")
    return cible


def test_retire_base_non_deployee_refuse(hub):
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    with pytest.raises(catalog.CatalogError):
        catalog.retire(config, "inconnue")


def test_retire_sans_remote_supprime_directement(hub, base_deployee):
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    rapport = catalog.retire(config, "base-x")
    assert rapport.removed is True
    assert rapport.blocked_reasons == []
    assert not base_deployee.exists()


def test_retire_avec_proposition_en_attente_est_bloque(hub, base_deployee):
    hub_root, _ = hub
    pending = base_deployee / "proposals" / "pending"
    pending.mkdir(parents=True)
    (pending / "p1.md").write_text("---\n---\n", encoding="utf-8")

    config = HubConfig.load(hub_root)
    rapport = catalog.retire(config, "base-x")
    assert rapport.removed is False
    assert any("proposition" in r for r in rapport.blocked_reasons)
    assert base_deployee.is_dir()


def test_retire_avec_proposition_en_attente_et_force_supprime_quand_meme(hub, base_deployee):
    hub_root, _ = hub
    pending = base_deployee / "proposals" / "pending"
    pending.mkdir(parents=True)
    (pending / "p1.md").write_text("---\n---\n", encoding="utf-8")

    config = HubConfig.load(hub_root)
    rapport = catalog.retire(config, "base-x", force=True)
    assert rapport.removed is True
    assert any("proposition" in r for r in rapport.blocked_reasons)
    assert not base_deployee.exists()


def test_retire_remote_sans_suivi_est_bloque(hub, base_deployee, tmp_path):
    hub_root, _ = hub
    amont = tmp_path / "amont.git"
    amont.mkdir()
    git(amont, "init", "-q", "--bare", "-b", "main")
    git(base_deployee, "remote", "add", "origin", str(amont))

    config = HubConfig.load(hub_root)
    rapport = catalog.retire(config, "base-x")
    assert rapport.removed is False
    assert any("amont" in r for r in rapport.blocked_reasons)


def test_retire_remote_en_avance_est_bloque(hub, base_deployee, tmp_path):
    hub_root, _ = hub
    amont = tmp_path / "amont.git"
    amont.mkdir()
    git(amont, "init", "-q", "--bare", "-b", "main")
    git(base_deployee, "remote", "add", "origin", str(amont))
    git(base_deployee, "push", "-q", "-u", "origin", "main")

    (base_deployee / "b.md").write_text("# B\n", encoding="utf-8")
    git(base_deployee, "add", "-A")
    git(base_deployee, "commit", "-q", "-m", "second")

    config = HubConfig.load(hub_root)
    rapport = catalog.retire(config, "base-x")
    assert rapport.removed is False
    assert any("non poussé" in r for r in rapport.blocked_reasons)


def test_retire_remote_a_jour_supprime(hub, base_deployee, tmp_path):
    hub_root, _ = hub
    amont = tmp_path / "amont.git"
    amont.mkdir()
    git(amont, "init", "-q", "--bare", "-b", "main")
    git(base_deployee, "remote", "add", "origin", str(amont))
    git(base_deployee, "push", "-q", "-u", "origin", "main")

    config = HubConfig.load(hub_root)
    rapport = catalog.retire(config, "base-x")
    assert rapport.removed is True
    assert rapport.blocked_reasons == []
    assert not base_deployee.exists()


# --- CLI (catalog_cmd) ----------------------------------------------------------


def test_cli_list_catalogue_vide(hub, capsys):
    hub_root, _ = hub
    code = catalog_cmd.main(hub_root, ["list"])
    assert code == 0
    assert "Aucune base connue" in capsys.readouterr().out


def test_cli_list_montre_titre_tags_et_etat(hub, capsys):
    hub_root, bases = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git", title="Base X", tags=("rh",))
    (bases / "base-x").mkdir()
    code = catalog_cmd.main(hub_root, ["list"])
    assert code == 0
    sortie = capsys.readouterr().out
    assert "base-x" in sortie
    assert "déployée" in sortie
    assert "Base X" in sortie
    assert "rh" in sortie


def test_cli_list_filtre_par_tag(hub, capsys):
    hub_root, _ = hub
    catalog.add(hub_root, "avec-tag", "https://x.invalid/a.git", tags=("rh",))
    catalog.add(hub_root, "sans-tag", "https://x.invalid/b.git")
    code = catalog_cmd.main(hub_root, ["list", "--tag", "rh"])
    assert code == 0
    sortie = capsys.readouterr().out
    assert "avec-tag" in sortie
    assert "sans-tag" not in sortie


def test_cli_show_entree_inconnue(hub, capsys):
    hub_root, _ = hub
    code = catalog_cmd.main(hub_root, ["show", "inconnue"])
    assert code == 3
    assert "inconnu" in capsys.readouterr().err


def test_cli_show_entree_connue(hub, capsys):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git", description="Une base.")
    code = catalog_cmd.main(hub_root, ["show", "base-x"])
    assert code == 0
    sortie = capsys.readouterr().out
    assert "https://x.invalid/x.git" in sortie
    assert "Une base." in sortie


def test_cli_add_puis_list(hub, capsys):
    hub_root, _ = hub
    code = catalog_cmd.main(hub_root, ["add", "base-x", "https://x.invalid/x.git", "--title", "Base X"])
    assert code == 0
    capsys.readouterr()
    assert catalog.load(hub_root)["base-x"].title == "Base X"


def test_cli_add_doublon_sans_overwrite_echoue(hub, capsys):
    hub_root, _ = hub
    catalog_cmd.main(hub_root, ["add", "base-x", "https://x.invalid/x.git"])
    capsys.readouterr()
    code = catalog_cmd.main(hub_root, ["add", "base-x", "https://x.invalid/autre.git"])
    assert code == 3


def test_cli_remove(hub, capsys):
    hub_root, _ = hub
    catalog_cmd.main(hub_root, ["add", "base-x", "https://x.invalid/x.git"])
    capsys.readouterr()
    code = catalog_cmd.main(hub_root, ["remove", "base-x"])
    assert code == 0
    assert catalog.load(hub_root) == {}


def test_cli_remove_absente_echoue(hub, capsys):
    hub_root, _ = hub
    code = catalog_cmd.main(hub_root, ["remove", "inconnue"])
    assert code == 3


def test_cli_import_reussi(hub, depot_source, capsys):
    hub_root, bases = hub
    catalog.add(hub_root, "base-x", str(depot_source))
    capsys.readouterr()
    code = catalog_cmd.main(hub_root, ["import", "base-x"])
    assert code == 0
    assert (bases / "base-x" / "a.md").is_file()


def test_cli_import_echec_de_clone(hub, capsys):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", str(hub_root / "absent.git"))
    capsys.readouterr()
    code = catalog_cmd.main(hub_root, ["import", "base-x"])
    assert code == 4
    assert "échec du clone" in capsys.readouterr().err


def test_cli_retire_bloque_sans_force(hub, base_deployee, capsys):
    hub_root, _ = hub
    pending = base_deployee / "proposals" / "pending"
    pending.mkdir(parents=True)
    (pending / "p1.md").write_text("---\n---\n", encoding="utf-8")
    code = catalog_cmd.main(hub_root, ["retire", "base-x"])
    assert code == 5
    assert base_deployee.is_dir()
    assert "--force" in capsys.readouterr().err


def test_cli_retire_avec_forget_oublie_aussi_le_catalogue(hub, base_deployee, capsys):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    capsys.readouterr()
    code = catalog_cmd.main(hub_root, ["retire", "base-x", "--forget"])
    assert code == 0
    assert not base_deployee.exists()
    assert catalog.load(hub_root) == {}


def test_cli_retire_sans_forget_garde_l_entree_du_catalogue(hub, base_deployee, capsys):
    hub_root, _ = hub
    catalog.add(hub_root, "base-x", "https://x.invalid/x.git")
    capsys.readouterr()
    code = catalog_cmd.main(hub_root, ["retire", "base-x"])
    assert code == 0
    assert catalog.load(hub_root).keys() == {"base-x"}
