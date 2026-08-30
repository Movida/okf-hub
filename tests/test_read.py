"""kb_read : sections, headings, gros documents, confinement (§ 5.3). Jalon J1."""

from __future__ import annotations

import pytest

from okf_hub.errors import NOT_FOUND, ToolError
from okf_hub.mdutil import extract_section, normalize_heading, parse_document, parse_headings
from okf_hub.tools import read_tool


# --- unités de mdutil --------------------------------------------------------


def test_frontmatter_parse_et_titre():
    doc = parse_document("---\ntitle: Mon titre\ntags: [a]\n---\n\n# Autre\n\ntexte\n")
    assert doc.frontmatter["title"] == "Mon titre"
    assert doc.title == "Mon titre"
    assert doc.body.strip().startswith("# Autre")


def test_titre_replie_sur_le_premier_heading():
    doc = parse_document("# Premier heading\n\ntexte\n")
    assert doc.frontmatter is None
    assert doc.title == "Premier heading"


def test_frontmatter_illisible_ne_casse_pas_le_document():
    # Principe § 1.4 : une base reste lisible même mal formée.
    doc = parse_document("---\n: : :\n  - [\n---\n\n# Titre\n")
    assert doc.frontmatter is None
    assert doc.title == "Titre"


def test_frontmatter_ignore_si_pas_en_premiere_ligne():
    doc = parse_document("\n---\ntitle: X\n---\n")
    assert doc.frontmatter is None


@pytest.mark.parametrize(
    "brut, attendu",
    [
        ("`kb_read` et **gras**", "kb_read et gras"),
        ("*Procédure* de _reconnexion_", "procédure de reconnexion"),
        ("Voir [la doc](https://exemple.test/a(b))", "voir la doc"),
        ("![schéma](img.png) architecture", "schéma architecture"),
        ("~~obsolète~~ Actuel", "obsolète actuel"),
        ("  Espaces    multiples  ", "espaces multiples"),
    ],
)
def test_normalisation_des_headings(brut, attendu):
    assert normalize_heading(brut) == attendu


def test_headings_dans_un_bloc_de_code_ignores():
    text = "# Vrai\n\n```md\n# Faux\n```\n\n## Autre vrai\n"
    assert [h.text for h in parse_headings(text)] == ["Vrai", "Autre vrai"]


def test_section_extraite_jusqu_au_heading_de_niveau_inferieur_ou_egal():
    text = "# A\n\naaa\n\n## B\n\nbbb\n\n### C\n\nccc\n\n## D\n\nddd\n"
    match = extract_section(text, "B")
    assert match is not None
    assert "bbb" in match.content and "ccc" in match.content
    assert "ddd" not in match.content


def test_section_correspond_malgre_le_formatage_inline():
    text = "# Doc\n\n## Procédure de `reconnexion` **SSO**\n\ncorps\n"
    match = extract_section(text, "procédure de reconnexion sso")
    assert match is not None
    assert "corps" in match.content


# --- outil kb_read -----------------------------------------------------------


def _base_with(make_bundle, registry, docs: dict[str, str], **manifest):
    b = make_bundle("ma-base", name="ma-base", git_init=False, **manifest)
    for rel, text in docs.items():
        b.doc(rel, text)
    b.init_git()
    registry.scan()
    return b


def test_lecture_document_complet(hub, make_bundle, registry):
    _base_with(make_bundle, registry, {"guide.md": "---\ntitle: Guide\n---\n\n# Guide\n\ncorps\n"})
    out = read_tool.run(registry, {"base": "ma-base", "path": "guide.md"})
    assert "corps" in out
    assert "title: Guide" in out  # frontmatter inclus (§ 5.3)


def test_lecture_de_section(hub, make_bundle, registry):
    _base_with(
        make_bundle, registry,
        {"guide.md": "# Guide\n\nintro\n\n## Installation\n\netapes\n\n## Exploitation\n\nautre\n"},
    )
    out = read_tool.run(registry, {"base": "ma-base", "path": "guide.md", "section": "installation"})
    assert "etapes" in out
    assert "autre" not in out


def test_section_introuvable_liste_les_headings(hub, make_bundle, registry):
    _base_with(make_bundle, registry, {"guide.md": "# Guide\n\n## Installation\n\nx\n"})
    with pytest.raises(ToolError) as exc:
        read_tool.run(registry, {"base": "ma-base", "path": "guide.md", "section": "absent"})
    assert exc.value.code == NOT_FOUND
    assert "Installation" in exc.value.message


def test_headings_dupliques_premiere_occurrence_et_mention(hub, make_bundle, registry):
    _base_with(
        make_bundle, registry,
        {"guide.md": "# A\n\n## Notes\n\npremiere\n\n# B\n\n## Notes\n\nseconde\n"},
    )
    out = read_tool.run(registry, {"base": "ma-base", "path": "guide.md", "section": "Notes"})
    assert "premiere" in out
    assert "seconde" not in out
    assert "1 autre(s) section(s) portent ce titre" in out


def test_gros_document_sans_section_retourne_la_table_des_headings(hub, make_bundle, registry):
    corps = "\n".join(f"## Section {i}\n\n" + ("ligne de remplissage. " * 40) for i in range(20))
    _base_with(make_bundle, registry, {"gros.md": "---\ntitle: Gros\n---\n\n# Gros\n\n" + corps})
    out = read_tool.run(registry, {"base": "ma-base", "path": "gros.md"})
    assert "table des headings" in out
    assert "Section 3" in out
    assert "ligne de remplissage" not in out
    assert "title: Gros" in out  # le frontmatter reste retourné


def test_force_true_contourne_la_table_des_headings(hub, make_bundle, registry):
    corps = "\n".join(f"## Section {i}\n\n" + ("ligne de remplissage. " * 40) for i in range(20))
    _base_with(make_bundle, registry, {"gros.md": "# Gros\n\n" + corps})
    out = read_tool.run(registry, {"base": "ma-base", "path": "gros.md", "force": True})
    assert "ligne de remplissage" in out


def test_section_fonctionne_sur_un_gros_document(hub, make_bundle, registry):
    corps = "\n".join(f"## Section {i}\n\nunique-{i} " + ("bourrage " * 60) for i in range(20))
    _base_with(make_bundle, registry, {"gros.md": "# Gros\n\n" + corps})
    out = read_tool.run(registry, {"base": "ma-base", "path": "gros.md", "section": "Section 7"})
    assert "unique-7" in out
    assert "unique-8" not in out


# --- confinement des chemins (§ 5.3, § 8) ------------------------------------


@pytest.mark.parametrize(
    "chemin",
    [
        "../okf-bundle.yaml",
        "../../../../etc/passwd",
        "/etc/passwd",
        "sous/../../GOVERNANCE.md",
        "./../GOVERNANCE.md",
    ],
)
def test_traversee_de_chemin_rejetee(hub, make_bundle, registry, chemin):
    _base_with(make_bundle, registry, {"sous/note.md": "# note\n"})
    with pytest.raises(ToolError) as exc:
        read_tool.run(registry, {"base": "ma-base", "path": chemin})
    assert exc.value.code == NOT_FOUND


def test_symlink_sortant_du_corpus_rejete(hub, make_bundle, registry):
    b = _base_with(make_bundle, registry, {"note.md": "# note\n"})
    cible = b.root / "GOVERNANCE.md"
    lien = b.root / "knowledge" / "fuite.md"
    lien.symlink_to(cible)
    registry.scan()
    with pytest.raises(ToolError) as exc:
        read_tool.run(registry, {"base": "ma-base", "path": "fuite.md"})
    # La résolution canonique fait sortir la cible du corpus (§ 5.3).
    assert exc.value.code == NOT_FOUND


def test_base_inconnue(hub, make_bundle, registry):
    _base_with(make_bundle, registry, {"note.md": "# note\n"})
    with pytest.raises(ToolError) as exc:
        read_tool.run(registry, {"base": "inexistante", "path": "note.md"})
    assert exc.value.code == "UNKNOWN_BASE"
    # Le message inclut la liste des bases valides (§ 5).
    assert "ma-base" in exc.value.message
