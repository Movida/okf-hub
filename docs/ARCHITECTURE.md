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
│                   isError, re-scan silencieux (avant kb_list et sur
│                   UNKNOWN_BASE — un mécanisme, un cooldown, un compteur par
│                   déclencheur, § 4.4.c rév. 4.2, cf. § 5 bis), émission de
│                   tools/list_changed.
│
├── config.py       hub-config.yaml → HubConfig (immuable).
├── hublog.py       Journal multi-instances : O_APPEND, une entrée = un write.
├── errors.py       Codes d'erreur et ToolError.
├── textutil.py     Plafond de sortie (BudgetedWriter), normalisation.
├── mdutil.py       Frontmatter, headings, sections, normalisation de heading,
│                   heading de la section contenant une ligne donnée.
├── governance.py   Statut draft/stable d'un GOVERNANCE.md et son bandeau.
├── bootstrap.py    Installation des bases livrées (bundles/ → bases/), au
│                   démarrage et en ligne de commande. Publication atomique.
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

### 4.3 `kb_proposal_status` — la lecture qui ne verrouille rien

```
run()
 ├─ registry.get(base)                      UNKNOWN_BASE si inconnue
 ├─ contrainte id | submitted_by             INVALID_INPUT si aucun des deux
 ├─ pour chaque statut retenu :
 │   └─ Base.proposal_files(statut)          confinement § 5.3 : resolve() puis
 │       │                                    vérification d'inclusion stricte
 │       └─ parse_document()                 frontmatter illisible → compté, ignoré
 ├─ tri par submitted-at décroissant
 ├─ filtres id / submitted_by                id sans résultat → NOT_FOUND
 └─ BudgetedWriter                           plafond transverse
```

**Aucun verrou n'est pris**, et c'est correct : la lecture peut voir un état
intermédiaire pendant une résolution (§ 4.4.d), au pire une proposition juste
avant son déplacement. Prendre le verrou ferait payer 15 s d'attente à une
consultation pour un gain nul — git reste canonique, la lecture suivante sera
juste.

**L'emplacement fait foi.** Le statut vient du répertoire, pas du frontmatter.
Une divergence est signalée dans la sortie et n'interrompt rien : un fichier
déposé à la main dans `accepted/` avec `status: pending` reste lisible.

**Le confinement est dans `registry`, pas dans l'outil.** `Base.proposal_files`
résout canoniquement chaque candidat et vérifie son inclusion stricte dans
`proposals/<statut>/` — même mécanique que `resolve_document` pour le corpus. Un
lien symbolique déposé dans `pending/` et pointant hors du bundle est ignoré.
C'est ce qui permet d'affirmer que l'exception à la liste d'exclusions du § 5.2
ne perce pas le confinement.

---

## 5. Écarts assumés par rapport à la spécification

La spec impose (§ 11, clôture) de remonter toute déviation aux principes du § 1
ou aux mécanismes du § 4.4.b. **Les écarts 5.1 et 5.2 ont été remontés au
propriétaire du projet et acceptés.** Le § 5.3 ne relève ni du § 1 ni du
§ 4.4.b — c'est un écart au § 4.3 — mais il est demandé par le propriétaire et
recensé ici au même titre : un écart qui ne figure pas dans cette liste est un
bug. Le § 5 bis documente un cas distinct : un bug de l'amendement rév. 4.1,
remonté puis corrigé **dans la spec elle-même** (rév. 4.2), pas laissé en écart
de code.

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

### 5.3 Un montage de plus que « le répertoire du hub uniquement »

**Ce que dit la spec.** § 4.3 : « montage : **le répertoire du hub uniquement**.
Les outils ne doivent jamais accéder hors de `bases-dir` (voir § 8). »

**Ce que fait le devcontainer.** Un second montage, `okf-hub-devcontainer-ssh`,
**volume nommé** monté sur `/home/vscode/.ssh`. Il porte les clés SSH du
conteneur — **une deploy key par dépôt**, générées par `deploy-keys.sh` — et le
`~/.ssh/config` qui associe chaque dépôt à la sienne.

**Motif.** Les remotes en `git@github.com:` sont injoignables depuis le
conteneur : ni clé ni agent, `SSH_AUTH_SOCK` vide, `git push` échoue en
« Permission denied (publickey) ». Le 01/09/2026, on a découvert qu'un commit du
31/08 dormait sur place depuis quatre jours pour cette raison — le commit avait
réussi, seul le push manquait, et rien ne le signalait. Sans volume, la clé
disparaîtrait à chaque rebuild et il faudrait la réenregistrer sur GitHub à
chaque fois.

**Mesure de ce que ça ouvre.** Le motif de la règle du § 4.3 est l'accès des
outils au système de fichiers de l'hôte. Un volume Docker n'expose **aucun**
chemin de l'hôte : le confinement visé est intact, seule la lettre de la règle
est pliée. Reste une exposition réelle et nouvelle : un matériel de clé est
lisible dans le conteneur. Elle est bornée par trois faits — les outils `kb_*`
sont confinés au corpus par `Base.resolve_document` (§ 5.3 de la spec), donc
aucune session ne lit `~/.ssh` à travers eux ; les clés sont sans phrase de passe
mais chacune est **une deploy key d'un seul dépôt**, donc son pire usage est un
push forcé sur ce dépôt, pas l'accès au compte ; et chacune est révocable en un
clic, sans toucher aux autres.

**Ce que le mécanisme a coûté, et pourquoi il est là.** Cette borne était
d'abord une intention écrite dans `devcontainer.json`, que rien ne faisait
tenir : le 01/09/2026, la clé du conteneur était en fait enregistrée **sur le
compte** (`ssh -T` répondait `Hi <login>!` et non `Hi Owner/Repo:`), donc en
écriture sur tous les dépôts du compte, depuis un conteneur où tournent des
sessions Claude. Une intention non outillée ne tient pas. `deploy-keys.sh`
l'outille, et l'obstacle qu'il contourne mérite d'être connu : choisir la clé
selon le dépôt n'est possible en SSH que par le nom d'hôte, et écrire un alias
d'hôte dans les URL contaminerait `bundles/upstreams.yaml`, versionné et censé
rester clonable depuis n'importe quelle machine. La réécriture vit donc dans la
config git **globale du conteneur**, en `url.<alias>.insteadOf`, sur les deux
formes d'URL (`git@github.com:` et `https://github.com/`) — locale à la machine,
appliquée aussi à `git clone`, donc aux clones de `bootstrap.py`. Elle n'est
posée qu'après vérification que la deploy key est acceptée : tant qu'elle ne
l'est pas, l'URL canonique continue de fonctionner et la migration se fait dépôt
par dépôt, sans coupure.

**Pour l'annuler.** Retirer le bloc `mounts` de
`.devcontainer/devcontainer.json` et les blocs « clé SSH » et « deploy keys » de
`.devcontainer/post-create.sh`, supprimer `.devcontainer/deploy-keys.sh`, purger
les réécritures (`git config --global --remove-section url.<alias>` pour chacune),
supprimer le volume (`docker volume rm okf-hub-devcontainer-ssh`), révoquer les
deploy keys sur GitHub. Pour
garder le push SSH sans aucun matériel de clé dans le conteneur : faire tourner
un `ssh-agent` sur l'hôte avant d'attacher VS Code, qui transmet sa socket.
Test concerné : `test_les_montages_du_devcontainer_restent_confines`.

## 5 bis. Post-mortem — cooldown de re-scan : bug de l'amendement rév. 4.1, corrigé par la rév. 4.2

Contrairement au § 5, **ceci n'est pas un écart** : le code suit la spec à la
lettre, à jour de la rév. 4.2. Ce post-mortem existe pour que la trace du
raisonnement survive au correctif — pourquoi la lettre de la rév. 4.1 était
intenable, pas seulement ce que dit la version corrigée.

**Ce que demandait la rév. 4.1.** § 4.4.c amendé : tout `kb_list` déclenche la
découverte, « sous le même cooldown de 5 s et **le même compteur** que le
re-scan sur `UNKNOWN_BASE` — ce n'est pas un second mécanisme ». Or la rév. 4,
au même paragraphe, garantit qu'`UNKNOWN_BASE` déclenche toujours son propre
re-scan compensatoire avant de rendre l'erreur.

**Pourquoi les deux phrases ne peuvent pas être vraies ensemble.** Avec un
compteur unique, cette séquence — banale, pas un cas tordu — casse la garantie
de la rév. 4 :

1. un `kb_list` scanne et arme le compteur ;
2. une base est importée dans la seconde qui suit ;
3. un appel sur cette base lève `UNKNOWN_BASE` ;
4. le re-scan compensatoire est ignoré — cooldown encore actif ;
5. l'erreur part telle quelle, sans nouvelle tentative.

Détecté par `test_import_a_chaud_et_rescan_silencieux` (job bout-en-bout).

**Résolution.** Remonté au propriétaire du projet sous la clause de clôture
« en cas de conflit non identifié entre la rév. 4 et le présent amendement, la
rév. 4 prévaut et le conflit est remonté ». Intégré en **rév. 4.2** plutôt que
laissé en écart de code : le § 4.4.c garde un cooldown unique et un mécanisme
unique, mais un horodatage **par déclencheur** — `HubServer._last_silent_rescan`
est un dictionnaire indexé par déclencheur (`kb_list`, `UNKNOWN_BASE`), pas un
flottant.

**Ce que la correction préserve.** L'intention de la rév. 4.1 tient tout entière
dans sa deuxième moitié — « deux `kb_list` en moins de cinq secondes ne
provoquent qu'un seul parcours » — et elle reste vraie
(`test_deux_kb_list_rapproches_ne_scannent_qu_une_fois`). « Ce n'est pas un
second mécanisme » reste vrai aussi : une fonction (`_silent_rescan`), un
branchement (`RESCAN_BEFORE`, § 6 bis), un cooldown, une émission de
`tools/list_changed`. Seul l'horodatage est dédoublé.

**Coût.** Au pire deux parcours de `bases-dir` par fenêtre de 5 s au lieu d'un,
et seulement si les deux déclencheurs se présentent dans la même fenêtre. Chacun
garde son propre garde-fou : une boucle d'appels sur une base inexistante ne
scanne toujours qu'une fois par fenêtre
(`test_deux_unknown_base_rapproches_ne_scannent_qu_une_fois`).

Tests : `test_un_kb_list_ne_consomme_pas_le_rescan_d_unknown_base`,
`test_deux_unknown_base_rapproches_ne_scannent_qu_une_fois`.

---

## 6. Questions ouvertes du § 11 — comment elles ont été tranchées

| § | Question | Décision |
|---|---|---|
| 11.1 | Estimation de tokens | Caractères/4, `textutil.CHARS_PER_TOKEN`. Le plafond s'applique **par bloc entier** : la sortie s'arrête sur un résultat complet, jamais au milieu. Un premier bloc plus gros que le budget entier est tout de même émis — mieux vaut une sortie trop longue que vide. |
| 11.2 | Format de la table des headings | Liste indentée par niveau, avec la taille approximative de chaque section en octets. Le texte du heading est donné **brut** (formatage inline conservé) pour qu'un `section:` recopié tel quel fonctionne. |
| 11.3 | Interopérabilité du verrou | **Vérifiée : elle fonctionne.** `flock(1)` et `fcntl.flock` s'excluent mutuellement sur le même fichier, dans les deux sens (`test_okf_lock_bloque_le_serveur`, `test_le_serveur_bloque_okf_lock`). Le wrapper reste en shell ; il ne délègue à Python que la résolution `name` → répertoire, qui exige un parseur YAML. |
| 11.4 | Normalisation des headings | Même fonction des deux côtés (`mdutil.normalize_heading`). Périmètre : images → alt, liens `[t](u)` et `[t][ref]` → `t`, backticks, `*`/`**`/`***`/`~~`, espaces réduits, casse ignorée. **`_` seulement hors position intra-mot** : sans cette réserve, un heading `` `kb_read` `` se normalisait en `kbread` et ne correspondait plus à lui-même. |

---

## 6 bis. Décisions d'implémentation de l'amendement rév. 4.1

L'amendement laissait quelques points à l'implémenteur. Voici ce qui a été
décidé, et pourquoi.

| Point | Décision | Motif |
|---|---|---|
| Où brancher le re-scan de `kb_list` (§ B2) | Dans `server.on_call_tool`, via `RESCAN_BEFORE`, **pas** dans `list_tool.run` | Les modules de `tools/` reçoivent un `Registry`, pas le serveur : eux ne connaissent ni le cooldown ni la session à notifier. Le brancher là aurait dupliqué le *mécanisme* — exactement ce que l'amendement interdit (« pas un second mécanisme »). Dédoubler le seul horodatage, en revanche, était nécessaire : § 5 bis, intégré en rév. 4.2. |
| Ancre de section d'un extrait (§ B3) | La **ligne touchée**, pas le début de la fenêtre | La fenêtre de contexte déborde de deux lignes et peut mordre sur la section précédente ; annoter avec le heading de celle-ci enverrait `kb_read` au mauvais endroit. |
| Forme du libellé de section (§ B3) | Texte **normalisé** (`Heading.normalized`) | L'amendement le dit explicitement (« le texte normalisé du heading, même normalisation que § 5.3/§ 11.4 »). Le texte brut aurait aussi fonctionné pour le chaînage — `normalize_heading` est idempotente sur lui — mais la spec fait autorité, et le normalisé garantit le round-trip sans hypothèse. |
| Filtre `submitted_by` (§ B1) | Correspondance **exacte, casse ignorée** | Un champ déclaratif est saisi à la main : `Human:Alice` et `human:alice` sont la même intention. Une correspondance partielle, elle, ferait fuiter les propositions d'un homonyme. |
| Frontmatter « illisible » (§ B1) | Frontmatter **absent ou non parseable** | Un frontmatter présent mais incomplet reste affiché avec `(non renseigné)` : c'est une information, pas une erreur. Absent, la proposition n'a pas d'identité exploitable — on ne devine pas. |
| `NOT_FOUND` sur `submitted_by` | **Non** — résultat vide | Un contributeur qui n'a encore rien déposé n'est pas une erreur. `NOT_FOUND` reste réservé à un `id` explicitement demandé et introuvable. |
| Statut de gouvernance inconnu (§ B5) | Vaut `stable`, avec avertissement au journal | `status: brouilon` ne doit pas basculer silencieusement une base en brouillon, ni faire échouer la lecture des règles (§ 1.4). |

**Une base meta ne bénéficie d'aucun traitement de faveur dans le code.**
`server.META_BASES` ne fait qu'une chose : décider si le nom est annoncé dans les
`instructions`, et seulement quand la base est réellement déployée — annoncer un
guide absent coûterait à chaque session un aller-retour pour un `UNKNOWN_BASE`.
Aucun outil ne les traite différemment, et le hub fonctionne sans elles.

**Les bases livrées ont leur source dans le dépôt, jamais leur instance.**
`bundles/` porte la source ; `bases/` reste ignoré par git. Ce n'est pas une
préférence esthétique : `gitops.commit_paths` exécute `git -C <racine du
bundle>`, donc une base qui ne serait qu'un sous-répertoire du dépôt du hub ferait
qu'un `kb_propose` **commite sur la branche `main` du hub**, sans erreur — c'est
vérifié, pas supposé. Ignorée par git, elle casse dans l'autre sens : `git add`
échoue et tout `kb_propose` rend `IO_ERROR`.

**Une base qui a un dépôt canonique est clonée, jamais semée.**
`bundles/upstreams.yaml` déclare ces dépôts. Semer produirait sur chaque machine
une histoire git sans rapport avec la sienne : les propositions déposées dessus
seraient irrécupérables, et l'invariant d'audit du § 6.2 — « exactement deux
commits par proposition » — porterait sur une histoire parallèle. Un clone qui
échoue **ne retombe jamais sur un semis** : la base reste absente, avec au journal
la commande de rattrapage. Absente se voit tout de suite ; orpheline se découvre
le jour où l'on veut remonter six mois de contributions.

Le déploiement tourne au démarrage du processus (`__main__`), pas dans
`HubServer.__init__` : les tests construisent des serveurs par dizaines, et la
découverte doit rester sans effet de bord. Il est **idempotent** — il ne crée que
ce qui manque — et **concurrent-safe** sans verrou de base, lequel serait
impossible ici puisque son fichier vit dans le bundle qui n'existe pas encore :
l'arbre est bâti dans un répertoire temporaire préfixé d'un point, au sein de
`bases-dir`, puis publié par un `os.rename()` atomique. La découverte saute les
répertoires cachés, donc un scan concurrent ne peut pas enregistrer un bundle à
moitié copié.

**La dérive d'un corpus meta est gardée par des tests, pas par des intentions.**
`tests/test_bases_meta.py` lit les `SCHEMA` du code comme source de vérité et
échoue si un corpus cite un outil qui n'existe plus, attribue à un outil un
paramètre absent de son schéma, ou introduit un tableau de référence. C'est le
seul mécanisme qui rendait acceptable d'écrire sur les outils ailleurs que dans
leurs descriptions.

Il lit `bundles/`, versionné, et non `bases/`, ignoré par git. La différence est
tout sauf cosmétique : la CI part d'un checkout neuf, donc tant que la source
vivait hors du dépôt, ces tests se contentaient de `skip` — le garde-fou ne
tournait nulle part.

**Aucun nouvel écart assumé.** Les sept points ci-dessus sont des précisions
d'implémentation à l'intérieur de ce que la spec autorise, pas des déviations —
la section 5 reste à deux écarts.

---

## 6 ter. Décisions d'implémentation de `kb_search` multi-bases (§ 10.3)

La spec pré-cadrait deux contraintes non négociables (plafond global unique
réparti, résultats groupés par base) et laissait le reste à l'implémenteur.

| Point | Décision | Motif |
|---|---|---|
| Algorithme de répartition du plafond | Parts égales entre bases **encore actives** (qui n'ont pas épuisé leurs résultats), reliquat redistribué en boucle | Un partage figé (`max_results // n`) gâcherait du budget dès qu'une base a moins de résultats que sa part — la spec dit « réparti », pas « divisé à parts fixes ». `search_tool._allocate_quota`, testée isolément. |
| Coût du calcul de disponibilité par base | Un seul appel `run_search(base, ..., max_results)` par base, jamais deux | `run_search` scanne tout le corpus via ripgrep quel que soit `max_results` — celui-ci ne fait que trancher la liste triée en sortie. Demander `max_results` (le plafond global, forcément ≥ toute part finale) donne donc un pool de candidats suffisant pour la répartition, sans second passage ripgrep. |
| Nom unique (chaîne) vs. liste à un élément | Sortie **strictement identique** à l'existant (pas d'en-tête `## Base :`) | Non-régression explicite : `base: "nom"` reste l'appel majoritaire, sa sortie ne doit ni changer de forme ni casser un appelant qui la parse. Le groupage n'apparaît qu'à partir de deux bases interrogées. |
| Nom inconnu dans une liste | `UNKNOWN_BASE` immédiat, **avant** toute recherche — jamais un repli silencieux qui ignore le nom fautif | Cohérent avec le comportement déjà existant à un seul nom (§ 5, `unknown_base`) ; un repli silencieux masquerait une faute de frappe au lieu de la signaler. |
| `base: "*"` sans base enregistrée | Message informatif, pas `INVALID_INPUT` | `"*"` est une intention méta (« tout ce qui existe »), pas la désignation d'un nom précis : zéro base est un état du hub, pas une erreur d'appel. |
| Base sans résultat, en sortie groupée | Absente de la sortie (pas de groupe vide) | Une base vide n'ajoute aucune information ; la même logique s'applique déjà à un nom unique (« Aucun résultat », pas un bloc vide). |

**Aucun écart assumé.** Les deux contraintes pré-cadrées sont respectées à la
lettre ; les points ci-dessus sont des précisions d'implémentation à
l'intérieur de ce qu'elles laissaient ouvert — la section 5 reste à deux
écarts.

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
| `kb_search` multi-bases, plafond global réparti, résultats groupés (§ 10.3) | `test_base_liste_groupe_les_resultats_par_base`, `test_base_etoile_interroge_toutes_les_bases_enregistrees`, `test_base_liste_plafond_global_reparti_a_egalite`, `test_base_liste_reliquat_redistribue_a_l_autre_base`, `test_base_liste_nom_inconnu_leve_unknown_base`, `test_base_chaine_unique_reste_sans_entete_de_groupe`, `test_allocate_quota_*` |
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

### rév. 4.1 — amendement du premier retour d'usage

| Exigence de l'amendement | Test |
|---|---|
| B1 — filtres `id` / `submitted_by` / `status` / `limit` | `test_filtre_par_contributeur`, `test_filtre_par_statut`, `test_limit_borne_la_sortie_et_signale_le_reste`, `test_limit_hors_bornes_refuse` |
| B1 — `id` ou `submitted_by` obligatoire | `test_sans_filtre_refuse` |
| B1 — `NOT_FOUND` sur `id` introuvable | `test_id_introuvable_est_un_not_found` |
| B1 — incohérence status/emplacement signalée sans échouer | `test_incoherence_status_emplacement_signalee_sans_echouer` |
| B1 — frontmatter illisible ignoré et signalé | `test_frontmatter_illisible_ignore_et_signale` |
| B1 — plafond de sortie | `test_plafond_de_sortie_respecte` |
| B1 — confinement à `proposals/`, exception limitée à cet outil | `test_lien_symbolique_sortant_ignore`, `test_proposals_reste_exclu_des_autres_outils` |
| B1 — le corps n'est pas retourné | `test_le_corps_de_la_proposition_n_est_pas_retourne` |
| B2 — deuxième instance voit un bundle importé au premier `kb_list` | `test_kb_list_voit_une_base_importee_apres_le_demarrage` |
| B2 — deux `kb_list` en < 5 s = un seul scan | `test_deux_kb_list_rapproches_ne_scannent_qu_une_fois` |
| B2 — cooldown unique de 5 s, **un compteur par déclencheur** (rév. 4.2, § 5 bis) | `test_le_cooldown_est_bien_de_cinq_secondes`, `test_un_kb_list_ne_consomme_pas_le_rescan_d_unknown_base`, `test_deux_unknown_base_rapproches_ne_scannent_qu_une_fois` |
| B2 — `tools/list_changed` et description régénérée | `test_la_liste_changee_emet_tools_list_changed`, `test_description_de_kb_list_regeneree_apres_import` |
| B3 — heading de section par extrait, `(préambule)` inclus | `test_extrait_annote_du_heading_de_sa_section`, `test_extrait_avant_tout_heading_annote_preambule` |
| B3 — ancre = ligne touchée, pas début de fenêtre | `test_le_heading_suit_la_ligne_touchee_pas_le_debut_de_fenetre` |
| B3 — `kb_read` ciblé en un appel sur un document > seuil | `test_chainage_en_un_appel_sur_un_gros_document`, `test_le_libelle_se_rejoue_tel_quel_dans_kb_read` |
| B5 — bandeau `draft`, défaut `stable`, valeur inconnue | `test_gouvernance_brouillon_prefixee_d_un_bandeau`, `test_absence_de_frontmatter_vaut_stable`, `test_status_inconnu_traite_comme_stable` |
| B5 — signalement à la revue | `test_context_signale_une_gouvernance_en_brouillon` |
| B6 — bundle de dogfooding conforme | `test_le_bundle_de_dogfooding_est_conforme` |
| Bases meta — manifeste, gouvernance stable, conventions OKF | `tests/test_bases_meta.py` : `test_manifeste_valide_et_sans_avertissement`, `test_gouvernance_stable`, `test_sommaire_et_journal_presents`, `test_chaque_document_porte_type_et_version`, `test_le_sommaire_reference_tous_les_documents` |
| Bases meta — **anti-dérive** vis-à-vis du code | `test_aucun_outil_inexistant_n_est_cite`, `test_aucun_parametre_inexistant_n_est_attribue_a_un_outil`, `test_le_guide_ne_recopie_pas_la_reference_d_api` |
| Bases meta — découvrabilité par les `instructions` | `test_les_instructions_annoncent_les_bases_meta_deployees`, `test_une_base_meta_absente_n_est_pas_annoncee` |
| Bases meta — le guide déployé ne diverge pas de sa source | `test_le_guide_deploye_est_conforme_a_sa_source` |
| Bases livrées — dépôt git autonome, pas un sous-répertoire du hub | `test_la_base_deployee_est_un_depot_git_autonome` |
| Bases livrées — **déploiements concurrents**, publication atomique | `test_deux_deploiements_concurrents_ne_produisent_qu_une_base`, `test_aucun_chantier_ne_survit`, `test_un_chantier_en_cours_n_est_pas_decouvert` |
| Bases livrées — idempotence, non-écrasement, échec non bloquant | `test_deuxieme_appel_sans_effet`, `test_une_base_existante_n_est_jamais_ecrasee`, `test_echec_de_deploiement_non_bloquant` |
| Bases livrées — installation au démarrage et interrupteur | `test_le_serveur_deploie_au_demarrage`, `test_bootstrap_bundles_false_desactive_le_deploiement` |
| Bases livrées — **dépôt canonique cloné, jamais semé** | `test_une_base_avec_amont_est_clonee_pas_semee`, `test_un_clone_impossible_ne_seme_jamais`, `test_le_journal_dit_comment_rattraper` |
| **D4 — critère d'acceptation** : contribution → revue → verdict, sans git côté contributeur | `test_boucle_complete_sans_acces_git_du_contributeur`, `test_le_rejet_est_lisible_avec_son_motif` |

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
schéma ; authentification des contributeurs ; politique d'incrément de version ;
index de recherche dérivé ; revue d'import outillée ; synchronisation remote ;
multi-hub.

`kb_proposal_status` a quitté cette liste : livré par l'amendement rév. 4.1
(§ 5.7 de la spec).

`kb_search` multi-bases a quitté cette liste : livré (§ 6 ter), spec pré-cadrée
au § 10.3 appliquée à la lettre.

**Refusé, et il faut savoir pourquoi avant de le re-proposer.** La validation du
frontmatter d'une proposition contre `schema.yaml` est un contresens du modèle
d'affirmation sémantique : le schéma décrit le corpus, la mise en forme est le
travail du gestionnaire. Le rescan « partagé au niveau du hub » supposerait un
état partagé ou un démon, contraires au § 4.4 ; le besoin est couvert par le
re-scan implicite de `kb_list`.

**Le volet template du § B5 n'est pas dans ce dépôt.** `okf-bundle-template` est
un livrable distinct, publié sur GitHub et non monté dans le devcontainer ; la
modification qui lui revenait a été livrée en patch, appliquée en amont, puis
retirée d'ici. C'est la procédure à reprendre pour toute évolution du template.

**L'index de recherche dérivé mérite un mot** : la spec dit de ne l'ajouter que
si ripgrep devient *mesurablement* insuffisant. Mesure actuelle sur le corpus le
plus gros disponible (856 documents) : découverte + recherche en **0,15 s**. Rien
ne le justifie aujourd'hui.
