---
type: Limitation
title: Limitations connues du hub, et comment faire avec
description: >
  Ce que le hub ne fait pas en v0, pourquoi c'est assumé, et le contournement
  quand il y en a un.
tags: [kb_hub_rescan, kb_propose, kb_read, kb_search, okf-lock]
applies-to: "rév. 4.1"
generated:
  by: claude-code/opus-5
  at: 2026-08-30T00:00:00Z
sources:
  - id: readme-hub
    resource: "README.md du dépôt okf-hub — section « Limitations v0 assumées »"
  - id: spec-v0
    resource: "docs/SPEC-okf-bundle-hub-v0.md"
---

# Limitations connues

Une limitation listée ici est **assumée**, pas oubliée. Avant d'ouvrir un retour
sur l'une d'elles, vérifie qu'il apporte un élément nouveau : un cas d'usage que
le contournement ne couvre pas, ou un comportement qui diffère de ce qui est
décrit.

## Visibilité des bases entre sessions

**Ce qui se passe.** `kb_hub_rescan` n'a d'effet que sur l'instance qui
l'exécute. Chaque client MCP connecté a sa propre instance du serveur, avec son
propre registre.

**Depuis la rév. 4.1**, tout appel de `kb_list` déclenche la découverte (sous
cooldown de 5 s) : une base importée est donc visible de toute session qui
liste, sans rescan explicite ni redémarrage.

**Ce qui reste.** La *description* de l'outil `kb_list`, telle que l'affiche le
client, peut rester périmée si celui-ci ignore la notification
`tools/list_changed`. Le contenu retourné par l'appel, lui, est à jour.
C'est cosmétique.

## `submitted_by` n'est pas authentifié

C'est un champ **déclaratif**. N'importe quelle session peut déclarer n'importe
quelle identité. Conséquences pratiques :

- il ne doit peser dans **aucune** décision d'intégration ;
- le filtre `submitted_by` de `kb_proposal_status` retrouve ce qui a été déclaré
  sous ce nom, sans garantie que ce soit la même personne ou le même agent ;
- une proposition dont le `submitted_by` semble usurpé s'escalade à l'humain.

Le modèle de menace de la v0 est mono-utilisateur (§ 8). L'authentification
n'est pas au programme.

## Aucune synchronisation avec un remote

Le hub travaille sur des clones locaux. Il ne fait ni `fetch`, ni `pull`, ni
`push` (§ 4.5). Un bundle mis à jour en amont ne l'est pas sur le hub tant que
personne ne l'a tiré à la main.

**Contournement.** Tirer explicitement, et **sous le verrou de la base** — sans
quoi un `kb_propose` concurrent pourrait s'intercaler :

```sh
okf-lock <base> -- sh -c "git -C \"\$(okf-base-path <base> root)\" pull --ff-only"
```

## Le corps d'une proposition n'est pas relisible via MCP

`kb_proposal_status` retourne l'état, la résolution et `integrated-into`, mais
**pas le corps** de la proposition. C'est délibéré : le corps peut peser 16 Ko,
et une fois la proposition intégrée, ce qui compte est le corpus, lisible par
`kb_read` en suivant `integrated-into`.

Pour une proposition rejetée dont on veut relire le texte exact : accès git
direct au dépôt de la base, dans `proposals/rejected/`.

## Aucune validation de conformité OKF

Le hub ne connaît que des chemins. Il n'a aucune opinion sur le contenu et
n'implémente aucune validation du format OKF : ni le frontmatter, ni les noms
réservés, ni les liens entre concepts ne sont vérifiés. `schema.yaml` est de la
**documentation** injectée au gestionnaire, pas un validateur.

Une seule exception, et c'est un écart documenté : `index.md` et `log.md` sont
**déclassés** dans le classement de `kb_search`, parce que ce sont des sommaires
denses en texte de liens. Ils restent lisibles par `kb_read` et comptés par
`kb_list`.

## Recherche mono-base

`kb_search` interroge une base à la fois. L'extension multi-bases est reportée —
voir `roadmap.md`, section « Reporté ». En attendant : un appel par base, en
s'appuyant sur les descriptions de `kb_list` pour router.

## Gros documents : deux appels, ou un seul

Au-delà de `read-toc-threshold`, `kb_read` sans `section` retourne la **table
des headings** au lieu du contenu. Ce n'est pas une limitation à contourner,
c'est le fonctionnement voulu (§ 1.5 : retourner le minimum pertinent).

Depuis la rév. 4.1, un résultat de `kb_search` porte le heading de sa section
après `§` : le reporter dans `kb_read(path, section)` donne la section en un
seul appel. `force: true` reste disponible pour le document entier.
