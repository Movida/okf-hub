"""`okf-hub setup` (piste « point d'entrée d'installation unique »).

Aucun test ici ne touche au réseau, à l'identité git réelle de la machine qui
exécute la suite, ni à un vrai fichier de config Claude Desktop : `subprocess.run`
et `shutil.which` sont injectés (`runner`/`which`), et le répertoire « home »
utilisé pour détecter Claude Desktop est toujours un `tmp_path`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from okf_hub import setup_cmd


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FauxRunner:
    """Enregistre les commandes reçues, répond selon une table préparée par le test."""

    def __init__(self) -> None:
        self.appels: list[list[str]] = []
        self.reponses: dict[str, subprocess.CompletedProcess] = {}

    def pour(self, prefixe: str, reponse: subprocess.CompletedProcess) -> None:
        self.reponses[prefixe] = reponse

    def __call__(self, cmd, **kwargs):
        self.appels.append(list(cmd))
        cle = " ".join(cmd[:3])
        for prefixe, reponse in self.reponses.items():
            if cle.startswith(prefixe):
                return reponse
        return _cp()


# --- identité git ---------------------------------------------------------------


def test_identite_deja_configuree_globalement_ne_redemande_rien(tmp_path):
    runner = FauxRunner()
    runner.pour("git config --global", _cp(0, stdout="Ada Lovelace\n"))
    resultat = setup_cmd.ensure_git_identity(tmp_path, interactive=True, ask=_fail_si_appelee, runner=runner)
    assert resultat.status == "ok"
    assert "déjà configurée" in resultat.detail


def test_identite_absente_et_non_interactif_saute_l_etape(tmp_path):
    runner = FauxRunner()
    runner.pour("git config --global", _cp(1))
    resultat = setup_cmd.ensure_git_identity(tmp_path, interactive=False, runner=runner)
    assert resultat.status == "skip"


def test_identite_lue_depuis_git_identity_env_existant(tmp_path):
    (tmp_path / ".devcontainer").mkdir()
    env = tmp_path / ".devcontainer" / "git-identity.env"
    env.write_text('OKF_GIT_NAME="Ada Lovelace"\nOKF_GIT_EMAIL="ada@users.noreply.github.com"\n')
    runner = FauxRunner()
    runner.pour("git config --global", _cp(1))
    resultat = setup_cmd.ensure_git_identity(tmp_path, interactive=True, ask=_fail_si_appelee, runner=runner)
    assert resultat.status == "ok"
    assert "git-identity.env" in resultat.detail
    assert ["git", "config", "--global", "user.name", "Ada Lovelace"] in runner.appels
    assert ["git", "config", "--global", "user.email", "ada@users.noreply.github.com"] in runner.appels


def test_identite_saisie_interactive_ecrit_git_identity_env_dans_un_devcontainer(tmp_path):
    (tmp_path / ".devcontainer").mkdir()
    runner = FauxRunner()
    runner.pour("git config --global", _cp(1))
    reponses = iter(["Ada Lovelace", "ada@users.noreply.github.com"])
    resultat = setup_cmd.ensure_git_identity(
        tmp_path, interactive=True, ask=lambda _prompt: next(reponses), runner=runner
    )
    assert resultat.status == "ok"
    env = tmp_path / ".devcontainer" / "git-identity.env"
    assert env.is_file()
    assert "Ada Lovelace" in env.read_text()


def test_identite_saisie_vide_est_sautee(tmp_path):
    runner = FauxRunner()
    runner.pour("git config --global", _cp(1))
    resultat = setup_cmd.ensure_git_identity(tmp_path, interactive=True, ask=lambda _p: "", runner=runner)
    assert resultat.status == "skip"


def _fail_si_appelee(_prompt):
    raise AssertionError("ask() ne devait pas être appelée")


# --- clé(s) SSH -------------------------------------------------------------------


def test_ssh_sans_devcontainer_est_sans_objet(tmp_path):
    resultat = setup_cmd.run_ssh_keys(tmp_path, runner=FauxRunner())
    assert resultat.status == "skip"
    assert "devcontainer" in resultat.detail


def test_ssh_avec_devcontainer_delegue_au_script_existant(tmp_path):
    devc = tmp_path / ".devcontainer"
    devc.mkdir()
    script = devc / "deploy-keys.sh"
    script.write_text("#!/bin/sh\necho ok\n")
    script.chmod(0o755)

    runner = FauxRunner()
    runner.pour("bash", _cp(0, stdout="clé prête\n"))
    resultat = setup_cmd.run_ssh_keys(tmp_path, runner=runner)
    assert resultat.status == "ok"
    assert runner.appels == [["bash", str(script)]]


def test_ssh_script_en_echec_est_rapporte_comme_echec(tmp_path):
    devc = tmp_path / ".devcontainer"
    devc.mkdir()
    (devc / "deploy-keys.sh").write_text("#!/bin/sh\nexit 1\n")

    runner = FauxRunner()
    runner.pour("bash", _cp(1, stderr="boum"))
    resultat = setup_cmd.run_ssh_keys(tmp_path, runner=runner)
    assert resultat.status == "fail"
    assert "boum" in resultat.detail


# --- client MCP ---------------------------------------------------------------------


def test_aucun_client_detecte_saute_les_deux_etapes(tmp_path):
    resultats = setup_cmd.configure_mcp_clients(
        tmp_path, runner=FauxRunner(), which=lambda _n: None, home=tmp_path
    )
    assert [r.status for r in resultats] == ["skip", "skip"]


def test_claude_code_detecte_est_enregistre(tmp_path):
    runner = FauxRunner()
    runner.pour("/usr/bin/claude", _cp(0, stdout="added\n"))
    resultats = setup_cmd.configure_mcp_clients(
        tmp_path, runner=runner, which=lambda n: "/usr/bin/claude" if n == "claude" else None,
        home=tmp_path,
    )
    claude_code = next(r for r in resultats if "Claude Code" in r.name)
    assert claude_code.status == "ok"
    (appel,) = [a for a in runner.appels if a[0] == "/usr/bin/claude"]
    assert appel[:4] == ["/usr/bin/claude", "mcp", "add", "okf-hub"]
    assert appel[-2:] == ["--hub-root", str(tmp_path)]


def test_claude_code_deja_enregistre_n_est_pas_un_echec(tmp_path):
    runner = FauxRunner()
    runner.pour("/usr/bin/claude", _cp(1, stderr="MCP server okf-hub already exists"))
    resultats = setup_cmd.configure_mcp_clients(
        tmp_path, runner=runner, which=lambda n: "/usr/bin/claude" if n == "claude" else None,
        home=tmp_path,
    )
    claude_code = next(r for r in resultats if "Claude Code" in r.name)
    assert claude_code.status == "ok"


def test_claude_desktop_absent_est_sans_objet(tmp_path):
    resultats = setup_cmd.configure_mcp_clients(
        tmp_path, runner=FauxRunner(), which=lambda _n: None, home=tmp_path
    )
    desktop = next(r for r in resultats if "Desktop" in r.name)
    assert desktop.status == "skip"


def test_claude_desktop_present_recoit_l_entree_okf_hub_sans_perdre_le_reste(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    chemin = tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
    chemin.parent.mkdir(parents=True)
    chemin.write_text(json.dumps({"mcpServers": {"autre": {"command": "x"}}}))

    resultats = setup_cmd.configure_mcp_clients(
        tmp_path, runner=FauxRunner(), which=lambda _n: None, home=tmp_path
    )
    desktop = next(r for r in resultats if "Desktop" in r.name)
    assert desktop.status == "ok"

    ecrit = json.loads(chemin.read_text())
    assert ecrit["mcpServers"]["autre"] == {"command": "x"}
    assert ecrit["mcpServers"]["okf-hub"]["args"][-1] == str(tmp_path)


def test_claude_desktop_config_invalide_est_un_echec_pas_un_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    chemin = tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
    chemin.parent.mkdir(parents=True)
    chemin.write_text("[]")  # racine non-objet

    resultats = setup_cmd.configure_mcp_clients(
        tmp_path, runner=FauxRunner(), which=lambda _n: None, home=tmp_path
    )
    desktop = next(r for r in resultats if "Desktop" in r.name)
    assert desktop.status == "fail"


# --- bootstrap des bases livrées -----------------------------------------------


def test_bootstrap_installe_les_bases_livrees(tmp_path):
    hub_root = tmp_path
    (hub_root / "bases").mkdir()
    (hub_root / "hub-config.yaml").write_text(yaml.safe_dump({"bases-dir": "./bases"}))
    bundle = hub_root / "bundles" / "guide"
    (bundle / "knowledge").mkdir(parents=True)
    (bundle / "okf-bundle.yaml").write_text(
        yaml.safe_dump(
            {
                "bundle-spec": "0.1",
                "name": "guide",
                "title": "Guide",
                "description": "Bundle de test.",
                "corpus-dir": "knowledge",
                "governance": {"rules": "./GOVERNANCE.md"},
            }
        )
    )
    (bundle / "GOVERNANCE.md").write_text("# Gouvernance\n")
    (bundle / "knowledge" / "a.md").write_text("# A\n")

    resultat = setup_cmd.bootstrap_bases(hub_root)
    assert resultat.status == "ok"
    assert "guide" in resultat.detail
    assert (hub_root / "bases" / "guide" / ".git").is_dir()


def test_bootstrap_desactive_par_la_config_est_sans_objet(tmp_path):
    hub_root = tmp_path
    (hub_root / "bases").mkdir()
    (hub_root / "hub-config.yaml").write_text(
        yaml.safe_dump({"bases-dir": "./bases", "bootstrap-bundles": False})
    )
    resultat = setup_cmd.bootstrap_bases(hub_root)
    assert resultat.status == "skip"


# --- orchestration --------------------------------------------------------------


def test_run_setup_code_de_sortie_non_nul_si_une_etape_echoue(tmp_path, capsys):
    hub_root = tmp_path
    (hub_root / "bases").mkdir()
    (hub_root / "hub-config.yaml").write_text(yaml.safe_dump({"bases-dir": "./bases"}))
    devc = hub_root / ".devcontainer"
    devc.mkdir()
    (devc / "deploy-keys.sh").write_text("#!/bin/sh\nexit 1\n")

    runner = FauxRunner()
    runner.pour("git config --global", _cp(0, stdout="x\n"))
    runner.pour("bash", _cp(1, stderr="boum"))

    code = setup_cmd.run_setup(
        hub_root, interactive=False, runner=runner, which=lambda _n: None, home=tmp_path
    )
    assert code == 1
    assert "boum" in capsys.readouterr().out


def test_run_setup_ne_signale_pas_les_etapes_sautees_comme_echec(tmp_path, capsys):
    hub_root = tmp_path
    (hub_root / "bases").mkdir()
    (hub_root / "hub-config.yaml").write_text(yaml.safe_dump({"bases-dir": "./bases"}))

    runner = FauxRunner()
    runner.pour("git config --global", _cp(0, stdout="x\n"))

    code = setup_cmd.run_setup(
        hub_root, interactive=False, runner=runner, which=lambda _n: None, home=tmp_path
    )
    assert code == 0
    assert "sans objet" in capsys.readouterr().out
