---
type: Procédure
title: Déposer une proposition recevable, et en relire le verdict
description: >
  Ce qui fait qu'une proposition est intégrée plutôt que rejetée, le contresens
  le plus fréquent sur schema.yaml, et comment retrouver la résolution.
tags: [kb_propose, kb_proposal_status, kb_governance]
applies-to: "rév. 4.1"
generated: { by: "claude-code/opus-5", at: 2026-08-30T00:00:00Z }
---

# Déposer une proposition, et en relire le verdict

Une proposition est une **affirmation sémantique** : tu affirmes un fait, avec
sa provenance. Tu ne rédiges pas un document, tu ne proposes pas un diff, tu ne
mets rien en forme. La mise en forme est le travail du gestionnaire.

## La séquence

```
kb_governance  → ce que cette base exige d'une proposition
kb_search      → que dit le corpus aujourd'hui sur ce sujet ?
kb_list        → quelqu'un l'a-t-il déjà signalé ?   (option des concerns en attente)
kb_propose     → le dépôt. Note l'identifiant retourné.
```

`kb_governance` n'est pas une formalité : les règles d'intégration varient d'une
base à l'autre. Certaines rejettent une affirmation sans seconde source ;
d'autres exigent la version du produit sur laquelle le constat a été fait. Tu ne
peux pas deviner.

`kb_search` avant de proposer sert à deux choses : savoir si ton fait **contredit**
un document existant — ce qui change le type de ta proposition — et pouvoir citer
le document concerné, ce qui fait gagner du temps à la revue.

## Le contresens le plus fréquent

**Le `schema.yaml` d'une base décrit le frontmatter de son corpus, pas celui des
propositions.** Ta proposition n'a pas à s'y conformer. N'essaie pas de produire
un document conforme au schéma : soumets l'information, le gestionnaire la mettra
en forme à l'intégration. Les champs de l'outil de proposition sont le seul
format requis.

Ce contresens est assez fréquent pour avoir motivé une clarification de la
spécification. Si tu te surprends à construire un frontmatter, arrête-toi.

## Ce qui fait la différence entre intégré et rejeté

**Une provenance exploitable.** « J'ai constaté que » ne se vérifie pas. Une URL,
un numéro d'incident, une référence de ticket, un constat daté et localisé : si
personne ne peut aller vérifier, la proposition est invérifiable et sera rejetée
par la plupart des bases.

**Un sujet qui permet le regroupement.** Le sujet déclaré sert au gestionnaire à
rapprocher les propositions qui parlent de la même chose — doublons,
contradictions, compléments. Un sujet précis fait bien regrouper ; un sujet vague
isole ta proposition et lui fait perdre son contexte.

**Le bon type.** Quatre valeurs, qui ne veulent pas dire la même chose au
gestionnaire :

| Type | Quand |
|---|---|
| `observation` | Un fait constaté, sans présumer d'un document existant |
| `correction` | Le corpus dit le contraire, et tu peux dire lequel |
| `addition` | Le sujet est couvert, tu le complètes |
| `question` | Une lacune : tu n'as pas la réponse, tu signales qu'elle manque |

Une `question` ne sera jamais intégrée telle quelle — c'est normal, ce n'est pas
un échec. Elle déclenche une enquête ou un rejet motivé.

**Une confiance honnête.** Surestimer la confiance ne fait pas passer une
proposition : les règles de la base croisent confiance et sources. Une confiance
basse assumée, avec une bonne source, passe mieux qu'une confiance haute qu'on ne
peut pas vérifier.

## Après le dépôt

**Rien n'est intégré.** Le corpus n'a pas bougé. Ta proposition attend une revue
humaine, qui peut prendre des jours. Ne raisonne pas comme si le fait était
désormais dans la base.

**Note l'identifiant** retourné au dépôt. C'est lui qui te permettra de retrouver
le verdict, dans cette session ou dans une autre :

```
kb_proposal_status  → en attente / intégrée / rejetée
kb_read             → si intégrée, les documents cités par la résolution
```

Une proposition rejetée porte son motif. Lis-le : il t'apprend la règle que tu
as manquée, et c'est ce qui te fait proposer mieux la fois suivante. Un rejet
n'est pas un échec du circuit, c'est le circuit qui fonctionne.
