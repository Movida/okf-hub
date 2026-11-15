"""kb_find (§ 5.8, rév. 4.5).

Liste les documents qui répondent à des critères structurés — type, tags,
champs, sections, sous-chemin, liens entrants, ancienneté — ou compte leurs
valeurs. Origine : le retour d'usage du 26/10/2026, où une session a dû
enchaîner sept recherches plein texte pour établir qu'une procédure n'existait
pas. Ici, **un total de zéro est une réponse** : le total par base est toujours
exact, même quand la liste est tronquée par le plafond du § 1.5.

Lecture pure, sur le moteur de lecture (`lecture/`), le même que l'interface
web : ce que voit l'humain est ce que lit l'agent.
"""

from __future__ import annotations

import datetime as dt
import json

from ..errors import INVALID_INPUT, NOT_FOUND, ToolError
from ..lecture.modele import Lecteur, cible_document, resoudre
from ..lecture.liens import INTERBASE, classer
from ..lecture.donnees import extraire
from ..lecture.profil import ROLES_SECTION_ORDRE
from ..mdutil import normalize_heading
from ..lecture.requete import (
    CLES_CRITERES,
    SANS_TYPE,
    Criteres,
    compter,
    filtrer,
    lire_criteres,
)
from ..registry import Registry
from ..textutil import BudgetedWriter, truncate_chars
from .common import Sortie, allocate_quota, optional_int, resolve_base_names

DEFAULT_MAX_RESULTS = 50
HARD_MAX_RESULTS = 200
TITLE_DISPLAY_MAX = 120
#: Types présents rappelés quand un `type` ne trouve rien : une faute de frappe
#: (`Procedure` pour `Procédure`) ne doit pas passer pour une absence.
TYPES_RAPPELES_MAX = 15

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {
            "description": (
                "Nom de la base (champ `name` du manifeste), une liste de noms, "
                'ou "*" pour toutes les bases enregistrées.'
            ),
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}, "minItems": 1},
            ],
        },
        "query": {
            "type": "string",
            "description": (
                "Termes que le texte du document contient tous (casse ignorée), comme "
                "kb_search en mode keyword — mais en filtre : sans repli en OU, et "
                "cumulable avec les autres critères."
            ),
        },
        "type": {"type": "string", "description": "Valeur exacte du champ `type`."},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Tags exigés, tous à la fois, sous leur forme exacte (ex. `env:prod`).",
        },
        "fields": {
            "type": "object",
            "additionalProperties": {"anyOf": [{"type": "string"}, {"type": "boolean"}]},
            "description": (
                "{champ: valeur} : valeur exacte, ou appartenance si le champ est une "
                "liste ; true exige le champ, false son absence."
            ),
        },
        "section": {
            "type": "string",
            "description": (
                "Le document a une section de ce titre (casse et formatage ignorés), "
                "ou d'un titre que le profil de la base range sous ce rôle "
                f"({', '.join(ROLES_SECTION_ORDRE)})."
            ),
        },
        "without_section": {
            "type": "string",
            "description": "Le document n'a pas cette section — même correspondance que `section`.",
        },
        "under": {
            "type": "string",
            "description": "Sous-chemin du corpus, lu comme un répertoire (ex. `exploitation/incidents`).",
        },
        "links_to": {
            "type": "string",
            "description": (
                "Documents qui citent celui-ci : `<base>:/<chemin>`, ou un chemin "
                "si une seule base est interrogée. Un lien venu d'une section de "
                "rôle upstream est marqué « amont »."
            ),
        },
        "links_to_depth": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
            "default": 1,
            "description": (
                "Avec links_to : 2 ajoute les documents qui citent ceux qui la citent, "
                "3 un degré de plus — « si cette fiche est fausse, quoi d'autre l'est ». "
                "Sommaires et journaux ne propagent pas."
            ),
        },
        "upstream_changed": {
            "type": "boolean",
            "description": (
                "Documents qui citent un document d'une autre base modifié (commit) "
                "après leur propre dernier commit ou dernière vérification : à relire."
            ),
        },
        "older_than_months": {
            "type": "integer",
            "minimum": 1,
            "maximum": 600,
            "description": (
                "Documents dont la fraîcheur est plus ancienne, les plus anciens "
                "d'abord, avec la date et son origine : vérification, production, "
                "source, ou à défaut dernier commit."
            ),
        },
        "sort": {
            "type": "string",
            "enum": ["path", "relevance", "oldest", "most_cited", "reaudit"],
            "description": (
                "Ordre de la liste : chemin (défaut), pertinence (avec query, défaut "
                "alors), plus anciens d'abord (défaut sous older_than_months), plus "
                "cités, ou priorité de ré-audit (âge × citations, doublé si l'amont a "
                "changé)."
            ),
        },
        "count_by": {
            "type": "string",
            "description": (
                "Effectifs par valeur au lieu de la liste : `type`, `tags`, `section`, "
                "une facette déclarée par le profil, ou un nom de champ."
            ),
        },
        "select": {
            "type": "object",
            "properties": {
                "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "data": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "additionalProperties": False,
            "description": (
                "Projection : pour chaque document retenu, rend en sortie structurée les "
                "champs du frontmatter nommés par `fields` et les lignes des blocs de "
                "données nommés par `data` (titres de section que le profil de la base "
                "déclare sous `data`). Un nom que le profil ne déclare pas est refusé. "
                "Le texte ne donne que le nombre de lignes."
            ),
        },
        "max_results": {
            "type": "integer",
            "default": DEFAULT_MAX_RESULTS,
            "minimum": 1,
            "maximum": HARD_MAX_RESULTS,
            "description": "Documents listés au plus, toutes bases confondues ; le total reste exact.",
        },
    },
    "required": ["base"],
    "additionalProperties": False,
}


def description(registry: Registry) -> str:
    return (
        "Trouve les documents d'une ou plusieurs bases par critères structurés — "
        "texte (tous les termes), type, tags, champs du frontmatter, présence ou "
        "absence d'une section, sous-chemin, documents qui en citent un autre "
        "(jusqu'au 3e degré), amont modifié depuis, ancienneté — triés par chemin, "
        "pertinence, ancienneté, citations ou priorité de ré-audit, ou compte "
        "leurs valeurs (`count_by`). Rend, par base, le TOTAL EXACT puis les "
        "documents (chemin, titre, type) : un total de zéro est une réponse, qui "
        "établit une absence en un appel. Les critères se cumulent (ET). "
        "Sommaires et journaux (index.md, log.md) ne sont pas des concepts : ni "
        "listés, ni comptés. Pour lire un document trouvé : kb_read ; pour une "
        "recherche dans le texte : kb_search. "
        "Bases disponibles : " + (", ".join(registry.names()) or "aucune") + "."
    )


def _cible_des_liens(lecteur: Lecteur, reference: str, bases: list[str]) -> tuple[str, str]:
    """(base, chemin du document) désigné par `links_to`."""
    defaut = bases[0] if len(bases) == 1 else None
    cible = classer(reference, defaut or "", "")
    if cible.sorte != INTERBASE and defaut is None:
        raise ToolError(
            INVALID_INPUT,
            "paramètre 'links_to' : avec plusieurs bases, écrire `<base>:/<chemin>`",
        )
    if cible.sorte not in ("interne", INTERBASE):
        raise ToolError(INVALID_INPUT, f"paramètre 'links_to' : référence illisible « {reference} »")
    # UNKNOWN_BASE si le préfixe ne nomme aucune base : le serveur re-scanne et
    # retente, comme pour tout outil (§ 4.4.c).
    modeles = lecteur.modeles([cible.base])
    trouve = cible_document(resoudre(cible, modeles), modeles)
    if trouve is None:
        raise ToolError(NOT_FOUND, f"links_to : aucun document « {reference} » dans la base '{cible.base}'")
    return trouve


#: Sortie structurée (`structuredContent` MCP) : les mêmes documents et effectifs
#: que le texte, sous le même plafond, pour qu'une autre interface les lise sans
#: analyser de texte (§ 5 bis.3). Le texte reste la sortie destinée au modèle.
_DOCUMENT = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "title": {"type": "string"},
        "type": {"type": ["string", "null"]},
        "freshness": {
            "type": ["object", "null"],
            "properties": {"date": {"type": "string"}, "origin": {"type": "string"}},
        },
        "upstream": {"type": "boolean"},
        "upstream_changed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "base": {"type": "string"}, "path": {"type": "string"},
                    "changed": {"type": "string"}, "declines": {"type": "boolean"},
                },
                "required": ["base", "path", "changed"],
            },
        },
        "cited_by": {"type": "integer"},
        "reaudit_score": {"type": "number"},
        "depth": {"type": "integer"},
        "fields": {"type": "object", "description": "Projection (`select.fields`) : champ → valeur, null si absent."},
        "data": {
            "type": "object",
            "description": "Projection (`select.data`) : titre du bloc → lignes (objets colonne → valeur).",
            "additionalProperties": {"type": "array", "items": {"type": "object"}},
        },
        "data_truncated": {"type": "boolean", "description": "Lignes omises : plafond de sortie atteint."},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["path", "title", "type"],
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {"type": "string"},
        "count_by": {"type": ["string", "null"]},
        "bases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "base": {"type": "string"},
                    "total": {"type": "integer", "description": "Exact, même si la liste est tronquée."},
                    "documents": {"type": "array", "items": _DOCUMENT},
                    "counts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "string"},
                                "count": {"type": "integer"},
                                "declared": {"type": ["boolean", "null"]},
                            },
                            "required": ["value", "count"],
                        },
                    },
                },
                "required": ["base", "total"],
            },
        },
    },
    "required": ["criteria", "bases"],
}


def run(registry: Registry, arguments: dict) -> str:
    return run_structured(registry, arguments).texte


def run_structured(registry: Registry, arguments: dict) -> Sortie:
    noms = resolve_base_names(registry, arguments)
    criteres = lire_criteres({k: arguments.get(k) for k in CLES_CRITERES})
    count_by = arguments.get("count_by")
    if count_by is not None and (not isinstance(count_by, str) or not count_by.strip()):
        raise ToolError(INVALID_INPUT, "paramètre 'count_by' : chaîne non vide attendue")
    max_results = optional_int(arguments, "max_results", DEFAULT_MAX_RESULTS, 1, HARD_MAX_RESULTS)
    select = _lire_select(arguments.get("select"))
    if select is not None and count_by:
        raise ToolError(INVALID_INPUT, "paramètres 'select' et 'count_by' : l'un ou l'autre")
    donnees = {"criteria": criteres.decrire(), "count_by": count_by.strip() if count_by else None, "bases": []}
    if not noms:
        return Sortie("Aucune base n'est actuellement enregistrée.", donnees)

    lecteur = Lecteur.de(registry)
    cible = _cible_des_liens(lecteur, criteres.links_to, noms) if criteres.links_to else None
    resultats = {
        nom: filtrer(lecteur, lecteur.modele(nom), criteres, cible=cible) for nom in noms
    }
    en_tete = f"critères : {criteres.decrire()}"
    if count_by:
        texte = _rendre_effectifs(lecteur, noms, resultats, count_by.strip(), en_tete, donnees)
    else:
        projection = _Projection(registry, lecteur, noms, select) if select else None
        texte = _rendre_liste(lecteur, noms, resultats, criteres, max_results, en_tete, donnees, projection)
    return Sortie(texte, donnees)


# --- projection (rév. 4.8, § 5.8) ----------------------------------------------

#: Budget de la sortie structurée pour les lignes de données, en caractères JSON :
#: le plafond transverse du § 1.5 (~4 000 tokens), compté comme le texte.
BUDGET_DONNEES = 16_000


def _lire_select(brut) -> dict | None:
    if brut is None:
        return None
    if not isinstance(brut, dict) or set(brut) - {"fields", "data"} or not brut:
        raise ToolError(INVALID_INPUT, "paramètre 'select' : {fields: [...], data: [...]} attendu")
    out = {}
    for cle in ("fields", "data"):
        valeur = brut.get(cle)
        if valeur is None:
            continue
        if not isinstance(valeur, list) or not valeur or not all(isinstance(v, str) and v.strip() for v in valeur):
            raise ToolError(INVALID_INPUT, f"paramètre 'select.{cle}' : liste non vide de noms attendue")
        out[cle] = [v.strip() for v in valeur]
    return out


class _Projection:
    """Ce que `select` demande, résolu base par base contre le profil."""

    def __init__(self, registry: Registry, lecteur: Lecteur, noms: list[str], select: dict) -> None:
        self.registry = registry
        self.par_base: dict[str, tuple[list[str], list, list[str]]] = {}
        champs_vus, blocs_vus = set(), set()
        for nom in noms:
            profil = lecteur.modele(nom).profil
            champs = [c for c in select.get("fields", []) if c in profil.champs]
            blocs = [profil.donnees[normalize_heading(d)] for d in select.get("data", [])
                     if normalize_heading(d) in profil.donnees]
            absents = [c for c in select.get("fields", []) if c not in profil.champs]
            absents += [d for d in select.get("data", []) if normalize_heading(d) not in profil.donnees]
            self.par_base[nom] = (champs, blocs, absents)
            champs_vus.update(champs)
            blocs_vus.update(normalize_heading(b.titre) for b in blocs)
        inconnus = [c for c in select.get("fields", []) if c not in champs_vus]
        inconnus += [d for d in select.get("data", []) if normalize_heading(d) not in blocs_vus]
        if inconnus:
            raise ToolError(
                INVALID_INPUT,
                f"paramètre 'select' : {', '.join(inconnus)} — déclaré par le profil d'aucune "
                f"base interrogée (champs : required/optional ; blocs : data)",
            )
        self.budget = BUDGET_DONNEES
        self.tronque = False

    def avertissement_base(self, nom: str) -> str | None:
        absents = self.par_base[nom][2]
        if not absents:
            return None
        return f"[select : non déclaré par le profil de '{nom}' : {', '.join(absents)}]"

    def projeter(self, nom: str, doc) -> tuple[dict, str]:
        """(données à ajouter au document, suffixe de sa ligne de texte)."""
        champs, blocs, _ = self.par_base[nom]
        sortie: dict = {}
        avertissements: list[str] = []
        if champs:
            sortie["fields"] = {c: _valeur_json(doc.frontmatter.get(c)) for c in champs}
        resume = []
        if blocs:
            base = self.registry.get(nom)
            try:
                texte = (base.corpus_dir / doc.chemin).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                texte = ""
                avertissements.append(f"document illisible : {exc}")
            sortie["data"] = {}
            for bloc in blocs:
                extraction = extraire(texte, bloc) if texte else None
                lignes = extraction.lignes if extraction else []
                if extraction:
                    avertissements += extraction.avertissements
                gardees = []
                for ligne in lignes:
                    cout = len(json.dumps(ligne, ensure_ascii=False))
                    if cout > self.budget:
                        self.tronque = True
                        sortie["data_truncated"] = True
                        break
                    self.budget -= cout
                    gardees.append(ligne)
                sortie["data"][bloc.titre] = gardees
                resume.append(f"{bloc.titre} : {len(lignes)} ligne(s)")
        if avertissements:
            sortie["warnings"] = avertissements
        suffixe = (" — " + ", ".join(resume)) if resume else ""
        if avertissements:
            suffixe += f" — {len(avertissements)} avertissement(s)"
        return sortie, suffixe


def _valeur_json(valeur):
    if isinstance(valeur, (dt.date, dt.datetime)):
        return valeur.isoformat()
    if isinstance(valeur, dict):
        return {str(k): _valeur_json(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_valeur_json(v) for v in valeur]
    return valeur


def _document(t) -> dict:
    sortie = {
        "path": t.doc.chemin,
        "title": t.doc.titre,
        "type": t.doc.type,
        "freshness": (
            {"date": t.fraicheur.date.isoformat(), "origin": t.fraicheur.origine}
            if t.fraicheur is not None else None
        ),
        "upstream": t.amont,
    }
    if t.derives:
        sortie["upstream_changed"] = [
            {"base": d.base, "path": d.chemin, "changed": d.date_cible.isoformat(), "declines": d.amont}
            for d in t.derives
        ]
    if t.citations is not None:
        sortie["cited_by"] = t.citations
    if t.score is not None:
        sortie["reaudit_score"] = t.score
    if t.degre is not None:
        sortie["depth"] = t.degre
    return sortie


def _ligne(t) -> str:
    ligne = f"- {t.doc.chemin} — {truncate_chars(t.doc.titre, TITLE_DISPLAY_MAX)} [{t.doc.type or SANS_TYPE}]"
    if t.fraicheur is not None:
        ligne += f" — {t.fraicheur.date.date().isoformat()} ({t.fraicheur.origine})"
    if t.degre is not None and t.degre > 1:
        ligne += f" — degré {t.degre}"
    if t.amont:
        ligne += " — amont"
    if t.citations is not None:
        ligne += f" — cité par {t.citations}"
    if t.score is not None:
        ligne += f" — priorité {t.score:g}"
    if t.derives:
        d = t.derives[0]
        autres = f" (+{len(t.derives) - 1})" if len(t.derives) > 1 else ""
        ligne += f" — amont modifié : {d.base}:/{d.chemin} le {d.date_cible.date().isoformat()}{autres}"
    return ligne


def _rendre_liste(lecteur, noms, resultats, criteres: Criteres, max_results, en_tete, donnees,
                  projection: "_Projection | None" = None) -> str:
    quotas = allocate_quota([len(resultats[n]) for n in noms], max_results)
    writer = BudgetedWriter()
    plusieurs = len(noms) > 1
    total = sum(len(r) for r in resultats.values())
    if plusieurs:
        writer.add_forced(f"{total} document(s) dans {len(noms)} base(s) — {en_tete}")
    omis = 0
    for nom, quota in zip(noms, quotas):
        trouves = resultats[nom]
        listes = []
        donnees["bases"].append({"base": nom, "total": len(trouves), "documents": listes})
        titre = f"{len(trouves)} document(s) dans '{nom}'"
        writer.add_forced(f"\n## {titre}" if plusieurs else f"{titre} — {en_tete}")
        # Seulement si le type n'existe nulle part : présent dans une autre base
        # interrogée, ce n'est pas une faute de frappe.
        if total == 0 and criteres.type is not None:
            writer.add_forced(_rappel_des_types(lecteur, nom, criteres.type))
        if projection is not None and (avert := projection.avertissement_base(nom)):
            writer.add_forced(avert)
        for t in trouves[:quota]:
            extra, suffixe = projection.projeter(nom, t.doc) if projection is not None else ({}, "")
            if writer.add(_ligne(t) + suffixe):
                listes.append({**_document(t), **extra})
            else:
                omis += 1
        omis += len(trouves) - min(quota, len(trouves))
    note = (
        f"[liste incomplète : {omis} document(s) non affiché(s) — les totaux ci-dessus "
        f"sont exacts ; affinez par `under`, `type` ou `tags`"
        + ("]" if writer.truncated else ", ou relevez `max_results`]")
    )
    if projection is not None and projection.tronque:
        writer.add_forced(
            "[données tronquées : le plafond de sortie est atteint, des lignes manquent "
            "(data_truncated) ; affinez les critères ou demandez moins de blocs]"
        )
    if writer.truncated:
        return writer.render(note)
    if omis:
        writer.add_forced(note)
    return writer.render()


def _rappel_des_types(lecteur: Lecteur, nom: str, demande: str) -> str:
    modele = lecteur.modele(nom)
    _, effectifs = compter(modele.concepts(), "type", modele.profil)
    presents = [e for e in effectifs if e.nombre]
    if demande in modele.profil.types and all(e.valeur != demande for e in presents):
        # Un type du vocabulaire déclaré (§ 3.5) n'est pas une faute de frappe :
        # zéro est alors la réponse, sans liste qui la ferait douter.
        return f"[type « {demande} » déclaré par le profil de '{nom}' : aucun document de ce type]"
    if not presents:
        return f"[aucun type « {demande} » : la base ne type aucun document]"
    liste = ", ".join(f"{e.valeur} ({e.nombre})" for e in presents[:TYPES_RAPPELES_MAX])
    if len(presents) > TYPES_RAPPELES_MAX:
        liste += f", … ({len(presents) - TYPES_RAPPELES_MAX} autres : count_by=type)"
    return f"[aucun document de type « {demande} » ; types présents : {liste}]"


def _rendre_effectifs(lecteur, noms, resultats, cle: str, en_tete: str, donnees) -> str:
    writer = BudgetedWriter()
    plusieurs = len(noms) > 1
    for nom in noms:
        modele = lecteur.modele(nom)
        docs = [t.doc for t in resultats[nom]]
        nature, effectifs = compter(docs, cle, modele.profil)
        comptes = []
        donnees["bases"].append({"base": nom, "total": len(docs), "counts": comptes})
        par = nature if nature == cle else f"{nature} « {cle} »"
        titre = f"{len(docs)} document(s) dans '{nom}', par {par}"
        writer.add_forced(f"\n## {titre}" if plusieurs else f"{titre} — {en_tete}")
        for e in effectifs:
            marque = " (hors vocabulaire déclaré)" if e.declare is False else ""
            if not writer.add(f"- {truncate_chars(e.valeur, TITLE_DISPLAY_MAX)} : {e.nombre}{marque}"):
                break
            comptes.append({"value": e.valeur, "count": e.nombre, "declared": e.declare})
    return writer.render(
        "[effectifs tronqués : les valeurs les moins fréquentes ne sont pas affichées]"
    )
