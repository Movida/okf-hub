# Journal des mises à jour

Historique des changements du corpus, groupé par date (convention OKF § 9), le
plus récent en premier. Le gestionnaire y ajoute une ligne à chaque intégration.

## 2026-09-17

* **Roadmap** : `kb_search` multi-bases (§ 10.3) déplacée de « Reporté » à
  « Livré » — la fonctionnalité avait été livrée le 2026-09-02 (commit
  `00de954`), l'entrée était restée périmée depuis.
* **Limitations** : « Recherche mono-base » retirée, devenue fausse depuis la
  même livraison. Remplacée par la vraie limitation résiduelle : la
  troncature de `kb_search` par budget de sortie, où relever `max_results`
  ne fait réapparaître aucun résultat masqué.

## 2026-08-30

* **Initialisation** : création de la base à partir de `okf-bundle-template`,
  en application de l'amendement rév. 4.1, § B6.
* **Roadmap** : consignation des six évolutions de la rév. 4.1 (livrées,
  reportées, refusées) et des retours d'usage qui les ont motivées.
* **Limitations** : reprise des limitations v0 assumées du README du hub, mises
  à jour de ce que la rév. 4.1 a levé.
