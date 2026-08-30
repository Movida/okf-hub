# `bundles/` — sources des bases livrées avec le hub

Ce répertoire contient la **source** des bases que le hub livre par défaut. Il ne
contient pas les bases elles-mêmes : celles-ci vivent dans `bases/`, qui reste
ignoré par git — chaque base déployée est un dépôt git autonome (§ 4.2).

```sh
bin/okf-bootstrap        # déploie dans bases/ ce qui manque
```

## Pourquoi deux emplacements plutôt qu'un

**Une base versionnée dans le dépôt du hub casse le circuit de contribution.**
Ce n'est pas une précaution de principe, c'est vérifiable en une minute :
`gitops.commit_paths` exécute `git -C <racine du bundle>`. Si cette racine est un
simple sous-répertoire du dépôt du hub, git remonte au dépôt englobant et
`kb_propose` **commite sur la branche `main` du hub** — sans erreur, sans
avertissement. Et si le répertoire reste ignoré par git, c'est l'inverse :
`git add` échoue et tout `kb_propose` retourne `IO_ERROR`.

Une base déployée doit donc être son propre dépôt. Mais la source, elle, gagne à
vivre ici :

- **elle change en même temps que le code.** Le guide décrit le comportement des
  outils ; un même commit peut modifier l'outil et ce qu'on en dit ;
- **la CI la vérifie.** `tests/test_bases_meta.py` lit les `SCHEMA` du code et
  échoue si un corpus cite un outil inexistant ou attribue à un outil un
  paramètre absent de son schéma. Tant que la source vivait hors du dépôt, ce
  garde-fou se contentait de `skip` sur un checkout neuf — il ne tournait nulle
  part ;
- **une installation neuve n'est pas muette.** Un `git clone` du hub suffit à
  disposer du guide, sans dépendre d'un second clone que personne ne fait.

## Qui fait autorité, une fois déployé

Les deux bases ne se comportent pas pareil, et c'est délibéré.

| Base | Source de vérité | Installée par | Divergence attendue |
|---|---|---|---|
| `okf-hub-guide` | **ici**, toujours | semis depuis `bundles/` | **Non.** Un test compare le corpus déployé à cette source. Une résolution appliquée à la copie déployée doit être reportée ici. |
| `okf-hub-feedback` | son **dépôt canonique** | `git clone` | **Oui.** Alimentée par les sessions, son corpus s'enrichit de propositions intégrées. La copie ici n'est qu'une graine historique. |

L'asymétrie tient à leur nature. Le guide est **rédigé par les mainteneurs**, en
verrou avec le code : c'est de la documentation, sa place est dans le dépôt.
`okf-hub-feedback` est **alimentée par les sessions** : son contenu s'accumule
depuis le terrain, et le dépôt publié devient l'original.

## `upstreams.yaml` — pourquoi certaines bases sont clonées

Une base listée dans [`upstreams.yaml`](upstreams.yaml) est **installée par
clone**, jamais semée depuis `bundles/`.

Semer une base qui a un dépôt canonique produirait, sur chaque machine, une
histoire git **sans aucun rapport** avec la sienne. Une proposition déposée sur
une telle base orpheline serait irrécupérable : `git` refuse de fusionner des
histoires non liées, et le circuit d'audit du § 6.2 — « toute proposition
apparaît dans exactement deux commits » — ne veut plus rien dire s'il porte sur
une histoire parallèle.

Si le clone échoue — hors ligne, droits manquants — la base **n'est pas
installée**, et le journal explique quoi faire. Absente vaut mieux qu'orpheline :
une base manquante se voit tout de suite, une histoire divergente se découvre le
jour où l'on veut remonter six mois de contributions.
