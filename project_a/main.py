from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from project_a.compiled_engine.calculator import calculate_tax as compiled_calculate
from project_a.llm_engine.calculator import calculate_tax as llm_calculate
from project_a.llm_engine.gemini_client import GeminiError
from project_a.models import TaxpayerInput
from project_a.parser.input_parser import parse_text
from project_a.rules_engine.calculator import calculate_tax as rules_calculate


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError("Input JSON must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare Indian salary-tax regimes for supported financial years.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--json", type=Path, help="Path to structured taxpayer JSON")
    source.add_argument("--text", help="Plain-language description (may return clarification questions)")
    parser.add_argument("--financial-year", choices=["FY_2024_25", "FY_2025_26"])
    parser.add_argument("--engine", choices=["rules", "compiled", "llm"], default="rules")
    args = parser.parse_args(argv)
    if args.text is not None:
        result = parse_text(args.text, args.financial_year)
        if result["clarification_needed"]:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 2
        raw = result["extracted_fields"]
    else:
        raw = _read_json(args.json)
    if args.financial_year:
        raw.setdefault("financial_year", args.financial_year)
    data = TaxpayerInput.from_dict(raw)
    calculator = {
        "rules": rules_calculate,
        "compiled": compiled_calculate,
        "llm": llm_calculate,
    }[args.engine]
    result = calculator(data)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, GeminiError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2)
