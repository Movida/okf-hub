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

## Ce sur quoi rien ne repose

Conformément à la spec du hub, **aucun autre livrable ne dépend de cette
vérification**. Le hub n'implémente aucune validation de conformité OKF : un
bundle dont le corpus ignore complètement OKF se charge, se recherche et se lit
normalement, du moment que ses fichiers sont du markdown UTF-8. C'est le § 1.4
— « une base sans le hub reste utilisable » — pris dans l'autre sens : le hub
reste utilisable sans OKF.
