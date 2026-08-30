"""Plafonnement des sorties d'outils (§ 1.5, § 5, § 11.1).

Principe § 1.5 : « toute sortie d'outil potentiellement volumineuse est
plafonnée (~4 000 tokens, approximation caractères/4) avec troncature
signalée ». La question ouverte § 11.1 valide l'approximation caractères/4.
"""

from __future__ import annotations

import re

TOKEN_CAP = 4000
CHARS_PER_TOKEN = 4
CHAR_CAP = TOKEN_CAP * CHARS_PER_TOKEN


def estimate_tokens(text: str) -> int:
    """Approximation caractères/4 (§ 11.1)."""
    return len(text) // CHARS_PER_TOKEN


_WS_RUN = re.compile(r"\s+")


def normalize_inline(text: str) -> str:
    """Réduit retours à la ligne et suites d'espaces à un espace simple.

    Utilisé pour la `description` de manifeste (§ 3.3) avant injection dans les
    descriptions d'outils MCP.
    """
    return _WS_RUN.sub(" ", text).strip()


class BudgetedWriter:
    """Accumule des blocs de texte sous un plafond de caractères.

    Le plafond est vérifié *avant* d'ajouter un bloc : la sortie n'est jamais
    coupée au milieu d'un résultat, elle s'arrête sur un résultat entier et
    signale explicitement ce qui manque.
    """

    def __init__(self, char_cap: int = CHAR_CAP) -> None:
        self.char_cap = char_cap
        self._blocks: list[str] = []
        self._size = 0
        self.truncated = False
        self.dropped = 0

    @property
    def remaining(self) -> int:
        return max(0, self.char_cap - self._size)

    def add(self, block: str) -> bool:
        """Ajoute un bloc s'il tient dans le budget. Retourne False sinon."""
        if self.truncated:
            self.dropped += 1
            return False
        cost = len(block) + 1
        # Un premier bloc plus gros que le budget entier est tout de même émis :
        # mieux vaut une sortie trop longue que vide.
        if self._size and self._size + cost > self.char_cap:
            self.truncated = True
            self.dropped += 1
            return False
        self._blocks.append(block)
        self._size += cost
        return True

    def add_forced(self, block: str) -> None:
        """Ajoute un bloc hors budget (en-tête, note de troncature)."""
        self._blocks.append(block)
        self._size += len(block) + 1

    def render(self, truncation_note: str | None = None) -> str:
        out = list(self._blocks)
        if self.truncated:
            note = truncation_note or (
                f"[résultats tronqués, {self.dropped} élément(s) omis — "
                f"affinez la requête ou réduisez max_results]"
            )
            out.append(note)
        return "\n".join(out)


def truncate_chars(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix
