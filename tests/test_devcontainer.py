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


# --- La clé du conteneur reste bornée à un dépôt ------------------------------
#
# `devcontainer.json` et ARCHITECTURE § 5.3 affirment tous deux que la clé du
# conteneur est une deploy key d'un seul dépôt, « pas une clé personnelle, dont
# la compromission ouvrirait tous les dépôts du compte ». Cette affirmation est
# ce qui rend l'écart du § 5.3 tenable — et elle a déjà été fausse : le
# 01/09/2026, la clé en place était enregistrée sur le compte, donc en écriture
# sur tous ses dépôts, depuis un conteneur où tournent des sessions Claude.
# Une intention écrite que rien ne garde dérive. Ces tests la gardent.

POST_CREATE = RACINE / ".devcontainer" / "post-create.sh"
DEPLOY_KEYS = RACINE / ".devcontainer" / "deploy-keys.sh"


def test_le_script_de_deploy_keys_existe_et_est_executable():
    assert DEPLOY_KEYS.is_file(), (
        "deploy-keys.sh est le seul mécanisme qui donne corps à la promesse "
        "« une deploy key par dépôt » de devcontainer.json et d'ARCHITECTURE "
        "§ 5.3. Sans lui, cette promesse redevient une intention."
    )
    assert DEPLOY_KEYS.stat().st_mode & 0o111, "deploy-keys.sh doit être exécutable"


def test_post_create_ne_genere_aucune_cle_de_compte():
    """`post-create.sh` ne doit pas fabriquer de clé unique pour tout le compte.

    Une seule `ssh-keygen` sur `id_ed25519` suffit à recréer la situation que le
    § 5.3 borne : une clé que son opérateur enregistrera, par facilité, dans
    « SSH and GPG keys » du compte plutôt qu'en deploy key de chaque dépôt.
    """
    source = POST_CREATE.read_text(encoding="utf-8")
    assert "ssh-keygen -t" not in source, (
        "post-create.sh génère une clé SSH. La génération appartient à "
        "deploy-keys.sh, qui en produit une par dépôt ; une clé unique créée "
        "ici finit enregistrée sur le compte, et ouvre tous ses dépôts depuis "
        "un conteneur où tournent des sessions Claude (ARCHITECTURE § 5.3)."
    )
    assert "deploy-keys.sh" in source, (
        "post-create.sh doit appeler deploy-keys.sh : sinon un conteneur neuf "
        "n'a aucune clé, et l'opérateur en refabriquera une à la main — de "
        "compte, comme la première fois."
    )


def test_deploy_keys_ne_reecrit_l_url_qu_apres_verification():
    """La réécriture `insteadOf` n'est posée qu'une fois la clé acceptée.

    Posée d'avance, elle dirigerait git vers un alias dont la clé n'est pas
    enregistrée : tout accès au dépôt tomberait, alors que l'URL canonique
    fonctionnait. La migration doit rester dépôt par dépôt, sans coupure.
    """
    source = DEPLOY_KEYS.read_text(encoding="utf-8")
    pose = source.index('git config --global --add "url.$url_alias.insteadOf"')
    verification = source.index("successfully authenticated")
    assert verification < pose, (
        "la réécriture d'URL est posée avant le test d'authentification : un "
        "dépôt dont la deploy key n'est pas encore enregistrée deviendrait "
        "inatteignable."
    )


def test_deploy_keys_ne_pipe_pas_directement_ssh_vers_grep():
    """`ssh -T git@github.com` sort toujours en erreur (1) : GitHub ne donne pas
    d'accès shell, authentification réussie ou non — seul le message diffère.
    Sous `set -o pipefail` (posé en tête du script), `ssh ... | grep -q ...`
    renvoie donc le code de sortie de `ssh`, jamais celui de `grep` : la
    détection « clé acceptée » échouerait alors systématiquement, même quand
    GitHub répond `Hi Owner/Repo: ... successfully authenticated`. Vécu : les
    5 clés du 01/09/2026 étaient actives et le script les donnait toutes comme
    manquantes. La sortie de `ssh` doit être capturée avant d'être testée.
    """
    source = DEPLOY_KEYS.read_text(encoding="utf-8")
    assert "set -o pipefail" in source or "set -euo pipefail" in source, (
        "ce test suppose pipefail actif — sinon son motif ne prouve rien"
    )
    # Les continuations de ligne (`\` en fin de ligne) sont jointes avant le
    # test : c'est précisément la forme qui a caché le bug d'origine, le pipe
    # se trouvant sur la ligne suivant le `ssh ... \`.
    aplati = re.sub(r"\\\n\s*", " ", source)
    for ligne in aplati.splitlines():
        assert not re.search(r"\bssh\b[^|\n]*\|\s*grep\b", ligne), (
            f"pipe direct ssh → grep détecté : {ligne!r}. Sous pipefail, ceci "
            "renvoie le code de sortie de ssh (toujours 1) et non celui de "
            "grep : capturer la sortie de ssh dans une variable d'abord."
        )
