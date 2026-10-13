# Banc d'essai — navigation et assemblage (2026-09-09)

> Étape 1 du diagnostic architectural externe (propriétaire du projet, message
> du 2026-09-09) : mesurer avant d'investir. Ce document est le relevé de
> cette mesure. Il est daté à dessein — un futur re-run après correctifs se
> compare à celui-ci, il ne l'écrase pas.
>
> **Version anonymisée.** Le banc a tourné sur les bases privées d'un
> opérateur ; ce dépôt étant public, elles sont désignées ici par leur rôle —
> `produit-x` (documentation d'un produit éditeur), `installation-x` (ce qu'une
> organisation a fait de ce produit), `referentiel-x` (inventaire généré de ses
> objets) — et les tâches par leur nature. Le relevé complet, objets et chemins
> compris, est conservé dans le dépôt privé de `referentiel-x`.

## Méthodologie

20 tâches représentatives, 5 catégories (retrouver un objet, comprendre un
objet/workflow, préparer une modification, comparer des environnements,
identifier un impact), formulées dans le langage d'un développeur de
l'organisation, sans chemin ni identifiant technique pré-donné — sauf quand un
développeur réel en connaîtrait déjà un (B1).

Chaque tâche a été traitée par une **session fraîche et indépendante** (4
agents Claude, 5 tâches chacun), n'ayant accès **qu'à un harnais**
(`kb_cli.py`, script jetable) appelant directement les fonctions réelles de
`tools/list_tool.py`, `search_tool.py`, `read_tool.py`, `governance_tool.py`
contre les bases réellement clonées dans `bases/` — même format de sortie
qu'un appel MCP (`ERROR: <CODE>: <message>` inclus), mais sans passer par le
protocole. Interdiction stricte d'inspecter `bases/` autrement (Read, Grep,
`cat`) : une session MCP réelle ne le peut pas, la mesure aurait été faussée.

**Limites assumées.** (1) L'exécutant est un modèle Claude — représentatif du
consommateur réel de ce hub, mais pas une garantie de couvrir toute variation
de comportement. (2) Le verdict de chaque tâche est l'auto-évaluation de
l'agent qui l'a traitée, pas une vérité terrain indépendante préexistante.
(3) `kb_propose`, `kb_proposal_status`, `kb_hub_rescan` hors périmètre —
lecture/navigation seulement.

## Résultat global

**14 réussi, 5 partiel, 1 échoué**, sur 20. **215 appels `kb_*`** au total
(moyenne 10,75/tâche, de 3 à 20). **0/20 affirmation non sourcée** dans une
réponse finale — le hub ne fait pas dire au modèle des choses qu'il n'a pas
lues.

## Tableau consolidé

| ID | Tâche (nature) | Verdict | Appels | Troncature→suivie | Ambiguïté env.→traitement | Cause si non-réussi | Bases lues |
|---|---|---|---|---|---|---|---|
| A1 | retrouver un écran de consultation | réussi | 9 | oui | silencieuse | — | referentiel-x, installation-x |
| A2 | retrouver une table de base de données | réussi | 10 | oui | silencieuse | — | referentiel-x |
| A3 | retrouver un workflow de validation | réussi | 9 | oui | signalée | — | referentiel-x, installation-x |
| A4 | retrouver le support technique d'un processus | réussi | 10 | oui | signalée | — | referentiel-x, installation-x |
| B1 | ce que fait un écran nommé | réussi | 7 | N-A | N-A | — | referentiel-x, installation-x |
| B2 | structure d'un workflow | **partiel** | 9 | oui→oui | signalée | **introuvable** (détail des acteurs) | installation-x, referentiel-x |
| B3 | dépendances d'une interface | réussi | 5 | oui→oui | signalée | — | referentiel-x |
| B4 | agrégat vs instance (provenance) | réussi | 11 | oui→oui | N/A | — | referentiel-x |
| C1 | ajouter un filtre à un écran | réussi | 12 | oui→oui | signalée (module dev seul, absent de prod) | — | installation-x, referentiel-x |
| C2 | règle de modification d'un objet généré | réussi | 3 | non | N/A | — | referentiel-x (`kb_governance`) |
| C3 | lire avant d'ajouter un statut à un workflow | réussi | 19 | oui→oui | N-A | — | produit-x, installation-x, referentiel-x |
| C4 | appliquer ici une procédure du produit | **partiel** | 20 | oui→oui | N-A | **non-assemblée** | produit-x, installation-x, referentiel-x |
| D1 | différence de configuration entre deux environnements | **échoué** | 16 | non | signalée | **absente** | installation-x, referentiel-x |
| D2 | un objet est-il documenté par environnement ? | réussi | 16 | oui→partiellement | signalée | — | referentiel-x, installation-x |
| D3 | version du produit par environnement | **partiel** | 9 | non | signalée | **absente** | produit-x, installation-x |
| D4 | configuration d'une base externe entre deux environnements | **partiel** | 18 | oui | signalée | **absente** | installation-x, referentiel-x |
| E1 | impact d'une modification sur un écran nommé | réussi | 9 | oui | N/A | — | referentiel-x, installation-x |
| E2 | workflows consommant une table | **partiel** | 11 | non | N/A | **non-assemblée** | referentiel-x |
| E3 | retirer un support technique sans casser | réussi | 6 | non | N/A | — | referentiel-x |
| E4 | existe-t-il un outil de dépendances ? | réussi | 6 | oui | N/A | — | referentiel-x |

## Cause, quand le verdict n'est pas « réussi » (typologie à 4 voies du diagnostic initial)

| Cause | Occurrences | Tâches |
|---|---|---|
| Information **absente** du corpus | 3 | D1, D3, D4 |
| Information **introuvable** (recherche insuffisante) | 1 | B2 |
| Information **inaccessible en pratique** (troncature/plafond non résolu) | 0 | — |
| Information **non assemblée** (sources séparées, jamais reliées) | 2 | C4, E2 |

Deux lectures immédiates, confirmées sur l'échantillon complet (elles ne
l'étaient qu'à moitié après les groupes 2 et 4 seuls) :

- **La troncature n'est la cause d'aucun échec, sur 20/20.** 14 tâches en ont
  rencontré une, toutes suivies avec succès sauf une (D2 : un `index.md` de
  dossier de 78 Ko abandonné faute de lecture par section utile — la tâche a
  quand même réussi via d'autres sources). Le mécanisme table-des-headings +
  `section:` fonctionne. Ce n'est donc **pas** la priorité.
- **« Absente » et « non-assemblée » dominent à parts presque égales** (3 et
  2 occurrences sur 6 échecs/partiels), avec un seul cas d'« introuvable ».
  Deux problèmes distincts, deux remèdes distincts — confirmé, pas supposé.

## Constat additionnel : des « réussi » qui ne le sont que par vigilance manuelle

**A1 et B1**, traités indépendamment par le même agent (groupe 1), partagent
exactement le même symptôme : la fiche générée de l'objet affiche
`Références entrantes : Aucune`. C'est **faux** dans les deux cas — un usage
réel existe (une inclusion d'écran pour A1, une étape de workflow pour B1) —,
retrouvé uniquement en recoupant à la main deux identifiants numériques
présents dans deux documents séparés, sans qu'aucun champ ne les relie.
L'agent l'a fait par prudence méthodologique ; rien dans la sortie de l'outil
ne l'y invite. Une session moins méticuleuse rapporte « aucune référence
entrante » comme un fait — silencieusement faux, et **non détectable de
l'extérieur** puisque la réponse reste par ailleurs entièrement sourcée.

C'est le constat le plus sérieux de ce banc d'essai : le taux de réussite
brut (14/20) **surestime** la fiabilité réelle, parce que deux réussites ne
tiennent qu'à un réflexe de vérification que l'outil ne demande pas. Il porte
sur le générateur d'une base, pas sur le hub : il se traite dans cette base.

## Quatre constats transversaux

**1. L'absence de données par environnement est systémique, pas ponctuelle.**
La base d'inventaire est mono-environnement, et la base d'installation ne
documente qu'un environnement sur les cinq que son schéma autorise. Une vue de
couverture, pour être utile, doit donc être **générique par base et par type
d'objet** — pas une réponse ad hoc à un cas.

**2. Le graphe de citation généré rate au moins trois mécanismes de
composition, pas un seul.** Confirmé indépendamment sur trois formes de lien :
SQL → objet consommateur (E2), étape de workflow → objet cible (A3, contourné
en lisant le contenu plutôt que le graphe), et inclusion d'écran (A1, B1 —
reproduit deux fois sur deux objets choisis indépendamment). Ce n'est pas un
correctif ponctuel, c'est une catégorie entière de composition absente du
graphe. Comme le constat précédent, il relève du générateur de la base.

**3. La recherche par mots-clés est peu discriminante sur les fiches courtes
noyées dans du bruit lexical.** `A2` : deux requêtes en langage naturel
échouent, noyées sous des dizaines de fiches qui mentionnent le mot en
passant ; seule une regex sur un nom de fichier *inféré* a retrouvé la fiche.
Piste concrète de pondération lexicale (identité/titre vs corps), pas
nécessairement sémantique.

**4. Une anomalie de `kb_search` à vérifier, pas encore à ériger en
conclusion.** Le groupe 1 rapporte que relancer une recherche avec
`max_results` plus élevé n'a jamais fait remonter des résultats masqués par
le plafond de sortie (~4000 tokens) — seule une requête reformulée a marché.
Observé sur 1-2 cas par un agent, pas vérifié par un test contrôlé : à
confirmer avant d'agir dessus. (Confirmé depuis et traité :
`ARCHITECTURE.md`, § 6 sexies.)

## Ce que ça change, par rapport à la lecture après les seuls groupes 2 et 4

L'ordre de priorité proposé (D4/E2 → tests de non-régression → générateur →
couverture/parcours → recherche hybride en dernier) **tient**, avec quatre
ajustements que l'échantillon complet impose :

1. La vue de couverture environnement (constat 1) doit être conçue pour
   n'importe quelle base/type.
2. Le chantier « relations du générateur » (constat 2) inclut explicitement
   les inclusions d'écran, en plus des étapes de workflow et des accès SQL.
3. Un chantier recherche lexicale (constat 3) — pondération identité/titre —
   est maintenant justifié par un cas concret, avant tout sémantique.
4. Le constat le plus important à agir en premier n'est peut-être ni D4 ni
   E2 mais le « faux Aucune » d'A1/B1 : tant que `Références entrantes` ne
   distingue pas « zéro trouvé dans le périmètre indexé » de « zéro
   probable », chaque fiche générée peut mentir par omission sans que rien ne
   le signale.

## Sources

Rapports bruts des 4 agents (transcript complet non conservé, ce document en
est la synthèse fidèle) : groupes 1 (A1-A4, B1), 2 (B2-C2), 3 (C3-D3), 4
(D4-E4), session du 2026-09-09. Harnais : `kb_cli.py` (jetable, non versionné
— appelle `okf_hub.tools.{list,search,read,governance}_tool.run` contre
`HubConfig.load(HUB_ROOT)` + `Registry.scan()` réels).
