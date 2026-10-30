"""Figures SVG calculées côté serveur : graphe des bases, vue `graph` d'une base.

Aucun script (la politique de contenu n'en permet pas) : la lisibilité tient à
la disposition. Les nœuds reliés sont placés sur un cercle, dans l'ordre qui
croise le moins d'arêtes — cherché exhaustivement tant que c'est bon marché ;
les nœuds sans aucune arête vont sur une ligne à part, sous le cercle. Chaque
arête est un arc légèrement courbé vers sa gauche — deux arêtes opposées ne se
superposent donc jamais —, d'épaisseur croissante avec son poids, et son
étiquette est posée *sur* l'arc, dans une pastille, avec une infobulle qui
nomme les deux extrémités ; une étiquette qui en toucherait une autre, ou un
nœud, glisse le long de son arc.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from functools import lru_cache

#: Au-delà, l'ordre exhaustif coûterait trop (8 nœuds : 5 040 ordres) ; on
#: range alors les nœuds par degré décroissant.
ORDRE_EXHAUSTIF_MAX = 8
COURBURE = 0.18
"""Décalage du point de contrôle, en fraction de la longueur de l'arête."""


@dataclass(frozen=True)
class Noeud:
    cle: str
    x: float
    y: float
    isole: bool
    etiquette_x: float = 0.0
    etiquette_y: float = 0.0
    ancrage: str = "middle"
    rayon: float = 30.0
    """`text-anchor` de l'étiquette : posée vers l'extérieur du cercle, là où
    n'arrive aucun arc."""


@dataclass(frozen=True)
class Arc:
    source: str
    cible: str
    chemin: str
    """Attribut `d` d'un `<path>` SVG : un arc quadratique."""
    epaisseur: float
    etiquette_x: float
    etiquette_y: float
    donnees: object


@dataclass(frozen=True)
class Figure:
    largeur: int
    hauteur: int
    rayon: int
    noeuds: tuple[Noeud, ...]
    arcs: tuple[Arc, ...]
    isoles_y: float | None = None
    """Ordonnée de la légende de la ligne des nœuds isolés, s'il y en a."""


def _croisements(ordre: tuple[str, ...], paires: tuple[tuple[str, str], ...]) -> int:
    """Cordes qui se croisent : deux cordes d'un cercle se croisent si et
    seulement si leurs extrémités s'entrelacent."""
    pos = {nom: i for i, nom in enumerate(ordre)}
    cordes = [tuple(sorted((pos[a], pos[b]))) for a, b in paires]
    total = 0
    for (a1, b1), (a2, b2) in itertools.combinations(cordes, 2):
        if len({a1, b1, a2, b2}) == 4 and (a1 < a2 < b1) != (a1 < b2 < b1):
            total += 1
    return total


@lru_cache(maxsize=64)
def ordonner(noms: tuple[str, ...], paires: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Ordre des nœuds sur le cercle qui croise le moins d'arêtes, déterministe."""
    if len(noms) <= 3:
        return noms
    if len(noms) > ORDRE_EXHAUSTIF_MAX:
        degre = {n: sum(n in p for p in paires) for n in noms}
        return tuple(sorted(noms, key=lambda n: (-degre[n], n)))
    premier, reste = noms[0], noms[1:]
    return min(
        ((premier, *p) for p in itertools.permutations(reste)),
        key=lambda ordre: (_croisements(ordre, paires), ordre),
    )


def disposer(
    noms: list[str],
    aretes: list[tuple[str, str, float, object]],
    *,
    rayon_noeud: int = 30,
    ordonne: bool = True,
    rayons: dict[str, float] | None = None,
) -> Figure:
    """`aretes` : (source, cible, poids, données). `ordonne=False` garde l'ordre
    donné (des chemins de documents, que leur ordre regroupe par dossier).
    `rayons` : le rayon de chaque nœud, s'il varie (par exemple selon la taille
    d'une base) ; sinon `rayon_noeud` pour tous."""
    rayons = rayons or {}
    relies = {a for a, b, _, _ in aretes} | {b for a, b, _, _ in aretes}
    sur_cercle = [n for n in noms if n in relies]
    isoles = [n for n in noms if n not in relies]
    paires = tuple(sorted({(a, b) for a, b, _, _ in aretes if a != b}))
    if ordonne:
        sur_cercle = list(ordonner(tuple(sorted(sur_cercle)), paires))

    n = len(sur_cercle)
    centre = 300 if n <= 12 else 300 + 12 * (n - 12)
    rayon = centre - 80 if n > 1 else 0
    largeur = 2 * centre
    noeuds: list[Noeud] = []
    for i, nom in enumerate(sur_cercle):
        angle = -math.pi / 2 + 2 * math.pi * i / max(n, 1)
        x, y = centre + rayon * math.cos(angle), centre + rayon * math.sin(angle)
        noeuds.append(_etiquetee(nom, x, y, math.cos(angle), math.sin(angle), rayons.get(nom, rayon_noeud), n > 1))
    hauteur = largeur if sur_cercle else 0
    isoles_y = None
    if isoles:
        pas = largeur / (len(isoles) + 1)
        isoles_y = hauteur + (10 if sur_cercle else 0)
        y = isoles_y + rayon_noeud + 24
        noeuds += [_etiquetee(nom, pas * (i + 1), y, 0.0, 1.0, rayons.get(nom, rayon_noeud), False, isole=True)
                   for i, nom in enumerate(isoles)]
        hauteur = int(y + rayon_noeud + 36)

    position = {nd.cle: (nd.x, nd.y) for nd in noeuds}
    rayon_de = {nd.cle: nd.rayon for nd in noeuds}
    geometries = [(_courbe(position[source], position[cible], rayon_de[source], rayon_de[cible]), source, cible, poids, donnees)
                  for source, cible, poids, donnees in aretes if source != cible]
    # Les étiquettes des arcs les plus lourds d'abord : elles gardent le milieu
    # de leur arc ; les autres glissent le long du leur si la place est prise.
    obstacles = [(nd.x, nd.y, nd.rayon + 12) for nd in noeuds]
    places: dict[int, tuple[float, float]] = {}
    for i in sorted(range(len(geometries)), key=lambda i: -geometries[i][3]):
        courbe = geometries[i][0]
        places[i] = _place_d_etiquette(courbe, obstacles + [(x, y, ECART_ETIQUETTES) for x, y in places.values()])
    arcs = []
    for i, ((a, c, b), source, cible, poids, donnees) in enumerate(geometries):
        ex, ey = places[i]
        arcs.append(Arc(source, cible, f"M {a[0]:.1f} {a[1]:.1f} Q {c[0]:.1f} {c[1]:.1f} {b[0]:.1f} {b[1]:.1f}",
                        epaisseur_de(poids), round(ex, 1), round(ey, 1), donnees))
    return Figure(int(largeur), int(hauteur), rayon_noeud, tuple(noeuds), tuple(arcs), isoles_y)


#: Distance minimale entre deux étiquettes d'arcs, en unités de la figure.
ECART_ETIQUETTES = 34.0
_POSITIONS_SUR_L_ARC = (0.5, 0.42, 0.58, 0.34, 0.66, 0.27, 0.73)


def epaisseur_de(poids: float) -> float:
    """Épaisseur d'un arc selon son poids, sur une échelle absolue (la même
    d'une figure à l'autre, donc lisible dans une légende) : racine carrée,
    bornée. 6 références : 2,2 ; 50 : 4,5 ; 440 : 11,5."""
    return round(min(14.0, 1.0 + 0.5 * math.sqrt(max(poids, 0))), 2)


def _point(courbe, t: float) -> tuple[float, float]:
    """B(t) d'une courbe quadratique (A, C, B)."""
    (ax, ay), (cx, cy), (bx, by) = courbe
    u = 1 - t
    return u * u * ax + 2 * u * t * cx + t * t * bx, u * u * ay + 2 * u * t * cy + t * t * by


def _place_d_etiquette(courbe, obstacles) -> tuple[float, float]:
    """La première position le long de l'arc, en partant du milieu, qui ne
    touche aucun obstacle ; à défaut, la plus dégagée."""
    meilleure, marge_max = _point(courbe, 0.5), -math.inf
    for t in _POSITIONS_SUR_L_ARC:
        x, y = _point(courbe, t)
        marge = min((math.hypot(x - ox, y - oy) - r for ox, oy, r in obstacles), default=math.inf)
        if marge >= 0:
            return x, y
        if marge > marge_max:
            meilleure, marge_max = (x, y), marge
    return meilleure


def _etiquetee(nom, x, y, dx, dy, rayon_noeud, radial, isole=False) -> Noeud:
    """Le nœud et son étiquette : vers l'extérieur du cercle (`dx`, `dy` : la
    direction depuis le centre), ou dessous pour un nœud hors cercle."""
    if not radial:
        dx, dy = 0.0, 1.0
    marge = rayon_noeud + 8  # le rayon de ce nœud
    ex, ey = x + dx * marge, y + dy * marge
    if dx > 0.35:
        ancrage = "start"
    elif dx < -0.35:
        ancrage = "end"
    else:
        ancrage = "middle"
    # Ligne de base : au-dessous d'un nœud du bas, au-dessus d'un nœud du haut.
    ey += 13 if dy > 0.35 else (-2 if dy < -0.35 else 4)
    return Noeud(nom, round(x, 1), round(y, 1), isole, round(ex, 1), round(ey, 1), ancrage, rayon_noeud)


def _courbe(p1, p2, rayon_source, rayon_cible):
    """(A, C, B) : l'arc quadratique d'une arête, extrémités ramenées au bord
    des nœuds."""
    (x1, y1), (x2, y2) = p1, p2
    dx, dy = x2 - x1, y2 - y1
    longueur = math.hypot(dx, dy) or 1.0
    # Point de contrôle : le milieu, décalé vers la gauche du sens source → cible.
    nx, ny = -dy / longueur, dx / longueur
    cx = (x1 + x2) / 2 + nx * COURBURE * longueur
    cy = (y1 + y2) / 2 + ny * COURBURE * longueur

    # Extrémités ramenées au bord des nœuds, le long des tangentes de l'arc.
    def bord(px, py, vers_x, vers_y, marge):
        vx, vy = vers_x - px, vers_y - py
        norme = math.hypot(vx, vy) or 1.0
        return px + vx / norme * marge, py + vy / norme * marge

    return bord(x1, y1, cx, cy, rayon_source + 2), (cx, cy), bord(x2, y2, cx, cy, rayon_cible + 6)
