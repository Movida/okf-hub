# okf-hub-guide

Mode d'emploi du **OKF Bundle Hub**, destiné aux sessions qui s'y connectent.

Une session consommatrice ne voit ni le `README.md` du hub, ni sa documentation
d'API, ni son `CLAUDE.md` : elle ne dispose que des outils MCP et de leurs
descriptions. Cette base comble cet écart — et seulement lui.

## Ce qu'on y trouve

- **Les rôles** et la frontière de confiance à l'écriture : qui peut modifier un
  corpus, par quel moyen, et pourquoi les autres ne le peuvent pas.
- **Chercher et lire** : les séquences d'appels, les mécanismes de la recherche,
  le chaînage d'un extrait vers une section.
- **Proposer** : ce qui fait qu'une proposition est intégrée plutôt que rejetée.
- **Le cycle de vie d'une base** : créer, déployer, alimenter, réviser, retirer,
  avec à chaque étape le rôle et le moyen.

## Ce qu'on n'y trouve pas, délibérément

**Les schémas d'outils** — noms de paramètres, types, bornes. C'est interdit par
la golden rule 1 de sa gouvernance, pas seulement déconseillé.

La source de vérité est la description de chaque outil, dérivée du code. Une
copie ici serait la seule qu'aucun test ne garde, et elle dériverait — d'autant
qu'une base se met à jour par le circuit de propositions, alors qu'une référence
d'API doit bouger en verrou avec le code.

Ce corpus décrit des **procédures et des mécanismes**. Ils changent lentement.
