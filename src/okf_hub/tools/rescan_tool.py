"""kb_hub_rescan (§ 5.6)."""

from __future__ import annotations

from ..lecture.graphe import analyser, declarations
from ..lecture.modele import Lecteur
from ..registry import RescanReport, Registry

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

DESCRIPTION = (
    "Relance la découverte des bases dans bases-dir : à appeler après avoir "
    "importé un bundle (`git clone <url> bases/<nom>`). "
    "PORTÉE MONO-INSTANCE : ce rescan n'affecte que la session qui l'appelle. "
    "Les autres sessions Claude connectées à ce hub continueront d'ignorer une "
    "base nouvellement importée jusqu'à leur propre rescan ou redémarrage."
)


def description(registry: Registry) -> str:
    return DESCRIPTION


def render_report(report: RescanReport, total: int) -> str:
    lines = [f"Découverte terminée : {total} base(s) enregistrée(s)."]
    lines.append(f"ajoutées ({len(report.added)}) : " + (", ".join(report.added) or "—"))
    lines.append(f"retirées ({len(report.removed)}) : " + (", ".join(report.removed) or "—"))
    lines.append(f"inchangées ({len(report.unchanged)}) : " + (", ".join(report.unchanged) or "—"))

    if report.invalid:
        lines.append("")
        lines.append("Bundles invalides (ignorés) :")
        for dirname, reason in report.invalid:
            lines.append(f"- {dirname} : {reason}")

    if report.collisions:
        lines.append("")
        lines.append("Collisions de name (premier en ordre lexicographique retenu) :")
        for name, kept, ignored in report.collisions:
            lines.append(f"- '{name}' : '{kept}' retenu, '{ignored}' ignoré")

    if report.compat_warnings:
        lines.append("")
        lines.append("Avertissements :")
        for name, warn in report.compat_warnings:
            lines.append(f"- [{name}] {warn}")

    return "\n".join(lines)


def render_dependencies(registry: Registry) -> str | None:
    """Avertissements du graphe des bases (§ 3.3, rév. 4.5), ou None.

    Les références ne sont lues — donc les corpus chargés — que si une base au
    moins déclare `cites` : la déclaration est volontaire, et un hub qui n'en a
    aucune ne paie rien de plus qu'avant à chaque rescan.
    """
    noms = registry.names()
    if not declarations(registry, noms):
        return None
    graphe = analyser(Lecteur.de(registry), noms)
    if not graphe.avertissements:
        return "Dépendances entre bases (cites) : aucun avertissement."
    lignes = ["Dépendances entre bases (cites) :"]
    lignes += [f"- {a}" for a in graphe.avertissements]
    return "\n".join(lignes)


def run(registry: Registry, arguments: dict) -> str:
    report = registry.scan()
    texte = render_report(report, len(registry.bases))
    dependances = render_dependencies(registry)
    return texte if dependances is None else texte + "\n\n" + dependances
