"""Publier une base partagée vers sa réplique (spec rév. 4.8, § 4.6.c).

Chaque refus arrête tout avant le premier envoi : la réplique (un dépôt nu
jetable) est relue après chaque refus. Les faux secrets sont assemblés à
l'exécution, jamais écrits en clair dans ce fichier : un dépôt public ne doit
pas porter ce qu'un scanner prendrait pour un vrai jeton.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from okf_hub import publication
from okf_hub.__main__ import main as okf_hub_main
from okf_hub.config import HubConfig
from okf_hub.publication import PublicationRefusee, publier

from conftest import git

JETON_GITHUB = "gh" + "p_" + "Ab1" * 12
CLE_PRIVEE = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"


def _local(hub_root, **cles):
    (hub_root / "hub-config.local.yaml").write_text(yaml.safe_dump(cles), encoding="utf-8")


def _commit(base, rel, texte, message="ajout"):
    chemin = base.root / "knowledge" / rel
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8")
    git(base.root, "add", "-A")
    git(base.root, "commit", "-q", "-m", message)


def _distant(replique) -> str:
    out = subprocess.run(["git", "-C", str(replique), "rev-parse", "--verify", "--quiet",
                          "refs/heads/main"], capture_output=True, text=True)
    return out.stdout.strip()


@pytest.fixture
def poste(hub, make_bundle, tmp_path):
    """`base-ouverte` partagée, avec une réplique (dépôt nu) en remote ;
    `base-fermee` (corpus `ferme-kb`) privée ; `MONORG` terme privé."""
    hub_root, _ = hub
    ouverte = make_bundle("base-ouverte", name="base-ouverte")
    make_bundle("base-fermee", name="base-fermee", corpus_dir="ferme-kb")
    replique = tmp_path / "replique" / "base-ouverte.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(replique)], check=True)
    git(ouverte.root, "remote", "add", "replica", str(replique))
    _local(hub_root, **{"shared-bases": ["base-ouverte"], "private-terms": ["MONORG"]})
    return HubConfig.load(hub_root), ouverte, replique


def test_publie_une_base_partagee(poste):
    config, base, replique = poste
    bilan = publier(config, "base-ouverte")
    assert bilan.pousse and len(bilan.commits) == 1
    assert _distant(replique) == git(base.root, "rev-parse", "HEAD").strip()
    # Seconde fois : rien à publier.
    assert publier(config, "base-ouverte").commits == []


def test_seuls_les_commits_nouveaux_partent(poste):
    config, base, replique = poste
    publier(config, "base-ouverte")
    _commit(base, "suite.md", "# Suite\n\nUne affirmation de plus.\n")
    bilan = publier(config, "base-ouverte")
    assert len(bilan.commits) == 1
    assert _distant(replique) == git(base.root, "rev-parse", "HEAD").strip()


def test_une_base_privee_ne_se_publie_pas(poste, tmp_path):
    config, _, _ = poste
    with pytest.raises(PublicationRefusee, match="n'est pas une base partagée"):
        publier(config, "base-fermee")


def test_un_terme_prive_bloque_l_envoi(poste):
    config, base, replique = poste
    _commit(base, "fuite.md", "# Note\n\nVoir aussi base-fermee pour le détail.\n")
    with pytest.raises(PublicationRefusee) as exc:
        publier(config, "base-ouverte")
    assert any("knowledge/fuite.md:3" in d and "base-fermee" in d for d in exc.value.details)
    assert _distant(replique) == ""


def test_un_terme_de_private_terms_bloque_l_envoi(poste):
    config, base, replique = poste
    _commit(base, "client.md", "# Client\n\nInstallé chez MonOrg.\n")
    with pytest.raises(PublicationRefusee):
        publier(config, "base-ouverte")
    assert _distant(replique) == ""


def test_le_nom_d_une_base_partagee_n_est_pas_un_terme_prive(poste):
    config, base, _ = poste
    _commit(base, "lien.md", "# Lien\n\nVoir [la fiche](base-ouverte:/exemple.md).\n")
    assert publier(config, "base-ouverte").pousse


def test_un_message_ou_un_chemin_compte_aussi(poste):
    config, base, replique = poste
    _commit(base, "ferme-kb-notes.md", "# Notes\n\nRien de privé ici.\n")
    with pytest.raises(PublicationRefusee) as exc:
        publier(config, "base-ouverte")
    assert any("(chemin)" in d for d in exc.value.details)
    assert _distant(replique) == ""


@pytest.mark.parametrize("ligne", [
    f"jeton : {JETON_GITHUB}",
    CLE_PRIVEE,
    "password: Xk9vR2mQ7pLw",
    "API_KEY=4f9a2c7e1b8d",
])
def test_un_secret_bloque_l_envoi_sans_etre_recopie(poste, ligne, capsys):
    config, base, replique = poste
    _commit(base, "config.md", f"# Config\n\n{ligne}\n")
    assert okf_hub_main(["--hub-root", str(config.hub_root), "publish", "base-ouverte"]) == 1
    err = capsys.readouterr().err
    assert "knowledge/config.md:3" in err
    valeur = ligne.split(":", 1)[-1].split("=", 1)[-1].strip()
    assert valeur not in err
    assert _distant(replique) == ""


@pytest.mark.parametrize("ligne", [
    "password: op://coffre/element/mot-de-passe",
    "token: ${TOKEN_DU_SERVICE}",
    "api_key: <la clé de l'opérateur>",
    "Le token : identifiant unique de session.",
])
def test_une_reference_ou_de_la_prose_ne_sont_pas_des_secrets(poste, ligne):
    config, base, _ = poste
    _commit(base, "reference.md", f"# Référence\n\n{ligne}\n")
    assert publier(config, "base-ouverte").pousse


def test_secret_patterns_de_l_operateur(poste):
    config, base, replique = poste
    _local(config.hub_root, **{"shared-bases": ["base-ouverte"],
                               "secret-patterns": [r"MAISON-[0-9A-F]{16}"]})
    config = HubConfig.load(config.hub_root)
    _commit(base, "maison.md", "# Maison\n\nCode MAISON-0123456789ABCDEF.\n")
    with pytest.raises(PublicationRefusee) as exc:
        publier(config, "base-ouverte")
    assert any("secret-patterns" in d for d in exc.value.details)
    assert _distant(replique) == ""


def test_secret_patterns_invalide_refuse_au_chargement(hub):
    hub_root, _ = hub
    _local(hub_root, **{"secret-patterns": ["(non fermée"]})
    with pytest.raises(ValueError, match="secret-patterns"):
        HubConfig.load(hub_root)


def test_divergence_jamais_forcee(poste, tmp_path):
    config, base, replique = poste
    publier(config, "base-ouverte")
    autre = tmp_path / "autre"
    subprocess.run(["git", "clone", "-q", str(replique), str(autre)], check=True)
    (autre / "intrus.md").write_text("intrus\n", encoding="utf-8")
    git(autre, "add", "-A")
    git(autre, "commit", "-q", "-m", "intrus")
    git(autre, "push", "-q", "origin", "HEAD:main")
    avant = _distant(replique)
    _commit(base, "suite.md", "# Suite\n\nUne affirmation.\n")
    with pytest.raises(PublicationRefusee, match="divergence"):
        publier(config, "base-ouverte")
    assert _distant(replique) == avant


def test_une_replique_qui_refuse_toute_lecture_par_git(poste):
    """Une réplique peut ne servir que la poussée (la lecture passant par son
    MCP, qui applique la visibilité) : la publication ne tire jamais rien."""
    config, base, replique = poste
    git(base.root, "config", "remote.replica.uploadpack", "false")
    assert publier(config, "base-ouverte").pousse
    _commit(base, "suite.md", "# Suite\n\nUne affirmation de plus.\n")
    bilan = publier(config, "base-ouverte")
    assert len(bilan.commits) == 1
    assert _distant(replique) == git(base.root, "rev-parse", "HEAD").strip()
    assert publier(config, "base-ouverte").commits == []


def test_replique_injoignable_refusee_sans_rien_envoyer(poste, tmp_path):
    config, base, _ = poste
    git(base.root, "remote", "set-url", "replica", str(tmp_path / "absente.git"))
    with pytest.raises(PublicationRefusee, match="injoignable"):
        publier(config, "base-ouverte")


def test_sans_remote_refuse_avec_la_commande(poste):
    config, base, _ = poste
    git(base.root, "remote", "remove", "replica")
    with pytest.raises(PublicationRefusee, match="remote add replica"):
        publier(config, "base-ouverte")


def test_dry_run_n_envoie_rien(poste, capsys):
    config, _, replique = poste
    code = okf_hub_main(["--hub-root", str(config.hub_root), "publish", "--dry-run",
                         "base-ouverte"])
    assert code == 0 and "rien envoyé" in capsys.readouterr().out
    assert _distant(replique) == ""


def test_la_base_n_est_pas_modifiee(poste):
    config, base, _ = poste
    avant = git(base.root, "status", "--porcelain")
    tete = git(base.root, "rev-parse", "HEAD")
    publier(config, "base-ouverte")
    assert git(base.root, "status", "--porcelain") == avant
    assert git(base.root, "rev-parse", "HEAD") == tete


def test_numerotation_des_lignes_ajoutees():
    diff = (
        "+++ b/a.md\n@@ -1,2 +1,3 @@\n ligne 1\n-ancienne\n+nouvelle\n+autre\n ligne 3\n"
        "@@ -10 +11 @@\n+loin\n"
    )
    lignes = publication.lignes_ajoutees(diff, "abc")
    assert [(l.ou, l.texte) for l in lignes] == [
        ("a.md (chemin)", "a.md"), ("a.md:2", "nouvelle"), ("a.md:3", "autre"),
        ("a.md:11", "loin"),
    ]
