"""`okf-hub setup` — point d'entrée d'installation unique.

Aujourd'hui, le README documente séparément quatre étapes pour passer d'un
clone du dépôt à un hub opérationnel : préparer l'identité git du conteneur,
générer/enregistrer une clé SSH par dépôt, configurer le client MCP utilisé
(quatre variantes selon Claude Code CLI, `.mcp.json`, Claude Desktop, ou un
hub dans un devcontainer), puis installer les bases livrées. Ce module les
enchaîne en une seule commande.

**Ce module ne remplace aucune des procédures manuelles déjà documentées au
README** — il ne fait qu'automatiser les cas qu'il peut détecter sans deviner
un identifiant ou un secret externe. Une étape sans objet dans l'environnement
courant (pas de devcontainer, Claude Desktop non installé, aucun client MCP
détecté) est signalée comme telle, jamais silencieuse : l'opérateur retombe
alors sur la procédure documentée correspondante.

Chaque étape est idempotente : relancer `okf-hub setup` après un premier
passage réussi ne fait que confirmer l'état déjà en place.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import bootstrap
from .config import HubConfig

GIT_IDENTITY_ENV = Path(".devcontainer/git-identity.env")
DEPLOY_KEYS_SCRIPT = Path(".devcontainer/deploy-keys.sh")

_STATUS_MARK = {"ok": "✓", "skip": "○", "fail": "✗"}

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str  # "ok" | "skip" | "fail"
    detail: str

    def render(self) -> str:
        return f"{_STATUS_MARK[self.status]} {self.name} — {self.detail}"


# --- 1. Identité git (§ README « Identité git ») ------------------------------


def ensure_git_identity(
    hub_root: Path,
    *,
    interactive: bool,
    ask: Callable[[str], str] = input,
    runner: Runner = subprocess.run,
) -> StepResult:
    """Configure `user.name`/`user.email` globalement s'ils manquent encore.

    Un commit poussé sous une adresse qui n'existe pas n'est rattaché à aucun
    compte GitHub (README « Identité git »). Trois sources, dans l'ordre :
    l'identité globale déjà configurée, `.devcontainer/git-identity.env`
    laissé par une session précédente (rebuild), ou une saisie interactive.
    """
    name = _git_config_get("user.name", runner=runner)
    email = _git_config_get("user.email", runner=runner)
    if name and email:
        return StepResult("identité git", "ok", f"déjà configurée : {name} <{email}>")

    env_path = hub_root / GIT_IDENTITY_ENV
    if env_path.is_file():
        depuis_env = _read_identity_env(env_path)
        if depuis_env is not None:
            name, email = depuis_env
            _git_config_set(runner, "user.name", name)
            _git_config_set(runner, "user.email", email)
            return StepResult("identité git", "ok", f"appliquée depuis {GIT_IDENTITY_ENV} : {name} <{email}>")

    if not interactive:
        return StepResult(
            "identité git", "skip",
            f"non interactif et aucune identité connue — voir README « Identité git » "
            f"ou créer {GIT_IDENTITY_ENV}",
        )

    name = ask("Nom pour les commits git (ex. « Prénom NOM ») : ").strip()
    email = ask(
        "Adresse noreply GitHub (Settings > Emails, ex. "
        "<id>+<login>@users.noreply.github.com) : "
    ).strip()
    if not name or not email:
        return StepResult("identité git", "skip", "saisie vide — voir README « Identité git »")

    _git_config_set(runner, "user.name", name)
    _git_config_set(runner, "user.email", email)
    if (hub_root / ".devcontainer").is_dir() and not env_path.is_file():
        env_path.write_text(
            f'OKF_GIT_NAME="{name}"\nOKF_GIT_EMAIL="{email}"\n', encoding="utf-8"
        )
    return StepResult("identité git", "ok", f"{name} <{email}>")


def _git_config_get(key: str, *, runner: Runner) -> str:
    result = runner(
        ["git", "config", "--global", "--get", key],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_config_set(runner: Runner, key: str, value: str) -> None:
    runner(["git", "config", "--global", key, value], capture_output=True, text=True, check=True)


def _read_identity_env(path: Path) -> tuple[str, str] | None:
    valeurs: dict[str, str] = {}
    for ligne in path.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, brut = ligne.partition("=")
        valeurs[cle.strip()] = brut.strip().strip('"').strip("'")
    name, email = valeurs.get("OKF_GIT_NAME"), valeurs.get("OKF_GIT_EMAIL")
    if name and email:
        return name, email
    return None


# --- 2. Clé(s) SSH de dépôt (§ README « Pousser depuis le devcontainer ») -----


def run_ssh_keys(hub_root: Path, *, runner: Runner = subprocess.run) -> StepResult:
    """Délègue à `.devcontainer/deploy-keys.sh`, déjà idempotent.

    N'existe que dans un devcontainer (§ 4.3) : c'est le seul contexte où le
    dépôt cible documente un besoin de clé SSH dédiée. Réutilisé tel quel
    plutôt que réimplémenté, pour ne garder qu'un seul endroit qui raisonne
    sur les deploy keys (piste « automatisation des deploy keys », instance
    sœur `deploy-keys-github-app`, traite l'enregistrement automatique auprès
    de GitHub — hors périmètre ici).
    """
    script = hub_root / DEPLOY_KEYS_SCRIPT
    if not script.is_file():
        return StepResult(
            "clé(s) SSH", "skip",
            f"{DEPLOY_KEYS_SCRIPT} absent — pas de devcontainer détecté, rien à générer ici",
        )
    try:
        result = runner(
            ["bash", str(script)], cwd=str(hub_root),
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return StepResult("clé(s) SSH", "fail", f"{script} : {exc}")

    sortie = ((result.stdout or "") + (result.stderr or "")).strip()
    extrait = sortie[-500:] if sortie else "aucune sortie"
    if result.returncode != 0:
        return StepResult("clé(s) SSH", "fail", f"code {result.returncode} — {extrait}")
    return StepResult("clé(s) SSH", "ok", extrait)


# --- 3. Détection et configuration du client MCP (§ README « Connecter un client Claude ») --


def hub_python(hub_root: Path) -> str:
    venv = hub_root / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def configure_mcp_clients(
    hub_root: Path,
    *,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
) -> list[StepResult]:
    resultats: list[StepResult] = []
    python = hub_python(hub_root)

    claude_bin = which("claude")
    if claude_bin is None:
        resultats.append(
            StepResult(
                "client MCP — Claude Code", "skip",
                "commande `claude` introuvable dans PATH — voir README « Claude Code », "
                "ou « Hub dans un devcontainer » si le client tourne hors de ce conteneur",
            )
        )
    else:
        resultats.append(_register_claude_code(claude_bin, hub_root, python, runner=runner))

    home = home or Path.home()
    chemin = _claude_desktop_config_path(home)
    if not chemin.is_file():
        resultats.append(
            StepResult(
                "client MCP — Claude Desktop", "skip",
                f"config introuvable ({chemin}) — Claude Desktop non détecté sur cette machine",
            )
        )
    else:
        try:
            _write_claude_desktop_entry(chemin, python, hub_root)
        except (OSError, ValueError) as exc:
            resultats.append(StepResult("client MCP — Claude Desktop", "fail", str(exc)))
        else:
            resultats.append(StepResult("client MCP — Claude Desktop", "ok", f"{chemin} mis à jour"))

    return resultats


def _register_claude_code(claude_bin: str, hub_root: Path, python: str, *, runner: Runner) -> StepResult:
    try:
        proc = runner(
            [claude_bin, "mcp", "add", "okf-hub", "--",
             python, "-m", "okf_hub", "--hub-root", str(hub_root)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return StepResult("client MCP — Claude Code", "fail", str(exc))

    sortie = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return StepResult("client MCP — Claude Code", "ok", sortie or "enregistré (claude mcp add)")
    if "already exists" in sortie.lower() or "existe déjà" in sortie.lower():
        return StepResult("client MCP — Claude Code", "ok", "déjà enregistré")
    return StepResult("client MCP — Claude Code", "fail", sortie or f"code {proc.returncode}")


def _claude_desktop_config_path(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _write_claude_desktop_entry(path: Path, python: str, hub_root: Path) -> None:
    brut = path.read_text(encoding="utf-8") if path.stat().st_size else ""
    charge = json.loads(brut) if brut.strip() else {}
    if not isinstance(charge, dict):
        raise ValueError(f"{path} : la racine doit être un objet JSON")
    serveurs = charge.setdefault("mcpServers", {})
    if not isinstance(serveurs, dict):
        raise ValueError(f"{path} : « mcpServers » doit être un objet JSON")
    serveurs["okf-hub"] = {
        "command": python,
        "args": ["-m", "okf_hub", "--hub-root", str(hub_root)],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(charge, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# --- 4. Bootstrap des bases livrées (README « Elles s'installent au premier lancement ») --


def bootstrap_bases(hub_root: Path) -> StepResult:
    try:
        config = HubConfig.load(hub_root)
    except (OSError, ValueError) as exc:
        return StepResult("bases livrées", "fail", f"configuration invalide : {exc}")

    if not config.bootstrap_bundles:
        return StepResult(
            "bases livrées", "skip",
            "bootstrap-bundles: false dans hub-config.yaml — opérateur maîtrise bases/ lui-même",
        )

    installees = bootstrap.deploy_missing(config)
    if installees:
        return StepResult("bases livrées", "ok", f"installées : {', '.join(installees)}")
    return StepResult("bases livrées", "ok", "déjà toutes installées")


# --- Orchestration -------------------------------------------------------------


def run_setup(
    hub_root: Path,
    *,
    interactive: bool = True,
    ask: Callable[[str], str] = input,
    runner: Runner = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Enchaîne les quatre étapes et affiche un rapport. Code de sortie non nul
    si au moins une étape a échoué (`fail`) — une étape `skip` n'est pas un
    échec, c'est un repli documenté."""
    etapes: list[StepResult] = []

    etapes.append(ensure_git_identity(hub_root, interactive=interactive, ask=ask, runner=runner))
    etapes.append(run_ssh_keys(hub_root, runner=runner))
    etapes.extend(configure_mcp_clients(hub_root, runner=runner, which=which, home=home))
    etapes.append(bootstrap_bases(hub_root))

    out("Installation okf-hub — rapport :")
    for etape in etapes:
        out(f"  {etape.render()}")

    echecs = [e for e in etapes if e.status == "fail"]
    if echecs:
        out(
            f"\n{len(echecs)} étape(s) en échec — voir le détail ci-dessus et le "
            "README pour la procédure manuelle correspondante."
        )
        return 1

    sautees = [e for e in etapes if e.status == "skip"]
    if sautees:
        out(f"\n{len(sautees)} étape(s) sans objet ici — voir le README pour la suite si besoin.")
    else:
        out("\nInstallation complète.")
    return 0
