# Banc d'essai — navigation et assemblage (2026-09-09)

> Étape 1 du diagnostic architectural externe (propriétaire du projet, message
> du 2026-09-09) : mesurer avant d'investir. Ce document est le relevé de
> cette mesure. Il est daté à dessein — un futur re-run après correctifs se
> compare à celui-ci, il ne l'écrase pas.

## Méthodologie

20 tâches représentatives, 5 catégories (retrouver un objet, comprendre un
objet/workflow, préparer une modification, comparer des environnements,
identifier un impact), formulées en langage de développeur EL2D, sans chemin
ni identifiant technique pré-donné — sauf quand un développeur réel en
connaîtrait déjà un (B1).

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

| ID | Tâche (résumé) | Verdict | Appels | Troncature→suivie | Ambiguïté env.→traitement | Cause si non-réussi | Sources retenues |
|---|---|---|---|---|---|---|---|
| A1 | écran de consultation des commandes | réussi | 9 | oui | silencieuse | — | el2d-referentiel:designer/interface/ach-bpm-ecr000-transverse-tab-commandes-v1.md ; …ach-bpm-ptl001-classic-v2-contenu.md ; el2d-blueway:solution/bpm-achat.md |
| A2 | table PostgreSQL des offres | réussi | 10 | oui | silencieuse | — | el2d-referentiel:bdd/table-el2d-offer.md |
| A3 | workflow de validation d'une demande d'achat | réussi | 9 | oui | signalée | — | el2d-referentiel:designer/workflow/ach-bpm001-commande-v1.md(+contenu) ; el2d-blueway:solution/bpm-achat.md ; environnement/bdd-ext.md |
| A4 | support Designer du processus achat | réussi | 10 | oui | signalée | — | el2d-referentiel:designer/support/sup-bdd-ext.md ; …google-wbs000-maps.md ; …ach-bpm-svc000-transverse-livraison-recherche-v1.md ; el2d-blueway:environnement/bdd-ext.md, solution/bpm-achat.md |
| B1 | ce que fait ACH_BPM_ECR000_Commande_Modele_v1 | réussi | 7 | N-A | N-A | — | el2d-referentiel:designer/interface/ach-bpm-ecr000-commande-modele-v1.md ; analyses/…-dedoublement-order-type.md ; el2d-blueway:solution/bpm-achat.md ; el2d-referentiel:…ecr020…-contenu.md |
| B2 | structure du workflow de validation achat | **partiel** | 9 | oui→oui | signalée | **introuvable** (détail des acteurs) | el2d-blueway:solution/bpm-achat.md §Périmètre+§écran modèle ; el2d-referentiel:designer/workflow/ach-bpm001-commande-v1.md(+contenu §Étapes) |
| B3 | dépendances d'une interface Designer | réussi | 5 | oui→oui | signalée | — | el2d-referentiel:…ach-bpm-ecr020-commande-saisir-v1.md §Références sortantes+entrantes |
| B4 | agrégat vs instance (provenance) | réussi | 11 | oui→oui | N/A | — | el2d-referentiel:decouverte/conventions-du-bundle.md ; decouverte/lire-une-fiche-generee.md ; analyses/…-dedoublement-order-type.md |
| C1 | ajouter un filtre à l'écran commandes | réussi | 12 | oui→oui | signalée (module dev seul, absent de prod) | — | el2d-blueway:solution/bpm-achat.md ; el2d-referentiel:…tab-commandes-v1.md(+contenu) ; el2d-blueway:environnement/bdd-ext.md |
| C2 | règle de modification d'un objet généré | réussi | 3 | non | N/A | — | el2d-referentiel GOVERNANCE.md+schema.yaml (kb_governance) ; designer/interface/ser-signer-devis-v2.md |
| C3 | lire avant d'ajouter un statut au workflow achat | réussi | 19 | oui→oui | N-A | — | phoenix-blueway:designer/bpm/composants/{activite-humaine,tags,boutons,fleches}.md, …format-export-workflow.md ; el2d-blueway:solution/bpm-achat.md ; el2d-referentiel:…ach-bpm001-commande-v1.md(+contenu) |
| C4 | procédure Phoenix « écran filtrable » appliquée ici | **partiel** | 20 | oui→oui | N-A | **non-assemblée** | phoenix-blueway:designer/ecran-portail/composants/{tableau-grid,traitement-reload,listview}.md ; el2d-blueway:solution/bpm-achat.md §Périmètre ; el2d-referentiel:…tab-commandes-v1.md(+contenu) |
| D1 | différence config dev / recette EL2D | **échoué** | 16 | non | signalée | **absente** | el2d-blueway:environnement/{index,dev}.md, index.md §Routage ; el2d-referentiel:designer/env/bpm-achat.md, designer/server/servers.md |
| D2 | objet documenté par environnement ? | réussi | 16 | oui→partiellement | signalée | — | el2d-referentiel:decouverte/lire-une-fiche-generee.md §Identité, log.md, bdd/table-el2d-orders.md, designer/env/bpm-devis.md ; el2d-blueway:decouverte/frontiere-avec-el2d-referentiel.md |
| D3 | version de Phoenix par environnement | **partiel** | 9 | non | signalée | **absente** | phoenix-blueway:supervision/api/engine-informations.md ; el2d-blueway:environnement/dev.md §Rôle et version, index.md §Routage |
| D4 | config bdd_ext en préprod vs recette | **partiel** | 18 | oui | signalée | **absente** | el2d-blueway:environnement/bdd-ext.md, decouverte/conventions-du-bundle.md, GOVERNANCE.md ; el2d-referentiel:bdd/constraint-el2d-ci-ci-pk.md |
| E1 | impact d'une modif sur ACH_BPM_ECR000_Commande_Modele_v1 | réussi | 9 | oui | N/A | — | el2d-referentiel:designer/interface/ach-bpm-ecr000-commande-modele-v1.md ; analyses/…-dedoublement-order-type.md ; el2d-blueway:solution/bpm-achat.md ; el2d-referentiel:decouverte/lire-une-fiche-generee.md |
| E2 | workflows consommant la table offer | **partiel** | 11 | non | N/A | **non-assemblée** | el2d-referentiel:bdd/table-el2d-offer.md ; designer/interface/bpm-offre-gtw-010-post-init-demande-v1-contenu.md ; designer/workflow/{project-bpm-offre-v1,bpm-offre-v1}.md ; designer/interface/bpm-devis-ser-010-init-devis-v1.md |
| E3 | retirer un support Designer sans casser | réussi | 6 | non | N/A | — | el2d-referentiel:decouverte/lire-une-fiche-generee.md ; designer/support/bw-api-google.md ; designer/interface/ser-cancel-demande-v1.md |
| E4 | existe-t-il un outil de dépendances ? | réussi | 6 | oui | N/A | — | el2d-referentiel:index.md |

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
  rencontré une, toutes suivies avec succès sauf une (D2 : un `bdd/index.md`
  de 78 Ko abandonné faute de lecture par section utile — la tâche a quand
  même réussi via d'autres sources). Le mécanisme table-des-headings +
  `section:` fonctionne. Ce n'est donc **pas** la priorité.
- **« Absente » et « non-assemblée » dominent à parts presque égales** (3 et
  2 occurrences sur 6 échecs/partiels), avec un seul cas d'« introuvable ».
  Deux problèmes distincts, deux remèdes distincts — confirmé, pas supposé.

## Constat additionnel, absent des groupes 2 et 4 : des « réussi » qui ne le sont que par vigilance manuelle

**A1 et B1**, traités indépendamment par le même agent (groupe 1), partagent
exactement le même symptôme : la fiche générée de l'objet affiche
`Références entrantes : Aucune`. C'est **faux** dans les deux cas — un usage
réel existe (une inclusion de portail `IncludeScreen`→`idEcran` pour A1, une
étape BPM pour B1) — retrouvé uniquement en recoupant à la main deux
identifiants numériques (`idEcran` / `id_referentiel`) présents dans deux
documents séparés, sans qu'aucun champ ne les relie. L'agent l'a fait par
prudence méthodologique ; rien dans la sortie de l'outil ne l'y invite. Une
session moins méticuleuse rapporte « aucune référence entrante » comme un
fait — silencieusement faux, et **non détectable de l'extérieur** puisque la
réponse reste par ailleurs entièrement sourcée.

C'est le constat le plus sérieux de ce banc d'essai : le taux de réussite
brut (14/20) **surestime** la fiabilité réelle, parce que deux réussites ne
tiennent qu'à un réflexe de vérification que l'outil ne demande pas.

## Quatre constats transversaux

**1. L'absence de données par environnement est systémique, pas ponctuelle
(confirme et généralise le D4 initial).** `el2d-referentiel` est aujourd'hui
mono-environnement : sur 945 documents, la recherche `env:recette` en regex
renvoie **zéro** résultat — tout est `env:dev`. `el2d-blueway/environnement/`
ne contient qu'un fichier peuplé (`dev.md`) sur les 5 valeurs d'environnement
que `schema.yaml` autorise ; `index.md` le confirme lui-même (1 seul document
de type `Environnement` dans tout le corpus). Le module Achats/fournisseurs
entier (tables `orders`, `orders_lines`, `supplier`…) n'a jamais été recroisé
avec un dump prod. Une vue de couverture, pour être utile, doit donc être
**générique par base et par type d'objet** — pas une réponse ad hoc à
bdd_ext.

**2. Le graphe de citation généré rate au moins trois mécanismes de
composition, pas un seul (élargit le E2 initial).** Confirmé indépendamment
sur trois formes de lien : SQL → objet consommateur (E2, déjà identifié),
étape de workflow → objet cible (A3, contourné en lisant le contenu plutôt
que le graphe), et **inclusion de portail/écran BPM** (A1, B1 — nouveau,
reproduit deux fois sur deux objets choisis indépendamment). Ce dernier point
change le périmètre de « corriger les relations du générateur » : ce n'est
pas un correctif ponctuel sur les liens SQL, c'est une catégorie entière de
composition BPM absente du graphe de citation.

**3. La recherche par mots-clés est peu discriminante sur les fiches courtes
noyées dans du bruit lexical (nouveau, rejoint le point 2 du diagnostic
initial).** `A2` : deux requêtes en langage naturel (« table offer », « table
offre offer bdd_ext ») échouent, noyées sous des dizaines d'écrans BPM_OFFRE
qui mentionnent le mot en passant ; seule une regex sur un nom de fichier
*inféré* a retrouvé la fiche DDL. Une requête en langage naturel seule n'y
serait probablement pas arrivée — piste concrète de pondération lexicale
(identité/titre vs corps), pas nécessairement sémantique.

**4. Une anomalie de `kb_search` à vérifier, pas encore à ériger en
conclusion.** Le groupe 1 rapporte que relancer une recherche avec
`max_results` plus élevé n'a jamais fait remonter des résultats masqués par
le plafond de sortie (~4000 tokens) — seule une requête reformulée a marché.
Observé sur 1-2 cas par un agent, pas vérifié par un test contrôlé : à
confirmer avant d'agir dessus.

## Ce que ça change, par rapport à la lecture après les seuls groupes 2 et 4

L'ordre de priorité proposé (D4/E2 → tests de non-régression → générateur →
couverture/parcours → recherche hybride en dernier) **tient**, avec trois
ajustements que l'échantillon complet impose :

1. La vue de couverture environnement (constat 1) doit être conçue pour
   n'importe quelle base/type, pas seulement `bdd_ext`.
2. Le chantier « relations du générateur » (constat 2) inclut désormais
   explicitement les inclusions de portail/écran BPM, en plus des étapes de
   workflow et des accès SQL — sa faisabilité (extractible du XML source, ou
   pas) est en cours de vérification.
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
