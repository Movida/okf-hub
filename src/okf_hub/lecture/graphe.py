"""Graphe des bases : `cites` déclarés, références observées (§ 3.3, § 3.6, rév. 4.5).

Le manifeste déclare les bases qu'une base peut citer ; le corps des documents
dit lesquelles elle cite réellement. Le moteur confronte les deux et signale :

* un **cycle** dans les `cites` déclarés (« l'amont ignore l'aval ») ;
* une base nommée dans `cites` qui n'est pas installée ;
* une référence vers une base absente de `cites`, pour une base qui en déclare.

Ce sont des **avertissements** : aucun bundle n'est invalidé pour cela. Une base
qui ne déclare pas `cites` ne reçoit aucun avertissement de référence — la
déclaration est volontaire.

L'analyse porte sur un **périmètre** de bases, celles qu'un lecteur peut voir :
une référence vers une base hors du périmètre est comptée sous son nom (il est
écrit dans le texte), sans dire si la base est installée, ce qui révélerait
l'existence d'une base fermée.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..registry import Registry
from .liens import INTERBASE, INTERNE
from .modele import BASE_ABSENTE, CASSE, Lecteur, resoudre


@dataclass(frozen=True)
class Arete:
    source: str
    cible: str
    declaree: bool
    references: int


@dataclass(frozen=True)
class Graphe:
    bases: tuple[str, ...]
    aretes: tuple[Arete, ...]
    cycles: tuple[tuple[str, ...], ...]
    cites_inconnues: tuple[tuple[str, str], ...]
    """(base, nom cité) : nommée dans `cites`, hors du périmètre."""
    hors_cites: tuple[tuple[str, str, int], ...]
    """(base, base citée, références) : citée sans être déclarée dans `cites`."""
    hors_perimetre: tuple[tuple[str, str, int], ...]
    """(base, nom, références) : références vers une base hors du périmètre."""
    cassees: dict[str, int]
    """Liens internes ou entre bases du périmètre dont la cible n'existe pas."""

    @property
    def avertissements(self) -> list[str]:
        out = [f"cycle dans les cites déclarés : {' → '.join(c + (c[0],))}" for c in self.cycles]
        out += [f"{b} déclare citer {n}, qui n'est pas installée" for b, n in self.cites_inconnues]
        out += [
            f"{b} cite {c} ({n} référence(s)) sans le déclarer dans cites"
            for b, c, n in self.hors_cites
        ]
        return out


def cycles(cites: dict[str, tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Composantes fortement connexes de plus d'une base, membres triés.

    Quelques bases par hub : une fermeture par parcours suffit, et donne un
    résultat déterministe.
    """
    def atteignables(depart: str) -> set[str]:
        vus, pile = set(), [depart]
        while pile:
            for suivant in cites.get(pile.pop(), ()):
                if suivant in cites and suivant not in vus:
                    vus.add(suivant)
                    pile.append(suivant)
        return vus

    acces = {b: atteignables(b) for b in cites}
    composantes = set()
    for b in cites:
        membres = tuple(sorted({b} | {a for a in acces[b] if b in acces[a]}))
        if len(membres) > 1:
            composantes.add(membres)
    return sorted(composantes)


def declarations(registry: Registry, perimetre: list[str]) -> dict[str, tuple[str, ...]]:
    """`cites` déclarés par les bases du périmètre qui en déclarent."""
    out = {}
    for nom in perimetre:
        cites = registry.get(nom).manifest.cites
        if cites is not None:
            out[nom] = cites
    return out


def analyser(lecteur: Lecteur, perimetre: list[str], *, references: bool = True) -> Graphe:
    """Le graphe du périmètre. `references=False` : manifestes seuls, sans charger
    aucun corpus — ce que `kb_hub_rescan` peut se permettre à chaque appel."""
    perimetre = sorted(perimetre)
    dans = set(perimetre)
    declares = declarations(lecteur.registry, perimetre)
    observees: Counter = Counter()
    vers_dehors: Counter = Counter()
    cassees: Counter = Counter()
    if references:
        modeles = lecteur.modeles(perimetre)
        for nom, modele in modeles.items():
            for doc in modele.docs.values():
                for lien in doc.liens:
                    cible = lien.cible
                    if cible.sorte == INTERBASE and cible.base not in dans:
                        vers_dehors[(nom, cible.base)] += 1
                        continue
                    if cible.sorte not in (INTERNE, INTERBASE):
                        continue
                    resolution = resoudre(cible, modeles)
                    if resolution.sorte in (CASSE, BASE_ABSENTE):
                        cassees[nom] += 1
                    if cible.sorte == INTERBASE and cible.base != nom:
                        observees[(nom, cible.base)] += 1

    paires = set(observees) | {(b, c) for b, cs in declares.items() for c in cs if c in dans}
    aretes = tuple(
        Arete(b, c, c in declares.get(b, ()), observees.get((b, c), 0)) for b, c in sorted(paires)
    )
    return Graphe(
        bases=tuple(perimetre),
        aretes=aretes,
        cycles=tuple(cycles({b: tuple(c for c in cs if c in dans) for b, cs in declares.items()})),
        cites_inconnues=tuple(
            (b, c) for b, cs in sorted(declares.items()) for c in cs if c not in dans
        ),
        hors_cites=tuple(
            (b, c, n) for (b, c), n in sorted((observees + vers_dehors).items())
            if b in declares and c not in declares[b]
        ),
        hors_perimetre=tuple((b, c, n) for (b, c), n in sorted(vers_dehors.items())),
        cassees=dict(cassees),
    )
