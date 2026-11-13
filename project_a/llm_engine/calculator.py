from __future__ import annotations

import json
import re
from typing import Any

from project_a.engine import load_config, missing_tax_facts
from project_a.llm_engine.gemini_client import generate
from project_a.models import TaxpayerInput
from project_a.parser.validator import clarification_questions

PROMPT_VERSION = "tax-query-v1"


def _decode_json(text: str, expected_year: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise ValueError(f"Gemini returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Gemini result must be a JSON object")
    if value.get("financial_year") != expected_year:
        raise ValueError("Gemini result financial_year does not match the request")
    for key in ("old_regime", "new_regime"):
        tax = value.get(key, {}).get("tax_payable") if isinstance(value.get(key), dict) else None
        if not isinstance(tax, int) or isinstance(tax, bool) or tax < 0:
            raise ValueError(f"Gemini result must contain integer {key}.tax_payable")
    if value.get("preferred_regime") not in {"old", "new", "tie"}:
        raise ValueError("Gemini result has invalid preferred_regime")
    if not isinstance(value.get("difference_in_tax"), int) or isinstance(value["difference_in_tax"], bool):
        raise ValueError("Gemini result must contain integer difference_in_tax")
    if not isinstance(value.get("audit"), list):
        raise ValueError("Gemini result must contain an audit list")
    for line in value["audit"]:
        if not isinstance(line, dict) or not line.get("rule_id") or "amount" not in line:
            raise ValueError("Gemini audit lines must include rule_id and amount")
    return value


def calculate_tax(data: TaxpayerInput) -> dict[str, Any]:
    data.validate()
    missing = missing_tax_facts(data)
    if missing:
        return {
            "financial_year": data.financial_year,
            "clarification_needed": True,
            "missing_fields": missing,
            "questions": clarification_questions(missing),
            "llm_metadata": {
                "model": None,
                "prompt_version": PROMPT_VERSION,
                "latency_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "api_error": None,
                "api_called": False,
            },
        }
    config = load_config(data.financial_year)
    prompt = (
        "You are an experimental query-time income-tax calculator. Use only the supplied configuration and "
        "structured input; do not infer missing facts. This experiment's legal configuration has not been "
        "independently validated against the official calculator, so return calculations only, not legal advice. "
        "Calculate the old and new regimes independently using Decimal-style exact arithmetic, stated caps, rebate, "
        "surcharge and marginal relief, 4% cess, and the configured rounding. Return JSON with keys "
        "financial_year, old_regime, new_regime, preferred_regime, difference_in_tax, assumptions, and "
        "audit. Each regime must contain integer tax_payable and a breakdown. If a necessary fact is absent, "
        "return {\"clarification_needed\": true, \"missing_fields\": [...], \"questions\": [...]}. "
        "Configuration (also includes the official source references):\n"
        + json.dumps(config, ensure_ascii=False)
        + "\nStructured taxpayer input:\n"
        + json.dumps(data.as_dict(), ensure_ascii=False)
    )
    text, usage, elapsed_ms, model = generate(prompt)
    result = _decode_json(text, data.financial_year)
    result["llm_metadata"] = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "latency_ms": round(elapsed_ms, 3),
        "input_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
        "api_error": None,
        "api_called": True,
    }
    for regime in ("old_regime", "new_regime"):
        if result[regime]["tax_payable"] < 0:
            raise ValueError(f"Gemini returned a negative {regime} tax amount")
    expected_preference = (
        "old" if result["old_regime"]["tax_payable"] < result["new_regime"]["tax_payable"]
        else "new" if result["new_regime"]["tax_payable"] < result["old_regime"]["tax_payable"]
        else "tie"
    )
    if result["preferred_regime"] != expected_preference:
        raise ValueError("Gemini preferred_regime does not agree with its returned tax amounts")
    expected_difference = abs(result["old_regime"]["tax_payable"] - result["new_regime"]["tax_payable"])
    if result.get("difference_in_tax") != expected_difference:
        raise ValueError("Gemini difference_in_tax does not agree with its returned tax amounts")
    result["experimental_unverified"] = True
    result["review_status"] = "not_authoritative"
    return result
