# Contexte — OKF Bundle Hub

Serveur MCP donnant à des sessions Claude isolées un accès en lecture à des bases
de connaissance markdown, et un moyen de les alimenter **sans jamais les modifier
directement**.

Tu es dans le dépôt du **hub** — le moteur. Les bases, elles, sont des dépôts
séparés clonés dans `bases/` (non versionnés ici).

## Par où commencer

| Document | Quand le lire |
|---|---|
| [`docs/SPEC-okf-bundle-hub-v0.md`](docs/SPEC-okf-bundle-hub-v0.md) | **La spécification. Elle fait autorité.** Tous les renvois « § x.y » du code y renvoient. À lire avant toute modification de comportement. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Pourquoi le code est ce qu'il est : choix d'implémentation, mécanismes de correction, **écarts assumés**, traçabilité test ↔ exigence. |
| [`docs/API.md`](docs/API.md) | Contrat exact des six outils `kb_*` : schémas, sorties, erreurs, séquences typiques. |
| [`docs/J0-verification-okf.md`](docs/J0-verification-okf.md) | Ce que dit la spec OKF externe, et les trois points où elle diverge de celle du hub. |
| [`README.md`](README.md) | Installation, connexion d'un client Claude, exploitation. |
| [`skills/kb-review/SKILL.md`](skills/kb-review/SKILL.md) | Le rôle gestionnaire : déroulé de revue imposé. |

## Règles de travail sur ce dépôt

**La spécification fait autorité.** Elle est dans `docs/`, transcrite depuis la
source du propriétaire. Si une évidence technique la contredit, ce n'est pas une
raison de dévier : les principes du § 1 et les mécanismes du § 4.4.b **doivent
être remontés au propriétaire avant implémentation** — c'est écrit en clôture de
la spec, et deux écarts ont suivi cette procédure.

**Les écarts assumés se recensent à un seul endroit.** `ARCHITECTURE.md`,
section « Écarts assumés », avec motif, mesure et manière de les annuler. Un
écart introduit sans y figurer est un bug.

**Ne pas affaiblir les tests de concurrence.** `test_fd_neuf_par_acquisition` et
`test_le_commit_ne_retire_aucun_fichier_du_tree` gardent deux erreurs invisibles
en usage normal et destructrices en usage concurrent. Si l'un casse, c'est le
changement qui est faux, pas le test.

**Jamais de YAML par templating de chaînes** (§ 1.7). Tout frontmatter, tout
manifeste passe par `yaml.safe_dump` / `safe_load`. Les tests d'injection en
dépendent, et les invariants d'audit du § 6.2 aussi.

**Une nouvelle capacité d'écriture ne va pas dans `tools/`.** La surface MCP
exposée aux sessions consommatrices se limite à `kb_propose`, confiné à
`proposals/pending/`. Ce qui touche au corpus vit dans `review.py`, invoqué en
ligne de commande.

**Le travail bloquant passe par `anyio.to_thread.run_sync`.** Un `subprocess` git
ou ripgrep appelé directement dans un handler sérialiserait toutes les requêtes
de l'instance.

## Vérifier avant de conclure

```sh
uv run pytest -q                 # 144 tests, dont la boucle de stress
uv run pytest -q -m "not slow"   # sans le test deux processus × 25 itérations
```

Un changement de comportement d'outil se vérifie aussi de bout en bout :
`tests/test_end_to_end.py` pilote un vrai serveur en stdio avec le SDK client
officiel.

## Ce que ce dépôt ne contient pas

- **Les bases.** `bases/` est ignoré par git : chaque base est un dépôt à part,
  importé par `git clone <url> bases/<nom>`.
- **Le template de bundle.** Livrable distinct, dans `../okf-bundle-template`.
- **Les corpus.** Le hub ne connaît que des chemins ; il n'a aucune opinion sur
  le contenu, et n'implémente aucune validation de conformité OKF.
