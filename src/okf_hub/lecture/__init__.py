"""Moteur de lecture : un modèle commun de toutes les bases (§ 5 bis, rév. 4.5).

Il sert toutes les surfaces de consultation — `kb_find` côté MCP, l'interface
web côté navigateur — pour que ce que voit l'humain soit ce que lit l'agent.
Le modèle est un cache dérivé du disque, sans persistance (§ 1.1, § 4.4.a) ;
la recherche plein texte reste ripgrep (`search.run_search`, § 10.3).
"""
