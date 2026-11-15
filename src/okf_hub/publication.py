"""Publier une base partagée vers sa réplique (spec rév. 4.8, § 4.6.c).

    okf-hub publish <base> [--dry-run]

Dans l'ordre, et chaque refus arrête tout avant le premier envoi :

1. la base figure dans `shared-bases` (`hub-config.local.yaml`) ;
2. les commits que le remote `publish-remote` ne connaît pas passent la garde de
   confidentialité du § 4.1 — contenus ajoutés, chemins, messages, auteurs —,
   avec pour termes privés ceux du § 4.1 moins les noms des bases partagées ;
3. les mêmes contenus passent le détecteur de secrets, plus `secret-patterns` ;
4. `git push` de la branche courante, en avance rapide seulement.

Le refus nomme le commit, le fichier, la ligne et la règle, jamais la valeur.
C'est la seule synchronisation outillée par le hub, et elle ne va que dans un
sens (§ 4.5) : rien n'est jamais tiré de la réplique.

La base est lue, jamais écrite : `git fetch` ne touche qu'aux références du
remote, et `git push` qu'au dépôt distant. Aucun verrou de base n'est donc
pris (§ 4.4.b) — un `kb_propose` concurrent ajoute au plus un commit que la
poussée n'emporte pas, et qui partira à la suivante.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import confidentialite, hublog
from .config import HubConfig
from .registry import Registry

#: Règles du détecteur : (nom affiché, expression). Les formats de jetons
#: connus d'abord ; l'affectation à un nom de secret en dernier, la plus large.
REGLES_SECRETS: tuple[tuple[str, str], ...] = (
    ("clé privée", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    ("jeton GitHub", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    ("clé AWS", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("clé d'API Anthropic", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("clé d'API OpenAI", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    ("clé d'API Google", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("jeton Slack", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    ("jeton de compte de service 1Password", r"\bops_[A-Za-z0-9_-]{40,}"),
    ("jeton JWT", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)

#: Une valeur affectée à un nom de secret (`password: …`, `API_KEY=…`).
AFFECTATION = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret|auth)(?![A-Za-z0-9])[\"']?\s*[:=]\s*[\"']?"
    r"(?P<valeur>[^\s\"'#,;]+)"
)


def _ressemble_a_un_secret(valeur: str) -> bool:
    """Une valeur affectée n'est un secret que si elle en a l'allure : au moins
    huit caractères, lettres et chiffres mêlés. Une référence (`op://…`, `${…}`,
    `$VAR`) ou un gabarit (`<…>`) n'en est pas un : c'est la forme qu'on veut
    voir dans une base."""
    if len(valeur) < 8 or valeur.startswith(("op://", "$", "<", "{{")):
        return False
    return bool(re.search(r"[A-Za-z]", valeur)) and bool(re.search(r"[0-9]", valeur))


class PublicationRefusee(Exception):
    """Un refus : rien n'a été envoyé."""

    def __init__(self, motif: str, details: list[str] | None = None) -> None:
        super().__init__(motif)
        self.motif = motif
        self.details = details or []


@dataclass
class Bilan:
    base: str
    remote: str
    branche: str
    commits: list[str] = field(default_factory=list)
    pousse: bool = False


@dataclass(frozen=True)
class Ligne:
    commit: str
    ou: str
    """`fichier:ligne`, ou `(auteur, message)`."""
    texte: str


def _git(racine: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(racine), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def lignes_ajoutees(diff: str, commit: str) -> list[Ligne]:
    """Chaque ligne ajoutée d'un diff, numérotée dans le fichier d'arrivée,
    plus le chemin de chaque fichier touché (un nom de fichier publie aussi)."""
    out: list[Ligne] = []
    fichier, numero = "?", 0
    for ligne in diff.splitlines():
        if ligne.startswith("+++ "):
            fichier = ligne[6:] if ligne.startswith("+++ b/") else ligne[4:]
            out.append(Ligne(commit, f"{fichier} (chemin)", fichier))
        elif ligne.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", ligne)
            numero = int(m.group(1)) if m else 0
        elif ligne.startswith("+"):
            out.append(Ligne(commit, f"{fichier}:{numero}", ligne[1:]))
            numero += 1
        elif not ligne.startswith("-") and not ligne.startswith("\\"):
            numero += 1
    return out


def sortants(racine: Path, remote: str) -> list[str]:
    """Commits de HEAD qu'aucune référence du remote ne connaît, du plus ancien
    au plus récent."""
    res = _git(racine, "rev-list", "--reverse", "HEAD", "--not", f"--remotes={remote}")
    return res.stdout.split()


def elements(racine: Path, commits: list[str]) -> list[Ligne]:
    out: list[Ligne] = []
    for sha in commits:
        court = sha[:12]
        entete = _git(racine, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha).stdout
        out += [Ligne(court, "(auteur, message)", l) for l in entete.splitlines()]
        # `--root` : le premier commit d'une base se compare à l'arbre vide.
        diff = _git(racine, "show", "--root", "--format=", "-p", "--no-color", "--no-ext-diff",
                    "--no-renames", sha).stdout
        out += lignes_ajoutees(diff, court)
    return out


def secrets(lignes: list[Ligne], motifs: tuple[str, ...]) -> list[str]:
    regles = [(nom, re.compile(rx)) for nom, rx in REGLES_SECRETS]
    regles += [(f"secret-patterns « {rx} »", re.compile(rx)) for rx in motifs]
    trouves: list[str] = []
    for l in lignes:
        for nom, rx in regles:
            if rx.search(l.texte):
                trouves.append(f"{l.commit} {l.ou} : {nom}")
        for m in AFFECTATION.finditer(l.texte):
            if _ressemble_a_un_secret(m.group("valeur")):
                trouves.append(f"{l.commit} {l.ou} : valeur affectée à un nom de secret")
                break
    return list(dict.fromkeys(trouves))


def termes(registry: Registry, lignes: list[Ligne]) -> list[str]:
    config = registry.config
    rx = confidentialite.motif(confidentialite.termes_prives(registry, sauf=config.shared_bases))
    trouves = []
    for l in lignes:
        for terme in confidentialite.trouver(rx, l.texte):
            trouves.append(f"{l.commit} {l.ou} : terme privé « {terme} »")
    return list(dict.fromkeys(trouves))


def publier(config: HubConfig, nom: str, *, dry_run: bool = False) -> Bilan:
    """Publie la base `nom`, ou lève `PublicationRefusee` sans rien envoyer."""
    # Toutes les bases, périmètre ignoré : la garde doit connaître les privées.
    registry = Registry(config.with_scope(None))
    registry.scan()
    if nom not in config.shared_bases:
        raise PublicationRefusee(
            f"'{nom}' n'est pas une base partagée : seule une base de `shared-bases` "
            f"(hub-config.local.yaml) se publie. Partager une base est une décision "
            f"de l'opérateur, prise là, base par base."
        )
    base = registry.bases.get(nom)
    if base is None:
        raise PublicationRefusee(f"base '{nom}' introuvable dans {config.bases_dir}")
    racine = base.root
    remote = config.publish_remote

    if _git(racine, "remote", "get-url", remote, check=False).returncode != 0:
        raise PublicationRefusee(
            f"la base '{nom}' n'a pas de remote '{remote}' : l'ajouter une fois, "
            f"`git -C {racine} remote add {remote} <url de la réplique>`."
        )
    branche = _git(racine, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    if not branche:
        raise PublicationRefusee(f"la base '{nom}' n'est sur aucune branche (HEAD détachée)")

    recu = _git(racine, "fetch", "--quiet", "--no-tags", remote, check=False)
    if recu.returncode != 0:
        raise PublicationRefusee(
            f"remote '{remote}' injoignable : ce qui part ne peut pas être établi.",
            [l for l in recu.stderr.splitlines() if l.strip()][:5],
        )
    distante = f"refs/remotes/{remote}/{branche}"
    if _git(racine, "rev-parse", "--verify", "--quiet", distante, check=False).returncode == 0:
        if _git(racine, "merge-base", "--is-ancestor", distante, "HEAD", check=False).returncode != 0:
            raise PublicationRefusee(
                f"la réplique a sur '{branche}' des commits que la base n'a pas : "
                f"divergence, jamais forcée. La source est la base ; examiner la "
                f"réplique avant tout."
            )

    bilan = Bilan(base=nom, remote=remote, branche=branche, commits=sortants(racine, remote))
    if not bilan.commits:
        return bilan

    lignes = elements(racine, bilan.commits)
    fuites = termes(registry, lignes)
    if fuites:
        raise PublicationRefusee(
            "des termes privés (bases privées, `private-terms`) partiraient vers la "
            "réplique. Les retirer de la base par une proposition, puis republier.",
            fuites,
        )
    trouves = secrets(lignes, config.secret_patterns)
    if trouves:
        raise PublicationRefusee(
            "le détecteur voit une valeur secrète. La retirer de la base (une "
            "référence `op://…` à la place), puis republier ; un secret déjà "
            "commité reste dans l'historique : le révoquer.",
            trouves,
        )
    if dry_run:
        return bilan

    pousse = _git(racine, "push", "--quiet", remote,
                  f"HEAD:refs/heads/{branche}", check=False)
    if pousse.returncode != 0:
        raise PublicationRefusee(
            f"git push vers '{remote}' refusé.",
            [l for l in pousse.stderr.splitlines() if l.strip()][:5],
        )
    bilan.pousse = True
    hublog.info(f"publication : '{nom}' → {remote}/{branche}, {len(bilan.commits)} commit(s)")
    return bilan


def main(config: HubConfig, nom: str, *, dry_run: bool = False) -> int:
    import sys

    try:
        bilan = publier(config, nom, dry_run=dry_run)
    except PublicationRefusee as exc:
        hublog.warning(f"publication de '{nom}' refusée : {exc.motif}")
        print(f"okf-hub publish : refusé, rien n'est parti. {exc.motif}", file=sys.stderr)
        for d in exc.details[:30]:
            print(f"  {d}", file=sys.stderr)
        if len(exc.details) > 30:
            print(f"  … et {len(exc.details) - 30} autre(s)", file=sys.stderr)
        return 1
    if not bilan.commits:
        print(f"'{nom}' : rien à publier, la réplique a déjà tout.")
    elif dry_run:
        print(f"'{nom}' : {len(bilan.commits)} commit(s) passeraient les gardes (rien envoyé).")
    else:
        print(f"'{nom}' : {len(bilan.commits)} commit(s) publié(s) vers {bilan.remote}/{bilan.branche}.")
    return 0
