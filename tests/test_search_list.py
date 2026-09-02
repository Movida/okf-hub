"""kb_search, kb_list, kb_governance : classement, exclusions, plafonds (§ 5.1, 5.2, 5.4). Jalon J1."""

from __future__ import annotations

import re

import pytest

from okf_hub.errors import INVALID_INPUT, UNKNOWN_BASE, ToolError
from okf_hub.governance import DRAFT_BANNER
from okf_hub.textutil import CHAR_CAP, BudgetedWriter
from okf_hub.tools import governance_tool, list_tool, read_tool, search_tool


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


def test_message_ripgrep_absent_cite_des_fichiers_qui_existent(monkeypatch):
    """Le remède annoncé par IO_ERROR doit être vérifiable — il l'a déjà été faux.

    Le message renvoyait à `.devcontainer/devcontainer.json`, où « ripgrep »
    n'apparaît pas : l'opérateur y cherchait une installation qui vit dans
    `post-create.sh`. Un fichier cité comme l'endroit où ripgrep s'installe doit
    donc exister *et* en parler, sinon le message envoie chercher au mauvais
    endroit avec l'assurance d'une source. Même régime que les corpus meta
    (`test_bases_meta.py`) : ce qui est écrit ailleurs que dans la description
    d'un outil est gardé par un test.
    """
    from pathlib import Path

    from okf_hub import search
    from okf_hub.errors import IO_ERROR

    monkeypatch.setattr(search.shutil, "which", lambda _: None)
    with pytest.raises(ToolError) as exc:
        search._rg_binary()
    assert exc.value.code == IO_ERROR

    racine = Path(__file__).resolve().parent.parent
    cites = re.findall(r"[\w./-]+\.(?:json|sh|md|py|toml)", exc.value.message)
    assert cites, "le message doit dire où ripgrep s'installe"
    for rel in cites:
        chemin = racine / rel
        assert chemin.is_file(), f"le message cite {rel}, qui n'existe pas"
        assert "ripgrep" in chemin.read_text(encoding="utf-8"), (
            f"le message cite {rel} comme remède, mais ce fichier ne parle pas "
            "de ripgrep"
        )


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


# --- kb_search multi-bases (§ 10.3) -------------------------------------------


def build_multi(make_bundle, registry, bases: dict[str, dict[str, str]]):
    for name, docs in bases.items():
        b = make_bundle(name, name=name, git_init=False)
        for rel, text in docs.items():
            b.doc(rel, text)
        b.init_git()
    registry.scan()


def test_base_chaine_unique_reste_sans_entete_de_groupe(hub, make_bundle, registry):
    """Non-régression : un `base` chaîne simple garde exactement le format
    d'aujourd'hui, sans entête de groupe ajouté."""
    build(make_bundle, registry, {"a.md": "# A\n\ncible-solo\n"})
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible-solo"})
    assert "## Base :" not in out
    assert "a.md" in out


def test_base_liste_groupe_les_resultats_par_base(hub, make_bundle, registry):
    build_multi(
        make_bundle, registry,
        {
            "base-a": {"a.md": "# A\n\ncible-multi ici\n"},
            "base-b": {"b.md": "# B\n\ncible-multi aussi\n"},
        },
    )
    out = search_tool.run(registry, {"base": ["base-a", "base-b"], "query": "cible-multi"})
    assert "## Base : base-a" in out and "## Base : base-b" in out
    assert out.index("## Base : base-a") < out.index("## Base : base-b")
    assert "a.md" in out and "b.md" in out
    assert "2 résultat(s) dans 2 base(s) pour : cible-multi" in out


def test_base_etoile_interroge_toutes_les_bases_enregistrees(hub, make_bundle, registry):
    build_multi(
        make_bundle, registry,
        {
            "base-a": {"a.md": "# A\n\ncible-etoile\n"},
            "base-b": {"b.md": "# B\n\ncible-etoile\n"},
        },
    )
    out = search_tool.run(registry, {"base": "*", "query": "cible-etoile"})
    assert "## Base : base-a" in out and "## Base : base-b" in out


def test_base_etoile_sans_base_enregistree(hub, registry):
    out = search_tool.run(registry, {"base": "*", "query": "cible"})
    assert "Aucune base" in out


def test_base_liste_deduplique_les_noms(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\ncible-dup\n"})
    out = search_tool.run(registry, {"base": ["ma-base", "ma-base"], "query": "cible-dup"})
    # Un seul nom effectif après déduplication : pas d'entête de groupe.
    assert "## Base :" not in out
    assert "a.md" in out


def test_base_liste_nom_inconnu_leve_unknown_base(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\ntexte\n"})
    with pytest.raises(ToolError) as exc:
        search_tool.run(registry, {"base": ["ma-base", "absente"], "query": "texte"})
    assert exc.value.code == UNKNOWN_BASE


def test_base_liste_vide_leve_invalid_input(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "# A\n\ntexte\n"})
    with pytest.raises(ToolError) as exc:
        search_tool.run(registry, {"base": [], "query": "texte"})
    assert exc.value.code == INVALID_INPUT


def test_base_liste_aucun_resultat_dans_aucune_base(hub, make_bundle, registry):
    build_multi(
        make_bundle, registry,
        {"base-a": {"a.md": "# A\n\ntexte\n"}, "base-b": {"b.md": "# B\n\ntexte\n"}},
    )
    out = search_tool.run(registry, {"base": ["base-a", "base-b"], "query": "introuvable-xyz"})
    assert "Aucun résultat" in out


def test_base_liste_plafond_global_reparti_a_egalite(hub, make_bundle, registry):
    docs_a = {f"a{i}.md": f"# A{i}\n\nrepartition-cap\n" for i in range(5)}
    docs_b = {f"b{i}.md": f"# B{i}\n\nrepartition-cap\n" for i in range(5)}
    build_multi(make_bundle, registry, {"base-a": docs_a, "base-b": docs_b})
    out = search_tool.run(
        registry, {"base": ["base-a", "base-b"], "query": "repartition-cap", "max_results": 4}
    )
    # Plafond global unique : 4 résultats au total, pas 4 par base (§ 10.3).
    assert out.count("### ") == 4
    section_a = out.split("## Base : base-b")[0]
    assert section_a.count("### ") == 2


def test_base_liste_reliquat_redistribue_a_l_autre_base(hub, make_bundle, registry):
    docs_a = {"a0.md": "# A0\n\nreliquat-cap\n"}
    docs_b = {f"b{i}.md": f"# B{i}\n\nreliquat-cap\n" for i in range(5)}
    build_multi(make_bundle, registry, {"base-a": docs_a, "base-b": docs_b})
    out = search_tool.run(
        registry, {"base": ["base-a", "base-b"], "query": "reliquat-cap", "max_results": 4}
    )
    assert out.count("### ") == 4
    section_a = out.split("## Base : base-b")[0]
    # base-a n'a qu'un document : le reliquat profite à base-b plutôt que d'être perdu.
    assert section_a.count("### ") == 1


def test_base_liste_repli_or_signale_par_base(hub, make_bundle, registry):
    """Le repli OU est propre à chaque base : base-a trouve les deux termes
    dans un même document (ET strict, pas de repli), base-b non (repli OU) —
    la mention ne doit apparaître que dans le groupe de base-b."""
    build_multi(
        make_bundle, registry,
        {
            "base-a": {"a.md": "# A\n\ndelta-flag epsilon-flag ensemble\n"},
            "base-b": {
                "b.md": "# B\n\ndelta-flag seulement\n",
                "c.md": "# C\n\nepsilon-flag seulement\n",
            },
        },
    )
    out = search_tool.run(
        registry, {"base": ["base-a", "base-b"], "query": "delta-flag epsilon-flag"}
    )
    section_a, section_b = out.split("## Base : base-b", 1)
    assert "résultats partiels" not in section_a
    assert "résultats partiels" in section_b


def test_allocate_quota_repartition_egale():
    assert search_tool._allocate_quota([10, 10], 8) == [4, 4]


def test_allocate_quota_reliquat_vers_l_autre_base():
    assert search_tool._allocate_quota([1, 10], 8) == [1, 7]


def test_allocate_quota_ne_depasse_jamais_la_disponibilite():
    assert search_tool._allocate_quota([0, 5], 8) == [0, 5]


def test_allocate_quota_total_nul():
    assert search_tool._allocate_quota([5, 5], 0) == [0, 0]


def test_allocate_quota_trois_bases_inegales():
    assert search_tool._allocate_quota([1, 1, 10], 6) == [1, 1, 4]


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


# --- déclassement des noms réservés OKF (§ 3.1 OKF, écart documenté § 5.2) ---


def test_index_et_log_declasses_en_recherche(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {
            "index.md": "# Sommaire\n\n* [Connexion SSO](sso.md) - la procédure de connexion SSO\n",
            "log.md": "# Journal\n\n## 2026-01-01\n* Ajout de la connexion SSO\n",
            "sso.md": "# SSO\n\nProcédure de connexion.\n",
        },
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "connexion"})
    # Un sommaire dense en texte de liens ne doit pas passer devant le document.
    # On compare les en-têtes de résultat, pas le texte brut : la note de
    # déclassement mentionne elle-même « index.md ».
    ordre = [l.split("### ")[1].split(" —")[0] for l in out.splitlines() if l.startswith("### ")]
    assert ordre[0] == "sso.md", ordre
    assert set(ordre[1:]) == {"index.md", "log.md"}, ordre
    assert "déclassés" in out


def test_sommaire_reste_trouvable_faute_de_mieux(hub, make_bundle, registry):
    build(make_bundle, registry, {"index.md": "# Sommaire\n\nrubrique-unique\n"})
    out = search_tool.run(registry, {"base": "ma-base", "query": "rubrique-unique"})
    assert "index.md" in out


def test_sommaire_reste_lisible_et_compte(hub, make_bundle, registry):
    build(make_bundle, registry, {"index.md": "# Sommaire\n\nrubrique\n"})
    # Le déclassement ne concerne que le classement de kb_search : un sommaire
    # reste un document au sens de la § 2.
    assert "rubrique" in read_tool.run(registry, {"base": "ma-base", "path": "index.md"})
    assert "documents : 2" in list_tool.run(registry, {})


# --- heading de section dans les résultats (amendement rév. 4.1, § B3) --------


def _section_de(sortie: str) -> str:
    """Le libellé qui suit « § » sur la première ligne d'extrait."""
    ligne = next(l for l in sortie.splitlines() if l.startswith("  L"))
    return ligne.split("§", 1)[1].strip()


def test_extrait_annote_du_heading_de_sa_section(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {"a.md": "# Titre\n\n## Reconnexion SSO\n\nle mot cible est ici\n"},
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    assert _section_de(out) == "reconnexion sso"


def test_extrait_avant_tout_heading_annote_preambule(hub, make_bundle, registry):
    build(make_bundle, registry, {"a.md": "le mot cible est ici\n\n# Titre\n\nsuite\n"})
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    assert _section_de(out) == search_tool.PREAMBLE_LABEL


def test_le_heading_suit_la_ligne_touchee_pas_le_debut_de_fenetre(hub, make_bundle, registry):
    """La fenêtre de contexte déborde de deux lignes : elle peut mordre sur la
    section précédente, pas le libellé."""
    build(
        make_bundle, registry,
        {"a.md": "# A\n\ntexte\n\n## Section deux\n\ncible\n"},
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    assert _section_de(out) == "section deux"


def test_le_libelle_se_rejoue_tel_quel_dans_kb_read(hub, make_bundle, registry):
    """Chaînage direct kb_search → kb_read(path, section), sans table des
    headings intermédiaire — y compris sur un heading formaté (§ 11.4)."""
    build(
        make_bundle, registry,
        {"a.md": "# A\n\n## Piège `relativePath` **confirmé**\n\nle mot cible\n"},
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    section = _section_de(out)

    lu = read_tool.run(registry, {"base": "ma-base", "path": "a.md", "section": section})
    assert "le mot cible" in lu


def test_chainage_en_un_appel_sur_un_gros_document(hub, make_bundle, registry):
    """Le cas signalé : sur un document au-delà de read-toc-threshold, kb_read
    sans section ne rend que la table des headings."""
    remplissage = "\n".join(f"ligne de remplissage {i}" for i in range(1500))
    build(
        make_bundle, registry,
        {"gros.md": f"# Gros\n\n## Bruit\n\n{remplissage}\n\n## Cible utile\n\nle mot cible\n"},
    )
    assert (registry.get("ma-base").corpus_dir / "gros.md").stat().st_size > (
        registry.config.read_toc_threshold
    )

    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    section = _section_de(out)

    lu = read_tool.run(registry, {"base": "ma-base", "path": "gros.md", "section": section})
    assert "le mot cible" in lu
    assert "table des headings" not in lu


def test_le_heading_dans_un_bloc_de_code_n_est_pas_une_section(hub, make_bundle, registry):
    build(
        make_bundle, registry,
        {"a.md": "# A\n\n## Vraie section\n\n```\n# Faux heading\n```\n\ncible\n"},
    )
    out = search_tool.run(registry, {"base": "ma-base", "query": "cible"})
    assert _section_de(out) == "vraie section"


# --- convention `status` du GOVERNANCE.md (amendement rév. 4.1, § B5) --------


def gouvernance(make_bundle, registry, texte: str):
    b = make_bundle("ma-base", name="ma-base", git_init=False)
    b.governance(texte)
    b.init_git()
    registry.scan()
    return b


def test_gouvernance_brouillon_prefixee_d_un_bandeau(hub, make_bundle, registry):
    gouvernance(make_bundle, registry, "---\nstatus: draft\n---\n\n# G\n\nRègles.\n")
    out = governance_tool.run(registry, {"base": "ma-base"})
    assert out.startswith(DRAFT_BANNER)
    assert "les propositions restent acceptées" in out


def test_gouvernance_stable_sans_bandeau(hub, make_bundle, registry):
    gouvernance(make_bundle, registry, "---\nstatus: stable\n---\n\n# G\n\nRègles.\n")
    assert DRAFT_BANNER not in governance_tool.run(registry, {"base": "ma-base"})


def test_absence_de_frontmatter_vaut_stable(hub, make_bundle, registry):
    """Une base antérieure à la convention ne devient pas un brouillon."""
    gouvernance(make_bundle, registry, "# G\n\nRègles.\n")
    assert DRAFT_BANNER not in governance_tool.run(registry, {"base": "ma-base"})


def test_status_inconnu_traite_comme_stable(hub, make_bundle, registry):
    gouvernance(make_bundle, registry, "---\nstatus: brouilon\n---\n\n# G\n")
    assert DRAFT_BANNER not in governance_tool.run(registry, {"base": "ma-base"})


def test_status_insensible_a_la_casse(hub, make_bundle, registry):
    gouvernance(make_bundle, registry, "---\nstatus: Draft\n---\n\n# G\n")
    assert DRAFT_BANNER in governance_tool.run(registry, {"base": "ma-base"})


def test_les_regles_restent_lisibles_en_brouillon(hub, make_bundle, registry):
    gouvernance(make_bundle, registry, "---\nstatus: draft\n---\n\n# G\n\nRègle unique.\n")
    assert "Règle unique." in governance_tool.run(registry, {"base": "ma-base"})
