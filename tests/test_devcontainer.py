"""Le devcontainer n'expose du système de fichiers de l'hôte que le hub (§ 4.3).

La spec est catégorique : « montage : le répertoire du hub uniquement. Les outils
ne doivent jamais accéder hors de `bases-dir` ». Un second montage existe
pourtant — le volume nommé qui porte la clé SSH du conteneur, écart assumé et
mesuré en `docs/ARCHITECTURE.md` § 5.3. Ce qui rend cet écart tenable, et qui est
la seule chose que ce test garde, c'est sa **nature** : un volume Docker n'expose
aucun chemin de l'hôte.

Le remplacer un jour par un `type=bind` sur `~/.ssh` de l'hôte — geste tentant,
d'apparence équivalente, et qui rendrait la clé personnelle de l'opérateur
lisible depuis le conteneur — ne se verrait dans aucun test sans celui-ci. C'est
précisément la classe d'erreur que la § 4.3 essaie d'empêcher.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DEVCONTAINER = RACINE / ".devcontainer" / "devcontainer.json"


def charger() -> dict:
    """Lit le JSONC : seules les lignes entièrement en commentaire sont retirées.

    Un `//` en milieu de ligne peut appartenir à une valeur (une URL, un chemin) ;
    une règle plus large casserait le fichier au lieu de le lire.
    """
    lignes = [
        "" if re.match(r"^\s*//", ligne) else ligne
        for ligne in DEVCONTAINER.read_text(encoding="utf-8").splitlines()
    ]
    return json.loads("\n".join(lignes))


def test_le_workspace_est_le_seul_chemin_de_l_hote_monte():
    cfg = charger()
    assert "${localWorkspaceFolder}" in cfg["workspaceMount"]
    assert cfg["workspaceFolder"] == "/workspaces/okf-hub"


def test_les_montages_du_devcontainer_restent_confines():
    cfg = charger()
    for montage in cfg.get("mounts", []):
        champs = dict(
            morceau.split("=", 1) for morceau in montage.split(",") if "=" in morceau
        )
        assert champs.get("type") == "volume", (
            f"montage non confiné : {montage!r}. Le § 4.3 n'autorise que le "
            "répertoire du hub ; le seul écart recensé (ARCHITECTURE § 5.3) porte "
            "sur un volume nommé, qui n'expose aucun chemin de l'hôte. Un "
            "`type=bind` ici exposerait l'hôte — et, sur ~/.ssh, la clé "
            "personnelle de l'opérateur."
        )
        assert not champs["source"].startswith(("/", "~", "$")), (
            f"montage non confiné : {montage!r}. La source d'un volume est un "
            "nom, pas un chemin."
        )
