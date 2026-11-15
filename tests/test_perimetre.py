"""Niveau d'une base et périmètre d'une instance (spec rév. 4.8, § 4.6.a-b).

Une base est privée par défaut, partagée si l'opérateur la nomme dans
`shared-bases` ; une instance `--scope shared` ne découvre que les partagées,
`--scope private` que les autres. Hors de son périmètre, une base n'existe pas
pour l'instance — mais son nom reste un terme privé pour la garde du § 4.1.
"""

from __future__ import annotations

import pytest
import yaml

from okf_hub import confidentialite
from okf_hub.__main__ import _parse_args
from okf_hub.config import HubConfig
from okf_hub.errors import ToolError
from okf_hub.registry import Registry


def _local(hub_root, **cles):
    (hub_root / "hub-config.local.yaml").write_text(yaml.safe_dump(cles), encoding="utf-8")


@pytest.fixture
def hub_deux_niveaux(hub, make_bundle):
    """`base-ouverte` partagée, `base-fermee` (corpus `ferme-kb`) privée."""
    hub_root, _ = hub
    make_bundle("base-ouverte", name="base-ouverte")
    make_bundle("base-fermee", name="base-fermee", corpus_dir="ferme-kb")
    _local(hub_root, **{"shared-bases": ["base-ouverte"]})
    return hub_root


def _registre(hub_root, scope=None) -> Registry:
    reg = Registry(HubConfig.load(hub_root).with_scope(scope))
    reg.scan()
    return reg


def test_shared_bases_lue_dans_la_configuration_locale(hub_deux_niveaux):
    config = HubConfig.load(hub_deux_niveaux)
    assert config.shared_bases == {"base-ouverte"}
    assert config.scope is None


def test_shared_bases_mal_formee_refusee(hub):
    hub_root, _ = hub
    _local(hub_root, **{"shared-bases": "base-ouverte"})
    with pytest.raises(ValueError, match="shared-bases"):
        HubConfig.load(hub_root)


def test_sans_perimetre_toutes_les_bases(hub_deux_niveaux):
    assert _registre(hub_deux_niveaux).names() == ["base-fermee", "base-ouverte"]


def test_perimetre_shared(hub_deux_niveaux):
    reg = _registre(hub_deux_niveaux, "shared")
    assert reg.names() == ["base-ouverte"]
    with pytest.raises(ToolError) as exc:
        reg.get("base-fermee")
    # Une base hors périmètre se répond comme une base absente : la liste des
    # bases valides ne la nomme pas.
    assert exc.value.code == "UNKNOWN_BASE"
    assert str(exc.value.message).endswith("Bases disponibles : base-ouverte")


def test_perimetre_private(hub_deux_niveaux):
    assert _registre(hub_deux_niveaux, "private").names() == ["base-fermee"]


def test_le_rapport_de_decouverte_ignore_le_hors_perimetre(hub_deux_niveaux):
    reg = _registre(hub_deux_niveaux, "shared")
    rapport = reg.last_report
    assert rapport.added == ["base-ouverte"]
    assert not rapport.invalid and not rapport.collisions


def test_hors_perimetre_le_nom_reste_un_terme_prive(hub_deux_niveaux):
    reg = _registre(hub_deux_niveaux, "shared")
    termes = confidentialite.termes_prives(reg)
    assert {"base-fermee", "ferme-kb"} <= termes


def test_perimetre_inconnu_refuse(hub_deux_niveaux):
    with pytest.raises(ValueError, match="périmètre inconnu"):
        HubConfig.load(hub_deux_niveaux).with_scope("tout")


def test_option_scope_en_ligne_de_commande():
    assert _parse_args(["--scope", "shared"]).scope == "shared"
    assert _parse_args([]).scope is None
    with pytest.raises(SystemExit):
        _parse_args(["--scope", "tout"])
