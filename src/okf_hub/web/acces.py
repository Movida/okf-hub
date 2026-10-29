"""Accès à l'interface web : configuration, visiteur, bases visibles (§ 5 bis.4).

Deux modes :

* **local** (défaut) — aucune authentification, l'opérateur voit toutes les
  bases ; l'interface n'écoute donc que sur une adresse de bouclage.
* **équipe** — `identity-header` est posé : l'authentification est déléguée à
  un proxy inverse (SSO de l'organisation) qui transmet l'identité dans cet
  en-tête. Seules sont servies les bases que `visibility` ouvre au visiteur ;
  une base absente de la liste ne l'est à personne, une base personnelle
  comprise.

**Confiance dans l'en-tête.** Il n'est cru que sur une connexion venue d'un
proxy de `trusted-proxies` — par défaut le bouclage : un proxy sur la même
machine. Toute autre connexion est refusée, en-tête ou non : sinon un client qui
joindrait le port directement choisirait son identité. Le proxy peut donc vivre
dans un autre conteneur ou sur une autre machine, à condition d'être nommé ; et
l'adresse vérifiée est celle de la connexion, jamais un `X-Forwarded-For`.

La configuration vit dans `hub-config.local.yaml`, jamais versionné, puisqu'elle
nomme les bases de l'opérateur (§ 4.1) :

```yaml
web:
  host: 0.0.0.0                         # hors bouclage : mode équipe seulement
  port: 8077
  identity-header: X-Forwarded-User     # absent : mode local
  groups-header: X-Forwarded-Groups     # facultatif, valeurs séparées par des virgules
  trusted-proxies: [172.18.0.5]         # défaut : 127.0.0.0/8 et ::1
  visibility:
    ma-base: ["*"]                      # tout visiteur authentifié
    mon-autre-base: [equipe-a, lecteur@exemple.org]
```
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8077
TOUS = "*"
CLES = frozenset({"host", "port", "identity-header", "groups-header", "trusted-proxies", "visibility"})
BOUCLAGE = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))

Reseau = ipaddress.IPv4Network | ipaddress.IPv6Network


class ConfigWebInvalide(ValueError):
    pass


@dataclass(frozen=True)
class WebConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    identity_header: str | None = None
    groups_header: str | None = None
    trusted_proxies: tuple[Reseau, ...] = BOUCLAGE
    visibility: Mapping[str, frozenset[str]] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def equipe(self) -> bool:
        return self.identity_header is not None

    def proxy_de_confiance(self, adresse: str | None) -> bool:
        try:
            ip = ipaddress.ip_address(adresse or "")
        except ValueError:
            return False
        return any(ip in reseau for reseau in self.trusted_proxies)

    @staticmethod
    def depuis(brut, *, host: str | None = None, port: int | None = None) -> "WebConfig":
        """Valide la clé `web` ; `host` et `port` viennent de la ligne de commande."""
        if brut is None:
            brut = {}
        if not isinstance(brut, dict):
            raise ConfigWebInvalide("web : correspondance attendue")
        inconnues = set(brut) - CLES
        if inconnues:
            raise ConfigWebInvalide(f"web : clé(s) inconnue(s) {', '.join(sorted(inconnues))}")
        identite = _en_tete(brut, "identity-header")
        groupes = _en_tete(brut, "groups-header")
        if groupes and not identite:
            raise ConfigWebInvalide("web.groups-header n'a de sens qu'avec web.identity-header")

        host = host or brut.get("host", DEFAULT_HOST)
        if not isinstance(host, str) or not host.strip():
            raise ConfigWebInvalide("web.host : adresse attendue")
        if not identite and not _bouclage(host):
            raise ConfigWebInvalide(
                f"web.host « {host} » : en mode local l'interface n'authentifie personne, "
                f"elle n'écoute donc que sur une adresse de bouclage (127.0.0.1, ::1, "
                f"localhost). Pour une équipe : web.identity-header et un proxy inverse."
            )
        port = port if port is not None else brut.get("port", DEFAULT_PORT)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ConfigWebInvalide("web.port : entier entre 1 et 65535 attendu")

        proxys = BOUCLAGE
        if "trusted-proxies" in brut:
            if not identite:
                raise ConfigWebInvalide("web.trusted-proxies n'a de sens qu'en mode équipe (web.identity-header)")
            liste = brut["trusted-proxies"]
            if not isinstance(liste, list) or not liste or not all(isinstance(x, str) for x in liste):
                raise ConfigWebInvalide("web.trusted-proxies : liste non vide d'adresses ou de réseaux attendue")
            try:
                proxys = tuple(ipaddress.ip_network(x.strip(), strict=False) for x in liste)
            except ValueError as exc:
                raise ConfigWebInvalide(f"web.trusted-proxies : {exc}") from exc

        visibilite = brut.get("visibility") or {}
        if not isinstance(visibilite, dict) or not all(
            isinstance(b, str) and isinstance(v, list) and all(isinstance(x, str) for x in v)
            for b, v in visibilite.items()
        ):
            raise ConfigWebInvalide("web.visibility : {base: [groupes ou identités]} attendu")
        if visibilite and not identite:
            raise ConfigWebInvalide(
                "web.visibility n'a de sens qu'en mode équipe (web.identity-header) : "
                "en local, l'opérateur voit toutes les bases"
            )
        return WebConfig(
            host=host.strip(),
            port=port,
            identity_header=identite,
            groups_header=groupes,
            trusted_proxies=proxys,
            visibility=MappingProxyType({b: frozenset(v) for b, v in visibilite.items()}),
        )


def _en_tete(brut: dict, cle: str) -> str | None:
    valeur = brut.get(cle)
    if valeur is None:
        return None
    if not isinstance(valeur, str) or not valeur.strip() or any(c.isspace() for c in valeur.strip()):
        raise ConfigWebInvalide(f"web.{cle} : nom d'en-tête HTTP attendu")
    return valeur.strip()


def _bouclage(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Visiteur:
    """Qui lit : `identite` None en mode local (l'opérateur)."""

    identite: str | None
    groupes: frozenset[str] = frozenset()


class AccesRefuse(Exception):
    """Mode équipe : connexion hors proxy de confiance, ou sans identité."""


def visiteur_de(config: WebConfig, en_tetes: Mapping[str, str], adresse: str | None) -> Visiteur:
    """`adresse` : celle de la connexion (le pair TCP), seule à faire foi."""
    if not config.equipe:
        return Visiteur(None)
    if not config.proxy_de_confiance(adresse):
        raise AccesRefuse(
            "Connexion directe refusée : ce hub ne sert qu'à travers son proxy inverse."
        )
    identite = (en_tetes.get(config.identity_header or "") or "").strip()
    if not identite:
        raise AccesRefuse(
            f"Le hub attend l'identité du visiteur dans l'en-tête {config.identity_header}, "
            f"posé par son proxy inverse."
        )
    groupes = frozenset()
    if config.groups_header:
        brut = en_tetes.get(config.groups_header) or ""
        groupes = frozenset(g.strip() for g in brut.split(",") if g.strip())
    return Visiteur(identite, groupes)


def bases_visibles(config: WebConfig, visiteur: Visiteur, installees: list[str]) -> frozenset[str]:
    """Les bases installées que ce visiteur peut lire.

    En équipe, une base n'est servie que sur une ligne explicite de
    `visibility` qui nomme `*`, l'identité du visiteur ou l'un de ses groupes.
    """
    if not config.equipe:
        return frozenset(installees)
    ouvertes = set()
    for nom in installees:
        autorises = config.visibility.get(nom)
        if not autorises:
            continue
        if TOUS in autorises or visiteur.identite in autorises or visiteur.groupes & autorises:
            ouvertes.add(nom)
    return frozenset(ouvertes)
