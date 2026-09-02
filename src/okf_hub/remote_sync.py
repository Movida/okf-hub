"""Synchronisation remote au démarrage du serveur (§ 4.5, « [v1+] »).

En v0, le clone dans `bases/` est la copie canonique : aucune synchronisation
automatique n'existait. Ce module l'ajoute, à la lettre de la contrainte
non négociable du § 4.4 : **pas d'état partagé entre instances, pas de
démon**. Le mécanisme se limite à un point unique, explicite, du cycle de vie
d'**une** instance de serveur — son propre démarrage — avant sa première
découverte (`__main__.main`, avant `HubServer(config)`). Chaque instance (une
par client connecté, § 4.4) fait sa propre tentative ; rien n'est partagé
entre elles au-delà de ce que le disque et le verrou de base portent déjà.

Portée volontairement minimale :

* seules les bases **avec un remote** sont concernées (`git remote` non vide)
  — une base semée depuis `bundles/` n'en a pas et n'est jamais touchée ;
* seul un **fast-forward** est appliqué. Une divergence (HEAD et la branche
  amont ont chacun des commits que l'autre n'a pas — typiquement des
  propositions locales commitées par `kb_propose` mais jamais poussées) est
  **signalée dans le journal, jamais écrasée ni ignorée silencieusement**
  (contrainte explicite du contexte hérité de ce rôle) ; aucun push n'est
  tenté, conforme au § 4.5 ;
* un remote absent, injoignable, ou une base sans branche amont configurée ne
  lève jamais : un hub qui ne peut pas se synchroniser démarre quand même,
  au même titre qu'un hub sans ses bases livrées (`bootstrap.deploy_missing`).

La séquence fetch + comparaison + fast-forward mute le HEAD et le working tree
du dépôt : elle est donc exécutée sous le **même verrou de base**
(`locking.base_lock`) que `kb_propose`, pour ne jamais s'entrelacer avec une
séquence d'écriture en cours dans une autre instance déjà démarrée.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import hublog
from .config import HubConfig
from .errors import ToolError
from .locking import base_lock
from .manifest import MANIFEST_FILENAME

GIT_TIMEOUT_S = 20

#: Jamais d'invite interactive (identifiants, host key) : une instance de
#: serveur MCP n'a personne à qui les poser, et ça bloquerait le démarrage
#: jusqu'au timeout du transport plutôt que jusqu'à celui, borné, de ce module.
_ENV_NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0"}


def _candidate_dirs(bases_dir: Path) -> list[Path]:
    """Bundles installés (répertoire non caché, manifeste et `.git` présents).

    Même filtre que la découverte (§ 4.2) et que `bootstrap.available` : un
    scan concurrent qui construit un bundle dans un répertoire préfixé de `.`
    n'est jamais pris pour une base à synchroniser.
    """
    if not bases_dir.is_dir():
        return []
    out: list[Path] = []
    for entry in sorted(bases_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / MANIFEST_FILENAME).is_file():
            continue
        # Un `.git` fichier signale un worktree lié : cas non suivi en v0
        # (même réserve que `locking.ensure_git_exclude`).
        if not (entry / ".git").is_dir():
            continue
        out.append(entry)
    return out


def _run(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(_ENV_NO_PROMPT)
    return subprocess.run(
        ["git", "-C", str(repo)] + args,
        capture_output=True,
        timeout=GIT_TIMEOUT_S,
        env=env,
        check=False,
    )


def _has_remote(repo: Path) -> bool:
    proc = _run(repo, ["remote"])
    return proc.returncode == 0 and bool(proc.stdout.decode().strip())


def _upstream_ref(repo: Path) -> str | None:
    proc = _run(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if proc.returncode != 0:
        return None
    ref = proc.stdout.decode("utf-8", errors="replace").strip()
    return ref or None


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = _run(repo, ["merge-base", "--is-ancestor", ancestor, descendant])
    return proc.returncode == 0


def sync_one(repo: Path) -> None:
    """Tente une synchronisation fast-forward-only de `repo` avec son amont.

    Ne lève jamais — chaque cas d'échec est journalisé et laisse la base dans
    son état courant, à retenter au prochain démarrage.
    """
    name = repo.name
    try:
        if not _has_remote(repo):
            return  # base semée depuis bundles/ : rien à synchroniser.

        with base_lock(repo):
            fetch = _run(repo, ["fetch", "--quiet"])
            if fetch.returncode != 0:
                stderr = fetch.stderr.decode("utf-8", errors="replace").strip()
                hublog.warning(
                    f"sync remote '{name}' : remote injoignable, ignorée pour ce "
                    f"démarrage ({stderr or fetch.returncode})"
                )
                return

            upstream = _upstream_ref(repo)
            if upstream is None:
                hublog.info(
                    f"sync remote '{name}' : aucune branche amont configurée, ignorée"
                )
                return

            local_devant_amont = _is_ancestor(repo, upstream, "HEAD")
            amont_devant_local = _is_ancestor(repo, "HEAD", upstream)

            if local_devant_amont and amont_devant_local:
                return  # déjà synchronisées.
            if local_devant_amont:
                return  # local en avance (propositions non poussées) : rien à tirer, jamais de push en v0.
            if not amont_devant_local:
                hublog.warning(
                    f"sync remote '{name}' : divergence locale détectée (HEAD et "
                    f"'{upstream}' ont chacun des commits que l'autre n'a pas) — "
                    f"synchronisation automatique refusée, résolution manuelle requise (§ 4.5)"
                )
                return

            merge = _run(repo, ["merge", "--ff-only", "--quiet", upstream])
            if merge.returncode != 0:
                stderr = merge.stderr.decode("utf-8", errors="replace").strip()
                hublog.warning(
                    f"sync remote '{name}' : fast-forward attendu mais échoué "
                    f"({stderr or merge.returncode})"
                )
                return
            hublog.info(f"sync remote '{name}' : mise à jour fast-forward depuis '{upstream}'")
    except ToolError as exc:
        # BASE_BUSY (verrou non acquis, § 4.4.b.1) : non bloquant, la
        # synchronisation attendra le prochain démarrage.
        hublog.warning(f"sync remote '{name}' : {exc.message}")
    except (OSError, subprocess.SubprocessError) as exc:
        hublog.warning(f"sync remote '{name}' : opération git impossible ({exc})")


def sync_all(config: HubConfig) -> None:
    """Synchronise, au démarrage, chaque base installée disposant d'un remote."""
    for repo in _candidate_dirs(config.bases_dir):
        sync_one(repo)
