# Contribuer

Merci de l'intérêt. Ce projet implémente une spécification écrite ; la façon de
contribuer en découle directement.

## Avant tout : la spécification fait autorité

Le comportement du hub est fixé par
[`docs/SPEC-okf-bundle-hub-v0.md`](docs/SPEC-okf-bundle-hub-v0.md). Tous les
renvois « § x.y » du code et des tests y renvoient.

**Une évidence technique ne suffit pas à s'en écarter.** La spec l'écrit en
clôture : toute déviation aux principes du § 1 ou aux mécanismes du § 4.4.b doit
être remontée avant implémentation. Deux écarts existent aujourd'hui ; chacun est
motivé, mesuré et documenté dans
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) § 5. Un écart introduit sans y
figurer est un bug, pas une amélioration.

Si vous pensez qu'une exigence de la spec est mauvaise : ouvrez une issue avec
le raisonnement et, si possible, une mesure. C'est une discussion valide — la
faire dans une pull request ne l'est pas.

## Mettre en place

Prérequis : Python ≥ 3.11, git, [ripgrep](https://github.com/BurntSushi/ripgrep).

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # si uv n'est pas installé
uv sync
uv run pytest -q
```

Ou ouvrez le dépôt dans VS Code → *Reopen in Container* : le devcontainer
installe tout et lance les tests.

## Ce que vérifie la CI

`uv run pytest -q` sur Python 3.11 et 3.12, y compris la boucle de stress à deux
processus. Une PR dont la suite ne passe pas ne sera pas relue.

Localement, `uv run pytest -q -m "not slow"` saute le test de concurrence
multi-processus (~10 s) pendant l'itération. **Faites tourner la suite complète
avant d'ouvrir la PR.**

## Ce qui ne se négocie pas

Quatre règles. Elles gardent des erreurs qui ne se voient pas en usage normal et
détruisent des données en usage concurrent.

**Ne pas affaiblir les tests de concurrence.**
`test_fd_neuf_par_acquisition` échoue si le verrou réutilise un descripteur de
fichier — auquel cas deux requêtes d'une même instance ne s'excluent plus.
`test_le_commit_ne_retire_aucun_fichier_du_tree` échoue si le `read-tree HEAD`
disparaît — auquel cas chaque commit de proposition supprime tout le corpus. Si
l'un casse, c'est le changement qui est faux.

**Jamais de YAML par templating de chaînes** (§ 1.7). Tout frontmatter, tout
manifeste passe par `yaml.safe_dump` / `yaml.safe_load`. Les tests d'injection
et les invariants d'audit du § 6.2 en dépendent.

**Une nouvelle capacité d'écriture ne va pas dans `tools/`.** La surface MCP
exposée aux sessions consommatrices se limite à `kb_propose`, confiné à
`proposals/pending/`. Ce qui touche au corpus vit dans `review.py`, invoqué en
ligne de commande par un humain.

**Le travail bloquant passe par `anyio.to_thread.run_sync`.** Un `subprocess`
git ou ripgrep appelé directement dans un handler bloque la boucle d'événements
et sérialise toutes les requêtes de l'instance.

## Style

Le code suit celui qui l'entoure : même densité de commentaires, mêmes idiomes.

Deux habitudes propres à ce dépôt, à conserver :

- **Les commentaires disent pourquoi, pas quoi.** `# CRITIQUE : sans ce
  read-tree, le commit supprimerait tout le tree` vaut mieux que
  `# lit le tree de HEAD`.
- **Les renvois à la spec sont dans les docstrings**, sous la forme `(§ 4.4.b.2)`.
  Ils permettent de retrouver l'exigence derrière une ligne de code.

Messages de commit : première ligne courte et impérative, puis un corps qui
explique le raisonnement si le changement n'est pas évident.

## Ajouter un outil MCP

Créer `src/okf_hub/tools/<nom>_tool.py` exposant trois choses :

```python
SCHEMA = {...}                                    # JSON Schema des paramètres
def description(registry) -> str: ...             # recalculée à chaque tools/list
def run(registry, arguments: dict) -> str: ...    # lève ToolError en cas d'erreur métier
```

puis l'inscrire dans `server.TOOLS`. La conversion des erreurs en `isError`, le
re-scan silencieux sur `UNKNOWN_BASE` et les notifications `tools/list_changed`
sont pris en charge par `server.py`.

Un nouvel outil demande : des tests, une entrée dans
[`docs/API.md`](docs/API.md), et une ligne dans le tableau du README.

## Signaler un bug

Ouvrez une issue avec : ce que vous attendiez, ce qui s'est produit, la version
de Python et de git, et l'extrait pertinent de `hub.log` — il porte le PID de
chaque entrée, ce qui est indispensable pour tout problème de concurrence.

Pour une vulnérabilité, ne pas ouvrir d'issue publique : voir
[`SECURITY.md`](SECURITY.md).

## Licence

En contribuant, vous acceptez que votre contribution soit distribuée sous la
licence [Apache 2.0](LICENSE) du projet.
