"""Moteur du rôle gestionnaire (§ 6.2, § 7) — support déterministe de la skill kb-review.

Ce module porte tout ce qui doit être *mécaniquement* correct dans une revue :
acquisition du verrou à la bonne granularité, mise à jour du frontmatter,
déplacement des propositions, message de commit et trailers d'audit. La session
Claude apporte le jugement (pertinence, golden rules, regroupement) ; elle ne
reproduit jamais le protocole de verrouillage à la main — c'est le genre de
consigne qui échoue silencieusement (§ 7).

Usage :
    python -m okf_hub.review context   <base>
    python -m okf_hub.review inventory <base> [--full]
    python -m okf_hub.review reconcile <base> [--apply]
    python -m okf_hub.review resolve   <base> --plan <plan.json> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import gitops, hublog
from .config import HubConfig
from .errors import INVALID_INPUT, NOT_FOUND, ToolError
from .governance import DRAFT, status_of_text
from .locking import base_lock, ensure_git_exclude
from .mdutil import extract_section, parse_document
from .registry import ACCEPTED_SUBDIR, PENDING_SUBDIR, REJECTED_SUBDIR, Base, Registry

RESOLUTIONS = ("accepted", "rejected")
REJECTION_REASON_MAX = 500
SUMMARY_MAX = 60


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- inventaire ---------------------------------------------------------------


@dataclass
class Proposal:
    id: str
    path: Path
    frontmatter: dict
    body: str
    tracked: bool
    """Présente dans le tree de HEAD — donc couverte par un commit de soumission."""

    @property
    def submitted_at(self) -> str:
        return str(self.frontmatter.get("submitted-at") or "")

    @property
    def submitted_by(self) -> str:
        return str(self.frontmatter.get("submitted-by") or "")

    @property
    def concerns(self) -> str:
        return str(self.frontmatter.get("concerns") or "")

    @property
    def malformed(self) -> bool:
        """Frontmatter absent, ou dépourvu des champs indispensables à l'audit."""
        return not (self.frontmatter.get("id") and self.frontmatter.get("submitted-by"))


def _head_pending(base: Base) -> set[str]:
    """Chemins de proposals/pending/ présents dans le tree de HEAD.

    On interroge HEAD plutôt que l'index : c'est HEAD qui porte l'invariant
    d'audit (§ 6.2), et un index partagé peut être désynchronisé par n'importe
    quel outil git tiers agissant sur le dépôt.
    """
    if not gitops.has_head(base.root):
        return set()
    out = gitops.git_output(base.root, ["ls-tree", "-r", "HEAD", "--name-only", "--", PENDING_SUBDIR])
    return {line.strip() for line in out.splitlines() if line.strip()}


def load_pending(base: Base) -> list[Proposal]:
    """Propositions en attente, triées par date de soumission (§ 7.1.3)."""
    suivis = _head_pending(base)
    out: list[Proposal] = []
    for path in base.pending_files():
        rel = path.relative_to(base.root).as_posix()
        try:
            doc = parse_document(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            hublog.warning(f"proposition illisible {rel} : {exc}")
            continue
        fm = doc.frontmatter or {}
        out.append(
            Proposal(
                id=str(fm.get("id") or path.stem),
                path=path,
                frontmatter=fm,
                body=doc.body.strip(),
                tracked=rel in suivis,
            )
        )
    out.sort(key=lambda p: (p.submitted_at, p.id))
    return out


# --- étape 0 : réconciliation (§ 7.1.0) ---------------------------------------


@dataclass
class ReconcileReport:
    recovered: list[str] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    already_tracked: int = 0
    applied: bool = False


def reconcile(base: Base, apply: bool) -> ReconcileReport:
    """Rattrape la fenêtre de crash du § 5.5.

    Un crash entre le `rename()` et le commit laisse un fichier de proposition
    valide mais absent de l'histoire git. Le commit de récupération tient alors
    lieu de commit de soumission et restaure l'invariant d'audit (§ 6.2).
    """
    report = ReconcileReport(applied=apply)
    pending = load_pending(base)
    a_commiter: list[Proposal] = []

    for prop in pending:
        if prop.tracked:
            report.already_tracked += 1
            continue
        if prop.malformed:
            # Signalé à l'humain, jamais commité : on ne fabrique pas une
            # identité de contributeur pour un fichier qu'on ne comprend pas.
            report.malformed.append(prop.path.name)
            continue
        a_commiter.append(prop)

    if not apply:
        report.recovered = [p.id for p in a_commiter]
        return report

    for prop in a_commiter:
        with base_lock(base.root):
            ensure_git_exclude(base.root)
            subject = (
                f"proposal: {prop.id} ({prop.frontmatter.get('type', 'observation')}) — "
                f"{prop.concerns[:SUMMARY_MAX].rstrip()} (recovered)"
            )
            message = gitops.build_message(
                subject, [("Submitted-By", _one_line(prop.submitted_by))]
            )
            gitops.commit_paths(base.root, [prop.path], message)
        report.recovered.append(prop.id)
        hublog.info(f"proposition récupérée : {prop.id}")

    return report


def _one_line(value: str) -> str:
    """Défense en profondeur : le frontmatter d'un fichier déposé à la main
    peut contenir un retour à la ligne que kb_propose aurait refusé (§ 5.5)."""
    return " ".join(str(value).split())


# --- résolution (§ 6.2) -------------------------------------------------------


@dataclass
class Resolution:
    id: str
    resolution: str
    integrated_into: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Edit:
    path: str
    """Chemin relatif à corpus-dir."""
    content: str | None = None
    append: str | None = None
    section: str | None = None
    """Avec `content` : remplace cette section au lieu du fichier entier."""
    frontmatter: dict = field(default_factory=dict)
    """Champs à fusionner dans le frontmatter (fraîcheur, § 7.1.7)."""


@dataclass
class Plan:
    summary: str
    reviewed_by: str
    resolutions: list[Resolution]
    edits: list[Edit]


def parse_plan(raw: dict) -> Plan:
    if not isinstance(raw, dict):
        raise ToolError(INVALID_INPUT, "le plan doit être un objet JSON")

    summary = _require(raw, "summary", str)
    reviewed_by = _require(raw, "reviewed_by", str)
    for label, value in (("summary", summary), ("reviewed_by", reviewed_by)):
        if "\n" in value or "\r" in value:
            raise ToolError(INVALID_INPUT, f"{label} ne doit pas contenir de retour à la ligne")

    raw_res = raw.get("resolutions")
    if not isinstance(raw_res, list) or not raw_res:
        raise ToolError(INVALID_INPUT, "resolutions doit être une liste non vide")

    resolutions: list[Resolution] = []
    for i, item in enumerate(raw_res):
        if not isinstance(item, dict):
            raise ToolError(INVALID_INPUT, f"resolutions[{i}] doit être un objet")
        rid = _require(item, "id", str, f"resolutions[{i}].")
        kind = _require(item, "resolution", str, f"resolutions[{i}].")
        if kind not in RESOLUTIONS:
            raise ToolError(
                INVALID_INPUT,
                f"resolutions[{i}].resolution doit valoir {' ou '.join(RESOLUTIONS)}",
            )
        reason = item.get("reason", "")
        if kind == "rejected" and not str(reason).strip():
            # § 6.2 : un rejet porte toujours son motif — c'est la seule trace
            # que le contributeur pourra retrouver.
            raise ToolError(INVALID_INPUT, f"resolutions[{i}] : un rejet exige un motif (reason)")
        if len(str(reason)) > REJECTION_REASON_MAX:
            raise ToolError(
                INVALID_INPUT, f"resolutions[{i}].reason dépasse {REJECTION_REASON_MAX} caractères"
            )
        into = item.get("integrated_into") or []
        if not isinstance(into, list) or not all(isinstance(p, str) for p in into):
            raise ToolError(INVALID_INPUT, f"resolutions[{i}].integrated_into : liste de chemins")
        resolutions.append(
            Resolution(id=rid, resolution=kind, integrated_into=into, reason=str(reason))
        )

    ids = [r.id for r in resolutions]
    if len(set(ids)) != len(ids):
        raise ToolError(INVALID_INPUT, "une proposition apparaît deux fois dans le plan")

    edits: list[Edit] = []
    for i, item in enumerate(raw.get("edits") or []):
        if not isinstance(item, dict):
            raise ToolError(INVALID_INPUT, f"edits[{i}] doit être un objet")
        edit = Edit(
            path=_require(item, "path", str, f"edits[{i}]."),
            content=item.get("content"),
            append=item.get("append"),
            section=item.get("section"),
            frontmatter=item.get("frontmatter") or {},
        )
        if edit.content is None and edit.append is None and not edit.frontmatter:
            raise ToolError(
                INVALID_INPUT, f"edits[{i}] : fournir content, append ou frontmatter"
            )
        if edit.content is not None and edit.append is not None:
            raise ToolError(INVALID_INPUT, f"edits[{i}] : content et append sont exclusifs")
        if not isinstance(edit.frontmatter, dict):
            raise ToolError(INVALID_INPUT, f"edits[{i}].frontmatter doit être un objet")
        edits.append(edit)

    return Plan(summary=summary, reviewed_by=reviewed_by, resolutions=resolutions, edits=edits)


def _require(data: dict, key: str, kind: type, prefix: str = ""):
    value = data.get(key)
    if not isinstance(value, kind) or (kind is str and not value.strip()):
        raise ToolError(INVALID_INPUT, f"champ requis manquant ou mal typé : {prefix}{key}")
    return value.strip() if kind is str else value


def _apply_edit(base: Base, edit: Edit) -> Path:
    """Applique une édition du corpus. Le confinement est celui de kb_read (§ 5.3)."""
    candidate = Path(edit.path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ToolError(INVALID_INPUT, f"chemin d'édition refusé : {edit.path}")
    target = (base.corpus_dir / candidate).resolve()
    if base.corpus_dir not in target.parents:
        raise ToolError(INVALID_INPUT, f"chemin d'édition hors du corpus : {edit.path}")
    if base.is_excluded(target, is_dir=False):
        raise ToolError(INVALID_INPUT, f"chemin d'édition exclu du corpus : {edit.path}")

    existing = target.read_text(encoding="utf-8") if target.is_file() else ""

    if edit.content is not None and edit.section:
        match = extract_section(existing, edit.section)
        if match is None:
            raise ToolError(
                NOT_FOUND, f"section '{edit.section}' introuvable dans {edit.path}"
            )
        lines = existing.split("\n")
        fin = len(lines)
        from .mdutil import parse_headings

        for h in parse_headings(existing):
            if h.line > match.heading.line and h.level <= match.heading.level:
                fin = h.line
                break
        nouveau = "\n".join(
            lines[: match.heading.line] + edit.content.rstrip().split("\n") + [""] + lines[fin:]
        )
    elif edit.content is not None:
        nouveau = edit.content
    elif edit.append is not None:
        separateur = "" if not existing or existing.endswith("\n\n") else (
            "\n" if existing.endswith("\n") else "\n\n"
        )
        nouveau = existing + separateur + edit.append.rstrip() + "\n"
    else:
        nouveau = existing

    if edit.frontmatter:
        nouveau = _merge_frontmatter(nouveau, edit.frontmatter)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(nouveau if nouveau.endswith("\n") else nouveau + "\n", encoding="utf-8")
    return target


def _merge_frontmatter(text: str, updates: dict) -> str:
    """Fusionne des champs dans le frontmatter, via la bibliothèque YAML (§ 1.7)."""
    doc = parse_document(text)
    data = dict(doc.frontmatter or {})
    data.update(updates)
    block = yaml.safe_dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False, width=10_000
    )
    body = doc.body if doc.frontmatter_raw else text
    return f"---\n{block}---\n{body if body.startswith(chr(10)) else chr(10) + body}"


def _resolve_proposal(base: Base, prop: Proposal, res: Resolution) -> tuple[Path, Path]:
    """Enrichit le frontmatter et déplace le fichier. Retourne (ancien, nouveau)."""
    fm = dict(prop.frontmatter)
    fm["status"] = res.resolution
    fm["resolution"] = res.resolution
    fm["resolved-at"] = _now()
    if res.resolution == "accepted":
        fm["integrated-into"] = list(res.integrated_into)
        fm.pop("rejection-reason", None)
    else:
        fm["rejection-reason"] = res.reason
        fm.pop("integrated-into", None)

    block = yaml.safe_dump(
        fm, allow_unicode=True, default_flow_style=False, sort_keys=False, width=10_000
    )
    contenu = f"---\n{block}---\n\n{prop.body}\n"

    sous_dossier = ACCEPTED_SUBDIR if res.resolution == "accepted" else REJECTED_SUBDIR
    destination = base.root / sous_dossier / prop.path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(contenu, encoding="utf-8")
    prop.path.unlink()
    return prop.path, destination


def build_subject(plan: Plan) -> str:
    """Sujet de commit de résolution (§ 6.2)."""
    accepted = [r for r in plan.resolutions if r.resolution == "accepted"]
    n = len(plan.resolutions)
    verbe = "integrate" if accepted else "reject"
    if n == 1:
        return f"{verbe}: {plan.resolutions[0].id} — {plan.summary}"
    return f"{verbe}: {n} proposals — {plan.summary}"


def apply_plan(base: Base, plan: Plan) -> str:
    """Exécute une résolution complète sous UNE seule acquisition du verrou.

    Granularité imposée § 4.4.b.3 : éditions du corpus, déplacements et commit
    dans la même section critique. Une résolution par lot produit un unique
    commit portant un trailer `Proposal:` par proposition — jamais des commits
    séparés, qui laisseraient le corpus dans des états intermédiaires
    incohérents (§ 6.2).
    """
    with base_lock(base.root):
        ensure_git_exclude(base.root)

        index = {p.id: p for p in load_pending(base)}
        manquantes = [r.id for r in plan.resolutions if r.id not in index]
        if manquantes:
            raise ToolError(
                NOT_FOUND,
                f"propositions absentes de pending/ : {', '.join(manquantes)}",
            )

        touches: list[Path] = []
        for edit in plan.edits:
            touches.append(_apply_edit(base, edit))

        trailers: list[tuple[str, str]] = []
        for res in plan.resolutions:
            prop = index[res.id]
            ancien, nouveau = _resolve_proposal(base, prop, res)
            touches += [ancien, nouveau]
            trailers.append(("Proposal", res.id))
            trailers.append(("Submitted-By", _one_line(prop.submitted_by)))
        trailers.append(("Reviewed-By", plan.reviewed_by))

        message = gitops.build_message(build_subject(plan), trailers)
        sha = gitops.commit_paths(base.root, touches, message)

    return sha


# --- CLI ----------------------------------------------------------------------


def _registry(hub_root: Path | None) -> Registry:
    from .__main__ import _default_hub_root

    root = (hub_root or _default_hub_root()).resolve()
    config = HubConfig.load(root)
    hublog.configure(config.log_file, echo_stderr=False)
    reg = Registry(config)
    reg.scan()
    return reg


def _corpus_outline(base: Base, limit: int = 300) -> str:
    lignes = []
    for path in base.iter_documents()[:limit]:
        doc = parse_document(path.read_text(encoding="utf-8", errors="replace"))
        lignes.append(f"- {base.rel_path(path)} — {doc.title or '(sans titre)'}")
    total = base.count_documents()
    if total > limit:
        lignes.append(f"[… {total - limit} documents supplémentaires non listés]")
    return "\n".join(lignes) or "(corpus vide)"


def cmd_context(base: Base) -> str:
    m = base.manifest
    regles = m.governance_rules.read_text(encoding="utf-8")
    parts = [f"# Contexte de revue — {m.name} ({m.title})"]
    # § B5 : la skill doit prévenir l'humain avant de lui faire arbitrer selon
    # des règles que personne n'a encore validées.
    if status_of_text(regles, source=str(m.governance_rules)) == DRAFT:
        parts.append(
            "\n⚠ GOUVERNANCE EN BROUILLON (`status: draft`) — les règles appliquées "
            "dans cette revue ne sont pas validées. Signale-le à l'humain avant "
            "de présenter tes recommandations."
        )
    parts += [
        f"\nrépertoire : {base.root}",
        f"corpus : {base.corpus_dir}",
        f"\n## {m.governance_rules.name}\n",
        regles,
    ]
    if m.frontmatter_schema:
        parts += [
            f"\n## {m.frontmatter_schema.name}\n",
            "```yaml\n" + m.frontmatter_schema.read_text(encoding="utf-8").rstrip() + "\n```",
        ]
    else:
        parts.append("\n[aucun schema.yaml : aucune contrainte de frontmatter documentée]")
    parts += ["\n## Structure du corpus\n", _corpus_outline(base)]
    return "\n".join(parts)


def cmd_inventory(base: Base, full: bool) -> str:
    pending = load_pending(base)
    if not pending:
        return f"Aucune proposition en attente dans '{base.name}'."
    parts = [f"# {len(pending)} proposition(s) en attente — {base.name}"]
    for p in pending:
        entete = [
            f"\n## {p.id}",
            f"- type : {p.frontmatter.get('type', '?')}",
            f"- concerns : {p.concerns}",
            f"- confidence : {p.frontmatter.get('confidence', '?')}",
            f"- submitted-by : {p.submitted_by}",
            f"- submitted-at : {p.submitted_at}",
            f"- sources : {p.frontmatter.get('sources', [])}",
        ]
        if not p.tracked:
            entete.append("- ⚠ absente du tree de HEAD (voir `reconcile`)")
        if p.malformed:
            entete.append("- ⚠ frontmatter incomplet")
        parts.append("\n".join(entete))
        if full:
            parts.append(f"\n### corps de {p.id}\n\n{p.body}")
    if not full:
        parts.append("\n[corps des propositions omis — relancer avec --full]")
    return "\n".join(parts)


def cmd_reconcile(base: Base, apply: bool) -> str:
    report = reconcile(base, apply)
    verbe = "Récupérée(s)" if apply else "À récupérer"
    lignes = [
        f"# Réconciliation — {base.name}",
        f"propositions déjà couvertes par un commit : {report.already_tracked}",
        f"{verbe} : {len(report.recovered)}" + (
            " — " + ", ".join(report.recovered) if report.recovered else ""
        ),
    ]
    if report.malformed:
        lignes += [
            "",
            "⚠ Fichiers au frontmatter incomplet, NON commités — à examiner à la main :",
            *(f"- proposals/pending/{n}" for n in report.malformed),
        ]
    if not apply and report.recovered:
        lignes += ["", "Relancer avec --apply pour créer les commits de récupération."]
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="okf-review", description=__doc__.split("\n")[0])
    parser.add_argument("--hub-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("context", "inventory", "reconcile"):
        p = sub.add_parser(name)
        p.add_argument("base")
    sub.choices["inventory"].add_argument("--full", action="store_true")
    sub.choices["reconcile"].add_argument("--apply", action="store_true")

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("base")
    p_resolve.add_argument("--plan", type=Path, required=True)
    p_resolve.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)

    try:
        registry = _registry(args.hub_root)
        base = registry.get(args.base)

        if args.command == "context":
            print(cmd_context(base))
        elif args.command == "inventory":
            print(cmd_inventory(base, args.full))
        elif args.command == "reconcile":
            print(cmd_reconcile(base, args.apply))
        elif args.command == "resolve":
            raw = json.loads(args.plan.read_text(encoding="utf-8"))
            plan = parse_plan(raw)
            if args.dry_run:
                print("Plan valide. Sujet du commit :")
                print(f"  {build_subject(plan)}")
                for res in plan.resolutions:
                    detail = (
                        ", ".join(res.integrated_into) if res.resolution == "accepted"
                        else res.reason
                    )
                    print(f"  - {res.id} → {res.resolution} : {detail}")
                for edit in plan.edits:
                    quoi = "contenu" if edit.content is not None else (
                        "ajout" if edit.append is not None else "frontmatter"
                    )
                    print(f"  - édition {edit.path} ({quoi})")
                return 0
            sha = apply_plan(base, plan)
            print(f"Résolution commitée : {sha}")
    except ToolError as exc:
        print(f"ERROR: {exc.code}: {exc.message}", file=sys.stderr)
        return 4
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
