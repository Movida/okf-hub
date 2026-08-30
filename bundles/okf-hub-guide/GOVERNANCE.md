---
# Périmètre et règles arbitrés à la création de la base. Ce ne sont pas des
# exemples de template.
status: stable
---

# Gouvernance — mode d'emploi du hub

Cette base répond à une seule question : **comment une session travaille-t-elle
correctement avec ce hub ?** Elle existe parce qu'une session consommatrice ne
voit ni le `README.md` du hub, ni `docs/API.md`, ni `CLAUDE.md` : elle ne dispose
que des outils MCP et de leurs descriptions.

## Périmètre

**Appartient à cette base :**

- les **séquences d'appels** — quel outil, dans quel ordre, pour quel but ;
- la **stratégie** — comment formuler une recherche, quand demander une section
  plutôt qu'un document, comment interpréter un résultat partiel ;
- les **rôles** et la frontière de confiance à l'écriture : qui peut modifier un
  corpus, par quel outil, et pourquoi les autres ne le peuvent pas ;
- ce qu'est une **proposition recevable**, et ce qui la fait rejeter ;
- le **cycle de vie d'une base** : créer, déployer, alimenter, réviser, retirer —
  avec, à chaque étape, le rôle qui l'exécute et l'outil qui la porte ;
- les **anti-patterns** constatés, avec ce qu'il faut faire à la place.

**N'appartient PAS à cette base :**

- **les schémas d'outils** — noms de paramètres, types, bornes, valeurs
  d'énumération, format exact des sorties. Voir la golden rule 1 : c'est
  interdit, pas seulement déconseillé ;
- le contenu métier des autres bases : c'est chez elles ;
- les retours d'usage et demandes d'évolution sur l'outillage : c'est
  `okf-hub-feedback` ;
- l'exploitation du hub côté machine — installation, configuration du client,
  sauvegarde. L'opérateur a le dépôt du hub et son `README.md` ;
- la spécification du hub, qui fait autorité et vit dans `docs/` du dépôt.

## Golden rules d'intégration

1. **Aucun schéma d'outil, jamais.** Ce document ne cite pas un nom de paramètre
   pour en décrire le type, les bornes ou les valeurs admises. La source de
   vérité est la description de l'outil, elle-même dérivée du code. Une
   troisième copie dériverait, et serait la seule que personne ne teste. Une
   proposition qui ajoute un tableau de paramètres est **rejetée**, motif
   « golden rule 1 : la référence n'est pas ici ».

   Nommer un outil ou un paramètre **pour dire quoi en faire** reste correct :
   « reportez le heading affiché après `§` dans le paramètre `section` de
   `kb_read` » est de la procédure, pas de la référence.

2. **Une consigne se justifie par un mécanisme.** « Utilise `section` » ne suffit
   pas ; il faut dire qu'au-delà d'un seuil un document renvoie sa table des
   headings, et pourquoi c'est voulu. Une session qui comprend le mécanisme
   généralise ; une session qui apprend une recette échoue au premier cas
   inhabituel.

3. **Un anti-pattern porte son remplacement.** Interdire sans dire quoi faire à
   la place produit une session bloquée, pas une session prudente.

4. **Rien qui contredise la spécification.** En cas de doute entre ce document et
   `docs/SPEC-okf-bundle-hub-v0.md`, la spec a raison et ce document est en
   dette. Une proposition qui signale une telle divergence est prioritaire.

5. **Le rôle est toujours explicite.** Toute opération décrite indique qui
   l'exécute — session consommatrice, gestionnaire, opérateur — et par quel
   moyen : outil MCP, ligne de commande, ou git. Une instruction sans rôle laisse
   croire que tout le monde peut tout faire, ce qui est exactement faux ici.

6. **Toute affirmation porte sa version.** Le hub évolue. Un document indique la
   révision de spec à laquelle il se rapporte (`applies-to`).

## Organisation du corpus

```
knowledge/
├── index.md                     # sommaire (convention OKF § 8)
├── log.md                       # journal des mises à jour (convention OKF § 9)
├── roles-et-ecriture.md         # qui écrit quoi, et pourquoi la frontière existe
├── chercher-et-lire.md          # séquences et stratégie de lecture
├── proposer.md                  # ce qu'est une proposition recevable
└── cycle-de-vie-d-une-base.md   # créer, déployer, alimenter, réviser, retirer
```

Documents courts et autonomes : ils sont lus par `kb_read` en entier, et chaque
lecture inutile est du contexte perdu.

## Style et conventions

- Français, présent de l'indicatif, phrases courtes, impératif pour les consignes.
- Noms d'outils en `code`.
- Une consigne s'écrit du point de vue de la session qui l'exécute : « appelle »,
  pas « on appellera ».
- Les renvois à la spécification sous la forme « § 4.4.b ».
