# okf-hub-feedback

Base de connaissance des **retours d'usage sur le OKF Bundle Hub lui-même** :
comportement des outils `kb_*`, ergonomie du circuit de contribution, roadmap
des évolutions et limitations connues.

C'est le dogfooding du hub : le circuit de contribution du hub sert à faire
évoluer le hub. Un retour arrive par `kb_propose`, se résout par la skill
`kb-review`, et son verdict se relit par `kb_proposal_status`.

## Déposer un retour

Depuis une session connectée au hub :

```
kb_propose(base: "okf-hub-feedback", type: "observation", …)
```

Deux règles qui font rejeter la moitié des retours mal formés (voir
`GOVERNANCE.md`) :

1. **cite l'outil** concerné, sous sa forme exacte ;
2. **décris le comportement observé** — entrées, base, sortie obtenue, sortie
   attendue — avant de proposer quoi que ce soit.

## Consulter

- `kb_search(base: "okf-hub-feedback", query: "<outil>")` ;
- `kb_read(base: "okf-hub-feedback", path: "roadmap.md")` ;
- `kb_proposal_status(base: "okf-hub-feedback", id: "<id rendu par kb_propose">)`.

## Ce que cette base ne contient pas

Le **contenu** des autres bases, l'installation d'un poste, et le code du hub.
La spécification fait autorité et vit dans le dépôt du hub, dans `docs/`.
