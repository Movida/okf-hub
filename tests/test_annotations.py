"""Annotations MCP des outils : ce qu'un client en déduit pour les autorisations.

Claude Desktop classe les outils d'un serveur d'après `readOnlyHint` et
`destructiveHint` (lecture seule / écriture / autres) et règle l'approbation par
classe. Un outil sans annotation tombe dans « autres » ; un outil de lecture
mal annoté réclamerait une approbation à chaque appel ; un outil d'écriture
annoté en lecture passerait sans elle. La surface d'écriture se limite à
`kb_propose` (§ 5.2) : c'est ce que ce test garde.
"""

from __future__ import annotations

import anyio
import pytest

from okf_hub.config import HubConfig
from okf_hub.server import HubServer


@pytest.fixture
def outils(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("demo", name="demo")
    srv = HubServer(HubConfig.load(hub_root))
    srv.stop()
    return {o.name: o for o in anyio.run(srv.on_list_tools, None, None).tools}


def test_chaque_outil_est_annote(outils):
    sans = [nom for nom, o in outils.items() if o.annotations is None]
    assert sans == []


def test_kb_propose_est_la_seule_ecriture(outils):
    ecritures = {nom for nom, o in outils.items() if not o.annotations.read_only_hint}
    assert ecritures == {"kb_propose"}


def test_aucun_outil_n_est_destructif_ni_ouvert(outils):
    for nom, o in outils.items():
        assert o.annotations.destructive_hint is False, nom
        assert o.annotations.open_world_hint is False, nom


def test_annotations_sur_le_fil(outils):
    """Le client lit les noms camelCase de la spécification MCP."""
    filaire = outils["kb_propose"].model_dump(by_alias=True, exclude_none=True)
    assert filaire["annotations"] == {
        "readOnlyHint": False, "destructiveHint": False,
        "idempotentHint": False, "openWorldHint": False,
    }
