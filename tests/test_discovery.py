"""Découverte et validation de manifeste (§ 3.3, § 4.2). Jalon J1."""

from __future__ import annotations

import pytest

from okf_hub.config import HubConfig
from okf_hub.registry import Registry


def scan(hub_root):
    reg = Registry(HubConfig.load(hub_root))
    report = reg.scan()
    return reg, report


def test_bundle_valide_enregistre(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("ma-base", name="ma-base")
    reg, report = scan(hub_root)
    assert reg.names() == ["ma-base"]
    assert report.added == ["ma-base"]
    assert not report.invalid


def test_repertoire_sans_manifeste_ignore_silencieusement(hub):
    hub_root, bases = hub
    (bases / "pas-un-bundle").mkdir()
    reg, report = scan(hub_root)
    assert reg.names() == []
    # Absence de manifeste = pas un bundle : ce n'est pas une erreur.
    assert report.invalid == []


@pytest.mark.parametrize(
    "overrides, motif",
    [
        ({"name": None}, "name"),
        ({"title": None}, "title"),
        ({"description": None}, "description"),
        ({"bundle-spec": None}, "bundle-spec"),
        ({"name": "Ma_Base"}, "motif"),
        ({"governance": {"rules": "./ABSENT.md"}}, "introuvable"),
    ],
)
def test_bundle_invalide_ignore_avec_motif(hub, make_bundle, overrides, motif):
    hub_root, _ = hub
    make_bundle("mauvais", **overrides)
    make_bundle("bonne", name="bonne")
    reg, report = scan(hub_root)
    # Un bundle invalide n'est jamais bloquant pour les autres (§ 3.3).
    assert "bonne" in reg.names()
    assert len(report.invalid) == 1
    assert motif in report.invalid[0][1]


def test_title_avec_retour_a_la_ligne_rejete(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("mauvais", title="Titre\nsur deux lignes")
    reg, report = scan(hub_root)
    assert reg.names() == []
    assert "retour à la ligne" in report.invalid[0][1]


def test_title_trop_long_rejete(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("mauvais", title="T" * 101)
    reg, report = scan(hub_root)
    assert "dépasse 100" in report.invalid[0][1]


def test_description_normalisee_et_tronquee(hub, make_bundle):
    hub_root, _ = hub
    longue = ("phrase   avec\ndes    espaces\n\net des sauts. " * 40).strip()
    make_bundle("ma-base", name="ma-base", description=longue)
    reg, report = scan(hub_root)
    desc = reg.get("ma-base").manifest.description
    assert "\n" not in desc
    assert "   " not in desc
    assert len(desc) <= 501  # 500 + le caractère de troncature
    assert any("tronquée" in w for _, w in report.compat_warnings)


def test_bundle_spec_inconnu_charge_avec_avertissement(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("ma-base", name="ma-base", **{"bundle-spec": "9.9"})
    reg, report = scan(hub_root)
    # Politique de compatibilité § 3.3 : chargement tenté, réussi, signalé.
    assert reg.names() == ["ma-base"]
    assert any("bundle-spec" in w for _, w in report.compat_warnings)


def test_collision_de_name_deterministe(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("aaa-clone", name="doublon")
    make_bundle("zzz-clone", name="doublon")
    reg, report = scan(hub_root)
    assert reg.names() == ["doublon"]
    # Ordre lexicographique : le premier rencontré gagne (§ 3.3).
    assert reg.get("doublon").dir_name == "aaa-clone"
    assert report.collisions == [("doublon", "aaa-clone", "zzz-clone")]


def test_name_du_manifeste_prime_sur_le_nom_de_repertoire(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("clone-renomme", name="identite-reelle")
    reg, _ = scan(hub_root)
    assert reg.names() == ["identite-reelle"]
    assert reg.get("identite-reelle").dir_name == "clone-renomme"


def test_corpus_dir_racine_rejete(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("mauvais", **{"corpus-dir": "."})
    _, report = scan(hub_root)
    assert "racine" in report.invalid[0][1]


def test_corpus_dir_egal_a_proposals_rejete(hub, make_bundle):
    hub_root, _ = hub
    # La liste d'exclusions transverse (§ 5.2) viderait le corpus silencieusement.
    make_bundle("mauvais", **{"corpus-dir": "proposals"})
    _, report = scan(hub_root)
    assert "proposals" in report.invalid[0][1]


def test_corpus_dir_contenant_proposals_rejete(hub, make_bundle):
    hub_root, _ = hub
    b = make_bundle("mauvais", create_corpus=False, git_init=False, **{"corpus-dir": "."})
    b.manifest(**{"corpus-dir": "docs"})
    (b.root / "docs").mkdir(parents=True, exist_ok=True)
    (b.root / "docs" / "note.md").write_text("# note\n", encoding="utf-8")
    _, report = scan(hub_root)
    # Ici proposals/ n'existe pas encore ; corpus-dir=docs est donc valide.
    assert not report.invalid

    # Mais si le corpus englobe la racine où vivra proposals/, c'est refusé.
    b.manifest(**{"corpus-dir": "."})
    _, report = scan(hub_root)
    assert report.invalid


def test_corpus_dir_inexistant_rejete(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("mauvais", create_corpus=False, **{"corpus-dir": "absent"})
    _, report = scan(hub_root)
    assert "n'existe pas" in report.invalid[0][1]


def test_corpus_dir_sortant_du_bundle_rejete(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("mauvais", **{"corpus-dir": "../ailleurs"})
    _, report = scan(hub_root)
    assert "sort du bundle" in report.invalid[0][1]


def test_champs_extension_v1_ignores_sans_rejet(hub, make_bundle):
    hub_root, _ = hub
    make_bundle(
        "ma-base",
        name="ma-base",
        tools=[{"name": "futur"}],
        skills=["une-skill"],
        **{"okf-spec": "0.2", "version": "1.2.3"},
    )
    reg, report = scan(hub_root)
    # § 3.3 : les champs inconnus sont ignorés, jamais rejetés.
    assert reg.names() == ["ma-base"]
    assert reg.get("ma-base").manifest.version == "1.2.3"


def test_manifeste_non_parseable_ignore(hub, make_bundle):
    hub_root, _ = hub
    b = make_bundle("mauvais", git_init=False)
    b.raw_manifest("name: [non fermé\n")
    _, report = scan(hub_root)
    assert "non parseable" in report.invalid[0][1]


def test_rescan_signale_ajout_et_retrait(hub, make_bundle):
    hub_root, bases = hub
    make_bundle("une", name="une")
    reg = Registry(HubConfig.load(hub_root))
    reg.scan()
    make_bundle("deux", name="deux")
    report = reg.scan()
    assert report.added == ["deux"]
    assert report.unchanged == ["une"]
    assert report.changed

    import shutil

    shutil.rmtree(bases / "une")
    report = reg.scan()
    assert report.removed == ["une"]
