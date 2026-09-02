# Journal des changements

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Le projet suit la version de la spécification qu'il implémente : `bundle-spec 0.1`.

## [0.2.6] — 2026-09-02

### Corrigé

- **La version annoncée au handshake MCP était une seconde chaîne en dur,
  désynchronisée de celle du paquet.** `server.py` déclarait `version="0.1.0"`
  indépendamment de `pyproject.toml`, resté lui-même figé à `0.1.0` alors que
  ce journal en était déjà à 0.2.5 : rien ne distinguait donc une installation
  à jour d'une installation périmée qui reproduit un bug déjà corrigé en
  amont. Constaté en pratique (`prop-2026-09-01-9513` d'`okf-hub-feedback`,
  un opérateur a rouvert un bug déjà corrigé sans moyen de le savoir) et
  documenté dans `okf-hub-feedback/knowledge/limitations-connues.md`, section
  « Pas de vérification de fraîcheur de l'installation ».

  La version annoncée vient désormais des métadonnées du paquet installé
  (`importlib.metadata.version("okf-hub")`, dérivées de `pyproject.toml` par
  hatchling à la construction) — une **source unique de vérité**, correcte
  aussi bien en `uv run` local qu'une fois packagé. `pyproject.toml` est
  remonté à `0.2.6` pour refléter l'état réel du projet, qu'il avait cessé de
  suivre depuis 0.1.0 : c'est ce même écart qui causait le bug.

### Tests

2 nouveaux tests dans `tests/test_server_version.py` : la constante
`SERVER_VERSION` et la version annoncée par `HubServer(...).build()` valent
toutes deux `importlib.metadata.version("okf-hub")` — l'invariant qu'un
retour à une chaîne en dur romprait.

## [0.2.5] — 2026-09-02

### Ajouté

- **Synchronisation remote au démarrage (§ 4.5).** Chaque instance de serveur
  synchronise en **fast-forward-only** les bases installées disposant d'un
  remote, avant sa première découverte — un point unique et explicite du
  cycle de vie d'une instance, sans état partagé ni démon (§ 4.4). Une base
  semée depuis `bundles/` (sans remote) n'est jamais concernée. Aucun push :
  une base en avance sur son amont (propositions locales commitées par
  `kb_propose` mais non poussées) n'est pas touchée. Une **divergence** (HEAD
  et l'amont ont chacun des commits que l'autre n'a pas) est **signalée dans
  `hub.log`, jamais écrasée** ; la séquence fetch + fast-forward passe par le
  verrou de base existant, pour ne jamais s'entrelacer avec un `kb_propose` en
  cours. Un remote absent, injoignable, une base sans branche amont, ou un
  verrou occupé ne bloquent jamais le démarrage. Désactivable par
  `sync-on-start: false`. Voir `docs/ARCHITECTURE.md` § 6 quater.

### Tests

14 nouveaux tests dans `tests/test_remote_sync.py` : fast-forward simple,
base déjà à jour, base en avance (rien à tirer), divergence signalée et non
écrasée, remote injoignable, absence de branche amont, verrou occupé,
parcours de `sync_all`, validation de `sync-on-start`, intégration au
démarrage réel du serveur (activé et désactivé).

## [0.2.4] — 2026-09-02

### Ajouté

- **`kb_search` multi-bases (§ 10.3).** `base` accepte désormais, en plus d'un
  nom unique, une **liste de noms** ou `"*"` pour toutes les bases
  enregistrées. Les deux contraintes pré-cadrées à la mise en réserve de cette
  fonctionnalité sont respectées à la lettre : `max_results` reste un
  **plafond de sortie global**, jamais multiplié par le nombre de bases
  interrogées (réparti à parts égales, reliquat redistribué à la base qui peut
  encore en profiter) ; les résultats sont **groupés par base**, chacune sous
  son propre en-tête. Un nom unique (chaîne) garde une sortie strictement
  identique à l'existant — non-régression explicite, voir
  `docs/ARCHITECTURE.md` § 6 ter pour le détail des choix laissés ouverts par
  la spec.

### Tests

16 nouveaux tests dans `tests/test_search_list.py` : répartition du plafond
entre bases (parts égales, reliquat redistribué, borne jamais dépassée),
groupage de la sortie, `base: "*"`, déduplication, nom de base inconnu dans
une liste (`UNKNOWN_BASE` avant toute recherche), non-régression du format à
un seul nom.

## [0.2.3] — 2026-09-01

### Ajouté

- **Clé SSH du conteneur, dans un volume nommé.** Les remotes en
  `git@github.com:` étaient injoignables depuis le devcontainer : ni clé ni
  agent, `SSH_AUTH_SOCK` vide, `git push` en « Permission denied (publickey) ».
  Constaté en découvrant qu'un commit du 31/08 dormait sur place depuis quatre
  jours — le commit avait réussi, seul le push manquait, et rien ne le
  signalait. `post-create.sh` génère désormais une clé ed25519 une fois pour
  toutes dans un volume qui survit aux rebuilds, et affiche sa partie publique
  avec les deux façons de l'enregistrer — deploy key d'un dépôt (recommandé) ou
  clé de compte.

  La host key de github.com n'est plus acceptée à la première rencontre : elle
  est vérifiée contre l'empreinte publiée par GitHub, et en cas d'écart
  `known_hosts` n'est pas modifié — un `ssh-keyscan` gobé tel quel fait
  confiance à qui répond, ce qui est ce que `known_hosts` doit empêcher.

  **Écart assumé au § 4.3** (« montage : le répertoire du hub uniquement »),
  recensé en `docs/ARCHITECTURE.md` § 5.3 avec sa mesure : c'est un **volume
  nommé**, qui n'expose aucun chemin de l'hôte, donc le confinement visé par la
  règle est intact ; l'exposition nouvelle — un matériel de clé lisible dans le
  conteneur — est bornée par le confinement des outils `kb_*` au corpus et par
  le choix d'une deploy key révocable d'un seul dépôt. Alternative sans aucune
  clé dans le conteneur, documentée au README : un `ssh-agent` sur l'hôte, dont
  VS Code transmet la socket.

### Tests

237 tests. `tests/test_devcontainer.py` :
`test_les_montages_du_devcontainer_restent_confines` échoue si un montage
devient un `type=bind`, ou si la source d'un volume ressemble à un chemin.
Remplacer le volume par un bind sur le `~/.ssh` de l'hôte — geste tentant et
d'apparence équivalente, qui exposerait la clé personnelle de l'opérateur — ne
se voyait dans aucun test.

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
