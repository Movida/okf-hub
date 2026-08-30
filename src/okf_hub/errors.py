"""Codes et exceptions d'erreur du noyau (§ 5, convention transverse « erreurs »).

Toute erreur métier remonte sous forme de `ToolError`. Le serveur la convertit
en résultat MCP `isError: true` dont le texte suit `ERROR: <code>: <message>`.
Les erreurs de protocole (paramètre manquant ou mal typé) restent des erreurs
JSON-RPC gérées par le SDK et ne passent donc pas par ici.
"""

from __future__ import annotations

UNKNOWN_BASE = "UNKNOWN_BASE"
NOT_FOUND = "NOT_FOUND"
INVALID_INPUT = "INVALID_INPUT"
BASE_BUSY = "BASE_BUSY"
IO_ERROR = "IO_ERROR"


class ToolError(Exception):
    """Erreur métier d'un outil kb_*."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def to_text(self) -> str:
        return f"ERROR: {self.code}: {self.message}"


def unknown_base(base: str, known: list[str]) -> ToolError:
    """§ 5 : le message d'UNKNOWN_BASE inclut la liste des bases valides."""
    if known:
        listing = ", ".join(known)
        msg = f"base '{base}' inconnue. Bases disponibles : {listing}"
    else:
        msg = (
            f"base '{base}' inconnue. Aucune base valide n'est enregistrée "
            f"(vérifiez bases-dir et lancez kb_hub_rescan)."
        )
    return ToolError(UNKNOWN_BASE, msg)
