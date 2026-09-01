#!/usr/bin/env bash
# Une deploy key par dépôt, pour le conteneur du hub.
#
# POURQUOI. devcontainer.json l'écrit déjà : la clé du conteneur est « à
# enregistrer sur GitHub en deploy key d'un seul dépôt — pas une clé
# personnelle, dont la compromission ouvrirait tous les dépôts du compte ».
# Le motif n'est pas théorique : ce conteneur fait tourner des instances du
# hub, donc des sessions Claude qui exécutent du code. Une clé de compte y
# vaut un accès en écriture à *tous* les dépôts du compte ; une deploy key ne
# vaut que le dépôt qu'elle sert, et se révoque seule en un clic.
#
# COMMENT, SANS TOUCHER AUX FICHIERS VERSIONNÉS. Une deploy key par dépôt
# suppose de choisir la clé selon le dépôt, ce que ssh ne sait faire que par
# le nom d'hôte. Écrire l'alias dans les URL contaminerait des fichiers
# versionnés — `bundles/upstreams.yaml` doit rester clonable depuis n'importe
# quelle machine, avec les identifiants de son opérateur. La réécriture vit
# donc dans la config git *globale du conteneur* :
#
#   url.git@gh-movida-okf-hub-feedback:Movida/okf-hub-feedback.insteadOf
#       = git@github.com:Movida/okf-hub-feedback
#       = https://github.com/Movida/okf-hub-feedback
#
# Les deux formes d'URL sont couvertes, donc les bases clonées en https://
# passent aussi par leur deploy key sans qu'on réécrive leur remote. Et la
# réécriture s'applique à `git clone`, ce qu'un `core.sshCommand` local au
# dépôt ne saurait pas faire : bootstrap.py clone avec un `git clone` nu.
#
# Ce script est idempotent : relancé, il ne régénère aucune clé existante et
# se contente de réafficher ce qui reste à enregistrer.
set -euo pipefail

SSH_DIR="$HOME/.ssh"
DEBUT="# >>> okf-hub deploy keys — bloc géré, ne pas éditer à la main >>>"
FIN="# <<< okf-hub deploy keys <<<"

hub_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Inventaire des dépôts à servir ------------------------------------------
# Trois sources : le dépôt du hub lui-même, les amonts déclarés des bases
# livrées, et les bases déjà clonées dans bases/. Une base importée à la main
# est donc prise en compte au prochain passage, sans configuration.
inventaire() {
    git -C "$hub_root" remote get-url origin 2>/dev/null || true
    sed -nE 's/^[a-zA-Z0-9_-]+:[[:space:]]*(\S+)[[:space:]]*$/\1/p' \
        "$hub_root/bundles/upstreams.yaml" 2>/dev/null || true
    for d in "$hub_root"/bases/*/; do
        [ -d "$d/.git" ] || continue
        git -C "$d" remote get-url origin 2>/dev/null || true
    done
}

# `git@github.com:Owner/Repo.git` ou `https://github.com/Owner/Repo.git`
# -> `Owner/Repo`. Toute autre forge est ignorée : ce mécanisme ne prétend
# servir que GitHub.
normalise() {
    sed -E \
        -e 's#^git@github\.com:##' \
        -e 's#^ssh://git@github\.com/##' \
        -e 's#^https://github\.com/##' \
        -e 's#\.git$##' \
        -e 's#/+$##'
}

mapfile -t depots < <(inventaire | normalise | grep -E '^[^/]+/[^/]+$' | sort -u)

if [ "${#depots[@]}" -eq 0 ]; then
    echo "   aucun dépôt GitHub détecté — rien à faire"
    exit 0
fi

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

bloc="$DEBUT"
a_enregistrer=()
alias_de=()

# --- Passe 1 : les clés et les alias -----------------------------------------
# Le bloc ~/.ssh/config est écrit AVANT tout test d'authentification : sans
# l'alias, `ssh git@gh-...` ne résout rien et le test échouerait toujours.
for depot in "${depots[@]}"; do
    alias_ssh="gh-$(echo "$depot" | tr 'A-Z/' 'a-z-' | tr -cs 'a-z0-9-' '-')"
    alias_ssh="${alias_ssh%-}"
    cle="$SSH_DIR/deploy_${alias_ssh#gh-}"
    alias_de+=("$depot|$alias_ssh|$cle")

    if [ ! -f "$cle" ]; then
        ssh-keygen -t ed25519 -N "" -q -f "$cle" \
            -C "okf-hub-devcontainer deploy key — $depot"
    fi
    chmod 600 "$cle"
    chmod 644 "$cle.pub"

    bloc+="
Host $alias_ssh
    HostName github.com
    User git
    IdentityFile $cle
    IdentitiesOnly yes"
done
bloc+="
$FIN"

# Tout ce qui est hors des marqueurs est préservé : l'opérateur peut avoir ses
# propres hôtes dans ce fichier.
config="$SSH_DIR/config"
touch "$config"
reste="$(awk -v d="$DEBUT" -v f="$FIN" '
    $0 == d { dans = 1; next }
    $0 == f { dans = 0; next }
    !dans   { print }
' "$config")"
printf '%s\n%s\n' "$bloc" "$reste" > "$config"
chmod 600 "$config"

# --- Passe 2 : la réécriture d'URL, si et seulement si la clé est acceptée ----
for entree in "${alias_de[@]}"; do
    IFS='|' read -r depot alias_ssh cle <<< "$entree"

    # Une réécriture vers un alias dont la clé n'est pas enregistrée casserait
    # tout accès au dépôt, alors que sans elle l'URL canonique continue de
    # fonctionner par le moyen déjà en place. On la retire d'abord, on ne la
    # pose qu'après preuve : la migration se fait dépôt par dépôt, sans coupure.
    url_alias="git@$alias_ssh:$depot"
    git config --global --unset-all "url.$url_alias.insteadOf" 2>/dev/null || true

    # GitHub répond « Hi Owner/Repo: ... » à une deploy key reconnue, et
    # refuse l'authentification sinon. `ssh -T` sort toujours en erreur (1) —
    # GitHub ne donne pas d'accès shell, succès d'authentification ou non — donc
    # la sortie est capturée puis testée à part : un `| grep -q` direct ferait
    # remonter le code de sortie de `ssh` sous `pipefail`, jamais celui de
    # `grep`, et ce test échouerait toujours quelle que soit la réalité.
    reponse="$(ssh -o BatchMode=yes -o ConnectTimeout=10 -T "git@$alias_ssh" 2>&1 || true)"
    if echo "$reponse" | grep -q "successfully authenticated"; then
        git config --global --add "url.$url_alias.insteadOf" "git@github.com:$depot"
        git config --global --add "url.$url_alias.insteadOf" "https://github.com/$depot"
        echo "   ✓ $depot — deploy key active"
    else
        echo "   ✗ $depot — deploy key à enregistrer (accès inchangé en attendant)"
        a_enregistrer+=("$depot|$cle.pub")
    fi
done

# --- Ce qui reste à faire à la main ------------------------------------------
if [ "${#a_enregistrer[@]}" -gt 0 ]; then
    cat <<'ENTETE'

  ─── Deploy keys à enregistrer ────────────────────────────────────────────
  Pour chaque dépôt : Settings > Deploy keys > Add deploy key, coller la
  clé, cocher « Allow write access » si le hub doit y pousser.
ENTETE
    for entree in "${a_enregistrer[@]}"; do
        depot="${entree%%|*}"
        pub="${entree##*|}"
        echo
        echo "  https://github.com/$depot/settings/keys/new"
        echo "    $(cat "$pub")"
    done
    echo
    echo "  Puis relancer : .devcontainer/deploy-keys.sh"
    echo
fi

# --- La clé de compte doit disparaître ---------------------------------------
if [ -f "$SSH_DIR/id_ed25519" ] && [ "${#a_enregistrer[@]}" -eq 0 ]; then
    cat <<'RESTE'

  ─── Il reste la clé de compte ────────────────────────────────────────────
  ~/.ssh/id_ed25519 ouvre encore TOUS les dépôts du compte depuis ce
  conteneur. Les deploy keys couvrent désormais les dépôts utilisés :
    1. la révoquer sur https://github.com/settings/keys
    2. rm ~/.ssh/id_ed25519 ~/.ssh/id_ed25519.pub

RESTE
fi
