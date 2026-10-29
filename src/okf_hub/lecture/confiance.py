"""Confiance et fraîcheur d'un document, lues dans son frontmatter (§ 5 bis.1).

Fonctions pures : elles ne lisent que le frontmatter, jamais le disque ni git.
La date du dernier commit, dernier recours de la fraîcheur, est fournie par
l'appelant (`modele.Lecteur`).

Deux dialectes coexistent sur un même hub : les familles OKF 0.2 (`verified`,
`status`, `stale_after`, `generated`) et la provenance déclarée par le profil
d'une base. Le moteur affiche l'un ou l'autre selon ce que porte le document,
sans inventer de palier pour qui n'en déclare pas.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Mapping

UTC = dt.timezone.utc

#: Origines possibles de la date de fraîcheur, dans l'ordre où elles sont
#: cherchées : la plus récente vérification, la production du contenu, la
#: plus récente source, le dernier commit, et à défaut le fichier lui-même.
VERIFIED = "verified"
GENERATED = "generated"
SOURCES = "sources"
COMMIT = "commit"
FICHIER = "fichier"


def instant(valeur) -> dt.datetime | None:
    """Une valeur de frontmatter lue comme instant UTC, ou None.

    PyYAML rend un horodatage non quoté en `datetime` (naïf s'il n'a pas de
    décalage), une date seule en `date`, un horodatage quoté en chaîne : les
    trois formes se rencontrent dans les corpus réels.
    """
    if isinstance(valeur, dt.datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=UTC)
    if isinstance(valeur, dt.date):
        return dt.datetime(valeur.year, valeur.month, valeur.day, tzinfo=UTC)
    if isinstance(valeur, str) and valeur.strip():
        texte = valeur.strip()
        try:
            lu = dt.datetime.fromisoformat(texte.replace("Z", "+00:00"))
        except ValueError:
            return None
        return lu if lu.tzinfo else lu.replace(tzinfo=UTC)
    return None


def _entrees(valeur) -> list[Mapping]:
    """`verified` et `sources` : une liste de correspondances ; un mapping seul
    vaut liste à un élément (OKF § 5.2)."""
    if isinstance(valeur, Mapping):
        return [valeur]
    if isinstance(valeur, list):
        return [e for e in valeur if isinstance(e, Mapping)]
    return []


def _plus_recent(valeurs) -> dt.datetime | None:
    dates = [d for d in (instant(v) for v in valeurs) if d is not None]
    return max(dates) if dates else None


def date_declaree(frontmatter: Mapping) -> tuple[dt.datetime, str] | None:
    """(date, origine) de fraîcheur portée par le frontmatter, ou None."""
    verifie = _plus_recent(e.get("at") for e in _entrees(frontmatter.get("verified")))
    if verifie is not None:
        return verifie, VERIFIED
    genere = frontmatter.get("generated")
    if isinstance(genere, Mapping) and (at := instant(genere.get("at"))) is not None:
        return at, GENERATED
    source = _plus_recent(e.get("last_modified") for e in _entrees(frontmatter.get("sources")))
    if source is not None:
        return source, SOURCES
    return None


@dataclass(frozen=True)
class Confiance:
    palier: str | None
    """`humain` si une vérification est signée `human:` (OKF § 5.3, le palier le
    plus élevé), `machine` si elle est signée autrement, None sans vérification."""
    statut: str | None
    """`status` OKF s'il ne vaut pas `stable` (absent vaut `stable`)."""
    stale_after: dt.datetime | None
    perime: bool
    genere: bool
    """`generated.by: process:…` : un agrégat produit par un script."""


def confiance(frontmatter: Mapping, maintenant: dt.datetime | None = None) -> Confiance:
    maintenant = maintenant or dt.datetime.now(UTC)
    signataires = [str(e.get("by") or "") for e in _entrees(frontmatter.get("verified"))]
    if any(s.startswith("human:") for s in signataires):
        palier = "humain"
    elif signataires:
        palier = "machine"
    else:
        palier = None
    statut = frontmatter.get("status")
    statut = str(statut).strip() if statut is not None else None
    if statut in ("", "stable"):
        statut = None
    stale_after = instant(frontmatter.get("stale_after"))
    genere = frontmatter.get("generated")
    par = str(genere.get("by") or "") if isinstance(genere, Mapping) else ""
    return Confiance(
        palier=palier,
        statut=statut,
        stale_after=stale_after,
        perime=stale_after is not None and stale_after <= maintenant,
        genere=par.startswith("process:"),
    )


def mois_avant(instant_: dt.datetime, mois: int) -> dt.datetime:
    """Le même jour `mois` mois plus tôt, ramené au dernier jour du mois si besoin."""
    total = instant_.year * 12 + (instant_.month - 1) - mois
    annee, mois0 = divmod(total, 12)
    jour = min(instant_.day, _jours_du_mois(annee, mois0 + 1))
    return instant_.replace(year=annee, month=mois0 + 1, day=jour)


def _jours_du_mois(annee: int, mois: int) -> int:
    suivant = dt.date(annee + (mois == 12), mois % 12 + 1, 1)
    return (suivant - dt.timedelta(days=1)).day
