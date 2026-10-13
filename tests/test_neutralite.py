"""Le dépôt du hub est public, ses bases livrées publiques aussi ; les bases d'un
opérateur ne le sont pas (spec rév. 4.4).

Aucun fichier versionné du hub, ni d'une base publique installée, ne cite un
terme privé : nom ou corpus d'une base privée installée, ou `private-terms` de
`hub-config.local.yaml`. Pour une base publique, les commits pas encore poussés
sont examinés aussi : c'est la poussée qui publie. C'est arrivé : six semaines
de leçons de revue avaient fait entrer dans les skills, la documentation et les
tests des noms de bases, d'objets et d'environnements privés, et des sessions
avaient déposé les mêmes noms dans `okf-hub-feedback`.

La liste des termes n'est jamais écrite ici — elle publierait ce qu'elle
protège : `okf_hub.confidentialite` la déduit de la machine. En CI, `bases/` est
vide et le test est sauté ; sur le poste de l'opérateur, les hooks de
`.githooks/` font le même contrôle à chaque commit et à chaque poussée.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import HUB_ROOT

from okf_hub import confidentialite
from okf_hub.config import HubConfig
from okf_hub.registry import Registry


@pytest.fixture(scope="module")
def registre():
    reg = Registry(HubConfig.load(HUB_ROOT))
    reg.scan()
    return reg


@pytest.fixture(scope="module")
def rx(registre):
    rx = confidentialite.motif(confidentialite.termes_prives(registre))
    if rx is None:
        pytest.skip("aucune base privée installée : rien à protéger sur cette machine")
    return rx


def _git(depot: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(depot), *args], capture_output=True, check=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


def _fuites_versionnees(depot: Path, rx) -> list[str]:
    fuites = []
    for rel in filter(None, _git(depot, "ls-files", "-z").split("\0")):
        chemin = depot / rel
        try:
            texte = chemin.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, IsADirectoryError):
            continue
        for n, ligne in enumerate([rel, *texte.splitlines()]):
            for terme in confidentialite.trouver(rx, ligne):
                fuites.append(f"{depot.name}/{rel}:{n}: « {terme} »")
    return fuites


MESSAGE = (
    "Ce dépôt est public : ces fichiers versionnés citent une base privée ou un "
    "terme déclaré privé. Une règle propre à une base va dans son AGENTS.md ; un "
    "exemple prend un nom neutre (`produit-x`, `installation-x`…).\n"
)


def test_aucun_fichier_versionne_du_hub_ne_cite_un_terme_prive(rx):
    fuites = _fuites_versionnees(HUB_ROOT, rx)
    assert not fuites, MESSAGE + "\n".join(fuites[:50])


def test_aucune_base_publique_ne_cite_un_terme_prive(registre, rx):
    publiques = [
        registre.get(n) for n in sorted(confidentialite.bases_publiques(registre.config))
        if n in registre.bases
    ]
    if not publiques:
        pytest.skip("aucune base publique installée")
    fuites = []
    for base in publiques:
        fuites += _fuites_versionnees(base.root, rx)
        amont = subprocess.run(
            ["git", "-C", str(base.root), "rev-parse", "--verify", "-q", "@{u}"],
            capture_output=True, text=True,
        ).stdout.strip()
        if amont:
            journal = _git(base.root, "log", "-p", "--no-color", "--format=%an <%ae>%n%B", "@{u}..HEAD")
            for ligne in journal.splitlines():
                if ligne.startswith("-"):
                    continue
                for terme in confidentialite.trouver(rx, ligne):
                    fuites.append(f"{base.name} (commit non poussé) : « {terme} »")
    assert not fuites, MESSAGE + "\n".join(dict.fromkeys(fuites[:50]))
