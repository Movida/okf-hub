# Contexte — bundle de connaissance OKF

Ce dépôt est un **bundle** : un corpus markdown versionné en git, exploitable
tel quel par un humain, et déployable sur un OKF Bundle Hub.

## Règle qui prime sur tout le reste

**Tu ne modifies jamais le corpus directement.**

Si tu constates un fait nouveau, une erreur ou une lacune, tu déposes une
**proposition** — via l'outil MCP `kb_propose` si le hub est connecté à ta
session, sinon en écrivant un fichier dans `proposals/pending/` au format
décrit plus bas. Un humain, ou une session tenant le rôle gestionnaire, décide
ensuite de l'intégrer ou de la rejeter.

Cette règle n'a pas d'exception « évidente » : une coquille manifeste, une URL
morte, une date visiblement fausse se signalent par une proposition comme le
reste. La frontière de confiance à l'écriture est ce qui rend la base fiable.

Les seules exceptions : une instruction humaine directe et explicite, ou le fait
de tenir le rôle gestionnaire (skill `kb-review`), qui a ses propres règles.

## Structure

```
.
├── okf-bundle.yaml     # manifeste — sa présence fait de ce dépôt un bundle
├── GOVERNANCE.md       # périmètre et golden rules d'intégration
├── schema.yaml         # champs de frontmatter attendus (documentaire)
├── CLAUDE.md           # ce fichier
├── knowledge/          # LE CORPUS (nom réglé par corpus-dir du manifeste)
└── proposals/
    ├── pending/        # propositions en attente de revue
    ├── accepted/       # propositions intégrées (archive)
    └── rejected/       # propositions rejetées, avec leur motif (archive)
```

`proposals/` n'est **pas** du corpus : il n'est ni recherché ni lu par les
outils de lecture du hub.

## Exigences documentaires

Elles tiennent en peu de lignes :

- un document = un fichier `*.md` en UTF-8, n'importe où sous `knowledge/` ;
- frontmatter YAML optionnel, délimité par `---` **en première ligne** ;
- si frontmatter il y a, le champ `title` est recommandé : c'est lui qui
  intitule les résultats de recherche ;
- tout le reste est régi par `GOVERNANCE.md` et `schema.yaml`.

## Format OKF (v0.2) — résumé opérationnel

Le corpus suit l'**Open Knowledge Format** v0.2
(<https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md>).
Ce qu'il faut en retenir pour écrire ou juger un document :

**Un concept = un fichier markdown.** Son identifiant est son chemin dans le
bundle, sans le `.md`. La structure de répertoires est libre.

**Noms de fichiers réservés**, à tout niveau de la hiérarchie — ce ne sont pas
des concepts :

| Fichier | Rôle |
|---|---|
| `index.md` | Sommaire du répertoire, pour la divulgation progressive. Sans frontmatter, sauf à la racine du corpus où il peut porter `okf_version`. |
| `log.md` | Historique des mises à jour, groupé par date `YYYY-MM-DD`, le plus récent en premier. |

**Frontmatter.** Seul `type` est requis (chaîne libre : `Procédure`,
`Incident`, `API Endpoint`…). Recommandés : `title`, `description`, `resource`
(URI de l'objet décrit), `tags`. Les familles optionnelles — `sources`,
`generated`, `verified`, `status`, `stale_after` — sont décrites dans
`schema.yaml`. Les clés inconnues sont conservées, jamais rejetées.

**Convention d'acteur**, pour tout champ qui nomme une identité
(`generated.by`, `verified[].by`, et par extension `submitted_by` d'une
proposition) :

- `human:<id>` pour une personne — c'est ce préfixe qui distingue une
  vérification humaine d'une vérification machine ;
- `<producteur>/<version>` pour un agent, ex. `claude-code/opus-5` ;
- `process:<id>` pour un traitement automatisé.

**Horodatages** : ISO 8601 avec décalage UTC explicite,
ex. `2026-06-30T14:00:00Z`.

**Liens entre concepts** : liens markdown standard. La forme absolue relative au
bundle (`/domaine/concept.md`) est recommandée, car stable quand un document
change de répertoire. Un lien cassé n'est pas une erreur : il peut désigner une
connaissance pas encore écrite.

**Attribution d'une affirmation précise** : note de bas de page markdown dont
l'étiquette est un `sources[].id`.

```markdown
Le bouton a été déplacé dans le menu profil.[^incident-4521]

[^incident-4521]: Incident #4521, constaté sur 3 postes le 13/06.
```

**Corps du document** : privilégie le markdown structuré — titres, listes,
tableaux, blocs de code — au texte suivi. C'est ce qui rend un document
citable par section (`kb_read` sait n'en retourner qu'une seule).

## Déposer une proposition sans le hub

Écris un fichier dans `proposals/pending/`, nommé
`prop-<AAAA-MM-JJ>-<4 caractères hexadécimaux>.md` :

```markdown
---
id: prop-2026-08-30-a3f2
submitted-by: human:<toi>
submitted-at: 2026-08-30T09:12:00Z
type: correction          # observation | correction | addition | question
concerns: "procédure de reconnexion SSO"
sources:
  - "constat terrain, incident #4521"
confidence: high          # high | medium | low
status: pending
---

L'affirmation, en markdown. Ce que tu as constaté, où, quand, et en quoi cela
contredit ou complète le corpus.
```

Sémantique des types :

- `observation` — un fait constaté, sans présumer d'un document existant ;
- `correction` — contredit un contenu actuel ;
- `addition` — complète un sujet déjà couvert ;
- `question` — une lacune identifiée, sans réponse fournie.

Le champ `status` est redondant avec l'emplacement du fichier : c'est
volontaire, pour qu'une proposition reste lisible hors de son contexte.
