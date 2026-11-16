#!/usr/bin/env bash
# Les essais du dépôt, joués par le banc de l'hôte de l'atelier (Movida/architecte, hote/banc.sh) à la place
# de l'ancien workflow GitHub Actions (ci.yml) : .github/essais.sh [commit de base]
# Décision : D-2026-11-15-actions-03 (pas de fournisseur, budget Actions à 0 $).
#
# Comme l'ancienne CI : la suite complète (pytest) sous Python 3.11 et 3.12, avec ripgrep (dépendance
# d'exécution de kb_search) et une identité git (les fixtures commitent). Le cycle de bout en bout
# (tests/test_end_to_end.py) se joue si le dépôt okf-bundle-template se clone sans identifiant, et
# s'auto-saute sinon, comme avant.
#
# Chaque version tourne dans une image Python épinglée par empreinte. Le dépôt est monté en lecture seule
# puis copié : rien n'est écrit chez l'appelant. Les essais tournent sous un utilisateur non root (comme
# l'ancienne CI). Le banc lance ce script avec un environnement vidé ; seul DOCKER_HOST passe.
#
# Essai local derrière un mandataire : ESSAIS_DOCKER_OPTS="--network host -e HTTPS_PROXY …" .github/essais.sh
set -uo pipefail

UV_VERSION=0.11.32
# ripgrep : le binaire statique de la version publiée, vérifié par empreinte (pas d'apt : plus court, et rien
# ne dépend des miroirs Debian).
RG_URL=https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz
RG_SHA256=4cf9f2741e6c465ffdb7c26f38056a59e2a2544b51f7cc128ef28337eeae4d8e
IMAGES=(
    "3.11 python:3.11-bookworm@sha256:726d304906fa5eb468e5531e366548d1903286352f5adb54ef508ac01fbd80c6"
    "3.12 python:3.12-bookworm@sha256:4209fae09a3e20afd863e528bfddc235da5fd5977983e1996cd54cc9a72e6267"
)

echecs=()
lancer() { # libellé commande…
    local libelle=$1; shift
    echo "::group::$libelle"
    if "$@"; then echo "::endgroup::"; echo "ok    $libelle"
    else echo "::endgroup::"; echo "ÉCHEC $libelle"; echecs+=("$libelle"); fi
}
bilan() {
    if [ ${#echecs[@]} -gt 0 ]; then printf 'Essais en échec : %s\n' "${echecs[*]}"; exit 1; fi
    echo "Tous les essais passent."; exit 0
}

case ${1:-} in
--dedans) # Dans le conteneur, en root : les outils, puis la main à un utilisateur ordinaire.
    { curl -fsSLo /tmp/rg.tgz "$RG_URL" && echo "$RG_SHA256  /tmp/rg.tgz" | sha256sum -c --quiet \
        && tar -xzf /tmp/rg.tgz -C /usr/local/bin --strip-components=1 --wildcards '*/rg'; } || { echo "ÉCHEC ripgrep"; exit 1; }
    pip install -q --root-user-action=ignore --disable-pip-version-check "uv==$UV_VERSION" || { echo "ÉCHEC uv"; exit 1; }
    useradd -m -u 1000 banc
    mkdir -p /tmp/depot && tar -C /source --exclude=./.venv -cf - . | tar -C /tmp/depot -xf - && chown -R banc: /tmp/depot || exit 1
    exec setpriv --reuid=1000 --regid=1000 --init-groups env HOME=/home/banc LANG=C.UTF-8 PATH="$PATH" \
        bash /tmp/depot/.github/essais.sh --essais "$2" ;;
--essais) # Sous l'utilisateur banc : la suite, comme l'ancienne CI.
    cd /tmp/depot || exit 1
    git config --global user.name "Banc" && git config --global user.email "banc@atelier.invalid" && git config --global init.defaultBranch main
    if [ "$2" = 3.12 ]; then
        GIT_TERMINAL_PROMPT=0 git clone -q --depth 1 https://github.com/Movida/okf-bundle-template.git "$HOME/okf-bundle-template" 2>/dev/null \
            && echo "okf-bundle-template cloné : le cycle de bout en bout se joue" \
            || echo "--    okf-bundle-template introuvable : le cycle de bout en bout s'auto-saute"
    fi
    lancer "dépendances (uv sync, Python $2)" uv sync -q --python "$2"
    lancer "suite complète (pytest, Python $2)" uv run --python "$2" pytest -q
    bilan ;;
esac

# Sur l'hôte : une image par version de Python.
cd "$(dirname "$0")/.."
for i in "${IMAGES[@]}"; do
    v=${i%% *}; image=${i#* }
    # shellcheck disable=SC2086
    lancer "Python $v" docker run --rm -i ${ESSAIS_DOCKER_OPTS:-} -v "$PWD:/source:ro" "$image" \
        bash /source/.github/essais.sh --dedans "$v"
done
bilan
