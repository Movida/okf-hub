"""Recherche plein texte via ripgrep (§ 5.2)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
    exe = shutil.which("rg")
    if exe is None:
        raise ToolError(
            IO_ERROR,
            "ripgrep (rg) introuvable dans le PATH — il est requis par kb_search "
            "(voir .devcontainer/devcontainer.json)",
        )
    return exe


def _run_rg(pattern: str, root: Path, *, fixed: bool) -> dict[str, list[int]]:
    """Exécute ripgrep et retourne {chemin absolu: [numéros de ligne]}.

    Le format `--json` est préféré au format ligne `path:line:text` : ce dernier
    est ambigu dès qu'un chemin contient un « : ».
    """
    argv = [
        _rg_binary(),
        "--json",
        "--ignore-case",
        "--no-ignore",  # indépendance vis-à-vis des .gitignore du bundle
        "--glob",
        "*.md",
    ]
    for glob in _EXCLUDE_GLOBS:
        argv += ["--glob", glob]
    if fixed:
        argv.append("--fixed-strings")
    argv += ["--regexp", pattern, "--", str(root)]

    try:
        proc = subprocess.run(
            argv, capture_output=True, timeout=RG_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(IO_ERROR, f"ripgrep a dépassé {RG_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise ToolError(IO_ERROR, f"ripgrep n'a pas pu être lancé : {exc}") from exc

    if proc.returncode not in (0, 1):
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        # Une expression régulière invalide est une erreur d'entrée, pas une
        # défaillance : le message de ripgrep est remonté tel quel (§ 5.2).
        raise ToolError(INVALID_INPUT, stderr or f"ripgrep a échoué (code {proc.returncode})")

    hits: dict[str, list[int]] = {}
    for raw in proc.stdout.decode("utf-8", errors="replace").splitlines():
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


def _merge(target: dict[str, DocHits], hits: dict[str, list[int]], base: Base) -> None:
    for path, lines in hits.items():
        # Filtrage de vérité : la liste d'exclusions transverse (§ 5.2) est
        # appliquée par le hub, pas déléguée aux globs de ripgrep.
        if base.is_excluded(Path(path), is_dir=False):
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

    @property
    def reserved_count(self) -> int:
        return sum(1 for d in self.docs if d.reserved)


def run_search(base: Base, query: str, mode: str, max_results: int) -> SearchOutcome:
    """Exécute la recherche selon le mode demandé (§ 5.2)."""
    corpus = base.corpus_dir
    if not corpus.is_dir():
        return SearchOutcome([], False)

    if mode == "regex":
        hits = _run_rg(query, corpus, fixed=False)
        docs: dict[str, DocHits] = {}
        _merge(docs, hits, base)
        ranked = sorted(
            docs.values(), key=lambda d: (d.reserved, -d.match_count, str(d.path))
        )
        return SearchOutcome(ranked[:max_results], False)

    terms = split_terms(query)
    if not terms:
        raise ToolError(INVALID_INPUT, "query vide")

    per_term: list[dict[str, list[int]]] = [_run_rg(t, corpus, fixed=True) for t in terms]

    docs = {}
    for hits in per_term:
        _merge(docs, hits, base)

    # AND strict d'abord : documents touchant *tous* les termes.
    # `d.reserved` en tête de clé : les sommaires et journaux passent derrière
    # tout document de connaissance, et ne remontent donc que faute de mieux.
    strict = [d for d in docs.values() if d.terms_hit == len(terms)]
    if strict:
        ranked = sorted(strict, key=lambda d: (d.reserved, -d.match_count, str(d.path)))
        return SearchOutcome(ranked[:max_results], False)

    # Repli OR : classement par nombre de termes touchés, puis densité.
    ranked = sorted(
        docs.values(),
        key=lambda d: (d.reserved, -d.terms_hit, -d.density, -d.match_count, str(d.path)),
    )
    return SearchOutcome(ranked[:max_results], bool(ranked))
