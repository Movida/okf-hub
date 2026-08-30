"""Fixtures : fabrique de hub et de bundles jetables."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from okf_hub.config import HubConfig  # noqa: E402
from okf_hub.registry import Registry  # noqa: E402

HUB_ROOT = Path(__file__).resolve().parents[1]


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@local",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, check=True, text=True,
    )
    return out.stdout


DEFAULT_MANIFEST = {
    "bundle-spec": "0.1",
    "name": "base-test",
    "title": "Base de test",
    "description": "Corpus jetable servant aux tests du hub.",
    "governance": {"rules": "./GOVERNANCE.md"},
}


class BundleBuilder:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def manifest(self, **overrides) -> "BundleBuilder":
        data = {**DEFAULT_MANIFEST}
        for key, value in overrides.items():
            key = key.replace("_", "-")
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        (self.root / "okf-bundle.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return self

    def raw_manifest(self, text: str) -> "BundleBuilder":
        (self.root / "okf-bundle.yaml").write_text(text, encoding="utf-8")
        return self

    def governance(self, text: str = "# Gouvernance\n\nRègles de test.\n") -> "BundleBuilder":
        (self.root / "GOVERNANCE.md").write_text(text, encoding="utf-8")
        return self

    def schema(self, text: str = "required:\n  - name: title\n    type: string\n") -> "BundleBuilder":
        (self.root / "schema.yaml").write_text(text, encoding="utf-8")
        return self

    def doc(self, rel: str, text: str, corpus: str = "knowledge") -> "BundleBuilder":
        path = self.root / corpus / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return self

    def init_git(self) -> "BundleBuilder":
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        return self


@pytest.fixture
def hub(tmp_path: Path):
    """Retourne (hub_root, bases_dir) avec un hub-config.yaml minimal."""
    hub_root = tmp_path / "hub"
    bases = hub_root / "bases"
    bases.mkdir(parents=True)
    (hub_root / "hub-config.yaml").write_text(
        yaml.safe_dump(
            {"bases-dir": "./bases", "read-toc-threshold": 8192, "log-file": "./hub.log"}
        ),
        encoding="utf-8",
    )
    return hub_root, bases


@pytest.fixture
def make_bundle(hub):
    _, bases = hub

    def _make(
        dir_name: str = "base-test",
        *,
        git_init: bool = True,
        create_corpus: bool = True,
        **manifest,
    ) -> BundleBuilder:
        b = BundleBuilder(bases / dir_name)
        b.manifest(**manifest).governance()
        if create_corpus:
            b.doc(
                "exemple.md",
                "---\ntype: Reference\ntitle: Document d'exemple\n---\n\n"
                "# Document d'exemple\n\nContenu de démonstration.\n",
                corpus=manifest.get("corpus-dir", manifest.get("corpus_dir", "knowledge")),
            )
        if git_init:
            b.init_git()
        return b

    return _make


@pytest.fixture
def registry(hub):
    hub_root, _ = hub
    config = HubConfig.load(hub_root)
    reg = Registry(config)
    reg.scan()
    return reg
