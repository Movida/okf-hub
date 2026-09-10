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

    L'arrêt est **définitif** : dès qu'un bloc dépasse le budget restant,
    `truncated` passe à `True` et tout bloc suivant est rejeté sans être
    essayé, quelle que soit sa taille — y compris un bloc plus petit qui
    tiendrait dans ce qu'il reste de budget. Ce n'est pas un remplissage au
    mieux (« best-effort »). C'est délibéré pour un appelant qui ajoute ses
    blocs dans l'ordre de pertinence (`kb_search`) : ce qui est montré reste
    toujours un **préfixe du classement**, jamais un mélange de gros résultats
    pertinents sautés et de petits résultats moins pertinents remontés à leur
    place. Voir `docs/ARCHITECTURE.md` § 6 sexies pour la décision et son
    alternative écartée. Caractérisé par
    `tests/test_search_list.py::test_budgeted_writer_ne_retente_pas_un_bloc_plus_petit_apres_troncature`.
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
        """Rend les blocs accumulés, avec une note de troncature si besoin.

        `truncation_note`, quand fourni, devrait nommer le levier qui change
        réellement ce qui est affiché. Relever le nombre de résultats demandé
        n'en fait *jamais* partie : les blocs sont essayés dans l'ordre où ils
        ont été ajoutés (typiquement un ordre de pertinence), et l'arrêt au
        premier dépassement (cf. `add`) fait que la troncature ne dépend que
        de leur taille cumulée, pas de combien en ont été demandés — un
        appelant qui en redemande davantage ne fait qu'allonger la liste
        derrière un plafond déjà atteint.
        """
        out = list(self._blocks)
        if self.truncated:
            note = truncation_note or f"[résultats tronqués, {self.dropped} élément(s) omis]"
            out.append(note)
        return "\n".join(out)


def truncate_chars(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix
