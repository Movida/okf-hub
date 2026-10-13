"""`okf-review resolve --checks` : après les résolutions réelles, les contrôles
approuvés de la base tournent dans la base, et ce qu'ils régénèrent est commité
à part, sous le verrou (spec rév. 4.4, § 7.1, § 8)."""

from __future__ import annotations

import tempfile

import pytest

from okf_hub import review

from conftest import git
from test_verify import CHECK, GEN, _approuve, _base, _checks, _depose, _plan


@pytest.fixture(autouse=True)
def _journaux_dans_le_test(tmp_path, monkeypatch):
    """Un contrôle en échec garde ses journaux pour examen : en test, sous
    `tmp_path`, pas dans le /tmp de la machine."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def _resolve(hub, *args):
    return review.main(["--hub-root", str(hub[0]), "resolve", *args])


def _base_avec_vues(make_bundle, registry):
    return _base(make_bundle, registry, "ma-base", _checks("gen.py", "check.py"),
                 {"gen.py": GEN, "check.py": CHECK}, {"index.md": "# Index\n"})


def test_les_vues_regenerees_sont_commitees_a_part(make_bundle, registry, hub, tmp_path, capsys):
    base = _base_avec_vues(make_bundle, registry)
    p1, p2 = _depose(registry, "ma-base"), _depose(registry, "ma-base")
    _approuve(hub, "ma-base")
    avant = git(base.root, "rev-parse", "HEAD").strip()

    code = _resolve(hub, "ma-base", "--plan", str(_plan(tmp_path, "1.json", p1, "a.md", "# A\n")),
                    "--plan", str(_plan(tmp_path, "2.json", p2, "b.md", "# B\n")), "--checks")

    assert code == 0, capsys.readouterr()
    commits = git(base.root, "log", "--format=%s", f"{avant}..HEAD").splitlines()
    assert commits[0] == "regen: vues régénérées après 2 résolution(s)"
    assert [c.split(":")[0] for c in commits[1:]] == ["integrate", "integrate"]
    assert git(base.root, "show", "--name-only", "--format=", "HEAD").split() == ["knowledge/index.md"]
    assert "Checks: " in git(base.root, "log", "-1", "--format=%B")
    assert "a.md" in (base.corpus_dir / "index.md").read_text(encoding="utf-8")
    assert git(base.root, "status", "--porcelain").strip() == ""


def test_controle_en_echec_rien_de_plus_n_est_commite(make_bundle, registry, hub, tmp_path, capsys):
    base = _base_avec_vues(make_bundle, registry)
    pid = _depose(registry, "ma-base")
    _approuve(hub, "ma-base")

    code = _resolve(hub, "ma-base", "--plan",
                    str(_plan(tmp_path, "1.json", pid, "a.md", "# A\n\nINTERDIT\n")), "--checks")

    assert code == 1
    assert git(base.root, "log", "-1", "--format=%s").startswith("integrate:")
    assert "mot interdit dans : a.md" in capsys.readouterr().out
    assert "knowledge/index.md" in git(base.root, "status", "--porcelain")  # laissé pour examen


def test_tout_refus_precede_la_premiere_resolution(make_bundle, registry, hub, tmp_path, capsys):
    base = _base_avec_vues(make_bundle, registry)
    pid = _depose(registry, "ma-base")
    plan = str(_plan(tmp_path, "1.json", pid, "a.md", "# A\n"))
    tete = git(base.root, "rev-parse", "HEAD").strip()

    assert _resolve(hub, "ma-base", "--plan", plan, "--checks") == 4      # non approuvée
    assert "trusted-checks" in capsys.readouterr().err

    _approuve(hub, "ma-base")
    (base.corpus_dir / "brouillon.md").write_text("en cours\n", encoding="utf-8")
    assert _resolve(hub, "ma-base", "--plan", plan, "--checks") == 4      # arbre sale
    assert "brouillon.md" in capsys.readouterr().err

    assert _resolve(hub, "ma-base", "--plan", plan, "--checks", "--dry-run") == 4
    assert "okf-review verify" in capsys.readouterr().err
    assert git(base.root, "rev-parse", "HEAD").strip() == tete


def test_reprise_les_controles_tournent_meme_si_tout_est_deja_resolu(make_bundle, registry, hub, tmp_path, capsys):
    """Un lot interrompu après ses résolutions se termine en relançant la commande."""
    base = _base_avec_vues(make_bundle, registry)
    pid = _depose(registry, "ma-base")
    plan = str(_plan(tmp_path, "1.json", pid, "a.md", "# A\n"))
    assert _resolve(hub, "ma-base", "--plan", plan) == 0
    capsys.readouterr()

    _approuve(hub, "ma-base")
    assert _resolve(hub, "ma-base", "--plan", plan, "--checks") == 0
    out = capsys.readouterr().out
    assert "Déjà résolu, sauté" in out and "vues régénérées commitées" in out
    assert git(base.root, "log", "-1", "--format=%s") .strip() == "regen: vues régénérées après 0 résolution(s)"
