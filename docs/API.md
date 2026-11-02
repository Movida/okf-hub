# Référence d'API — outils MCP `kb_*`

Contrat exact des huit outils du noyau. Vérifié contre les schémas réels du code
(`src/okf_hub/tools/*.py`, constante `SCHEMA`) : si ce document et le code
divergent, **le code fait foi** et ce fichier est en dette.

Pour la justification de chaque comportement, voir la spécification :
[`SPEC-okf-bundle-hub-v0.md`](SPEC-okf-bundle-hub-v0.md) § 5.

---

## Conventions transverses

### Le paramètre `base`

Toujours le champ **`name` du manifeste**, jamais le nom du répertoire dans
`bases/`. Les deux diffèrent dès qu'un clone est renommé. `kb_list` donne la
correspondance.

### Chemins

Relatifs à `corpus-dir`, séparateur `/`. Un chemin absolu, un `..` ou un lien
symbolique sortant du corpus sont refusés (`NOT_FOUND`).

### Erreurs

Une erreur métier retourne un résultat MCP avec `isError: true`, dont le texte
suit exactement :

```
ERROR: <CODE>: <message>
```

| Code | Signification | Que faire |
|---|---|---|
| `UNKNOWN_BASE` | Base inconnue. Le message **liste les bases valides**. | Utiliser un nom de la liste. Un re-scan silencieux a déjà été tenté. |
| `NOT_FOUND` | Document, section ou proposition introuvable. Pour une section, le message liste les headings disponibles. | Corriger le chemin ou la section. |
| `INVALID_INPUT` | Paramètre manquant ou mal typé, hors bornes, énumération non respectée, regex invalide, retour à la ligne interdit. Pour un paramètre manquant, le message **nomme la cause** (absent, `null`, vide, mauvais type) et **liste les clés reçues** — jamais les valeurs. | Corriger l'entrée. Ne pas réessayer à l'identique. Si les clés reçues ne sont pas celles envoyées, le défaut est côté client : le signaler à l'opérateur avec le message complet. |
| `BASE_BUSY` | Verrou d'écriture non acquis en 15 s. | **Réessayer plus tard.** Ce n'est pas une défaillance. |
| `IO_ERROR` | Défaillance réelle : ripgrep absent, git en échec, disque. | Signaler à l'opérateur. Un retry n'aidera pas. |

Le SDK MCP ne valide **pas** les arguments contre `inputSchema` : un paramètre
manquant ou mal typé atteint l'outil, qui le rend en `INVALID_INPUT` (`isError`),
avec la liste des clés reçues. Exemple, pour un appel arrivé sans aucun argument :

```
ERROR: INVALID_INPUT: paramètre 'base' requis (chaîne non vide) — paramètre 'base' absent ; aucun paramètre reçu
```

En revanche, un message **mal encodé en JSON** (par exemple un champ `content`
dont un guillemet, un antislash ou un saut de ligne n'est pas échappé par le
client) est rejeté par le **transport** avant d'atteindre le moindre outil : le
client reçoit une erreur JSON-RPC standard, pas un `isError`. Ce cas n'apparaît
donc dans aucun code `kb_*` ci-dessus ; côté serveur, le hub le journalise
désormais (`transport : message client rejeté avant tout outil…`). Un `kb_propose`
qui « échoue » sans laisser de trace ni de verdict relève de ce cas, pas d'une
validation d'outil : le contenu à corriger est l'échappement JSON côté client,
jamais la contribution elle-même (`ARCHITECTURE.md` § 5 ter).

### Plafond de sortie

`kb_search`, `kb_list`, `kb_proposal_status` et `kb_find` plafonnent leur sortie à
**~4 000 tokens**
(approximation caractères/4, soit 16 000 caractères). La troncature s'arrête sur
un résultat entier — jamais au milieu — et est signalée par une ligne
`[résultats tronqués, N élément(s) omis — …]`.

`kb_read` n'est pas plafonné : c'est le mode « table des headings » qui joue ce
rôle. `kb_governance` ne l'est pas non plus, conformément à la spec.

### Descriptions dynamiques

Les descriptions de `kb_list`, `kb_search`, `kb_read`, `kb_find`, `kb_governance`,
`kb_propose` et `kb_proposal_status` **énumèrent les bases connues** avec leur titre et leur objet, et
sont recalculées à chaque `tools/list`. C'est ce qui permet à une session de
router sa requête vers la bonne base **sans appel préalable**.

### Annotations

Chaque outil porte des annotations MCP, d'après lesquelles un client classe les
outils et règle leur approbation (Claude Desktop : lecture seule, écriture,
autres). Tous s'annoncent en lecture seule (`readOnlyHint: true`), sauf
`kb_propose` : écriture (`readOnlyHint: false`), mais non destructive
(`destructiveHint: false`), puisqu'elle n'ajoute qu'un fichier dans
`proposals/pending/`. Aucun n'est ouvert sur l'extérieur
(`openWorldHint: false`). `tests/test_annotations.py` garde cette répartition.

---

## kb_list

Liste les bases disponibles.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `include_pending_concerns` | `boolean` | `false` | Ajoute la liste `(id, type, concerns)` des propositions en attente. |

**Sortie** — un bloc par base :

```
## solution-editeur-x — Base solution Éditeur X
Documentation d'exploitation de la solution X : configuration, incidents connus.
version : 1.0.0
documents : 42 | propositions en attente : 3 (la plus ancienne : 2026-10-19)
```

La date est celle de la plus ancienne proposition en attente, d'après son
`submitted-at`, en UTC (spec rév. 4.7) : le retard de la revue se lit sans autre
appel. Sans proposition en attente, la ligne s'arrête au nombre ; une
proposition sans date lisible est comptée, pas datée.

Avec `include_pending_concerns`, un second bloc par base suit :

```
### propositions en attente — solution-editeur-x
- prop-2026-06-14-a3f2 (correction) — procédure de reconnexion SSO
```

Les listes de `concerns` sont **tronquées en priorité** : les résumés de bases
sont toujours émis en entier, les concerns seulement si le budget le permet.
La troncature le dit, avec le nombre de listes omises et les deux voies qui
existent pour aller plus loin — `kb_list` n'a pas de paramètre de base :

```
[résultats tronqués : 1 liste(s) de propositions en attente omise(s) — pour savoir si un sujet est déjà proposé, kb_search sur ce sujet avec include_pending: true ; pour ce qu'a déposé un contributeur, kb_proposal_status avec submitted_by]
```

Sans aucune base enregistrée, la sortie explique comment en importer une.

**Quand l'appeler** — au début d'une session, pour savoir quoi interroger ; et
avec `include_pending_concerns` **avant `kb_propose`**, pour ne pas soumettre un
doublon. Pour un sujet précis, `kb_search` avec `include_pending` le vérifie
plus sûrement, sur le texte des propositions et pas seulement leur objet. Ce
n'est pas obligatoire : la déduplication est le travail du gestionnaire, pas le
vôtre.

---

## kb_search

Recherche plein texte dans le corpus d'une ou plusieurs bases, via ripgrep.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `base` | `string` \| `string[]` | **requis** | Nom de la base, liste de noms, ou `"*"` pour toutes les bases enregistrées. Voir « Multi-bases » ci-dessous. |
| `query` | `string` | **requis** | Termes, ou expression régulière en mode `regex`. |
| `mode` | `"keyword"` \| `"regex"` | `"keyword"` | Voir ci-dessous. |
| `max_results` | `integer` 1–25 | `8` | Hors bornes → `INVALID_INPUT`. Plafond **global** : voir « Multi-bases ». |
| `include_pending` | `boolean` | `false` | Cherche aussi dans les propositions en attente, après le corpus, résultats marqués « non intégré ». Voir « Propositions en attente » ci-dessous. |

**Mode `keyword`** — les termes sont séparés par des espaces et traités comme
des **littéraux** (pas de regex : `3.2(beta)` cherche bien `3.2(beta)`).

1. **ET strict d'abord** : seuls les documents touchant *tous* les termes,
   insensible à la casse.
2. **Si zéro résultat, repli automatique en OU**, classé par nombre de termes
   touchés puis densité, avec la mention explicite :
   `[aucun document ne contient tous les termes — résultats partiels]`.

**Mode `regex`** — dialecte ripgrep (syntaxe Rust regex). Une expression
invalide remonte `INVALID_INPUT` **avec le message de ripgrep tel quel**.

**Sortie** — par résultat :

```
### procedures/sso.md — Procédure de reconnexion SSO
frontmatter : {title: Procédure de reconnexion SSO, tags: [sso], last-verified: 2026-01-15}
  L12-16 § reconnexion
           | ## Reconnexion
           | 
           | Cliquer sur « réauthentifier » dans le menu profil.
```

- `path` relatif au corpus, puis le titre (frontmatter `title`, sinon premier `#`) ;
- frontmatter **limité à `title`, dates et `tags`** — le reste n'est pas exposé ;
- extraits : ligne touchée ± 2 lignes, **3 au maximum par document**, fenêtres
  qui se recouvrent fusionnées ;
- après `§`, le **heading de la section contenant la ligne touchée**, sous sa
  forme normalisée — ou `(préambule)` si la ligne précède tout heading.

**Le `§` est fait pour être recopié.** C'est exactement ce que `kb_read` attend
dans `section` :

```
kb_search base=… query="réauthentifier"      → "  L12-16 § reconnexion"
kb_read   base=… path=procedures/sso.md section="reconnexion"
```

Sur un document dépassant `read-toc-threshold`, cela économise l'aller-retour par
la table des headings. Le heading retenu suit la **ligne touchée**, pas le début
de la fenêtre de contexte : celle-ci déborde de deux lignes et peut mordre sur la
section précédente.

**Déclassement des sommaires** — `index.md` et `log.md` (noms réservés OKF)
passent **derrière tout autre document**, et la sortie le signale. Ce sont des
tables de matières ; elles matchent beaucoup et n'apprennent rien. Elles
ressortent quand rien d'autre ne correspond. *Écart assumé au § 5.2, voir
[`ARCHITECTURE.md`](ARCHITECTURE.md).*

**Ce que kb_search n'est pas** — un ripgrep est un motif sur le texte brut. Il
sert à **trouver**, jamais à **compter** : un champ de frontmatter ou une section
cités entre backticks dans un document de conventions seront comptés à tort.

**Multi-bases (§ 10.3).** `base` accepte, en plus d'un nom unique :

- une **liste de noms** — `base: ["solution-editeur-x", "okf-hub-guide"]` —
  chacun doit exister, sinon `UNKNOWN_BASE` (comme pour un nom unique) ; aucune
  recherche n'est lancée si l'un d'eux est invalide. Les doublons sont
  silencieusement dédoublonnés.
- `base: "*"` — toutes les bases actuellement enregistrées. Sans aucune base
  enregistrée, la sortie l'indique au lieu d'échouer.

`max_results` reste un **plafond de sortie global**, jamais un plafond par
base : avec deux bases interrogées et `max_results: 8`, le total des résultats
rendus est au plus 8, pas 16. Le budget est **réparti à parts égales** entre
les bases interrogées ; une base qui a moins de résultats que sa part cède son
reliquat aux autres plutôt que de le perdre — la répartition finale peut donc
être inégale si les bases n'ont pas toutes assez de matches.

**Relever `max_results` ne fait jamais réapparaître un résultat masqué par le
plafond de caractères (~4000 tokens, § « Plafond de sortie » ci-dessus).** Les
résultats sont ajoutés à la sortie dans l'ordre du classement, et l'arrêt au
premier résultat qui dépasse le budget restant est définitif — tout résultat
suivant est omis, quelle que soit sa taille. Les résultats affichés sont donc
toujours les mieux classés (un **préfixe** du classement), jamais un mélange
qui aurait sauté un gros résultat pertinent pour faire de la place à un petit
moins pertinent. `max_results` ne change que la longueur de la liste de
candidats *derrière* ce plafond — ni le monter ni le descendre ne change les
résultats réellement montrés une fois la troncature atteinte. Le message
`[résultats tronqués, N élément(s) omis — …]` le rappelle. **Seule une requête
reformulée** change le classement, donc ce qui arrive en tête.

Avec plus d'une base, la sortie est **groupée par base**, chacune sous son
propre en-tête (même format qu'à une seule base) :

```
2 résultat(s) dans 2 base(s) pour : réauthentifier

## Base : solution-editeur-x
1 résultat(s) dans 'solution-editeur-x' pour : réauthentifier
### procedures/sso.md — Procédure de reconnexion SSO
  L12-16 § reconnexion
           | ...

## Base : okf-hub-guide
1 résultat(s) dans 'okf-hub-guide' pour : réauthentifier
### ...
```

Une base sans aucun résultat n'apparaît pas dans la sortie groupée. Avec un
nom unique (chaîne, pas liste ni `"*"` à plusieurs bases), la sortie reste
strictement celle d'aujourd'hui — sans en-tête de groupe.

**Propositions en attente — `include_pending` (spec rév. 4.7).** Ce que des
sessions ont déposé par `kb_propose` et qu'aucune revue n'a encore intégré.
Sans l'option, la sortie est exactement celle décrite plus haut et rien n'est
lu dans `proposals/`. Avec elle :

- la recherche est la même — mode, ET strict puis repli en OU, classement —,
  sur `proposals/pending/` de chaque base visée, et seulement là ;
- **le corpus passe d'abord.** `max_results` reste un plafond global : les
  propositions ne prennent que ce que le corpus en laisse. Dans la sortie, elles
  viennent après tous les résultats du corpus, de toutes les bases, sous le même
  plafond de caractères. La part « corpus » de la sortie est donc celle qu'on
  aurait eue sans l'option ;
- chaque résultat est **marqué « non intégré »**, avec l'`id`, le `type`, le
  `concerns` et la date de dépôt. Les extraits ne portent que sur le corps de
  la proposition — une occurrence dans son frontmatter est signalée par le nom
  du champ — et n'ont pas de `§` : `kb_read` ne lit pas les propositions ;
- par base, le nombre de propositions qui répondent est toujours donné, même
  quand le plafond n'en laisse lister aucune — avec le moyen d'en voir plus.

```
1 résultat(s) dans 'solution-editeur-x' pour : réauthentifier
### procedures/sso.md — Procédure de reconnexion SSO
  L12-16 § reconnexion
           | ...

[NON INTÉGRÉ — ce qui suit vient de propositions en attente : des affirmations déposées par des sessions, qu'aucune revue n'a intégrées. Hors du corpus, à vérifier avant de s'y fier ; kb_read ne les lit pas, kb_proposal_status donne leur état par id.]
1 proposition(s) en attente dans 'solution-editeur-x' pour : réauthentifier
### prop-2026-10-26-a3f2 — non intégré, en attente de revue
type : correction | concerns : bouton réauthentifier déplacé | soumise : 2026-10-26T09:12:00Z
trouvé dans le frontmatter : concerns
  L11-13
           |
           | Depuis la 3.2, le bouton réauthentifier est dans le menu profil.
```

Avec plusieurs bases, chaque base qui a des propositions qui répondent a son
groupe `## Base : <nom> — non intégré`, après tous les groupes du corpus. Si
le corpus ne répond pas, la première ligne le dit (`Aucun résultat dans le
corpus de la base …`) ; si rien ne répond nulle part, une seule ligne, avec le
nombre de propositions examinées. Si des propositions existent sans qu'aucune
réponde : `[propositions en attente : aucune ne répond, sur N examinée(s)]`.

**Quand l'activer** — avant `kb_propose`, pour savoir si un sujet est déjà
proposé ; dans une enquête, pour ne pas refaire ce qu'une session a déjà déposé.
Une proposition en attente est une **affirmation à vérifier**, pas une
connaissance : ne pas la citer comme acquise, et lire son état par
`kb_proposal_status` avant de s'y fier.

---

## kb_read

Lit un document, ou une seule de ses sections.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `base` | `string` | **requis** | Nom de la base. |
| `path` | `string` | **requis** | Chemin relatif au corpus, ou lien interbase `<base>:/<chemin>` (voir ci-dessous). |
| `section` | `string` | — | Titre du heading à extraire. |
| `force` | `boolean` | `false` | Retourne le document entier même s'il est volumineux. |

**Lien interbase dans `path`.** Le corps des documents cite parfois une autre
base par un lien littéral `[libellé](produit-x:/chemin.md)`. `kb_read`
reconnaît cette forme quand elle est recopiée telle quelle dans `path` : si le
préfixe avant `:/` correspond au nom d'une base enregistrée, c'est **cette
base qui est lue**, `base` étant alors ignoré pour l'appel. Un préfixe qui ne
correspond à aucune base connue est traité comme un chemin littéral (donc,
normalement, `NOT_FOUND`) — pas d'erreur `UNKNOWN_BASE` surprenante sur un
faux positif.

```
kb_read base=referentiel-x path="installation-x:/environnement/base-externe.md"
→ lit environnement/base-externe.md dans la base installation-x, pas referentiel-x
```

**Trois comportements, dans cet ordre :**

1. **`section` fourni** → la section seule, du heading jusqu'au prochain heading
   de niveau inférieur ou égal.
2. **Document > `read-toc-threshold`** (8192 octets par défaut) **et ni
   `section` ni `force`** → frontmatter + **table des headings** avec tailles
   approximatives, au lieu du contenu.
3. **Sinon** → le document complet, frontmatter inclus.

**Correspondance de section** — insensible à la casse, après normalisation
identique du heading et du paramètre : liens `[texte](url)` → `texte`, images
→ texte alternatif, backticks supprimés, emphase `*`/`**`/`~~` supprimée, `_`
supprimé **sauf en position intra-mot** (`kb_read` reste `kb_read`), espaces
réduits.

Donc `section: "procedure de reconnexion sso"` trouve
`## Procédure de \`reconnexion\` **SSO**`.

**Headings dupliqués** — la **première occurrence** est retournée, avec la
mention `[N autres sections portent ce titre]`.

**Section introuvable** — `NOT_FOUND`, et le message **liste tous les headings
du document** : un second appel suffit à corriger le tir.

**Sécurité** — résolution canonique du chemin puis vérification d'inclusion
stricte dans `corpus-dir`. `..`, chemins absolus et liens symboliques sortants
sont refusés.

**Chemin hors corpus mais réel dans le bundle.** Si le chemin demandé n'existe
pas sous `corpus-dir`, mais qu'un fichier existe au même chemin relatif à la
**racine du bundle** (typiquement un champ de frontmatter comme `source_xml`,
relatif au bundle et pas à `corpus-dir`, recopié tel quel dans `path`), le
`NOT_FOUND` le signale explicitement au lieu d'un générique « introuvable » —
pour couper court à une tentative de lecture qui ne peut pas aboutir. Ce
diagnostic supplémentaire ne s'applique **jamais** aux tentatives d'évasion
(`..`, symlink sortant) : leur message reste inchangé, opaque — un chemin qui
sort de `corpus-dir` par ce biais garde exactement le refus d'aujourd'hui.

**Chemin préfixé par `corpus-dir`.** Cas particulier du précédent : le fichier
trouvé à partir de la racine du bundle est le document du corpus lui-même, le
chemin répétant `corpus-dir` en tête — typiquement recopié d'une arborescence
de `GOVERNANCE.md` qui montre le dépôt entier. Le `NOT_FOUND` donne alors le
chemin à passer, vérifié par la même résolution confinée :

```
kb_read base=produit-x path="knowledge/procedures/sso.md"
→ ERROR: NOT_FOUND: document 'knowledge/procedures/sso.md' introuvable dans la base 'produit-x' : les chemins sont relatifs à corpus-dir ('knowledge'), et celui-ci le répète en tête. Le document existe : path='procedures/sso.md'.
```

Un chemin préfixé vers un document absent, ou vers un fichier exclu du corpus,
reçoit le message générique.

---

## kb_find

Trouve les documents qui répondent à des critères structurés, ou compte leurs
valeurs (spec § 5.8). Là où `kb_search` dit *où l'on parle* d'un sujet,
`kb_find` dit *ce qui existe* — et **un total de zéro est une réponse** : il
établit une absence en un appel.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `base` | `string` ou `string[]` | **requis** | Comme `kb_search` : un nom, une liste, ou `"*"`. |
| `type` | `string` | — | Valeur exacte du champ `type`. |
| `tags` | `string[]` | — | Tous requis, forme exacte (`env:prod`). |
| `fields` | `{champ: string \| boolean}` | — | Valeur exacte (appartenance si le champ est une liste) ; `true` : présent ; `false` : absent. |
| `section` | `string` | — | Le document a une section de ce titre (même normalisation que `kb_read`), ou d'un titre que le profil range sous ce rôle (`warnings`, `observations`, `upstream`, `procedure`). |
| `without_section` | `string` | — | L'inverse. |
| `under` | `string` | — | Sous-chemin du corpus, lu comme un répertoire : `exploitation` ne couvre pas `exploitation-ancien/`. |
| `links_to` | `string` | — | Documents qui citent `<base>:/<chemin>` ; un chemin seul si une seule base est interrogée. |
| `older_than_months` | `integer` | — | 1 à 600 : fraîcheur plus ancienne, les plus anciens d'abord. |
| `count_by` | `string` | — | `type`, `tags`, `section`, une facette déclarée par le profil, ou un champ : effectifs au lieu de la liste. |
| `query` | `string` | — | Le texte contient **tous** ces termes (casse ignorée). Un filtre : sans le repli en OU de `kb_search`. |
| `links_to_depth` | `integer` | 1 | 1 à 3, avec `links_to` : les documents qui citent ceux qui citent la cible… Sommaires et journaux ne propagent pas. |
| `upstream_changed` | `boolean` | — | Documents qui citent un document d'une autre base modifié (commit) après leur propre dernier commit, ou dernière vérification. |
| `sort` | `string` | voir ci-dessous | `path`, `relevance` (avec `query`), `oldest`, `most_cited`, `reaudit` (âge en mois × (1 + citations), doublé si l'amont a changé). |
| `max_results` | `integer` | 50 | 1 à 200, toutes bases confondues, réparti comme `kb_search`. |

Les critères se cumulent (ET). `index.md` et `log.md` ne sont ni listés ni
comptés : ce sont des sommaires et des journaux, pas des concepts (OKF § 3.1).
Tri par défaut : par chemin (le degré d'abord sous `links_to`), les plus anciens
d'abord sous `older_than_months`, par pertinence sous `query`.

**Sortie — liste**

```
3 document(s) dans 'installation-x' — critères : type=Procédure ; section=warnings
- exploitation/reprise.md — Reprise après coupure [Procédure]
- exploitation/sauvegarde.md — Sauvegarde nocturne [Procédure]
- support/arrivee.md — Arrivée d'un collaborateur [Procédure]
```

Le total est **toujours exact**, même quand la liste est tronquée
(`[liste incomplète : N document(s) non affiché(s) — les totaux ci-dessus sont exacts …]`).
Sous `older_than_months`, chaque ligne porte la date et son origine
(`verified`, `generated`, `sources`, `commit`, `fichier`) ; sous `links_to`, un
lien venu d'une section de rôle `upstream` est marqué `— amont`. Selon les
critères et le tri s'ajoutent `— degré N`, `— cité par N`, `— priorité N` et
`— amont modifié : <base>:/<chemin> le <date>`.

**Zéro, et pas une faute de frappe**

```
0 document(s) dans 'installation-x' — critères : type=Procedure
[aucun document de type « Procedure » ; types présents : Procédure (14), Référence (9), …]
```

Les types présents ne sont rappelés que si le type ne se trouve dans **aucune**
des bases interrogées.

**Sortie — effectifs**

```
38 document(s) dans 'installation-x', par type — critères : aucun critère (tous les documents)
- Procédure : 14
- Référence : 9
- Décision : 0
```

Une valeur déclarée par le profil s'affiche même à effectif nul ; une valeur
observée hors du vocabulaire déclaré porte `(hors vocabulaire déclaré)`.

**Sortie structurée.** L'outil déclare un `outputSchema` : le résultat porte
aussi, en `structuredContent`, les mêmes documents et effectifs
(`{criteria, count_by, bases: [{base, total, documents | counts}]}` ; un document
porte au besoin `upstream_changed`, `cited_by`, `reaudit_score`, `depth`). C'est la
voie des programmes — une autre interface lit les données sans analyser le
texte. Le client MCP officiel valide ces données contre le schéma.

**Erreurs** — `INVALID_INPUT` (critère mal formé ; `links_to` sans préfixe sur
plusieurs bases), `NOT_FOUND` (`links_to` vers un document absent),
`UNKNOWN_BASE` (base, ou préfixe de `links_to`, inconnu).

---

## kb_governance

Retourne les règles d'une base.

| Paramètre | Type | Rôle |
|---|---|---|
| `base` | `string` | **requis** |

**Sortie** — le contenu intégral de `GOVERNANCE.md`, suivi de `schema.yaml` dans
un bloc de code s'il est déclaré, ou de `[aucun schema.yaml déclaré pour cette
base]`.

**Bandeau de gouvernance en brouillon** — si `GOVERNANCE.md` porte
`status: draft` dans son frontmatter, la sortie s'ouvre sur :

```
[GOUVERNANCE EN BROUILLON — les règles peuvent évoluer, les propositions restent acceptées]
```

Rien n'est bloqué pour autant : c'est un avertissement sur la maturité des
règles, pas une machine à états. Absent ou inconnu, le statut vaut `stable`.

**Quand l'appeler** — **avant `kb_propose`**, pour savoir ce que la base attend
d'une proposition (sources exigées, seuil de confiance, périmètre). Et
systématiquement au début d'une revue : c'est le contexte du gestionnaire.

---

## kb_propose

Dépose une proposition. **Seul outil d'écriture du noyau**, confiné à
`proposals/pending/`. Il ne touche jamais au corpus.

| Paramètre | Type | Contrainte |
|---|---|---|
| `base` | `string` | **requis** |
| `type` | `"observation"` \| `"correction"` \| `"addition"` \| `"question"` | **requis** |
| `concerns` | `string` | **requis**, ≤ 200 car., **sans `\n` ni `\r`** |
| `content` | `string` | **requis**, ≤ 16 Ko (16384 octets UTF-8) |
| `sources` | `string[]` | **requis**, 1 à 20 entrées, ≤ 300 car. chacune, **sans `\n` ni `\r`** |
| `confidence` | `"high"` \| `"medium"` \| `"low"` | **requis** |
| `submitted_by` | `string` | **requis**, ≤ 100 car., **sans `\n` ni `\r`** |

**Sémantique de `type`**

| Valeur | Sens |
|---|---|
| `observation` | Fait constaté, sans présumer d'un document existant. |
| `correction` | Contredit un contenu actuel. |
| `addition` | Complète un sujet déjà couvert. |
| `question` | Lacune identifiée, sans réponse fournie. Le gestionnaire enquête ou rejette. |

**Pourquoi les retours à la ligne sont refusés** — `concerns`, `submitted_by` et
`sources` sont injectés dans le sujet et les trailers du message de commit. Un
`\n` permettrait de forger un faux `Reviewed-By:` et de corrompre les invariants
d'audit basés sur `git log --grep`. Le refus est **normatif**, pas cosmétique.

`content` n'a aucune restriction de caractères : il vit dans le corps du fichier,
après un frontmatter sérialisé par bibliothèque YAML. Un `---` dans le contenu ne
casse rien.

**Convention pour `submitted_by`** — convention d'acteur OKF § 7 :
`human:<id>`, `<agent>/<version>` (ex. `claude-code/opus-5`), `process:<id>`.
Le champ est **déclaratif et non authentifié en v0** : il ne doit peser dans
aucune décision d'intégration.

**Sortie**

```
Proposition déposée.
id : prop-2026-08-30-a3f2
chemin : proposals/pending/prop-2026-08-30-a3f2.md
base : solution-editeur-x

Elle est en attente de revue par le gestionnaire de la base.
Le corpus n'a pas été modifié.
```

**Effets de bord** — sous verrou exclusif de la base : création de
`proposals/pending|accepted|rejected` avec `.gitkeep` si absents, ajout de
`.okf-hub.lock` au `.git/info/exclude`, écriture atomique du fichier, et **un
commit ne contenant que ce fichier** :

```
proposal: prop-2026-08-30-a3f2 (correction) — procédure de reconnexion SSO

Submitted-By: human:morva
```

**Relire le verdict** — `kb_proposal_status` avec l'`id` retourné ici. La sortie
de `kb_propose` rappelle l'appel exact. Seul le **corps** d'une proposition
résolue reste hors MCP : il se relit par accès git direct dans
`proposals/accepted|rejected/`.

**Une proposition, une base** — le gestionnaire n'intègre que la part qui relève
du périmètre de sa base, et ne déplace rien vers une autre : chaque part mal
placée coûte un rejet, un redépôt et une seconde revue. La description de
l'outil le dit au moment du dépôt : lire la section « Périmètre » de
`kb_governance` pour chaque base candidate, et scinder un constat qui en couvre
plusieurs (la règle générale dans l'une, sa conséquence ou le cas particulier
dans l'autre) en une proposition par base, chacune citant l'autre dans
`sources`. Motif : environ une proposition résolue sur dix a dû être déplacée,
presque toujours parce qu'elle mêlait les parts de deux bases (relevé du
06/10/2026).

**`schema.yaml` ne s'applique pas ici** — il décrit le frontmatter du **corpus**,
pas celui des propositions. Ne cherchez pas à vous y conformer : soumettez
l'information, sa mise en forme conforme au schéma relève du gestionnaire à
l'intégration. Les champs de cet outil sont le seul format requis.

---

## kb_proposal_status

Consulte l'état et la résolution des propositions. **Lecture pure** : aucun
verrou, aucun état, git reste canonique.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `base` | `string` | **requis** | Nom de la base. |
| `id` | `string` | — | Id exact d'une proposition, tel que retourné par `kb_propose`. |
| `submitted_by` | `string` | — | Contributeur déclaré. Correspondance exacte, casse ignorée. |
| `status` | `"pending"` \| `"accepted"` \| `"rejected"` | — | Restreint à ce statut. |
| `limit` | `integer` 1–50 | `20` | Les plus récentes d'abord. |

**Au moins un de `id` ou `submitted_by` est requis** — sinon `INVALID_INPUT`.
Sans filtre, l'outil déverserait `proposals/` en entier. `status` et `limit` ne
font que raffiner.

**Sortie** — par proposition :

```
2 proposition(s) dans 'solution-editeur-x' pour : submitted_by=human:morva
### prop-2026-08-30-a3f2 — accepted
type : correction | concerns : procédure de reconnexion SSO
soumise : 2026-08-30T09:12:00Z par human:morva
résolue : 2026-08-31T14:02:11Z — accepted
integrated-into : procedures/sso.md  (lisibles via kb_read)
### prop-2026-08-29-b81c — rejected
type : observation | concerns : bouton déplacé
soumise : 2026-08-29T16:40:00Z par human:morva
résolue : 2026-08-31T14:02:11Z — rejected
rejection-reason : doublon de prop-2026-08-30-a3f2, moins bien sourcé
```

Tri par `submitted-at` **décroissant** ; une proposition sans date passe en fin
de liste. Le **corps** n'est pas retourné : suivez `integrated-into` avec
`kb_read` pour lire ce qui a été intégré.

**Le statut vient de l'emplacement du fichier**, qui fait foi (§ 6.2). Le champ
`status` du frontmatter n'est qu'affiché. En cas de divergence, la ligne
`[incohérence status/emplacement : … — l'emplacement fait foi]` est ajoutée et
l'appel réussit quand même.

**Cas limites**

| Situation | Comportement |
|---|---|
| `id` introuvable dans les trois répertoires | `NOT_FOUND` |
| `submitted_by` sans aucune proposition | Résultat vide, **pas** une erreur |
| Frontmatter illisible ou absent | Fichier ignoré, `[N fichier(s) illisible(s) ignoré(s)]`, journalisé |
| Plus de résultats que `limit` | `[N plus ancienne(s) non listée(s) — augmentez limit]` |

**`submitted_by` n'est pas authentifié** (§ 8). Le filtre retrouve les
propositions **déclarées** sous ce nom, sans garantie d'identité.

**Confinement** — cet outil est l'une des **deux exceptions** à la liste
d'exclusions transverse du § 5.2, qui retire `proposals/` des lectures ;
l'autre est `kb_search` avec `include_pending`, qui ne lit que `pending/`
(rév. 4.7). Toutes deux en lecture seule, par la même liste confinée : un lien
symbolique déposé dans `pending/` et pointant hors du bundle est ignoré, comme
pour `kb_read`.

---

## kb_hub_rescan

Relance la découverte des bases. Aucun paramètre.

**Sortie**

```
Découverte terminée : 2 base(s) enregistrée(s).
ajoutées (1) : produit-x
retirées (0) : —
inchangées (1) : okf-hub-feedback

Bundles invalides (ignorés) :
- brouillon : champ obligatoire manquant : governance.rules

Collisions de name (premier en ordre lexicographique retenu) :
- 'doublon' : 'aaa-clone' retenu, 'zzz-clone' ignoré

Avertissements :
- [produit-x] bundle-spec '9.9' différent de la version supportée '0.1' — chargement tenté et réussi
```

**PORTÉE MONO-INSTANCE.** Le rescan n'affecte que la session qui l'appelle —
comme toute découverte sur ce hub : chaque client MCP a son instance et son
registre, il n'y a ni état partagé ni démon (§ 4.4).

**Vous n'avez généralement pas besoin de l'appeler.** Deux déclencheurs couvrent
déjà le besoin, chacun sous un **cooldown de 5 s par instance** :

- une erreur `UNKNOWN_BASE` déclenche un re-scan silencieux puis retente l'appel ;
- **tout `kb_list` déclenche la découverte** avant de répondre : une base
  importée après le démarrage d'une session lui devient donc visible dès qu'elle
  liste, sans rescan explicite ni redémarrage.

Leurs cooldowns sont comptés séparément : un `kb_list` ne prive pas l'appel
suivant du re-scan compensatoire sur `UNKNOWN_BASE`.

Ce qui reste propre à `kb_hub_rescan` : le **rapport** ci-dessus — bundles
rejetés avec leur motif, collisions de `name`, avertissements de compatibilité,
et, si une base au moins déclare `cites` (spec § 3.3), les avertissements du
graphe des bases :

```
Dépendances entre bases (cites) :
- cycle dans les cites déclarés : produit-x → installation-x → produit-x
- installation-x cite referentiel-x (12 référence(s)) sans le déclarer dans cites
```

C'est un outil de diagnostic d'import, pas de rafraîchissement.

---

## Séquences typiques

**Répondre à une question depuis les bases**

```
kb_list                                  → quelle base ?  (souvent inutile :
                                            les descriptions d'outils listent
                                            déjà les bases)
kb_search  base=… query="termes"         → quels documents ?
kb_read    base=… path=… section=…       → le contenu utile, pas le fichier entier
```

**Établir qu'une chose existe, ou non**

```
kb_find  base=… count_by=type                 → quels types existent ici ?
kb_find  base=… type=Procédure under=…        → total exact ; zéro est une réponse
kb_find  base=… links_to="produit-x:/…"       → qui, chez nous, décline ce concept ?
kb_find  base=… query="sauvegarde" type=Procédure  → texte et critères en un appel
kb_find  base=… upstream_changed=true         → ce qui est à revérifier, l'amont ayant changé
kb_find  base=… sort="reaudit"                → par où commencer un ré-audit
```

**Contribuer un fait nouveau**

```
kb_governance base=…                      → ce que la base exige, et son
                                            périmètre — pour chaque base
                                            candidate ; un constat à cheval
                                            se scinde, une proposition par base
kb_search     base=… query="sujet" include_pending=true
                                          → que dit le corpus aujourd'hui, et
                                            quelqu'un l'a-t-il déjà proposé ?
kb_propose    base=… type=correction …    → dépôt. Le corpus n'est pas modifié.
                                            → note l'`id` retourné.
```

**Relire le verdict d'une proposition** — plus tard, éventuellement dans une
autre session :

```
kb_proposal_status base=… id=prop-…            → pending / accepted / rejected
kb_read            base=… path=<integrated-into>  → ce qui a réellement été écrit
```

Aucun accès git n'est nécessaire côté contributeur pour cette boucle.

**Ce qu'une session consommatrice ne fait jamais** — écrire dans le corpus,
`git commit` sur un dépôt de base, ou considérer qu'une proposition déposée est
acquise. Elle ne l'est qu'après revue humaine.

---

## Côté gestionnaire — `okf-review`

Le rôle gestionnaire n'utilise pas les outils MCP pour écrire : il passe par le
moteur `bin/okf-review`, qui gère le verrou, les trailers et le commit atomique.
Procédure complète dans [`../skills/kb-review/SKILL.md`](../skills/kb-review/SKILL.md).

```sh
okf-review reconcile <base> [--apply]        # étape 0, rattrapage des crashs
okf-review context   <base>                  # golden rules + schéma + corpus
okf-review inventory <base> [--full]         # propositions en attente
okf-review resolve   <base> --plan p.json [--dry-run]
okf-review resolve   <base> --plan 01.json --plan 02.json --dry-run   # cascade
```

`--dry-run` passe par le même calcul que l'exécution (`review.verify_plans`),
sans écrire : un id absent de `pending/`, une section introuvable ou une fusion
de frontmatter refusée y échouent comme à l'exécution. Il avertit, sans échouer,
d'une section présente plusieurs fois (seule la première est remplacée), d'un
document créé et d'un `integrated_into` qu'aucune édition ne touche. Un motif de
rejet (`reason`) est plafonné à 500 caractères.

**Format du plan** (`resolve`) :

```json
{
  "summary": "résumé sur une ligne — devient le sujet du commit",
  "reviewed_by": "human:<qui confirme>",
  "resolutions": [
    { "id": "prop-…", "resolution": "accepted", "integrated_into": ["chemin.md"] },
    { "id": "prop-…", "resolution": "rejected", "reason": "motif obligatoire" }
  ],
  "edits": [
    {
      "path": "chemin/relatif/au/corpus.md",
      "section": "Titre du heading à remplacer",
      "content": "## Titre\n\nnouveau contenu",
      "frontmatter": { "last-verified": "2026-08-30" }
    }
  ]
}
```

| Champ d'édition | Effet |
|---|---|
| `content` seul | Remplace **tout le fichier**. Crée le document s'il n'existe pas. |
| `content` + `section` | Remplace **cette seule section**. À préférer : évite de réécrire un gros document. |
| `append` | Ajoute en fin de document. Exclusif de `content`. |
| `frontmatter` | Fusionne ces champs dans le frontmatter. Combinable avec les précédents. Les champs non cités gardent leur écriture exacte — liste en style flow, timestamp ISO, commentaires, guillemets. Si cette fidélité ne peut pas être garantie, la résolution est refusée (`INVALID_INPUT`) **sans rien modifier** : le plan entier est sans effet, et le message dit quoi corriger à la source (voir `ARCHITECTURE.md` § 5.4). Un champ cité sans forme existante (champ ajouté, document créé) s'écrit en style flow sur une ligne s'il est une liste de scalaires ou de mappings de scalaires (`tags: [a, b]`, `sources: [{id: x, …}]`), en style bloc sinon. |

**Un plan = un commit.** Trois groupes indépendants confirmés = trois plans
successifs. Le regroupement en un seul commit ne vaut que pour des propositions
portant sur le **même sujet**.

**Codes de sortie** : `0` succès, `4` erreur métier (`ERROR: <code>: …` sur
stderr), `5` erreur d'entrée/sortie ou JSON illisible.
