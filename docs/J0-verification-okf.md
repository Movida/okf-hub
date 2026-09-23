# J0 — Vérification de la spécification OKF externe

Compte rendu de la tâche préalable imposée par la spec du hub
([§ 3.2](SPEC-okf-bundle-hub-v0.md#32-statut-du-format-okf-en-v0)) :
vérifier l'existence et le contenu de la spec OKF référencée par le propriétaire
du projet, et en tirer le résumé opérationnel destiné au `CLAUDE.md` du template
(§ 9).

**Effectué le 30/08/2026. Résultat : spec accessible.** Le template a donc été
livré avec le résumé opérationnel, et non avec les seules exigences minimales.

---

## Ce qui a été trouvé

| | |
|---|---|
| Dépôt | `github.com/GoogleCloudPlatform/knowledge-catalog` — **existe** |
| Fichier | `okf/SPEC.md` |
| Version | **0.2** |
| Nature | Auto-suffisante : « This document is self-contained: it specifies everything needed to produce and consume OKF v0.2. » |

L'URL était donnée comme « à confirmer » par la spec du hub. Elle est confirmée.

---

## Trois divergences à connaître

### 1. La version est 0.2, pas 1.0

L'exemple de manifeste de la spec du hub (§ 3.3) porte `okf-spec: "1.0"`. Cette
version n'existe pas. Le champ étant **déclaratif et non vérifié** (§ 3.2), c'est
sans conséquence fonctionnelle — mais le template déclare `okf-spec: "0.2"`, et
un `okf-spec: "1.0"` rencontré dans un bundle tiers doit être lu comme une
erreur de saisie, pas comme une version future.

### 2. OKF réserve `index.md` et `log.md` ; la spec du hub non

OKF § 3.1 : ces deux noms ont un sens défini à tout niveau de la hiérarchie et
**ne sont pas des documents de concept** — `index.md` est un sommaire pour la
divulgation progressive, `log.md` un journal de mises à jour.

La spec du hub (§ 2) définit au contraire « Document = tout fichier `*.md` sous
`corpus-dir` ».

**Conséquence mesurée**, sur le corpus réel `phoenix` (856 documents, dont 48
`index.md` générés automatiquement), 8 requêtes d'exploitation : **28 % des
résultats de `kb_search` étaient des sommaires** pleins de texte de liens.

**Traitement retenu** — un écart assumé, décrit dans
[`ARCHITECTURE.md` § 5.1](ARCHITECTURE.md) : ces fichiers restent des documents
(lus par `kb_read`, comptés par `kb_list`) mais sont **déclassés** dans le
classement de `kb_search`. Mesure après : **2 %**.

### 3. `timestamp` (v0.1) ≠ `generated.at` (v0.2)

OKF § 13.1 présente le remplacement de `timestamp` par `generated: { by, at }`
comme un changement cassant, avec repli toléré sur `timestamp`.

**Une migration mécanique serait fausse** sur le corpus `phoenix` : `timestamp` y
date **la page source** dont la fiche est le miroir, alors que `generated.at`
date la **dernière modification du contenu**. Ce ne sont pas les mêmes faits, et
réécrire le champ rendrait la fiche définitivement invisible au protocole de
ré-audit de cette base.

Le corpus reste donc en **v0.1**, ce qu'OKF autorise explicitement (§ 12 :
« Consumers that do not understand the declared version SHOULD attempt
best-effort consumption »). C'est documenté dans le `schema.yaml` et le
`CLAUDE.md` du bundle concerné.

---

## Ce qui a été repris dans les livrables

**Template (`okf-bundle-template`)** — `CLAUDE.md` porte le résumé opérationnel
OKF v0.2 : concept = fichier, identifiant = chemin sans `.md`, noms réservés,
`type` seul champ requis, familles `sources` / `generated` / `verified` /
`status` / `stale_after`, convention d'acteur (`human:<id>`,
`<producteur>/<version>`, `process:<id>`), horodatages ISO 8601 UTC, liens
bundle-relatifs, attribution par note de bas de page vers un `sources[].id`.

`schema.yaml` déclare ces familles plutôt que d'inventer des champs de fraîcheur
locaux — la spec du hub proposait `last-verified` en exemple, OKF a `verified` et
`stale_after`, et il n'y avait pas de raison de diverger sur une base neuve.

**Hub** — la convention d'acteur est recommandée dans la description de
`submitted_by` (`kb_propose`) et pour `Reviewed-By` dans la skill `kb-review`.
Elle n'est **pas imposée** : le champ reste libre, conformément au fait qu'il est
déclaratif et non authentifié (§ 8).

---

## Re-vérification du 16/09/2026

Refaite à l'occasion de la garde de fidélité du front-matter
(`ARCHITECTURE.md` § 5.4), qui avait besoin de savoir ce qu'OKF exige d'un
bloc de front-matter.

**La spec est toujours en 0.2** — même dépôt, même fichier, aucune 0.3
annoncée : le § 12 décrit le schéma de versionnage, le § 13 les ruptures
depuis la 0.1, et la sous-section « Considered and deferred » n'engage aucune
version. Les trois divergences ci-dessus tiennent donc toujours, y compris la
troisième : le corpus `phoenix` reste en `timestamp` plutôt qu'en
`generated.at`, ce qu'OKF tolère explicitement (§ 13.1 : les consommateurs
« MAY fall back to a legacy `timestamp` when `generated` is absent »). **Rien à
migrer.**

Deux exigences relues de près, parce que la garde de fidélité les touche :

### Le front-matter doit être parseable — et 91 documents ne l'étaient pas

Section conformance : « Every non-reserved `.md` file in the tree contains a
parseable YAML frontmatter block. » Ce n'est pas une recommandation.

**91 documents des bases installées ne la respectaient pas**, sans que rien ne
le signale : `mdutil.parse_document` tolère un front-matter illisible (§ 1.4 du
hub) et le rend simplement en brut, les parseurs maison des bundles lisent
champ par champ à la regex, et les `check_*.py` de chaque base ne vérifient pas
la syntaxe YAML. Deux causes mécaniques : un `: ` non échappé dans un scalaire
nu (`description:`), et une accolade dans une séquence flow
(`aliases: [PUT /api/.../{environment}/notification, …]`, où `{` est un
indicateur de flow interdit dans un scalaire nu).

Corrigé à la source le 16/09/2026 — `el2d-blueway` (4 fiches, commit `48bc9c4`)
et `phoenix-blueway` (87 fiches, commit `cc275ec`) : aucune valeur changée,
seule leur écriture. Les corpus installés sont désormais **conformes sur ce
point à 100 %** (3 166 documents à front-matter, 0 illisible), et le guide du
hub a suivi sur une question de style (`e1d5f22`).

### Les clés inconnues se préservent, et ne justifient aucun rejet

« Producers MAY include any additional keys. Consumers SHOULD preserve unknown
keys when round-tripping and MUST NOT reject documents with unrecognized
fields. »

Le hub va au-delà du « preserve » demandé : depuis la rév. de
`frontmatter.py`, une fusion préserve la **représentation** des champs qu'elle
ne cible pas, ou refuse de s'appliquer (§ 5.4 d'`ARCHITECTURE.md`). Le
« MUST NOT reject » n'est pas entamé pour autant, et la nuance vaut d'être
écrite : **aucun document n'est jamais rejeté**, ni à la lecture, ni à
l'énumération, ni à la recherche, quels que soient ses champs. Ce qui peut être
refusé, c'est une **écriture** dont la fidélité n'est pas établie — le document
reste lisible exactement comme avant, et c'est le plan de résolution qui est
renvoyé à son auteur.

---

## Ce sur quoi rien ne repose

Conformément à la spec du hub, **aucun autre livrable ne dépend de cette
vérification**. Le hub n'implémente aucune validation de conformité OKF : un
bundle dont le corpus ignore complètement OKF se charge, se recherche et se lit
normalement, du moment que ses fichiers sont du markdown UTF-8. C'est le § 1.4
— « une base sans le hub reste utilisable » — pris dans l'autre sens : le hub
reste utilisable sans OKF.

---

## Migration des bases métier en 0.2 — 23/09/2026

Sur instruction du propriétaire (« la spécification OKF a évolué (0.2) ; mets à
jour l'ensemble »). La spec elle-même n'a pas changé depuis le 21/08 (dernier
commit de `okf/SPEC.md`, `62432a0`) : ce qui restait en 0.1, c'étaient les trois
bases métier — `phoenix-blueway`, `el2d-blueway`, `el2d-referentiel`.

**La divergence 3 ci-dessus est levée, sans perte de sens.** Son objection
visait une migration vers `generated.at`, qui date le contenu. Or les trois
`schema.yaml` disent la même chose : `timestamp` date **la source** (page
miroir, relevé d'instance, `date_revision` du XML), jamais l'édition locale. La
0.2 a exactement ce champ : `sources[].last_modified`, « when the source itself
last changed … distinct from `generated.at` » (§ 5.1). La migration porte donc
chaque `timestamp` sur l'entrée `sources` de la source qu'il date — le
protocole de ré-audit de `phoenix-blueway` continue de comparer la même date.

Correspondance appliquée aux trois bases (spécification commune, un script
`_work/scripts/migrate_okf02.py` versionné dans chacune) :

- `timestamp` → entrée `sources` avec `last_modified` en datetime ISO avec
  décalage, puis retrait de `timestamp` (§ 13.1) ;
- liste `# Citations` → entrées `sources` et notes `[^id]` (§ 5.1, § 13.1) ;
- `generated: {by: process:<générateur>}` sans `at` sur les fiches générées, pour
  qu'une régénération ne re-date pas le corpus ;
- `log.md` aux titres stricts `## AAAA-MM-JJ` (§ 9), `okf_version: "0.2"` à la
  racine et `okf-spec: "0.2"` au manifeste ;
- contrôles de conformité des bases passés à la § 11 de la 0.2.

Les deux bases meta étaient déjà en 0.2 ; seuls quelques horodatages et un
acteur hors convention y ont été corrigés le même jour.
