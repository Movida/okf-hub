"""Recherche plein texte via ripgrep (§ 5.2)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .errors import INVALID_INPUT, IO_ERROR, ToolError
from .registry import Base

RG_TIMEOUT_S = 30

#: Globs d'exclusion passés à ripgrep — redondants avec les contraintes sur
#: corpus-dir (§ 3.3), conservés en défense en profondeur (§ 5.2).
#: Les globs contenant un « / » sont ancrés sur le répertoire courant, pas sur
#: la racine de recherche : d'où le préfixe `**/`. Le filtrage de vérité reste
#: `Base._excluded`, appliqué aux résultats — la correction ne dépend donc pas
#: de la syntaxe de glob de ripgrep.
_EXCLUDE_GLOBS = [
    "!**/proposals/**",
    "!**/.git/**",
    "!okf-bundle.yaml",
    "!GOVERNANCE.md",
    "!schema.yaml",
    "!CLAUDE.md",
    "!.okf-hub.lock",
]


#: Noms réservés par OKF v0.2 (§ 3.1) : ce ne sont pas des concepts mais des
#: sommaires et des journaux. Ils restent des documents au sens de la § 2 du
#: hub — lisibles par kb_read, comptés par kb_list — mais ils sont **déclassés**
#: en recherche : un sommaire est dense en texte de liens, donc il matche
#: beaucoup et n'apprend rien. Mesure sur un corpus réel de 856 documents :
#: sans ce déclassement, 28 % des résultats étaient des index.md ou log.md.
#: Écart mineur et documenté vis-à-vis de la § 5.2, au service du principe
#: § 1.5 (« retourner le minimum pertinent »).
RESERVED_NAMES = frozenset({"index.md", "log.md"})


@dataclass
class DocHits:
    path: Path
    lines: list[int] = field(default_factory=list)
    """Numéros de ligne (base 1) touchés, triés et dédoublonnés."""
    terms_hit: int = 0
    """Nombre de termes distincts de la requête touchés (mode keyword)."""
    match_count: int = 0

    @property
    def density(self) -> float:
        return self.match_count / max(1, len(self.lines))

    @property
    def reserved(self) -> bool:
        return self.path.name in RESERVED_NAMES


def _rg_binary() -> str:
    """Chemin de `rg`, ou `IO_ERROR` avec de quoi le réparer.

    Le message nomme le **PATH du processus serveur**, et pas « le PATH » tout
    court : le transport est stdio (§ 4.3), donc chaque client lance sa propre
    instance et celle-ci hérite de l'environnement du client. Un hub installé
    dans le devcontainer mais lancé depuis un clone vu côté hôte ne voit pas le
    ripgrep posé par `post-create.sh`, qui ne s'exécute qu'à la création du
    conteneur — c'est le cas réel du 01/09/2026, diagnostiqué par le `hub_root`
    de la ligne de démarrage du journal. La version précédente de ce message
    renvoyait à `.devcontainer/devcontainer.json`, qui ne mentionne pas ripgrep
    et n'aide sur aucun des quatre modes de lancement du README.
    """
    exe = shutil.which("rg")
    if exe is None:
        raise ToolError(
            IO_ERROR,
            "ripgrep (rg) introuvable dans le PATH du processus qui exécute le "
            "serveur — il est requis par kb_search, qui n'a aucun repli. Le "
            "transport étant stdio, ce PATH est celui du client qui a lancé le "
            "serveur : installer ripgrep là où le serveur tourne réellement "
            "(`apt-get install ripgrep`), ou lancer le serveur dans le "
            "conteneur qui l'a déjà. Voir .devcontainer/post-create.sh, qui "
            "l'installe à la création du devcontainer, et README.md, "
            "« Connecter un client Claude ».",
        )
    return exe


def _run_rg(pattern: str, root: Path, *, fixed: bool) -> dict[str, list[int]]:
    """Exécute ripgrep sur un répertoire du corpus et retourne
    {chemin absolu: [numéros de ligne]}.

    Le format `--json` est préféré au format ligne `path:line:text` : ce dernier
    est ambigu dès qu'un chemin contient un « : ».
    """
    options = ["--glob", "*.md"]
    for glob in _EXCLUDE_GLOBS:
        options += ["--glob", glob]
    return _rg(pattern, fixed, options, [str(root)])


#: Fichiers passés à un même appel de ripgrep : la ligne de commande reste très
#: en deçà de `ARG_MAX`, même pour une file de plusieurs milliers de propositions.
FILES_PER_RG_CALL = 500


def _run_rg_files(pattern: str, files: list[Path], *, fixed: bool) -> dict[str, list[int]]:
    """Exécute ripgrep sur une liste **explicite** de fichiers (rév. 4.7).

    Sert aux propositions en attente : la liste vient de `Base.proposal_files`,
    qui en fait le confinement (§ 5.3) — ripgrep ne lit rien d'autre, et ne
    reçoit donc aucun glob. Un fichier déplacé vers `accepted/` entre la liste
    et la lecture (une résolution concurrente, § 4.4.d) n'est pas une erreur
    d'entrée : il est ignoré.
    """
    hits: dict[str, list[int]] = {}
    for i in range(0, len(files), FILES_PER_RG_CALL):
        lot = [str(f) for f in files[i : i + FILES_PER_RG_CALL]]
        hits.update(_rg(pattern, fixed, [], lot, missing_ok=True))
    return hits


def _rg(
    pattern: str, fixed: bool, options: list[str], targets: list[str], *, missing_ok: bool = False
) -> dict[str, list[int]]:
    argv = [
        _rg_binary(),
        "--json",
        "--ignore-case",
        "--no-ignore",  # indépendance vis-à-vis des .gitignore du bundle
        *options,
    ]
    if fixed:
        argv.append("--fixed-strings")
    argv += ["--regexp", pattern, "--", *targets]

    try:
        proc = subprocess.run(
            argv, capture_output=True, timeout=RG_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(IO_ERROR, f"ripgrep a dépassé {RG_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise ToolError(IO_ERROR, f"ripgrep n'a pas pu être lancé : {exc}") from exc

    stdout = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode not in (0, 1):
        # Code 2 avec un bilan (`summary`) : la recherche a eu lieu, un fichier
        # nommé a seulement disparu entre-temps. Sans bilan, ripgrep n'a rien
        # cherché — l'expression est en cause.
        if not (missing_ok and '"type":"summary"' in stdout):
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            # Une expression régulière invalide est une erreur d'entrée, pas une
            # défaillance : le message de ripgrep est remonté tel quel (§ 5.2).
            raise ToolError(INVALID_INPUT, stderr or f"ripgrep a échoué (code {proc.returncode})")

    hits: dict[str, list[int]] = {}
    for raw in stdout.splitlines():
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path_obj = data.get("path", {})
        path = path_obj.get("text")
        if path is None:
            # Chemin non-UTF-8 : ripgrep le rend en base64. Le corpus étant
            # spécifié UTF-8 (§ 1.6), on ignore le cas plutôt que d'échouer.
            continue
        line_no = data.get("line_number")
        if isinstance(line_no, int):
            hits.setdefault(path, []).append(line_no)
    return hits


def _merge(target: dict[str, DocHits], hits: dict[str, list[int]], retenir: Callable[[str], bool]) -> None:
    for path, lines in hits.items():
        # Filtrage de vérité, appliqué par le hub et non délégué aux globs de
        # ripgrep : la liste d'exclusions transverse (§ 5.2) pour le corpus, la
        # liste confinée de `Base.proposal_files` pour les propositions.
        if not retenir(path):
            continue
        doc = target.get(path)
        if doc is None:
            doc = DocHits(path=Path(path))
            target[path] = doc
        doc.terms_hit += 1
        doc.match_count += len(lines)
        doc.lines = sorted(set(doc.lines) | set(lines))


_TERM_SPLIT = re.compile(r"\s+")


def split_terms(query: str) -> list[str]:
    return [t for t in _TERM_SPLIT.split(query.strip()) if t]


@dataclass
class SearchOutcome:
    docs: list[DocHits]
    partial: bool
    """True si le repli OR a été utilisé (aucun document ne contient tous les termes)."""
    total: int = 0
    """Documents qui répondent, avant la coupe à `max_results`."""
    examined: int = 0
    """Propositions examinées (`run_search_pending` seulement)."""

    @property
    def reserved_count(self) -> int:
        return sum(1 for d in self.docs if d.reserved)


def run_search(base: Base, query: str, mode: str, max_results: int) -> SearchOutcome:
    """Exécute la recherche selon le mode demandé (§ 5.2)."""
    corpus = base.corpus_dir
    if not corpus.is_dir():
        return SearchOutcome([], False)
    return _search(
        query, mode, max_results,
        lambda motif, fixed: _run_rg(motif, corpus, fixed=fixed),
        lambda path: not base.is_excluded(Path(path), is_dir=False),
    )


def run_search_pending(base: Base, query: str, mode: str, max_results: int) -> SearchOutcome:
    """La même recherche, dans les propositions en attente (§ 5.2, rév. 4.7).

    Seconde exception à la liste d'exclusions transverse, en lecture seule : les
    fichiers lus sont ceux de `Base.proposal_files("pending")`, qui les résout et
    les confine à `proposals/pending/` — un lien symbolique sortant n'y figure
    pas, et ripgrep ne lit que cette liste. Même dialecte, même ET strict puis
    repli en OU, même classement que le corpus : `kb_search` (`include_pending`)
    et l'Explorer de l'interface web (§ 5 bis.1) en sont les deux appelants.
    """
    files = base.pending_files()
    if not files:
        return SearchOutcome([], False)
    permis = {str(f) for f in files}
    issue = _search(
        query, mode, max_results,
        lambda motif, fixed: _run_rg_files(motif, files, fixed=fixed),
        permis.__contains__,
    )
    issue.examined = len(files)
    return issue


def _search(
    query: str,
    mode: str,
    max_results: int,
    chercher: Callable[[str, bool], dict[str, list[int]]],
    retenir: Callable[[str], bool],
) -> SearchOutcome:
    if mode == "regex":
        docs: dict[str, DocHits] = {}
        _merge(docs, chercher(query, False), retenir)
        ranked = sorted(
            docs.values(), key=lambda d: (d.reserved, -d.match_count, str(d.path))
        )
        return SearchOutcome(ranked[:max_results], False, len(ranked))

    terms = split_terms(query)
    if not terms:
        raise ToolError(INVALID_INPUT, "query vide")

    per_term: list[dict[str, list[int]]] = [chercher(t, True) for t in terms]

    docs = {}
    for hits in per_term:
        _merge(docs, hits, retenir)

    # AND strict d'abord : documents touchant *tous* les termes.
    # `d.reserved` en tête de clé : les sommaires et journaux passent derrière
    # tout document de connaissance, et ne remontent donc que faute de mieux.
    strict = [d for d in docs.values() if d.terms_hit == len(terms)]
    if strict:
        ranked = sorted(strict, key=lambda d: (d.reserved, -d.match_count, str(d.path)))
        return SearchOutcome(ranked[:max_results], False, len(ranked))

    # Repli OR : classement par nombre de termes touchés, puis densité.
    ranked = sorted(
        docs.values(),
        key=lambda d: (d.reserved, -d.terms_hit, -d.density, -d.match_count, str(d.path)),
    )
    return SearchOutcome(ranked[:max_results], bool(ranked), len(ranked))
