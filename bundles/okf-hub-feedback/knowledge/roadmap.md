---
type: Roadmap
title: Roadmap des évolutions de l'outillage du hub
description: >
  Ce qui a été décidé, reporté ou refusé sur les outils du hub, avec le motif de
  chaque décision et le retour d'usage qui l'a provoquée.
tags: [kb_search, kb_propose, kb_proposal_status, kb_list, kb_hub_rescan, kb-review]
applies-to: "rév. 4.1"
generated:
  by: claude-code/opus-5
  at: 2026-08-30T00:00:00Z
sources:
  - id: retour-j5
    resource: "premier retour d'usage réel d'une session consommatrice, post-J5"
  - id: amendement-41
    resource: "docs/SPEC-okf-bundle-hub-v0.md — amendement rév. 4.1"
---

# Roadmap des évolutions de l'outillage

Ce document est la mémoire des arbitrages. Une demande qui figure ici en
« refusée » avec son motif n'a pas à être re-instruite : c'est précisément ce
qu'on y range pour qu'elle ne revienne pas tous les trois mois (golden rule 4).

## Livré — rév. 4.1

### `kb_proposal_status` — consulter la résolution d'une proposition

**Retour à l'origine.** Un contributeur MCP-only voyait ce qui était en attente
(`kb_list` + `include_pending_concerns`) mais ne pouvait pas lire le motif de
rejet ni l'intégration de ses propres propositions : `accepted/` et `rejected/`
n'étaient lisibles que par accès git direct au dépôt.[^retour-j5]

**Décision.** Livré. Lecture pure, sans verrou ni état nouveau, git reste
canonique. Le statut est **déduit de l'emplacement** du fichier, qui fait foi ;
le champ `status` du frontmatter n'est qu'affiché, et une divergence est
signalée sans faire échouer l'appel.

**Ce qu'il faut savoir en l'utilisant :**

- au moins un de `id` ou `submitted_by` est requis — sans filtre, l'outil
  déverserait `proposals/` en entier ;
- le **corps** de la proposition n'est pas retourné. L'`id` et
  `integrated-into` suffisent : on va lire le corpus avec `kb_read` ;
- `submitted_by` est déclaratif et non authentifié. Le filtre retrouve ce qui a
  été déclaré sous ce nom, sans garantie d'identité.

### `kb_list` — re-scan implicite

**Retour à l'origine.** Une base importée pendant qu'une session était déjà
ouverte lui restait invisible jusqu'à un `kb_hub_rescan` explicite ou un
redémarrage.[^retour-j5]

**Décision.** Livré sous la forme d'un re-scan **avant** chaque `kb_list`,
soumis au cooldown de 5 s déjà en place pour le re-scan sur `UNKNOWN_BASE`
(§ 4.4.c) — même compteur, pas un second mécanisme. Toute session qui liste voit
donc l'état réel du disque.

**Limitation résiduelle, cosmétique :** un client qui ignore la notification
`tools/list_changed` continuera d'afficher une description de `kb_list` périmée
jusqu'à sa prochaine énumération d'outils. Le contenu retourné, lui, est à jour.

### `kb_search` — heading de section dans les extraits

**Retour à l'origine.** Sur un document dépassant `read-toc-threshold`, un
résultat de recherche obligeait à un aller-retour : `kb_read` sans `section`
rendait la table des headings, qu'il fallait lire pour rappeler `kb_read` avec
la bonne section.[^retour-j5]

**Décision.** Livré. Chaque extrait est annoté, après `§`, du heading normalisé
de la section contenant la ligne touchée — ou `(préambule)` si elle précède tout
heading. La valeur se reporte **telle quelle** dans `kb_read(path, section)` :
le chaînage se fait en un appel.

### `kb_propose` — clarification sur `schema.yaml`

**Retour à l'origine.** Une session consommatrice a cru devoir valider le
frontmatter de sa proposition contre le `schema.yaml` de la base.[^retour-j5]

**Décision.** Contresens du modèle : une proposition est une **affirmation
sémantique**, pas un document de corpus. Le `schema.yaml` décrit le frontmatter
du corpus ; la mise en forme conforme relève du gestionnaire, à l'intégration.
La description de `kb_propose` le dit désormais explicitement.

### Convention `status` sur `GOVERNANCE.md`

Un `GOVERNANCE.md` peut porter `status: draft` dans son frontmatter (défaut si
absent : `stable`). En brouillon, `kb_governance` préfixe sa sortie d'un
bandeau, et la skill `kb-review` prévient l'humain que les règles appliquées ne
sont pas validées. **Rien n'est bloqué** : les propositions restent acceptées et
les revues possibles. C'est une convention documentée, pas une machine à états.

## Reporté

### `kb_search` multi-bases

**Demande.** Pouvoir passer `base: [...]` ou `base: "*"` pour chercher dans
plusieurs bases d'un coup.[^retour-j5]

**Décision : reportée en v1 optionnelle**, sur un seul retour. Élargir la
surface d'outils se paie sur toutes les sessions ; on attend la récurrence.

**Spec pré-cadrée**, pour que la reprise soit mécanique le jour venu : plafond
de sortie **global unique**, réparti entre les bases interrogées (et non un
plafond par base, qui multiplierait la sortie par le nombre de bases) ;
résultats **groupés par base**.

## Refusé

### Validation du frontmatter d'une proposition avant dépôt

**Refusée.** Contresens du modèle d'affirmation sémantique — voir ci-dessus la
clarification de `kb_propose`. Une proposition soumet de l'information ; sa mise
en forme est le travail du gestionnaire. Refuser un dépôt sur un critère de
forme reviendrait à demander au contributeur de connaître le schéma du corpus
avant de pouvoir signaler quoi que ce soit.

### Re-scan « partagé au niveau du hub »

**Refusée dans cette forme.** Le besoin — qu'une session voie les bases
importées après son démarrage — est couvert par le re-scan implicite de
`kb_list`. Un rescan partagé, lui, supposerait un état partagé entre instances
ou un démon, contraire au modèle multi-instances du § 4.4 : **aucun état en
mémoire ne fait autorité, la vérité est sur le disque**.

### Authentification de `submitted_by`

**Hors périmètre v0 et v1**, inchangé : le modèle de menace est mono-utilisateur
(§ 8). Le champ reste déclaratif, et ne doit peser dans aucune décision
d'intégration.

[^retour-j5]: Premier retour d'usage réel d'une session consommatrice, post-J5, consolidé par l'amendement rév. 4.1 de la spécification.
