#!/usr/bin/env bash
# Préparation du devcontainer du hub (§ 4.3).
set -euo pipefail

echo "→ ripgrep (requis par kb_search)"
if ! command -v rg >/dev/null 2>&1; then
    sudo apt-get update -qq
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

echo "→ vérification"
uv run pytest -q -m "not slow"

cat <<'EOF'

Hub prêt.

  bin/okf-lock       verrouillage d'une base pour une séquence git complète
  bin/okf-review     moteur de revue du rôle gestionnaire
  bin/okf-base-path  résolution d'un nom de base en chemin

Importer une base :  git clone <url> bases/<nom>   puis  kb_hub_rescan
EOF
