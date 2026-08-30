---
type: Procédure
title: Cycle de vie d'une base — créer, déployer, alimenter, réviser, retirer
description: >
  Les cinq étapes de la vie d'une base, avec à chaque fois le rôle qui l'exécute
  et le moyen employé. Aucun outil MCP ne crée ni ne supprime une base, et c'est
  délibéré.
tags: [kb_hub_rescan, kb_propose, kb-review, okf-review]
applies-to: "rév. 4.1"
generated: { by: "claude-code/opus-5", at: 2026-08-30T00:00:00Z }
---

# Cycle de vie d'une base

Une confusion à dissiper d'emblée : **il n'existe pas d'API de gestion des
bases.** Les outils MCP lisent et proposent. Créer une base, la retirer,
intégrer une proposition — ce sont des opérations de shell et de git, faites par
un rôle qui a accès à la machine du hub.

Ce n'est pas une lacune. Toute capacité d'écriture nouvelle exposée par MCP
serait, par construction, offerte à toutes les sessions connectées — y compris
celles qui n'ont aucun mandat pour l'exercer.

| Étape | Rôle | Moyen |
|---|---|---|
| Créer | opérateur, ou session avec shell | template + questionnaire d'instanciation |
| Déployer | opérateur | `git clone` dans `bases/`, puis découverte |
| Alimenter | toute session | `kb_propose` |
| Réviser | gestionnaire | `okf-review`, en ligne de commande |
| Retirer | opérateur | suppression du répertoire, puis découverte |

## 1. Créer

Une base est un **bundle** : un dépôt git contenant un corpus markdown, un
manifeste, et des règles de gouvernance. On n'en écrit pas un de zéro : on part
du dépôt template, qui porte le manifeste pré-rempli, un `GOVERNANCE.md`
d'exemple, un schéma de frontmatter et un document modèle.

Le template contient un `INSTANTIATE.md` : une checklist **conçue pour être
déroulée par une session qui interroge l'humain**, question par question. Si tu
es cette session, suis-la sans inventer de valeur par défaut sur les points
marqués « demander ».

Deux points d'attention, parce qu'ils sont ratés une fois sur deux :

- **La description du manifeste est rédigée pour le routage.** Elle sera injectée
  dans les descriptions d'outils de toutes les sessions connectées, et c'est sur
  elle qu'un agent décidera d'interroger cette base plutôt qu'une autre.
  « Documentation de X » ne suffit pas : dis ce qu'on y trouve.
- **Les golden rules doivent être décidables.** Si en lisant une règle tu ne peux
  pas dire si une proposition donnée passe ou non, elle est trop vague et le
  gestionnaire ne pourra pas s'en servir.

Le `GOVERNANCE.md` du template est livré en `status: draft`. Tant qu'il l'est,
les outils le signalent : les règles affichées sont des exemples que personne n'a
validés. Le passage à `stable` est une étape explicite du questionnaire, à faire
une fois les règles arbitrées avec l'humain. Rien n'est bloqué en brouillon — les
propositions sont acceptées, la revue fonctionne.

Enfin : **retire l'`origin` du template** avant de commiter. Un dépôt instancié
qui pointe encore sur le template expose à un `push` qui écraserait le livrable.

## 2. Déployer

Cloner le bundle dans le répertoire des bases du hub, sous le nom que tu veux —
le nom du répertoire n'a pas d'importance, c'est le champ `name` du manifeste qui
sert partout de paramètre `base`. Les deux diffèrent dès qu'un clone est renommé.

La découverte est ensuite automatique : toute session qui liste les bases relit
le disque avant de répondre. L'outil de rescan reste utile pour une autre raison
— il rend le **rapport** d'import : bundles rejetés avec leur motif, collisions
de nom, avertissements de compatibilité. Si une base que tu viens de déployer
n'apparaît pas, c'est là que tu apprendras pourquoi.

Vérifie ensuite, depuis une session : la base apparaît dans la liste avec son
nombre de documents ; ses règles sortent ; une recherche sur un mot du premier
document le trouve ; une proposition d'essai se dépose et se relit.

## 3. Alimenter

Par des propositions, et seulement par elles. Voir `proposer.md`.

## 4. Réviser

Le gestionnaire est une session invoquée à la demande, outillée par la skill
`kb-review`. Il ne reproduit jamais le protocole de verrouillage à la main : il
passe par le moteur `okf-review`, qui prend le verrou de la base à la bonne
granularité — **une résolution complète = une acquisition** — applique les
éditions, déplace les propositions et produit un commit unique portant les
trailers d'audit.

Trois choses qu'un gestionnaire ne fait pas : écrire dans le dépôt avec ses
propres outils d'édition ; envelopper le moteur dans le wrapper de verrouillage,
qu'il utilise déjà lui-même ; commiter avant confirmation explicite de l'humain.

La première étape d'une revue est la **réconciliation**, et elle ne se saute
pas : elle rattrape les propositions écrites sur disque dont le commit n'a pas
abouti. Sans elle, l'historique d'audit de la base est incomplet.

## 5. Retirer

Supprimer le répertoire du bundle sous `bases/`, puis laisser la découverte
suivante constater le retrait. Rien n'est perdu tant que le dépôt existe
ailleurs : un bundle est un dépôt git autonome, et **une base se lit sans le
hub** — c'est un répertoire de markdown.

Avant de retirer : vérifier qu'aucune proposition en attente n'y dort, et que le
dépôt a bien été poussé si son contenu compte.

## Pourquoi l'invariant d'audit tient

Toute proposition apparaît dans **exactement deux commits** : un de soumission,
un de résolution — éventuellement partagé avec d'autres propositions du même
sujet. C'est ce qui permet de reconstituer l'histoire complète d'une affirmation
depuis l'historique git, sans base de données annexe.

Cet invariant est la raison de la plupart des contraintes ci-dessus : le verrou,
la granularité d'une résolution, l'interdiction d'écrire hors circuit, l'étape de
réconciliation. Ce ne sont pas des précautions abstraites — chacune garde une
manière précise de le casser.
