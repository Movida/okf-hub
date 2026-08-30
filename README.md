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
| [`docs/API.md`](docs/API.md) | Contrat des six outils `kb_*` : schémas, sorties, codes d'erreur, séquences typiques. |
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
| `kb_search` | Recherche plein texte dans une base. Mode `keyword` (ET strict, repli automatique en OU signalé) ou `regex` (dialecte ripgrep). |
| `kb_read` | Lecture d'un document, ou d'une seule section. Au-delà du seuil, retourne la table des headings — `force: true` pour passer outre. |
| `kb_governance` | Golden rules et schéma de frontmatter d'une base. |
| `kb_propose` | Dépose une proposition dans `proposals/pending/`. Seul outil d'écriture, et il ne touche jamais au corpus. |
| `kb_hub_rescan` | Relance la découverte des bases. **Portée mono-instance**, voir plus bas. |

Le paramètre `base` est toujours le champ `name` du manifeste, jamais le nom du
répertoire dans `bases/` — les deux diffèrent dès qu'un clone est renommé.

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
[`okf-bundle-template`](../okf-bundle-template) et dérouler son
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

### Un rescan n'affecte que la session qui l'exécute

Une base importée pendant qu'une autre session est ouverte restera invisible de
celle-ci jusqu'à son propre `kb_hub_rescan` ou son redémarrage.

Atténuation automatique : une erreur `UNKNOWN_BASE` déclenche un re-scan
silencieux (avec un délai de garde de 5 s) avant de rendre l'erreur. Une session
qui tente d'utiliser une base fraîchement importée la trouvera donc, sans rien
faire de particulier.

### Certains clients ignorent `tools/list_changed`

Le serveur émet la notification MCP `tools/list_changed` quand la liste des
bases change. Claude Desktop l'a historiquement ignorée. **L'implémentation ne
compte pas dessus** : c'est le re-scan sur `UNKNOWN_BASE` qui garantit le
fonctionnement.

Conséquence visible : la description de `kb_list`, qui énumère les bases
connues, peut rester périmée dans le contexte d'une session jusqu'à son
prochain rescan.

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

### La résolution d'une proposition n'est pas consultable via MCP

Un contributeur qui n'a que l'accès MCP voit ce qui est en attente
(`kb_list` avec `include_pending_concerns`) mais **ne peut pas lire le motif de
rejet ni la résolution de ses propositions** : `accepted/` et `rejected/` ne
sont lisibles que par accès git direct au dépôt.

```sh
git -C bases/<nom> log --grep "Proposal: prop-2026-08-30-a3f2"
cat bases/<nom>/proposals/rejected/prop-2026-08-30-a3f2.md
```

Un outil `kb_proposal_status` est prévu en v1+.

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
├── server.py       câblage MCP, re-scan sur UNKNOWN_BASE, notifications
├── config.py       hub-config.yaml
├── registry.py     découverte, corpus, exclusions, confinement des chemins
├── manifest.py     validation de okf-bundle.yaml
├── locking.py      flock() — fd neuf par acquisition
├── gitops.py       index git temporaire initialisé depuis HEAD, identité explicite
├── search.py       ripgrep, ET strict puis repli OU
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

## Hors périmètre v0

Extensions `tools`/`skills` ; `review: agent|auto` ; validation automatique de
schéma ; `kb_proposal_status` ; authentification des contributeurs ; politique
d'incrément de version ; index de recherche dérivé ; revue d'import outillée ;
synchronisation remote ; multi-hub.
