# Conception — consultation des bases

> **Statut : intégrée à la spécification, révision 4.5** (§ 1.2, § 2, § 3.3,
> § 3.5, § 3.6, § 4.1, § 5.6, § 5.8, § 5 bis, § 8, § 9). La spécification
> ([`SPEC-okf-bundle-hub-v0.md`](SPEC-okf-bundle-hub-v0.md)) fait autorité ;
> les décisions d'implémentation sont dans
> [`ARCHITECTURE.md`](ARCHITECTURE.md) § 6 nonies. Ce document ne garde que ce
> qui n'a pas sa place dans l'une ou l'autre : l'origine, le constat, les
> options écartées, ce que l'implémentation a changé à la proposition du
> 28/10/2026, et les questions encore ouvertes.
>
> **Origine.** Le retour d'usage du 26/10/2026 rangé « À arbitrer » dans la
> roadmap d'`okf-hub-feedback` (« Consultation par un humain : une interface web
> en lecture seule ») : une session a dû enchaîner sept appels pour établir
> qu'une procédure n'existait pas, et un humain n'a aucun accès aux bases hors
> session MCP ou lecture des dépôts.
>
> **Décisions du propriétaire (28/10/2026)** : le profil d'une base s'écrit en
> YAML ; okf-hub expose ses vues par lui-même, sans dépendre d'un autre
> logiciel, et les rend accessibles à d'autres interfaces (HOLARCH en premier) ;
> la consultation est en lecture seule.

Public : qui reprend la consultation, et le propriétaire de la spec.

---

## 1. Objectifs

1. **Un humain consulte les bases sans session Claude** : liste des bases,
   navigation, documents rendus, liens entre bases, filtres, recherche,
   propositions, journal.
2. **Un agent prouve une absence en un appel** : « aucun document de type
   `Procédure` sur ce sujet » doit être une réponse exacte, pas le résultat de
   reformulations successives.
3. **Le tout est générique** : n'importe quel bundle s'affiche, quelle que soit
   la nature de la base instanciée depuis le template. Ce qu'une base sait de
   son propre domaine améliore l'affichage, sans le conditionner.

## 2. Constat (28/10/2026, hub de 8 bases, 3 733 fichiers markdown)

**OKF 0.2 laisse ouverts, explicitement**, les liens entre bundles, la
déclaration d'un vocabulaire, les facettes de tags, toute métadonnée de bundle
au-delà d'`okf_version`, et tout ce qui touche au rendu, au catalogue ou à la
requête. L'amendement comble ces trous sans contredire OKF.

**Ce qui n'était pas générique**, relevé sur les bases installées :

| Point | Situation |
|---|---|
| Liens entre bases | Six bases partagent un outillage qui écrit `[libellé](<base>:/<chemin>)` : 877 références dans cinq bases, que seul `kb_read` savait suivre. Leurs contrôles les résolvent par des chemins disque déclarés dans un module Python de chaque base ; une référence vers une base **non déclarée** échappe à tout contrôle : une base en cite une autre qui la cite 441 fois, et seul le hasard (le lien est un exemple) fait qu'il n'y a pas de cycle. |
| Vocabulaire | Le vocabulaire fermé d'une base vit jusqu'à **quatre endroits** : un module Python, un script de contrôle, la prose de `schema.yaml`, une table de routage écrite à la main dans l'index racine. |
| Champs, sections | `provenance`, `aliases`, périodes, identifiants externes, et des sections comme `# Pièges` ou `# Amont` ont un sens précis, connu des seuls scripts de la base. |
| Confiance | Deux dialectes : les six bases outillées portent `provenance` et un champ de preuve, et **aucune** n'utilise `verified`, `status` ni `stale_after` ; les bases livrées avec le hub utilisent `verified`. |
| Vues dérivées | Pages générées par script (index des pièges, chronologie) ; requêtes par un outil en ligne de commande propre à chaque base. |
| Liens | 27 700 liens bundle-relatifs (`/dossier/fiche.md`, OKF § 6.1), 3 600 relatifs : la proposition initiale ne gardait que « relatifs et `<name>:/` ». |

Un autre format compatible OKF, l'arbre de HOLARCH, écrit ses liens entre arbres
`<id de la racine>:<chemin>` : même forme, mais chemin pris depuis la racine du
dépôt, et non depuis celle du bundle (question 1 ci-dessous).

## 3. Ce que l'implémentation a changé à la proposition

La conception a été reprise sous-ensemble par sous-ensemble contre le code et
les données réelles avant toute ligne de code, puis ses contraintes ont été
réexaminées une à une.

| Proposition du 28/10 | Implémenté | Motif |
|---|---|---|
| Clés de profil en français (`valeurs`, `facettes`, `vues`…) à côté des clés anglaises du schéma v0 | **Clés et rôles en anglais** (`values`, `facets`, `views`, `warnings`, `upstream`…) ; titres, facettes et valeurs dans la langue de la base | Un même vocabulaire en deux langues ; la règle était déjà celle des paramètres de `kb_find`. Réversible tant qu'aucune base n'a écrit son profil. |
| En-tête d'identité cru « sur l'interface locale » seulement | **`trusted-proxies`** (défaut : bouclage), adresse du pair TCP | Le proxy peut vivre ailleurs que sur la machine du hub, sans qu'un client direct choisisse son identité. |
| Les autres interfaces passent par MCP ; `structuredContent` « si besoin » | **`kb_find` rend un `structuredContent`** validé par un `outputSchema` | Une interface ne doit pas analyser du texte pour lire des effectifs. |
| `/b/<base>/propositions` | **`/b/<base>/-/propositions`** (segment `-`) | Un dossier de corpus nommé `propositions/` restait inadressable. |
| Liens conservés : http, https, mailto, relatifs, `<name>:/` | **Plus les liens bundle-relatifs** `/…` | 27 700 liens. |
| `kb_find` : le tableau de paramètres | **Plus `without_section`, `links_to`, `count_by: section`** ; le lien venu d'une section `upstream` est marqué | Sans eux, l'outil générique faisait moins que les outils en ligne de commande des bases qu'il remplace. |
| Fraîcheur : verified, generated, sources, commit | Même ordre, **origine toujours dite** ; la vue `freshness` ne retient que les provenances qui déclarent une preuve | Les outils de ré-audit des bases ne confrontent que ce qui a une source ; « dernier commit » n'est pas « dernière confrontation ». |
| Images : non traité | **Servies**, types d'image seulement, SVG sous `sandbox` | Objectif 3 (généricité), même si aucun corpus installé n'en contient. |
| Vue `graph` | Figure SVG jusqu'à 40 documents, sans script | Lisible sans JavaScript ; au-delà, liste d'adjacence. |
| Rendu par `markdown-it-py` | Même choix, **limite d'imbrication relevée et troncature détectée** | À sa valeur par défaut, le parseur abandonnait en silence la fin d'une fiche réelle (14 Ko rendus sur 154). |
| Liens extraits « par le même moteur » | Expression régulière pour l'index, parseur pour la page, **même classement** | 1,8 s contre 17,4 s ; exactement les mêmes 32 356 liens sur le hub mesuré. |

## 4. Non retenu

| Option | Motif |
|---|---|
| Un visualiseur dans le template | Chaque base instanciée en garderait une copie divergente ; il ne verrait pas les autres bases ; ce serait du code de base lancé hors `okf-review` (§ 8). |
| Les vues dans HOLARCH seulement | Décision du 28/10 : le hub expose ses vues par lui-même ; HOLARCH y accède. |
| API JSON pour les autres interfaces | Un contrat d'échange nouveau, parallèle à MCP : ce qu'interdit le § 1.2. MCP couvre les données (sortie structurée comprise), un lien couvre la lecture. |
| Index ou base de données persistants | 2,9 s pour charger tout le hub mesuré, 0,08 s pour vérifier qu'il n'a pas changé ; § 1.1 et § 10.3. À rouvrir seulement sur mesure. |
| Outil de notes local (type Obsidian) | Un clone par lecteur, pas de références entre bases, pas de propositions. |
| Générateur de site statique (type Quartz, MkDocs) | Il faudrait des extensions pour les références entre bases, le profil et les propositions : ce serait un second lecteur. Il reste une cible possible pour un export. |
| Wiki (type Wiki.js, Outline) | Son stockage et son édition contourneraient `kb_propose`. |
| Profil en JSON ou TOML | Décision du 28/10 : YAML, comme le manifeste et le frontmatter. |
| Schéma d'URI `okf://<base>/<chemin>` | `<name>:/<chemin>` est déjà en usage, et a la forme des liens entre arbres de HOLARCH. |
| Découper les tags sur `:` sans déclaration | Une heuristique se tromperait sur des tags libres qui contiennent `:`. |
| Déposer une proposition depuis le web | Décision du 28/10 : lecture seule pour le moment. |
| JavaScript dans l'interface | Tout se sert par formulaires GET ; la politique de contenu interdit les scripts. À rouvrir pour une vue interactive, servie localement. |
| `git status` pour invalider le cache | Ne voit pas un fichier déjà modifié qui l'est une seconde fois. |

## 5. Questions ouvertes

1. **Racine des chemins** des liens entre arbres de HOLARCH (dépôt) et des
   références entre bases (corpus) : aligner HOLARCH sur la racine OKF, ou
   résoudre les deux formes. À trancher avec HOLARCH, dont la question Q13 porte
   sur la brique Savoir.
2. **Liste des rôles** : à confirmer après le profil de la base pilote.
3. **Exemples de syntaxe** dans les pages de convention des bases : écrits en
   code, ils ne comptent pas comme liens (l'extraction ignore le code) ; écrits
   comme de vrais liens, ils apparaissent cassés. L'affaire de chaque base.
4. **Sortie structurée et contexte du modèle** : un client qui transmettrait au
   modèle à la fois le texte et `structuredContent` de `kb_find` doublerait le
   coût en tokens que le § 1.5 plafonne. À mesurer sur les clients réels avant
   d'étendre la sortie structurée à d'autres outils.

La question du chargeur YAML compilé est tranchée : `CSafeLoader`, 0,41 s au
lieu de 2,79 s, aucune divergence (`ARCHITECTURE.md` § 6 nonies).
