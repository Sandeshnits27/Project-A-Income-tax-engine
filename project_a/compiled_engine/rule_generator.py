from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_a.configuration import add_configuration_checksum, configuration_checksum
from project_a.llm_engine.gemini_client import generate


def compile_rule_text(financial_year: str, official_rule_text: str, output_dir: Path) -> dict[str, Any]:
    if not official_rule_text.strip():
        raise ValueError("Supply the relevant official statutory text; no source text was provided.")
    prompt = (
        "Convert the supplied official Indian income-tax source text into a JSON tax configuration. "
        "Do not add rules not present in the source. Preserve citations for every rule. Use this exact "
        "top-level shape: financial_year, version, sources, regimes, hra, cess_rate, round_total_income_to, "
        "round_final_tax_to. The financial year is "
        + financial_year
        + ". Return only JSON. Source text:\n"
        + official_rule_text
    )
    generated, usage, elapsed_ms, model = generate(prompt)
    try:
        config = json.loads(generated)
    except json.JSONDecodeError as error:
        raise ValueError(f"Generated rules are not valid JSON: {error}") from error
    validate_compiled_config(config, financial_year)
    config = add_configuration_checksum(config)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{financial_year.lower()}_candidate.json"
    output.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"path": str(output), "model": model, "usage": usage, "latency_ms": round(elapsed_ms, 3)}


def validate_compiled_config(config: dict[str, Any], expected_year: str) -> None:
    required = {"financial_year", "version", "sources", "regimes", "hra", "cess_rate", "round_total_income_to", "round_final_tax_to"}
    if not isinstance(config, dict) or not required.issubset(config):
        raise ValueError("Compiled configuration is missing required top-level keys")
    if config["financial_year"] != expected_year:
        raise ValueError("Compiled configuration financial year does not match the requested year")
    if config.get("config_checksum") and configuration_checksum(config) != config["config_checksum"]:
        raise ValueError("Compiled configuration checksum does not match its contents")
    if set(config["regimes"]) != {"old", "new"}:
        raise ValueError("Compiled configuration must define old and new regimes")
    if not isinstance(config["sources"], list) or not config["sources"]:
        raise ValueError("Compiled configuration must retain source references")
    for regime_name, regime in config["regimes"].items():
        for key in ("standard_deduction", "slabs", "rebate", "deductions", "surcharge"):
            if key not in regime:
                raise ValueError(f"{regime_name} regime is missing {key}")
        if not isinstance(regime["standard_deduction"], int) or regime["standard_deduction"] < 0:
            raise ValueError(f"{regime_name} regime has invalid standard deduction")
        if not regime["slabs"] or regime["slabs"][-1].get("up_to") is not None:
            raise ValueError(f"{regime_name} regime must have an open-ended last tax slab")
        previous = 0
        for slab in regime["slabs"]:
            ceiling = slab.get("up_to")
            if ceiling is not None and (not isinstance(ceiling, int) or ceiling <= previous):
                raise ValueError(f"{regime_name} regime has unordered or invalid slab thresholds")
            rate = float(slab["rate"])
            if not 0 <= rate <= 1:
                raise ValueError(f"{regime_name} regime has invalid tax rate")
            if ceiling is not None:
                previous = ceiling
        rebate = regime["rebate"]
        if any(not isinstance(rebate.get(key), int) or rebate[key] < 0 for key in ("income_limit", "maximum")):
            raise ValueError(f"{regime_name} regime has invalid rebate amounts")
        if not isinstance(regime["deductions"], dict) or any(
            not isinstance(value, int) or value < 0 for value in regime["deductions"].values()
        ):
            raise ValueError(f"{regime_name} regime has invalid deduction limits")
        previous = -1
        for tier in regime["surcharge"]:
            if not isinstance(tier.get("above"), int) or tier["above"] <= previous:
                raise ValueError(f"{regime_name} regime has unordered surcharge thresholds")
            previous = tier["above"]
            if not 0 <= float(tier["rate"]) <= 1:
                raise ValueError(f"{regime_name} regime has invalid surcharge rate")
