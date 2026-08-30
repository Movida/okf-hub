"""Verrouillage par base (§ 4.4.b.1).

Mécanisme imposé : verrou exclusif `flock()` sur un fichier persistant
`bases/<nom>/.okf-hub.lock`. Propriétés recherchées :

* libération automatique à la mort du processus — pas de verrou orphelin, pas
  de timestamp, pas de procédure de bris ;
* **un descripteur de fichier neuf par acquisition** : `flock()` s'applique à
  la description de fichier ouverte, donc deux acquisitions partageant un fd
  mis en cache ne s'excluraient pas entre elles. C'est l'exigence
  intra-processus qui rend le verrou correct quand une même instance du
  serveur traite deux requêtes concurremment ;
* interopérable avec `flock(1)`, utilisé par le script `okf-lock` (§ 4.4.b.3,
  § 11.3) : même fichier, même appel système.
"""

from __future__ import annotations

import errno
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

from . import hublog
from .errors import BASE_BUSY, IO_ERROR, ToolError

LOCK_FILENAME = ".okf-hub.lock"
LOCK_TIMEOUT_S = 15.0

_POLL_INITIAL_S = 0.025
_POLL_MAX_S = 0.25


def ensure_git_exclude(bundle_root: Path) -> None:
    """Ajoute `.okf-hub.lock` au `.git/info/exclude` de la base (§ 4.4.b.1).

    Fichier local et non versionné : même un bundle tiers dont le `.gitignore`
    ne prévoit pas le fichier de verrou ne le verra jamais en untracked, ce qui
    ferait échouer l'étape de réconciliation (§ 7.1) sur un faux positif.
    """
    git_dir = bundle_root / ".git"
    if not git_dir.exists():
        return
    # Un worktree lié a un `.git` fichier ; on ne suit pas ce cas en v0.
    if not git_dir.is_dir():
        return
    info_dir = git_dir / "info"
    exclude = info_dir / "exclude"
    try:
        info_dir.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        if any(line.strip() == LOCK_FILENAME for line in existing.splitlines()):
            return
        prefix = "" if (not existing or existing.endswith("\n")) else "\n"
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}{LOCK_FILENAME}\n")
        hublog.info(f"{LOCK_FILENAME} ajouté à {exclude}")
    except OSError as exc:
        # Non bloquant : le verrou fonctionne sans l'entrée d'exclusion.
        hublog.warning(f"impossible de mettre à jour {exclude} : {exc}")


@contextmanager
def base_lock(bundle_root: Path, timeout: float = LOCK_TIMEOUT_S):
    """Acquiert le verrou exclusif de la base, ou lève BASE_BUSY."""
    lock_path = bundle_root / LOCK_FILENAME
    try:
        # Descripteur neuf à chaque acquisition — jamais mis en cache.
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise ToolError(
            IO_ERROR, f"fichier de verrou inaccessible ({lock_path}) : {exc}"
        ) from exc

    ensure_git_exclude(bundle_root)

    deadline = time.monotonic() + timeout
    delay = _POLL_INITIAL_S
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise ToolError(
                        IO_ERROR, f"verrouillage impossible sur {lock_path} : {exc}"
                    ) from exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    hublog.warning(f"BASE_BUSY : verrou {lock_path} non acquis en {timeout:g} s")
                    raise ToolError(
                        BASE_BUSY,
                        "base occupée par une autre écriture, réessayez",
                    ) from exc
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, _POLL_MAX_S)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass
