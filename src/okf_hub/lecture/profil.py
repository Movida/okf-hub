"""Profil d'une base : le `schema.yaml` étendu (§ 3.5, rév. 4.5).

La spec fixe des **rôles**, la base fixe ses **noms** : un profil déclare, sans
code, quel champ, quelle section ou quelle facette tient quel rôle, dans la
langue de la base. L'extension est rétrocompatible — un `schema.yaml` v0 est un
profil sans enrichissement — et toutes les clés ajoutées sont optionnelles.

**Deux vocabulaires, deux langues.** Les clés du profil et les noms de rôles sont
le vocabulaire du *hub* : en anglais, comme le schéma v0 (`required`, `name`),
le manifeste, OKF et les paramètres des outils. Les titres de section, les noms
de facette et les valeurs sont le vocabulaire de la *base*, dans sa langue :

```yaml
optional:
  - name: provenance
    role: provenance
    values:
      interne: {label: Document interne, evidence: resource}
types:
  Procédure: {description: Un mode opératoire, paths: [/exploitation/]}
facets:
  separator: ":"
  env: {label: Environnement, values: {prod: Production}}
sections:
  Pièges: warnings
views:
  - {title: Pièges, kind: list, filter: {section: warnings}, group: {facet: module}}
```

Lecture **tolérante** : une partie mal formée est ignorée avec un
avertissement, jamais le profil entier, et jamais la base. Le profil améliore
l'affichage, il ne le conditionne pas. Une clé inconnue est ignorée sans
avertissement : une base peut y ranger ce dont ses propres contrôles ont
besoin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from ..mdutil import normalize_heading

#: Rôles reconnus — liste fermée, tenue par la spec (§ 3.5). Une base n'en
#: invente pas : elle en demande un par un retour d'usage.
ROLES_CHAMP = frozenset({"provenance", "aliases", "start", "end", "version", "identifier"})
ROLES_SECTION = frozenset({"warnings", "observations", "upstream", "procedure", "gaps"})

SORTES_VUE = ("list", "timeline", "graph", "freshness")

_VIDE: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class Valeur:
    libelle: str | None = None
    preuve: str | None = None
    """Champ qui porte la preuve d'un document qui a cette valeur (rôle provenance)."""


@dataclass(frozen=True)
class Champ:
    nom: str
    type: str | None = None
    description: str | None = None
    role: str | None = None
    requis: bool = False
    valeurs: Mapping[str, Valeur] = _VIDE


@dataclass(frozen=True)
class TypeDeclare:
    nom: str
    description: str | None = None
    ou: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class Facette:
    nom: str
    libelle: str | None = None
    valeurs: Mapping[str, str | None] = _VIDE
    """Valeurs déclarées, dans l'ordre du profil, avec leur libellé éventuel."""


@dataclass(frozen=True)
class Vue:
    """Une requête `kb_find` enregistrée, et une sorte de rendu (§ 3.5)."""

    titre: str
    sorte: str
    filtre: Mapping = _VIDE
    """Critères au format des paramètres de `kb_find`, validés à l'usage."""
    groupe: tuple[str, str] | None = None
    """('facet', nom), ('field', nom) ou ('type', '')."""


@dataclass(frozen=True)
class Profil:
    champs: Mapping[str, Champ] = _VIDE
    types: Mapping[str, TypeDeclare] = _VIDE
    separateur: str | None = None
    facettes: Mapping[str, Facette] = _VIDE
    sections: Mapping[str, str] = _VIDE
    """Titre de section normalisé → rôle."""
    vues: tuple[Vue, ...] = ()
    avertissements: tuple[str, ...] = ()

    @property
    def enrichi(self) -> bool:
        """Le profil déclare au moins un élément au-delà du schéma v0."""
        return bool(
            self.types or self.facettes or self.sections or self.vues
            or any(c.role or c.valeurs for c in self.champs.values())
        )

    def champ_du_role(self, role: str) -> Champ | None:
        for champ in self.champs.values():
            if champ.role == role:
                return champ
        return None

    def titres_du_role(self, role: str) -> frozenset[str]:
        return frozenset(t for t, r in self.sections.items() if r == role)

    def role_de_section(self, titre_normalise: str | None) -> str | None:
        if titre_normalise is None:
            return None
        return self.sections.get(titre_normalise)

    def facette_de(self, tag: str) -> tuple[str, str] | None:
        """(facette, valeur) d'un tag, si le profil déclare un séparateur.

        Sans séparateur déclaré, le moteur ne découpe rien de lui-même : une
        heuristique se tromperait sur des tags libres qui contiennent `:`.
        """
        if not self.separateur or self.separateur not in tag:
            return None
        nom, _, valeur = tag.partition(self.separateur)
        if not nom or not valeur:
            return None
        return nom, valeur


PROFIL_VIDE = Profil()


def charger_profil(chemin: Path | None) -> Profil:
    """Lit le profil désigné par `governance.frontmatter-schema` ; jamais ne lève."""
    if chemin is None:
        return PROFIL_VIDE
    try:
        brut = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return Profil(avertissements=(f"profil illisible : {exc}",))
    except yaml.YAMLError as exc:
        return Profil(avertissements=(f"profil non parseable : {exc}",))
    if brut is None:
        return PROFIL_VIDE
    if not isinstance(brut, dict):
        return Profil(avertissements=("profil : la racine doit être une correspondance",))
    return _Lecture(brut).profil()


@dataclass
class _Lecture:
    brut: dict
    avertissements: list[str] = field(default_factory=list)

    def note(self, message: str) -> None:
        self.avertissements.append(message)

    def profil(self) -> Profil:
        champs: dict[str, Champ] = {}
        for cle, requis in (("required", True), ("optional", False)):
            for champ in self._liste(cle):
                lu = self._champ(champ, requis, cle)
                if lu is not None and lu.nom not in champs:
                    champs[lu.nom] = lu
        roles = [c.role for c in champs.values() if c.role]
        for role in sorted({r for r in roles if roles.count(r) > 1}):
            self.note(f"rôle « {role} » porté par plusieurs champs : le premier est retenu")
        separateur, facettes = self._facettes()
        return Profil(
            champs=MappingProxyType(champs),
            types=MappingProxyType(self._types()),
            separateur=separateur,
            facettes=MappingProxyType(facettes),
            sections=MappingProxyType(self._sections()),
            vues=tuple(self._vues()),
            avertissements=tuple(self.avertissements),
        )

    # --- champs ---------------------------------------------------------------

    def _liste(self, cle: str) -> list:
        valeur = self.brut.get(cle)
        if valeur is None:
            return []
        if not isinstance(valeur, list):
            self.note(f"{cle} : liste attendue — ignoré")
            return []
        return valeur

    def _champ(self, brut, requis: bool, cle: str) -> Champ | None:
        if not isinstance(brut, dict) or not isinstance(brut.get("name"), str):
            self.note(f"{cle} : entrée sans `name` — ignorée")
            return None
        nom = brut["name"].strip()
        role = brut.get("role")
        if role is not None and role not in ROLES_CHAMP:
            self.note(f"champ {nom} : rôle inconnu « {role} » — ignoré")
            role = None
        return Champ(
            nom=nom,
            type=_texte(brut.get("type")),
            description=_texte(brut.get("description")),
            role=role,
            requis=requis,
            valeurs=MappingProxyType(self._valeurs_de_champ(nom, brut.get("values"))),
        )

    def _valeurs_de_champ(self, nom: str, brut) -> dict[str, Valeur]:
        if brut is None:
            return {}
        if isinstance(brut, list):
            return {str(v): Valeur() for v in brut if v is not None}
        if not isinstance(brut, dict):
            self.note(f"champ {nom} : `values` doit être une liste ou une correspondance — ignoré")
            return {}
        out: dict[str, Valeur] = {}
        for valeur, detail in brut.items():
            if isinstance(detail, dict):
                out[str(valeur)] = Valeur(_texte(detail.get("label")), _texte(detail.get("evidence")))
            else:
                out[str(valeur)] = Valeur(_texte(detail))
        return out

    # --- types, facettes, sections --------------------------------------------

    def _types(self) -> dict[str, TypeDeclare]:
        brut = self.brut.get("types")
        if brut is None:
            return {}
        if not isinstance(brut, dict):
            self.note("types : correspondance attendue — ignoré")
            return {}
        out: dict[str, TypeDeclare] = {}
        for nom, detail in brut.items():
            detail = detail if isinstance(detail, dict) else {}
            out[str(nom)] = TypeDeclare(
                nom=str(nom),
                description=_texte(detail.get("description")),
                ou=_chaines(detail.get("paths")),
                sections=_chaines(detail.get("sections")),
            )
        return out

    def _facettes(self) -> tuple[str | None, dict[str, Facette]]:
        brut = self.brut.get("facets")
        if brut is None:
            return None, {}
        if not isinstance(brut, dict):
            self.note("facets : correspondance attendue — ignoré")
            return None, {}
        separateur = brut.get("separator")
        if separateur is not None and (not isinstance(separateur, str) or not separateur):
            self.note("facets.separator : chaîne non vide attendue — ignoré")
            separateur = None
        out: dict[str, Facette] = {}
        for nom, detail in brut.items():
            if nom == "separator":
                continue
            detail = detail if isinstance(detail, dict) else {}
            valeurs = detail.get("values")
            if isinstance(valeurs, dict):
                lues = {str(v): _texte(l) for v, l in valeurs.items()}
            elif isinstance(valeurs, list):
                lues = {str(v): None for v in valeurs if v is not None}
            else:
                lues = {}
            out[str(nom)] = Facette(str(nom), _texte(detail.get("label")), MappingProxyType(lues))
        if out and separateur is None:
            self.note("facets déclarées sans `separator` : les tags ne seront pas découpés")
        return separateur, out

    def _sections(self) -> dict[str, str]:
        brut = self.brut.get("sections")
        if brut is None:
            return {}
        if not isinstance(brut, dict):
            self.note("sections : correspondance titre → rôle attendue — ignoré")
            return {}
        out: dict[str, str] = {}
        for titre, role in brut.items():
            if role not in ROLES_SECTION:
                self.note(f"section « {titre} » : rôle inconnu « {role} » — ignorée")
                continue
            out[normalize_heading(str(titre))] = role
        return out

    # --- vues -----------------------------------------------------------------

    def _vues(self) -> list[Vue]:
        out: list[Vue] = []
        for i, brut in enumerate(self._liste("views"), start=1):
            if not isinstance(brut, dict) or not isinstance(brut.get("title"), str):
                self.note(f"vue n° {i} : `title` manquant — ignorée")
                continue
            titre = brut["title"].strip()
            sorte = brut.get("kind", "list")
            if sorte not in SORTES_VUE:
                self.note(f"vue « {titre} » : kind inconnu « {sorte} » — ignorée")
                continue
            filtre = brut.get("filter") or {}
            if not isinstance(filtre, dict):
                self.note(f"vue « {titre} » : `filter` doit être une correspondance — ignoré")
                filtre = {}
            out.append(
                Vue(
                    titre=titre,
                    sorte=sorte,
                    filtre=MappingProxyType(dict(filtre)),
                    groupe=self._groupe(titre, brut.get("group")),
                )
            )
        return out

    def _groupe(self, titre: str, brut) -> tuple[str, str] | None:
        if brut is None:
            return None
        if brut == "type":
            return ("type", "")
        if isinstance(brut, dict) and len(brut) == 1:
            ((sorte, nom),) = brut.items()
            if sorte in ("facet", "field") and isinstance(nom, str) and nom:
                return (sorte, nom)
        self.note(f"vue « {titre} » : `group` attend type, {{facet: …}} ou {{field: …}} — ignoré")
        return None


def _texte(valeur) -> str | None:
    if valeur is None:
        return None
    texte = str(valeur).strip()
    return texte or None


def _chaines(valeur) -> tuple[str, ...]:
    if isinstance(valeur, str):
        return (valeur,)
    if isinstance(valeur, list):
        return tuple(str(v) for v in valeur if v is not None)
    return ()
