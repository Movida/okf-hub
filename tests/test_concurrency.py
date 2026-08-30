"""Tests de concurrence obligatoires du jalon J2 (§ 4.4.b, § 10.2, § 11.3)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from okf_hub.config import HubConfig
from okf_hub.errors import BASE_BUSY, ToolError
from okf_hub.locking import base_lock
from okf_hub.mdutil import parse_document
from okf_hub.registry import Registry
from okf_hub.tools import propose_tool

from conftest import HUB_ROOT, git

WORKER = Path(__file__).parent / "_propose_worker.py"
OKF_LOCK = HUB_ROOT / "bin" / "okf-lock"


@pytest.fixture
def base(make_bundle, registry):
    b = make_bundle("ma-base", name="ma-base")
    registry.scan()
    return b


def _pending(base) -> list[Path]:
    return sorted((base.root / "proposals" / "pending").glob("prop-*.md"))


def _assert_corpus_intact(base, attendu: int):
    """Aucune perte ni corruption : dépôt sain, tree complet, fichiers lisibles."""
    git(base.root, "fsck", "--no-progress")

    fichiers = _pending(base)
    assert len(fichiers) == attendu, f"{len(fichiers)} propositions sur disque, {attendu} attendues"

    ids = set()
    for path in fichiers:
        doc = parse_document(path.read_text(encoding="utf-8"))
        assert doc.frontmatter is not None, f"frontmatter corrompu : {path.name}"
        assert doc.frontmatter["id"] == path.stem
        assert doc.frontmatter["status"] == "pending"
        assert doc.body.strip(), f"corps vide : {path.name}"
        ids.add(path.stem)
    assert len(ids) == attendu, "identifiants dupliqués"

    # Chaque proposition a exactement un commit de soumission (invariant § 6.2).
    for prop_id in ids:
        commits = git(base.root, "log", "--grep", f"proposal: {prop_id}", "--format=%H").split()
        assert len(commits) == 1, f"{prop_id} : {len(commits)} commits de soumission"

    # Tout est commité : aucune proposition n'est restée hors du tree de HEAD.
    dans_head = {
        Path(p).stem
        for p in git(base.root, "ls-tree", "-r", "HEAD", "--name-only").split()
        if p.startswith("proposals/pending/prop-")
    }
    assert dans_head == ids

    # Le corpus n'a pas bougé.
    assert "knowledge/exemple.md" in git(base.root, "ls-tree", "-r", "HEAD", "--name-only")


# --- 1. Deux instances serveur en parallèle ---------------------------------


@pytest.mark.slow
def test_deux_instances_proposent_simultanement(hub, base):
    """≥ 50 itérations réparties sur deux processus : zéro perte, zéro corruption."""
    hub_root, _ = hub
    iterations = 25
    procs = [
        subprocess.Popen(
            [sys.executable, str(WORKER), str(hub_root), "ma-base", label, str(iterations)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for label in ("instance-a", "instance-b")
    ]
    sorties = []
    for proc in procs:
        out, err = proc.communicate(timeout=300)
        sorties.append(out)
        assert proc.returncode == 0, f"échec du worker :\n{out}\n{err}"

    deposees = [l for out in sorties for l in out.splitlines() if l.startswith("OK ")]
    assert len(deposees) == 2 * iterations
    _assert_corpus_intact(base, 2 * iterations)


# --- 2. Requêtes concurrentes dans une même instance ------------------------


def test_fd_neuf_par_acquisition(base):
    """Exigence intra-processus § 4.4.b.1.

    `flock()` s'applique à la description de fichier ouverte : si le verrou
    réutilisait un descripteur mis en cache, un second verrouillage dans le même
    processus réussirait et les deux requêtes ne s'excluraient plus.
    """
    tenu = threading.Event()
    relacher = threading.Event()
    resultat = {}

    def porteur():
        with base_lock(base.root):
            tenu.set()
            relacher.wait(timeout=10)

    t = threading.Thread(target=porteur)
    t.start()
    assert tenu.wait(timeout=5)
    try:
        with pytest.raises(ToolError) as exc:
            with base_lock(base.root, timeout=0.5):
                resultat["acquis"] = True
        assert exc.value.code == BASE_BUSY
    finally:
        relacher.set()
        t.join(timeout=10)
    assert "acquis" not in resultat


def test_requetes_concurrentes_dans_une_meme_instance(hub, base):
    """Un seul registre, plusieurs threads : les propositions se sérialisent."""
    hub_root, _ = hub
    registry = Registry(HubConfig.load(hub_root))
    registry.scan()

    n = 12
    erreurs: list[BaseException] = []
    depart = threading.Barrier(n)

    def deposer(i: int):
        try:
            depart.wait(timeout=10)
            propose_tool.run(
                registry,
                {
                    "base": "ma-base",
                    "type": "observation",
                    "concerns": f"sujet concurrent {i}",
                    "content": f"Contenu concurrent {i}.",
                    "sources": [f"source {i}"],
                    "confidence": "low",
                    "submitted_by": f"thread-{i}",
                },
            )
        except BaseException as exc:  # noqa: BLE001
            erreurs.append(exc)

    threads = [threading.Thread(target=deposer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not erreurs, erreurs
    _assert_corpus_intact(base, n)


# --- 3. Mort brutale d'un porteur de verrou ---------------------------------


def test_mort_brutale_du_porteur_libere_le_verrou(base, tmp_path):
    """Le verrou flock() est relâché par le noyau : pas de procédure de bris."""
    script = tmp_path / "suicide.py"
    script.write_text(
        "import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
        "fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "print('verrou acquis', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    lock_path = base.root / ".okf-hub.lock"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(lock_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "verrou acquis"
        # Le verrou est bien tenu.
        with pytest.raises(ToolError):
            with base_lock(base.root, timeout=0.5):
                pass
        # SIGKILL : aucune chance de nettoyage côté processus.
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    debut = time.monotonic()
    with base_lock(base.root, timeout=5):
        pass
    assert time.monotonic() - debut < 2, "le verrou orphelin n'a pas été libéré immédiatement"


# --- 4. Interopérabilité okf-lock (shell) ↔ serveur (Python) ----------------
#
# Question ouverte § 11.3 : le wrapper flock(1) et fcntl.flock doivent partager
# exactement le même fichier et la même sémantique.


def _okf_lock_env(hub_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["OKF_HUB_ROOT"] = str(hub_root)
    env["OKF_HUB_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(HUB_ROOT / "src")
    return env


def test_okf_lock_resout_le_name_du_manifeste(hub, make_bundle, registry):
    """okf-lock prend le `name` du manifeste, pas le nom de répertoire (§ 3.3)."""
    hub_root, _ = hub
    make_bundle("clone-renomme", name="identite-reelle")
    registry.scan()
    proc = subprocess.run(
        [str(OKF_LOCK), "identite-reelle", "--", "sh", "-c", "echo execute"],
        capture_output=True, text=True, env=_okf_lock_env(hub_root), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "execute" in proc.stdout


def test_okf_lock_bloque_le_serveur(hub, base):
    """Le verrou pris par le shell exclut bien le serveur."""
    hub_root, _ = hub
    proc = subprocess.Popen(
        [str(OKF_LOCK), "ma-base", "--", "sh", "-c", "echo tenu; sleep 10"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=_okf_lock_env(hub_root),
    )
    try:
        assert proc.stdout.readline().strip() == "tenu"
        with pytest.raises(ToolError) as exc:
            with base_lock(base.root, timeout=1.0):
                pass
        assert exc.value.code == BASE_BUSY
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_le_serveur_bloque_okf_lock(hub, base):
    """Et réciproquement : le verrou Python exclut le wrapper shell."""
    hub_root, _ = hub
    tenu = threading.Event()
    relacher = threading.Event()

    def porteur():
        with base_lock(base.root):
            tenu.set()
            relacher.wait(timeout=30)

    t = threading.Thread(target=porteur)
    t.start()
    try:
        assert tenu.wait(timeout=5)
        env = _okf_lock_env(hub_root)
        env["OKF_LOCK_TIMEOUT"] = "1"
        proc = subprocess.run(
            [str(OKF_LOCK), "ma-base", "--", "sh", "-c", "echo NE_DOIT_PAS_S_EXECUTER"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        # 75 = code de conflit choisi par le wrapper, équivalent de BASE_BUSY.
        assert proc.returncode == 75, (proc.returncode, proc.stdout, proc.stderr)
        assert "NE_DOIT_PAS_S_EXECUTER" not in proc.stdout
    finally:
        relacher.set()
        t.join(timeout=30)


def test_okf_lock_serialise_une_sequence_complete(hub, base):
    """Granularité imposée § 4.4.b.3 : toute la séquence sous un seul verrou."""
    hub_root, _ = hub
    root = base.root
    sequence = (
        f"printf 'note ajoutée par le gestionnaire\\n' >> '{root}/knowledge/exemple.md' && "
        f"git -C '{root}' -c user.name=g -c user.email=g@l add knowledge/exemple.md && "
        f"git -C '{root}' -c user.name=g -c user.email=g@l commit -q -m 'integrate: essai'"
    )
    proc = subprocess.run(
        [str(OKF_LOCK), "ma-base", "--", "sh", "-c", sequence],
        capture_output=True, text=True, env=_okf_lock_env(hub_root), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "integrate: essai" in git(root, "log", "-1", "--format=%s")


def test_okf_lock_base_inconnue(hub, base):
    hub_root, _ = hub
    proc = subprocess.run(
        [str(OKF_LOCK), "fantome", "--", "sh", "-c", "echo NE_DOIT_PAS_S_EXECUTER"],
        capture_output=True, text=True, env=_okf_lock_env(hub_root), timeout=60,
    )
    assert proc.returncode == 3
    assert "NE_DOIT_PAS_S_EXECUTER" not in proc.stdout
    assert "ma-base" in proc.stderr  # la liste des bases valides est rendue
