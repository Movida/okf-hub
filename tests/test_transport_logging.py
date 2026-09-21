"""Le transport journalise les messages client illisibles (§ 5 ter).

Un JSON-RPC mal encodé par le client — typiquement un champ `content` dont un
guillemet, un antislash ou un saut de ligne n'a pas été échappé — est rejeté par
le SDK *avant* tout outil, sous forme d'objet `Exception` posé sur le flux de
lecture. Sans point d'observation, l'appel n'apparaît nulle part dans le journal
du hub, ce qui a rendu un `kb_propose` « cassé » indiagnosticable à deux
reprises (21/09/2026). Le relais `_tee_read_stream` doit journaliser l'événement
et réémettre chaque message inchangé.
"""

from __future__ import annotations

import anyio
import pytest

from okf_hub import hublog
from okf_hub.__main__ import _tee_read_stream


class _Boom(Exception):
    pass


def test_le_relais_journalise_les_exceptions_et_transmet_tout(tmp_path, monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(hublog, "info", lambda m: messages.append(m))

    items = ["msg-avant", _Boom("ligne JSON coupée"), "msg-apres"]
    recus: list = []

    async def scenario():
        src_send, src_recv = anyio.create_memory_object_stream(0)
        sink_send, sink_recv = anyio.create_memory_object_stream(0)

        async def producteur():
            async with src_send:
                for it in items:
                    await src_send.send(it)

        async def consommateur():
            async for it in sink_recv:
                recus.append(it)

        async with anyio.create_task_group() as tg:
            tg.start_soon(producteur)
            tg.start_soon(consommateur)
            await _tee_read_stream(src_recv, sink_send)

    anyio.run(scenario)

    # Tout est transmis, dans l'ordre, y compris l'exception (le SDK doit la
    # recevoir pour renvoyer sa propre erreur au client).
    assert recus == items
    # Exactement une ligne de journal, pour l'exception, sans le corps.
    assert len(messages) == 1
    assert "transport" in messages[0] and "_Boom" in messages[0]
    assert "ligne JSON coupée" not in messages[0]


def test_aucun_journal_sans_exception(tmp_path, monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(hublog, "info", lambda m: messages.append(m))
    recus: list = []

    async def scenario():
        src_send, src_recv = anyio.create_memory_object_stream(0)
        sink_send, sink_recv = anyio.create_memory_object_stream(0)

        async def producteur():
            async with src_send:
                for it in ("a", "b", "c"):
                    await src_send.send(it)

        async def consommateur():
            async for it in sink_recv:
                recus.append(it)

        async with anyio.create_task_group() as tg:
            tg.start_soon(producteur)
            tg.start_soon(consommateur)
            await _tee_read_stream(src_recv, sink_send)

    anyio.run(scenario)
    assert recus == ["a", "b", "c"]
    assert messages == []
