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

La fusion se fait donc dans l'objet round-trip de `ruamel.yaml`, mais ruamel
n'écrit que les champs **ciblés** : tout le reste du bloc — préambule, champs
non ciblés, commentaires et lignes vides qui les annoncent, épilogue — est
recopié du texte source, par lignes entières, aux bornes que donne le
composeur de PyYAML. Le round-trip ne reproduit pas tout ce qu'un YAML valide
admet : il renormalise l'indentation, retire les espaces intérieurs d'un
`{ by: x }` — la notation d'OKF elle-même —, réaligne `a:   1`. Réémettre le
bloc entier faisait donc refuser des frontmatters valides que le hub n'avait
aucune raison de réécrire (jusqu'au 2026-11-08, `docs/ARCHITECTURE.md` § 5.4).
Le résultat est ensuite **vérifié avant d'être rendu** ; la garantie tenue ici
est celle de la vérification, pas celle de l'assemblage :

- hors des corps des champs ciblés, le texte est identique ligne à ligne,
  contrôlé par deux découpages indépendants de la source et du résultat ;
  sinon la fusion est **refusée** — aucun fichier touché, aucune proposition
  déplacée, aucun commit (l'atomicité est assurée par `review._compose_edit`,
  qui calcule tous les contenus avant d'écrire le premier) ;
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

_FIN_DE_SCALAIRE_EN_FLOW = ",?[]{}"
"""Caractères où PyYAML coupe un scalaire nu dans une collection flow.

C'est la liste de son `Scanner.scan_plain`. YAML 1.2 n'y range pas `?` au
milieu d'une chaîne : ruamel émet donc `members?role=admin` nu, et PyYAML ne
le relit plus.
"""

_INDENTATIONS = (
    # (mapping, sequence, offset) — on préfère la première configuration qui
    # reproduit le texte d'origine au caractère près (vérifié, pas supposé) :
    # les champs ciblés sont alors émis dans l'indentation du document.
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
    reference = _normaliser(origine)
    emetteur, noeud = _charger_round_trip(origine)

    cibles = set(updates)
    for cle, valeur in updates.items():
        noeud[cle] = _preparer(valeur, noeud.get(cle) if cle in noeud else None)
    obtenu = _assembler(reference, _emettre(emetteur, noeud), cibles)

    _verifier_hors_cibles(reference, obtenu, cibles)
    _verifier_representation(reference, obtenu, cibles)
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


def _charger_round_trip(origine: str):
    """(émetteur, nœud) du round-trip de `ruamel` sur le frontmatter d'origine.

    `ruamel` ne mémorise pas l'indentation du document : elle est cherchée
    parmi quelques configurations. On préfère la première dont la réémission
    **sans aucune modification** redonne le texte d'origine au caractère près,
    pour que les champs ciblés sortent dans l'indentation du document ; à
    défaut, la première qui le charge. Qu'aucune ne le reproduise n'est plus
    un refus depuis le 2026-11-08 : seuls les corps des champs ciblés sont
    repris de l'émission, le reste est recopié de la source (`_assembler`),
    et `_verifier_hors_cibles` le contrôle.
    """
    attendu = _normaliser(origine)
    premiere: ToolError | None = None
    repli = None
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
        if _normaliser(_emettre(emetteur, noeud)) == attendu:
            return emetteur, noeud
        if repli is None:
            repli = (emetteur, noeud)
    if repli is not None:
        return repli
    if premiere is not None:
        raise premiere
    raise _refus("frontmatter illisible")


def _normaliser(bloc: str) -> str:
    return bloc.rstrip("\n") + "\n"


# --- préparation des valeurs à écrire -----------------------------------------


def _preparer(valeur, existant, flow: bool = False):
    """Valeur prête à émettre : représentation non ambiguë, style conservé.

    Deux points :

    - une chaîne que `yaml.safe_load` ne relirait pas comme elle-même est
      explicitement mise entre quotes. `ruamel` résout en YAML 1.2 et PyYAML
      en YAML 1.1 : sans cela, la chaîne `"yes"` s'émettrait `yes` (chaîne en
      1.2) et se relirait `True` (booléen en 1.1). Même chose pour `on`,
      `12:30` (sexagésimal en 1.1) ou `2026-09-16`. La politique ne dépend
      donc pas du corpus : c'est le contrat d'entrée du hub, PyYAML, qui
      arbitre. Dans une collection flow (`flow`), une chaîne qui contient un
      caractère de `_FIN_DE_SCALAIRE_EN_FLOW` l'est aussi : relue seule, elle
      passe, mais PyYAML la couperait à cet endroit ;
    - remplacer une liste écrite en style flow la réécrit en style flow. La
      garantie porte sur les champs non ciblés, mais une revue qui met `tags`
      à jour casserait les lecteurs du corpus exactement comme l'incident
      qu'elle corrige. Seul le style du nœud racine de la valeur remplacée
      est repris ; ce qu'il y a dessous est du contenu neuf ;
    - une liste **sans forme existante** (champ ajouté, ou frontmatter créé)
      dont les éléments sont des scalaires ou des mappings de scalaires
      s'écrit en style flow, sur une ligne. C'est la forme des corpus
      (`tags: [a, b]`, `sources: [{id: releve, …}]`), et la seule que lisent
      leurs parseurs mono-ligne : en style bloc, un `tags` créé par une revue
      était lu comme zéro tag, et un `sources` comme illisible. Un lecteur
      YAML lit les deux formes ; la forme flow est donc la seule qui ne
      casse aucun des deux. Une liste plus profonde garde le style bloc.
    """
    if isinstance(valeur, str):
        return _chaine(valeur, flow)
    if isinstance(valeur, bool) or valeur is None or isinstance(valeur, (int, float)):
        return valeur
    if isinstance(valeur, (list, tuple)):
        en_flow = (
            flow
            or _est_flow(existant)
            or (existant is None and all(_plat(v) for v in valeur))
        )
        suite = CommentedSeq([_preparer(v, None, en_flow) for v in valeur])
        if en_flow:
            suite.fa.set_flow_style()
        return suite
    if isinstance(valeur, dict):
        en_flow = flow or _est_flow(existant)
        table = CommentedMap()
        for cle, sous in valeur.items():
            table[cle] = _preparer(sous, None, en_flow)
        if en_flow:
            table.fa.set_flow_style()
        return table
    raise ToolError(
        INVALID_INPUT, f"valeur de frontmatter non sérialisable : {type(valeur).__name__}"
    )


def _chaine(valeur: str, flow: bool = False):
    if any(c in valeur for c in "\n\r\x85\u2028\u2029"):
        # Émise en style double-quote, donc sur une seule ligne avec des
        # échappements `\n` : un retour à la ligne réel produirait une ligne
        # de continuation indentée, et une valeur contenant `---` couperait le
        # frontmatter en deux pour `mdutil.parse_document`, qui reconnaît le
        # délimiteur sur la ligne *strippée* (§ 3.2).
        return DoubleQuotedScalarString(valeur)
    if flow and any(c in valeur for c in _FIN_DE_SCALAIRE_EN_FLOW):
        return SingleQuotedScalarString(valeur)
    try:
        relu = yaml.safe_load(valeur)
    except yaml.YAMLError:
        relu = None
    if type(relu) is str and relu == valeur:
        return valeur
    return SingleQuotedScalarString(valeur)


def _plat(valeur) -> bool:
    """Scalaire, ou mapping dont toutes les valeurs sont des scalaires."""
    if isinstance(valeur, dict):
        return all(not isinstance(v, (list, tuple, dict)) for v in valeur.values())
    return not isinstance(valeur, (list, tuple))


def _est_flow(existant) -> bool:
    try:
        return bool(existant is not None and existant.fa.flow_style())
    except AttributeError:
        return False


# --- vérification du résultat -------------------------------------------------


def _verifier_representation(reference: str, obtenu: str, cibles: set[str]) -> None:
    """Refuse si un champ non ciblé, un commentaire ou l'ordre a bougé."""
    avant_preambule, avant = _decouper(reference)
    apres_preambule, apres = _decouper(obtenu, reemis=True)

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


def _decouper(
    bloc: str, reemis: bool = False
) -> tuple[tuple[str, ...], list[tuple[str, tuple[str, ...], tuple[str, ...]]]]:
    """(préambule, [(clé, lignes qui la précèdent, lignes du champ)])."""
    lignes, fin_preambule, bornes = _bornes(bloc, reemis)
    return tuple(lignes[:fin_preambule]), [
        (cle, tuple(lignes[debut:ligne]), tuple(lignes[ligne:fin]))
        for cle, debut, ligne, fin in bornes
    ]


def _bornes(
    bloc: str, reemis: bool = False
) -> tuple[list[str], int, list[tuple[str, int, int, int]]]:
    """(lignes, fin du préambule, [(clé, début de l'annonce, ligne de la clé, fin)]).

    Les indices sont des numéros de ligne du bloc, `fin` exclue.

    Le découpage s'appuie sur les marques du composeur, pas sur une regex : un
    champ commence à la première ligne de la suite de commentaires et de lignes
    vides qui l'annonce — de sorte qu'un commentaire appartienne toujours au
    champ qu'il précède — et s'arrête à la fin de sa propre valeur, pour qu'un
    champ ajouté en queue de frontmatter ne soit pas imputé au dernier champ
    d'origine. Tout cas où l'attribution ne serait pas décidable — racine en
    style flow, deux clés sur la même ligne, clé indentée — est refusé.

    `reemis` dit d'où vient un bloc illisible : du document d'origine, ou de
    l'émission. Le confondre envoie corriger à la source un frontmatter sain.
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
        if reemis:
            raise _refus(
                f"le frontmatter réémis n'est plus lisible : {_une_ligne(exc)}"
            ) from exc
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

    bornes: list[tuple[str, int, int, int]] = []
    for i, (cle, ligne, fin_valeur) in enumerate(reperes):
        plafond = debuts[i + 1] if i + 1 < len(reperes) else len(lignes)
        fin = max(ligne + 1, min(fin_valeur + 1, plafond))
        bornes.append((cle, debuts[i], ligne, fin))
    return lignes, debuts[0], bornes


# --- assemblage : la source, sauf les corps des champs ciblés -----------------


def _assembler(source: str, emis: str, cibles: set[str]) -> str:
    """Le texte source, où seuls les corps des champs ciblés viennent de `emis`.

    Le préambule, les lignes qui annoncent chaque champ (commentaires, lignes
    vides), les champs non ciblés et l'épilogue sont ceux de la source ; un
    champ ajouté vient en fin de bloc, dans l'ordre où ruamel l'a émis. Les
    bornes sont celles du composeur, pour la source comme pour l'émission :
    aucune ligne n'est coupée, aucune valeur n'est insérée dans une chaîne.
    """
    l_src, _, b_src = _bornes(source)
    l_em, _, b_em = _bornes(emis, reemis=True)
    corps_emis = {cle: l_em[ligne:fin] for cle, _, ligne, fin in b_em}
    presents = {cle for cle, *_ in b_src}

    sortie: list[str] = []
    curseur = 0
    for cle, _, ligne, fin in b_src:
        sortie += l_src[curseur:ligne]
        if cle in cibles:
            if cle not in corps_emis:
                raise _refus(f"le champ ciblé '{cle}' manque à l'émission")
            sortie += corps_emis[cle]
        else:
            sortie += l_src[ligne:fin]
        curseur = fin
    sortie += l_src[curseur:]
    for cle, _, ligne, fin in b_em:
        if cle not in presents:
            sortie += l_em[ligne:fin]
    return "\n".join(sortie) + "\n"


def _verifier_hors_cibles(source: str, obtenu: str, cibles: set[str]) -> None:
    """Hors des corps des champs ciblés, le résultat redit la source ligne à ligne.

    Calculé par deux découpages indépendants, de la source et du résultat, et
    non repris de `_assembler` : c'est une vérification, pas une conséquence.
    Elle voit aussi ce que la comparaison par champ de
    `_verifier_representation` laisse passer — une ligne vide supprimée dans
    l'épilogue, après la valeur du dernier champ.
    """
    ajoutes = cibles - {cle for cle, *_ in _bornes(source)[2]}
    if _masquer(source, cibles) != _masquer(obtenu, cibles, ajoutes, reemis=True):
        raise _refus("le texte hors des champs ciblés a changé")


def _masquer(
    bloc: str,
    cibles: set[str],
    ajoutes: frozenset[str] | set[str] = frozenset(),
    reemis: bool = False,
) -> list:
    """Les lignes du bloc, le corps de chaque champ ciblé remplacé par un repère.

    Un champ `ajoutes` — absent de la source — ne laisse ni repère ni texte :
    seules restent les lignes qui l'annoncent, qui sont celles de la source.
    """
    lignes, _, bornes = _bornes(bloc, reemis)
    sortie: list = []
    curseur = 0
    for cle, _, ligne, fin in bornes:
        sortie += lignes[curseur:ligne]
        if cle in cibles and cle not in ajoutes:
            sortie.append(("champ ciblé", cle))
        elif cle not in cibles:
            sortie += lignes[ligne:fin]
        curseur = fin
    sortie += lignes[curseur:]
    return sortie


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
