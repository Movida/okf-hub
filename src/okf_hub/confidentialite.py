"""Ce qui ne doit jamais entrer dans un dépôt public (rév. 4.4).

Le dépôt du hub est public, comme les bases livrées avec lui qui ont un dépôt
canonique (`bundles/upstreams.yaml`) ; les autres bases d'un opérateur ne le
sont pas. Les termes privés sont les noms des bases privées installées, leurs
répertoires et répertoires de corpus, et `private-terms` de la configuration
locale. La liste n'est jamais écrite dans un fichier versionné : elle se déduit
de la machine, sans quoi elle publierait ce qu'elle protège.

Trois usages, un seul calcul :
- `kb_propose` refuse une proposition qui en cite un dans une base publique
  (§ 5.5) — c'est au dépôt que le contenu entre dans l'historique ;
- `tests/test_neutralite.py` les cherche dans les fichiers versionnés ;
- les hooks git de `.githooks/` les cherchent dans ce qui est commité et poussé :

    python -m okf_hub.confidentialite index              # pre-commit
    python -m okf_hub.confidentialite message <fichier>  # commit-msg
    python -m okf_hub.confidentialite poussee            # pre-push (stdin de git)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from . import hublog
from .bootstrap import available, upstreams
from .config import HubConfig
from .registry import Registry

#: Nom de corpus générique : pas un nom propre.
GENERIQUES = frozenset({"knowledge"})

ZERO = "0" * 40


def bases_publiques(config: HubConfig) -> frozenset[str]:
    """Livrées avec le hub et clonées d'un dépôt canonique, ou déclarées par
    l'opérateur (`public-bases`)."""
    livrees = available(config.hub_root)
    return frozenset(n for n in upstreams(config.hub_root) if n in livrees) | config.public_bases


def termes_prives(registry: Registry) -> frozenset[str]:
    config = registry.config
    livrees = set(available(config.hub_root))
    termes: set[str] = set(config.private_terms)
    for base in registry.ordered():
        if base.name in livrees or base.dir_name in livrees:
            continue
        termes |= {base.name, base.dir_name, base.corpus_dir.name}
    return frozenset(t for t in termes if t and t not in GENERIQUES and t not in livrees)


def motif(termes) -> re.Pattern | None:
    if not termes:
        return None
    # Seuls une lettre ou un chiffre accolés empêchent la correspondance : un
    # terme collé par un tiret ou un souligné (`cowork-<terme>`, `<terme>_vw`)
    # est publié tout autant.
    alternatives = "|".join(re.escape(t) for t in sorted(termes, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])({alternatives})(?![A-Za-z0-9])", re.IGNORECASE)


def trouver(rx: re.Pattern | None, texte: str) -> list[str]:
    """Termes privés présents dans `texte`, chacun une fois, tels qu'écrits."""
    if rx is None:
        return []
    return list(dict.fromkeys(m.group(1) for m in rx.finditer(texte)))


# --- hooks git ----------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, check=True, text=True, encoding="utf-8",
        errors="replace",
    ).stdout


def _lignes_ajoutees(diff: str):
    """(fichier, texte) pour chaque ligne ajoutée d'un diff, chemins compris."""
    fichier = "?"
    for ligne in diff.splitlines():
        if ligne.startswith("+++ b/"):
            fichier = ligne[6:]
            yield fichier, fichier
        elif ligne.startswith("+") and not ligne.startswith("+++"):
            yield fichier, ligne[1:]


def _rapport(rx, elements) -> list[str]:
    fuites = []
    for ou, texte in elements:
        for terme in trouver(rx, texte):
            fuites.append(f"{ou} : « {terme} »")
    return list(dict.fromkeys(fuites))


def _poussee(stdin: str) -> list[tuple[str, str]]:
    """Ce que chaque commit poussé ajoute, plus son auteur et son message.

    Une branche nouvelle pour le remote est examinée sur tout ce qu'aucun remote
    ne connaît encore : c'est ce qui empêche de republier une vieille branche
    locale portant un historique d'avant nettoyage.
    """
    elements: list[tuple[str, str]] = []
    for ligne in stdin.splitlines():
        champs = ligne.split()
        if len(champs) != 4 or champs[1] == ZERO:      # suppression de branche
            continue
        local, distant = champs[1], champs[3]
        plage = [local, "--not", "--remotes"] if distant == ZERO else [f"{distant}..{local}"]
        for sha in _git("rev-list", *plage).split():
            court = sha[:12]
            entete = _git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
            elements += [(f"{court} (auteur, message)", l) for l in entete.splitlines()]
            diff = _git("show", "--format=", "-p", "--no-color", sha)
            elements += [(f"{court} {f}", t) for f, t in _lignes_ajoutees(diff)]
    return elements


def main(argv: list[str] | None = None) -> int:
    from .__main__ import _default_hub_root

    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("index", "message", "poussee"):
        print(__doc__, file=sys.stderr)
        return 2
    config = HubConfig.load(_default_hub_root())
    hublog.configure(config.log_file, echo_stderr=False)   # un hook se tait
    registry = Registry(config)
    registry.scan()
    rx = motif(termes_prives(registry))
    if rx is None:
        return 0

    if argv[0] == "index":
        elements = list(_lignes_ajoutees(_git("diff", "--cached", "-U0", "--no-color")))
        quoi = "ce commit"
    elif argv[0] == "message":
        texte = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
        elements = [("message", l) for l in texte.splitlines() if not l.startswith("#")]
        quoi = "ce message de commit"
    else:
        elements = _poussee(sys.stdin.read())
        quoi = "cette poussée"

    fuites = _rapport(rx, elements)
    if not fuites:
        return 0
    print(
        f"okf-hub : {quoi} publierait des termes privés (bases de l'opérateur, "
        f"`private-terms`) dans un dépôt public :",
        file=sys.stderr,
    )
    for f in fuites[:30]:
        print(f"  {f}", file=sys.stderr)
    if len(fuites) > 30:
        print(f"  … et {len(fuites) - 30} autre(s)", file=sys.stderr)
    print(
        "Reformuler avec un nom neutre (`produit-x`, `installation-x`…) ; une règle "
        "propre à une base va dans son AGENTS.md.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
