# OKF Bundle Hub

Serveur MCP donnant à des sessions Claude multiples et isolées un accès en
lecture à des bases de connaissance markdown, et un moyen de les **alimenter
sans jamais les modifier directement**.

Implémente la spécification « OKF Bundle Hub v0 » (identifiant de version dans
les manifestes : `bundle-spec: "0.1"`).

## Documentation

| Document | Contenu |
|---|---|
| [`docs/SPEC-okf-bundle-hub-v0.md`](docs/SPEC-okf-bundle-hub-v0.md) | **La spécification.** Elle fait autorité ; tous les renvois « § x.y » du code y renvoient. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Conception : choix d'implémentation, mécanismes de correction, écarts assumés, traçabilité test ↔ exigence. |
| [`docs/API.md`](docs/API.md) | Contrat des sept outils `kb_*` : schémas, sorties, codes d'erreur, séquences typiques. |
| [`docs/J0-verification-okf.md`](docs/J0-verification-okf.md) | La spec OKF externe et ses trois divergences avec celle du hub. |
| [`CLAUDE.md`](CLAUDE.md) | Orientation pour une session ouvrant ce dépôt. |
| [`skills/kb-review/SKILL.md`](skills/kb-review/SKILL.md) | Déroulé de revue du rôle gestionnaire. |

## Principes

- **Git est canonique.** Tout l'état vit dans les dépôts git des bases. Aucune
  base de données. Tout index ou cache est dérivé et régénérable.
- **Frontière de confiance à l'écriture.** Les sessions consommatrices ne
  modifient jamais le corpus : elles déposent des propositions. Seul le rôle
  gestionnaire intègre.
- **Une base sans le hub reste utilisable.** Un bundle est un dépôt markdown
  lisible par un humain ou n'importe quel outil.
- **Optimisation = économie de tokens.** Les outils retournent le minimum
  pertinent ; toute sortie volumineuse est plafonnée (~4 000 tokens) avec
  troncature signalée.

## Démarrage

### Dans le devcontainer (recommandé)

Ouvrir le dépôt dans VS Code → *Reopen in Container*. Le `post-create` installe
ripgrep, `uv` et les dépendances, puis lance les tests.

### Sur une machine, sans conteneur

Prérequis : Python ≥ 3.11, git, [ripgrep](https://github.com/BurntSushi/ripgrep).

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh    # si uv n'est pas installé
uv sync
uv run pytest -q
```

## Outils MCP

| Outil | Rôle |
|---|---|
| `kb_list` | Bases disponibles, avec titre, objet, nombre de documents et de propositions en attente. `include_pending_concerns` ajoute les sujets en attente. |
| `kb_search` | Recherche plein texte dans une base. Mode `keyword` (ET strict, repli automatique en OU signalé) ou `regex` (dialecte ripgrep). Chaque extrait porte le heading de sa section, après `§`, à reporter tel quel dans `kb_read`. |
| `kb_read` | Lecture d'un document, ou d'une seule section. Au-delà du seuil, retourne la table des headings — `force: true` pour passer outre. |
| `kb_governance` | Golden rules et schéma de frontmatter d'une base. Signale par un bandeau une gouvernance en `status: draft`. |
| `kb_propose` | Dépose une proposition dans `proposals/pending/`. Seul outil d'écriture, et il ne touche jamais au corpus. |
| `kb_proposal_status` | État et résolution des propositions : intégrée (avec les documents modifiés) ou rejetée (avec le motif). Lecture pure. `id` ou `submitted_by` requis. |
| `kb_hub_rescan` | Rapport de découverte : bundles rejetés avec motif, collisions de `name`. La découverte elle-même est déjà déclenchée par `kb_list`. |

Le paramètre `base` est toujours le champ `name` du manifeste, jamais le nom du
répertoire dans `bases/` — les deux diffèrent dès qu'un clone est renommé.

**Le `schema.yaml` d'une base décrit le frontmatter de son corpus, pas celui des
propositions.** Une proposition n'a pas à s'y conformer : soumettez
l'information, sa mise en forme conforme au schéma relève du gestionnaire à
l'intégration. Les champs de `kb_propose` sont le seul format requis.

## Connecter un client Claude

Le transport est **stdio** : chaque client connecté lance sa propre instance du
serveur.

### Claude Code

```sh
claude mcp add okf-hub -- /chemin/vers/okf-hub/.venv/bin/python -m okf_hub \
    --hub-root /chemin/vers/okf-hub
```

Ou dans `.mcp.json` à la racine d'un projet :

```json
{
  "mcpServers": {
    "okf-hub": {
      "command": "/chemin/vers/okf-hub/.venv/bin/python",
      "args": ["-m", "okf_hub", "--hub-root", "/chemin/vers/okf-hub"],
      "env": { "PYTHONPATH": "/chemin/vers/okf-hub/src" }
    }
  }
}
```

### Claude Desktop

Dans `claude_desktop_config.json` : mêmes `command` et `args`.

### Depuis Windows, hub dans WSL

La commande doit traverser la frontière WSL. `wsl.exe` prend le relais :

```json
{
  "mcpServers": {
    "okf-hub": {
      "command": "wsl.exe",
      "args": [
        "-d", "Ubuntu", "--cd", "/home/<utilisateur>/okf-hub", "--",
        "/home/<utilisateur>/okf-hub/.venv/bin/python", "-m", "okf_hub"
      ]
    }
  }
}
```

Points de vigilance : utiliser des chemins **Linux** après `--`, et vérifier le
nom de la distribution avec `wsl.exe -l -q`.

### Hub dans un devcontainer

Le client doit lancer le serveur *à l'intérieur* du conteneur :

```json
{
  "mcpServers": {
    "okf-hub": {
      "command": "docker",
      "args": [
        "exec", "-i", "<nom-ou-id-du-conteneur>",
        "/workspaces/okf-hub/.venv/bin/python", "-m", "okf_hub"
      ]
    }
  }
}
```

`-i` est indispensable : sans lui, stdin est fermé et la poignée de main MCP
échoue sans message.

### Vérifier

```sh
.venv/bin/python -m okf_hub --hub-root . --verbose
```

Le serveur attend sur stdin ; le journal part sur stderr. `Ctrl-D` pour sortir.
Si rien n'apparaît, `hub.log` porte la trace du démarrage.

## Importer une base

```sh
git clone <url> bases/<nom>
```

Puis `kb_hub_rescan` depuis une session connectée. **Il n'y a pas d'autre
étape** — c'est un invariant du produit.

Pour créer une base : partir du template
[`okf-bundle-template`](https://github.com/Movida/okf-bundle-template) et dérouler son
`INSTANTIATE.md`.

### ⚠ Avant d'importer un bundle tiers

Importer un bundle en v0 est sans risque d'**exécution** — `tools/` et
`skills/` ne sont pas chargés — mais pas sans risque d'**influence**. Trois
vecteurs d'injection de prompt existent :

1. `title` et `description` du manifeste, injectés dans les descriptions
   d'outils MCP, donc dans le contexte de **toutes** les sessions connectées,
   sans même que quiconque ouvre le bundle ;
2. `GOVERNANCE.md`, injecté dans le contexte du gestionnaire ;
3. `CLAUDE.md`, dans celui de toute session ouvrant le dépôt.

La validation du manifeste limite la surface (pas de retour à la ligne dans
`title`, `description` normalisée et plafonnée à 500 caractères) sans
l'éliminer.

**Consigne v0 : n'importer que des bundles de confiance, et relire le
manifeste, `GOVERNANCE.md` et `CLAUDE.md` avant le premier usage de tout
bundle tiers.**

## Modèle multi-instances — à lire avant d'exploiter

Le transport stdio implique qu'**une instance du serveur tourne par client
connecté**. Plusieurs processus opèrent donc simultanément sur les mêmes dépôts
git. Conséquences pratiques :

### Chaque instance découvre les bases pour elle-même

Il n'y a ni état partagé ni démon : la vérité est sur le disque, chaque instance
la relit. Deux déclencheurs automatiques, chacun sous un **cooldown de 5 s par
instance**, font qu'une base importée après le démarrage d'une session lui
devient visible sans intervention :

- **tout `kb_list` déclenche la découverte** avant de répondre ;
- une erreur `UNKNOWN_BASE` déclenche un re-scan silencieux, puis retente
  l'appel.

Les deux comptent leur cooldown séparément : lister puis appeler dans la foulée
une base importée entre-temps fonctionne, le premier appel ne consomme pas le
re-scan du second.

`kb_hub_rescan` reste utile pour *voir le rapport* d'un import — bundles rejetés
avec leur motif, collisions de `name` — pas pour rafraîchir.

Un rescan « partagé au niveau du hub » a été demandé et **refusé** : il
supposerait précisément l'état partagé que ce modèle exclut.

### Certains clients ignorent `tools/list_changed`

Le serveur émet la notification MCP `tools/list_changed` quand la liste des
bases change. Claude Desktop l'a historiquement ignorée. **L'implémentation ne
compte pas dessus** : ce sont les re-scans ci-dessus qui garantissent le
fonctionnement.

Conséquence visible, et purement cosmétique : la *description* de `kb_list`,
qui énumère les bases connues, peut rester périmée dans le contexte d'une
session. Le *contenu* que l'outil retourne, lui, est à jour.

### Les écritures sont sérialisées, les lectures ne le sont pas

Toute écriture git prend un verrou `flock()` exclusif sur
`bases/<nom>/.okf-hub.lock` — libéré automatiquement à la mort du processus, et
partagé entre le serveur et le script `okf-lock`. Au-delà de 15 s d'attente, une
erreur `BASE_BUSY` invite à réessayer.

Les lectures (`kb_search`, `kb_read`) ne prennent aucun verrou. Une lecture
pendant une intégration peut donc voir un état intermédiaire du worktree.
Accepté en v0.

## Le rôle gestionnaire

Le gestionnaire n'est pas un démon : c'est une session Claude invoquée à la
demande, outillée par la skill [`kb-review`](skills/kb-review/SKILL.md).

Installer la skill pour Claude Code :

```sh
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/kb-review" ~/.claude/skills/kb-review
```

Puis, dans une session : « passe en revue les propositions de la base `<name>` ».

Le moteur sous-jacent est utilisable seul :

```sh
bin/okf-review reconcile <base>            # étape 0, rattrapage
bin/okf-review reconcile <base> --apply
bin/okf-review context   <base>            # golden rules + schéma + corpus
bin/okf-review inventory <base> --full     # propositions en attente
bin/okf-review resolve   <base> --plan plan.json --dry-run
bin/okf-review resolve   <base> --plan plan.json
```

`resolve` et `reconcile --apply` prennent eux-mêmes le verrou, à la granularité
imposée : **une résolution complète = une acquisition**. Ne pas les envelopper
dans `okf-lock`.

Pour toute autre séquence git sur une base, passer par le wrapper :

```sh
root=$(bin/okf-base-path ma-base root)
bin/okf-lock ma-base -- sh -c "git -C '$root' … && git -C '$root' commit -m '…'"
```

Le verrou doit couvrir la séquence **complète**, jamais commande par commande.

## Limitations v0 assumées

### Le corps d'une proposition résolue n'est pas relisible via MCP

`kb_proposal_status` rend l'état, la résolution, `integrated-into` et le motif de
rejet — c'est la boucle complète du contributeur, sans accès git. Ce qu'il ne
rend **pas**, délibérément, c'est le **corps** de la proposition : il peut peser
16 Ko, et une fois intégrée, ce qui compte est le corpus, lisible par `kb_read`
en suivant `integrated-into`.

Pour relire le texte exact d'une proposition rejetée :

```sh
git -C bases/<nom> log --grep "Proposal: prop-2026-08-30-a3f2"
cat bases/<nom>/proposals/rejected/prop-2026-08-30-a3f2.md
```

### La recherche est mono-base

`kb_search` interroge une base à la fois. L'extension multi-bases est reportée en
v1 optionnelle : un seul retour d'usage l'a demandée, on attend la récurrence
avant d'élargir la surface d'outils.

### `submitted_by` n'est pas authentifié

C'est un champ déclaratif. Il ne doit peser dans aucune décision d'intégration.

### Aucune synchronisation avec un remote

Le clone présent dans `bases/` est la copie canonique. Aucun `push` ni `pull`
automatique n'est effectué, même si le bundle a un remote. Toute
synchronisation est manuelle et **hors garanties** : un `pull` qui écrase des
propositions locales non poussées est de la responsabilité de l'opérateur.

Pratique recommandée : pousser après chaque session de revue.

```sh
git -C bases/<nom> push
```

## Bases par défaut

Deux bases documentent le hub lui-même. Ce sont des **bundles ordinaires** —
aucun traitement de faveur dans le code, et le hub tourne sans elles — mais elles
comblent une lacune structurelle : une session connectée en MCP ne voit ni ce
README, ni `docs/API.md`, ni `CLAUDE.md`. Elle ne dispose que des outils et de
leurs descriptions.

Le serveur les annonce dans son champ `instructions`, le seul texte qu'une
session reçoit sans dépenser d'appel — et seulement si elles sont déployées.

### `okf-hub-guide` — mode d'emploi pour une session

Séquences d'appels, stratégie de recherche et de lecture, rôles et frontière de
confiance à l'écriture, ce qu'est une proposition recevable, et le cycle de vie
complet d'une base : créer, déployer, alimenter, réviser, retirer — avec à chaque
étape le rôle qui l'exécute et le moyen employé.

**Elle ne contient aucun schéma d'outil**, par golden rule. La référence vit dans
les descriptions d'outils et dans `docs/API.md` ; une troisième copie serait la
seule qu'aucun test ne garde, et une base se met à jour par le circuit de
propositions quand une référence d'API doit bouger en verrou avec le code.

Cette exclusion n'est pas qu'une intention : `tests/test_bases_meta.py` lit les
`SCHEMA` du code et échoue si un corpus meta cite un outil inexistant, attribue à
un outil un paramètre absent de son schéma, ou introduit un tableau de référence.

### `okf-hub-feedback` — retours sur l'outillage

Le hub est son propre premier cas d'usage : les retours sur **les outils** — pas
sur le contenu métier des autres bases — arrivent par le circuit standard.

```
kb_governance      base=okf-hub-feedback        → ce qu'un retour recevable contient
kb_propose         base=okf-hub-feedback …      → le dépôt
kb_proposal_status base=okf-hub-feedback id=…   → le verdict, plus tard
```

Deux golden rules décident de la recevabilité : **citer l'outil concerné** et
**décrire le comportement observé** (entrées, base, sortie obtenue, sortie
attendue) avant toute demande d'évolution. Son corpus porte la roadmap des
évolutions — décidées, reportées, refusées, avec le motif de chaque arbitrage —
et les limitations connues.

### Elles s'installent au premier lancement

Leur **source** est versionnée dans [`bundles/`](bundles/). Au démarrage, le
serveur installe dans `bases/` celles qui manquent : un `git clone` du hub suffit
donc à disposer du guide, sans second dépôt à cloner.

```sh
bin/okf-bootstrap --list     # ce qui est livré, et ce qui est déployé
bin/okf-bootstrap            # installe ce qui manque, sans rien écraser
```

Pour maîtriser entièrement le contenu de `bases/`, mettre
`bootstrap-bundles: false` dans `hub-config.yaml`.

**Pourquoi deux emplacements.** Une base doit être **son propre dépôt git**. Si
elle n'était qu'un sous-répertoire du dépôt du hub, `gitops.commit_paths`
exécuterait `git -C` dans le dépôt englobant, et un `kb_propose` de n'importe
quelle session **commiterait sur la branche `main` du hub** — sans erreur. Le
détail est dans [`bundles/README.md`](bundles/README.md).

La source de vérité diffère ensuite selon la base.

**`okf-hub-guide`** est rédigée par les mainteneurs, en verrou avec le code :
`bundles/` fait foi, elle est semée de là, et un test vérifie que la copie
déployée n'en diverge pas.

**`okf-hub-feedback`** est alimentée par les sessions : son dépôt publié est
l'original, et elle est donc **clonée**, pas semée.

<https://github.com/Movida/okf-hub-feedback>

Semer une base qui a un dépôt canonique produirait sur chaque machine une
histoire git sans rapport avec la sienne, et les propositions qu'on y déposerait
seraient irrécupérables. Les dépôts canoniques sont déclarés dans
[`bundles/upstreams.yaml`](bundles/upstreams.yaml) ; si le clone échoue, la base
n'est pas installée et le journal dit comment rattraper — absente vaut mieux
qu'orpheline.

## Invariants d'audit

Toute proposition apparaît dans **exactement deux commits** : un de soumission,
un de résolution (éventuellement partagé avec d'autres propositions du même
sujet).

```sh
git -C bases/<nom> log --grep "Proposal: <id>"          # histoire d'une proposition
git -C bases/<nom> log --grep "Submitted-By: <qui>"     # contributions d'un auteur
```

Ces invariants reposent sur le rejet des retours à la ligne dans `concerns`,
`submitted_by` et `sources` : sans cette validation, un contributeur pourrait
forger de faux trailers.

## Architecture

```
src/okf_hub/
├── __main__.py     point d'entrée stdio
├── server.py       câblage MCP, re-scan (kb_list et UNKNOWN_BASE), notifications
├── config.py       hub-config.yaml
├── registry.py     découverte, corpus, exclusions, confinement des chemins
├── manifest.py     validation de okf-bundle.yaml
├── locking.py      flock() — fd neuf par acquisition
├── gitops.py       index git temporaire initialisé depuis HEAD, identité explicite
├── search.py       ripgrep, ET strict puis repli OU
├── bootstrap.py    installation des bases livrées (bundles/ → bases/)
├── governance.py   statut draft/stable d'un GOVERNANCE.md
├── mdutil.py       frontmatter, headings, sections
├── textutil.py     plafonnement des sorties
├── review.py       moteur du rôle gestionnaire
└── tools/          un module par outil kb_*
```

Langage, SDK, bibliothèque YAML, accès git, forme du wrapper de verrouillage :
ces choix étaient laissés ouverts par la spec (§ 10.1). Le raisonnement derrière
chacun, la carte détaillée des modules et les parcours d'appel sont dans
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Écarts assumés par rapport à la spec

Deux, tous deux mesurés, documentés et réversibles :

1. **Déclassement de `index.md` et `log.md` dans `kb_search`** — sur un corpus
   réel de 856 documents, 28 % des résultats étaient des sommaires générés ;
   2 % après déclassement.
2. **Synchronisation de l'index git partagé après commit** — sans elle,
   `git status` affiche toutes les propositions commitées comme supprimées, et
   l'étape de réconciliation les re-commite, cassant l'invariant d'audit.

Motif complet, mesure et manière de les annuler :
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), section « Écarts assumés ».

## Tests

```sh
uv run pytest -q                # tout, dont la boucle de stress
uv run pytest -q -m "not slow"  # sans le test deux instances × 25 itérations
```

Couvre notamment : validation de manifeste et collisions de `name`, confinement
des chemins et symlinks sortants, repli ET→OU, headings dupliqués et formatés,
plafonnement des sorties, non-destruction du tree, worktree sale, dépôt sans
HEAD, collision d'identifiant, tentatives d'injection de trailers, exclusion
mutuelle entre `okf-lock` et le serveur, et deux instances proposant en
parallèle.

`tests/test_boucle_contribution.py` déroule le **critère d'acceptation** de la
rév. 4.1 : dépôt d'une proposition par un vrai client MCP en stdio, résolution
par `okf-review`, puis relecture du verdict — intégration ou motif de rejet —
toujours en MCP seul. Il tourne sur une copie du bundle `okf-hub-feedback`
réellement déployé quand il est présent.

## Hors périmètre v0

Extensions `tools`/`skills` ; `review: agent|auto` ; validation automatique de
schéma ; authentification des contributeurs ; politique d'incrément de version ;
index de recherche dérivé ; revue d'import outillée ; synchronisation remote ;
multi-hub. Reporté en v1 optionnelle : `kb_search` multi-bases.

## Licence

[Apache 2.0](LICENSE). Voir [`NOTICE`](NOTICE).

La spécification transcrite dans `docs/` est de David Morvan et suit la même
licence. Le format OKF auquel le hub se réfère est publié séparément par
Google Cloud Platform, également sous Apache 2.0.

## Contribuer

[`CONTRIBUTING.md`](CONTRIBUTING.md) — et lire d'abord la section « Ce qui ne se
négocie pas ».

Vulnérabilité : [`SECURITY.md`](SECURITY.md), advisory privée, jamais d'issue
publique.
