#!/usr/bin/env bash
# Préparation du devcontainer du hub (§ 4.3).
set -euo pipefail

echo "→ ripgrep (requis par kb_search)"
if ! command -v rg >/dev/null 2>&1; then
    # apt-get update échoue sur un dépôt tiers cassé de l'image de base
    # (yarn.list, clé GPG expirée, sans rapport avec le hub) : on tolère cet
    # échec, car les dépôts Debian officiels sont mis à jour avant que
    # l'erreur ne soit levée. Ne pas filtrer via
    # -o Dir::Etc::sourceparts=/dev/null : sur cette image le dépôt Debian
    # lui-même vit dans sources.list.d (format deb822), donc cette option le
    # désactiverait aussi et ripgrep resterait introuvable (vécu).
    sudo apt-get update -qq || true
    sudo apt-get install -y -qq ripgrep
fi
rg --version | head -1

echo "→ uv"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "→ dépendances Python"
cd "$(dirname "$0")/.."
uv sync

echo "→ identité git du conteneur"
# Le hub passe toujours son identité explicitement à chaque commit (§ 4.4.e) ;
# cette valeur ne sert qu'aux commandes git lancées à la main dans le
# conteneur, pour qu'elles n'échouent pas sur une identité manquante.
git config --global --get user.email >/dev/null 2>&1 || \
    git config --global user.email "operateur@local"
git config --global --get user.name >/dev/null 2>&1 || \
    git config --global user.name "opérateur du hub"

# Les clones de bases sont des dépôts appartenant potentiellement à un autre
# uid que celui du conteneur ; sans cela git refuse de les lire.
git config --global --add safe.directory '*'

echo "→ clé SSH du conteneur"
# Les remotes en `git@github.com:` sont injoignables depuis un conteneur sans clé
# ni agent : `SSH_AUTH_SOCK` est vide, et un `git push` échoue en « Permission
# denied (publickey) ». C'est arrivé — un commit est resté sur place quatre jours
# sans que rien ne le signale, parce que le commit, lui, avait réussi.
#
# La clé vit dans le volume nommé monté sur ~/.ssh (devcontainer.json) : générée
# une fois, elle survit aux rebuilds. Un volume neuf appartient à root, d'où le
# chown — sans lui ssh refuse de lire quoi que ce soit.
SSH_DIR="$HOME/.ssh"
sudo mkdir -p "$SSH_DIR"
sudo chown -R "$(id -u):$(id -g)" "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Host key de github.com. Vérifiée contre l'empreinte publiée par GitHub, pas
# acceptée à la première rencontre : un `ssh-keyscan` gobé tel quel fait
# confiance à qui répond, ce qui est précisément ce que known_hosts doit empêcher.
# Si GitHub fait tourner sa clé, ce script échoue bruyamment plutôt que d'ouvrir.
GITHUB_ED25519="SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"
if ! ssh-keygen -F github.com >/dev/null 2>&1; then
    scan="$(mktemp)"
    ssh-keyscan -t ed25519 github.com > "$scan" 2>/dev/null || true
    empreinte="$(ssh-keygen -lf "$scan" 2>/dev/null | awk '{print $2}')"
    if [ "$empreinte" = "$GITHUB_ED25519" ]; then
        cat "$scan" >> "$SSH_DIR/known_hosts"
        echo "   host key de github.com vérifiée et enregistrée"
    else
        echo "   !! empreinte inattendue pour github.com : ${empreinte:-aucune réponse}"
        echo "      attendue : $GITHUB_ED25519"
        echo "      known_hosts n'est PAS modifié. Vérifier la publication de"
        echo "      GitHub (docs.github.com, « SSH key fingerprints ») avant de"
        echo "      forcer quoi que ce soit."
    fi
    rm -f "$scan"
fi

CLE="$SSH_DIR/id_ed25519"
if [ ! -f "$CLE" ]; then
    ssh-keygen -t ed25519 -N "" -C "okf-hub-devcontainer" -f "$CLE" -q
    echo "   clé générée : $CLE"
fi
chmod 600 "$CLE"
chmod 644 "$CLE.pub"

echo "→ vérification"
uv run pytest -q -m "not slow"

cat <<EOF

Clé SSH du conteneur — à enregistrer une fois, si vous poussez en SSH :

$(cat "$SSH_DIR/id_ed25519.pub")

  Étroit (recommandé) : Settings > Deploy keys du dépôt à pousser,
    « Allow write access ». Une clé par dépôt, révocable en un clic, sans
    aucun accès aux autres dépôts du compte.
  Large : Settings > SSH and GPG keys du compte — la clé ouvre alors tous
    les dépôts, depuis un conteneur où tournent des instances du hub.

  Sans clé du tout : faites tourner un ssh-agent sur l'hôte avant
    d'attacher VS Code, qui en transmet la socket (SSH_AUTH_SOCK). Aucun
    matériel de clé n'entre alors dans le conteneur — et le montage de
    devcontainer.json devient inutile.

  Les remotes en https:// n'ont besoin de rien : le helper de credentials
  de VS Code les sert déjà.

EOF
cat <<'EOF'
Hub prêt.

  bin/okf-lock       verrouillage d'une base pour une séquence git complète
  bin/okf-review     moteur de revue du rôle gestionnaire
  bin/okf-base-path  résolution d'un nom de base en chemin

Importer une base :  git clone <url> bases/<nom>   puis  kb_hub_rescan
EOF
