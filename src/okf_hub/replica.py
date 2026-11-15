"""La réplique : des bases partagées servies en lecture, à distance (rév. 4.8, § 4.6.d).

    okf-hub replica                       # sert MCP sur HTTP
    okf-hub replica receive <base>        # depuis le hook post-receive du dépôt de la base

**Ce qu'elle sert.** MCP sur HTTP (*streamable HTTP*, transport du protocole),
sur `/mcp`, et rien d'autre. Les outils de lecture seulement : ni `kb_propose`,
ni `kb_hub_rescan`. Elle n'écrit rien, ne prend aucun verrou.

**À qui.** L'authentification est déléguée à un proxy inverse, comme le mode
équipe de l'interface web (§ 5 bis.4), dont elle reprend la configuration et le
code (`web.acces`) : l'en-tête d'identité n'est cru que d'un pair de
`trusted-proxies`, toute autre connexion est refusée avant MCP. Chaque appel voit
un registre réduit aux bases que `visibility` ouvre à l'appelant : une base
fermée et une base absente y sont indiscernables, dans les outils, leurs
descriptions et les références entre bases.

**Quoi.** Une instance limitée aux bases partagées (`--scope shared`, § 4.6.b) :
une base absente de la `shared-bases` de cette machine n'est pas chargée, même
présente dans `bases-dir` — la double barrière de niveau.

**Les copies.** Chaque base est un dépôt reçu par `git push` (`okf-hub publish`,
§ 4.6.c). La réplique ne le lit jamais en place : `receive` en tire un clone neuf
dans `bases-dir/.copies/`, puis bascule le lien `bases-dir/<base>` vers lui par
`rename()` atomique. La copie précédente reste, pour une lecture en cours ; les
plus anciennes sont supprimées. Le premier appel qui suit une bascule la voit,
par le re-scan silencieux du § 4.4.c sous le déclencheur `replica`.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import hublog
from .config import SCOPE_SHARED, HubConfig
from starlette.datastructures import Headers

from .manifest import ManifestError, load_manifest
from .registry import Registry
from .server import TOOLS, HubServer, ToolSpec
from .tools import list_tool
from .web.acces import AccesRefuse, ConfigWebInvalide, WebConfig, bases_visibles, visiteur_de

DEFAULT_PORT = 8078
CHEMIN_MCP = "/mcp"
COPIES = ".copies"

#: Outils de la réplique : la lecture du § 5, rien qui écrive ni ne commande.
OUTILS_LECTURE = ("kb_list", "kb_search", "kb_read", "kb_find", "kb_governance", "kb_proposal_status")


class ConfigReplicaInvalide(ValueError):
    pass


@dataclass(frozen=True)
class ReplicaConfig:
    acces: WebConfig
    propose_hint: str | None = None

    @staticmethod
    def depuis(brut, *, host: str | None = None, port: int | None = None) -> "ReplicaConfig":
        """Valide la clé `replica` : celle de `web` (§ 4.1), plus `propose-hint`.
        L'identité est obligatoire : une réplique ne sert que des clients distants."""
        if brut is None:
            brut = {}
        if not isinstance(brut, dict):
            raise ConfigReplicaInvalide("replica : correspondance attendue")
        brut = dict(brut)
        hint = brut.pop("propose-hint", None)
        if hint is not None and (not isinstance(hint, str) or not hint.strip()):
            raise ConfigReplicaInvalide("replica.propose-hint : texte attendu")
        if not brut.get("identity-header"):
            raise ConfigReplicaInvalide(
                "replica.identity-header est requis : la réplique sert des clients "
                "distants, à travers un proxy inverse qui transmet leur identité."
            )
        try:
            acces = WebConfig.depuis(
                brut, host=host, port=port if port is not None else brut.get("port", DEFAULT_PORT)
            )
        except ConfigWebInvalide as exc:
            raise ConfigReplicaInvalide(str(exc).replace("web.", "replica.").replace("web :", "replica :")) from None
        return ReplicaConfig(acces=acces, propose_hint=hint.strip() if hint else None)


# --- les copies ---------------------------------------------------------------


def date_copie(lien: Path) -> dt.datetime | None:
    """La date de la bascule : celle du lien lui-même, posé par `rename()`."""
    try:
        return dt.datetime.fromtimestamp(lien.lstat().st_mtime, tz=dt.timezone.utc)
    except OSError:
        return None


def _signature(bases_dir: Path) -> tuple:
    """Ce qui change à chaque bascule : la cible de chaque lien. Un `readlink`
    par base, rien de plus à chaque appel."""
    if not bases_dir.is_dir():
        return ()
    sig = []
    for entree in sorted(bases_dir.iterdir(), key=lambda p: p.name):
        if entree.name.startswith("."):
            continue
        try:
            sig.append((entree.name, os.readlink(entree) if entree.is_symlink() else None))
        except OSError:
            continue
    return tuple(sig)


class ReceptionRefusee(Exception):
    pass


def recevoir(config: HubConfig, nom: str, source: Path, *, garder: int = 2) -> Path:
    """Tire une copie neuve de `source` et bascule `bases-dir/<nom>` vers elle."""
    if nom not in config.shared_bases:
        raise ReceptionRefusee(
            f"'{nom}' n'est pas dans la shared-bases de cette machine : la réplique "
            f"ne charge que les bases qu'elle-même déclare partagées."
        )
    bases_dir = config.bases_dir
    lien = bases_dir / nom
    if lien.exists() and not lien.is_symlink():
        raise ReceptionRefusee(
            f"{lien} est un répertoire, pas un lien vers une copie : la réplique ne "
            f"remplace pas ce qu'elle n'a pas posé."
        )
    copies = bases_dir / COPIES
    copies.mkdir(parents=True, exist_ok=True)
    horodatage = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    neuve = copies / f"{nom}-{horodatage}"
    clone = subprocess.run(
        ["git", "clone", "--quiet", "--no-local", str(source), str(neuve)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if clone.returncode != 0:
        shutil.rmtree(neuve, ignore_errors=True)
        raise ReceptionRefusee(f"clone de {source} impossible : {clone.stderr.strip()}")
    try:
        manifeste = load_manifest(neuve)
    except (ManifestError, OSError) as exc:
        shutil.rmtree(neuve, ignore_errors=True)
        raise ReceptionRefusee(f"la copie reçue n'est pas un bundle valide : {exc}") from None
    if manifeste.name != nom:
        shutil.rmtree(neuve, ignore_errors=True)
        raise ReceptionRefusee(
            f"la copie reçue se nomme '{manifeste.name}', pas '{nom}' : refusée."
        )
    provisoire = bases_dir / f".{nom}.lien-{os.getpid()}"
    if provisoire.is_symlink():
        provisoire.unlink()
    os.symlink(os.path.join(COPIES, neuve.name), provisoire)
    os.replace(provisoire, lien)
    hublog.info(f"réplique : '{nom}' basculée vers {neuve.name}")
    _elaguer(copies, nom, garder)
    return neuve


def _elaguer(copies: Path, nom: str, garder: int) -> None:
    """Supprime les copies au-delà des `garder` plus récentes : la courante, et
    la précédente pour une lecture qui finirait dessus."""
    miennes = sorted(p for p in copies.iterdir() if p.is_dir() and p.name.startswith(f"{nom}-")
                     and p.name[len(nom) + 1:][:1].isdigit())
    for vieille in miennes[:-garder]:
        shutil.rmtree(vieille, ignore_errors=True)
        hublog.info(f"réplique : ancienne copie supprimée — {vieille.name}")


# --- le serveur ---------------------------------------------------------------


class _ListeReplique:
    """`kb_list` de la réplique : celui du hub, plus la date de chaque copie, pour
    que qui lit sache de quand date ce qu'il lit (§ 4.6.d)."""

    SCHEMA = list_tool.SCHEMA

    @staticmethod
    def description(registry: Registry) -> str:
        return list_tool.description(registry) + (
            "\n\nCe serveur est une réplique en lecture : chaque base y est la copie "
            "de sa dernière publication, dont la date est donnée."
        )

    @staticmethod
    def run(registry: Registry, arguments: dict) -> str:
        texte = list_tool.run(registry, arguments)
        lignes = []
        for base in registry.ordered():
            date = date_copie(registry.config.bases_dir / base.dir_name)
            quand = date.strftime("%Y-%m-%d %H:%M UTC") if date else "date inconnue"
            lignes.append(f"- {base.name} : copie du {quand}")
        if not lignes:
            return texte
        return texte + "\n\nCopies (réplique en lecture) :\n" + "\n".join(lignes)


def _outils() -> list[ToolSpec]:
    par_nom = {s.name: s for s in TOOLS}
    outils = [par_nom[n] for n in OUTILS_LECTURE]
    outils[0] = ToolSpec("kb_list", _ListeReplique, _ListeReplique.SCHEMA)
    return outils


class ReplicaServer(HubServer):
    outils = _outils()

    def __init__(self, config: HubConfig, replica: ReplicaConfig) -> None:
        self.replica = replica
        self._signature_copies: tuple = ()
        self._vues: dict[frozenset[str], Registry] = {}
        super().__init__(config.with_scope(SCOPE_SHARED))
        self._signature_copies = _signature(self.config.bases_dir)

    def _registre_pour(self, ctx) -> Registry:
        signature = _signature(self.config.bases_dir)
        if signature != self._signature_copies:
            rescanne, _ = self._silent_rescan("replica")
            if rescanne:
                self._signature_copies = signature
        requete = getattr(ctx, "request", None)
        try:
            visiteur = visiteur_de(
                self.replica.acces,
                requete.headers if requete is not None else {},
                requete.client.host if requete is not None and requete.client else None,
            )
        except AccesRefuse:
            # Le filtre HTTP refuse déjà ces connexions ; ceinture : rien de visible.
            return self._vue(frozenset())
        return self._vue(bases_visibles(self.replica.acces, visiteur, self.registry.names()))

    def _vue(self, noms: frozenset[str]) -> Registry:
        """Une vue par ensemble de bases visibles, gardée tant que le registre
        ne change pas : chaque vue a son moteur de lecture, qu'on ne recharge
        pas à chaque appel."""
        registre = self.registry
        cle = frozenset(noms)
        vue = self._vues.get(cle)
        if vue is None or any(vue.bases.get(n) is not registre.bases.get(n) for n in cle):
            vue = self._vues[cle] = registre.vue(cle)
        return vue

    def _instructions(self) -> str:
        texte = (
            "Ce serveur est la réplique en lecture d'un hub de bases de connaissance "
            "markdown : kb_list, kb_search, kb_read, kb_find, kb_governance, "
            "kb_proposal_status. Chaque base est la copie de sa dernière publication "
            "(kb_list en donne la date) ; aucune écriture n'est possible ici."
        )
        if self.replica.propose_hint:
            texte += "\n\n" + self.replica.propose_hint
        return texte


class _FiltreProxy:
    """Refuse, avant MCP, toute connexion qui ne vient pas d'un proxy de
    confiance ou qui n'a pas d'identité (§ 5 bis.4)."""

    def __init__(self, app, acces: WebConfig) -> None:
        self.app = app
        self.acces = acces

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            client = scope.get("client")
            try:
                # `Headers` : lecture insensible à la casse, comme HTTP.
                visiteur_de(self.acces, Headers(scope=scope), client[0] if client else None)
            except AccesRefuse as exc:
                corps = str(exc).encode("utf-8")
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": corps})
                return
        await self.app(scope, receive, send)


def application(server: ReplicaServer):
    """L'application ASGI : MCP sur `/mcp`, derrière le filtre de proxy."""
    app = server.build().streamable_http_app(
        streamable_http_path=CHEMIN_MCP, stateless_http=True, json_response=True,
        host=server.replica.acces.host,
    )
    return _FiltreProxy(app, server.replica.acces)


def main(config: HubConfig, argv: list[str], *, host: str | None, port: int | None) -> int:
    if argv and argv[0] == "receive":
        if len(argv) != 2:
            print("usage : okf-hub replica receive <base>", file=sys.stderr)
            return 2
        source = Path(os.environ.get("GIT_DIR") or os.getcwd()).resolve()
        try:
            neuve = recevoir(config, argv[1], source)
        except ReceptionRefusee as exc:
            hublog.warning(f"réplique : réception de '{argv[1]}' refusée — {exc}")
            print(f"okf-hub replica receive : refusé. {exc}", file=sys.stderr)
            return 1
        print(f"'{argv[1]}' : copie {neuve.name} en service.")
        return 0
    if argv:
        print(f"okf-hub replica : action inconnue '{argv[0]}'", file=sys.stderr)
        return 2

    import uvicorn

    try:
        replica = ReplicaConfig.depuis(_cle(config), host=host, port=port)
    except ConfigReplicaInvalide as exc:
        print(f"okf-hub replica : configuration invalide : {exc}", file=sys.stderr)
        return 2
    server = ReplicaServer(config, replica)
    hublog.info(
        f"réplique : {len(server.registry.names())} base(s) partagée(s) servie(s) sur "
        f"{replica.acces.host}:{replica.acces.port}{CHEMIN_MCP}"
    )
    try:
        uvicorn.run(application(server), host=replica.acces.host, port=replica.acces.port,
                    log_level="warning", proxy_headers=False)
    finally:
        server.stop()
    return 0


def _cle(config: HubConfig):
    return config.replica

