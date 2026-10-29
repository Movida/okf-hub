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

from ..errors import INVALID_INPUT, NOT_FOUND, ToolError
from ..lecture.modele import Lecteur, cible_document, resoudre
from ..lecture.liens import INTERBASE, classer
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
                "(warnings, observations, upstream, procedure)."
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
        "count_by": {
            "type": "string",
            "description": (
                "Effectifs par valeur au lieu de la liste : `type`, `tags`, `section`, "
                "une facette déclarée par le profil, ou un nom de champ."
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
        "type, tags, champs du frontmatter, présence ou absence d'une section, "
        "sous-chemin, documents qui en citent un autre, ancienneté — ou compte "
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
    donnees = {"criteria": criteres.decrire(), "count_by": count_by.strip() if count_by else None, "bases": []}
    if not noms:
        return Sortie("Aucune base n'est actuellement enregistrée.", donnees)

    lecteur = Lecteur.de(registry)
    entrants = None
    if criteres.links_to:
        cible = _cible_des_liens(lecteur, criteres.links_to, noms)
        entrants = lecteur.entrants(*cible)

    resultats = {
        nom: filtrer(lecteur, lecteur.modele(nom), criteres, entrants=entrants) for nom in noms
    }
    en_tete = f"critères : {criteres.decrire()}"
    if count_by:
        texte = _rendre_effectifs(lecteur, noms, resultats, count_by.strip(), en_tete, donnees)
    else:
        texte = _rendre_liste(lecteur, noms, resultats, criteres, max_results, en_tete, donnees)
    return Sortie(texte, donnees)


def _document(t) -> dict:
    return {
        "path": t.doc.chemin,
        "title": t.doc.titre,
        "type": t.doc.type,
        "freshness": (
            {"date": t.fraicheur.date.isoformat(), "origin": t.fraicheur.origine}
            if t.fraicheur is not None else None
        ),
        "upstream": t.amont,
    }


def _rendre_liste(lecteur, noms, resultats, criteres: Criteres, max_results, en_tete, donnees) -> str:
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
        for t in trouves[:quota]:
            ligne = f"- {t.doc.chemin} — {truncate_chars(t.doc.titre, TITLE_DISPLAY_MAX)} [{t.doc.type or SANS_TYPE}]"
            if t.fraicheur is not None:
                ligne += f" — {t.fraicheur.date.date().isoformat()} ({t.fraicheur.origine})"
            if t.amont:
                ligne += " — amont"
            if writer.add(ligne):
                listes.append(_document(t))
            else:
                omis += 1
        omis += len(trouves) - min(quota, len(trouves))
    note = (
        f"[liste incomplète : {omis} document(s) non affiché(s) — les totaux ci-dessus "
        f"sont exacts ; affinez par `under`, `type` ou `tags`"
        + ("]" if writer.truncated else ", ou relevez `max_results`]")
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
