"""Fusion de champs dans un frontmatter, sans dégrader sa représentation (§ 1.7).

Le principe § 1.7 interdit de produire du YAML par templating de chaînes ; il
n'impose pas *comment* fusionner. La première implémentation relisait tout le
frontmatter avec `yaml.safe_load` et réémettait l'ensemble avec
`yaml.safe_dump` : le YAML restait bien produit par une bibliothèque, mais les
champs **non ciblés** perdaient leur représentation — une liste `tags: [a, b]`
repassait en style bloc, un `timestamp: 2026-08-27T12:00:00Z` ressortait en
`2026-08-27 12:00:00+00:00`. Le YAML reste valide ; ce sont ses lecteurs qui
cassent (les corpus réels portent des parseurs maison qui ne lisent `tags` que
sur une seule ligne), et ils cassent sans erreur.

La fusion se fait donc dans l'objet round-trip de `ruamel.yaml` — ce qui
*élargit* ce qui est réalisable sans reformatage — puis le résultat est
**vérifié avant d'être rendu** : le round-trip ne garantit pas à lui seul
l'absence de normalisation résiduelle (il renormalise l'indentation, par
exemple). La garantie tenue ici est celle de la vérification, pas celle du
nom « round-trip » :

- ce qui n'est pas ciblé est identique au caractère près, sinon la fusion est
  **refusée** — aucun fichier touché, aucune proposition déplacée, aucun
  commit (l'atomicité est assurée par `review._compose_edit`, qui calcule tous
  les contenus avant d'écrire le premier) ;
- ce qui est ciblé est relu et comparé aux valeurs demandées, avec une
  égalité sensible au type ;
- les cas que la méthode de vérification ne couvre pas sont refusés
  explicitement, pas traités au mieux : ancres, alias, `<<`, clés dupliquées,
  mapping racine absent ou en style flow, frontmatter illisible.

Une seconde perte, trouvée en mesurant celle-ci sur les corpus installés : un
frontmatter qui n'est pas du YAML valide — `parse_document` le tolère et rend
`frontmatter=None` (§ 1.4) — partait d'un dictionnaire vide et **disparaissait
tout entier**, réduit au seul champ fusionné. 91 documents réels sont dans cet
état ; ils sont maintenant refusés.

Voir `docs/ARCHITECTURE.md` § 5.4 pour l'écart de règle de travail
correspondant (élargissement de l'API YAML autorisée à `ruamel.yaml`), son
motif mesuré et la manière de l'annuler.
"""

from __future__ import annotations

import io
import re

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError as RuamelError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

from .errors import INVALID_INPUT, ToolError
from .mdutil import FM_DELIM, parse_document

MAX_BYTES = 64 * 1024
"""Taille du frontmatter d'origine au-delà de laquelle on refuse de fusionner.

L'absence d'exécution de code arbitraire ne dispense pas d'une garde contre la
consommation de ressources : le round-trip conserve tout le document en
mémoire et la vérification le réémet plusieurs fois.
"""

MAX_NODES = 5_000
MAX_DEPTH = 20

_COMMENTAIRE_OU_VIDE = re.compile(r"^\s*(#.*)?$")
_COMMENTAIRE = re.compile(r"^\s*#")

_INDENTATIONS = (
    # (mapping, sequence, offset) — on retient la première configuration qui
    # reproduit le texte d'origine au caractère près (vérifié, pas supposé).
    (2, 2, 0),
    (2, 4, 2),
    (4, 4, 2),
    (4, 2, 0),
    (2, 6, 4),
    (4, 6, 4),
    (2, 4, 0),
    (2, 3, 1),
)


def _refus(raison: str) -> ToolError:
    return ToolError(
        INVALID_INPUT,
        f"fusion de frontmatter refusée ({raison}) : la préservation des champs "
        f"non ciblés ne peut pas être garantie, rien n'a été modifié. Corriger "
        f"le frontmatter à la source, ou passer par un autre chemin de "
        f"résolution — recomposer le frontmatter via edits[].content "
        f"recréerait le templating de chaînes interdit par le § 1.7.",
    )


def merge_frontmatter(text: str, updates: dict) -> str:
    """Retourne `text` avec `updates` fusionné dans son frontmatter.

    Lève `ToolError(INVALID_INPUT)` — sans effet de bord — si la fidélité du
    résultat ne peut pas être établie.
    """
    if not updates:
        return text
    for cle in updates:
        if not isinstance(cle, str) or not cle.strip():
            raise ToolError(INVALID_INPUT, f"clé de frontmatter invalide : {cle!r}")

    doc = parse_document(text)
    if doc.frontmatter_raw is None:
        return _creer(text, updates)

    lignes = doc.frontmatter_raw.split("\n")
    origine = "\n".join(lignes[1:-1])
    if not origine.strip():
        return _creer(text, updates, corps=doc.body, delimite=True)

    if len(origine.encode("utf-8")) > MAX_BYTES:
        raise _refus("frontmatter trop volumineux")

    depart = _charger_pyyaml(origine)
    emetteur, noeud, reference = _round_trip_fidele(origine)

    for cle, valeur in updates.items():
        noeud[cle] = _preparer(valeur, noeud.get(cle) if cle in noeud else None)
    obtenu = _emettre(emetteur, noeud)

    _verifier_representation(reference, obtenu, set(updates))
    return _verifier_document(
        f"{FM_DELIM}\n{obtenu}{FM_DELIM}\n{doc.body}", obtenu, doc.body, {**depart, **updates}
    )


# --- construction d'un frontmatter absent -------------------------------------


def _creer(text: str, updates: dict, corps: str | None = None, delimite: bool = False) -> str:
    """Pas de frontmatter à préserver : on en produit un, et on le vérifie."""
    noeud = CommentedMap()
    for cle, valeur in updates.items():
        noeud[cle] = _preparer(valeur, None)
    bloc = _emettre(_emetteur(*_INDENTATIONS[0]), noeud)
    if delimite:
        return _verifier_document(
            f"{FM_DELIM}\n{bloc}{FM_DELIM}\n{corps}", bloc, corps or "", dict(updates)
        )
    corps_neuf = f"\n{text}"
    return _verifier_document(
        f"{FM_DELIM}\n{bloc}{FM_DELIM}\n{corps_neuf}", bloc, corps_neuf, dict(updates)
    )


# --- chargement et contrôles d'entrée -----------------------------------------


def _charger_pyyaml(bloc: str) -> dict:
    """Contrat d'entrée actuel du hub : `safe_load`, mapping, sans ancre ni `<<`.

    Maintenu **en plus** du round-trip, pas à sa place : le mode round-trip
    conserve les tags YAML inconnus comme données au lieu de les refuser, et
    `safe_load` seul ne garantit pas que la racine est un mapping.
    """
    _inspecter(bloc)
    try:
        data = yaml.safe_load(bloc)
    except yaml.YAMLError as exc:
        raise _refus(f"frontmatter illisible : {_une_ligne(exc)}") from exc
    if not isinstance(data, dict):
        raise _refus("le frontmatter n'est pas un mapping")
    return data


def _inspecter(bloc: str) -> None:
    """Parcourt les événements YAML : ancres, alias, `<<`, taille, profondeur."""
    profondeur = 0
    noeuds = 0
    try:
        for ev in yaml.parse(bloc, Loader=yaml.SafeLoader):
            if isinstance(ev, yaml.events.AliasEvent):
                raise _refus("le frontmatter contient un alias YAML (`*ancre`)")
            if getattr(ev, "anchor", None):
                raise _refus("le frontmatter contient une ancre YAML (`&ancre`)")
            if isinstance(ev, yaml.events.ScalarEvent) and ev.value == "<<":
                raise _refus("le frontmatter contient une clé de fusion (`<<`)")
            if isinstance(ev, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                profondeur += 1
                if profondeur > MAX_DEPTH:
                    raise _refus("frontmatter trop imbriqué")
            elif isinstance(ev, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                profondeur -= 1
            noeuds += 1
            if noeuds > MAX_NODES:
                raise _refus("frontmatter trop complexe")
    except yaml.YAMLError as exc:
        raise _refus(f"frontmatter illisible : {_une_ligne(exc)}") from exc


def _emetteur(mapping: int, sequence: int, offset: int) -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 10_000
    y.allow_unicode = True
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    return y


def _emettre(emetteur: YAML, noeud) -> str:
    tampon = io.StringIO()
    try:
        emetteur.dump(noeud, tampon)
    except RuamelError as exc:
        raise _refus(f"réémission impossible : {_une_ligne(exc)}") from exc
    return tampon.getvalue()


def _round_trip_fidele(origine: str):
    """(émetteur, nœud, texte de référence) pour un round-trip vérifié fidèle.

    `ruamel` ne mémorise pas l'indentation du document : elle est cherchée
    parmi quelques configurations, et on ne retient que celle dont la
    réémission **sans aucune modification** redonne le texte d'origine au
    caractère près. Si aucune n'y parvient, une normalisation résiduelle
    subsiste et la fusion est refusée plutôt que de laisser passer un
    reformatage silencieux.
    """
    attendu = _normaliser(origine)
    premiere: ToolError | None = None
    for indents in _INDENTATIONS:
        emetteur = _emetteur(*indents)
        try:
            noeud = emetteur.load(origine)
        except DuplicateKeyError as exc:
            # `safe_load` applique « la dernière valeur gagne » sans rien dire :
            # le refus explicite vient de `ruamel`, et il est délibéré.
            raise _refus("clés dupliquées dans le frontmatter") from exc
        except RuamelError as exc:
            if premiere is None:
                premiere = _refus(f"frontmatter illisible : {_une_ligne(exc)}")
            continue
        if not isinstance(noeud, CommentedMap):
            raise _refus("le frontmatter n'est pas un mapping en style bloc")
        obtenu = _emettre(emetteur, noeud)
        if _normaliser(obtenu) == attendu:
            return emetteur, noeud, obtenu
    if premiere is not None:
        raise premiere
    raise _refus("normalisation résiduelle du frontmatter d'origine")


def _normaliser(bloc: str) -> str:
    return bloc.rstrip("\n") + "\n"


# --- préparation des valeurs à écrire -----------------------------------------


def _preparer(valeur, existant):
    """Valeur prête à émettre : représentation non ambiguë, style conservé.

    Deux points :

    - une chaîne que `yaml.safe_load` ne relirait pas comme elle-même est
      explicitement mise entre quotes. `ruamel` résout en YAML 1.2 et PyYAML
      en YAML 1.1 : sans cela, la chaîne `"yes"` s'émettrait `yes` (chaîne en
      1.2) et se relirait `True` (booléen en 1.1). Même chose pour `on`,
      `12:30` (sexagésimal en 1.1) ou `2026-09-16`. La politique ne dépend
      donc pas du corpus : c'est le contrat d'entrée du hub, PyYAML, qui
      arbitre ;
    - remplacer une liste écrite en style flow la réécrit en style flow. La
      garantie porte sur les champs non ciblés, mais une revue qui met `tags`
      à jour casserait les lecteurs du corpus exactement comme l'incident
      qu'elle corrige. Seul le style du nœud racine de la valeur remplacée
      est repris ; ce qu'il y a dessous est du contenu neuf.
    """
    if isinstance(valeur, str):
        return _chaine(valeur)
    if isinstance(valeur, bool) or valeur is None or isinstance(valeur, (int, float)):
        return valeur
    if isinstance(valeur, (list, tuple)):
        suite = CommentedSeq([_preparer(v, None) for v in valeur])
        if _est_flow(existant):
            suite.fa.set_flow_style()
        return suite
    if isinstance(valeur, dict):
        table = CommentedMap()
        for cle, sous in valeur.items():
            table[cle] = _preparer(sous, None)
        if _est_flow(existant):
            table.fa.set_flow_style()
        return table
    raise ToolError(
        INVALID_INPUT, f"valeur de frontmatter non sérialisable : {type(valeur).__name__}"
    )


def _chaine(valeur: str):
    if any(c in valeur for c in "\n\r\x85\u2028\u2029"):
        # Émise en style double-quote, donc sur une seule ligne avec des
        # échappements `\n` : un retour à la ligne réel produirait une ligne
        # de continuation indentée, et une valeur contenant `---` couperait le
        # frontmatter en deux pour `mdutil.parse_document`, qui reconnaît le
        # délimiteur sur la ligne *strippée* (§ 3.2).
        return DoubleQuotedScalarString(valeur)
    try:
        relu = yaml.safe_load(valeur)
    except yaml.YAMLError:
        relu = None
    if type(relu) is str and relu == valeur:
        return valeur
    return SingleQuotedScalarString(valeur)


def _est_flow(existant) -> bool:
    try:
        return bool(existant is not None and existant.fa.flow_style())
    except AttributeError:
        return False


# --- vérification du résultat -------------------------------------------------


def _verifier_representation(reference: str, obtenu: str, cibles: set[str]) -> None:
    """Refuse si un champ non ciblé, un commentaire ou l'ordre a bougé."""
    avant_preambule, avant = _decouper(reference)
    apres_preambule, apres = _decouper(obtenu)

    if avant_preambule != apres_preambule:
        raise _refus("l'en-tête du frontmatter a changé")

    apres_index = {cle: (prefixe, corps) for cle, prefixe, corps in apres}
    if len(apres_index) != len(apres):
        raise _refus("clés dupliquées dans le frontmatter réémis")

    for cle, prefixe, corps in avant:
        if cle not in apres_index:
            raise _refus(f"le champ '{cle}' a disparu")
        nouveau_prefixe, nouveau_corps = apres_index[cle]
        if prefixe != nouveau_prefixe:
            raise _refus(f"les lignes précédant le champ '{cle}' ont changé")
        if cle not in cibles and corps != nouveau_corps:
            raise _refus(f"la représentation du champ non ciblé '{cle}' a changé")

    conserves = [cle for cle, _, _ in avant]
    if [cle for cle, _, _ in apres][: len(conserves)] != conserves:
        raise _refus("l'ordre des champs a changé")

    if _commentaires(reference) != _commentaires(obtenu):
        raise _refus("les commentaires du frontmatter ont changé")


def _decouper(bloc: str) -> tuple[str, list[tuple[str, str, str]]]:
    """(préambule, [(clé, lignes qui la précèdent, lignes du champ)]).

    Le découpage s'appuie sur les marques du composeur, pas sur une regex : un
    champ commence à la première ligne de la suite de commentaires et de lignes
    vides qui l'annonce — de sorte qu'un commentaire appartienne toujours au
    champ qu'il précède — et s'arrête à la fin de sa propre valeur, pour qu'un
    champ ajouté en queue de frontmatter ne soit pas imputé au dernier champ
    d'origine. Tout cas où l'attribution ne serait pas décidable — racine en
    style flow, deux clés sur la même ligne, clé indentée — est refusé.
    """
    lignes = bloc.split("\n")
    if lignes and lignes[-1] == "":
        # Le bloc finit par un saut de ligne : son dernier élément est un
        # artefact du découpage, et il ferait diverger le dernier champ du
        # frontmatter d'origine de celui du frontmatter réémis, qui a une clé
        # de plus derrière lui. Les indices des lignes ne bougent pas.
        lignes.pop()
    try:
        racine = yaml.compose(bloc)
    except yaml.YAMLError as exc:
        raise _refus(f"frontmatter illisible : {_une_ligne(exc)}") from exc
    if not isinstance(racine, yaml.MappingNode):
        raise _refus("le frontmatter n'est pas un mapping")

    reperes: list[tuple[str, int, int]] = []
    for cle_noeud, valeur_noeud in racine.value:
        if not isinstance(cle_noeud, yaml.ScalarNode) or cle_noeud.start_mark.column != 0:
            raise _refus("découpage du frontmatter ambigu (clé non ancrée en colonne 0)")
        ligne = cle_noeud.start_mark.line
        if reperes and ligne <= reperes[-1][1]:
            raise _refus("découpage du frontmatter ambigu (deux clés sur une même ligne)")
        reperes.append((str(cle_noeud.value), ligne, valeur_noeud.end_mark.line))

    if not reperes:
        raise _refus("frontmatter sans champ")

    debuts: list[int] = []
    for i, (_, ligne, _) in enumerate(reperes):
        limite = 0 if i == 0 else reperes[i - 1][1] + 1
        debut = ligne
        while debut > limite and _COMMENTAIRE_OU_VIDE.match(lignes[debut - 1]):
            debut -= 1
        debuts.append(debut)

    preambule = "\n".join(lignes[: debuts[0]])
    champs: list[tuple[str, str, str]] = []
    for i, (cle, ligne, fin_valeur) in enumerate(reperes):
        plafond = debuts[i + 1] if i + 1 < len(reperes) else len(lignes)
        fin = max(ligne + 1, min(fin_valeur + 1, plafond))
        champs.append(
            (cle, "\n".join(lignes[debuts[i] : ligne]), "\n".join(lignes[ligne:fin]))
        )
    return preambule, champs


def _commentaires(bloc: str) -> list[str]:
    return [ligne.strip() for ligne in bloc.split("\n") if _COMMENTAIRE.match(ligne)]


def _verifier_document(resultat: str, bloc: str, corps: str, attendu: dict) -> str:
    """Relit le document entier avec le lecteur du hub, puis le rend.

    Dernier filet, et le seul qui voie le document tel que le corpus le lira :
    `mdutil.parse_document` reconnaît le délimiteur `---` sur la ligne
    *strippée* (§ 3.2), donc une valeur émise sur plusieurs lignes pourrait
    clore le frontmatter par accident. Le corps doit en ressortir intact au
    caractère près.
    """
    doc = parse_document(resultat)
    if doc.frontmatter_raw is None:
        raise _refus("le frontmatter réémis n'est plus délimité")
    relu = "\n".join(doc.frontmatter_raw.split("\n")[1:-1])
    if _normaliser(relu) != _normaliser(bloc):
        raise _refus("le frontmatter réémis n'est pas relu comme un seul bloc")
    if doc.body != corps:
        raise _refus("le corps du document a changé")
    _verifier_semantique(bloc, attendu)
    return resultat


def _verifier_semantique(bloc: str, attendu: dict) -> None:
    """Relit le résultat avec le parseur du hub et le compare valeur par valeur."""
    try:
        relu = yaml.safe_load(bloc)
    except yaml.YAMLError as exc:
        raise _refus(f"le frontmatter réémis n'est plus lisible : {_une_ligne(exc)}") from exc
    if not isinstance(relu, dict):
        raise _refus("le frontmatter réémis n'est plus un mapping")
    if set(relu) != set(attendu):
        raise _refus("les champs du frontmatter réémis ne sont pas ceux attendus")
    for cle, valeur in attendu.items():
        if not _identiques(relu[cle], valeur):
            raise _refus(f"la valeur relue du champ '{cle}' diffère de la valeur demandée")


def _identiques(a, b) -> bool:
    """Égalité sensible au type : `True` et `1` ne sont pas la même valeur."""
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_identiques(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_identiques(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    return a == b


def _une_ligne(exc: Exception) -> str:
    return " ".join(str(exc).split())
