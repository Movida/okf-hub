"""Le dépôt du hub est public ; les bases d'un opérateur ne le sont pas.

Aucun fichier versionné du hub ne nomme une base installée localement qui n'est
pas livrée avec lui (`bundles/`), ni son répertoire de corpus, ni un terme que
l'opérateur déclare privé (`private-terms` dans `hub-config.local.yaml`, jamais
versionné). C'est arrivé : six semaines de leçons de revue avaient fait entrer
dans les skills, la documentation et les tests des noms de bases, d'objets et
d'environnements privés.

La liste à surveiller n'est jamais écrite ici — elle publierait ce qu'elle
protège : elle se déduit des bases présentes sur la machine. En CI, `bases/` est
vide et le test est sauté ; c'est sur le poste de l'opérateur qu'il protège,
avant le commit.
"""

from __future__ import annotations

import re
import subprocess

import pytest
import yaml

from conftest import HUB_ROOT

from okf_hub.config import LOCAL_CONFIG_FILENAME
from okf_hub.manifest import ManifestError, load_manifest

BASES_DIR = HUB_ROOT / "bases"
BUNDLES_DIR = HUB_ROOT / "bundles"


def _termes_prives() -> set[str]:
    livrees = {p.name for p in BUNDLES_DIR.iterdir() if p.is_dir()}
    termes: set[str] = set()
    for racine in sorted(BASES_DIR.iterdir()) if BASES_DIR.is_dir() else []:
        if not racine.is_dir() or racine.name.startswith(".") or racine.name in livrees:
            continue
        termes.add(racine.name)
        try:
            manifeste = load_manifest(racine)
        except ManifestError:
            continue
        if manifeste.name not in livrees:
            termes |= {manifeste.name, manifeste.corpus_dir.name}
    local = HUB_ROOT / LOCAL_CONFIG_FILENAME
    if local.is_file():
        termes |= set((yaml.safe_load(local.read_text(encoding="utf-8")) or {}).get("private-terms", []))
    # Un nom de corpus générique (`knowledge`) n'est pas un nom propre.
    return {t for t in termes if t and t != "knowledge"}


def test_aucun_fichier_versionne_ne_nomme_une_base_privee():
    termes = _termes_prives()
    if not termes:
        pytest.skip("aucune base privée installée : rien à protéger sur cette machine")

    motif = re.compile(
        r"(?<![\w-])(" + "|".join(re.escape(t) for t in sorted(termes, key=len, reverse=True)) + r")(?![\w-])",
        re.IGNORECASE,
    )
    suivis = subprocess.run(
        ["git", "-C", str(HUB_ROOT), "ls-files", "-z"], capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")

    fuites = []
    for rel in filter(None, suivis):
        chemin = HUB_ROOT / rel
        if not chemin.is_file():
            continue
        try:
            texte = chemin.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, ligne in enumerate(texte.splitlines(), 1):
            for m in motif.finditer(ligne):
                fuites.append(f"{rel}:{n}: « {m.group(1)} »")

    assert not fuites, (
        "Le dépôt du hub est public : ces fichiers versionnés nomment une base privée "
        "ou un terme déclaré privé. Une règle propre à une base va dans son AGENTS.md ; "
        "un exemple prend un nom neutre (`produit-x`, `installation-x`…).\n"
        + "\n".join(fuites[:50])
    )
