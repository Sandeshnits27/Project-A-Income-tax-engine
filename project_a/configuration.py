from __future__ import annotations

import hashlib
import json
from typing import Any


def configuration_checksum(config: dict[str, Any]) -> str:
    payload = {key: value for key, value in config.items() if key != "config_checksum"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def add_configuration_checksum(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    result["config_checksum"] = configuration_checksum(result)
    return result
