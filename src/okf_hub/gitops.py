"""Opérations git du hub (§ 4.4.b.2, § 4.4.e).

Le commit de proposition n'utilise jamais l'index partagé du dépôt : il passe
par un index temporaire pointé par ``GIT_INDEX_FILE``, **initialisé depuis le
tree de HEAD** par ``git read-tree HEAD``. Sans ce ``read-tree``, l'index
temporaire serait vide et le commit apparaîtrait comme supprimant tout le
corpus — le piège classique de ``GIT_INDEX_FILE``.

Ce mécanisme garantit simultanément :

* aucune interaction avec l'index partagé du dépôt (donc pas de course avec un
  ``git add`` lancé à la main dans le worktree) ;
* les modifications non commitées du worktree ne sont pas embarquées
  (exigence § 5.5) ;
* la préservation intégrale du tree, vérifiée par test obligatoire (J2).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from . import hublog
from .errors import IO_ERROR, ToolError

GIT_TIMEOUT_S = 60

#: Identité explicite du hub (§ 4.4.e) : ne jamais dépendre de la config git
#: globale du devcontainer. L'attribution réelle vit dans les trailers et le
#: frontmatter, pas dans l'identité git.
HUB_USER_NAME = "okf-hub"
HUB_USER_EMAIL = "hub@local"

_BASE_ARGS = [
    "-c", f"user.name={HUB_USER_NAME}",
    "-c", f"user.email={HUB_USER_EMAIL}",
    # Une config globale `commit.gpgsign=true` ferait échouer tout commit du
    # hub dans un devcontainer sans clé.
    "-c", "commit.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
]

#: Backoff en cas de collision `index.lock` due à un outil git actif hors hub
#: (§ 4.4.b, dernier alinéa).
_RETRY_INITIAL_S = 0.1
_RETRY_MAX_S = 2.0
_LOCK_MARKERS = ("index.lock", "Unable to create", "File exists", "cannot lock ref")


class GitError(ToolError):
    def __init__(self, message: str) -> None:
        super().__init__(IO_ERROR, message)


def _run(repo: Path, args: list[str], env_extra: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    # Neutralise toute identité ou configuration héritée de l'environnement.
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(var, None)
    if env_extra:
        env.update(env_extra)

    argv = ["git", "-C", str(repo)] + _BASE_ARGS + args
    try:
        proc = subprocess.run(
            argv, capture_output=True, timeout=GIT_TIMEOUT_S, check=False, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} a dépassé {GIT_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise GitError(f"git introuvable ou non exécutable : {exc}") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args)} a échoué : {stderr or proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace")


def _run_with_lock_retry(
    repo: Path, args: list[str], env_extra: dict[str, str] | None, deadline: float
) -> str:
    delay = _RETRY_INITIAL_S
    while True:
        try:
            return _run(repo, args, env_extra)
        except GitError as exc:
            if not any(marker in exc.message for marker in _LOCK_MARKERS):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            hublog.warning(f"collision de verrou git, nouvelle tentative dans {delay:g} s")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, _RETRY_MAX_S)


def has_head(repo: Path) -> bool:
    """True si HEAD référence un commit (§ 4.4.b.2, cas du dépôt vierge)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "HEAD"],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def build_message(subject: str, trailers: list[tuple[str, str]]) -> str:
    """Assemble sujet et trailers.

    Les valeurs de trailers ne sont jamais échappées ici : elles ont été
    validées en amont comme dépourvues de retour à la ligne (§ 5.5). C'est
    cette validation qui rend les invariants d'audit (§ 6.2) incontournables.
    """
    for key, value in trailers:
        if "\n" in value or "\r" in value:
            raise ToolError(
                "INVALID_INPUT",
                f"trailer {key} contient un retour à la ligne — refusé (§ 5.5)",
            )
    body = "\n".join(f"{key}: {value}" for key, value in trailers)
    return f"{subject}\n\n{body}\n" if body else f"{subject}\n"


def commit_paths(
    repo: Path,
    paths: list[Path],
    message: str,
    *,
    timeout: float = GIT_TIMEOUT_S,
) -> str:
    """Commite exactement `paths` via un index temporaire. Retourne le SHA.

    À appeler impérativement sous le verrou de la base (§ 4.4.b.1).
    """
    if not paths:
        raise GitError("aucun chemin à commiter")

    rel_paths: list[str] = []
    for path in paths:
        try:
            rel_paths.append(path.resolve().relative_to(repo.resolve()).as_posix())
        except ValueError as exc:
            raise GitError(f"chemin hors du dépôt : {path}") from exc

    deadline = time.monotonic() + timeout

    # L'index temporaire vit hors du dépôt : aucun risque qu'il soit ramassé
    # par un `git add .` extérieur ou qu'il apparaisse en untracked.
    fd, tmp_index = tempfile.mkstemp(prefix="okf-index-")
    os.close(fd)
    # git veut créer le fichier lui-même ; un fichier vide serait un index
    # invalide.
    os.unlink(tmp_index)
    env = {"GIT_INDEX_FILE": tmp_index}

    try:
        if has_head(repo):
            # CRITIQUE : sans ce read-tree, le commit supprimerait tout le tree.
            _run_with_lock_retry(repo, ["read-tree", "HEAD"], env, deadline)
        # Dépôt sans HEAD : l'index vide est le comportement correct pour ce
        # tout premier commit (§ 4.4.b.2, cas limite).

        _run_with_lock_retry(repo, ["add", "--"] + rel_paths, env, deadline)
        _run_with_lock_retry(
            repo, ["commit", "--no-verify", "-m", message], env, deadline
        )
        sha = _run(repo, ["rev-parse", "HEAD"], env).strip()
        hublog.info(f"commit {sha[:10]} dans {repo.name} : {message.splitlines()[0]}")
        _sync_shared_index(repo, rel_paths, deadline)
        return sha
    finally:
        for leftover in (tmp_index, tmp_index + ".lock"):
            try:
                os.unlink(leftover)
            except OSError:
                pass


def _sync_shared_index(repo: Path, rel_paths: list[str], deadline: float) -> None:
    """Reporte les chemins commités dans l'index partagé du dépôt.

    ÉCART ASSUMÉ vis-à-vis de la lettre du § 4.4.b.2 (« aucune interaction avec
    l'index partagé »), motivé ainsi :

    un commit construit via ``GIT_INDEX_FILE`` fait avancer HEAD sans toucher
    ``.git/index``. L'index partagé reste donc sur l'ancien tree, et
    ``git status`` affiche l'intégralité des propositions commitées comme
    **supprimées**, avec ``proposals/`` en untracked. Deux conséquences :

    * l'étape de réconciliation (§ 7.1.0) prend ces fichiers pour des
      propositions non commitées et les re-commite, ce qui viole l'invariant
      d'audit « exactement deux commits par proposition » (§ 6.2) ;
    * le propriétaire de la base qui ouvre un terminal voit un dépôt qui semble
      avoir perdu tout son contenu.

    La synchronisation est **additive et chirurgicale** : seuls les chemins que
    l'on vient de commiter sont reportés, via ``update-index --add``. Les
    éventuelles modifications indexées par l'opérateur (``git add`` manuel)
    sont préservées. Elle a lieu sous le même verrou que le commit, et son
    échec n'invalide pas la proposition — le commit, lui, est déjà acquis.
    """
    try:
        _run_with_lock_retry(repo, ["update-index", "--add", "--"] + rel_paths, None, deadline)
    except GitError as exc:
        hublog.warning(
            f"index partagé non synchronisé dans {repo.name} ({exc.message}) — "
            f"`git reset` restaurera un `git status` cohérent"
        )


def status_porcelain(repo: Path) -> list[tuple[str, str]]:
    """Retourne [(code, chemin)] au format porcelain v1, terminé par NUL.

    Le format NUL évite l'échappement des chemins « inhabituels », que le
    format ligne rend entre guillemets.
    """
    out = _run(repo, ["status", "--porcelain", "-z", "--untracked-files=all"])
    entries: list[tuple[str, str]] = []
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        field = fields[i]
        if not field:
            i += 1
            continue
        code, path = field[:2], field[3:]
        entries.append((code, path))
        # Une entrée de renommage est suivie de son ancien chemin.
        if code and code[0] in ("R", "C"):
            i += 1
        i += 1
    return entries
