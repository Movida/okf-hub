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
# conteneur — mais elle n'est pas cosmétique pour autant : un commit poussé
# sous une adresse inexistante n'est rattaché à aucun compte GitHub, donc sans
# auteur identifiable dans l'historique public. `operateur@local` était ce
# défaut-là, et l'historique du hub en porte la trace.
#
# L'adresse vit dans git-identity.env, non versionné : elle est propre à
# chaque mainteneur. Modèle (l'adresse noreply évite de publier une adresse
# personnelle tout en garantissant le rattachement au compte) :
#
#   OKF_GIT_NAME="Prénom NOM"
#   OKF_GIT_EMAIL="<id>+<login>@users.noreply.github.com"
IDENTITE="$(dirname "$0")/git-identity.env"
if [ -f "$IDENTITE" ]; then
    # shellcheck disable=SC1090
    . "$IDENTITE"
    git config --global user.name "${OKF_GIT_NAME:?OKF_GIT_NAME manquant dans git-identity.env}"
    git config --global user.email "${OKF_GIT_EMAIL:?OKF_GIT_EMAIL manquant dans git-identity.env}"
    echo "   $(git config --global user.name) <$(git config --global user.email)>"
elif ! git config --global --get user.email >/dev/null 2>&1; then
    git config --global user.name "opérateur du hub"
    git config --global user.email "operateur@local"
    echo "   !! aucune identité déclarée : commits non rattachables à un compte"
    echo "      GitHub. Créer .devcontainer/git-identity.env (modèle dans ce"
    echo "      script) et relancer, avant de pousser quoi que ce soit."
fi

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

# Aucune clé de compte n'est générée ici : ce serait une clé unique ouvrant
# tous les dépôts du compte, ce que devcontainer.json interdit explicitement.
# Les clés sont créées par dépôt, en deploy keys — voir deploy-keys.sh, qui
# porte le raisonnement complet. Une clé de compte héritée d'une installation
# antérieure est conservée telle quelle : deploy-keys.sh rappelle de la
# révoquer une fois les deploy keys en place.
ANCIENNE="$SSH_DIR/id_ed25519"
if [ -f "$ANCIENNE" ]; then
    chmod 600 "$ANCIENNE"
    [ -f "$ANCIENNE.pub" ] && chmod 644 "$ANCIENNE.pub"
fi

echo "→ deploy keys par dépôt"
"$(dirname "$0")/deploy-keys.sh"

echo "→ vérification"
uv run pytest -q -m "not slow"

cat <<'EOF'

Accès GitHub — les clés publiques à enregistrer, s'il en reste, ont été
listées plus haut par deploy-keys.sh. Une clé par dépôt, révocable seule,
sans aucun accès aux autres dépôts du compte.

  Sans clé du tout : faites tourner un ssh-agent sur l'hôte avant
    d'attacher VS Code, qui en transmet la socket (SSH_AUTH_SOCK). Aucun
    matériel de clé n'entre alors dans le conteneur — mais le hub ne peut
    plus rien pousser quand VS Code est détaché, et le montage de
    devcontainer.json devient inutile.

EOF
cat <<'EOF'
Hub prêt.

  bin/okf-lock       verrouillage d'une base pour une séquence git complète
  bin/okf-review     moteur de revue du rôle gestionnaire
  bin/okf-base-path  résolution d'un nom de base en chemin

Importer une base :  git clone <url> bases/<nom>   puis  kb_hub_rescan
EOF
