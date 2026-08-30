"""Manifeste `okf-bundle.yaml` : parsing et validation (§ 3.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .textutil import normalize_inline

MANIFEST_FILENAME = "okf-bundle.yaml"
SUPPORTED_BUNDLE_SPEC = "0.1"

DEFAULT_CORPUS_DIR = "knowledge"

NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
TITLE_MAX = 100
DESCRIPTION_MAX = 500

PROPOSALS_DIRNAME = "proposals"


class ManifestError(Exception):
    """Le bundle est invalide : il sera ignoré avec un avertissement (§ 3.3)."""


@dataclass
class Manifest:
    bundle_spec: str
    name: str
    title: str
    description: str
    governance_rules: Path
    """Chemin absolu du fichier de golden rules."""
    corpus_dir: Path
    """Chemin absolu du répertoire de corpus, résolu."""
    version: str | None = None
    okf_spec: str | None = None
    frontmatter_schema: Path | None = None
    review: str = "human"
    warnings: list[str] = field(default_factory=list)
    """Avertissements non bloquants, journalisés et remontés par kb_hub_rescan."""


def load_manifest(bundle_root: Path) -> Manifest:
    """Charge et valide le manifeste d'un bundle.

    Lève `ManifestError` si le bundle doit être ignoré. Les écarts tolérés
    (description trop longue, bundle-spec inconnu) remontent dans
    `Manifest.warnings` au lieu de faire échouer le chargement.
    """
    bundle_root = bundle_root.resolve()
    path = bundle_root / MANIFEST_FILENAME
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"{MANIFEST_FILENAME} illisible : {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{MANIFEST_FILENAME} non parseable : {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{MANIFEST_FILENAME} : la racine doit être une correspondance YAML")

    warnings: list[str] = []

    # --- champs obligatoires ---
    bundle_spec = _require_str(data, "bundle-spec")
    name = _require_str(data, "name")
    title = _require_str(data, "title")
    description_raw = _require_str(data, "description")

    governance = data.get("governance")
    if not isinstance(governance, dict):
        raise ManifestError("champ obligatoire manquant ou mal typé : governance")
    rules_rel = governance.get("rules")
    if not isinstance(rules_rel, str) or not rules_rel.strip():
        raise ManifestError("champ obligatoire manquant : governance.rules")

    # --- name ---
    if not NAME_PATTERN.match(name):
        raise ManifestError(f"name '{name}' non conforme au motif [a-z0-9-]+")

    # --- title : ≤ 100 car., sans retour à la ligne (après parsing YAML) ---
    if "\n" in title or "\r" in title:
        raise ManifestError("title contient un retour à la ligne")
    if len(title) > TITLE_MAX:
        raise ManifestError(f"title dépasse {TITLE_MAX} caractères ({len(title)})")

    # --- description : normalisée puis plafonnée, avec avertissement ---
    # Motif § 3.3 : title et description sont injectés dans les descriptions
    # d'outils MCP, donc dans le contexte de toutes les sessions connectées.
    description = normalize_inline(description_raw)
    if len(description) > DESCRIPTION_MAX:
        warnings.append(
            f"description tronquée à {DESCRIPTION_MAX} caractères "
            f"(longueur d'origine : {len(description)})"
        )
        description = description[:DESCRIPTION_MAX].rstrip() + "…"

    # --- corpus-dir ---
    corpus_raw = data.get("corpus-dir", DEFAULT_CORPUS_DIR)
    if not isinstance(corpus_raw, str) or not corpus_raw.strip():
        raise ManifestError("corpus-dir doit être une chaîne non vide")
    corpus_dir = _resolve_inside(bundle_root, corpus_raw, "corpus-dir")
    if not corpus_dir.is_dir():
        raise ManifestError(f"corpus-dir '{corpus_raw}' n'existe pas ou n'est pas un répertoire")
    if corpus_dir == bundle_root:
        raise ManifestError("corpus-dir ne peut pas être la racine du bundle")
    # Le corpus ne doit ni être ni contenir proposals/ : sinon la liste
    # d'exclusions transverse (§ 5.2) viderait le corpus silencieusement.
    proposals = bundle_root / PROPOSALS_DIRNAME
    if corpus_dir == proposals or _is_within(proposals, corpus_dir):
        raise ManifestError(
            f"corpus-dir '{corpus_raw}' est ou contient le répertoire {PROPOSALS_DIRNAME}/"
        )
    if _is_within(corpus_dir, proposals):
        raise ManifestError(
            f"corpus-dir '{corpus_raw}' est situé à l'intérieur de {PROPOSALS_DIRNAME}/"
        )

    # --- governance.rules ---
    governance_rules = _resolve_inside(bundle_root, rules_rel, "governance.rules")
    if not governance_rules.is_file():
        raise ManifestError(f"governance.rules '{rules_rel}' introuvable")

    schema_rel = governance.get("frontmatter-schema")
    frontmatter_schema: Path | None = None
    if isinstance(schema_rel, str) and schema_rel.strip():
        candidate = _resolve_inside(bundle_root, schema_rel, "governance.frontmatter-schema")
        if candidate.is_file():
            frontmatter_schema = candidate
        else:
            warnings.append(f"governance.frontmatter-schema '{schema_rel}' introuvable — ignoré")

    review = governance.get("review", "human")
    if review != "human":
        # Seule valeur v0 ; « agent » et « auto » sont [v1+]. On n'échoue pas :
        # cohérent avec la tolérance générale aux champs inconnus.
        warnings.append(f"governance.review='{review}' non supporté en v0 — traité comme 'human'")
        review = "human"

    # --- bundle-spec : tolérance de compatibilité (§ 3.3) ---
    if bundle_spec != SUPPORTED_BUNDLE_SPEC:
        warnings.append(
            f"bundle-spec '{bundle_spec}' différent de la version supportée "
            f"'{SUPPORTED_BUNDLE_SPEC}' — chargement tenté et réussi"
        )

    version = data.get("version")
    version = version if isinstance(version, str) else None
    okf_spec = data.get("okf-spec")
    okf_spec = okf_spec if isinstance(okf_spec, str) else None

    return Manifest(
        bundle_spec=bundle_spec,
        name=name,
        title=title,
        description=description,
        governance_rules=governance_rules,
        corpus_dir=corpus_dir,
        version=version,
        okf_spec=okf_spec,
        frontmatter_schema=frontmatter_schema,
        review=review,
        warnings=warnings,
    )


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        raise ManifestError(f"champ obligatoire manquant : {key}")
    if not isinstance(value, str):
        # `bundle-spec: 0.1` non quoté donne un float : message explicite.
        raise ManifestError(
            f"champ {key} doit être une chaîne (reçu {type(value).__name__} : {value!r}) "
            f"— pensez aux guillemets en YAML"
        )
    value = value.strip()
    if not value:
        raise ManifestError(f"champ obligatoire vide : {key}")
    return value


def _resolve_inside(root: Path, rel: str, label: str) -> Path:
    """Résout un chemin du manifeste et vérifie qu'il reste dans le bundle."""
    candidate = Path(rel)
    if candidate.is_absolute():
        raise ManifestError(f"{label} doit être un chemin relatif au bundle (reçu '{rel}')")
    resolved = (root / candidate).resolve()
    if resolved != root and not _is_within(resolved, root):
        raise ManifestError(f"{label} '{rel}' sort du bundle")
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    return parent in child.parents
