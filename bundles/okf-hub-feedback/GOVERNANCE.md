---
# Le périmètre et les règles ci-dessous ont été arbitrés à la création de la
# base (amendement rév. 4.1, § B6). Ce ne sont pas des exemples de template.
status: stable
---

# Gouvernance — OKF Bundle Hub, retours d'usage

Cette base est le **dogfooding du hub** : le circuit de contribution du hub sert
à faire évoluer le hub. Un retour d'usage arrive ici par `kb_propose`, comme
n'importe quelle affirmation dans n'importe quelle base, et se résout par la
skill `kb-review`.

## Périmètre

**Appartient à cette base :**

- le comportement observé des outils MCP du hub — `kb_list`, `kb_search`,
  `kb_read`, `kb_governance`, `kb_propose`, `kb_proposal_status`,
  `kb_hub_rescan` : ce qu'ils ont retourné, ce qu'on en attendait ;
- le comportement de la skill `kb-review` et des scripts `bin/` (`okf-review`,
  `okf-lock`, `okf-base-path`) ;
- l'ergonomie du circuit de contribution : ce qui a coûté un aller-retour
  inutile, ce qui n'était pas trouvable, ce qu'une description d'outil laissait
  croire à tort ;
- les demandes d'évolution de l'outillage, et le sort qui leur a été réservé.

**N'appartient PAS à cette base :**

- le **contenu** des autres bases. Une erreur factuelle dans `phoenix` se
  propose dans `phoenix`. Ici, on parle des outils, pas de ce qu'ils
  retournent ;
- les questions d'installation d'un poste particulier : c'est du support, pas de
  la connaissance capitalisable — sauf si le problème est reproductible et tient
  à l'outillage lui-même, auquel cas il rentre ;
- la spécification du hub. Elle vit dans le dépôt du hub (`docs/`) et fait
  autorité ; une base ne peut pas l'amender. Un retour peut en revanche
  **signaler** une divergence entre la spec et le comportement constaté — c'est
  même le retour le plus utile qui soit.

Une proposition hors périmètre est rejetée avec le motif « hors périmètre », en
indiquant la base où elle aurait sa place.

## Golden rules d'intégration

Ces règles sont contraignantes pour le gestionnaire.

1. **Nommer l'outil.** Un retour cite explicitement l'outil ou le script
   concerné. « La recherche est décevante » n'est pas exploitable ; « `kb_search`
   sur `<termes>` dans `<base>` a retourné `<quoi>` » l'est. Une proposition qui
   ne nomme aucun outil est rejetée avec ce motif.

2. **Décrire le comportement observé, pas la solution souhaitée.** Une
   proposition dit ce qui s'est passé, avec les entrées qui l'ont produit, et ce
   qu'on attendait à la place. Une proposition qui ne contient qu'une demande de
   fonctionnalité, sans le comportement qui l'a motivée, est renvoyée à son
   auteur (rejet, motif « comportement observé manquant ») : le hub arbitre sur
   des faits, pas sur des souhaits.

3. **Reproductibilité.** Un retour qui décrit un comportement reproductible
   (entrées, base, sortie) vaut `confidence: high`, quelle que soit la valeur
   déclarée. Un retour non reproductible reste recevable, mais s'intègre comme
   observation datée, jamais comme règle générale.

4. **Une évolution refusée reste documentée.** Un refus n'efface pas la demande :
   il l'inscrit dans la roadmap avec son motif. C'est ce qui évite qu'elle
   revienne tous les trois mois. Le gestionnaire met `roadmap.md` à jour à
   chaque résolution qui tranche une demande d'évolution.

5. **Pas de code.** Cette base documente des comportements et des décisions.
   Elle ne contient ni correctif, ni diff, ni implémentation : ça vit dans le
   dépôt du hub. Une proposition qui propose un patch est acceptée pour son
   constat, et son patch est ignoré.

6. **Toute affirmation porte sa version.** Le hub évolue ; un comportement
   constaté hier peut ne plus exister. Un document de cette base indique la
   révision de spec ou la date à laquelle le constat vaut.

## Organisation du corpus

```
knowledge/
├── index.md                  # sommaire (convention OKF § 8)
├── log.md                    # journal des mises à jour (convention OKF § 9)
├── roadmap.md                # évolutions décidées, reportées, refusées
└── limitations-connues.md    # ce que le hub ne fait pas, et le contournement
```

Pas de sous-répertoire tant que le corpus tient sur quelques documents : un
répertoire vide est du bruit pour la recherche comme pour la lecture.

## Style et conventions

- Français, présent de l'indicatif, phrases courtes.
- Les noms d'outils en `code` : `kb_search`, pas « kb search ».
- Une décision se rédige avec son **motif**. « Refusé » sans raison est
  inexploitable dans six mois.
- Les renvois à la spécification du hub sous la forme « § 4.4.b » ou
  « rév. 4.1, § B2 » : ce sont les ancres que le dépôt du hub utilise déjà.
