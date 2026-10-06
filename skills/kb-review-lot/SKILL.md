---
name: kb-review-lot
description: Traiter en une session un gros lot de propositions en attente sur plusieurs bases du OKF Bundle Hub, en déléguant instruction et exécution à des agents. À utiliser quand on demande de « s'occuper des nouvelles propositions » et qu'elles se comptent par dizaines ou couvrent plusieurs bases. Complète kb-review, qui reste la règle pour chaque proposition.
---

# Revue d'un lot sur plusieurs bases

`skills/kb-review/SKILL.md` fait autorité sur chaque proposition : lis-le en
entier d'abord. Ce skill ne dit que comment **orchestrer** un gros lot sans
perdre ce qu'il garantit.

## 1. État des lieux (toi, pas un agent)

```sh
for b in bases/*/; do n=$(basename $b); bin/okf-review reconcile $n | tail -1; done
bin/okf-review inventory <base>                 # pour chaque base qui a du pending
git -C bases/<base> log --format=%B | grep '^Reviewed-By' | sort | uniq -c
```

Vérifie les règles d'autorisation de `.claude/settings.local.json`
(`bin/okf-review *`, `okf-lock … git -C bases/* commit`, `python3 _work/scripts/*`)
**avant** de déléguer : sans elles, un agent s'arrête au premier `resolve`.

## 2. Instruire : un agent par base, ou par groupe de documents

**Répartis par document visé, jamais par nombre de propositions.** Deux agents
qui rédigent des plans sur le même fichier partent du même état et s'écrasent.
En pratique : un agent par base, ou par grand groupe de fiches disjoint.
Donne-leur le brief de `brief-instruction.md` (à côté de ce fichier), avec la
liste des ids, les regroupements par sujet que tu vois déjà, et les liens avec
l'autre base (la frontière phoenix-blueway ↔ el2d-blueway se croise souvent).
Donne à chacun **son propre sous-répertoire** du répertoire temporaire : vu le
05/10, deux agents ont écrit chacun un `gen_plans.py` à la même racine, le
second a écrasé le premier et effacé ses plans.

Des propositions arrivent pendant l'instruction (cinq le 06/10, dont une qui
réfutait un plan prêt). Quand l'agent les signale, relance-le (SendMessage) pour
qu'il les instruise à la suite, en cascade, depuis sa copie où ses premiers
plans sont déjà appliqués : le lot reste présenté d'un seul tenant.

Exige d'eux des **décisions fermes, sur preuves** : l'humain ne veut pas arbitrer
ce qu'un export, un dump, le corpus ou un précédent de la base peut trancher.
Ne remontent vers lui que les faits qu'aucune source n'établit, chacun avec une
valeur par défaut sûre déjà écrite dans le plan. Sources de preuves disponibles
sans l'humain :

- exports Designer : `bases/el2d-blueway/_work/brut/`,
  `bases/el2d-referentiel/source/library/` ;
- dumps de base : `bases/el2d-referentiel/source/db/` ;
- fiches générées par objet : `bases/el2d-referentiel/referentiel-kb/` ;
- précédents : `git -C bases/<base> log --grep '^integrate\|^reject'`.

## 3. Présenter, puis exécuter

Présente d'abord les conflits de périmètre, puis un tableau par base (plan, ids,
décision, preuve qui tranche), puis les valeurs par défaut, puis les parts « à
porter vers ». Attends la confirmation (`kb-review`, étape 5) : une question de
l'humain n'en est pas une.

**Forme des commandes d'exécution.** Les règles d'autorisation couvrent des
commandes simples (`bin/okf-review resolve <base> --plan …`, `python3
_work/scripts/…` lancé depuis la base). Une boucle shell qui enchaîne les
`resolve` avec un `cd` vers la base n'est couverte par aucune règle : le
classifieur du mode auto l'a refusée le 05/10, et ce refus interdit ensuite de
redécouper la même opération sans l'accord de l'humain. Lance donc chaque
`resolve` et chaque script comme une commande isolée, ou donne la boucle à
l'humain pour qu'il l'exécute lui-même.

À la confirmation, **un agent d'exécution par base**. Il :

1. refait `inventory` — des propositions arrivent en cours de journée ; il
   instruit les nouvelles sans les résoudre, et écarte un plan qu'une arrivée
   contredit ;
2. régénère les plans **en cascade depuis le corpus actuel**, les vérifie
   ensemble au `--dry-run`, et compare les sections avant/après pour qu'aucune
   puce ne se perde ;
3. exécute un plan à la fois, puis lance depuis la base `gen_pieges.py`,
   `gen_index.py`, `check_all.py` (doit sortir 0) ;
4. ne commite pas les vues régénérées : c'est toi qui le fais, ensuite.

Dans un fil d'agent, un `cd` ne persiste pas d'un appel à l'autre : les scripts
de la base se lancent par chemin absolu (`python3
/workspaces/okf-hub/bases/<base>/_work/scripts/check_all.py`), ils retrouvent
leur racine par `__file__`.

**Le lancement des agents d'exécution peut lui-même être refusé.** Le 06/10,
trois agents lancés ensemble après « continue » : le classifieur en a refusé deux
(« Modify Shared Resources »), malgré les règles d'autorisation, et en a laissé
partir un. Le refus vaut pour le résultat : ni `resolve` à la main, ni nouvel
agent pour ces bases. Donne alors à l'humain, pour chaque base refusée, la boucle
prête à coller (`resolve` puis scripts, arrêt au premier échec), ou attends qu'il
te dise explicitement d'exécuter toi-même. Prépare cette boucle dès la
présentation du lot, pour qu'elle soit prête dans ce cas : un script dans le
répertoire temporaire, que l'humain lance par `! sh <chemin>`. L'après-midi du
06/10, le refus a de nouveau visé el2d-referentiel seul, alors que les agents de
phoenix-blueway et d'el2d-blueway sont partis : prévois d'office ce script pour
cette base.

Dans le brief d'exécution, écris que chaque commande se lance **seule et l'une
après l'autre** : un agent a lancé `gen_pieges`, `gen_index` et `check_all` dans
un même bloc d'appels, donc peut-être en parallèle, et a dû les rejouer.

**Un lot confirmé mais resté bloqué survit à sa session.** Les plans sont dans
le répertoire temporaire de la session précédente
(`/tmp/claude-1000/-workspaces-okf-hub/<session>/scratchpad/<base>/plans/`), et
les dernières réponses de son transcript (`~/.claude/projects/-workspaces-okf-hub/<session>.jsonl`)
disent ce qui a été présenté et exécuté. Si le corpus n'a reçu que des commits
`proposal:` depuis, copie ces plans, refais le `--dry-run`, et fais instruire
les arrivées en cascade après eux. Présente l'ensemble : la confirmation d'une
autre session ne vaut pas pour celle-ci.

Durées à prévoir : dans el2d-referentiel, `check_all.py` prend environ 7 min
(`check_catalogue` rejoue le générateur) — 8 plans font une heure. Ne donne
pas à un agent une attente bornée plus courte que l'exécution qu'il attend.

## 4. Après les résolutions (toi)

Les vues générées (`references/pieges-et-limites.md`…) et les correctifs
d'outillage de `_work/scripts/` ne passent pas par `okf-review`. Commite-les
**sous le verrou** de la base :

```sh
git -C bases/<base> add <fichiers>
bin/okf-lock <base> -- git -C bases/<base> commit -m "…"
```

Puis `git -C bases/<base> push origin main` si l'humain l'a demandé, et
vérifie `git status -sb`. Les parts « à porter vers » se redéposent par
`skills/kb-redepot` si l'humain le demande.

## 5. Ce qui a coûté cher la première fois (24/09/2026)

- Deux agents d'instruction sur la même base ont écrit des plans concurrents sur
  `solution/bpm-achat.md` : tout a dû être régénéré.
- Le brief demandait `generated.at` : plusieurs `schema.yaml` le réservent aux
  fiches produites par un générateur. Le brief dit maintenant « selon le
  `schema.yaml` de la base ».
- Huit questions renvoyées à l'humain, dont six se tranchaient par un export ou
  un dump. D'où la règle des décisions fermes, avec une valeur par défaut.
