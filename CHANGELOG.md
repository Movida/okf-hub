# Journal des changements

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Le projet suit la version de la spécification qu'il implémente : `bundle-spec 0.1`.

## [0.2.2] — 2026-09-01

### Corrigé

- **Le message d'`IO_ERROR` de ripgrep absent envoyait chercher au mauvais
  endroit.** Il renvoyait à `.devcontainer/devcontainer.json`, où « ripgrep »
  n'apparaît pas : l'installation vit dans `.devcontainer/post-create.sh`, et
  celui-ci ne s'exécute qu'à la **création** du conteneur. Le message ne
  couvrait donc aucun des trois autres modes de lancement documentés au README
  — et c'est précisément l'un d'eux qui a échoué le 01/09/2026, un hub lancé
  depuis un clone vu côté hôte (`hub_root=/home/…` dans la ligne de démarrage
  du journal, contre `/workspaces/…` pour une instance intra-conteneur).

  Le nouveau message nomme le **PATH du processus serveur** plutôt que « le
  PATH », dit pourquoi il diffère de celui de l'opérateur — transport stdio,
  donc environnement hérité du client (§ 4.3) — et donne le remède valable
  quel que soit le mode de lancement. Aucun changement de comportement :
  le code d'erreur, le moment où il est levé et l'absence de repli sont
  inchangés (§ 5.2).

### Tests

235 tests. `test_message_ripgrep_absent_cite_des_fichiers_qui_existent` : tout
fichier cité par le message doit exister **et** mentionner ripgrep. Le message
fautif passait tous les contrôles précédents — rien ne gardait ce qu'un message
d'erreur affirme, alors que la même règle vaut déjà pour les corpus meta.

## [0.2.1] — 2026-08-31

### Corrigé

- **Un `kb_list` ne consomme plus le re-scan silencieux d'`UNKNOWN_BASE`.**
  Régression introduite en 0.2.0 : les deux déclencheurs partageaient un seul
  compteur de cooldown, comme le demandait la lettre du § 4.4.c amendé. Un
  `kb_list`, puis un import de base, puis un appel sur cette base dans les cinq
  secondes — séquence banale — voyaient le re-scan compensatoire ignoré et
  l'erreur `UNKNOWN_BASE` rendue telle quelle. La garantie de la rév. 4 « une
  base importée pendant qu'une session tourne devient joignable sans rien
  faire » tombait. Chaque déclencheur compte désormais son cooldown à part ;
  le mécanisme, lui, reste unique, et deux `kb_list` rapprochés ne provoquent
  toujours qu'un seul parcours de `bases-dir`.

  Détecté par `test_import_a_chaud_et_rescan_silencieux` (job bout-en-bout).
  Remonté au propriétaire du projet sous la clause de clôture « en cas de
  conflit entre la rév. 4 et le présent amendement, la rév. 4 prévaut et le
  conflit est remonté », et intégré dans la spec elle-même comme **rév. 4.2**
  du § 4.4.c — ce n'est donc pas un écart de code. Post-mortem complet en
  `docs/ARCHITECTURE.md` § 5 bis.

### Tests

232 tests. `test_le_cooldown_est_le_meme_que_celui_d_unknown_base` — qui gardait
le comportement fautif — est remplacé par
`test_un_kb_list_ne_consomme_pas_le_rescan_d_unknown_base` et
`test_deux_unknown_base_rapproches_ne_scannent_qu_une_fois`.

## [0.2.0] — 2026-08-30

Amendement **rév. 4.1** de la spécification, issu du premier retour d'usage réel
d'une session consommatrice (post-J5). Intégré dans le corps de
[`docs/SPEC-okf-bundle-hub-v0.md`](docs/SPEC-okf-bundle-hub-v0.md), section par
section — voir son § 12, journal des révisions.

Précédé d'un **audit de conformité bloquant** de l'implémentation vis-à-vis de
la rév. 4 (commit par `GIT_INDEX_FILE`, verrou `flock()`, interopérabilité
`okf-lock` vérifiée par test croisé réel, validation anti-injection, étape 0 de
la skill, trailers d'audit, plafonds de sortie, cooldown de re-scan, format du
journal) : **aucun écart constaté**.

### Ajouté

- **`kb_proposal_status`** (§ 5.7) — septième outil. État et résolution des
  propositions : `integrated-into` pour une intégration, le motif pour un rejet.
  Lecture pure, sans verrou ; le statut vient de l'emplacement du fichier, qui
  fait foi, et une divergence avec le frontmatter est signalée sans échouer.
  `id` ou `submitted_by` est requis. Seule exception à la liste d'exclusions
  transverse du § 5.2, en lecture seule et sous le confinement du § 5.3.
- **Re-scan implicite de `kb_list`** (§ 4.4.c) — une base importée après le
  démarrage d'une session lui devient visible dès qu'elle liste, sans rescan
  explicite ni redémarrage. Sous le **cooldown de 5 s déjà existant**, compteur
  partagé avec le re-scan sur `UNKNOWN_BASE`. *(Le partage du compteur s'est
  révélé fautif — corrigé en 0.2.1.)*
- **Heading de section dans les résultats de `kb_search`** (§ 5.2) — chaque
  extrait porte, après `§`, le heading normalisé de la section contenant la
  ligne touchée, ou `(préambule)`. Se reporte tel quel dans
  `kb_read(path, section)` : plus d'aller-retour par la table des headings sur
  les gros documents.
- **Convention `status: draft | stable` sur `GOVERNANCE.md`** (§ 3.4) —
  `kb_governance` préfixe un bandeau, la skill `kb-review` prévient l'humain.
  Convention documentée, pas machine à états : rien n'est bloqué en brouillon.
- **`bundles/` et installation au premier lancement** — la source des bases
  livrées est versionnée dans le dépôt du hub, et le serveur installe au
  démarrage celles qui manquent dans `bases-dir`. Un `git clone` du hub suffit
  donc à disposer du guide. `bases/` reste ignoré par git : une base déployée
  doit être son propre dépôt, sans quoi un `kb_propose` commiterait sur la
  branche `main` du hub — comportement vérifié par test, pas supposé.
  Déploiement idempotent, non destructif, publication par `rename()` atomique
  pour supporter plusieurs instances démarrant ensemble. Désactivable par
  `bootstrap-bundles: false`. `bin/okf-bootstrap` pour l'installation explicite.
- **`bundles/upstreams.yaml`** — une base qui a un dépôt canonique est
  installée par `git clone`, jamais semée depuis `bundles/`. Semer produirait
  sur chaque machine une histoire git sans rapport avec la sienne, et les
  propositions déposées dessus seraient irrécupérables. Un clone qui échoue ne
  retombe pas sur un semis : la base reste absente, le journal donne la commande
  de rattrapage. `okf-hub-feedback` est publiée sur
  <https://github.com/Movida/okf-hub-feedback>.
- **Bundle `okf-hub-guide`** (§ 9) — mode d'emploi du hub pour une session
  connectée, qui ne voit ni le README, ni `docs/API.md`, ni `CLAUDE.md`.
  Séquences, stratégie de recherche et de lecture, rôles et frontière
  d'écriture, propositions recevables, cycle de vie d'une base. **Exclut tout
  schéma d'outil par golden rule** — la référence doit bouger en verrou avec le
  code, une base se met à jour par le circuit de propositions. Exclusion
  vérifiée par `tests/test_bases_meta.py`, qui lit les `SCHEMA` du code et
  échoue sur un outil inexistant, un paramètre absent d'un schéma ou un tableau
  de référence.
- **Bundle `okf-hub-feedback`** (§ 9) — instanciation standard du template,
  dédiée aux retours sur l'outillage du hub. Le hub devient son propre premier
  cas d'usage : les retours arrivent par `kb_propose` au lieu d'un canal manuel.
  Corpus initial : roadmap des évolutions et limitations connues.
- Le volet template du § B5 (`GOVERNANCE.md` livré en `status: draft`, étape de
  validation dans `INSTANTIATE.md`) a été livré en **patch** puis appliqué en
  amont sur `okf-bundle-template` : ce dépôt est un livrable distinct, non
  accessible depuis le devcontainer d'implémentation.

### Modifié

- Description de `kb_propose` : la mention de limitation v0 sur la consultation
  du verdict est remplacée par un renvoi à `kb_proposal_status`, et l'outil
  précise désormais que **`schema.yaml` décrit le corpus, pas les propositions**
  — une proposition n'a pas à s'y conformer.
- `kb_hub_rescan` devient un outil de **diagnostic d'import** (bundles rejetés,
  collisions) plutôt que de rafraîchissement.

### Refusé (documenté pour ne pas être re-proposé)

- Validation automatique du frontmatter d'une proposition contre `schema.yaml`
  avant dépôt : contresens du modèle d'affirmation sémantique.
- Re-scan « partagé au niveau du hub » : supposerait un état partagé ou un
  démon, contraires au § 4.4. Besoin couvert par le re-scan implicite.

### Reporté

- `kb_search` multi-bases, en v1 optionnelle, avec sa spec pré-cadrée (§ 10.3).

### Retiré

- **`base-demo`** — elle avait servi de base de test et n'avait jamais été
  instanciée : son unique document était le modèle de rédaction du template, que
  `INSTANTIATE.md` demande de supprimer, et son `origin` pointait encore sur le
  dépôt template. Le template reste publié pour initialiser une nouvelle base.

### Tests

229 tests (140 avant l'amendement), dont
`tests/test_boucle_contribution.py` — le critère d'acceptation D4 : dépôt par un
vrai client MCP en stdio, résolution par `okf-review`, relecture du verdict, sans
aucun accès git côté contributeur.

## [0.1.0] — 2026-08-30

Première implémentation complète de la spécification « OKF Bundle Hub v0 »
(révision 4), jalons J0 à J5.

### Ajouté

- **Serveur MCP en stdio** avec six outils : `kb_list`, `kb_search`, `kb_read`,
  `kb_governance`, `kb_propose`, `kb_hub_rescan` (un septième, `kb_proposal_status`,
  est arrivé en 0.2.0). Descriptions d'outils
  régénérées à chaque `tools/list` pour énumérer les bases connues, ce qui
  permet le routage sans appel préalable.
- **Découverte de bundles** : validation de manifeste, collisions de `name`
  déterministes, tolérance aux `bundle-spec` inconnus et aux champs d'extension.
- **Recherche** via ripgrep : ET strict avec repli automatique en OU signalé,
  mode expression régulière, extraits contextuels plafonnés.
- **Lecture par section** avec normalisation des headings, et mode « table des
  headings » au-delà d'un seuil configurable.
- **Circuit de proposition** : verrou `flock()` à descripteur neuf par
  acquisition, index git temporaire initialisé depuis HEAD, écriture atomique,
  validation anti-injection des champs injectés dans les messages de commit.
- **Scripts** `okf-lock`, `okf-review`, `okf-base-path`, interopérables avec le
  verrou du serveur.
- **Rôle gestionnaire** : skill `kb-review` et moteur `okf-review`
  (réconciliation, inventaire, contexte, résolution atomique avec trailers
  d'audit, y compris résolution par lot).
- **Devcontainer** et documentation de connexion pour Claude Code, Claude
  Desktop, WSL et conteneur.
- **Documentation de reprise** : spécification transcrite, architecture et
  décisions de conception, référence d'API, compte rendu de vérification OKF.
- **144 tests**, dont une boucle de stress à deux processus, l'interopérabilité
  des verrous, et un cycle complet piloté par un vrai client MCP en stdio.

### Écarts assumés vis-à-vis de la spécification

Motivés, mesurés et réversibles ; détaillés dans `docs/ARCHITECTURE.md` § 5.

- **Déclassement de `index.md` et `log.md` dans `kb_search`.** Sur un corpus
  réel de 856 documents, 28 % des résultats étaient des sommaires générés ; 2 %
  après déclassement.
- **Synchronisation de l'index git partagé après commit.** Sans elle,
  `git status` affiche toutes les propositions commitées comme supprimées, et
  l'étape de réconciliation les re-commite, cassant l'invariant « exactement
  deux commits par proposition ».

### Connu et documenté

- La résolution d'une proposition n'est pas consultable via MCP : `accepted/` et
  `rejected/` demandent un accès git direct. Un `kb_proposal_status` est prévu
  en v1+.
- `kb_hub_rescan` n'agit que sur l'instance qui l'appelle. Atténué par un
  re-scan silencieux déclenché par `UNKNOWN_BASE`.
- `submitted_by` n'est pas authentifié.
- Aucune synchronisation automatique avec un remote.
