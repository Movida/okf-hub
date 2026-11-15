"""okf-ranger : la revue en mode agent lancée sans humain, sous permissions fixes."""

from __future__ import annotations

import json
import stat
import sys

import pytest
import yaml

from okf_hub import ranger, review
from okf_hub.tools import propose_tool

from test_mode_agent import DOC, SOURCE, _local, _plan, _resolve


@pytest.fixture
def poste(hub, make_bundle, registry):
    hub_root, _ = hub
    gouv = {"rules": "./GOVERNANCE.md", "review": "agent"}
    for nom in ("base-p", "base-q"):
        b = make_bundle(nom, name=nom, git_init=False, governance=gouv)
        b.doc("sso.md", DOC)
        b.init_git()
    h = make_bundle("base-h", name="base-h", git_init=False)
    h.doc("sso.md", DOC)
    h.init_git()
    _local(hub_root, **{"agent-review": ["base-p", "base-q", "base-h"]})
    registry.config = registry.config.__class__.load(hub_root)
    registry.scan()
    return hub_root, registry


@pytest.fixture
def faux_claude(tmp_path, monkeypatch):
    """Un `claude` qui note ses arguments, son dossier et son environnement."""
    journal = tmp_path / "appels.jsonl"
    script = tmp_path / "claude"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"open({str(journal)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd(), "
        f"'agent': os.environ.get('OKF_REVIEW_AGENT')}}) + '\\n')\nprint('reste pour l humain : rien')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("OKF_RANGER_CLAUDE", str(script))
    return lambda: [json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines()] if journal.exists() else []


def _depose(registry, base):
    propose_tool.run(registry, {
        "base": base, "type": "correction", "concerns": "reconnexion SSO",
        "content": "Le bouton a été déplacé dans le menu profil depuis la 3.2.",
        "sources": [SOURCE], "confidence": "high", "submitted_by": "session-test",
    })


def test_rien_en_attente_aucun_modele_lance(poste, faux_claude, capsys):
    hub_root, _ = poste
    assert ranger.main(["--hub-root", str(hub_root), "base-p"]) == 0
    assert "rien en attente" in capsys.readouterr().out
    assert faux_claude() == []


def test_base_sans_deux_cles_refusee(poste, faux_claude, capsys):
    hub_root, registry = poste
    _depose(registry, "base-h")
    assert ranger.main(["--hub-root", str(hub_root), "base-h"]) == 1
    assert "review: agent" in capsys.readouterr().out
    assert faux_claude() == []


def test_session_lancee_sous_permissions_fixes(poste, faux_claude, capsys):
    hub_root, registry = poste
    _depose(registry, "base-q")
    assert ranger.main(["--hub-root", str(hub_root), "--agent", "rangeur-x"]) == 1  # base-h refusée
    (appel,) = faux_claude()
    argv = appel["argv"]
    permis = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    okf_review = (hub_root / "bin" / "okf-review").resolve()
    assert permis == [f"Bash({okf_review}:*)", "Read", "Glob", "Grep"]
    refuses = argv[argv.index("--disallowedTools") + 1:]
    assert f"Edit(/{hub_root.resolve()}/**)" in refuses
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert appel["agent"] == "rangeur-x"
    assert "okf-ranger-base-q-" in appel["cwd"] and not str(hub_root.resolve()) in appel["cwd"]
    assert "--agent rangeur-x" in argv[1] and "base-q" in argv[1]
    sortie = capsys.readouterr().out
    assert "base-q : 1 → 1 en attente" in sortie and "base-p : rien en attente" in sortie


def test_sec_ne_lance_rien(poste, faux_claude, capsys):
    hub_root, registry = poste
    _depose(registry, "base-p")
    assert ranger.main(["--hub-root", str(hub_root), "--sec", "base-p"]) == 0
    assert "lancerait" in capsys.readouterr().out and faux_claude() == []


def test_agent_impose_interdit_le_mode_humain(poste, tmp_path, capsys, monkeypatch):
    hub_root, registry = poste
    _depose(registry, "base-p")
    pid = review.load_pending(registry.get("base-p"))[0].id
    plan = _plan(tmp_path, pid, [{"path": "sso.md", "append": f"\nVoir [source]({SOURCE}).\n"}])
    monkeypatch.setenv(review.AGENT_IMPOSE, "rangeur")
    assert _resolve(hub_root, "base-p", plan, None) == 4
    assert _resolve(hub_root, "base-p", plan, "autre") == 4
    assert review.main(["--hub-root", str(hub_root), "reconcile", "base-p", "--apply"]) == 4
    assert "mode agent" in capsys.readouterr().err
    assert _resolve(hub_root, "base-p", plan, "rangeur") == 0
