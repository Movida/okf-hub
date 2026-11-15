"""La réplique : bases partagées servies en lecture, à distance (spec rév. 4.8, § 4.6.d).

De bout en bout : un vrai serveur HTTP (uvicorn, port libre), le client MCP
officiel, et un proxy inverse simulé par l'en-tête d'identité depuis le bouclage.
"""

from __future__ import annotations

import socket
import threading
import time

import anyio
import pytest
import uvicorn
import yaml

from okf_hub.config import HubConfig
from okf_hub.replica import (
    COPIES,
    ConfigReplicaInvalide,
    ReceptionRefusee,
    ReplicaConfig,
    ReplicaServer,
    application,
    recevoir,
)

from conftest import BundleBuilder, git

ENTETE = "X-Forwarded-User"


def _local(hub_root, **cles):
    (hub_root / "hub-config.local.yaml").write_text(yaml.safe_dump(cles), encoding="utf-8")


def _source(tmp_path, nom, texte="Contenu de démonstration."):
    b = BundleBuilder(tmp_path / "sources" / nom)
    b.manifest(name=nom).governance()
    b.doc("exemple.md", f"---\ntype: Reference\ntitle: Exemple {nom}\n---\n\n# Exemple\n\n{texte}\n")
    b.init_git()
    return b


def _ajouter(source, rel, texte):
    source.doc(rel, texte)
    git(source.root, "add", "-A")
    git(source.root, "commit", "-q", "-m", f"ajout {rel}")


@pytest.fixture
def replique(hub, tmp_path):
    """Machine de la réplique : `base-a` ouverte à tous, `base-b` à `lecteur-x`."""
    hub_root, bases = hub
    _local(hub_root, **{
        "shared-bases": ["base-a", "base-b"],
        "replica": {
            "identity-header": ENTETE,
            "visibility": {"base-a": ["*"], "base-b": ["lecteur-x"]},
            "propose-hint": "Pour proposer : la voie de l'opérateur.",
        },
    })
    config = HubConfig.load(hub_root)
    sources = {n: _source(tmp_path, n) for n in ("base-a", "base-b")}
    for nom, src in sources.items():
        recevoir(config, nom, src.root)
    return config, sources


# --- réception des copies -------------------------------------------------------


def test_reception_pose_un_lien_vers_une_copie(replique):
    config, _ = replique
    lien = config.bases_dir / "base-a"
    assert lien.is_symlink()
    assert (lien / "okf-bundle.yaml").is_file()
    assert lien.resolve().parent.name == COPIES


def test_bascule_et_elagage(replique):
    config, sources = replique
    copies = config.bases_dir / COPIES
    for i in range(3):
        _ajouter(sources["base-a"], f"n{i}.md", f"# N{i}\n")
        recevoir(config, "base-a", sources["base-a"].root)
    miennes = sorted(p.name for p in copies.iterdir() if p.name.startswith("base-a-"))
    assert len(miennes) == 2                       # la courante et la précédente
    assert (config.bases_dir / "base-a").resolve().name == miennes[-1]
    assert (config.bases_dir / "base-a" / "knowledge" / "n2.md").is_file()


def test_reception_refuse_une_base_non_partagee(replique, tmp_path):
    config, _ = replique
    src = _source(tmp_path, "base-c")
    with pytest.raises(ReceptionRefusee, match="shared-bases"):
        recevoir(config, "base-c", src.root)
    assert not (config.bases_dir / "base-c").exists()


def test_reception_refuse_un_nom_qui_ne_correspond_pas(replique, tmp_path):
    config, sources = replique
    with pytest.raises(ReceptionRefusee, match="se nomme 'base-b'"):
        recevoir(config, "base-a", sources["base-b"].root)
    assert (config.bases_dir / "base-a").resolve().name.startswith("base-a-")


def test_reception_ne_remplace_pas_un_vrai_repertoire(hub, tmp_path):
    hub_root, bases = hub
    _local(hub_root, **{"shared-bases": ["base-a"]})
    (bases / "base-a").mkdir()
    with pytest.raises(ReceptionRefusee, match="pas un lien"):
        recevoir(HubConfig.load(hub_root), "base-a", _source(tmp_path, "base-a").root)


# --- configuration ----------------------------------------------------------------


def test_identite_obligatoire():
    with pytest.raises(ConfigReplicaInvalide, match="identity-header"):
        ReplicaConfig.depuis({"visibility": {"base-a": ["*"]}})


def test_cle_inconnue_refusee():
    with pytest.raises(ConfigReplicaInvalide, match="replica"):
        ReplicaConfig.depuis({"identity-header": ENTETE, "ecriture": True})


# --- de bout en bout, en HTTP ----------------------------------------------------


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Serveur:
    def __init__(self, config, **surcharge):
        brut = {**config.replica, **surcharge}
        self.port = _port_libre()
        self.hub = ReplicaServer(config, ReplicaConfig.depuis(brut, host="127.0.0.1", port=self.port))
        self.uv = uvicorn.Server(uvicorn.Config(
            application(self.hub), host="127.0.0.1", port=self.port, log_level="warning",
            proxy_headers=False, lifespan="on",
        ))
        self.fil = threading.Thread(target=self.uv.run, daemon=True)

    def __enter__(self):
        self.fil.start()
        for _ in range(200):
            if self.uv.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("le serveur de test n'a pas démarré")

    def __exit__(self, *exc):
        self.uv.should_exit = True
        self.fil.join(timeout=5)
        self.hub.stop()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"


def _dialogue(url, identite, scenario):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def principal():
        en_tetes = {ENTETE: identite} if identite else {}
        async with httpx2.AsyncClient(headers=en_tetes, timeout=30) as client:
            async with streamable_http_client(url, http_client=client, terminate_on_close=False) as flux:
                read, write = flux[0], flux[1]
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    return await scenario(session, init)

    return anyio.run(principal)


def _texte(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")


def test_lecture_seule_et_visibilite(replique):
    config, _ = replique

    async def scenario(session, init):
        noms = {t.name for t in (await session.list_tools()).tools}
        liste = _texte(await session.call_tool("kb_list", {}))
        fermee = await session.call_tool("kb_read", {"base": "base-b", "path": "exemple.md"})
        ouverte = _texte(await session.call_tool("kb_read", {"base": "base-a", "path": "exemple.md"}))
        return init.instructions, noms, liste, fermee, ouverte

    with _Serveur(config) as srv:
        instructions, noms, liste, fermee, ouverte = _dialogue(srv.url, "lecteur-y", scenario)
    assert noms == {"kb_list", "kb_search", "kb_read", "kb_find", "kb_governance", "kb_proposal_status"}
    assert "la voie de l'opérateur" in instructions
    assert "base-a" in liste and "copie du" in liste
    assert "base-b" not in liste                     # fermée = absente
    assert fermee.is_error and "UNKNOWN_BASE" in _texte(fermee)
    assert "base-b" not in _texte(fermee).split("disponibles")[-1]
    assert "Contenu de démonstration" in ouverte


def test_un_lecteur_nomme_voit_sa_base(replique):
    config, _ = replique

    async def scenario(session, init):
        return _texte(await session.call_tool("kb_list", {}))

    with _Serveur(config) as srv:
        liste = _dialogue(srv.url, "lecteur-x", scenario)
    assert "base-a" in liste and "base-b" in liste


def test_une_bascule_est_vue_a_l_appel_suivant(replique):
    config, sources = replique

    async def avant(session, init):
        return await session.call_tool("kb_read", {"base": "base-a", "path": "nouveau.md"})

    async def apres(session, init):
        return _texte(await session.call_tool("kb_read", {"base": "base-a", "path": "nouveau.md"}))

    with _Serveur(config) as srv:
        assert _dialogue(srv.url, "lecteur-y", avant).is_error
        _ajouter(sources["base-a"], "nouveau.md", "# Nouveau\n\nPublié après le démarrage.\n")
        recevoir(config, "base-a", sources["base-a"].root)
        assert "Publié après le démarrage" in _dialogue(srv.url, "lecteur-y", apres)


def test_double_barriere_une_base_non_declaree_n_est_pas_chargee(replique, tmp_path):
    config, _ = replique
    intrus = _source(tmp_path, "base-intruse")
    git(intrus.root, "clone", "-q", str(intrus.root), str(config.bases_dir / "base-intruse"))
    _local(config.hub_root, **{
        "shared-bases": ["base-a", "base-b"],
        "replica": {**config.replica, "visibility": {**config.replica["visibility"], "base-intruse": ["*"]}},
    })
    config = HubConfig.load(config.hub_root)

    async def scenario(session, init):
        return _texte(await session.call_tool("kb_list", {}))

    with _Serveur(config) as srv:
        assert "base-intruse" not in _dialogue(srv.url, "lecteur-x", scenario)


def test_sans_identite_ou_hors_proxy_refuse_avant_mcp(replique):
    import httpx2

    config, _ = replique
    corps = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    en_tetes = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with _Serveur(config) as srv:
        sans = httpx2.post(srv.url, json=corps, headers=en_tetes)
    assert sans.status_code == 403 and ENTETE in sans.text
    with _Serveur(config, **{"trusted-proxies": ["10.9.9.9"]}) as srv:
        hors = httpx2.post(srv.url, json=corps, headers={**en_tetes, ENTETE: "lecteur-x"})
    assert hors.status_code == 403 and "proxy" in hors.text
