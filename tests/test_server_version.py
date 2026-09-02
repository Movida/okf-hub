"""La version annoncée au handshake MCP est la même chaîne que celle du paquet
installé — pas une seconde constante en dur, désynchronisable de la première
(`pyproject.toml` restait à 0.1.0 alors que `CHANGELOG.md` en était à 0.2.5,
prop-2026-09-01-9513 d'`okf-hub-feedback`).
"""

from __future__ import annotations

from importlib import metadata

from okf_hub.config import HubConfig
from okf_hub.server import SERVER_NAME, SERVER_VERSION, HubServer


def test_la_version_annoncee_est_celle_du_paquet_installe():
    assert SERVER_VERSION == metadata.version(SERVER_NAME)


def test_le_serveur_construit_annonce_la_version_du_paquet(hub):
    hub_root, _ = hub
    serveur = HubServer(HubConfig.load(hub_root)).build()

    assert serveur.version == metadata.version(SERVER_NAME)
