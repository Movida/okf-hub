# Architecture et décisions de conception

Ce document explique **pourquoi** le code est ce qu'il est. Le **quoi** est dans
la spécification ([`SPEC-okf-bundle-hub-v0.md`](SPEC-okf-bundle-hub-v0.md)), le
**comment l'utiliser** dans [`API.md`](API.md).

Public : quelqu'un qui reprend le projet, ou qui doit juger si un changement est
sûr.

---

## 1. Choix d'implémentation

La spécification (§ 10.1) laissait ces choix ouverts.

| Choix | Décision | Motif |
|---|---|---|
| Langage | **Python 3.12** | `fcntl.flock` en stdlib, `subprocess` avec `env` pour `GIT_INDEX_FILE`, appel de ripgrep trivial. Le pendant Node aurait demandé une dépendance native pour `flock`. |
| SDK MCP | `mcp` ≥ 2.1, **API bas niveau** (`mcp.server.lowlevel.Server`), pas FastMCP | Les descriptions d'outils doivent être **recalculées à chaque `tools/list`** pour énumérer les bases connues (§ 5.1). FastMCP fige les descriptions à l'enregistrement du décorateur. |
| YAML | **PyYAML**, `safe_load` / `safe_dump` | Obligatoire pour la sérialisation (§ 1.7). `safe_*` exclusivement : jamais de désérialisation d'objets arbitraires depuis un bundle tiers. |
| Git | **CLI via `subprocess`**, pas libgit2 | `GIT_INDEX_FILE` et `read-tree` sont directs et documentés. La spec avertissait que libgit2 pouvait ne pas exposer l'index alternatif. |
| Recherche | **ripgrep en sous-processus**, sortie `--json` | Le format ligne `path:line:text` est ambigu dès qu'un chemin contient un `:`. |
| `okf-lock` | **wrapper `/bin/sh` autour de `flock(1)`** | Conforme à § 10.1. L'interopérabilité avec `fcntl.flock` est vérifiée par test (§ 11.3 tranché : elle fonctionne, le wrapper reste en shell). |
| Gestion des dépendances | **`uv`** | Pas de pip ni de venv sur la machine cible ; `uv` est un binaire autonome installable sans privilèges. |

---

## 2. Carte des modules

```
src/okf_hub/
├── __main__.py     Point d'entrée stdio. Charge la config, ouvre le journal,
│                   instancie HubServer, lance anyio.
├── server.py       Câblage MCP. Dispatch des outils, conversion ToolError →
│                   isError, re-scan silencieux sur UNKNOWN_BASE, émission de
│                   tools/list_changed.
│
├── config.py       hub-config.yaml → HubConfig (immuable).
├── hublog.py       Journal multi-instances : O_APPEND, une entrée = un write.
├── errors.py       Codes d'erreur et ToolError.
├── textutil.py     Plafond de sortie (BudgetedWriter), normalisation.
├── mdutil.py       Frontmatter, headings, sections, normalisation de heading.
│
├── manifest.py     Validation de okf-bundle.yaml (§ 3.3).
├── registry.py     Découverte, énumération du corpus, exclusions transverses,
│                   confinement des chemins. Classes Base et Registry.
├── locking.py      flock() par base, fd neuf par acquisition, .git/info/exclude.
├── gitops.py       Index temporaire depuis HEAD, identité explicite, trailers,
│                   synchronisation de l'index partagé.
├── search.py       ripgrep, ET strict puis repli OU, déclassement des sommaires.
├── review.py       Moteur du rôle gestionnaire : réconciliation, plan, résolution.
├── resolve.py      CLI name → chemin, pour les scripts shell.
└── tools/          Un module par outil : SCHEMA, description(), run().
```

**Règle de dépendance :** `tools/` dépend de tout le reste ; `registry` dépend de
`manifest` et `config` ; `search` dépend de `registry` ; **rien ne dépend de
`server`**. On peut donc exercer chaque outil dans un test sans lancer de serveur
MCP — ce que fait la majorité de la suite.

**`review.py` n'est pas un outil MCP.** C'est délibéré : le gestionnaire écrit
dans le corpus, et cette capacité ne doit exister nulle part dans la surface MCP
exposée aux sessions consommatrices (§ 1.3). Il s'appelle en ligne de commande.

---

## 3. Les trois mécanismes qui portent la correction

### 3.1 Le verrou — `locking.py`

```python
with base_lock(bundle_root):      # flock(LOCK_EX) sur bases/<n>/.okf-hub.lock
    ...                            # toute la séquence d'écriture
```

Trois propriétés, chacune conséquence d'une décision :

**Un descripteur neuf à chaque acquisition.** `flock()` s'applique à la
*description de fichier ouverte*, pas au processus. Un fd mis en cache et
partagé entre deux requêtes de la même instance ferait réussir le second
verrouillage — les deux requêtes ne s'excluraient plus. C'est l'exigence
intra-processus du § 4.4.b.1, et elle est vérifiée par
`test_fd_neuf_par_acquisition`, qui échouerait au moindre partage de fd.

**Libération par le noyau.** Aucun timestamp, aucune procédure de bris, aucun
verrou orphelin possible : un `SIGKILL` sur le porteur libère immédiatement
(`test_mort_brutale_du_porteur_libere_le_verrou`).

**Attente par polling avec backoff**, pas `LOCK_EX` bloquant. Un `flock` bloquant
n'a pas de timeout portable ; la boucle 25 ms → 250 ms plafonnée à 15 s donne le
`BASE_BUSY` exigé sans `SIGALRM` ni thread.

### 3.2 L'index git temporaire — `gitops.commit_paths`

```
GIT_INDEX_FILE=<tmp>  git read-tree HEAD     ← sans ça, tout le corpus disparaît
GIT_INDEX_FILE=<tmp>  git add --all -- <paths>
GIT_INDEX_FILE=<tmp>  git commit --no-verify -m <message>
                      git update-index --add --remove -- <paths>   ← voir § 5.2
```

Ce que chaque ligne achète :

- **`read-tree HEAD`** — sans lui l'index temporaire est vide et le commit
  apparaît comme *supprimant l'intégralité du corpus*. C'est le piège classique
  de `GIT_INDEX_FILE`, et le test `test_le_commit_ne_retire_aucun_fichier_du_tree`
  existe pour qu'une régression soit impossible à rater.
- **Index séparé** — aucune course avec un `git add` lancé à la main dans le
  worktree, et les modifications non commitées de l'opérateur ne partent pas dans
  le commit (`test_worktree_sale_non_embarque`).
- **`--all` sur des pathspecs explicites** — enregistre les suppressions autant
  que les ajouts, ce dont a besoin une résolution qui déplace une proposition de
  `pending/` vers `accepted/`.
- **`--no-verify` et `core.hooksPath=/dev/null`** — un bundle tiers ne fait pas
  exécuter ses hooks par le hub.
- **Identité explicite `-c user.name=okf-hub -c user.email=hub@local`** — la
  config git globale du devcontainer ne doit jamais décider de l'attribution
  (§ 4.4.e). Les variables `GIT_AUTHOR_*` héritées de l'environnement sont
  d'ailleurs purgées avant l'appel.
- **`commit.gpgsign=false`** — une config globale exigeant une signature ferait
  échouer tout commit du hub dans un conteneur sans clé.

**Dépôt sans HEAD** : `read-tree` est sauté, l'index vide *est* le comportement
correct pour le tout premier commit (`test_depot_sans_head_premier_commit_correct`).

### 3.3 L'écriture atomique — `propose_tool._write_atomic`

Temporaire dans **le même répertoire** (donc même système de fichiers), `fsync`,
puis `os.replace()`. Un crash ne laisse jamais de `.md` tronqué dans `pending/`.
Le temporaire est préfixé d'un point pour ne pas ressembler à une proposition
s'il survit.

**La fenêtre résiduelle est assumée** : un crash entre le `rename()` et le commit
laisse un fichier valide mais absent de l'histoire git. C'est la raison d'être de
l'étape 0 de la revue.

---

## 4. Parcours d'un appel

### 4.1 `kb_propose`

```
server.on_call_tool
  └─ anyio.to_thread.run_sync            ← le travail est bloquant (git, fichiers) :
       └─ propose_tool.run                  le sortir de la boucle d'événements est
            ├─ registry.get(base)           ce qui rend deux requêtes réellement
            │    └─ UNKNOWN_BASE ?          concurrentes dans une même instance
            │        → re-scan silencieux (cooldown 5 s) → retente UNE fois
            ├─ _validate(...)             ← rejets \n, bornes, énumérations
            └─ with base_lock(root):      ← une seule acquisition pour toute la suite
                 ├─ ensure_git_exclude
                 ├─ _ensure_proposal_dirs  (+ .gitkeep)
                 ├─ _new_id                (unicité vérifiée sur pending+accepted+rejected)
                 ├─ _write_atomic
                 └─ gitops.commit_paths
```

Le verrou est pris **après** la validation : une entrée invalide ne doit pas
faire attendre une autre session.

L'unicité de l'`id` est vérifiée sur les **trois** répertoires : une proposition
résolue conserve son identifiant en migrant, et le réutiliser casserait
`git log --grep`.

### 4.2 Une résolution

```
review.apply_plan
  └─ with base_lock(root):               ← UNE acquisition pour tout (§ 4.4.b.3)
       ├─ load_pending + vérification que toutes les propositions existent
       │    └─ si l'une manque : NOT_FOUND, AVANT toute écriture
       ├─ pour chaque edit  : _apply_edit      (confinement corpus vérifié)
       ├─ pour chaque résolution : frontmatter enrichi, fichier déplacé
       └─ gitops.commit_paths(tous les chemins, message + trailers)
```

La vérification d'existence précède toute écriture : un plan partiellement faux
ne laisse pas le dépôt à moitié modifié
(`test_proposition_inconnue_refusee_sans_effet`).

---

## 5. Écarts assumés par rapport à la spécification

La spec impose (§ 11, clôture) de remonter toute déviation aux principes du § 1
ou aux mécanismes du § 4.4.b. **Ces deux écarts ont été remontés au propriétaire
du projet et acceptés.** Il n'y en a pas d'autres.

### 5.1 Déclassement des noms réservés OKF dans `kb_search`

**Ce que dit la spec.** § 2 : « Document = tout fichier `*.md` sous
`corpus-dir` ». § 5.2 ne prévoit aucun traitement particulier par nom de fichier.

**Ce que fait le code.** `index.md` et `log.md` — noms réservés par OKF v0.2
§ 3.1 — restent des documents (lus par `kb_read`, comptés par `kb_list`) mais
passent **derrière tout autre document** dans le classement de `kb_search`, et la
sortie le signale.

**Motif, mesuré.** Sur le corpus réel `phoenix` (856 documents, 48 `index.md`
générés), 8 requêtes d'exploitation : **28 % des résultats** étaient des
sommaires pleins de texte de liens. Après déclassement : **2 %**. Ce sont des
tables de matières, souvent générées ; elles matchent beaucoup et n'apprennent
rien. Le principe § 1.5 — « retourner le minimum pertinent » — pèse plus lourd
ici que la lettre du § 5.2.

**Pour l'annuler.** Retirer `d.reserved` des clés de tri dans
`search.run_search` (trois occurrences) et le bloc correspondant dans
`search_tool.run`. Tests concernés :
`test_index_et_log_declasses_en_recherche`,
`test_sommaire_reste_trouvable_faute_de_mieux`.

### 5.2 Synchronisation de l'index git partagé

**Ce que dit la spec.** § 4.4.b.2 : le mécanisme d'index temporaire garantit
« aucune interaction avec l'index partagé du dépôt ».

**Ce que fait le code.** Après le commit, et sous le même verrou,
`gitops._sync_shared_index` exécute
`git update-index --add --remove -- <chemins commités>` sur l'index partagé.

**Motif.** Un commit construit via `GIT_INDEX_FILE` fait avancer HEAD **sans
toucher `.git/index`**. L'index partagé reste donc sur l'ancien tree, et
`git status` affiche l'intégralité des propositions commitées comme
**supprimées**, avec `proposals/` en untracked. Deux conséquences, l'une grave :

1. l'étape de réconciliation (§ 7.1.0) prend ces fichiers pour des propositions
   non commitées et **les re-commite**, violant l'invariant « exactement deux
   commits par proposition » (§ 6.2) ;
2. le propriétaire de la base qui ouvre un terminal voit un dépôt qui semble
   avoir perdu tout son contenu.

Le problème a été découvert par `test_depot_cree_le_fichier_et_le_commit`, dont
l'assertion `git status --porcelain == ""` échouait.

**Pourquoi c'est sûr.** La synchronisation est **additive et chirurgicale** :
seuls les chemins que l'on vient de commiter sont reportés. Les modifications
indexées à la main par l'opérateur (`git add` manuel) sont préservées. Elle a
lieu sous le même verrou que le commit, et son échec n'invalide pas la
proposition — le commit est déjà acquis, un avertissement est journalisé.

**Défense supplémentaire.** Indépendamment de cette synchronisation, l'étape de
réconciliation de `review.py` détecte les propositions non suivies en comparant
au **tree de HEAD** (`_head_pending`), pas à l'index. C'est HEAD qui porte
l'invariant d'audit ; un index partagé désynchronisé par n'importe quel outil
git tiers ne peut donc pas la tromper.

---

## 6. Questions ouvertes du § 11 — comment elles ont été tranchées

| § | Question | Décision |
|---|---|---|
| 11.1 | Estimation de tokens | Caractères/4, `textutil.CHARS_PER_TOKEN`. Le plafond s'applique **par bloc entier** : la sortie s'arrête sur un résultat complet, jamais au milieu. Un premier bloc plus gros que le budget entier est tout de même émis — mieux vaut une sortie trop longue que vide. |
| 11.2 | Format de la table des headings | Liste indentée par niveau, avec la taille approximative de chaque section en octets. Le texte du heading est donné **brut** (formatage inline conservé) pour qu'un `section:` recopié tel quel fonctionne. |
| 11.3 | Interopérabilité du verrou | **Vérifiée : elle fonctionne.** `flock(1)` et `fcntl.flock` s'excluent mutuellement sur le même fichier, dans les deux sens (`test_okf_lock_bloque_le_serveur`, `test_le_serveur_bloque_okf_lock`). Le wrapper reste en shell ; il ne délègue à Python que la résolution `name` → répertoire, qui exige un parseur YAML. |
| 11.4 | Normalisation des headings | Même fonction des deux côtés (`mdutil.normalize_heading`). Périmètre : images → alt, liens `[t](u)` et `[t][ref]` → `t`, backticks, `*`/`**`/`***`/`~~`, espaces réduits, casse ignorée. **`_` seulement hors position intra-mot** : sans cette réserve, un heading `` `kb_read` `` se normalisait en `kbread` et ne correspondait plus à lui-même. |

---

## 7. Traçabilité — exigence de test → test

La spec liste des tests obligatoires par jalon (§ 10.2). Table de correspondance,
pour vérifier la couverture sans relire la suite.

### J1 — noyau lecture

| Exigence § 10.2 | Test |
|---|---|
| bundle valide / invalide | `test_bundle_valide_enregistre`, `test_bundle_invalide_ignore_avec_motif`, `test_manifeste_non_parseable_ignore` |
| `bundle-spec` inconnu | `test_bundle_spec_inconnu_charge_avec_avertissement` |
| collision de `name` | `test_collision_de_name_deterministe` |
| `corpus-dir` racine ou contenant `proposals/` | `test_corpus_dir_racine_rejete`, `test_corpus_dir_egal_a_proposals_rejete`, `test_corpus_dir_contenant_proposals_rejete` |
| `title` avec retour à la ligne | `test_title_avec_retour_a_la_ligne_rejete`, `test_title_trop_long_rejete` |
| `description` normalisée / tronquée | `test_description_normalisee_et_tronquee` |
| confinement de chemins | `test_traversee_de_chemin_rejetee`, `test_symlink_sortant_du_corpus_rejete` |
| troncature de recherche et de `kb_list` | `test_troncature_de_recherche_signalee`, `test_budgeted_writer_signale_la_troncature`, `test_kb_list_pending_concerns` |
| repli ET→OU | `test_and_strict_prioritaire`, `test_repli_or_signale_explicitement`, `test_repli_or_classe_par_nombre_de_termes_touches` |
| headings dupliqués et formatés | `test_headings_dupliques_premiere_occurrence_et_mention`, `test_section_correspond_malgre_le_formatage_inline`, `test_normalisation_des_headings` |
| gros document sans section | `test_gros_document_sans_section_retourne_la_table_des_headings` |
| `force: true` | `test_force_true_contourne_la_table_des_headings` |

### J2 — circuit de proposition

| Exigence § 10.2 | Test |
|---|---|
| deux instances, ≥ 50 itérations, zéro perte | `test_deux_instances_proposent_simultanement` (2 processus × 25) |
| requêtes concurrentes dans une instance (fd frais) | `test_fd_neuf_par_acquisition`, `test_requetes_concurrentes_dans_une_meme_instance` |
| mort brutale du porteur de verrou | `test_mort_brutale_du_porteur_libere_le_verrou` |
| non-destruction du tree | `test_le_commit_ne_retire_aucun_fichier_du_tree` |
| worktree sale | `test_worktree_sale_non_embarque` |
| dépôt sans HEAD | `test_depot_sans_head_premier_commit_correct` |
| collision d'`id` | `test_collision_d_id_retiree` |
| injection : `\n` rejetés | `test_retour_a_la_ligne_rejete`, `test_retour_a_la_ligne_dans_une_source_rejete`, `test_aucun_faux_trailer_n_atteint_le_journal_git` |
| injection : caractères YAML spéciaux | `test_caracteres_yaml_speciaux_produisent_un_frontmatter_fidele`, `test_contenu_avec_delimiteur_de_frontmatter` |
| interopérabilité `okf-lock` ↔ serveur | `test_okf_lock_bloque_le_serveur`, `test_le_serveur_bloque_okf_lock` |

### J3 — rôle gestionnaire

| Exigence § 10.2 | Test |
|---|---|
| fichier valide non suivi → commité `(recovered)` | `test_fichier_valide_non_suivi_est_recupere` |
| fichier malformé → signalé, non commité | `test_fichier_malforme_signale_sans_commit` |
| granularité « résolution complète » | `test_okf_lock_serialise_une_sequence_complete`, et `apply_plan` par construction |
| commits § 6.2, lot multi-trailers | `test_lot_mele_integration_et_rejet_en_un_seul_commit`, `test_lot_uniquement_de_rejets`, `test_integration_simple`, `test_rejet_avec_motif` |

### J5 — critère d'acceptation final

| Exigence § 10.2 | Test |
|---|---|
| cycle complet sur une base migrée | `test_cycle_de_vie_complet` (intégration + rejet + lot de 2 sur le même sujet) |
| deux sessions proposant en parallèle | `test_deux_instances_proposent_simultanement` |
| import = clone + rescan, rien d'autre | `test_import_a_chaud_et_rescan_silencieux` |
| client MCP réel de bout en bout | `test_cycle_mcp_complet` (poignée de main stdio, SDK client officiel) |

Exécution :

```sh
uv run pytest -q                 # tout
uv run pytest -q -m "not slow"   # sans la boucle de stress deux processus
```

---

## 8. Ce qu'il faut savoir avant de modifier

**Les tests de concurrence ne sont pas décoratifs.** `test_fd_neuf_par_acquisition`
et `test_le_commit_ne_retire_aucun_fichier_du_tree` gardent deux erreurs qui ne
se voient pas en usage normal et détruisent des données en usage concurrent. Ne
jamais les affaiblir pour faire passer un changement.

**Le journal doit rester en un `write` par entrée.** Le module `logging` de la
stdlib bufferise et peut découper une entrée ; deux instances entrelaceraient
alors des demi-lignes, rendant `hub.log` inutilisable pour diagnostiquer
précisément un problème de concurrence. D'où `hublog.py` en `os.write` direct.

**Rien ne doit sérialiser du YAML par templating de chaînes** (§ 1.7). Si vous
ajoutez un champ au frontmatter d'une proposition, il passe par
`yaml.safe_dump`, sans exception. Les tests d'injection en dépendent.

**Une nouvelle capacité d'écriture ne va pas dans `tools/`.** La surface MCP
exposée aux sessions consommatrices se limite à `kb_propose`, confiné à
`proposals/pending/`. Tout ce qui touche au corpus vit dans `review.py`, appelé
en ligne de commande par un humain ou le gestionnaire.

**Le travail bloquant passe par `anyio.to_thread.run_sync`.** Un appel git ou
ripgrep exécuté directement dans le handler bloquerait la boucle d'événements et
sérialiserait toutes les requêtes de l'instance.

**Ajouter un outil** : créer `tools/<nom>_tool.py` exposant `SCHEMA`,
`description(registry) -> str` et `run(registry, arguments) -> str`, puis
l'inscrire dans `server.TOOLS`. La conversion des erreurs, le re-scan silencieux
et les notifications sont pris en charge par `server.py`.

---

## 9. Ce qui n'est pas fait, et pourquoi

Hors périmètre v0 par décision de la spec (§ 10.3), pas par oubli : extensions
`tools`/`skills` d'un bundle ; `review: agent|auto` ; validation automatique de
schéma ; `kb_proposal_status` ; authentification des contributeurs ; politique
d'incrément de version ; index de recherche dérivé ; revue d'import outillée ;
synchronisation remote ; multi-hub.

**L'index de recherche dérivé mérite un mot** : la spec dit de ne l'ajouter que
si ripgrep devient *mesurablement* insuffisant. Mesure actuelle sur le corpus le
plus gros disponible (856 documents) : découverte + recherche en **0,15 s**. Rien
ne le justifie aujourd'hui.
