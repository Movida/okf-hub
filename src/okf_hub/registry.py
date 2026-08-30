"""Découverte des bundles et accès au corpus (§ 4.2, § 5.2 exclusions, § 5.3 sécurité)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import hublog
from .config import HubConfig
from .errors import NOT_FOUND, ToolError, unknown_base
from .manifest import (
    MANIFEST_FILENAME,
    Manifest,
    ManifestError,
    PROPOSALS_DIRNAME,
    load_manifest,
)

LOCK_FILENAME = ".okf-hub.lock"

#: Liste d'exclusions transverse (§ 5.2), applicable à kb_search, kb_read et au
#: comptage de documents de kb_list. Les contraintes sur corpus-dir (§ 3.3)
#: mettent déjà ces fichiers hors corpus ; cette liste est une défense en
#: profondeur. Chemins relatifs à la racine du *bundle*.
EXCLUDED_DIRS = frozenset({PROPOSALS_DIRNAME, ".git"})
EXCLUDED_FILES = frozenset(
    {MANIFEST_FILENAME, "GOVERNANCE.md", "schema.yaml", "CLAUDE.md", LOCK_FILENAME}
)

PENDING_SUBDIR = f"{PROPOSALS_DIRNAME}/pending"
ACCEPTED_SUBDIR = f"{PROPOSALS_DIRNAME}/accepted"
REJECTED_SUBDIR = f"{PROPOSALS_DIRNAME}/rejected"

#: Statut d'une proposition → sous-répertoire. **L'emplacement fait foi**
#: (§ 6.2) : le champ `status` du frontmatter est redondant à dessein, et
#: kb_proposal_status signale une divergence sans s'y fier.
PROPOSAL_SUBDIRS: dict[str, str] = {
    "pending": PENDING_SUBDIR,
    "accepted": ACCEPTED_SUBDIR,
    "rejected": REJECTED_SUBDIR,
}


@dataclass(frozen=True)
class Base:
    """Une base enregistrée : un bundle valide découvert dans bases-dir."""

    manifest: Manifest
    root: Path
    """Racine du bundle (le clone git), résolue."""
    dir_name: str
    """Nom du répertoire dans bases-dir — peut différer de manifest.name (§ 3.3)."""

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def corpus_dir(self) -> Path:
        return self.manifest.corpus_dir

    @property
    def lock_path(self) -> Path:
        return self.root / LOCK_FILENAME

    @property
    def pending_dir(self) -> Path:
        return self.root / PENDING_SUBDIR

    # --- corpus -------------------------------------------------------------

    def iter_documents(self) -> list[Path]:
        """Tous les `*.md` sous corpus-dir, hors exclusions, triés (§ 2)."""
        docs: list[Path] = []
        corpus = self.corpus_dir
        if not corpus.is_dir():
            return docs
        # followlinks=False (défaut) : un lien symbolique vers un répertoire
        # extérieur ne peut pas faire entrer des fichiers hors bundle.
        for dirpath, dirnames, filenames in os.walk(corpus, followlinks=False):
            here = Path(dirpath)
            dirnames[:] = sorted(d for d in dirnames if not self.is_excluded(here / d, is_dir=True))
            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue
                full = here / fname
                if self.is_excluded(full, is_dir=False):
                    continue
                docs.append(full)
        return docs

    def count_documents(self) -> int:
        return len(self.iter_documents())

    def is_excluded(self, path: Path, is_dir: bool) -> bool:
        try:
            rel = path.relative_to(self.root)
        except ValueError:
            return True
        parts = rel.parts
        if any(p in EXCLUDED_DIRS for p in parts[:-1] if p):
            return True
        last = parts[-1] if parts else ""
        if is_dir:
            return last in EXCLUDED_DIRS
        return last in EXCLUDED_FILES

    def rel_path(self, path: Path) -> str:
        """Chemin relatif à corpus-dir, séparateur `/` (convention transverse § 5)."""
        return path.relative_to(self.corpus_dir).as_posix()

    def resolve_document(self, rel: str) -> Path:
        """Résout un chemin de document et vérifie son confinement (§ 5.3).

        Résolution canonique (symlinks compris) puis vérification d'inclusion
        stricte dans corpus-dir : `..` et liens sortants sont rejetés.
        """
        if not rel or not rel.strip():
            raise ToolError(NOT_FOUND, "chemin vide")
        candidate = Path(rel.strip())
        if candidate.is_absolute():
            raise ToolError(NOT_FOUND, f"chemin absolu refusé : '{rel}' (chemin relatif à corpus-dir attendu)")
        resolved = (self.corpus_dir / candidate).resolve()
        if resolved == self.corpus_dir or self.corpus_dir not in resolved.parents:
            raise ToolError(NOT_FOUND, f"chemin '{rel}' hors du corpus de la base '{self.name}'")
        if self.is_excluded(resolved, is_dir=False):
            raise ToolError(NOT_FOUND, f"chemin '{rel}' exclu du corpus")
        if not resolved.is_file():
            raise ToolError(NOT_FOUND, f"document '{rel}' introuvable dans la base '{self.name}'")
        return resolved

    # --- propositions -------------------------------------------------------

    def pending_files(self) -> list[Path]:
        return self.proposal_files("pending")

    def proposal_files(self, status: str) -> list[Path]:
        """Fichiers de proposition d'un statut donné, confinés à `proposals/`.

        Le confinement reprend la mécanique du § 5.3 : chaque candidat est
        résolu canoniquement (symlinks compris) puis vérifié comme strictement
        inclus dans `proposals/<statut>/`. Un lien symbolique déposé dans
        `pending/` et pointant hors du bundle ne peut donc pas faire lire un
        fichier extérieur.
        """
        sub = PROPOSAL_SUBDIRS.get(status)
        if sub is None:
            raise ValueError(f"statut de proposition inconnu : {status}")
        directory = self.root / sub
        if not directory.is_dir():
            return []
        canonical = directory.resolve()
        out: list[Path] = []
        for path in sorted(directory.iterdir()):
            if path.suffix != ".md":
                continue
            resolved = path.resolve()
            if canonical not in resolved.parents:
                continue
            if not resolved.is_file():
                continue
            out.append(path)
        return out


@dataclass
class RescanReport:
    """Sortie de kb_hub_rescan (§ 5.6)."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    invalid: list[tuple[str, str]] = field(default_factory=list)
    """(répertoire, motif)"""
    collisions: list[tuple[str, str, str]] = field(default_factory=list)
    """(name, répertoire retenu, répertoire ignoré)"""
    compat_warnings: list[tuple[str, str]] = field(default_factory=list)
    """(name, avertissement)"""

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


class Registry:
    """Cache local d'une instance du serveur.

    § 4.4.a : aucun état en mémoire ne fait autorité. La vérité est sur le
    disque ; ce registre est reconstruit à chaque rescan.
    """

    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self.bases: dict[str, Base] = {}
        self.last_report: RescanReport = RescanReport()

    # --- découverte ---------------------------------------------------------

    def scan(self) -> RescanReport:
        """Relance la découverte (§ 4.2) et remplace le registre."""
        report = RescanReport()
        found: dict[str, Base] = {}
        bases_dir = self.config.bases_dir

        if not bases_dir.is_dir():
            hublog.warning(f"bases-dir introuvable : {bases_dir}")
        else:
            # Ordre lexicographique : rend déterministe la résolution des
            # collisions de name (§ 3.3).
            for entry in sorted(bases_dir.iterdir(), key=lambda p: p.name):
                if not entry.is_dir():
                    continue
                # Répertoire caché : jamais une base. C'est ce qui permet au
                # déploiement des bundles livrés (`bootstrap`) de construire son
                # arbre dans `bases-dir` sans qu'un scan concurrent enregistre
                # un bundle à moitié copié.
                if entry.name.startswith("."):
                    continue
                if not (entry / MANIFEST_FILENAME).is_file():
                    continue
                try:
                    manifest = load_manifest(entry)
                except ManifestError as exc:
                    report.invalid.append((entry.name, str(exc)))
                    hublog.warning(f"bundle invalide ignoré : {entry.name} — {exc}")
                    continue
                except OSError as exc:
                    report.invalid.append((entry.name, f"erreur d'entrée/sortie : {exc}"))
                    hublog.warning(f"bundle illisible ignoré : {entry.name} — {exc}")
                    continue

                if manifest.name in found:
                    kept = found[manifest.name].dir_name
                    report.collisions.append((manifest.name, kept, entry.name))
                    hublog.warning(
                        f"collision de name '{manifest.name}' : "
                        f"'{kept}' retenu, '{entry.name}' ignoré"
                    )
                    continue

                for warn in manifest.warnings:
                    report.compat_warnings.append((manifest.name, warn))
                    hublog.warning(f"[{manifest.name}] {warn}")

                found[manifest.name] = Base(
                    manifest=manifest, root=entry.resolve(), dir_name=entry.name
                )

        previous = set(self.bases)
        current = set(found)
        report.added = sorted(current - previous)
        report.removed = sorted(previous - current)
        report.unchanged = sorted(current & previous)

        self.bases = found
        self.last_report = report
        hublog.info(
            f"découverte : {len(found)} base(s) — "
            f"+{len(report.added)} -{len(report.removed)} "
            f"={len(report.unchanged)}, {len(report.invalid)} invalide(s)"
        )
        return report

    # --- accès --------------------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self.bases)

    def get(self, name: str) -> Base:
        base = self.bases.get(name)
        if base is None:
            raise unknown_base(name, self.names())
        return base

    def ordered(self) -> list[Base]:
        return [self.bases[n] for n in self.names()]
