from __future__ import annotations

import json
from pathlib import Path

from project_a.configuration import add_configuration_checksum


def refresh(directory: Path) -> None:
    for path in sorted(directory.glob("fy_*.json")):
        config = json.loads(path.read_text(encoding="utf-8"))
        signed = add_configuration_checksum(config)
        path.write_text(json.dumps(signed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    refresh(root / "config")
    refresh(root / "compiled_engine" / "compiled_rules")
