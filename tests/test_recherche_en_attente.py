"""Propositions en attente cherchées par `kb_search`, date de la plus ancienne
dans `kb_list` (spec rév. 4.7, § 5.1, § 5.2).

L'option est désactivée par défaut : sans elle, rien ne change, pas même ce qui
est lu sur le disque. Avec elle, ce qui n'est pas revu passe après ce qui l'est
— dans le plafond `max_results` comme dans la sortie — et chaque résultat est
marqué « non intégré ».
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from okf_hub import search
from okf_hub.errors import INVALID_INPUT, ToolError
from okf_hub.textutil import CHAR_CAP
from okf_hub.tools import list_tool, proposal_status_tool, propose_tool, search_tool

from conftest import git


def base(make_bundle, registry, docs: dict[str, str], nom: str = "produit-x"):
    b = make_bundle(nom, name=nom, git_init=False)
    for rel, text in docs.items():
        b.doc(rel, text)
    b.init_git()
    registry.scan()
    return b


def deposer(registry, nom: str, concerns: str, content: str, **kw) -> str:
    sortie = propose_tool.run(registry, {
        "base": nom, "type": kw.get("type", "observation"), "concerns": concerns,
        "content": content, "sources": kw.get("sources", ["constat de session"]),
        "confidence": "medium", "submitted_by": "human:test",
    })
    return next(l.split(":", 1)[1].strip() for l in sortie.splitlines() if l.startswith("id :"))


def chercher(registry, **arguments) -> str:
    return search_tool.run(registry, {"base": "produit-x", **arguments})


# --- sans l'option : rien ne change -------------------------------------------


def test_sans_l_option_la_sortie_est_identique_et_proposals_n_est_pas_lu(
    hub, make_bundle, registry, monkeypatch
):
    base(make_bundle, registry, {"sso.md": "# SSO\n\n## Reconnexion\n\nCliquer sur réauthentifier.\n"})
    avant = {q: chercher(registry, query=q) for q in ("réauthentifier", "xz42")}
    deposer(registry, "produit-x", "bouton réauthentifier déplacé (XZ42)", "Constaté sur XZ42.")

    def interdit(*a, **k):
        raise AssertionError("proposals/ lu sans include_pending")

    monkeypatch.setattr(search_tool, "run_search_pending", interdit)
    for q, attendu in avant.items():
        assert chercher(registry, query=q) == attendu
        assert chercher(registry, query=q, include_pending=False) == attendu
    assert "non intégré" not in chercher(registry, query="réauthentifier")


def test_include_pending_doit_etre_un_booleen(hub, make_bundle, registry):
    base(make_bundle, registry, {"a.md": "# A\n"})
    with pytest.raises(ToolError) as exc:
        chercher(registry, query="a", include_pending="oui")
    assert exc.value.code == INVALID_INPUT


# --- avec l'option -------------------------------------------------------------


def test_une_proposition_en_attente_est_trouvee_et_marquee(hub, make_bundle, registry):
    base(make_bundle, registry, {"sso.md": "# SSO\n\nRien sur le ticket.\n"})
    pid = deposer(
        registry, "produit-x", "ticket XZ42 : bouton déplacé",
        "Le bouton est dans le menu profil depuis la correction XZ42.", type="correction",
    )
    out = chercher(registry, query="xz42", include_pending=True)

    assert out.startswith("Aucun résultat dans le corpus de la base 'produit-x' pour : xz42")
    assert "[NON INTÉGRÉ —" in out and "kb_proposal_status" in out
    assert "1 proposition(s) en attente dans 'produit-x' pour : xz42" in out
    assert f"### {pid} — non intégré, en attente de revue" in out
    soumise = proposal_status_tool.collect(registry.get("produit-x"), ("pending",)).entries[0].submitted_at
    assert f"type : correction | concerns : ticket XZ42 : bouton déplacé | soumise : {soumise}" in out
    # Le frontmatter est résumé par l'en-tête, pas recopié ; le corps l'est.
    assert "trouvé dans le frontmatter : concerns" in out
    assert "submitted-by" not in out and "status: pending" not in out
    assert "depuis la correction XZ42" in out


def test_un_extrait_de_proposition_ne_porte_pas_de_section(hub, make_bundle, registry):
    base(make_bundle, registry, {"a.md": "# A\n"})
    deposer(registry, "produit-x", "sujet", "## Détail\n\nmot-temoin dans une section.")
    out = chercher(registry, query="mot-temoin", include_pending=True)
    extrait = next(l for l in out.splitlines() if re.match(r"^  L\d+-\d+", l))
    # kb_read ne lit pas les propositions : un « § » serait une fausse piste.
    assert "§" not in extrait


def test_le_corpus_passe_d_abord_et_reste_inchange(hub, make_bundle, registry):
    base(make_bundle, registry, {
        "sso.md": "# SSO\n\n## Reconnexion\n\nCliquer sur réauthentifier.\n",
        "faq.md": "# FAQ\n\nréauthentifier : voir SSO.\n",
    })
    sans = chercher(registry, query="réauthentifier")
    deposer(registry, "produit-x", "réauthentifier ne marche plus", "Le bouton réauthentifier a disparu.")
    avec = chercher(registry, query="réauthentifier", include_pending=True)

    # Ce que la recherche normale montre est un préfixe de la sortie avec l'option.
    assert avec.startswith(sans)
    reste = avec[len(sans):]
    assert reste.lstrip().startswith("[NON INTÉGRÉ —")
    assert "1 proposition(s) en attente" in reste


def test_le_plafond_est_partage_et_le_corpus_le_remplit_d_abord(hub, make_bundle, registry):
    base(make_bundle, registry, {"a.md": "# A\n\ncible\n", "b.md": "# B\n\ncible\n"})
    pid = deposer(registry, "produit-x", "cible aussi ici", "cible")

    plein = chercher(registry, query="cible", include_pending=True, max_results=3)
    # a.md, b.md et le exemple.md de la fabrique n'en font que deux : un reste.
    assert pid in plein

    plein = chercher(registry, query="cible", include_pending=True, max_results=2)
    assert "2 résultat(s) dans 'produit-x'" in plein
    assert "1 proposition(s) en attente dans 'produit-x' pour : cible" in plein
    assert "[1 non listée(s) : max_results (2) vaut pour le corpus et les propositions" in plein
    assert f"### {pid}" not in plein


def test_meme_budget_de_sortie_que_la_recherche_normale(hub, make_bundle, registry):
    gros = "cible " + ("remplissage " * 900)
    base(make_bundle, registry, {f"doc{i:02d}.md": f"# Doc {i}\n\n{gros}\n" for i in range(6)})
    deposer(registry, "produit-x", "cible en attente", "cible")
    out = chercher(registry, query="cible", include_pending=True, max_results=25)
    assert "[résultats tronqués" in out
    assert len(out) < CHAR_CAP * 1.5
    # L'en-tête de la part non revue est toujours émis, même quand ses résultats
    # ne tiennent plus : la session sait qu'il y a quelque chose.
    assert "1 proposition(s) en attente dans 'produit-x'" in out


def test_regex_et_repli_en_ou_dans_les_propositions(hub, make_bundle, registry):
    base(make_bundle, registry, {"a.md": "# A\n"})
    pid = deposer(registry, "produit-x", "erreur E-4012", "Message E-4012 au démarrage.")
    out = chercher(registry, query=r"E-\d{4}", mode="regex", include_pending=True)
    assert pid in out
    out = chercher(registry, query="E-4012 inexistant", include_pending=True)
    assert "[aucune proposition ne contient tous les termes — résultats partiels]" in out
    with pytest.raises(ToolError) as exc:
        chercher(registry, query="(", mode="regex", include_pending=True)
    assert exc.value.code == INVALID_INPUT


def test_rien_dans_les_propositions_est_dit(hub, make_bundle, registry):
    base(make_bundle, registry, {"a.md": "# A\n\ncible\n"})
    deposer(registry, "produit-x", "autre sujet", "rien à voir")
    out = chercher(registry, query="cible", include_pending=True)
    assert out.rstrip().endswith("[propositions en attente : aucune ne répond, sur 1 examinée(s)]")
    out = chercher(registry, query="introuvable-xyz", include_pending=True)
    assert out == (
        "Aucun résultat dans la base 'produit-x' pour : introuvable-xyz — ni dans le "
        "corpus, ni dans les 1 proposition(s) en attente examinée(s)"
    )


def test_plusieurs_bases_propositions_apres_tout_le_corpus(hub, make_bundle, registry):
    base(make_bundle, registry, {"a.md": "# A\n\ncible\n"}, nom="produit-x")
    base(make_bundle, registry, {"b.md": "# B\n\ncible\n"}, nom="installation-x")
    p1 = deposer(registry, "produit-x", "cible", "cible")
    p2 = deposer(registry, "installation-x", "cible", "cible")
    out = search_tool.run(registry, {"base": "*", "query": "cible", "include_pending": True})

    dernier_corpus = max(out.index("## Base : installation-x\n"), out.index("## Base : produit-x\n"))
    marque = out.index("[NON INTÉGRÉ —")
    assert dernier_corpus < marque
    assert out.index("## Base : installation-x — non intégré") > marque
    assert out.index(p2) < out.index("## Base : produit-x — non intégré") < out.index(p1)


def test_seules_les_propositions_en_attente_sont_cherchees(hub, make_bundle, registry):
    b = base(make_bundle, registry, {"a.md": "# A\n"})
    for statut in ("accepted", "rejected"):
        d = b.root / "proposals" / statut
        d.mkdir(parents=True)
        (d / f"prop-{statut}.md").write_text(
            f"---\nid: prop-{statut}\ntype: observation\nconcerns: mot-temoin\n---\n\nmot-temoin\n",
            encoding="utf-8",
        )
    out = chercher(registry, query="mot-temoin", include_pending=True)
    assert "prop-accepted" not in out and "prop-rejected" not in out


def test_confinement_un_lien_symbolique_sortant_est_ignore(hub, make_bundle, registry, tmp_path):
    b = base(make_bundle, registry, {"a.md": "# A\n"})
    dehors = tmp_path / "secret.md"
    dehors.write_text("---\nid: prop-secret\n---\nmot-temoin hors du bundle\n", encoding="utf-8")
    pending = b.root / "proposals" / "pending"
    pending.mkdir(parents=True)
    os.symlink(dehors, pending / "prop-2026-01-01-evad.md")
    out = chercher(registry, query="mot-temoin", include_pending=True)
    assert "hors du bundle" not in out and "prop-secret" not in out
    assert out.endswith("ni dans les 0 proposition(s) en attente examinée(s)")


def test_la_recherche_dans_les_propositions_n_ecrit_rien(hub, make_bundle, registry):
    b = base(make_bundle, registry, {"a.md": "# A\n\ncible\n"})
    deposer(registry, "produit-x", "cible", "cible")
    avant = git(b.root, "status", "--porcelain", "--ignored")
    chercher(registry, query="cible", include_pending=True)
    assert git(b.root, "status", "--porcelain", "--ignored") == avant


def test_un_fichier_disparu_pendant_la_recherche_n_est_pas_une_erreur(tmp_path):
    present = tmp_path / "prop-a.md"
    present.write_text("mot-temoin\n", encoding="utf-8")
    absent = tmp_path / "prop-b.md"   # déplacé vers accepted/ entre la liste et la lecture
    hits = search._run_rg_files("mot-temoin", [present, absent], fixed=True)
    assert hits == {str(present): [1]}
    # Une expression invalide reste une erreur d'entrée, fichier manquant ou non.
    with pytest.raises(ToolError) as exc:
        search._run_rg_files("(", [present, absent], fixed=False)
    assert exc.value.code == INVALID_INPUT


def test_une_longue_file_est_cherchee_par_lots(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "FILES_PER_RG_CALL", 2)
    fichiers = []
    for i in range(5):
        f = tmp_path / f"prop-{i}.md"
        f.write_text("mot-temoin\n" if i % 2 == 0 else "autre\n", encoding="utf-8")
        fichiers.append(f)
    hits = search._run_rg_files("mot-temoin", fichiers, fixed=True)
    assert sorted(hits) == sorted(str(fichiers[i]) for i in (0, 2, 4))


# --- kb_list : date de la plus ancienne ------------------------------------------


def test_kb_list_donne_la_date_de_la_plus_ancienne(hub, make_bundle, registry):
    b = base(make_bundle, registry, {"a.md": "# A\n"})
    assert "propositions en attente : 0\n" in list_tool.run(registry, {}) + "\n"
    assert "la plus ancienne" not in list_tool.run(registry, {})

    pending = b.root / "proposals" / "pending"
    pending.mkdir(parents=True)
    # Les trois formes rencontrées : chaîne quotée, horodatage non quoté (lu en
    # datetime par PyYAML), date absente — comptée, pas datée.
    (pending / "prop-1.md").write_text(
        "---\nid: prop-1\nsubmitted-at: '2026-10-19T08:12:00Z'\n---\n\nx\n", encoding="utf-8")
    (pending / "prop-2.md").write_text(
        "---\nid: prop-2\nsubmitted-at: 2026-10-17T23:30:00-02:00\n---\n\nx\n", encoding="utf-8")
    (pending / "prop-3.md").write_text("---\nid: prop-3\n---\n\nx\n", encoding="utf-8")
    out = list_tool.run(registry, {})
    # 23:30 à UTC−2, c'est le 18 en UTC.
    assert "propositions en attente : 3 (la plus ancienne : 2026-10-18)" in out


def test_kb_list_sans_date_lisible_compte_sans_dater(hub, make_bundle, registry):
    b = base(make_bundle, registry, {"a.md": "# A\n"})
    pending = b.root / "proposals" / "pending"
    pending.mkdir(parents=True)
    (pending / "prop-1.md").write_text("pas de frontmatter\n", encoding="utf-8")
    out = list_tool.run(registry, {})
    assert "propositions en attente : 1" in out and "la plus ancienne" not in out


def test_kb_list_ne_touche_pas_aux_propositions(hub, make_bundle, registry):
    b = base(make_bundle, registry, {"a.md": "# A\n"})
    deposer(registry, "produit-x", "sujet", "contenu")
    avant = git(b.root, "status", "--porcelain", "--ignored")
    list_tool.run(registry, {"include_pending_concerns": True})
    assert git(b.root, "status", "--porcelain", "--ignored") == avant
    assert subprocess.run(["git", "-C", str(b.root), "diff", "--quiet"]).returncode == 0
