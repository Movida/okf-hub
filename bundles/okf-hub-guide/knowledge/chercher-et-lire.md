---
type: Procédure
title: Chercher et lire dans une base, sans gaspiller de contexte
description: >
  Les séquences d'appels, le choix d'une base, et les mécanismes de recherche et
  de lecture qu'il faut comprendre pour ne pas rapatrier des documents entiers.
tags: [kb_list, kb_search, kb_read]
applies-to: "rév. 4.1"
generated: { by: "claude-code/opus-5", at: 2026-08-30T00:00:00Z }
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
nombre de documents, des propositions en attente, ou quand une base vient d'être
importée.

## Choisir la base

Lis la **description** de chaque base : elle est rédigée pour ça, elle dit ce
qu'on y trouve. Si aucune ne couvre ton sujet, ne force pas la plus proche : la
bonne réponse est qu'il n'y a pas de base pour ça. Signale-le plutôt à l'humain.

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

**Ce que la recherche n'est pas** : un moyen de compter. Un mot cité entre
backticks dans un document de conventions sera trouvé comme s'il était une vraie
occurrence. Elle sert à trouver, jamais à dénombrer.

## Lire une section, pas un document

Chaque extrait retourné par la recherche porte, après `§`, le titre de la
section d'où il vient. **Reporte-le tel quel** dans le paramètre `section` de
`kb_read` : tu obtiens la section entière en un seul appel, sans rapatrier le
document.

```
kb_search  → "  L120-124 § reconnexion sso"
kb_read    → section: "reconnexion sso"
```

Au-delà d'une certaine taille, un document lu **sans** section ne renvoie pas son
contenu mais sa **table des headings**. Ce n'est pas une erreur à contourner :
c'est le mécanisme qui te force à cibler. Choisis un heading dans la table et
rappelle la lecture avec.

Le paramètre qui force le document entier existe, mais il n'a qu'un usage
légitime : tu as besoin du document **en entier**, et tu sais pourquoi. L'utiliser
parce que la table des headings est apparue est un contresens — tu viens de
transformer une économie en dépense.

## Si tu ne trouves rien

Dans l'ordre :

1. **Moins de termes.** Trois mots précis valent mieux que huit.
2. **Le vocabulaire du corpus, pas le tien.** Une base sur un produit emploie les
   mots de ce produit ; cherche `réauthentification` si c'est ainsi qu'on y dit
   « reconnexion ».
3. **`kb_governance`** — la section « Organisation du corpus » t'indique comment
   la base est rangée, donc par quoi chercher.
4. **La bonne conclusion est parfois « ce n'est pas documenté ».** C'est un
   résultat utile, et c'est exactement le moment de déposer une proposition de
   `type: question`.
