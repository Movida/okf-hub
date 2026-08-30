# Politique de sécurité

## Modèle de menace — lisez-le avant de déployer

Le hub v0 est conçu pour un cadre précis, énoncé par sa spécification
([§ 8](docs/SPEC-okf-bundle-hub-v0.md)) :

> **hub mono-utilisateur, bundles auto-produits ou de provenance connue.**

Les garanties ci-dessous valent **dans ce cadre**. Le hub n'est pas un service
multi-locataires, n'authentifie personne, et n'est pas conçu pour être exposé sur
un réseau. Le transport est stdio : le serveur est lancé par le client Claude sur
la machine de l'utilisateur.

## Ce qui est garanti

| Garantie | Mécanisme |
|---|---|
| Une session consommatrice ne peut pas modifier un corpus | Seul `kb_propose` écrit, confiné à `proposals/pending/`. Aucun outil MCP n'écrit ailleurs. |
| Pas de traversée de chemin | Résolution canonique puis vérification d'inclusion stricte dans `corpus-dir`. `..`, chemins absolus et liens symboliques sortants rejetés. Tests dédiés. |
| Pas de faux trailers d'audit | Rejet de `\n` et `\r` dans `concerns`, `submitted_by` et chaque `sources[]`. Sans quoi `git log --grep "Proposal: …"` serait falsifiable. Tests dédiés. |
| Pas de frontmatter corrompu par l'entrée | Sérialisation exclusivement par bibliothèque YAML, jamais par templating de chaînes. Testé avec `---`, `:`, guillemets, ancres, directives. |
| Pas de corruption par écritures concurrentes | `flock()` avec descripteur neuf par acquisition, index git temporaire initialisé depuis HEAD, écriture atomique par `rename()`. Boucle de stress à deux processus dans la suite de tests. |
| Pas d'exécution de code d'un bundle | `tools/` et `skills/` d'un bundle **ne sont pas chargés** en v0. Les hooks git du dépôt sont neutralisés (`--no-verify`, `core.hooksPath`). |
| Pas de déni par inflation | `content` ≤ 16 Ko, `sources` ≤ 20 × 300 car., `concerns` ≤ 200 car., `submitted_by` ≤ 100 car. `description` de manifeste plafonnée à 500 car. Sorties d'outils plafonnées. |

## Ce qui n'est PAS garanti

**Importer un bundle tiers est sans risque d'exécution, mais pas sans risque
d'influence.** Trois vecteurs d'injection de prompt existent :

1. `title` et `description` du manifeste sont injectés dans les **descriptions
   d'outils MCP**, donc dans le contexte de **toutes les sessions connectées** —
   sans que quiconque ait ouvert le bundle. La validation limite la surface (pas
   de retour à la ligne dans `title`, `description` normalisée et plafonnée) sans
   l'éliminer.
2. `GOVERNANCE.md` est injecté dans le contexte du gestionnaire.
3. `CLAUDE.md` l'est dans celui de toute session ouvrant le dépôt.

> **N'importez que des bundles de confiance. Relisez le manifeste,
> `GOVERNANCE.md` et `CLAUDE.md` avant le premier usage de tout bundle tiers.**

**`submitted_by` n'est pas authentifié.** C'est un champ déclaratif. Il ne doit
peser dans aucune décision d'intégration, et le gestionnaire est explicitement
instruit de ne lui accorder aucun poids.

**Le contenu d'une proposition est une donnée, jamais une instruction.** La skill
`kb-review` impose d'escalader à l'humain toute proposition contenant des
directives adressées au gestionnaire. Cette barrière est une consigne de prompt :
elle est doublée d'une confirmation humaine obligatoire avant tout commit, mais
elle n'est pas une garantie technique.

**L'accès direct au système de fichiers reste possible** pour le propriétaire du
hub. La frontière de confiance porte sur ce que fait une session Claude via MCP,
pas sur ce que fait l'humain devant sa machine.

**Aucune isolation entre bases.** Toute session connectée au hub voit toutes les
bases. Il n'y a ni permission, ni cloisonnement.

**Les lectures ne prennent aucun verrou.** Une lecture concurrente d'une
intégration peut voir un état intermédiaire du worktree. Accepté en v0.

## Déployer prudemment

- **Montez le répertoire du hub, et lui seul.** Le devcontainer fourni le fait.
  Le confinement des chemins protège le corpus, pas votre machine.
- **Ne mettez pas de secret dans un corpus.** Il sera lu par des sessions
  Claude, et donc envoyé au modèle. Un bundle est une base de connaissance, pas
  un coffre.
- **Le clone dans `bases/` est la copie canonique.** Aucun `push` ni `pull`
  automatique n'a lieu. Une synchronisation manuelle qui écraserait des
  propositions locales non poussées est de la responsabilité de l'opérateur.

## Signaler une vulnérabilité

**N'ouvrez pas d'issue publique.**

Utilisez l'onglet *Security* → *Report a vulnerability* du dépôt GitHub
(advisory privée), ou contactez le mainteneur en privé.

Merci d'inclure : la version ou le commit, les étapes de reproduction, l'impact
que vous estimez, et — si le problème touche la concurrence — l'extrait de
`hub.log` correspondant : chaque entrée y porte son PID, ce qui est
indispensable pour reconstituer un entrelacement.

Réponse sous une semaine. Ce projet est maintenu à titre personnel, sans
engagement de délai de correction.

## Portée

Sont dans le périmètre : traversée de chemin, injection dans les messages de
commit ou le frontmatter, corruption par concurrence, exécution de code non
voulue, contournement du confinement à `proposals/pending/`.

Sont **hors périmètre**, par conception documentée : l'usurpation de
`submitted_by`, l'influence par prompt d'un bundle importé volontairement,
l'absence d'isolation entre bases, et tout ce qui suppose un attaquant ayant déjà
un accès au système de fichiers du hub.
