"""kb_search, kb_list, kb_governance : classement, exclusions, plafonds (§ 5.1, 5.2, 5.4). Jalon J1."""

from __future__ import annotations

import pytest

from okf_hub.errors import INVALID_INPUT, ToolError
from okf_hub.textutil import CHAR_CAP, BudgetedWriter
from okf_hub.tools import governance_tool, list_tool, search_tool


def build(make_bundle, registry, docs: dict[str, str], **manifest):
    b = make_bundle("ma-base", name="ma-base", git_init=False, **manifest)
    for rel, text in docs.items():
        b.doc(rel, text)
    b.init_git()
    registry.scan()
    return b


# --- kb_search ---------------------------------------------------------------


def test_and_strict_prioritaire(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {
            "a.md": "# A\n\nreconnexion SSO en panne\n",
            "b.md": "# B\n\nreconnexion seulement\n",
            "c.md": "# C\n\nSSO seulement\n",
        },
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "reconnexion SSO"})
    assert "a.md" in out
    assert "b.md" not in out and "c.md" not in out
    assert "résultats partiels" not in out


def test_repli_or_signale_explicitement(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {"b.md": "# B\n\nreconnexion seulement\n", "c.md": "# C\n\nSSO seulement\n"},
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "reconnexion SSO"})
    # § 5.2 : mention explicite du repli.
    assert "aucun document ne contient tous les termes — résultats partiels" in out
    assert "b.md" in out and "c.md" in out


def test_repli_or_classe_par_nombre_de_termes_touches(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {
            "un.md": "# Un\n\nalpha seulement\n",
            "deux.md": "# Deux\n\nalpha et beta ensemble\n",
        },
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "alpha beta gamma"})
    assert out.index("deux.md") < out.index("un.md")


def test_recherche_insensible_a_la_casse(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\nProcédure de RECONNEXION\n"})
    out = search_tool.run(registry, {"base": "ma-base", "query": "reconnexion"})
    assert "a.md" in out


def test_mode_regex(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\nincident #4521 signalé\n"})
    out = search_tool.run(
        registry, {"base": "ma-base", "query": r"incident #\d{4}", "mode": "regex"}
    )
    assert "a.md" in out


def test_regex_invalide_remonte_invalid_input(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\ntexte\n"})
    with pytest.raises(ToolError) as exc:
        search_tool.run(registry, {"base": "ma-base", "query": "[a-", "mode": "regex"})
    assert exc.value.code == INVALID_INPUT
    # Le message de ripgrep est remonté tel quel (§ 5.2).
    assert "regex" in exc.value.message.lower() or "unclosed" in exc.value.message.lower()


def test_mode_keyword_traite_les_termes_comme_litteraux(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\nversion 3.2(beta) publiée\n"})
    # En mode keyword, « 3.2(beta) » ne doit pas être interprété comme une regex.
    out = search_tool.run(registry, {"base": "ma-base", "query": "3.2(beta)"})
    assert "a.md" in out


def test_extraits_avec_contexte_et_plafond_par_document(hub, make_bundle, registry):
    lignes = "\n".join(f"ligne {i} cible" if i % 5 == 0 else f"ligne {i}" for i in range(40))
    build(make_bundle, registry, {"a.md": "# A\n\n" + lignes + "\n"})
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    # Au plus 3 extraits par document (§ 5.2).
    assert out.count("  L") <= 3


def test_frontmatter_limite_a_title_dates_tags(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {
            "a.md": (
                "---\ntitle: Titre A\ntags: [sso]\nlast-verified: 2026-01-15\n"
                "type: Reference\nresource: https://interne.test/secret\n"
                "description: une longue description qui n'a pas à remonter\n---\n\n"
                "# A\n\ncible\n"
            )
        },
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    assert "Titre A" in out and "sso" in out and "last-verified" in out
    assert "interne.test/secret" not in out


def test_exclusions_transverses(hub, make_bundle, registry):
    b = build(make_bundle, registry, {"a.md": "# A\n\nmot-temoin\n"})
    # Ces fichiers sont hors corpus par construction (§ 3.3) ; on vérifie que la
    # défense en profondeur (§ 5.2) tient même s'ils atterrissent dans le corpus.
    (b.root / "knowledge" / "CLAUDE.md").write_text("mot-temoin\n", encoding="utf-8")
    (b.root / "knowledge" / "GOVERNANCE.md").write_text("mot-temoin\n", encoding="utf-8")
    (b.root / "knowledge" / "proposals").mkdir()
    (b.root / "knowledge" / "proposals" / "p.md").write_text("mot-temoin\n", encoding="utf-8")
    registry.scan()

    out = search_tool.run(registry, {"base": "ma-base", "query": "mot-temoin"})
    assert "a.md" in out
    assert "CLAUDE.md" not in out
    assert "GOVERNANCE.md" not in out
    assert "proposals" not in out
    assert registry.get("ma-base").count_documents() == 2  # a.md + exemple.md


def test_troncature_de_recherche_signalee(hub, make_bundle, registry):
    gros = "cible " + ("remplissage " * 900)
    docs = {f"doc{i:02d}.md": f"---\ntitle: Doc {i}\n---\n\n# Doc {i}\n\n{gros}\n" for i in range(25)}
    build(make_bundle, registry, docs)
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible", "max_results": 25})
    assert "[résultats tronqués" in out
    assert len(out) < CHAR_CAP * 1.5


def test_max_results_borne(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\ncible\n"})
    with pytest.raises(ToolError) as exc:
        search_tool.run(registry, {"base": "ma-base", "query": "cible", "max_results": 99})
    assert exc.value.code == INVALID_INPUT


def test_aucun_resultat(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\ntexte\n"})
    out = search_tool.run(registry, {"base": "ma-base", "query": "introuvable-xyz"})
    assert "Aucun résultat" in out


# --- kb_list -----------------------------------------------------------------


def test_kb_list_resume_les_bases(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n"}, version="2.0.0")
    out = list_tool.run(registry, {})
    assert "ma-base" in out and "Base de test" in out
    assert "documents : 2" in out
    assert "version : 2.0.0" in out


def test_kb_list_pending_concerns(hub, make_bundle, registry):
    b = build(make_bundle, registry, {"a.md": "# A\n"})
    pending = b.root / "proposals" / "pending"
    pending.mkdir(parents=True)
    (pending / "prop-2026-01-01-aaaa.md").write_text(
        "---\nid: prop-2026-01-01-aaaa\ntype: correction\n"
        "concerns: procédure de reconnexion SSO\n---\n\ncorps\n",
        encoding="utf-8",
    )
    registry.scan()

    sans = list_tool.run(registry, {})
    assert "propositions en attente : 1" in sans
    assert "reconnexion SSO" not in sans

    avec = list_tool.run(registry, {"include_pending_concerns": True})
    assert "prop-2026-01-01-aaaa (correction) — procédure de reconnexion SSO" in avec


def test_description_de_kb_list_enumere_les_bases(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n"})
    desc = list_tool.description(registry)
    # C'est ce qui permet le routage sans appel préalable (§ 5.1).
    assert "ma-base" in desc and "Corpus jetable" in desc


def test_kb_list_sans_base(hub, registry):
    out = list_tool.run(registry, {})
    assert "Aucune base enregistrée" in out


# --- plafond transverse ------------------------------------------------------


def test_budgeted_writer_signale_la_troncature():
    w = BudgetedWriter(char_cap=100)
    assert w.add("a" * 60)
    assert not w.add("b" * 60)
    rendu = w.render()
    assert "a" * 60 in rendu
    assert "tronqués" in rendu


def test_budgeted_writer_emet_toujours_le_premier_bloc():
    w = BudgetedWriter(char_cap=10)
    assert w.add("x" * 500)
    assert "x" * 500 in w.render()


# --- kb_governance -----------------------------------------------------------


def test_kb_governance_retourne_regles_et_schema(hub, make_bundle, registry):
    b = make_bundle("ma-base", name="ma-base", git_init=False,
                    governance={"rules": "./GOVERNANCE.md", "frontmatter-schema": "./schema.yaml"})
    b.governance("# Gouvernance\n\n## Golden rules\n\n- jamais confidence: low seule\n")
    b.schema("required:\n  - name: title\n    type: string\n")
    b.init_git()
    registry.scan()

    out = governance_tool.run(registry, {"base": "ma-base"})
    assert "Golden rules" in out
    assert "confidence: low" in out
    assert "schema.yaml" in out and "name: title" in out


def test_kb_governance_sans_schema(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n"})
    out = governance_tool.run(registry, {"base": "ma-base"})
    assert "aucun schema.yaml" in out
