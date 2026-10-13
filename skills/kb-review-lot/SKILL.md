---
name: kb-review-lot
description: Traiter en une session un gros lot de propositions en attente sur plusieurs bases du OKF Bundle Hub, en déléguant instruction et exécution à des agents. À utiliser quand on demande de « s'occuper des nouvelles propositions » et qu'elles se comptent par dizaines ou couvrent plusieurs bases. Complète kb-review, qui reste la règle pour chaque proposition.
---

# Revue d'un lot sur plusieurs bases

`skills/kb-review/SKILL.md` fait autorité sur chaque proposition : lis-le en
entier d'abord. Ce skill ne dit que comment **orchestrer** un gros lot sans
perdre ce qu'il garantit. Comme lui, il est générique et public : ce qui ne
vaut que pour une base est dans la section « Revue des propositions par le
hub » de son `AGENTS.md`, et c'est là qu'une leçon propre à une base s'écrit.

## 1. État des lieux (toi, pas un agent)

```sh
for b in bases/*/; do n=$(basename $b); bin/okf-review reconcile $n | tail -1; done
bin/okf-review inventory <base>                 # pour chaque base qui a du pending
git -C bases/<base> log --format=%B | grep '^Reviewed-By' | sort | uniq -c
```

Lis la section de revue de l'`AGENTS.md` de chaque base concernée : tu y
trouveras les sources de preuves, les frontières qui se croisent souvent, et ce
qui rend un lot long.

Vérifie aussi, **avant** de déléguer :

- les règles d'autorisation de `.claude/settings.local.json` (`bin/okf-review *`,
  `okf-lock … git -C bases/* commit`) : sans elles, un agent s'arrête au premier
  `resolve` ;
- que chaque base touchée qui déclare des `governance.checks` figure dans
  `trusted-checks` (`hub-config.local.yaml`) : sinon `verify` refuse, et c'est à
  l'humain de l'y ajouter après relecture des scripts.

## 2. Instruire : un agent par base, ou par groupe de documents

**Répartis par document visé, jamais par nombre de propositions.** Deux agents
qui rédigent des plans sur le même fichier partent du même état et s'écrasent.
En pratique : un agent par base, ou par grand groupe de fiches disjoint.
Donne-leur le brief de `brief-instruction.md` (à côté de ce fichier), avec la
liste des ids, les regroupements par sujet que tu vois déjà, et les liens avec
les bases voisines. Donne à chacun **son propre sous-répertoire** du répertoire
temporaire : vu le 05/10, deux agents ont écrit chacun un `gen_plans.py` à la
même racine, le second a écrasé le premier et effacé ses plans.

Des propositions arrivent pendant l'instruction (cinq le 06/10, dont une qui
réfutait un plan prêt). Quand l'agent les signale, relance-le (SendMessage) pour
qu'il les instruise à la suite, en cascade, après ses premiers plans : le lot
reste présenté d'un seul tenant.

**Plusieurs agents sur une même base (vu le 13/10).** Quand une base dépasse ce
qu'un agent instruit, découpe-la par dossiers ou par fichier : un agent pour la
grosse fiche que sa section de revue désigne, un autre pour tout le reste. Il
reste presque toujours un fichier partagé — typiquement un index racine dont une
table porte des effectifs qu'un contrôle compare au corpus, et que chaque plan
qui crée une fiche doit mettre à jour. Fixe donc d'emblée un **ordre
d'exécution** entre les agents, et exige un **générateur de plans**
(`gen_plans.py`) qui recalcule ce fichier sur le corpus qu'on lui donne, en
dernière édition de chaque plan : le lot suivant se régénère après le précédent.
Dis à chacun quels fichiers lui sont réservés ; ce qu'il lui faudrait ailleurs
passe par toi (« À verser par un autre agent »), et tu le relaies par
SendMessage, tout comme les chemins des fiches que les autres vont créer, pour
qu'il les lie au lieu de les répéter.

Quand les rapports sont là, **rejoue toute la cascade d'un seul `verify`** :
chaque agent n'a vérifié que ses plans. `verify` accepte des plans de plusieurs
bases, dans l'ordre d'exécution :

```sh
bin/okf-review verify --plan <base-a>:<rep>/<base-a>/plans/01.json \
                      --plan <base-a>:<rep>/<base-a>/plans/02.json \
                      --plan <base-b>:<rep>/<base-b>/plans/01.json
```

Celui du 13/10, fait alors à la main par un agent de contrôle, a trouvé des
renvois à sens unique, une contradiction entre deux fiches et une colonne
d'index devenue fausse. Un contrôle qui échoue en fin de cascade se localise
par `--each`.

Exige des agents des **décisions fermes, sur preuves** : l'humain ne veut pas
arbitrer ce qu'un export, un dump, le corpus ou un précédent de la base peut
trancher. Ne remontent vers lui que les faits qu'aucune source n'établit, chacun
avec une valeur par défaut sûre déjà écrite dans le plan. Les sources de preuves
de chaque base sont dans sa section de revue ; les précédents, dans
`git -C bases/<base> log --grep '^integrate\|^reject'`.

## 3. Présenter, puis exécuter

Présente d'abord les conflits de périmètre, puis un tableau par base (plan, ids,
décision, preuve qui tranche), puis les valeurs par défaut, puis les parts « à
porter vers ». Attends la confirmation (`kb-review`, étape 5) : une question de
l'humain n'en est pas une.

**Une commande par base.** À la confirmation, chaque base s'exécute par :

```sh
bin/okf-review resolve <base> --plan <rep>/<base>/plans/01.json … --checks
```

Un commit par plan, dans l'ordre ; les contrôles approuvés ensuite, et les vues
régénérées commitées à part (`regen:`). Une commande interrompue se relance
telle quelle : les plans déjà résolus sont sautés, et les contrôles tournent
même si tous l'étaient. C'est une commande simple, couverte par la règle
d'autorisation `bin/okf-review *` ; n'enchaîne pas les bases dans une boucle
shell avec des `cd` — le classifieur du mode auto l'a refusée le 05/10, et ce
refus interdit ensuite de redécouper la même opération sans l'accord de l'humain.

Avant de lancer, refais un `inventory` : des propositions arrivent en cours de
journée. Instruis les nouvelles sans les résoudre ; écarte un plan qu'une
arrivée contredit, et régénère les suivants en cascade, revérifiés par `verify`.

**Le lancement peut lui-même être refusé.** Le 06/10, trois agents d'exécution
lancés ensemble après « continue » : le classifieur en a refusé deux (« Modify
Shared Resources »), malgré les règles d'autorisation. Le refus vaut pour le
résultat : ni `resolve` à la main, ni nouvel agent pour ces bases. Donne alors
à l'humain la commande ci-dessus, prête à coller dans un terminal, sortie copiée
dans un journal (`… | tee <tmp>/execution.log`) que tu vérifies ensuite — ou
attends qu'il te dise explicitement d'exécuter toi-même. Pour modifier un
fichier d'une base hors `okf-review` (un `AGENTS.md`, une gouvernance, sur
instruction directe), préfère les outils Edit et Write, que les règles
d'autorisation couvrent, à un script shell qui écrit dans `bases/`.

**Un lot confirmé mais resté bloqué survit à sa session.** Les plans sont dans
le répertoire temporaire de la session précédente
(`/tmp/claude-<uid>/<projet>/<session>/scratchpad/<base>/plans/`), et les
dernières réponses de son transcript (`~/.claude/projects/<projet>/<session>.jsonl`)
disent ce qui a été présenté et exécuté. Si le corpus n'a reçu que des commits
`proposal:` depuis, copie ces plans, refais un `verify`, et fais instruire les
arrivées en cascade après eux. Présente l'ensemble : la confirmation d'une autre
session ne vaut pas pour celle-ci.

**Durées.** Une base dont les contrôles sont longs le dit dans sa section de
revue ; `--checks` ne les lance qu'une fois, après le dernier plan. Le 13/10,
155 plans sur cinq bases ont tourné en 12 minutes, une base après l'autre. Ne
donne pas à un agent une attente bornée plus courte que l'exécution qu'il
attend.

## 4. Après les résolutions (toi)

`git -C bases/<base> push origin main` si l'humain l'a demandé, et vérifie
`git status -sb`. **Une base publique** (`okf-hub-feedback`) passe par le hook
`pre-push` du hub, qui refuse de publier un terme privé : s'il refuse,
expurge le commit fautif avant de repousser — ne le contourne jamais par
`--no-verify`. Les parts « à porter vers » se redéposent par `skills/kb-redepot`
si l'humain le demande ; dans une base publique, `kb_propose` refuse un texte
qui cite une base privée, à reformuler avec des noms neutres.

Un correctif d'outillage d'une base (`_work/scripts/`) ne passe pas par
`okf-review` : il se commite sous le verrou de la base,
`bin/okf-lock <base> -- git -C bases/<base> commit -m "…"`.

## 5. Ce qui a coûté cher la première fois (24/09/2026)

- Deux agents d'instruction sur la même base ont écrit des plans concurrents sur
  la même grosse fiche : tout a dû être régénéré.
- Le brief imposait un champ de fraîcheur que plusieurs `schema.yaml` réservent
  aux fiches produites par un générateur. Le brief dit maintenant « selon le
  `schema.yaml` de la base ».
- Huit questions renvoyées à l'humain, dont six se tranchaient par un export ou
  un dump. D'où la règle des décisions fermes, avec une valeur par défaut.
