"""Conformité des bases « meta » livrées avec le hub.

`okf-hub-guide` et `okf-hub-feedback` sont des bundles ordinaires — ils ne
reçoivent aucun traitement de faveur du code. Ce qui les rend particuliers, c'est
qu'ils **parlent du hub**, donc qu'ils peuvent se désynchroniser de lui.

Ces tests sont le garde-fou. Ils portent sur ce qu'une relecture humaine rate :
un nom d'outil qui n'existe plus, un paramètre renommé, un guide qui se met à
recopier la référence d'API au lieu de décrire des procédures.

Ils lisent les **sources** de `bundles/`, versionnées dans ce dépôt — et non les
bases déployées dans `bases/`, qui est ignoré par git. C'est ce qui les fait
tourner en intégration continue, sur un checkout neuf : tant que la source vivait
hors du dépôt, ce garde-fou se contentait de `skip` là où il aurait dû mordre.
"""

from __future__ import annotations

import re

import pytest
import yaml

from conftest import HUB_ROOT

from okf_hub.governance import STABLE, status_of_file
from okf_hub.manifest import load_manifest
from okf_hub.server import META_BASES, TOOLS

BUNDLES_DIR = HUB_ROOT / "bundles"
BASES_DIR = HUB_ROOT / "bases"

#: Noms d'outils réellement exposés — la seule liste qui fasse foi.
NOMS_D_OUTILS = {spec.name for spec in TOOLS}

#: Paramètres réellement acceptés, par outil, lus dans les SCHEMA du code.
PARAMS_PAR_OUTIL = {
    spec.name: set(spec.schema.get("properties", {})) for spec in TOOLS
}

#: `kb_quelquechose` mentionné dans un corpus.
_OUTIL_CITE = re.compile(r"\b(kb_[a-z_]+)\b")

#: Un paramètre attribué à un outil : `kb_read(path, section)`, `section` de
#: `kb_read`, `kb_read` avec `limit`… On ne capture que la forme d'appel, la
#: seule qui soit sans ambiguïté sur l'outil visé.
_APPEL_AVEC_PARAMS = re.compile(r"\b(kb_[a-z_]+)\s*\(([^)]*)\)")
_NOM_DE_PARAM = re.compile(r"\b([a-z][a-z_]{2,})\b")


def bundles_meta() -> list[str]:
    """Sources des bases meta livrées avec le hub, versionnées dans `bundles/`."""
    return [
        nom for nom in META_BASES if (BUNDLES_DIR / nom / "okf-bundle.yaml").is_file()
    ]


def documents(nom: str) -> list:
    return sorted((BUNDLES_DIR / nom / "knowledge").rglob("*.md"))


META_LIVREES = bundles_meta()

pytestmark = pytest.mark.skipif(
    not META_LIVREES, reason="aucune source de base meta dans bundles/"
)


@pytest.fixture(params=META_LIVREES)
def base_meta(request) -> str:
    return request.param


# --- conformité de bundle -----------------------------------------------------


def test_manifeste_valide_et_sans_avertissement(base_meta):
    manifeste = load_manifest(BUNDLES_DIR / base_meta)
    assert manifeste.name == base_meta
    assert manifeste.warnings == []
    assert manifeste.frontmatter_schema is not None


def test_gouvernance_stable(base_meta):
    """Ces bases ne sont pas des templates : leurs règles sont arbitrées."""
    manifeste = load_manifest(BUNDLES_DIR / base_meta)
    assert status_of_file(manifeste.governance_rules) == STABLE


def test_description_orientee_routage(base_meta):
    """Elle est injectée dans les descriptions d'outils : elle doit dire quand
    interroger cette base, pas seulement de quoi elle parle."""
    description = load_manifest(BUNDLES_DIR / base_meta).description
    assert len(description) > 150
    assert "kb_" in description


def test_sommaire_et_journal_presents(base_meta):
    """Conventions OKF § 8 et § 9."""
    knowledge = BUNDLES_DIR / base_meta / "knowledge"
    assert (knowledge / "index.md").is_file()
    assert (knowledge / "log.md").is_file()


def test_chaque_document_porte_type_et_version(base_meta):
    """Golden rule commune : une affirmation sur le hub porte sa révision."""
    for chemin in documents(base_meta):
        if chemin.name in ("index.md", "log.md"):
            continue
        texte = chemin.read_text(encoding="utf-8")
        assert texte.startswith("---\n"), f"{chemin.name} : frontmatter absent"
        frontmatter = yaml.safe_load(texte.split("---\n")[1])
        assert frontmatter.get("type"), f"{chemin.name} : `type` requis (OKF § 4.1)"
        assert frontmatter.get("applies-to"), f"{chemin.name} : `applies-to` requis"


def test_le_sommaire_reference_tous_les_documents(base_meta):
    """Un document absent du sommaire est invisible à la divulgation progressive."""
    sommaire = (BUNDLES_DIR / base_meta / "knowledge" / "index.md").read_text(
        encoding="utf-8"
    )
    for chemin in documents(base_meta):
        if chemin.name in ("index.md", "log.md"):
            continue
        assert chemin.name in sommaire, f"{chemin.name} absent de index.md"


# --- garde-fou anti-dérive ----------------------------------------------------


def test_aucun_outil_inexistant_n_est_cite(base_meta):
    """Un outil renommé ou retiré doit faire échouer la suite, pas égarer une
    session six mois plus tard."""
    for chemin in documents(base_meta):
        cites = set(_OUTIL_CITE.findall(chemin.read_text(encoding="utf-8")))
        inconnus = cites - NOMS_D_OUTILS
        assert not inconnus, f"{chemin.name} cite des outils inexistants : {inconnus}"


def test_aucun_parametre_inexistant_n_est_attribue_a_un_outil(base_meta):
    """`kb_read(path, section)` doit rester vrai. C'est la dérive la plus
    probable : un paramètre renommé dans le code, oublié dans le corpus."""
    for chemin in documents(base_meta):
        texte = chemin.read_text(encoding="utf-8")
        for outil, arguments in _APPEL_AVEC_PARAMS.findall(texte):
            if outil not in PARAMS_PAR_OUTIL:
                continue
            cites = set(_NOM_DE_PARAM.findall(arguments))
            inconnus = cites - PARAMS_PAR_OUTIL[outil] - {"base"}
            assert not inconnus, (
                f"{chemin.name} : {outil}({arguments}) mentionne {inconnus}, "
                f"absent de son schéma"
            )


def test_le_guide_ne_recopie_pas_la_reference_d_api():
    """Golden rule 1 de `okf-hub-guide` : aucun schéma d'outil.

    On ne peut pas détecter une intention, mais on peut détecter la forme qu'elle
    prend toujours : un tableau dont l'en-tête annonce des paramètres et des
    types. C'est exactement ce que `docs/API.md` contient, et qui n'a pas à être
    dupliqué dans un corpus mis à jour par le circuit de propositions.
    """
    if "okf-hub-guide" not in META_LIVREES:
        pytest.skip("okf-hub-guide non déployée")

    # Une colonne « Paramètre », ou le couple « Type » + « Défaut » : c'est la
    # forme d'un schéma. Un tableau `| Type | Quand |` décrivant une sémantique
    # n'en est pas un — le premier motif écrit ici le prenait pour tel.
    ligne_d_entete = re.compile(r"^\|.*\|\s*$", re.MULTILINE)

    def est_une_reference(ligne: str) -> bool:
        colonnes = [c.strip().casefold() for c in ligne.strip("|").split("|")]
        if any(c.startswith("param") for c in colonnes):
            return True
        return "type" in colonnes and any(c.startswith("défaut") or c.startswith("defaut") for c in colonnes)

    for chemin in documents("okf-hub-guide"):
        texte = chemin.read_text(encoding="utf-8")
        fautives = [l for l in ligne_d_entete.findall(texte) if est_une_reference(l)]
        assert not fautives, (
            f"{chemin.name} : tableau de paramètres détecté ({fautives}). "
            f"La référence vit dans les descriptions d'outils et docs/API.md, "
            f"jamais ici (golden rule 1)."
        )


# --- découvrabilité -----------------------------------------------------------


def test_les_instructions_annoncent_les_bases_meta_deployees(hub, make_bundle):
    """Les instructions du serveur sont le seul texte qu'une session reçoit sans
    dépenser d'appel : c'est de là que le guide doit être découvrable."""
    from okf_hub.config import HubConfig
    from okf_hub.server import HubServer

    hub_root, bases = hub
    for nom in META_LIVREES:
        make_bundle(nom, name=nom)
    serveur = HubServer(HubConfig.load(hub_root))

    instructions = serveur.build().instructions
    for nom in META_LIVREES:
        assert nom in instructions


def test_une_base_meta_absente_n_est_pas_annoncee(hub, make_bundle):
    """Annoncer un guide inexistant coûterait un aller-retour pour UNKNOWN_BASE."""
    from okf_hub.config import HubConfig
    from okf_hub.server import HubServer

    hub_root, _ = hub
    make_bundle("metier", name="metier")
    instructions = HubServer(HubConfig.load(hub_root)).build().instructions

    assert "Bases décrivant le hub" not in instructions
    for nom in META_BASES:
        assert nom not in instructions


# --- la base déployée ne doit pas diverger de sa source -----------------------


@pytest.mark.skipif(
    not (BASES_DIR / "okf-hub-guide" / "okf-bundle.yaml").is_file(),
    reason="okf-hub-guide non déployée sur ce hub",
)
def test_le_guide_deploye_est_conforme_a_sa_source():
    """`okf-hub-guide` est rédigée par les mainteneurs, en verrou avec le code :
    sa source de vérité est `bundles/`, pas la copie déployée.

    Ce test échoue si une résolution a été appliquée à la base déployée sans être
    reportée dans `bundles/` — auquel cas c'est le report qu'il faut faire, pas
    le test qu'il faut assouplir.

    `okf-hub-feedback` n'est volontairement pas soumise à cette règle : elle est
    alimentée par les sessions, son corpus s'enrichit de propositions intégrées,
    et le dépôt déployé y devient légitimement l'original.
    """
    source = BUNDLES_DIR / "okf-hub-guide" / "knowledge"
    deploye = BASES_DIR / "okf-hub-guide" / "knowledge"

    attendus = {p.relative_to(source): p.read_text(encoding="utf-8") for p in source.rglob("*.md")}
    obtenus = {p.relative_to(deploye): p.read_text(encoding="utf-8") for p in deploye.rglob("*.md")}

    assert set(obtenus) == set(attendus), (
        "le corpus déployé et sa source n'ont pas les mêmes documents — "
        "reporter la différence dans bundles/okf-hub-guide/"
    )
    divergents = sorted(str(k) for k in attendus if obtenus[k] != attendus[k])
    assert not divergents, (
        f"documents divergents entre bases/ et bundles/ : {divergents}. "
        f"La source de vérité du guide est bundles/ — y reporter la modification."
    )
