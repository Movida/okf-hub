# Référence d'API — outils MCP `kb_*`

Contrat exact des sept outils du noyau. Vérifié contre les schémas réels du code
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
| `INVALID_INPUT` | Paramètre hors bornes, énumération non respectée, regex invalide, retour à la ligne interdit. | Corriger l'entrée. Ne pas réessayer à l'identique. |
| `BASE_BUSY` | Verrou d'écriture non acquis en 15 s. | **Réessayer plus tard.** Ce n'est pas une défaillance. |
| `IO_ERROR` | Défaillance réelle : ripgrep absent, git en échec, disque. | Signaler à l'opérateur. Un retry n'aidera pas. |

Un paramètre manquant ou mal typé produit une erreur **JSON-RPC standard**, pas
un `isError` — c'est le SDK MCP qui la génère, avant d'atteindre l'outil.

### Plafond de sortie

`kb_search`, `kb_list` et `kb_proposal_status` plafonnent leur sortie à
**~4 000 tokens**
(approximation caractères/4, soit 16 000 caractères). La troncature s'arrête sur
un résultat entier — jamais au milieu — et est signalée par une ligne
`[résultats tronqués, N élément(s) omis — …]`.

`kb_read` n'est pas plafonné : c'est le mode « table des headings » qui joue ce
rôle. `kb_governance` ne l'est pas non plus, conformément à la spec.

### Descriptions dynamiques

Les descriptions de `kb_list`, `kb_search`, `kb_read`, `kb_governance`,
`kb_propose` et `kb_proposal_status` **énumèrent les bases connues** avec leur titre et leur objet, et
sont recalculées à chaque `tools/list`. C'est ce qui permet à une session de
router sa requête vers la bonne base **sans appel préalable**.

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
documents : 42 | propositions en attente : 3
```

Avec `include_pending_concerns`, un second bloc par base suit :

```
### propositions en attente — solution-editeur-x
- prop-2026-06-14-a3f2 (correction) — procédure de reconnexion SSO
```

Les listes de `concerns` sont **tronquées en priorité** : les résumés de bases
sont toujours émis en entier, les concerns seulement si le budget le permet.

Sans aucune base enregistrée, la sortie explique comment en importer une.

**Quand l'appeler** — au début d'une session, pour savoir quoi interroger ; et
avec `include_pending_concerns` **avant `kb_propose`**, pour ne pas soumettre un
doublon. Ce n'est pas obligatoire : la déduplication est le travail du
gestionnaire, pas le vôtre.

---

## kb_search

Recherche plein texte dans le corpus d'une ou plusieurs bases, via ripgrep.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `base` | `string` \| `string[]` | **requis** | Nom de la base, liste de noms, ou `"*"` pour toutes les bases enregistrées. Voir « Multi-bases » ci-dessous. |
| `query` | `string` | **requis** | Termes, ou expression régulière en mode `regex`. |
| `mode` | `"keyword"` \| `"regex"` | `"keyword"` | Voir ci-dessous. |
| `max_results` | `integer` 1–25 | `8` | Hors bornes → `INVALID_INPUT`. Plafond **global** : voir « Multi-bases ». |

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

---

## kb_read

Lit un document, ou une seule de ses sections.

| Paramètre | Type | Défaut | Rôle |
|---|---|---|---|
| `base` | `string` | **requis** | Nom de la base. |
| `path` | `string` | **requis** | Chemin relatif au corpus. |
| `section` | `string` | — | Titre du heading à extraire. |
| `force` | `boolean` | `false` | Retourne le document entier même s'il est volumineux. |

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

**Confinement** — cet outil est la **seule exception** à la liste d'exclusions
transverse du § 5.2, qui retire `proposals/` des lectures. Exception limitée à
lui, en lecture seule : un lien symbolique déposé dans `pending/` et pointant
hors du bundle est ignoré, comme pour `kb_read`.

---

## kb_hub_rescan

Relance la découverte des bases. Aucun paramètre.

**Sortie**

```
Découverte terminée : 2 base(s) enregistrée(s).
ajoutées (1) : phoenix
retirées (0) : —
inchangées (1) : okf-hub-feedback

Bundles invalides (ignorés) :
- brouillon : champ obligatoire manquant : governance.rules

Collisions de name (premier en ordre lexicographique retenu) :
- 'doublon' : 'aaa-clone' retenu, 'zzz-clone' ignoré

Avertissements :
- [phoenix] bundle-spec '9.9' différent de la version supportée '0.1' — chargement tenté et réussi
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
rejetés avec leur motif, collisions de `name`, avertissements de compatibilité.
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

**Contribuer un fait nouveau**

```
kb_governance base=…                      → ce que la base exige
kb_list       include_pending_concerns=true  → quelqu'un l'a-t-il déjà signalé ?
kb_search     base=… query="sujet"        → que dit le corpus aujourd'hui ?
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
```

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
| `frontmatter` | Fusionne ces champs dans le frontmatter. Combinable avec les précédents. |

**Un plan = un commit.** Trois groupes indépendants confirmés = trois plans
successifs. Le regroupement en un seul commit ne vaut que pour des propositions
portant sur le **même sujet**.

**Codes de sortie** : `0` succès, `4` erreur métier (`ERROR: <code>: …` sur
stderr), `5` erreur d'entrée/sortie ou JSON illisible.
