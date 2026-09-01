# Contexte — OKF Bundle Hub

Serveur MCP donnant à des sessions Claude isolées un accès en lecture à des bases
de connaissance markdown, et un moyen de les alimenter **sans jamais les modifier
directement**.

Tu es dans le dépôt du **hub** — le moteur. Les bases, elles, sont des dépôts
séparés clonés dans `bases/` (non versionnés ici).

## Par où commencer

| Document | Quand le lire |
|---|---|
| [`docs/SPEC-okf-bundle-hub-v0.md`](docs/SPEC-okf-bundle-hub-v0.md) | **La spécification. Elle fait autorité.** Rév. 4.1 : l'amendement est intégré section par section, son § 12 en donne la carte. Tous les renvois « § x.y » du code y renvoient. À lire avant toute modification de comportement. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Pourquoi le code est ce qu'il est : choix d'implémentation, mécanismes de correction, **écarts assumés**, traçabilité test ↔ exigence. |
| [`docs/API.md`](docs/API.md) | Contrat exact des sept outils `kb_*` : schémas, sorties, erreurs, séquences typiques. |
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
ligne de commande. `kb_proposal_status` *lit* `proposals/` — c'est la seule
exception à la liste d'exclusions du § 5.2, en lecture seule, et le confinement
passe par `Base.proposal_files`, pas par l'outil.

**Une base ne se versionne jamais dans `bases/`.** `gitops.commit_paths` exécute
`git -C <racine du bundle>` : une base qui ne serait qu'un sous-répertoire du
dépôt du hub ferait qu'un `kb_propose` de n'importe quelle session **commite sur
`main` du hub**, sans erreur (vérifié, pas supposé) ; ignorée par git, elle casse
dans l'autre sens avec `IO_ERROR`. La **source** des bases livrées vit donc dans
`bundles/`, et `bootstrap.py` les installe en dépôts autonomes au démarrage.
Toute nouvelle base livrée s'ajoute à `bundles/` **et** à `server.META_BASES` si
elle doit être annoncée dans les `instructions`. Si elle a un dépôt canonique,
elle s'ajoute aussi à `bundles/upstreams.yaml` : elle sera clonée et non semée,
sans quoi chaque machine s'en fabriquerait une histoire orpheline.

**Ce qui est écrit sur les outils ailleurs que dans leur description doit être
gardé par un test.** Deux bases meta (`okf-hub-guide`, `okf-hub-feedback`)
décrivent le hub. `tests/test_bases_meta.py` lit les `SCHEMA` du code et échoue
si un corpus cite un outil inexistant, attribue un paramètre absent d'un schéma,
ou recopie la référence d'API. Il lit `bundles/`, pas `bases/` : la CI part d'un
checkout neuf, où `bases/` est vide. Sans ce garde-fou, la duplication dérive — et
c'est la copie que personne ne teste qui égare une session. `server.META_BASES`
ne fait que décider de leur annonce dans `instructions` ; aucun outil ne les
traite différemment, le hub tourne sans elles.

**Le re-scan a un seul mécanisme, et un compteur par déclencheur.**
`HubServer._silent_rescan` est le seul point de re-scan silencieux ;
`_last_silent_rescan` est un dict indexé par déclencheur (§ 4.4.c, rév. 4.2) —
un compteur commun laissait un `kb_list` étouffer le re-scan compensatoire
d'`UNKNOWN_BASE`, post-mortem en `ARCHITECTURE.md` § 5 bis. Un second
déclencheur se branche dans `server.RESCAN_BEFORE`, jamais dans un module de
`tools/` — qui n'a ni le cooldown ni la session à notifier. C'est le *mécanisme*
que la rév. 4.1 interdit de dupliquer.

**Le travail bloquant passe par `anyio.to_thread.run_sync`.** Un `subprocess` git
ou ripgrep appelé directement dans un handler sérialiserait toutes les requêtes
de l'instance.

## Vérifier avant de conclure

```sh
uv run pytest -q                 # 237 tests, dont la boucle de stress
uv run pytest -q -m "not slow"   # sans le test deux processus × 25 itérations
```

Quatre tests d'`test_end_to_end.py` sont ignorés si le dépôt voisin
`okf-bundle-template` est absent : c'est attendu, pas une régression.

Un changement de comportement d'outil se vérifie aussi de bout en bout :
`tests/test_end_to_end.py` et `tests/test_boucle_contribution.py` pilotent un
vrai serveur en stdio avec le SDK client officiel. Le second porte le **critère
d'acceptation** de la rév. 4.1 : contribution → revue → consultation du verdict
sans aucun accès git côté contributeur.

## Ce que ce dépôt ne contient pas

- **Les bases.** `bases/` est ignoré par git : chaque base est un dépôt à part,
  importé par `git clone <url> bases/<nom>`. Seule la **source** des bases
  livrées avec le hub est versionnée, dans `bundles/`.
- **Le template de bundle.** Livrable distinct, publié sur GitHub et non monté
  dans le devcontainer. Une modification qui lui revient ne peut donc pas être
  appliquée ici : la livrer en patch, avec sa procédure, et la retirer du dépôt
  une fois appliquée en amont.
- **Les corpus.** Le hub ne connaît que des chemins ; il n'a aucune opinion sur
  le contenu, et n'implémente aucune validation de conformité OKF.
