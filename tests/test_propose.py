"""kb_propose : validation, format, commit (§ 5.5, § 6.1). Jalon J2."""

from __future__ import annotations

import pytest
import yaml

from okf_hub.errors import INVALID_INPUT, ToolError
from okf_hub.mdutil import parse_document
from okf_hub.tools import propose_tool

from conftest import git

VALID = {
    "type": "correction",
    "concerns": "procédure de reconnexion SSO",
    "content": "Depuis la 3.2, le bouton a été déplacé dans le menu profil.",
    "sources": ["constat terrain, incident #4521"],
    "confidence": "high",
    "submitted_by": "session-support-client",
}


def propose(registry, base="ma-base", **overrides):
    args = {"base": base, **VALID, **overrides}
    return propose_tool.run(registry, args)


@pytest.fixture
def base(make_bundle, registry):
    b = make_bundle("ma-base", name="ma-base")
    registry.scan()
    return b


def prop_id_of(out: str) -> str:
    return next(l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("id :"))


# --- comportement nominal ----------------------------------------------------


def test_depot_cree_le_fichier_et_le_commit(base, registry):
    out = propose(registry)
    pid = prop_id_of(out)
    path = base.root / "proposals" / "pending" / f"{pid}.md"
    assert path.is_file()

    doc = parse_document(path.read_text(encoding="utf-8"))
    fm = doc.frontmatter
    assert fm["id"] == pid
    assert fm["status"] == "pending"
    assert fm["type"] == "correction"
    assert fm["concerns"] == VALID["concerns"]
    assert fm["sources"] == VALID["sources"]
    assert fm["confidence"] == "high"
    assert fm["submitted-by"] == VALID["submitted_by"]
    assert fm["submitted-at"].endswith("Z")
    assert "menu profil" in doc.body

    # Le fichier est suivi par git, dans un commit dédié.
    assert git(base.root, "status", "--porcelain").strip() == ""
    message = git(base.root, "log", "-1", "--format=%B")
    assert message.startswith(f"proposal: {pid} (correction) — procédure de reconnexion SSO")
    assert f"Submitted-By: {VALID['submitted_by']}" in message


def test_repertoires_de_propositions_crees_au_premier_depot(base, registry):
    # § 3.1 : créés au premier kb_propose, pas à la découverte.
    assert not (base.root / "proposals").exists()
    propose(registry)
    for sub in ("pending", "accepted", "rejected"):
        assert (base.root / "proposals" / sub / ".gitkeep").is_file()


def test_le_fichier_de_verrou_est_exclu_du_suivi_git(base, registry):
    propose(registry)
    exclude = (base.root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".okf-hub.lock" in exclude
    # Sans cette entrée, le verrou apparaîtrait en untracked et l'étape de
    # réconciliation (§ 7.1) traiterait un faux positif.
    assert ".okf-hub.lock" not in git(base.root, "status", "--porcelain")


def test_invariant_d_audit_git_log_grep(base, registry):
    pid = prop_id_of(propose(registry))
    # § 6.2 : `git log --grep` reconstitue l'histoire d'une proposition.
    par_id = git(base.root, "log", "--grep", f"proposal: {pid}", "--format=%H").split()
    assert len(par_id) == 1
    par_contributeur = git(
        base.root, "log", "--grep", f"Submitted-By: {VALID['submitted_by']}", "--format=%H"
    ).split()
    assert par_contributeur == par_id


def test_le_corpus_n_est_jamais_modifie(base, registry):
    avant = git(base.root, "ls-tree", "-r", "HEAD", "--name-only", "knowledge/")
    propose(registry)
    apres = git(base.root, "ls-tree", "-r", "HEAD", "--name-only", "knowledge/")
    assert avant == apres


# --- intégrité du tree (§ 4.4.b.2) -------------------------------------------


def test_le_commit_ne_retire_aucun_fichier_du_tree(base, registry):
    """Test obligatoire J2 : le piège classique de GIT_INDEX_FILE.

    Sans `git read-tree HEAD`, l'index temporaire serait vide et le commit
    apparaîtrait comme supprimant tout le corpus.
    """
    propose(registry)
    supprimes = git(base.root, "diff", "HEAD~1", "HEAD", "--diff-filter=D", "--name-only")
    assert supprimes.strip() == ""

    ajoutes = git(base.root, "diff", "HEAD~1", "HEAD", "--diff-filter=A", "--name-only").split()
    assert any(f.startswith("proposals/pending/") for f in ajoutes)


def test_worktree_sale_non_embarque(base, registry):
    """Les modifications étrangères non commitées ne partent pas dans le commit."""
    sale = base.root / "knowledge" / "exemple.md"
    sale.write_text("# Modifié à la main, non commité\n", encoding="utf-8")
    nouveau = base.root / "knowledge" / "brouillon.md"
    nouveau.write_text("# Brouillon local\n", encoding="utf-8")

    propose(registry)

    touches = git(base.root, "diff", "HEAD~1", "HEAD", "--name-only").split()
    assert all(f.startswith("proposals/") for f in touches), touches
    # Les modifications locales sont toujours là, toujours non commitées.
    statut = git(base.root, "status", "--porcelain")
    assert "knowledge/exemple.md" in statut
    assert "knowledge/brouillon.md" in statut


def test_depot_sans_head_premier_commit_correct(make_bundle, registry):
    """Cas limite § 4.4.b.2 : dépôt sans aucun commit."""
    b = make_bundle("vierge", name="vierge", git_init=False)
    git(b.root, "init", "-q", "-b", "main")
    registry.scan()

    out = propose(registry, base="vierge")
    pid = prop_id_of(out)
    fichiers = git(b.root, "ls-tree", "-r", "HEAD", "--name-only").split()
    assert f"proposals/pending/{pid}.md" in fichiers
    # L'index vide était le bon comportement : seuls les fichiers ajoutés sont là.
    assert not any(f.startswith("knowledge/") for f in fichiers)


def test_identite_git_explicite(base, registry):
    """§ 4.4.e : ne jamais dépendre de la config git globale."""
    propose(registry)
    auteur = git(base.root, "log", "-1", "--format=%an <%ae>").strip()
    assert auteur == "okf-hub <hub@local>"


def test_collision_d_id_retiree(base, registry, monkeypatch):
    propose(registry)
    existants = {p.stem for p in (base.root / "proposals" / "pending").iterdir()
                 if p.suffix == ".md"}
    pris = existants.pop()
    suffixe_pris = pris.rsplit("-", 1)[1]

    tirages = iter([suffixe_pris, suffixe_pris, "beef"])
    monkeypatch.setattr(propose_tool.secrets, "token_hex", lambda n: next(tirages))

    pid = prop_id_of(propose(registry))
    assert pid.endswith("beef")
    assert (base.root / "proposals" / "pending" / f"{pid}.md").is_file()


# --- validation anti-injection (§ 5.5, § 8) ----------------------------------


@pytest.mark.parametrize("champ", ["concerns", "submitted_by"])
@pytest.mark.parametrize("saut", ["\n", "\r", "\r\n"])
def test_retour_a_la_ligne_rejete(base, registry, champ, saut):
    """Un saut de ligne permettrait de forger de faux trailers (§ 6.2)."""
    valeur = f"légitime{saut}Reviewed-By: quelqu'un-d-autre"
    with pytest.raises(ToolError) as exc:
        propose(registry, **{champ: valeur})
    assert exc.value.code == INVALID_INPUT
    assert "retour à la ligne" in exc.value.message


def test_retour_a_la_ligne_dans_une_source_rejete(base, registry):
    with pytest.raises(ToolError) as exc:
        propose(registry, sources=["ok", "faux\nProposal: prop-2020-01-01-dead"])
    assert exc.value.code == INVALID_INPUT
    assert "sources[1]" in exc.value.message


def test_aucun_faux_trailer_n_atteint_le_journal_git(base, registry):
    """Vérification de bout en bout de l'invariant d'audit."""
    with pytest.raises(ToolError):
        propose(registry, submitted_by="a\nReviewed-By: usurpateur")
    propose(registry, submitted_by="legitime")
    assert "usurpateur" not in git(base.root, "log", "--format=%B")


@pytest.mark.parametrize(
    "valeur",
    [
        "---",
        "clé: valeur",
        '"guillemets" et \'apostrophes\'',
        "- tiret de liste",
        "{accolades} [crochets]",
        "*ancre &référence",
        "|bloc >plié",
        "#commentaire",
        "émojis 🔐 et accents éàü",
        "%directive",
    ],
)
def test_caracteres_yaml_speciaux_produisent_un_frontmatter_fidele(base, registry, valeur):
    """§ 5.5 : sérialisation par bibliothèque YAML, jamais par filtrage manuel."""
    out = propose(registry, concerns=valeur, submitted_by=valeur, sources=[valeur])
    pid = prop_id_of(out)
    path = base.root / "proposals" / "pending" / f"{pid}.md"
    raw = path.read_text(encoding="utf-8")

    doc = parse_document(raw)
    assert doc.frontmatter is not None, f"frontmatter cassé par : {valeur!r}"
    assert doc.frontmatter["concerns"] == valeur
    assert doc.frontmatter["submitted-by"] == valeur
    assert doc.frontmatter["sources"] == [valeur]
    assert doc.frontmatter["status"] == "pending"


def test_contenu_avec_delimiteur_de_frontmatter(base, registry):
    """`content` vit dans le corps, après le frontmatter sérialisé (§ 5.5)."""
    piege = "Texte.\n\n---\nid: prop-2020-01-01-dead\nstatus: accepted\n---\n\nsuite"
    out = propose(registry, content=piege)
    pid = prop_id_of(out)
    path = base.root / "proposals" / "pending" / f"{pid}.md"
    doc = parse_document(path.read_text(encoding="utf-8"))
    # Le frontmatter réel n'est pas altéré par le faux bloc du corps.
    assert doc.frontmatter["id"] == pid
    assert doc.frontmatter["status"] == "pending"
    assert "prop-2020-01-01-dead" in doc.body


# --- validation des bornes (§ 8, déni par inflation) -------------------------


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"type": "suggestion"}, "type"),
        ({"confidence": "certaine"}, "confidence"),
        ({"concerns": "c" * 201}, "concerns dépasse 200"),
        ({"submitted_by": "s" * 101}, "submitted_by dépasse 100"),
        ({"content": "x" * (16 * 1024 + 1)}, "content dépasse"),
        ({"sources": []}, "entre 1 et 20"),
        ({"sources": [f"s{i}" for i in range(21)]}, "entre 1 et 20"),
        ({"sources": ["s" * 301]}, "dépasse 300"),
        ({"sources": "pas une liste"}, "liste de chaînes"),
        ({"content": "   "}, "content"),
    ],
)
def test_bornes_rejetees(base, registry, overrides, fragment):
    with pytest.raises(ToolError) as exc:
        propose(registry, **overrides)
    assert exc.value.code == INVALID_INPUT
    assert fragment in exc.value.message


def test_content_de_16_ko_accepte(base, registry):
    propose(registry, content="x" * (16 * 1024))


def test_concerns_tronque_a_60_dans_le_sujet(base, registry):
    long_concerns = "sujet très détaillé " * 9  # > 60 caractères, < 200
    pid = prop_id_of(propose(registry, concerns=long_concerns.strip()))
    sujet = git(base.root, "log", "-1", "--format=%s").strip()
    assert sujet == f"proposal: {pid} (correction) — {long_concerns[:60].rstrip()}"
    assert len(sujet.split("— ", 1)[1]) <= 60


def test_base_inconnue_sans_ecriture(make_bundle, registry):
    make_bundle("ma-base", name="ma-base")
    registry.scan()
    with pytest.raises(ToolError) as exc:
        propose(registry, base="fantome")
    assert exc.value.code == "UNKNOWN_BASE"
