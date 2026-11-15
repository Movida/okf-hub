"""Les données d'une fiche : champs et blocs déclarés au profil (§ 3.5, § 5.8, rév. 4.8).

Une section que le profil déclare sous `data` porte, comme premier bloc de son
format, un tableau markdown ou un bloc de code `yaml` qui est une liste
d'objets. Chaque ligne du tableau, ou chaque objet, est une ligne de données.
Rien n'est deviné : sans déclaration, une section n'est que du texte.

Le bloc reste du markdown lisible sans le hub (§ 1.4). Un bloc absent ou mal
formé n'est pas une erreur de la fiche : l'avertissement le dit, pour ce
document, et le reste est rendu.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import yaml

from ..mdutil import extract_section, lines_outside_fences
from .profil import BlocDonnees

_SEPARATEUR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_CLOTURE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_-]*)")


@dataclass
class Extraction:
    lignes: list[dict] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)


def extraire(texte: str, bloc: BlocDonnees) -> Extraction:
    """Les lignes du bloc `bloc` dans le document `texte`."""
    out = Extraction()
    section = extract_section(texte, bloc.titre)
    if section is None:
        out.avertissements.append(f"« {bloc.titre} » : section absente")
        return out
    corps = section.content.split("\n", 1)[1] if "\n" in section.content else ""
    if bloc.format == "table":
        lignes = _tableau(corps)
    else:
        lignes = _yaml(corps, out.avertissements, bloc.titre)
    if lignes is None:
        if not out.avertissements:
            out.avertissements.append(
                f"« {bloc.titre} » : aucun {'tableau' if bloc.format == 'table' else 'bloc yaml'} dans la section"
            )
        return out
    out.lignes = lignes
    if bloc.colonnes:
        presentes = set().union(*(l.keys() for l in lignes)) if lignes else set()
        manquantes = [c for c in bloc.colonnes if c not in presentes]
        if manquantes:
            out.avertissements.append(f"« {bloc.titre} » : colonne(s) manquante(s) : {', '.join(manquantes)}")
    return out


def _cellules(ligne: str) -> list[str]:
    ligne = ligne.strip()
    if ligne.startswith("|"):
        ligne = ligne[1:]
    if ligne.endswith("|") and not ligne.endswith("\\|"):
        ligne = ligne[:-1]
    cellules, courante, i = [], [], 0
    while i < len(ligne):
        c = ligne[i]
        if c == "\\" and i + 1 < len(ligne) and ligne[i + 1] == "|":
            courante.append("|")
            i += 2
            continue
        if c == "|":
            cellules.append("".join(courante).strip())
            courante = []
        else:
            courante.append(c)
        i += 1
    cellules.append("".join(courante).strip())
    return cellules


def _tableau(corps: str) -> list[dict] | None:
    """Le premier tableau markdown de la section, hors blocs de code."""
    lignes = [l for _, l in lines_outside_fences(corps)]
    for i in range(len(lignes) - 1):
        if "|" in lignes[i] and _SEPARATEUR.match(lignes[i + 1]):
            entetes = _cellules(lignes[i])
            rangs = []
            for l in lignes[i + 2:]:
                if not l.strip() or "|" not in l:
                    break
                cellules = _cellules(l)
                cellules += [""] * (len(entetes) - len(cellules))
                rangs.append({h: v for h, v in zip(entetes, cellules) if h})
            return rangs
    return None


def _yaml(corps: str, avertissements: list[str], titre: str) -> list[dict] | None:
    """Le premier bloc de code `yaml` (ou `yml`) de la section."""
    contenu, dedans, cloture = [], False, None
    for ligne in corps.split("\n"):
        m = _CLOTURE.match(ligne)
        if not dedans:
            if m and m.group(3).lower() in ("yaml", "yml"):
                dedans, cloture = True, m.group(2)
            continue
        if m and ligne.strip().startswith(cloture[0] * len(cloture)) and not m.group(3):
            break
        contenu.append(ligne)
    else:
        if not dedans:
            return None
    try:
        donnees = yaml.safe_load("\n".join(contenu))
    except yaml.YAMLError:
        avertissements.append(f"« {titre} » : bloc yaml illisible")
        return None
    if not isinstance(donnees, list) or not all(isinstance(x, dict) for x in donnees):
        avertissements.append(f"« {titre} » : le bloc yaml doit être une liste d'objets")
        return None
    return [{str(k): _json(v) for k, v in x.items()} for x in donnees]


def _json(valeur):
    """Une valeur YAML rendue telle quelle, sauf ce que JSON ne porte pas."""
    if isinstance(valeur, (dt.date, dt.datetime)):
        return valeur.isoformat()
    if isinstance(valeur, dict):
        return {str(k): _json(v) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_json(v) for v in valeur]
    return valeur
