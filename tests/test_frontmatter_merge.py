"""Fusion de frontmatter : fidélité de représentation et refus explicites (§ 1.7).

Ces tests portent la garantie annoncée par `okf_hub.frontmatter` : un champ non
ciblé est identique **au caractère près** après une fusion, sinon la fusion est
refusée. Ils couvrent aussi le contrat d'entrée (mapping, pas d'ancre, pas de clé
dupliquée) et la politique de résolution des scalaires ambigus entre YAML 1.1
(PyYAML, le parseur du hub) et YAML 1.2 (ruamel, l'émetteur).

Le premier test reproduit l'incident du 2026-09-16 sur `el2d-blueway` : une
fusion d'un seul champ avait reformaté `tags`/`aliases` en style bloc et réécrit
un timestamp ISO-8601, ce qui égarait le parseur maison du corpus sans produire
aucune erreur.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import git
from test_review import BASE_DOC, depose
from okf_hub import review
from okf_hub.errors import INVALID_INPUT, ToolError
from okf_hub.frontmatter import merge_frontmatter
from okf_hub.mdutil import parse_document

FICHE = """---
id: fiche-environnement
title: "Environnement Blueway"
tags: [module:environnement, domaine:blueway, env:dev]
aliases: [environnement, env]
timestamp: 2026-08-27T12:00:00Z
# les facettes fermées sont décrites dans GOVERNANCE.md
statut: valide
---

# Environnement Blueway

Le serveur de dev répond sur le port 8080.
"""


def lignes(texte: str) -> list[str]:
    return texte.split("\n")


# --- l'incident du 2026-09-16 -------------------------------------------------


def test_les_champs_non_cibles_restent_identiques_au_caractere_pres():
    obtenu = merge_frontmatter(FICHE, {"last-verified": "2026-09-16"})

    for ligne in (
        'title: "Environnement Blueway"',
        "tags: [module:environnement, domaine:blueway, env:dev]",
        "aliases: [environnement, env]",
        "timestamp: 2026-08-27T12:00:00Z",
        "# les facettes fermées sont décrites dans GOVERNANCE.md",
        "statut: valide",
    ):
        assert ligne in lignes(obtenu), ligne

    # Le champ demandé est écrit, et relu comme la chaîne demandée.
    relu = yaml.safe_load(obtenu.split("---\n")[1])
    assert relu["last-verified"] == "2026-09-16"
    assert isinstance(relu["last-verified"], str)
    assert relu["tags"] == ["module:environnement", "domaine:blueway", "env:dev"]

    # Le corps est strictement inchangé.
    assert obtenu.split("---\n", 2)[2] == FICHE.split("---\n", 2)[2]


def test_le_parseur_mono_ligne_du_corpus_lit_encore_les_tags():
    """La régression n'était pas un YAML invalide : c'était un lecteur égaré.

    Les trois bases métier embarquent le même parseur maison, volontairement
    non général (« front-matter here is always flat scalars plus one flow-list »)
    qui lit `tags` par une regex sur une seule ligne. En style bloc, cette regex
    ne capture pas une liste vide mais une liste à un seul élément corrompu.
    """
    import re

    obtenu = merge_frontmatter(FICHE, {"last-verified": "2026-09-16"})
    bloc = obtenu.split("---\n")[1]
    capture = re.search(r"^tags:\s*(.+)$", bloc, re.MULTILINE).group(1)
    assert capture == "[module:environnement, domaine:blueway, env:dev]"


def test_une_liste_flow_ciblee_reste_en_flow():
    obtenu = merge_frontmatter(FICHE, {"tags": ["module:securite", "env:prod"]})
    assert "tags: [module:securite, env:prod]" in lignes(obtenu)
    assert "aliases: [environnement, env]" in lignes(obtenu)


def lire_tags_comme_le_corpus(bloc: str) -> list[str]:
    """Le lecteur de `tags` des trois bases métier, reproduit à l'identique.

    `bundle_lib.parse_frontmatter` découpe sur la virgule **avant** de déciter,
    et `unquote` ne retire des guillemets que s'ils encadrent le scalaire
    entier — un item cité serait donc lu avec ses guillemets.
    """
    import re

    trouve = re.search(r"^tags:\s*(.+)$", bloc, re.MULTILINE)
    brut = trouve.group(1).strip() if trouve else ""
    bruts = [t.strip() for t in brut.strip("[]").split(",") if t.strip()]
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] in "\"'" else t for t in bruts]


def test_le_parseur_du_corpus_lit_les_tags_apres_une_mise_a_jour_ciblee():
    """Mettre `tags` à jour ne doit pas casser ses lecteurs non plus.

    La garantie de fidélité porte sur les champs *non* ciblés — mais une revue
    qui met `tags` à jour casserait le corpus exactement comme l'incident
    qu'elle corrige. Deux conditions : le style flow sur une seule ligne, et
    des valeurs **non citées** malgré leur `:` — PyYAML les citerait, le
    round-trip non, et le décitage du corpus intervient après le découpage sur
    la virgule.
    """
    attendus = ["module:securite", "domaine:reseau", "env:prod"]
    obtenu = merge_frontmatter(FICHE, {"tags": attendus})
    bloc = parse_document(obtenu).frontmatter_raw

    assert lire_tags_comme_le_corpus(bloc) == attendus
    assert yaml.safe_load(obtenu.split("---\n")[1])["tags"] == attendus
    ligne = next(l for l in lignes(bloc) if l.startswith("tags:"))
    assert '"' not in ligne and "'" not in ligne, ligne


@pytest.mark.parametrize(
    "texte",
    ["# Nouvelle analyse\n\nCorps.\n", "---\ntitle: Existante\n---\n\nCorps.\n"],
    ids=["frontmatter-cree", "champ-ajoute"],
)
def test_un_tags_sans_forme_existante_est_lu_par_le_parseur_du_corpus(texte):
    """Sans forme à reprendre, une liste de scalaires s'écrit en style flow.

    En style bloc, le parseur mono-ligne des corpus lisait zéro tag dans une
    fiche créée par une revue (`edits[].frontmatter` sur un document neuf).
    Une liste de mappings (`verified`) garde le style bloc.
    """
    attendus = ["type:analyse", "env:dev"]
    obtenu = merge_frontmatter(
        texte, {"tags": attendus, "verified": [{"by": "x", "at": "2026-09-23"}]}
    )
    bloc = parse_document(obtenu).frontmatter_raw
    assert lire_tags_comme_le_corpus(bloc) == attendus
    assert "tags: [type:analyse, env:dev]" in lignes(bloc)
    assert "verified:" in lignes(bloc)


def test_un_champ_existant_est_remplace_sans_toucher_ses_voisins():
    obtenu = merge_frontmatter(FICHE, {"statut": "obsolete"})
    assert "statut: obsolete" in lignes(obtenu)
    assert "timestamp: 2026-08-27T12:00:00Z" in lignes(obtenu)
    assert "# les facettes fermées sont décrites dans GOVERNANCE.md" in lignes(obtenu)


# --- représentations préexistantes --------------------------------------------


@pytest.fixture
def base(make_bundle, registry):
    b = make_bundle("ma-base", name="ma-base", git_init=False)
    b.doc("sso.md", BASE_DOC)
    b.init_git()
    registry.scan()
    return registry.get("ma-base")


@pytest.mark.parametrize(
    "bloc",
    [
        "simple: 'une valeur'\ndouble: \"une autre\"\nnu: brut\n",
        "tags:\n- a\n- b\ncle: 1\n",
        "tags:\n  - a\n  - b\ncle: 1\n",
        "desc: |\n  première ligne\n  seconde ligne\ncle: 1\n",
        "desc: >-\n  pliée sur\n  deux lignes\ncle: 1\n",
        "a: 1  # commentaire de fin de ligne\nb: 2\n",
        "# en-tête\na: 1\n\n# séparé par une ligne vide\nb: 2\n",
        "vide:\nb: 2\n",
        "imbrique:\n  x: 1\n  y: [2, 3]\nb: 2\n",
        "'clé quotée': valeur\nautre: 1\n",
        "titre: Été — déjà vu\nb: 2\n",
        "sources:\n  - id: readme\n    resource: \"README.md\"\nb: 2\n",
    ],
)
def test_representations_preexistantes_inchangees(bloc):
    texte = f"---\n{bloc}---\n\ncorps\n"
    obtenu = merge_frontmatter(texte, {"ajout": "valeur"})
    for ligne in bloc.rstrip("\n").split("\n"):
        assert ligne in lignes(obtenu), ligne
    assert "ajout: valeur" in lignes(obtenu)
    assert yaml.safe_load(obtenu.split("---\n")[1])["ajout"] == "valeur"


def test_updates_vide_rend_le_texte_original_sans_reemission():
    assert merge_frontmatter(FICHE, {}) is FICHE


def test_document_sans_frontmatter_en_recoit_un():
    obtenu = merge_frontmatter("# Titre\n\ncorps\n", {"title": "Titre", "type": "Reference"})
    assert obtenu.startswith("---\ntitle: Titre\ntype: Reference\n---\n\n# Titre")


def test_frontmatter_delimite_mais_vide():
    obtenu = merge_frontmatter("---\n---\n\ncorps\n", {"title": "T"})
    assert obtenu == "---\ntitle: T\n---\n\ncorps\n"


# --- refus explicites ---------------------------------------------------------


@pytest.mark.parametrize(
    "bloc, motif",
    [
        ("- un\n- deux\n", "n'est pas un mapping"),
        ("juste une chaîne\n", "n'est pas un mapping"),
        ("{a: 1, b: 2}\n", "ambigu"),
        ("a: 1\na: 2\n", "clés dupliquées"),
        ("socle: &s 1\nautre: *s\n", "ancre"),
        ("fiche:\n  <<: {x: 1}\n", "clé de fusion"),
        ("a: [1,\n", "illisible"),
        ("a: !weird 3\n", "illisible"),
        ("a:   1\nb: 2\n", "normalisation résiduelle"),
        ("tags:\n      - a\n      - b\n", "normalisation résiduelle"),
    ],
)
def test_refus_explicite(bloc, motif):
    texte = f"---\n{bloc}---\n\ncorps\n"
    with pytest.raises(ToolError) as exc:
        merge_frontmatter(texte, {"ajout": "valeur"})
    assert exc.value.code == INVALID_INPUT
    assert motif in exc.value.message
    # Le message renvoie vers un chemin conforme, pas vers un contournement.
    assert "content" in exc.value.message and "§ 1.7" in exc.value.message


FRONTMATTER_ILLISIBLE = (
    "---\n"
    "type: Module\n"
    "title: Environnement technique — le socle\n"
    # Un `: ` non échappé dans un scalaire nu : ce frontmatter n'est pas du YAML
    # valide. Relevé sur 91 documents des corpus réels au 2026-09-16.
    "description: Le socle sur lequel Blueway tourne chez nous : environnements, serveurs.\n"
    "tags: [module:environnement]\n"
    "---\n\n# Environnement\n"
)


def test_un_frontmatter_illisible_n_est_pas_efface():
    """Le second bug de la fonction d'origine, plus destructeur que le premier.

    `parse_document` tolère un frontmatter illisible (§ 1.4) : il rend
    `frontmatter=None` et conserve le bloc brut. L'ancienne fusion partait donc
    de `dict(doc.frontmatter or {})` — un dictionnaire **vide** — et réémettait
    un frontmatter ne contenant plus que le champ fusionné : `type`, `title`,
    `description` et `tags` disparaissaient sans un mot. 91 documents des
    corpus réels sont dans cet état (un `: ` non échappé dans un scalaire nu,
    une accolade dans une liste flow).
    """
    with pytest.raises(ToolError) as exc:
        merge_frontmatter(FRONTMATTER_ILLISIBLE, {"last-verified": "2026-09-16"})
    assert "illisible" in exc.value.message
    assert exc.value.code == INVALID_INPUT


def test_refus_sur_frontmatter_trop_volumineux():
    bloc = "".join(f"cle{i}: valeur\n" for i in range(9_000))
    with pytest.raises(ToolError) as exc:
        merge_frontmatter(f"---\n{bloc}---\n\ncorps\n", {"ajout": "v"})
    assert "volumineux" in exc.value.message


def test_refus_sur_frontmatter_trop_imbrique():
    bloc = "a:\n" + "".join(" " * (2 * i) + "- x:\n" for i in range(1, 25))
    with pytest.raises(ToolError):
        merge_frontmatter(f"---\n{bloc}---\n\ncorps\n", {"ajout": "v"})


def test_cle_de_fusion_invalide():
    with pytest.raises(ToolError) as exc:
        merge_frontmatter(FICHE, {"": "v"})
    assert exc.value.code == INVALID_INPUT


# --- injection et ambiguïtés YAML ---------------------------------------------


@pytest.mark.parametrize(
    "charge",
    [
        "valeur\n---\nsuite: injectée",
        "x\"\ninjecte: oui",
        "!!python/object/apply:os.system ['echo nope']",
        "*ancre",
        "&ancre 1",
        "[pas, une, liste",
        "a: b\nc: d",
    ],
)
def test_injection_dans_une_valeur_neutralisee(charge):
    obtenu = merge_frontmatter(FICHE, {"note": charge})
    doc = parse_document(obtenu)

    # Relu par le lecteur du hub : la valeur est une donnée, pas de la structure.
    assert doc.frontmatter["note"] == charge
    assert set(doc.frontmatter) == {
        "id", "title", "tags", "aliases", "timestamp", "statut", "note",
    }
    assert doc.body == parse_document(FICHE).body

    # Aucune ligne du frontmatter ne peut passer pour le délimiteur de clôture :
    # `parse_document` le reconnaît sur la ligne strippée (§ 3.2).
    interieur = doc.frontmatter_raw.split("\n")[1:-1]
    assert not [ligne for ligne in interieur if ligne.strip() == "---"]


@pytest.mark.parametrize(
    "valeur",
    ["yes", "no", "on", "off", "y", "n", "true", "false", "null", "~", "", "012",
     "1_000", "0x1f", "12:30", "2026-09-16", "2026-08-27T12:00:00Z", ".inf", "1.0"],
)
def test_scalaire_ambigu_relu_comme_la_chaine_demandee(valeur):
    """YAML 1.1 (PyYAML, le parseur du hub) arbitre, pas YAML 1.2 (ruamel).

    `yes` est une chaîne en 1.2 et un booléen en 1.1, `12:30` un sexagésimal en
    1.1 : une chaîne que `safe_load` ne relirait pas à l'identique est mise
    entre quotes à l'émission. La politique ne dépend pas du corpus — les
    propositions sont des données semi-fiables.
    """
    obtenu = merge_frontmatter(FICHE, {"note": valeur})
    relu = yaml.safe_load(obtenu.split("---\n")[1])
    assert relu["note"] == valeur
    assert isinstance(relu["note"], str)


@pytest.mark.parametrize("valeur", [True, False, None, 3, 3.5, ["a", "yes"], {"x": "on"}])
def test_valeurs_non_chaines_relues_avec_leur_type(valeur):
    obtenu = merge_frontmatter(FICHE, {"note": valeur})
    relu = yaml.safe_load(obtenu.split("---\n")[1])
    assert relu["note"] == valeur
    assert type(relu["note"]) is type(valeur)


def test_un_booleen_ne_passe_pas_pour_un_entier():
    obtenu = merge_frontmatter(FICHE, {"note": True})
    relu = yaml.safe_load(obtenu.split("---\n")[1])
    assert relu["note"] is True


# --- atomicité du refus dans un plan de résolution ----------------------------


def test_le_refus_ne_modifie_rien_du_depot(base, registry, tmp_path):
    """Un refus sur la seconde édition n'écrit pas la première (§ 6.2).

    C'est la raison de la séparation calcul/écriture dans `review._compose_edit` :
    un plan de résolution est tout ou rien, y compris quand la garde refuse.
    """
    pid = depose(registry)
    (base.corpus_dir / "casse.md").write_text(
        "---\na:   1\n---\n\ncorps\n", encoding="utf-8"
    )
    git(base.root, "add", "-A")
    git(base.root, "commit", "-m", "fiche au frontmatter non normalisable")
    avant_head = git(base.root, "rev-parse", "HEAD").strip()
    avant_sso = (base.corpus_dir / "sso.md").read_text(encoding="utf-8")

    plan = review.parse_plan(
        {
            "summary": "essai de fusion refusée",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": pid, "resolution": "accepted", "integrated_into": ["sso.md"]}
            ],
            "edits": [
                {"path": "sso.md", "append": "Une phrase ajoutée."},
                {"path": "casse.md", "frontmatter": {"last-verified": "2026-09-16"}},
            ],
        }
    )
    with pytest.raises(ToolError) as exc:
        review.apply_plan(base, plan)
    assert exc.value.code == INVALID_INPUT

    assert (base.corpus_dir / "sso.md").read_text(encoding="utf-8") == avant_sso
    assert "a:   1" in (base.corpus_dir / "casse.md").read_text(encoding="utf-8")
    assert git(base.root, "rev-parse", "HEAD").strip() == avant_head
    assert (base.root / "proposals" / "pending" / f"{pid}.md").is_file()
    assert not (base.root / "proposals" / "accepted" / f"{pid}.md").exists()
    assert git(base.root, "status", "--porcelain").strip() == ""


def test_deux_editions_du_meme_fichier_s_enchainent(base, registry):
    """Le calcul différé ne doit pas perdre la première édition d'un fichier."""
    pid = depose(registry)
    plan = review.parse_plan(
        {
            "summary": "deux éditions sur la même fiche",
            "reviewed_by": "human:morva",
            "resolutions": [
                {"id": pid, "resolution": "accepted", "integrated_into": ["sso.md"]}
            ],
            "edits": [
                {"path": "sso.md", "append": "Une phrase ajoutée."},
                {"path": "sso.md", "frontmatter": {"last-verified": "2026-09-16"}},
            ],
        }
    )
    review.apply_plan(base, plan)
    obtenu = (base.corpus_dir / "sso.md").read_text(encoding="utf-8")
    assert "Une phrase ajoutée." in obtenu
    assert yaml.safe_load(obtenu.split("---\n")[1])["last-verified"] == "2026-09-16"
