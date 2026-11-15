"""Ranger sans humain : lancer la revue en mode agent sur chaque base qui la permet.

    okf-ranger [--agent <id>] [--model <modèle>] [--sec] [<base>...]

Sans base nommée, toutes celles de `agent-review`. Pour chacune, l'outil vérifie
les deux clés (§ 7.1 étape 5 bis) et saute une base sans proposition en
attente — aucun modèle n'est lancé pour rien. Sinon, il lance une session
Claude Code non interactive (`claude -p`) qui suit la skill `kb-review`, sous
des permissions fixées ici et nulle part ailleurs :

- la session tourne dans un dossier de plans jetable, seul endroit où elle
  écrit (`acceptEdits` n'y vaut que pour lui ; l'écriture sous le hub est
  refusée en plus, explicitement) ;
- elle lit le hub (`Read`, `Glob`, `Grep`) et n'exécute qu'une commande,
  `bin/okf-review`, par son chemin absolu ; toute autre commande, et toute
  commande composée, est refusée sans question ;
- `OKF_REVIEW_AGENT` lui impose `resolve --agent <id>` : elle ne peut ni
  résoudre en mode humain ni réconcilier (review.py).

Le corpus n'est donc écrit que par `okf-review resolve --agent`, qui vérifie
ses règles avant toute résolution. Ce qui demande un humain reste dans
`pending/` ; la sortie de la session en donne la liste.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import agent, hublog
from .errors import ToolError
from .review import AGENT_IMPOSE, _registry, load_pending

AGENT_DEFAUT = "rangeur"

CONSIGNE = """\
Tu es le gestionnaire de la base de connaissance « {base} », en mode agent, sans humain.
Suis {skill}, étape 5 bis (mode agent), avec l'identifiant d'agent « {agent} ».

- Le moteur de revue est {okf_review} : appelle-le par ce chemin exact, une commande à la fois, jamais composée (ni &&, ni ;, ni |, ni heredoc). Commence par `{okf_review} context {base}` puis `{okf_review} inventory {base}`.
- Écris chaque plan JSON avec l'outil Write dans le dossier courant ({plans}), puis `{okf_review} resolve {base} --plan <fichier> --agent {agent} --dry-run`, puis la même commande sans --dry-run.
- N'intègre que ce que les règles et la gouvernance de la base permettent. Une proposition douteuse, contradictoire ou qui demande un jugement reste en attente : ne la mets dans aucun plan.
- Termine par la liste de ce qui reste pour l'humain : id, sujet, et pourquoi en une phrase.
"""


def outils_permis(okf_review: Path) -> list[str]:
    """Lire partout, n'exécuter que okf-review. L'écriture n'est pas listée :
    `acceptEdits` ne la permet que dans le dossier courant, celui des plans."""
    return [f"Bash({okf_review}:*)", "Read", "Glob", "Grep"]


def outils_refuses(hub: Path) -> list[str]:
    """Ceinture : aucune écriture sous le hub, quel que soit le mode (`//` : chemin absolu)."""
    return [f"Edit(/{hub}/**)", f"Write(/{hub}/**)"]


def commande(claude: str, consigne: str, okf_review: Path, hub: Path, modele: str | None) -> list[str]:
    cmd = [claude, "-p", consigne, "--permission-mode", "acceptEdits",
           "--allowedTools", *outils_permis(okf_review),
           "--disallowedTools", *outils_refuses(hub)]
    if modele:
        cmd += ["--model", modele]
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="okf-ranger", description=__doc__.split("\n")[0])
    parser.add_argument("--hub-root", type=Path, default=None)
    parser.add_argument("--agent", default=AGENT_DEFAUT, help=f"identifiant d'agent (défaut : {AGENT_DEFAUT})")
    parser.add_argument("--model", default=None, help="modèle de la session (défaut : celui de claude)")
    parser.add_argument("--sec", action="store_true", help="dire ce qui serait lancé, sans rien lancer")
    parser.add_argument("bases", nargs="*")
    args = parser.parse_args(argv)

    try:
        agent.reviewed_by(args.agent)
        registry = _registry(args.hub_root)
    except ToolError as exc:
        print(f"okf-ranger : {exc.message}", file=sys.stderr)
        return 4
    hub = registry.config.hub_root
    okf_review = (hub / "bin" / "okf-review").resolve()
    skill = (hub / "skills" / "kb-review" / "SKILL.md").resolve()
    claude = os.environ.get("OKF_RANGER_CLAUDE") or shutil.which("claude")
    noms = args.bases or list(registry.config.agent_review)
    if not noms:
        print("okf-ranger : aucune base dans agent-review (hub-config.local.yaml) : rien à ranger.")
        return 0

    echecs = 0
    for nom in noms:
        try:
            base = registry.get(nom)
            agent.exiger_deux_cles(base, registry.config)
        except ToolError as exc:
            print(f"{nom} : refusé — {exc.message}")
            echecs += 1
            continue
        avant = len(load_pending(base))
        if not avant:
            print(f"{nom} : rien en attente.")
            continue
        if not claude:
            print(f"{nom} : {avant} en attente, mais `claude` est introuvable (OKF_RANGER_CLAUDE)")
            echecs += 1
            continue
        plans = Path(tempfile.mkdtemp(prefix=f"okf-ranger-{nom}-"))
        cmd = commande(claude, CONSIGNE.format(base=nom, skill=skill, agent=args.agent,
                                               okf_review=okf_review, plans=plans),
                       okf_review, hub, args.model)
        if args.sec:
            print(f"{nom} : {avant} en attente ; lancerait, dans {plans} :")
            print("  " + " ".join(cmd[:2]) + " <consigne> " + " ".join(f"'{c}'" if " " in c else c for c in cmd[3:]))
            shutil.rmtree(plans, ignore_errors=True)
            continue
        debut = time.monotonic()
        fin = subprocess.run(cmd, cwd=plans, capture_output=True, text=True,
                             env={**os.environ, AGENT_IMPOSE: args.agent})
        duree = time.monotonic() - debut
        registry.scan()
        apres = len(load_pending(registry.get(nom)))
        hublog.info(f"okf-ranger : {nom} : {avant} → {apres} en attente en {duree:.0f} s (claude {fin.returncode})")
        print(f"{nom} : {avant} → {apres} en attente, {duree:.0f} s, claude {fin.returncode}")
        if fin.stdout.strip():
            print("  " + fin.stdout.strip().replace("\n", "\n  "))
        if fin.returncode != 0:
            print("  " + (fin.stderr.strip() or "(rien sur stderr)").replace("\n", "\n  "), file=sys.stderr)
            echecs += 1
        shutil.rmtree(plans, ignore_errors=True)
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
