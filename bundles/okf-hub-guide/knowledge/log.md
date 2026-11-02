# Journal des mises à jour

Historique des changements du corpus, groupé par date (convention OKF § 9), le
plus récent en premier. Le gestionnaire y ajoute une ligne à chaque intégration.

## 2026-11-02

* **Chercher et lire** : la recherche lit aussi, sur demande, les propositions
  en attente — après le corpus, marquées « non intégré » ; `kb_list` date la
  plus ancienne (rév. 4.7).
* **Proposer** : un doublon se vérifie par la recherche, propositions en
  attente comprises, plutôt que par la seule liste de leurs objets.
* **Chercher et lire** : un chemin part de la racine du corpus, sans son
  répertoire en tête ; la lecture qui l'oublie donne le bon chemin.

## 2026-10-29

* **Chercher et lire** : `kb_find` prend aussi le texte comme critère (`query`, en
  ET strict) et repère ce qui cite un document d'amont modifié depuis (rév. 4.6).
* **Chercher et lire** : mise à jour de suivi, livrée avec le code (rév. 4.5) —
  `kb_find` pour établir une existence ou une absence par critères, en un
  appel ; il entre dans la stratégie de repli quand rien ne sort.

## 2026-09-17

* **Chercher et lire** : mise à jour de suivi, pas de proposition — le corpus
  était en retard sur des mécanismes déjà livrés au code fin août / début
  septembre. Ajout des liens interbase dans `path` de `kb_read`
  (`<base>:/<chemin>`), de `kb_search` multi-bases (`base` en liste ou `"*"`),
  et de la nature de la troncature de `kb_search` (par volume, pas par
  nombre — relever `max_results` ne fait rien réapparaître).

## 2026-08-30

* **Initialisation** : création de la base à partir de `okf-bundle-template`.
  Elle comble une lacune structurelle — une session consommatrice ne voit ni le
  README du hub, ni sa documentation d'API, ni son CLAUDE.md.
* **Rôles** : les trois rôles et la frontière de confiance à l'écriture, avec les
  anti-patterns constatés.
* **Lecture** : séquences, mécanismes de recherche (repli ET→OU, déclassement des
  sommaires), chaînage d'un extrait vers une section.
* **Propositions** : critères de recevabilité, sémantique des types, et le
  contresens sur `schema.yaml` clarifié par la rév. 4.1.
* **Cycle de vie** : les cinq étapes, avec le rôle et le moyen de chacune, et la
  raison pour laquelle aucune opération de création ou de retrait n'est exposée
  par MCP.
