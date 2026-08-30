"""Processus de travail pour les tests de concurrence J2.

Simule une instance serveur distincte : chaque exécution est un processus
séparé, avec son propre registre et ses propres descripteurs de fichiers.

  python _propose_worker.py <hub_root> <base> <étiquette> <itérations>

Imprime une ligne par proposition déposée : "OK <id>" ou "ERR <code>".
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from okf_hub.config import HubConfig  # noqa: E402
from okf_hub.errors import ToolError  # noqa: E402
from okf_hub.registry import Registry  # noqa: E402
from okf_hub.tools import propose_tool  # noqa: E402


def main() -> int:
    hub_root, base, label, iterations = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    registry = Registry(HubConfig.load(Path(hub_root)))
    registry.scan()

    failures = 0
    for i in range(iterations):
        try:
            out = propose_tool.run(
                registry,
                {
                    "base": base,
                    "type": "observation",
                    "concerns": f"sujet {label}-{i}",
                    "content": f"Affirmation numéro {i} émise par {label}.",
                    "sources": [f"constat {label}-{i}"],
                    "confidence": "medium",
                    "submitted_by": f"process:{label}",
                },
            )
            prop_id = next(
                line.split(":", 1)[1].strip()
                for line in out.splitlines()
                if line.startswith("id :")
            )
            print(f"OK {prop_id}", flush=True)
        except ToolError as exc:
            failures += 1
            print(f"ERR {exc.code} {exc.message}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
