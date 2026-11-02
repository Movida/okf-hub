---
type: Procédure
title: Chercher et lire dans une base, sans gaspiller de contexte
description: >
  Les séquences d'appels, le choix d'une base, et les mécanismes de recherche et
  de lecture qu'il faut comprendre pour ne pas rapatrier des documents entiers.
tags: [kb_list, kb_search, kb_read, kb_find]
applies-to: "rév. 4.1"
generated:
  by: "claude-code/opus-5"
  at: 2026-08-30T00:00:00Z
verified:
  - by: claude-code/sonnet-5
    at: 2026-09-17T00:00:00Z
    note: >-
      ajout des liens interbase dans kb_read, de kb_search multi-bases et de
      la troncature par volume (livrés 2026-09-02 et 2026-09-10, non
      répercutés ici avant cette relecture)
  - by: claude-code/opus-5.5
    at: 2026-10-29T00:00:00Z
    note: >-
      ajout de kb_find (rév. 4.5) : établir une existence ou une absence par
      critères, en un appel, au lieu de reformuler des recherches ; texte comme
      critère et amont modifié (rév. 4.6)
  - by: claude-code/opus-5.5
    at: 2026-11-02T00:00:00Z
    note: >-
      ajout des propositions en attente dans la recherche, sur demande, après
      le corpus et marquées « non intégré » ; ancienneté de la file dans kb_list
      (rév. 4.7)
---

# Chercher et lire, sans gaspiller de contexte

Le principe qui gouverne tous ces outils : **retourner le minimum pertinent**.
Ils sont conçus pour que tu n'aies jamais à charger un document entier. Si tu te
retrouves à le faire, c'est presque toujours qu'une étape a été sautée.

## La séquence de base

```
kb_search  → où en parle-t-on ?
kb_read    → la section utile, pas le fichier
```

`kb_list` n'est en général **pas** nécessaire au début : les descriptions des
outils énumèrent déjà les bases connues avec leur objet. C'est fait pour que tu
routes ta requête sans dépenser un appel. Appelle `kb_list` quand tu as besoin du
nombre de documents, des propositions en attente — et de la date de la plus
ancienne, qui dit depuis quand la revue n'a pas eu lieu —, ou quand une base
vient d'être importée.

## Choisir la base

Lis la **description** de chaque base : elle est rédigée pour ça, elle dit ce
qu'on y trouve. Si aucune ne couvre ton sujet, ne force pas la plus proche : la
bonne réponse est qu'il n'y a pas de base pour ça. Signale-le plutôt à l'humain.

Si le sujet peut vraiment être à cheval sur plusieurs bases, `kb_search`
accepte une liste de bases (ou toutes les bases enregistrées) en un seul
appel plutôt qu'un appel par base. Le plafond de sortie reste partagé entre
elles, pas multiplié : ne le fais que si router vers une seule base est
vraiment impossible, pas par réflexe.

## Comprendre ce que la recherche te répond

La recherche est un plein texte, pas une recherche sémantique. Deux mécanismes
à connaître, parce qu'ils changent l'interprétation du résultat :

**Le repli automatique.** Les termes sont d'abord combinés en ET strict. Si
aucun document ne les contient tous, la recherche bascule en OU et **te le dit**
explicitement dans sa sortie. Ce signal est important : il veut dire que ta
formulation ne correspond à rien tel quel, et que les résultats sont des
approximations. Reformule avec moins de termes plutôt que d'exploiter des
résultats partiels.

**Le déclassement des sommaires.** Les fichiers `index.md` et `log.md` sont des
tables des matières et des journaux : denses en texte de liens, ils matchent
beaucoup et n'apprennent rien. Ils passent derrière tout autre document, et la
sortie le signale. S'ils remontent en tête, c'est qu'il n'y avait rien d'autre.

**La troncature par le volume, pas par le nombre.** La sortie est plafonnée en
taille, pas en nombre de résultats : `max_results` fixe combien de résultats
sont *candidats*, mais l'écriture s'arrête dès que l'un d'eux dépasse ce qui
reste de budget, et rien derrière lui n'est montré. Si tu vois
`[résultats tronqués, … omis]`, monter ou baisser `max_results` ne fera
jamais réapparaître ce qui a été coupé : seule une requête plus précise, qui
change le classement, y change quelque chose.

**Ce que la recherche n'est pas** : un moyen de compter. Un mot cité entre
backticks dans un document de conventions sera trouvé comme s'il était une vraie
occurrence. Elle sert à trouver, jamais à dénombrer.

## Ce qui attend la revue

Par défaut, la recherche ne lit que le corpus : ce qui a été revu. Ce que
d'autres sessions ont déposé et qui attend la revue n'y est pas — parfois depuis
plusieurs jours. Si ton sujet est récent (un incident de la veille, un numéro de
ticket) ou si tu t'apprêtes à proposer, demande-lui d'inclure les propositions
en attente.

Elles arrivent **après tous les résultats du corpus**, et ne leur prennent
aucune place : la part revue de la réponse est celle que tu aurais eue sans
elles. Chacune est marquée « non intégré », avec son identifiant, son type, son
objet et sa date de dépôt, puis des extraits de son texte. Ce sont des
**affirmations que personne n'a encore vérifiées** : utiles pour ne pas refaire
une enquête ni déposer un doublon, jamais à citer comme un fait établi. Tu ne
peux pas les lire en entier ; leur état se consulte par `kb_proposal_status`.

## Lire une section, pas un document

Chaque extrait retourné par la recherche porte, après `§`, le titre de la
section d'où il vient. **Reporte-le tel quel** dans le paramètre `section` de
`kb_read` : tu obtiens la section entière en un seul appel, sans rapatrier le
document.

```
kb_search  → "  L120-124 § reconnexion sso"
kb_read    → section: "reconnexion sso"
```

Le corps d'un document cite parfois une autre base par un lien littéral,
`[libellé](produit-x:/chemin.md)`. Ce n'est pas réservé au rendu
humain : recopie-le tel quel dans le `path` de `kb_read`, il est reconnu et
lit directement l'autre base — inutile de le décomposer toi-même en `base` +
`path`. Un préfixe qui ne correspond à aucune base enregistrée est traité
comme un chemin littéral, pas comme une base inconnue.

Au-delà d'une certaine taille, un document lu **sans** section ne renvoie pas son
contenu mais sa **table des headings**. Ce n'est pas une erreur à contourner :
c'est le mécanisme qui te force à cibler. Choisis un heading dans la table et
rappelle la lecture avec.

Le paramètre qui force le document entier existe, mais il n'a qu'un usage
légitime : tu as besoin du document **en entier**, et tu sais pourquoi. L'utiliser
parce que la table des headings est apparue est un contresens — tu viens de
transformer une économie en dépense.

## Établir qu'une chose existe — ou qu'elle n'existe pas

`kb_search` dit **où l'on parle** d'un sujet ; il ne prouve jamais une absence :
zéro résultat peut venir de tes mots, pas du corpus. Quand ta question porte sur
ce qui existe — « y a-t-il une procédure pour cela ? », « quels incidents sous
ce dossier ? », « quels types de documents cette base contient-elle ? » —,
c'est `kb_find` qu'il faut appeler :

```
kb_find  count_by: "type"                          → quels types existent ici
kb_find  type: "Procédure", under: "exploitation"   → total exact ; zéro est une réponse
kb_find  links_to: "<base>:/<chemin>"               → qui cite ce document, entre bases compris
kb_find  query: "sauvegarde", type: "Procédure"     → le texte devient un critère parmi d'autres
kb_find  upstream_changed: true                     → ce qui cite un document d'amont modifié depuis
```

Son total par base est **exact**, même quand la liste est tronquée. Avec
`query`, un document qui ne contient pas *tous* les termes n'est pas retenu :
c'est un filtre, pas une recherche qui se rabat sur une partie des mots. Si un type
ne trouve rien nulle part, l'outil rappelle les types présents : vérifie
l'orthographe avant de conclure. Un zéro obtenu sur le bon type, le bon
sous-chemin, est la preuve d'absence que plusieurs recherches plein texte ne
donnent jamais.

## Si tu ne trouves rien

Dans l'ordre :

1. **Moins de termes.** Trois mots précis valent mieux que huit.
2. **Le vocabulaire du corpus, pas le tien.** Une base sur un produit emploie les
   mots de ce produit ; cherche `réauthentification` si c'est ainsi qu'on y dit
   « reconnexion ».
3. **`kb_governance`** — la section « Organisation du corpus » t'indique comment
   la base est rangée, donc par quoi chercher.
4. **`kb_find`** pour trancher : si le type de document que tu cherches n'existe
   pas à l'endroit attendu, tu le sais en un appel.
5. **La bonne conclusion est parfois « ce n'est pas documenté ».** C'est un
   résultat utile, et c'est exactement le moment de déposer une proposition de
   `type: question`.
