"""Serveur MCP noyau (§ 5).

Une instance par client connecté (§ 4.4) : le registre porté par cette instance
est un cache local, jamais une autorité.
"""

from __future__ import annotations

import threading
import time
from typing import Awaitable, Callable

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.context import ServerRequestContext

from . import hublog
from .config import HubConfig
from .errors import IO_ERROR, UNKNOWN_BASE, ToolError
from .registry import Registry
from .tools import (
    governance_tool,
    list_tool,
    propose_tool,
    read_tool,
    rescan_tool,
    search_tool,
)

SERVER_NAME = "okf-hub"

#: Cooldown du re-scan silencieux déclenché par UNKNOWN_BASE (§ 4.4.c).
SILENT_RESCAN_COOLDOWN_S = 5.0


class ToolSpec:
    def __init__(self, name: str, module, schema: dict) -> None:
        self.name = name
        self.module = module
        self.schema = schema

    def describe(self, registry: Registry) -> str:
        return self.module.description(registry)

    def run(self, registry: Registry, arguments: dict) -> str:
        return self.module.run(registry, arguments)


TOOLS: list[ToolSpec] = [
    ToolSpec("kb_list", list_tool, list_tool.SCHEMA),
    ToolSpec("kb_search", search_tool, search_tool.SCHEMA),
    ToolSpec("kb_read", read_tool, read_tool.SCHEMA),
    ToolSpec("kb_governance", governance_tool, governance_tool.SCHEMA),
    ToolSpec("kb_propose", propose_tool, propose_tool.SCHEMA),
    ToolSpec("kb_hub_rescan", rescan_tool, rescan_tool.SCHEMA),
]

_BY_NAME = {spec.name: spec for spec in TOOLS}


class HubServer:
    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self.registry = Registry(config)
        self._lock = threading.Lock()
        self._last_silent_rescan = 0.0
        self.registry.scan()

    # --- re-scan silencieux (§ 4.4.c) ---------------------------------------

    def _silent_rescan(self) -> tuple[bool, bool]:
        """Rescan sous cooldown. Retourne (rescan effectué, liste changée)."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_silent_rescan < SILENT_RESCAN_COOLDOWN_S:
                return False, False
            self._last_silent_rescan = now
        hublog.info("re-scan silencieux déclenché par UNKNOWN_BASE")
        report = self.registry.scan()
        return True, report.changed

    # --- handlers MCP -------------------------------------------------------

    async def on_list_tools(
        self, ctx: ServerRequestContext, params
    ) -> types.ListToolsResult:
        # Les descriptions sont recalculées à chaque appel : elles énumèrent les
        # bases connues (§ 5.1) et suivent donc l'état courant du registre.
        tools = [
            types.Tool(
                name=spec.name,
                description=spec.describe(self.registry),
                input_schema=spec.schema,
            )
            for spec in TOOLS
        ]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(
        self, ctx: ServerRequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        spec = _BY_NAME.get(params.name)
        if spec is None:
            return _error(ToolError("NOT_FOUND", f"outil inconnu : {params.name}"))
        arguments = dict(params.arguments or {})

        notify_changed = False
        try:
            text = await anyio.to_thread.run_sync(
                lambda: spec.run(self.registry, arguments)
            )
        except ToolError as exc:
            if exc.code == UNKNOWN_BASE:
                # Atténuation § 4.4.c : la base a peut-être été importée depuis
                # le démarrage de cette instance. On re-scanne puis on retente
                # une fois avant de rendre l'erreur.
                rescanned, changed = await anyio.to_thread.run_sync(self._silent_rescan)
                notify_changed = changed
                if rescanned:
                    try:
                        text = await anyio.to_thread.run_sync(
                            lambda: spec.run(self.registry, arguments)
                        )
                    except ToolError as retry_exc:
                        await _maybe_notify(ctx, notify_changed)
                        return _error(retry_exc)
                    else:
                        await _maybe_notify(ctx, notify_changed)
                        return _ok(text)
            await _maybe_notify(ctx, notify_changed)
            hublog.info(f"{spec.name} → {exc.code}: {exc.message}")
            return _error(exc)
        except Exception as exc:  # défaillance réelle (§ 5, IO_ERROR)
            hublog.error(f"{spec.name} : défaillance non prévue — {exc!r}")
            return _error(ToolError(IO_ERROR, f"{type(exc).__name__}: {exc}"))

        if spec.name == "kb_hub_rescan" and self.registry.last_report.changed:
            notify_changed = True
        await _maybe_notify(ctx, notify_changed)
        return _ok(text)

    def build(self) -> Server:
        return Server(
            SERVER_NAME,
            version="0.1.0",
            instructions=(
                "Ce hub expose des bases de connaissance markdown versionnées en "
                "git. Lecture : kb_list, kb_search, kb_read, kb_governance. "
                "Contribution : kb_propose dépose une proposition dans "
                "proposals/pending/ — le corpus n'est jamais modifié directement, "
                "seul le rôle gestionnaire intègre."
            ),
            on_list_tools=self.on_list_tools,
            on_call_tool=self.on_call_tool,
        )


async def _maybe_notify(ctx: ServerRequestContext, changed: bool) -> None:
    """Émet tools/list_changed si la liste des bases a changé (§ 4.2).

    Certains clients ignorent cette notification : la correction fonctionnelle
    ne doit jamais en dépendre — le re-scan sur UNKNOWN_BASE (§ 4.4.c) couvre
    le besoin.
    """
    if not changed:
        return
    try:
        await ctx.session.send_tool_list_changed()
    except Exception as exc:
        hublog.warning(f"notification tools/list_changed non délivrée : {exc!r}")


def _ok(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def _error(exc: ToolError) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=exc.to_text())], is_error=True
    )
