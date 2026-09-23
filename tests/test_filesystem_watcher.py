"""Watcher filesystem par instance pour re-scan automatique (§ 4.4.a).

Le watcher observe bases-dir et déclenche le re-scan de l'instance dès qu'une
base apparaît ou disparaît, sans introduire d'état partagé entre instances
(chaque instance porte son propre observateur).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from okf_hub.config import HubConfig
from okf_hub.server import HubServer


@pytest.fixture
def serveur(hub, make_bundle):
    hub_root, _ = hub
    make_bundle("initiale", name="initiale")
    return HubServer(HubConfig.load(hub_root))


def test_watcher_demarre_au_demarrage_du_serveur(serveur):
    """Le watcher est démarré lors de l'initialisation de HubServer."""
    assert serveur._observer.is_alive()


def test_watcher_detecte_nouvelle_base(serveur, make_bundle):
    """Une base créée dans bases-dir est découverte automatiquement."""
    assert "nouvelle" not in serveur.registry.bases

    make_bundle("nouvelle", name="nouvelle")
    # Le watcher observe en arrière-plan : attendre le settle (création) puis
    # le traitement de l'événement.
    time.sleep(0.5)

    # Le re-scan a été déclenché automatiquement par le watcher
    assert "nouvelle" in serveur.registry.bases


def test_watcher_detecte_base_retiree(serveur, make_bundle):
    """Une base supprimée de bases-dir disparaît du registre."""
    make_bundle("ephemere", name="ephemere")
    time.sleep(0.5)
    assert "ephemere" in serveur.registry.bases

    # Clear le cooldown pour permettre un nouveau scan immédiat
    serveur._last_silent_rescan.clear()

    # Suppression du répertoire
    import shutil
    base_path = serveur.config.bases_dir / "ephemere"
    shutil.rmtree(base_path)
    time.sleep(0.2)

    # Le re-scan a été déclenché automatiquement
    assert "ephemere" not in serveur.registry.bases


def test_watcher_ignore_repertoires_caches(serveur):
    """Les répertoires commençant par '.' sont ignorés (cohérent avec Registry.scan)."""
    bases_dir = serveur.config.bases_dir
    cache_dir = bases_dir / ".cache"
    cache_dir.mkdir()

    time.sleep(0.2)
    assert ".cache" not in serveur.registry.bases


def test_watcher_respecte_le_cooldown(serveur, make_bundle, monkeypatch):
    """Le watcher utilise le même mécanisme de cooldown que les autres déclencheurs."""
    scans = []
    original = serveur.registry.scan
    monkeypatch.setattr(
        serveur.registry, "scan", lambda: (scans.append(1), original())[1]
    )

    # Efface les compteurs pour un départ propre
    serveur._last_silent_rescan.clear()

    # Deux créations rapprochées
    make_bundle("premiere", name="premiere")
    make_bundle("seconde", name="seconde")
    time.sleep(0.5)

    # Le cooldown de 5s doit empêcher le deuxième scan immédiat
    assert len(scans) == 1


def test_stop_arrete_le_watcher(serveur):
    """La méthode stop() arrête proprement l'observateur."""
    assert serveur._observer.is_alive()
    serveur.stop()
    # L'observer doit être arrêté
    assert not serveur._observer.is_alive()


def test_watcher_par_instance_pas_d_etat_partage(hub, make_bundle):
    """Chaque instance du serveur porte son propre observateur (§ 4.4.a)."""
    hub_root, _ = hub
    make_bundle("base", name="base")

    srv1 = HubServer(HubConfig.load(hub_root))
    srv2 = HubServer(HubConfig.load(hub_root))

    try:
        # Deux observateurs distincts
        assert srv1._observer is not srv2._observer
        # Chacun observe le même répertoire mais pour son propre registre
        assert srv1.registry is not srv2.registry
    finally:
        srv1.stop()
        srv2.stop()


class _FakeSession:
    def __init__(self) -> None:
        self.list_changed = 0

    async def send_tool_list_changed(self) -> None:
        self.list_changed += 1


class _FakeContext:
    def __init__(self) -> None:
        self.session = _FakeSession()


def test_le_changement_vu_par_le_watcher_est_notifie_au_prochain_appel(
    serveur, make_bundle
):
    """Le re-scan du watcher n'a pas de session : la notification ne doit pas se perdre.

    Sans report, le re-scan suivant de kb_list ne voyait plus de différence
    (le watcher l'avait déjà absorbée) et tools/list_changed n'était jamais émis.
    """
    import anyio
    import mcp_types as types

    ctx = _FakeContext()

    def kb_list():
        return anyio.run(
            serveur.on_call_tool,
            ctx,
            types.CallToolRequestParams(name="kb_list", arguments={}),
        )

    kb_list()
    assert ctx.session.list_changed == 0

    make_bundle("nouvelle", name="nouvelle")
    time.sleep(0.5)
    assert "nouvelle" in serveur.registry.bases

    serveur._last_silent_rescan.pop("kb_list", None)
    kb_list()
    assert ctx.session.list_changed == 1

    serveur._last_silent_rescan.pop("kb_list", None)
    kb_list()
    assert ctx.session.list_changed == 1
