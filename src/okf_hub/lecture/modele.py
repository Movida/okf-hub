"""Modèle en mémoire des bases, dérivé du disque (§ 5 bis.1, rév. 4.5).

**Un cache, jamais une autorité** (§ 1.1, § 4.4.a). Chaque processus — une
instance MCP, l'interface web — tient le sien, sans rien partager ni persister.

**Invalidation.** La signature d'une base est le parcours de son corpus :
chemin, `mtime_ns` et taille de chaque document, plus le profil. Elle se
calcule en quelques dizaines de millisecondes sur une base de plusieurs
milliers de documents, et n'est recalculée qu'au plus toutes les
`INTERVALLE_VERIFICATION_S` secondes. `git status` n'aurait pas suffi : un
fichier déjà modifié qui l'est une seconde fois ne change pas sa sortie. Seule
la base qui a changé est rechargée.

**Dates de commit.** Un seul `git log` par base, à la demande, mis en cache par
HEAD : il coûte plus d'une seconde sur un gros historique, et seules la
fraîcheur et la page d'un document en ont besoin.

**Mesure du 2026-10-29** (8 bases, 3 733 documents) : frontmatters lus en
0,41 s par `CSafeLoader` au lieu de 2,79 s par le chargeur Python pur, sans
aucune divergence entre les deux ; liens extraits en 1,8 s, contre 17,4 s par
le parseur markdown du rendu.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import subprocess
import threading
import time
import weakref
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from .. import hublog
from ..mdutil import Heading, parse_document, parse_headings
from ..registry import Base, Registry
from ..search import RESERVED_NAMES
from .confiance import COMMIT, FICHIER, UTC, date_declaree
from .liens import ANCRE, EXTERNE, HORS_CORPUS, INTERBASE, INTERNE, REFUSE, Cible, Lien, extraire
from .profil import Profil, charger_profil

INTERVALLE_VERIFICATION_S = 2.0
GIT_TIMEOUT_S = 60

#: Le chargeur compilé quand PyYAML en dispose ; c'est un chargeur *sûr*, comme
#: `safe_load` (§ 1.7 ne porte que sur la sérialisation, qui reste `safe_dump`).
CHARGEUR = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# Sortes de résolution, en plus d'EXTERNE, ANCRE et REFUSE, rendues telles quelles.
DOC = "doc"
REP = "rep"
CASSE = "casse"
BASE_ABSENTE = "base-absente"


@dataclass(frozen=True)
class Doc:
    chemin: str
    """Relatif au corpus, séparateur `/`."""
    titre: str
    frontmatter: Mapping
    """{} si le document n'en a pas, ou s'il est illisible (voir `fm_illisible`)."""
    fm_illisible: bool
    sections: tuple[Heading, ...]
    liens: tuple[Lien, ...]
    notes: frozenset[str]
    notes_definies: frozenset[str]
    taille: int
    mtime: float

    @property
    def type(self) -> str | None:
        valeur = self.frontmatter.get("type")
        return valeur.strip() or None if isinstance(valeur, str) else None

    @property
    def tags(self) -> tuple[str, ...]:
        valeur = self.frontmatter.get("tags")
        if isinstance(valeur, str):
            return (valeur,)
        if isinstance(valeur, list):
            return tuple(str(t) for t in valeur if t is not None)
        return ()

    @property
    def reserve(self) -> bool:
        """`index.md` ou `log.md` : un sommaire ou un journal, pas un concept (OKF § 3.1)."""
        return posixpath.basename(self.chemin) in RESERVED_NAMES

    @property
    def dossier(self) -> str:
        return posixpath.dirname(self.chemin)


@dataclass(frozen=True)
class BaseModele:
    base: Base
    profil: Profil
    docs: Mapping[str, Doc]
    repertoires: frozenset[str]
    """Tous les répertoires qui contiennent un document ; '' est la racine."""
    signature: tuple
    generation: int
    """Croît à chaque chargement, dans ce processus : sert de clé aux index dérivés."""

    @property
    def nom(self) -> str:
        return self.base.name

    def concepts(self) -> list[Doc]:
        """Les documents hors noms réservés, dans l'ordre des chemins."""
        return [d for d in self.docs.values() if not d.reserve]

    def trouver(self, chemin: str, repertoire: bool = False) -> tuple[str, str] | None:
        """(DOC, chemin) ou (REP, chemin) désigné par une cible, ou None."""
        if not repertoire:
            if chemin in self.docs:
                return DOC, chemin
            # Identifiant OKF : le chemin du concept sans son extension.
            if f"{chemin}.md" in self.docs:
                return DOC, f"{chemin}.md"
        if chemin in self.repertoires:
            return REP, chemin
        return None

    def index_de(self, repertoire: str) -> str | None:
        chemin = posixpath.join(repertoire, "index.md") if repertoire else "index.md"
        return chemin if chemin in self.docs else None

    def contenu(self, dossier: str) -> tuple[list[str], list[Doc]]:
        """(sous-répertoires directs, documents directs) d'un répertoire."""
        prefixe = f"{dossier}/" if dossier else ""
        sous = sorted(
            r for r in self.repertoires
            if r.startswith(prefixe) and r != dossier and "/" not in r[len(prefixe):]
        )
        docs = [d for d in self.docs.values() if d.dossier == dossier]
        return sous, docs


@dataclass(frozen=True)
class Resolution:
    sorte: str
    base: str | None = None
    chemin: str | None = None
    ancre: str | None = None


def resoudre(cible: Cible, modeles: Mapping[str, BaseModele]) -> Resolution:
    """Confronte une cible aux bases présentes (§ 3.6)."""
    if cible.sorte in (EXTERNE, ANCRE, REFUSE):
        return Resolution(cible.sorte, ancre=cible.ancre)
    if cible.sorte == HORS_CORPUS:
        return Resolution(CASSE, base=cible.base)
    modele = modeles.get(cible.base or "")
    if modele is None:
        return Resolution(BASE_ABSENTE, cible.base, cible.chemin)
    trouve = modele.trouver(cible.chemin or "", cible.repertoire)
    if trouve is None:
        return Resolution(CASSE, cible.base, cible.chemin)
    return Resolution(trouve[0], cible.base, trouve[1], cible.ancre)


@dataclass(frozen=True)
class Entrant:
    """Un lien qui arrive sur un document : depuis quel document, par quel lien."""

    base: str
    chemin: str
    lien: Lien


@dataclass(frozen=True)
class Fraicheur:
    date: dt.datetime
    origine: str


@dataclass(frozen=True)
class Commit:
    date: dt.datetime
    court: str
    sujet: str


@dataclass
class _Entree:
    modele: BaseModele
    verifie_a: float


class Lecteur:
    """Le moteur de lecture d'un processus : modèles des bases, à jour du disque.

    Sûr entre fils d'exécution : l'interface web sert ses pages en parallèle,
    une instance MCP ses outils. Chaque modèle est immuable ; une base qui
    change est rechargée sous son propre verrou et son modèle remplacé d'un bloc.
    """

    _instances: "weakref.WeakKeyDictionary[Registry, Lecteur]" = weakref.WeakKeyDictionary()
    _verrou_instances = threading.Lock()

    @classmethod
    def de(cls, registry: Registry) -> "Lecteur":
        """Le lecteur attaché à un registre — un par registre, donc par processus.

        Les outils MCP ne reçoivent que le registre (`run(registry, arguments)`) :
        les attacher ainsi garde leur interface commune, et un test qui fabrique
        son propre registre obtient son propre lecteur.
        """
        with cls._verrou_instances:
            lecteur = cls._instances.get(registry)
            if lecteur is None:
                lecteur = cls._instances[registry] = cls(registry)
            return lecteur

    def __init__(self, registry: Registry, intervalle: float = INTERVALLE_VERIFICATION_S) -> None:
        self.registry = registry
        self.intervalle = intervalle
        self._verrou = threading.Lock()
        self._verrous_base: dict[str, threading.Lock] = {}
        self._entrees: dict[str, _Entree] = {}
        self._generation = 0
        self._entrants: tuple[tuple, Mapping] | None = None
        self._dates: dict[str, tuple[tuple, Mapping[str, dt.datetime]]] = {}

    # --- modèles --------------------------------------------------------------

    def modele(self, nom: str) -> BaseModele:
        """Le modèle à jour d'une base. Lève `UNKNOWN_BASE` comme `Registry.get`."""
        base = self.registry.get(nom)
        with self._verrou:
            verrou = self._verrous_base.setdefault(nom, threading.Lock())
        with verrou:
            entree = self._entrees.get(nom)
            if (
                entree is not None
                and entree.modele.base is base
                and time.monotonic() - entree.verifie_a < self.intervalle
            ):
                return entree.modele
            signature, chemins = _signature(base)
            if entree is not None and entree.modele.signature == signature:
                modele = entree.modele
                if modele.base is not base:
                    # Re-scan sans changement de chemins : le manifeste a pu
                    # changer de titre ou de description, le corpus non.
                    modele = replace(modele, base=base)
            else:
                modele = self._charger(base, signature, chemins)
            self._entrees[nom] = _Entree(modele, time.monotonic())
            return modele

    def modeles(self, noms: list[str] | None = None) -> dict[str, BaseModele]:
        """Modèles des bases nommées, ou de toutes les bases du registre."""
        presents = set(self.registry.names())
        with self._verrou:
            for nom in [n for n in self._entrees if n not in presents]:
                del self._entrees[nom]
        return {nom: self.modele(nom) for nom in (noms if noms is not None else sorted(presents))}

    def _charger(self, base: Base, signature: tuple, chemins: list[Path]) -> BaseModele:
        debut = time.perf_counter()
        docs: dict[str, Doc] = {}
        repertoires = {""}
        for path in chemins:
            rel = base.rel_path(path)
            doc = _charger_doc(base.name, rel, path)
            if doc is None:
                continue
            docs[rel] = doc
            dossier = doc.dossier
            while dossier and dossier not in repertoires:
                repertoires.add(dossier)
                dossier = posixpath.dirname(dossier)
        # Dans l'ordre des chemins, que tout ce qui liste reprend tel quel.
        docs = dict(sorted(docs.items()))
        profil = charger_profil(base.manifest.frontmatter_schema)
        with self._verrou:
            self._generation += 1
            generation = self._generation
        hublog.info(
            f"lecture : {base.name} chargée — {len(docs)} document(s) en "
            f"{time.perf_counter() - debut:.2f} s"
        )
        for avertissement in profil.avertissements:
            hublog.warning(f"[{base.name}] profil : {avertissement}")
        return BaseModele(
            base, profil, MappingProxyType(docs), frozenset(repertoires), signature, generation
        )

    # --- liens entrants -------------------------------------------------------

    def entrants(self, nom: str, chemin: str) -> tuple[Entrant, ...]:
        """Liens qui arrivent sur un document, depuis toutes les bases du registre.

        Le premier appel charge toutes les bases ; l'index est ensuite reconstruit
        seulement quand l'une d'elles a été rechargée. Filtrer selon ce qu'un
        lecteur a le droit de voir est l'affaire de l'appelant (§ 5 bis.4).
        """
        return self._index_entrants().get((nom, chemin), ())

    def index_entrants(self) -> Mapping[tuple[str, str], tuple[Entrant, ...]]:
        """(base, chemin) → liens qui y arrivent, pour toutes les bases."""
        return self._index_entrants()

    def _index_entrants(self) -> Mapping[tuple[str, str], tuple[Entrant, ...]]:
        modeles = self.modeles()
        cle = tuple((n, m.generation) for n, m in modeles.items())
        with self._verrou:
            if self._entrants is not None and self._entrants[0] == cle:
                return self._entrants[1]
        index: dict[tuple[str, str], list[Entrant]] = defaultdict(list)
        for modele in modeles.values():
            for doc in modele.docs.values():
                for lien in doc.liens:
                    if lien.cible.sorte not in (INTERNE, INTERBASE):
                        continue
                    cible = cible_document(resoudre(lien.cible, modeles), modeles)
                    if cible is None or cible == (modele.nom, doc.chemin):
                        continue
                    index[cible].append(Entrant(modele.nom, doc.chemin, lien))
        fige = MappingProxyType({k: tuple(v) for k, v in index.items()})
        with self._verrou:
            self._entrants = (cle, fige)
        return fige

    # --- dates ----------------------------------------------------------------

    def dates_commit(self, nom: str) -> Mapping[str, dt.datetime]:
        """Date du dernier commit de chaque document suivi, pour le HEAD courant."""
        base = self.registry.get(nom)
        head = _git_head(base.root)
        cle = (str(base.root), str(base.corpus_dir), head)
        with self._verrou:
            deja = self._dates.get(nom)
            if deja is not None and deja[0] == cle:
                return deja[1]
        dates = MappingProxyType(_dates_git(base)) if head else MappingProxyType({})
        with self._verrou:
            self._dates[nom] = (cle, dates)
        return dates

    def date_commit(self, nom: str, chemin: str) -> dt.datetime | None:
        """Date du dernier commit d'un document : la table si elle est à jour,
        sinon un `git log -1` sur ce seul chemin — la page d'un document ne doit
        pas payer l'historique de toute la base."""
        base = self.registry.get(nom)
        head = _git_head(base.root)
        with self._verrou:
            deja = self._dates.get(nom)
        if deja is not None and deja[0] == (str(base.root), str(base.corpus_dir), head):
            return deja[1].get(chemin)
        if head is None:
            return None
        rel = (base.corpus_dir / chemin).relative_to(base.root).as_posix()
        sortie = _git(base.root, "log", "-1", "--format=%cI", "--", rel)
        return _date_git(sortie.strip()) if sortie else None

    def historique(self, nom: str, nombre: int = 30) -> list[Commit]:
        """Les derniers commits du dépôt de la base, du plus récent au plus ancien."""
        base = self.registry.get(nom)
        sortie = _git(base.root, "log", f"-n{nombre}", "--format=%cI%x1f%h%x1f%s")
        commits = []
        for ligne in (sortie or "").splitlines():
            date, _, reste = ligne.partition("\x1f")
            court, _, sujet = reste.partition("\x1f")
            instant = _date_git(date)
            if instant is not None:
                commits.append(Commit(instant, court, sujet))
        return commits

    def fraicheur(self, modele: BaseModele, doc: Doc, dates: Mapping | None = None) -> Fraicheur:
        """Date de fraîcheur d'un document (§ 5 bis.1), toujours disponible.

        Dans l'ordre : la plus récente vérification, `generated.at`, la plus
        récente source, le dernier commit, la date du fichier. `dates` : la table
        de `dates_commit`, quand l'appelant traite beaucoup de documents.
        """
        declaree = date_declaree(doc.frontmatter)
        if declaree is not None:
            return Fraicheur(*declaree)
        commit = dates.get(doc.chemin) if dates is not None else self.date_commit(modele.nom, doc.chemin)
        if commit is not None:
            return Fraicheur(commit, COMMIT)
        return Fraicheur(dt.datetime.fromtimestamp(doc.mtime, UTC), FICHIER)


def cible_document(resolution: Resolution, modeles: Mapping[str, BaseModele]) -> tuple[str, str] | None:
    """(base, chemin du document) visé ; un répertoire vaut son `index.md`."""
    if resolution.sorte == DOC:
        return resolution.base, resolution.chemin
    if resolution.sorte == REP:
        index = modeles[resolution.base].index_de(resolution.chemin or "")
        return (resolution.base, index) if index else None
    return None


# --- chargement d'un document -------------------------------------------------


def _charger_doc(base: str, rel: str, path: Path) -> Doc | None:
    try:
        texte = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        # Disparu entre le parcours et la lecture : la signature suivante le verra.
        return None
    parse = parse_document(texte, loader=CHARGEUR)
    headings = parse_headings(parse.body)
    extraction = extraire(parse.body, headings, base, rel)
    fm = parse.frontmatter or {}
    titre = fm.get("title")
    if not (isinstance(titre, str) and titre.strip()):
        titre = headings[0].text if headings else posixpath.splitext(posixpath.basename(rel))[0]
    return Doc(
        chemin=rel,
        titre=str(titre).strip(),
        frontmatter=MappingProxyType(dict(fm)),
        fm_illisible=parse.frontmatter_raw is not None and parse.frontmatter is None,
        sections=tuple(headings),
        liens=extraction.liens,
        notes=extraction.notes,
        notes_definies=extraction.notes_definies,
        taille=stat.st_size,
        mtime=stat.st_mtime,
    )


def _signature(base: Base) -> tuple[tuple, list[Path]]:
    chemins = base.iter_documents()
    items = []
    for path in chemins:
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))
    schema = base.manifest.frontmatter_schema
    etat_schema = None
    if schema is not None:
        try:
            stat = schema.stat()
            etat_schema = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass
    return (str(base.root), str(base.corpus_dir), str(schema), etat_schema, tuple(items)), chemins


# --- git ----------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    """Sortie d'une commande git en lecture, ou None si elle échoue.

    Lecture seule : rien ici n'écrit dans un dépôt, pas même un verrou d'index
    (`--no-optional-locks`).
    """
    try:
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "-c", "core.quotepath=off", *args],
            capture_output=True, timeout=GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        hublog.warning(f"lecture : git {args[0]} impossible dans {root} — {exc!r}")
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def _git_head(root: Path) -> str | None:
    sortie = _git(root, "rev-parse", "--verify", "-q", "HEAD")
    return sortie.strip() or None if sortie else None


def _date_git(texte: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(texte).astimezone(UTC)
    except ValueError:
        return None


def _dates_git(base: Base) -> dict[str, dt.datetime]:
    """{chemin relatif au corpus: date du dernier commit}, en un seul `git log`."""
    corpus = base.corpus_dir.relative_to(base.root).as_posix()
    sortie = _git(
        base.root, "log", "--format=%x1e%cI", "--name-only", "--no-renames", "--", corpus
    )
    if not sortie:
        return {}
    prefixe = f"{corpus}/"
    dates: dict[str, dt.datetime] = {}
    for bloc in sortie.split("\x1e"):
        lignes = [l for l in bloc.split("\n") if l]
        if not lignes:
            continue
        date = _date_git(lignes[0])
        if date is None:
            continue
        for chemin in lignes[1:]:
            if chemin.startswith(prefixe):
                # Le plus récent d'abord : la première date vue est la bonne.
                dates.setdefault(chemin[len(prefixe):], date)
    return dates
