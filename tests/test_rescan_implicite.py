"""Re-scan implicite de kb_list et cooldown (amendement rév. 4.1, § B2).

Le besoin : une session déjà connectée doit voir une base importée après son
démarrage, sans rescan explicite ni redémarrage. La solution retenue reste
strictement mono-instance (§ 4.4) — chaque instance découvre pour elle-même,
il n'y a ni état partagé ni démon.

Un mécanisme unique, un cooldown unique, mais **un compteur par déclencheur** :
c'est l'écart de l'ARCHITECTURE § 5.3, sans lequel un `kb_list` étouffe le
re-scan compensatoire d'`UNKNOWN_BASE` garanti par la rév. 4.
"""

from __future__ import annotations

import anyio
import mcp_types as types
import pytest

from okf_hub.config import HubConfig
from okf_hub.server import SILENT_RESCAN_COOLDOWN_S, HubServer


class _FakeSession:
    """Capte les notifications tools/list_changed émises par le serveur."""

    def __init__(self) -> None:
        self.list_changed = 0

    async def send_tool_list_changed(self) -> None:
        self.list_changed += 1


class _FakeContext:
    def __init__(self) -> None:
        self.session = _FakeSession()


def appel(serveur: HubServer, ctx: _FakeContext, nom: str, **arguments):
    return anyio.run(
        serveur.on_call_tool,
        ctx,
        types.CallToolRequestParams(name=nom, arguments=arguments),
    )


def texte(result) -> str:
    return "\n".join(c.text for c in result.content)


@pytest.fixture
def serveur(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("premiere", name="premiere")
    return HubServer(HubConfig.load(hub_root))


def test_kb_list_voit_une_base_importee_apres_le_demarrage(serveur, make_bundle):
    """Le cas d'usage signalé : deuxième instance déjà lancée, bundle importé."""
    ctx = _FakeContext()
    assert "seconde" not in texte(appel(serveur, ctx, "kb_list"))

    make_bundle("seconde", name="seconde")
    serveur._last_silent_rescan.clear()  # cooldown écoulé

    sortie = texte(appel(serveur, ctx, "kb_list"))
    assert "seconde" in sortie
    assert "premiere" in sortie


def test_la_liste_changee_emet_tools_list_changed(serveur, make_bundle):
    ctx = _FakeContext()
    appel(serveur, ctx, "kb_list")
    avant = ctx.session.list_changed

    make_bundle("seconde", name="seconde")
    serveur._last_silent_rescan.clear()
    appel(serveur, ctx, "kb_list")

    assert ctx.session.list_changed == avant + 1


def test_liste_inchangee_n_emet_rien(serveur):
    ctx = _FakeContext()
    serveur._last_silent_rescan.clear()
    appel(serveur, ctx, "kb_list")
    assert ctx.session.list_changed == 0


def test_description_de_kb_list_regeneree_apres_import(serveur, make_bundle):
    """La description énumère les bases (§ 5.1) : elle doit suivre l'import."""
    ctx = _FakeContext()
    appel(serveur, ctx, "kb_list")

    make_bundle("seconde", name="seconde")
    serveur._last_silent_rescan.clear()
    appel(serveur, ctx, "kb_list")

    outils = anyio.run(serveur.on_list_tools, ctx, None)
    kb_list = next(o for o in outils.tools if o.name == "kb_list")
    assert "seconde" in kb_list.description


def test_deux_kb_list_rapproches_ne_scannent_qu_une_fois(serveur, make_bundle, monkeypatch):
    """Cooldown de 5 s (§ 4.4.c) : un seul parcours de bases-dir, pas trois."""
    scans = []
    original = serveur.registry.scan
    monkeypatch.setattr(
        serveur.registry, "scan", lambda: (scans.append(1), original())[1]
    )

    ctx = _FakeContext()
    serveur._last_silent_rescan.clear()
    appel(serveur, ctx, "kb_list")
    appel(serveur, ctx, "kb_list")
    appel(serveur, ctx, "kb_list")

    assert len(scans) == 1


def test_un_kb_list_ne_consomme_pas_le_rescan_d_unknown_base(serveur, make_bundle):
    """Garantie rév. 4 (§ 4.4.c) : une base importée à chaud reste joignable.

    Régression de la rév. 4.1, reproduite par
    `test_import_a_chaud_et_rescan_silencieux` : avec un compteur unique, le
    re-scan proactif de `kb_list` armait le cooldown et le re-scan
    compensatoire d'`UNKNOWN_BASE` était ignoré — l'erreur partait telle
    quelle. Lister puis utiliser une base fraîche est un usage banal.
    """
    ctx = _FakeContext()
    appel(serveur, ctx, "kb_list")           # arme le compteur de kb_list
    make_bundle("tardive", name="tardive")   # import à chaud, dans la foulée

    resultat = appel(serveur, ctx, "kb_search", base="tardive", query="exemple")
    assert not resultat.is_error, texte(resultat)


def test_deux_unknown_base_rapproches_ne_scannent_qu_une_fois(serveur, monkeypatch):
    """Chaque déclencheur garde son garde-fou : UNKNOWN_BASE ne spamme pas."""
    scans = []
    original = serveur.registry.scan
    monkeypatch.setattr(
        serveur.registry, "scan", lambda: (scans.append(1), original())[1]
    )

    ctx = _FakeContext()
    serveur._last_silent_rescan.clear()
    for _ in range(3):
        assert appel(serveur, ctx, "kb_search", base="absente", query="x").is_error

    assert len(scans) == 1


def test_le_cooldown_est_bien_de_cinq_secondes():
    assert SILENT_RESCAN_COOLDOWN_S == 5.0


def test_le_rescan_implicite_ne_touche_que_kb_list(serveur, make_bundle, monkeypatch):
    """kb_search/kb_read n'ont pas à payer un parcours de disque par appel."""
    scans = []
    original = serveur.registry.scan
    monkeypatch.setattr(
        serveur.registry, "scan", lambda: (scans.append(1), original())[1]
    )

    ctx = _FakeContext()
    serveur._last_silent_rescan.clear()
    appel(serveur, ctx, "kb_search", base="premiere", query="exemple")
    assert scans == []
