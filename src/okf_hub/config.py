"""Configuration du hub (§ 4.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

CONFIG_FILENAME = "hub-config.yaml"

#: Surcharge propre à la machine, jamais versionnée (`.gitignore`). Ses clés
#: remplacent celles de `hub-config.yaml`. C'est là que vit `trusted-checks` :
#: approuver l'exécution du code d'une base est une décision de l'opérateur de
#: CE hub, et nommer ses bases dans un fichier versionné les publierait avec le
#: dépôt du hub.
LOCAL_CONFIG_FILENAME = "hub-config.local.yaml"

DEFAULT_BASES_DIR = "./bases"
DEFAULT_READ_TOC_THRESHOLD = 8192
DEFAULT_LOG_FILE = "./hub.log"

#: Les bases livrées dans `bundles/` sont installées au démarrage si elles
#: manquent. Un opérateur qui veut maîtriser entièrement le contenu de
#: `bases-dir` met `bootstrap-bundles: false`.
DEFAULT_BOOTSTRAP_BUNDLES = True

#: Synchronisation fast-forward-only avec le remote de chaque base installée,
#: au démarrage (§ 4.5). Un opérateur qui gère ses pulls lui-même met
#: `sync-on-start: false`.
DEFAULT_SYNC_ON_START = True

#: Profils de configuration prédéfinis. Chaque profil définit des valeurs par
#: défaut pour les cinq paramètres configurables. Un opérateur peut soit
#: choisir un profil via le champ `profile` de hub-config.yaml, soit laisser
#: le profil `solo` par défaut (comportement actuel), soit surcharger
#: individuellement les valeurs d'un profil.
PROFILES = {
    "solo": {
        "bases-dir": DEFAULT_BASES_DIR,
        "read-toc-threshold": DEFAULT_READ_TOC_THRESHOLD,
        "log-file": DEFAULT_LOG_FILE,
        "bootstrap-bundles": DEFAULT_BOOTSTRAP_BUNDLES,
        "sync-on-start": DEFAULT_SYNC_ON_START,
    },
    "dev": {
        "bases-dir": DEFAULT_BASES_DIR,
        "read-toc-threshold": DEFAULT_READ_TOC_THRESHOLD,
        "log-file": DEFAULT_LOG_FILE,
        "bootstrap-bundles": False,  # dev : pas de bootstrap auto
        "sync-on-start": False,      # dev : pas de sync auto
    },
    "ci": {
        "bases-dir": DEFAULT_BASES_DIR,
        "read-toc-threshold": 16384,  # CI : seuil plus élevé
        "log-file": None,             # CI : pas de log fichier
        "bootstrap-bundles": DEFAULT_BOOTSTRAP_BUNDLES,
        "sync-on-start": DEFAULT_SYNC_ON_START,
    },
}

DEFAULT_PROFILE = "solo"

#: Remote vers lequel `okf-hub publish` pousse une base partagée (§ 4.6.c).
DEFAULT_PUBLISH_REMOTE = "replica"

#: Périmètres d'une instance (§ 4.6.b, rév. 4.8). Sans périmètre, une instance
#: voit toutes les bases, comme avant la rév. 4.8.
SCOPE_SHARED = "shared"
SCOPE_PRIVATE = "private"
SCOPES = (SCOPE_SHARED, SCOPE_PRIVATE)


@dataclass(frozen=True)
class HubConfig:
    hub_root: Path
    bases_dir: Path
    read_toc_threshold: int
    log_file: Path | None
    bootstrap_bundles: bool = DEFAULT_BOOTSTRAP_BUNDLES
    sync_on_start: bool = DEFAULT_SYNC_ON_START
    trusted_checks: frozenset[str] = frozenset()
    """Bases dont `okf-review` peut lancer les `governance.checks` (§ 8)."""
    private_terms: frozenset[str] = frozenset()
    """Termes qu'aucun dépôt public ne doit contenir, en plus des noms des bases
    privées installées (`confidentialite`, rév. 4.4)."""
    public_bases: frozenset[str] = frozenset()
    """Bases publiques en plus des bases livrées avec un dépôt canonique."""
    shared_bases: frozenset[str] = frozenset()
    """Bases partagées, par `name` (§ 4.6.a, rév. 4.8). Absente = privée. Le
    niveau vit ici, dans la configuration de l'opérateur, et jamais dans la
    base : le gestionnaire écrit dans les bases, il ne doit pas pouvoir en
    partager une."""
    publish_remote: str = DEFAULT_PUBLISH_REMOTE
    """Remote git de la réplique, dans chaque base partagée (§ 4.6.c)."""
    secret_patterns: tuple[str, ...] = ()
    """Expressions de secrets propres à l'opérateur, en plus de celles du
    détecteur (§ 4.6.c). Compilées au chargement : une expression fausse est
    une configuration invalide, pas une garde muette."""
    scope: str | None = None
    """Périmètre de l'instance (§ 4.6.b) : `shared`, `private` ou aucun. Posé
    par la ligne de commande (`okf-hub --scope`), jamais par un fichier : deux
    instances d'un même hub peuvent avoir deux périmètres."""
    replica: object = field(default=None, hash=False, compare=False)
    """Clé `replica` telle quelle (§ 4.6.d, rév. 4.8) : validée par
    `okf-hub replica` seul, comme `web`."""
    web: object = field(default=None, hash=False, compare=False)
    """Clé `web` telle quelle (§ 4.1, rév. 4.5) : validée par `okf-hub web` seul
    (`web.acces.WebConfig`), pour qu'une section mal écrite n'empêche pas le
    serveur MCP de démarrer."""

    @staticmethod
    def load(hub_root: Path) -> "HubConfig":
        hub_root = hub_root.resolve()
        path = hub_root / CONFIG_FILENAME
        raw = {**_read(path), **_read(hub_root / LOCAL_CONFIG_FILENAME)}

        # Charger les valeurs par défaut du profil spécifié (ou "solo" par défaut).
        profile_name = raw.get("profile", DEFAULT_PROFILE)
        if profile_name not in PROFILES:
            raise ValueError(
                f"{path} : profil inconnu '{profile_name}'. "
                f"Profils disponibles : {', '.join(sorted(PROFILES.keys()))}"
            )
        profile_defaults = PROFILES[profile_name]

        # Les valeurs explicites de hub-config.yaml surchargent le profil.
        bases_dir = _resolve(hub_root, raw.get("bases-dir", profile_defaults["bases-dir"]))

        threshold = raw.get("read-toc-threshold", profile_defaults["read-toc-threshold"])
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
            raise ValueError(f"{path} : read-toc-threshold doit être un entier positif")

        log_raw = raw.get("log-file", profile_defaults["log-file"])
        log_file = None if log_raw in (None, False, "") else _resolve(hub_root, log_raw)

        bootstrap = raw.get("bootstrap-bundles", profile_defaults["bootstrap-bundles"])
        if not isinstance(bootstrap, bool):
            raise ValueError(f"{path} : bootstrap-bundles doit être un booléen")

        sync_on_start = raw.get("sync-on-start", profile_defaults["sync-on-start"])
        if not isinstance(sync_on_start, bool):
            raise ValueError(f"{path} : sync-on-start doit être un booléen")

        trusted = _liste(raw, "trusted-checks", path)

        return HubConfig(
            hub_root=hub_root,
            bases_dir=bases_dir,
            read_toc_threshold=threshold,
            log_file=log_file,
            bootstrap_bundles=bootstrap,
            sync_on_start=sync_on_start,
            trusted_checks=trusted,
            private_terms=_liste(raw, "private-terms", path),
            public_bases=_liste(raw, "public-bases", path),
            shared_bases=_liste(raw, "shared-bases", path),
            publish_remote=_remote(raw, path),
            secret_patterns=_motifs(raw, path),
            replica=raw.get("replica"),
            web=raw.get("web"),
        )

    def with_scope(self, scope: str | None) -> "HubConfig":
        """La même configuration, limitée à un périmètre (§ 4.6.b)."""
        if scope is not None and scope not in SCOPES:
            raise ValueError(f"périmètre inconnu '{scope}' (attendu : {', '.join(SCOPES)})")
        return replace(self, scope=scope)

    def in_scope(self, name: str) -> bool:
        """La base `name` appartient-elle au périmètre de l'instance ?"""
        if self.scope is None:
            return True
        return (name in self.shared_bases) == (self.scope == SCOPE_SHARED)


def _liste(raw: dict, cle: str, path: Path) -> frozenset[str]:
    valeur = raw.get(cle) or []
    if not isinstance(valeur, list) or not all(isinstance(n, str) for n in valeur):
        raise ValueError(f"{path} : {cle} doit être une liste de chaînes")
    return frozenset(valeur)


def _remote(raw: dict, path: Path) -> str:
    valeur = raw.get("publish-remote", DEFAULT_PUBLISH_REMOTE)
    if not isinstance(valeur, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", valeur):
        raise ValueError(f"{path} : publish-remote doit être un nom de remote git")
    return valeur


def _motifs(raw: dict, path: Path) -> tuple[str, ...]:
    valeur = raw.get("secret-patterns") or []
    if not isinstance(valeur, list) or not all(isinstance(m, str) and m for m in valeur):
        raise ValueError(f"{path} : secret-patterns doit être une liste d'expressions")
    for m in valeur:
        try:
            re.compile(m)
        except re.error as exc:
            raise ValueError(f"{path} : secret-patterns, expression invalide ({exc})") from None
    return tuple(valeur)


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} : la racine doit être une correspondance YAML")
    return loaded


def _resolve(hub_root: Path, value) -> Path:
    if not isinstance(value, str):
        raise ValueError(f"chemin de configuration invalide : {value!r}")
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = hub_root / p
    # `resolve()` sans strict : bases-dir peut ne pas exister encore.
    return p.resolve()
