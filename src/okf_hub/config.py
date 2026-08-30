"""Configuration du hub (§ 4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_FILENAME = "hub-config.yaml"

DEFAULT_BASES_DIR = "./bases"
DEFAULT_READ_TOC_THRESHOLD = 8192
DEFAULT_LOG_FILE = "./hub.log"

#: Les bases livrées dans `bundles/` sont installées au démarrage si elles
#: manquent. Un opérateur qui veut maîtriser entièrement le contenu de
#: `bases-dir` met `bootstrap-bundles: false`.
DEFAULT_BOOTSTRAP_BUNDLES = True


@dataclass(frozen=True)
class HubConfig:
    hub_root: Path
    bases_dir: Path
    read_toc_threshold: int
    log_file: Path | None
    bootstrap_bundles: bool = DEFAULT_BOOTSTRAP_BUNDLES

    @staticmethod
    def load(hub_root: Path) -> "HubConfig":
        hub_root = hub_root.resolve()
        path = hub_root / CONFIG_FILENAME
        raw: dict = {}
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} : la racine doit être une correspondance YAML")
            raw = loaded

        bases_dir = _resolve(hub_root, raw.get("bases-dir", DEFAULT_BASES_DIR))

        threshold = raw.get("read-toc-threshold", DEFAULT_READ_TOC_THRESHOLD)
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
            raise ValueError(f"{path} : read-toc-threshold doit être un entier positif")

        log_raw = raw.get("log-file", DEFAULT_LOG_FILE)
        log_file = None if log_raw in (None, False, "") else _resolve(hub_root, log_raw)

        bootstrap = raw.get("bootstrap-bundles", DEFAULT_BOOTSTRAP_BUNDLES)
        if not isinstance(bootstrap, bool):
            raise ValueError(f"{path} : bootstrap-bundles doit être un booléen")

        return HubConfig(
            hub_root=hub_root,
            bases_dir=bases_dir,
            read_toc_threshold=threshold,
            log_file=log_file,
            bootstrap_bundles=bootstrap,
        )


def _resolve(hub_root: Path, value) -> Path:
    if not isinstance(value, str):
        raise ValueError(f"chemin de configuration invalide : {value!r}")
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = hub_root / p
    # `resolve()` sans strict : bases-dir peut ne pas exister encore.
    return p.resolve()
