"""Journalisation multi-instances (§ 4.1).

`hub.log` est partagé par toutes les instances du serveur (une par client MCP
connecté, § 4.4). Exigences normatives respectées ici :

* ouverture en ``O_APPEND`` — le noyau garantit alors que le positionnement en
  fin de fichier et l'écriture sont atomiques pour un fichier régulier ;
* une entrée = un unique appel ``write`` d'une ligne complète, terminée par
  ``\\n`` : deux instances ne peuvent pas entrelacer des demi-lignes ;
* chaque ligne préfixée par un horodatage UTC et le PID, sans quoi le journal
  est inexploitable pour diagnostiquer la concurrence testée en J2.

Le journal est volontairement sans dépendance au module `logging` de la
bibliothèque standard : ses handlers bufferisent et peuvent découper une entrée
en plusieurs writes, ce qui casse l'atomicité recherchée.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_fd: int | None = None
_pid: int = os.getpid()
_echo_stderr: bool = False


def configure(log_file: Path | None, echo_stderr: bool = False) -> None:
    """Ouvre (ou réouvre) le journal. Sans fichier, seule la sortie d'erreur sert."""
    global _fd, _pid, _echo_stderr
    close()
    _pid = os.getpid()
    _echo_stderr = echo_stderr
    if log_file is None:
        return
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        _fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError as exc:  # journal inaccessible : ne jamais bloquer le serveur
        _fd = None
        print(f"okf-hub: journal indisponible ({exc})", file=sys.stderr)


def close() -> None:
    global _fd
    if _fd is not None:
        try:
            os.close(_fd)
        except OSError:
            pass
        _fd = None


def _emit(level: str, message: str) -> None:
    # Une entrée tient sur une seule ligne : les retours à la ligne du message
    # sont échappés, sinon une entrée multiligne serait indissociable de deux
    # entrées concurrentes entrelacées.
    flat = message.replace("\r", " ").replace("\n", "\\n")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    line = f"{stamp} [pid:{_pid}] {level:<7} {flat}\n"
    data = line.encode("utf-8")
    if _fd is not None:
        try:
            os.write(_fd, data)  # un seul write == une seule entrée
        except OSError:
            pass
    if _echo_stderr or _fd is None:
        sys.stderr.write(line)
        sys.stderr.flush()


def info(message: str) -> None:
    _emit("INFO", message)


def warning(message: str) -> None:
    _emit("WARNING", message)


def error(message: str) -> None:
    _emit("ERROR", message)
