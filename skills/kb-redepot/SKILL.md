---
name: kb-redepot
description: Déposer des propositions dans une base du OKF Bundle Hub quand la session n'a pas les outils kb_* — typiquement les parts « à porter vers <base> » relevées pendant une revue (kb-review). Passe par le vrai serveur en client MCP stdio, jamais par un import de propose_tool.
---

# Redéposer des propositions sans hub connecté

Une revue (`skills/kb-review`) relève souvent des parts qui appartiennent à une
base voisine. Quand le hub est connecté à la session, on les redépose par
`kb_propose`. Sinon, ce skill fait le même dépôt, par le même outil.

## Le principe : un vrai client, pas une imitation

`deposer.py` lance le serveur **exactement comme un client Claude** (stdio,
`python -m okf_hub --hub-root <hub>`) et appelle l'outil `kb_propose` du serveur.
Schéma, confinement à `proposals/pending/`, verrou et commit sont ceux de la
production — c'est ce que font `tests/test_end_to_end.py` et
`tests/test_boucle_contribution.py`.

Ce qui reste interdit : importer `propose_tool` (ou écrire dans
`proposals/pending/`) depuis un script. Cela contourne la frontière du hub, et
le classifieur du mode auto le refuse.

## Déroulé

1. **Rédiger** chaque proposition comme une contribution autonome
   (`bundles/okf-hub-guide/knowledge/proposer.md`) :
   - vérifier que la base cible l'accueille, **par la règle de son
     `GOVERNANCE.md`**, et adapter la forme à ce qu'elle attend (phoenix-blueway :
     générique et anonymisé ; el2d-blueway : un motif et la règle qu'on en tire ;
     el2d-referentiel : par objet) ;
   - première entrée de `sources` : `redépôt depuis <base>:<id>, instruction du
     <date>` — la proposition doit dire d'où elle vient, sinon le gestionnaire
     de la base cible peut la renvoyer (`kb-review`, étape 3.d, « déjà
     redirigée ») ;
   - revérifier chaque fait dans les exports et les dumps plutôt que recopier
     l'instruction ; une part non étayée ou déjà couverte ne se redépose pas ;
   - `submitted_by` : `claude/kb-review-<date> (redépôt)`.
2. **Écrire** la liste dans un fichier JSON du répertoire temporaire (une liste
   d'objets, chacun égal aux arguments de `kb_propose`).
3. **Valider** contre le schéma que le serveur annonce :
   ```
   uv run python skills/kb-redepot/deposer.py <fichier.json> --check
   ```
4. **Déposer** — c'est une écriture dans les bases : seulement sur demande
   explicite de l'humain.
   ```
   uv run python skills/kb-redepot/deposer.py <fichier.json>
   ```
   Chaque dépôt affiche l'id attribué. Donne-les à l'humain, avec la base cible.

Les propositions déposées attendent ensuite une revue dans leur base, comme
toutes les autres : les redéposer ne vaut pas les intégrer.
