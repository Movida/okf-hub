"""`okf-review verify` : plans rejoués sur une copie jetable, contrôles de la
base lancés après approbation de l'opérateur (rév. 4.3, § 3.3, § 7.1 étape 4 bis, § 8)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from okf_hub import review
from okf_hub.config import HubConfig
from okf_hub.tools import propose_tool

from conftest import git

#: Contrôle de base : refuse tout document du corpus qui contient « INTERDIT ».
CHECK = (
    "import pathlib, sys\n"
    "fautifs = [p.name for p in pathlib.Path('knowledge').rglob('*.md')\n"
    "           if 'INTERDIT' in p.read_text(encoding='utf-8')]\n"
    "if fautifs:\n"
    "    print('mot interdit dans : ' + ', '.join(fautifs))\n"
    "    sys.exit(1)\n"
    "print('corpus conforme')\n"
)

#: Générateur de vue : réécrit un fichier suivi, comme un `gen_index.py`.
GEN = (
    "import pathlib\n"
    "docs = sorted(p.name for p in pathlib.Path('knowledge').glob('*.md') if p.name != 'index.md')\n"
    "pathlib.Path('knowledge/index.md').write_text('# Index\\n\\n' + '\\n'.join(docs) + '\\n',"
    " encoding='utf-8')\n"
)


def _checks(*scripts: str) -> list[str]:
    return [f"{sys.executable} _work/{s}" for s in scripts]


def _base(make_bundle, registry, nom, checks=None, scripts=None, docs=None):
    governance = {"rules": "./GOVERNANCE.md"}
    if checks is not None:
        governance["checks"] = checks
    b = make_bundle(nom, name=nom, governance=governance, git_init=False)
    for rel, texte in (scripts or {}).items():
        chemin = b.root / "_work" / rel
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(texte, encoding="utf-8")
    for rel, texte in (docs or {}).items():
        b.doc(rel, texte)
    b.init_git()
    registry.scan()
    return registry.get(nom)


def _depose(registry, base: str) -> str:
    out = propose_tool.run(registry, {
        "base": base, "type": "addition", "concerns": "un sujet",
        "content": "Un fait.", "sources": ["constat"], "confidence": "high",
        "submitted_by": "session-test",
    })
    return next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("id :"))


def _plan(tmp_path, nom, pid, path, contenu):
    chemin = tmp_path / nom
    chemin.write_text(json.dumps({
        "summary": nom, "reviewed_by": "human:test",
        "resolutions": [{"id": pid, "resolution": "accepted", "integrated_into": [path]}],
        "edits": [{"path": path, "content": contenu}],
    }), encoding="utf-8")
    return chemin


def _approuve(hub, *noms):
    hub_root, _ = hub
    (hub_root / "hub-config.local.yaml").write_text(
        yaml.safe_dump({"trusted-checks": list(noms)}), encoding="utf-8"
    )


def _verify(hub, tmp_path, *args):
    hub_root, _ = hub
    workdir = Path(tempfile.mkdtemp(prefix="copies-", dir=tmp_path))  # un par appel
    code = review.main(["--hub-root", str(hub_root), "verify", *args, "--workdir", str(workdir)])
    return code, list(workdir.iterdir())


def _intacte(base, head_avant, pid):
    assert git(base.root, "rev-parse", "HEAD").strip() == head_avant
    assert (base.root / "proposals" / "pending" / f"{pid}.md").is_file()
    assert git(base.root, "status", "--porcelain").strip() == ""


# --- approbation (§ 8) -------------------------------------------------------


def test_base_non_approuvee_refusee_avant_toute_copie(make_bundle, registry, hub, tmp_path, capsys):
    base = _base(make_bundle, registry, "ma-base", _checks("check.py"), {"check.py": CHECK})
    pid = _depose(registry, "ma-base")
    plan = _plan(tmp_path, "p.json", pid, "neuf.md", "# Neuf\n")

    code, copies = _verify(hub, tmp_path, "ma-base", "--plan", str(plan))

    assert code == 4
    err = capsys.readouterr().err
    assert "_work/check.py" in err          # l'opérateur voit ce qu'il approuverait
    assert "trusted-checks" in err and "hub-config.local.yaml" in err
    assert copies == []


def test_l_approbation_porte_sur_les_manifestes_de_head(make_bundle, registry, hub, tmp_path, capsys):
    """Le répertoire de travail ne déclare plus de contrôles, HEAD si : c'est HEAD
    que la copie exécute, c'est donc lui que l'approbation doit couvrir."""
    base = _base(make_bundle, registry, "ma-base", _checks("check.py"), {"check.py": CHECK})
    pid = _depose(registry, "ma-base")
    manifeste = base.root / "okf-bundle.yaml"
    donnees = yaml.safe_load(manifeste.read_text(encoding="utf-8"))
    del donnees["governance"]["checks"]
    manifeste.write_text(yaml.safe_dump(donnees, allow_unicode=True), encoding="utf-8")

    code, copies = _verify(hub, tmp_path, "ma-base", "--plan", str(_plan(tmp_path, "p.json", pid, "n.md", "# N\n")))

    assert code == 4
    assert "trusted-checks" in capsys.readouterr().err
    assert copies == []                     # copie supprimée après le refus


def test_checks_mal_formes_n_invalident_pas_le_bundle(make_bundle, registry, hub, tmp_path, capsys):
    base = _base(make_bundle, registry, "ma-base", "pas une liste")
    assert base.manifest.checks == ()
    assert any("governance.checks" in w for w in base.manifest.warnings)

    _approuve(hub, "ma-base")
    pid = _depose(registry, "ma-base")
    code, _ = _verify(hub, tmp_path, "ma-base", "--plan", str(_plan(tmp_path, "p.json", pid, "n.md", "# N\n")))
    assert code == 4
    assert "governance.checks" in capsys.readouterr().err


def test_config_locale_surcharge_et_valide(hub):
    hub_root, _ = hub
    _approuve(hub, "a", "b")
    assert HubConfig.load(hub_root).trusted_checks == {"a", "b"}

    (hub_root / "hub-config.local.yaml").write_text("trusted-checks: a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="trusted-checks"):
        HubConfig.load(hub_root)


# --- rejeu et contrôles ------------------------------------------------------


def test_plans_rejoues_et_controles_reussis_sans_toucher_la_base(make_bundle, registry, hub, tmp_path, capsys):
    base = _base(make_bundle, registry, "ma-base", _checks("gen.py", "check.py"),
                 {"gen.py": GEN, "check.py": CHECK}, {"index.md": "# Index\n"})
    pid = _depose(registry, "ma-base")
    head = git(base.root, "rev-parse", "HEAD").strip()
    _approuve(hub, "ma-base")

    plan = _plan(tmp_path, "p.json", pid, "neuf.md", "# Neuf\n\nRien d'anormal.\n")
    code, copies = _verify(hub, tmp_path, "ma-base", "--plan", str(plan))

    out = capsys.readouterr().out
    assert code == 0, out
    assert "✓ plan 1" in out and "integrate:" in out
    assert out.count("✓ (") == 2            # gen puis check
    assert copies == []                     # réussite : copie supprimée
    _intacte(base, head, pid)
    # Le journal partagé ne rapporte pas un commit fait dans la copie.
    journal = (hub[0] / "hub.log").read_text(encoding="utf-8")
    assert "découverte" in journal and "integrate:" not in journal


def test_workdir_absent_cree(make_bundle, registry, hub, tmp_path, capsys):
    """Vu le 14/10 : un `--workdir` qui n'existait pas finissait en FileNotFoundError."""
    _base(make_bundle, registry, "ma-base", _checks("check.py"), {"check.py": CHECK})
    pid = _depose(registry, "ma-base")
    _approuve(hub, "ma-base")
    workdir = tmp_path / "pas" / "encore" / "la"

    code = review.main(["--hub-root", str(hub[0]), "verify", "ma-base", "--plan",
                        str(_plan(tmp_path, "p.json", pid, "n.md", "# N\n")),
                        "--workdir", str(workdir)])

    assert code == 0, capsys.readouterr().out
    assert workdir.is_dir() and list(workdir.iterdir()) == []


def test_controle_en_echec_rapporte_et_copie_conservee(make_bundle, registry, hub, tmp_path, capsys):
    base = _base(make_bundle, registry, "ma-base", _checks("check.py"), {"check.py": CHECK})
    pid = _depose(registry, "ma-base")
    head = git(base.root, "rev-parse", "HEAD").strip()
    _approuve(hub, "ma-base")

    plan = _plan(tmp_path, "p.json", pid, "neuf.md", "# Neuf\n\nINTERDIT\n")
    code, copies = _verify(hub, tmp_path, "ma-base", "--plan", str(plan))

    out = capsys.readouterr().out
    assert code == 1
    assert "✗ code 1" in out and "mot interdit dans : neuf.md" in out
    assert len(copies) == 1                 # échec : copie conservée pour examen
    _intacte(base, head, pid)


def test_plan_refuse_arrete_la_verification(make_bundle, registry, hub, tmp_path, capsys):
    _base(make_bundle, registry, "ma-base", _checks("check.py"), {"check.py": CHECK})
    _approuve(hub, "ma-base")
    plan = _plan(tmp_path, "p.json", "prop-2026-01-01-0000", "n.md", "# N\n")

    code, _ = _verify(hub, tmp_path, "ma-base", "--plan", str(plan))

    out = capsys.readouterr().out
    assert code == 1
    assert "✗ plan 1" in out and "NOT_FOUND" in out
    assert "→" not in out                   # aucun contrôle lancé


def test_each_designe_le_plan_fautif_et_restaure_les_vues(make_bundle, registry, hub, tmp_path, capsys):
    base = _base(make_bundle, registry, "ma-base", _checks("gen.py", "check.py"),
                 {"gen.py": GEN, "check.py": CHECK}, {"index.md": "# Index\n"})
    p1, p2 = _depose(registry, "ma-base"), _depose(registry, "ma-base")
    _approuve(hub, "ma-base")

    plan1 = _plan(tmp_path, "01.json", p1, "a.md", "# A\n")
    plan2 = _plan(tmp_path, "02.json", p2, "b.md", "# B\n\nINTERDIT\n")
    code, _ = _verify(hub, tmp_path, "ma-base", "--plan", str(plan1), "--plan", str(plan2), "--each")

    out = capsys.readouterr().out
    assert code == 1
    assert out.index("✓ plan 2") > out.index("✓ (") > out.index("✓ plan 1")
    assert out.index("✗ code 1") > out.index("✓ plan 2")
    assert "mot interdit dans : b.md" in out

    # Entre deux plans, la vue régénérée revient à son état commité : le plan
    # suivant a été rédigé contre lui, pas contre une régénération qu'il n'a pas vue.
    plan2 = _plan(tmp_path, "02.json", p2, "b.md", "# B\n")
    code, copies = _verify(
        hub, tmp_path, "ma-base", "--plan", str(plan1), "--plan", str(plan2), "--each", "--keep"
    )
    assert code == 0
    copie = copies[0] / "bases" / "ma-base"
    assert git(copie, "status", "--porcelain").strip() == ""
    assert (copie / "knowledge" / "index.md").read_text(encoding="utf-8") == "# Index\n"


# --- lot sur plusieurs bases, isolation -------------------------------------

#: Lit un document de la voisine par chemin relatif, et tente d'y écrire.
CHECK_VOISINE = (
    "import pathlib, sys\n"
    "voisin = pathlib.Path('../voisine/knowledge/socle.md')\n"
    "(voisin.parent / 'ecrit-par-le-controle.md').write_text('x', encoding='utf-8')\n"
    "if not voisin.is_file():\n"
    "    print('référence amont absente : socle.md')\n"
    "    sys.exit(1)\n"
)


def test_lot_multi_bases_et_voisines_clonees(make_bundle, registry, hub, tmp_path, capsys):
    voisine = _base(make_bundle, registry, "voisine")
    base = _base(make_bundle, registry, "ma-base", _checks("check.py"), {"check.py": CHECK_VOISINE})
    pv, pb = _depose(registry, "voisine"), _depose(registry, "ma-base")
    head_v = git(voisine.root, "rev-parse", "HEAD").strip()
    _approuve(hub, "ma-base")

    plan_v = _plan(tmp_path, "v.json", pv, "socle.md", "# Socle\n")
    plan_b = _plan(tmp_path, "b.json", pb, "usage.md", "# Usage\n\nVoir voisine:/socle.md\n")

    # Seule, la base échoue : le document amont n'existe pas encore.
    code, _ = _verify(hub, tmp_path, "ma-base", "--plan", str(plan_b))
    assert code == 1
    capsys.readouterr()

    # En lot, dans l'ordre d'exécution, la voisine modifiée est celle de la copie.
    code, _ = _verify(hub, tmp_path, "--plan", f"voisine:{plan_v}", "--plan", f"ma-base:{plan_b}")
    assert code == 0, capsys.readouterr().out

    # Le contrôle a écrit chez la voisine : dans la copie, jamais dans la vraie.
    assert not (voisine.corpus_dir / "ecrit-par-le-controle.md").exists()
    _intacte(voisine, head_v, pv)


def test_plan_sans_base_ni_prefixe_refuse(make_bundle, registry, hub, tmp_path, capsys):
    _base(make_bundle, registry, "ma-base")
    code, _ = _verify(hub, tmp_path, "--plan", str(tmp_path / "p.json"))
    assert code == 4
    assert "préfixez" in capsys.readouterr().err
