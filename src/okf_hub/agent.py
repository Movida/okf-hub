"""Revue en mode agent : les règles fixes (spec rév. 4.8, § 7.1 étape 5 bis).

En mode `review: agent`, aucun humain ne confirme les plans. La confirmation
cède la place à des règles que `okf-review resolve --agent <id>` vérifie avant
la première résolution — l'outil, pas le modèle :

- deux clés : le manifeste de la base déclare `review: agent`, et l'opérateur
  l'inscrit dans `agent-review` (`hub-config.local.yaml`) ;
- aucune édition du manifeste, de la gouvernance, du profil, des `AGENTS.md` et
  `CLAUDE.md` ;
- aucun document vidé : la suppression n'existe pas dans un plan, la vider en
  tiendrait lieu ;
- dans une base partagée (§ 4.6.a), **ajouts seulement** : aucune ligne retirée
  d'un document existant, hors des champs de fraîcheur du frontmatter ; et chaque
  proposition intégrée y laisse au moins une de ses sources.

Ce qui n'est pas dans un plan — une proposition à escalader — reste dans
`pending/` : c'est la skill qui en fait la liste pour l'humain. Un plan contraire
à une règle est refusé avec la règle en cause, et rien n'est résolu.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from .config import HubConfig
from .errors import INVALID_INPUT, ToolError
from .manifest import MANIFEST_FILENAME
from .mdutil import parse_document
from .registry import Base

#: Identifiant d'agent : sert dans le trailer `Reviewed-By: agent:<id>` (§ 7.2).
IDENTIFIANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,63}")

#: Fichiers qu'une revue en mode agent ne touche jamais, où qu'ils soient.
INTOUCHABLES = frozenset({MANIFEST_FILENAME, "GOVERNANCE.md", "schema.yaml", "AGENTS.md", "CLAUDE.md"})

#: Champs de fraîcheur (§ 7.1 étape 6) : les seuls dont une ligne peut changer
#: dans une base partagée. Familles OKF (`verified`, `stale_after`) et forme du
#: schéma d'exemple de la spec (`last-verified`).
FRAICHEUR = frozenset({"verified", "stale_after", "last-verified"})

_CLE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*:")


def reviewed_by(identifiant: str) -> str:
    if not IDENTIFIANT.fullmatch(identifiant or ""):
        raise ToolError(INVALID_INPUT, f"--agent : identifiant invalide « {identifiant} »")
    return f"agent:{identifiant}"


def exiger_deux_cles(base: Base, config: HubConfig) -> None:
    if base.manifest.review != "agent":
        raise ToolError(
            INVALID_INPUT,
            f"la base '{base.name}' ne déclare pas `governance.review: agent` : sa revue "
            f"attend un humain (§ 7.1 étape 5)",
        )
    if base.name not in config.agent_review:
        raise ToolError(
            INVALID_INPUT,
            f"'{base.name}' n'est pas dans `agent-review` (hub-config.local.yaml) : "
            f"l'opérateur n'a pas approuvé la revue sans confirmation pour cette base",
        )


def verifier(base: Base, config: HubConfig, plans, planifie: dict[Path, str], propositions) -> None:
    """Applique les règles à des plans déjà vérifiés (`review.verify_plans`).

    `planifie` : chemin → contenu final, tel que l'exécution l'écrira.
    `propositions` : id → proposition en attente.
    """
    partagee = base.name in config.shared_bases
    profil = base.manifest.frontmatter_schema
    for n, plan in enumerate(plans, 1):
        prefixe = f"plan {n} : " if len(plans) > 1 else ""
        for edit in plan.edits:
            cible = (base.corpus_dir / edit.path).resolve()
            if Path(edit.path).name in INTOUCHABLES or (profil is not None and cible == profil.resolve()):
                raise _refus(prefixe, f"{edit.path} : gouvernance, profil et manifeste sont hors d'atteinte")
    ajoute: dict[Path, str] = {}
    for cible, contenu in planifie.items():
        avant = cible.read_text(encoding="utf-8") if cible.is_file() else ""
        rel = cible.relative_to(base.corpus_dir).as_posix()
        if avant and not parse_document(contenu).body.strip() and parse_document(avant).body.strip():
            raise _refus("", f"{rel} : un document vidé vaut suppression, qu'un agent ne fait pas")
        retirees, ajoutees = _diff(avant, contenu)
        if partagee:
            hors_fraicheur = [l for l in retirees if l not in _lignes_de_fraicheur(avant)]
            if hors_fraicheur:
                raise _refus(
                    "",
                    f"{rel} : base partagée, ajouts seulement — {len(hors_fraicheur)} ligne(s) "
                    f"retirée(s) ou modifiée(s) hors des champs de fraîcheur (ligne "
                    f"{hors_fraicheur[0] + 1})",
                )
        ajoute[cible] = "\n".join(ajoutees)
    if partagee:
        for n, plan in enumerate(plans, 1):
            prefixe = f"plan {n} : " if len(plans) > 1 else ""
            for res in plan.resolutions:
                if res.resolution != "accepted":
                    continue
                sources = _sources(propositions.get(res.id))
                texte = "\n".join(
                    ajoute.get((base.corpus_dir / p).resolve(), "") for p in res.integrated_into
                )
                if sources and not any(s in texte for s in sources):
                    raise _refus(
                        prefixe,
                        f"{res.id} : base partagée, chaque ajout reprend les sources de sa "
                        f"proposition — aucune n'apparaît dans ce qui est ajouté à "
                        f"{', '.join(res.integrated_into) or '(aucun document)'}",
                    )


def _refus(prefixe: str, message: str) -> ToolError:
    return ToolError(INVALID_INPUT, f"mode agent, {prefixe}{message}")


def _diff(avant: str, apres: str) -> tuple[list[int], list[str]]:
    """(indices des lignes d'avant retirées ou remplacées, lignes ajoutées)."""
    a, b = avant.split("\n"), apres.split("\n")
    retirees, ajoutees = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            retirees += [i for i in range(i1, i2) if not (a[i] == "" and i == len(a) - 1)]
        if op in ("replace", "insert"):
            ajoutees += b[j1:j2]
    return retirees, ajoutees


def _lignes_de_fraicheur(texte: str) -> set[int]:
    """Indices des lignes du frontmatter qui appartiennent à un champ de fraîcheur."""
    doc = parse_document(texte)
    if doc.frontmatter_raw is None:
        return set()
    lignes = texte.split("\n")
    out, cle = set(), None
    for i in range(1, doc.body_offset - 1):
        m = _CLE.match(lignes[i])
        if m:
            cle = m.group(1)
        if cle in FRAICHEUR:
            out.add(i)
    return out


def _sources(proposition) -> list[str]:
    if proposition is None:
        return []
    brutes = proposition.frontmatter.get("sources") or []
    if isinstance(brutes, str):
        brutes = [brutes]
    return [str(s).strip() for s in brutes if str(s).strip()]
