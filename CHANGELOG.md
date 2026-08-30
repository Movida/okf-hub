# Journal des changements

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Le projet suit la version de la spécification qu'il implémente : `bundle-spec 0.1`.

## [0.1.0] — 2026-08-30

Première implémentation complète de la spécification « OKF Bundle Hub v0 »
(révision 4), jalons J0 à J5.

### Ajouté

- **Serveur MCP en stdio** avec six outils : `kb_list`, `kb_search`, `kb_read`,
  `kb_governance`, `kb_propose`, `kb_hub_rescan`. Descriptions d'outils
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
