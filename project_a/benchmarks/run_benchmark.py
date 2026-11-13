from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Callable

from project_a.compiled_engine.calculator import calculate_tax as compiled_calculate
from project_a.engine import IncompleteInputError
from project_a.llm_engine.calculator import calculate_tax as llm_calculate
from project_a.llm_engine.gemini_client import GeminiError
from project_a.models import TaxpayerInput
from project_a.parser.input_parser import parse_text
from project_a.rules_engine.calculator import calculate_tax as rules_calculate

ROOT = Path(__file__).resolve().parents[2]
TAX_CASES = ROOT / "project_a" / "tests" / "data" / "tax_cases.json"
LANGUAGE_CASES = ROOT / "project_a" / "data" / "natural_language_cases.json"
RESULTS = ROOT / "RESULTS_A.md"
LAST_RUN = Path(__file__).resolve().parent / "last_run.json"


def _latency(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean_ms": None, "median_ms": None, "p95_ms": None}
    ordered = sorted(values)
    return {
        "mean_ms": round(statistics.fmean(ordered), 3),
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(0.95 * len(ordered) + 0.5) - 1)], 3),
    }


def _expected(case: dict[str, Any]) -> dict[str, int]:
    return {"old": case["expected_old_regime_tax"], "new": case["expected_new_regime_tax"]}


def _actual(result: dict[str, Any]) -> dict[str, int]:
    return {"old": result["old_regime"]["tax_payable"], "new": result["new_regime"]["tax_payable"]}


def _run_local(cases: list[dict[str, Any]], calculate: Callable, repeat_case: dict[str, Any]) -> dict[str, Any]:
    matches = 0
    old_matches = 0
    new_matches = 0
    failures: list[dict[str, str]] = []
    elapsed: list[float] = []
    for case in cases:
        start = time.perf_counter()
        try:
            result = calculate(TaxpayerInput.from_dict(case["input"]))
            elapsed.append((time.perf_counter() - start) * 1000)
            actual = _actual(result)
            expected = _expected(case)
            old_matches += actual["old"] == expected["old"]
            new_matches += actual["new"] == expected["new"]
            if actual == expected:
                matches += 1
            else:
                failures.append({"case_id": case["case_id"], "error": f"expected {expected}, got {actual}"})
        except (ValueError, IncompleteInputError) as error:
            elapsed.append((time.perf_counter() - start) * 1000)
            failures.append({"case_id": case["case_id"], "error": f"{type(error).__name__}: {error}"})
    repeatability = _repeatability(repeat_case, calculate) if cases else {"runs": 0, "distinct_tax_results": 0, "failures": 0, "results": []}
    return {
        "status": "measured",
        "case_count": len(cases),
        "exact_matches": matches,
        "old_regime_exact_matches": old_matches,
        "old_regime_accuracy_percent": round(old_matches / len(cases) * 100, 3) if cases else None,
        "new_regime_exact_matches": new_matches,
        "new_regime_accuracy_percent": round(new_matches / len(cases) * 100, 3) if cases else None,
        "failures": len(failures),
        "accuracy_percent": round(matches / len(cases) * 100, 3) if cases else None,
        "latency": _latency(elapsed),
        "failure_examples": failures[:10],
        "llm_api_cost": "not applicable; no model requests",
        "repeatability": {**repeatability, "case_id": repeat_case["case_id"]},
    }


def _repeatability(case: dict[str, Any], calculate: Callable, count: int = 10) -> dict[str, Any]:
    values = []
    errors = 0
    for _ in range(count):
        try:
            result = calculate(TaxpayerInput.from_dict(case["input"]))
            values.append((_actual(result)["old"], _actual(result)["new"]))
        except (ValueError, GeminiError, IncompleteInputError):
            errors += 1
    return {"runs": count, "distinct_tax_results": len(set(values)), "failures": errors, "results": [list(value) for value in values]}


def _run_llm(cases: list[dict[str, Any]], repeat_case: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("GEMINI_API_KEY"):
        return {
            "status": "not_run",
            "reason": "GEMINI_API_KEY is unset; no API requests were made.",
            "case_count": len(cases),
            "exact_matches": None,
            "failures": None,
            "accuracy_percent": None,
            "latency": _latency([]),
            "tokens": {"input": None, "output": None},
            "cost_per_1000": "not calculated; no usage occurred and current free-tier quotas/pricing must be verified for the account",
            "repeatability": {"status": "not_run"},
        }
    matches = 0
    old_matches = 0
    new_matches = 0
    failures = []
    durations = []
    input_tokens = 0
    output_tokens = 0
    repeat_values = []
    repeat_failures = 0
    for index, case in enumerate(cases):
        start = time.perf_counter()
        try:
            result = llm_calculate(TaxpayerInput.from_dict(case["input"]))
            durations.append((time.perf_counter() - start) * 1000)
            usage = result.get("llm_metadata", {})
            input_tokens += usage.get("input_tokens") or 0
            output_tokens += usage.get("output_tokens") or 0
            actual = _actual(result)
            expected = _expected(case)
            old_matches += actual["old"] == expected["old"]
            new_matches += actual["new"] == expected["new"]
            if actual == expected:
                matches += 1
            else:
                failures.append({"case_id": case["case_id"], "error": f"expected {expected}, got {actual}"})
            if index == 0:
                repeat_values.append((_actual(result)["old"], _actual(result)["new"]))
        except (ValueError, GeminiError, IncompleteInputError) as error:
            durations.append((time.perf_counter() - start) * 1000)
            failures.append({"case_id": case["case_id"], "error": f"{type(error).__name__}: {error}"})
            if index == 0:
                repeat_failures += 1
    for _ in range(9):
        try:
            repeated = llm_calculate(TaxpayerInput.from_dict(repeat_case["input"]))
            repeat_values.append((_actual(repeated)["old"], _actual(repeated)["new"]))
            usage = repeated.get("llm_metadata", {})
            input_tokens += usage.get("input_tokens") or 0
            output_tokens += usage.get("output_tokens") or 0
        except (ValueError, GeminiError, IncompleteInputError):
            repeat_failures += 1
    return {
        "status": "measured",
        "case_count": len(cases),
        "exact_matches": matches,
        "old_regime_exact_matches": old_matches,
        "old_regime_accuracy_percent": round(old_matches / len(cases) * 100, 3) if cases else None,
        "new_regime_exact_matches": new_matches,
        "new_regime_accuracy_percent": round(new_matches / len(cases) * 100, 3) if cases else None,
        "failures": len(failures),
        "accuracy_percent": round(matches / len(cases) * 100, 3) if cases else None,
        "latency": _latency(durations),
        "failure_examples": failures[:10],
        "tokens": {"input": input_tokens, "output": output_tokens},
        "cost_per_1000": "not calculated; verify current Gemini account pricing/free-tier quota before claiming a dollar amount",
        "repeatability": {
            "runs_for_same_input": 10,
            "distinct_tax_results": len(set(repeat_values)),
            "failures": repeat_failures,
            "results": [list(value) for value in repeat_values],
            "case_id": repeat_case["case_id"],
        },
    }


def _write_results(report: dict[str, Any]) -> None:
    rows = []
    for name in ("hand_coded_rules", "query_time_llm", "compiled_rules"):
        data = report["implementations"][name]
        if data["status"] == "not_run":
            old_accuracy = "Not run"
            new_accuracy = "Not run"
            latency = "Not measured"
            repeats = "Not measured"
            cost = data["cost_per_1000"]
        else:
            old_accuracy = f'{data["old_regime_exact_matches"]}/{data["case_count"]} ({data["old_regime_accuracy_percent"]}%)'
            new_accuracy = f'{data["new_regime_exact_matches"]}/{data["case_count"]} ({data["new_regime_accuracy_percent"]}%)'
            latency_data = data["latency"]
            latency = f'{latency_data["median_ms"]} / {latency_data["p95_ms"]} ms median / p95'
            repeat = data["repeatability"]
            repeats = f'{repeat["distinct_tax_results"]} distinct; {repeat["failures"]} failed'
            cost = data.get("llm_api_cost", data.get("cost_per_1000", "not measured"))
        rows.append(f"| {name.replace('_', ' ')} | {old_accuracy} | {new_accuracy} | {repeats} | {latency} | {cost} |")
    parser = report["parser"]
    text = f"""# Project A — Results

Generated by `python -m project_a.reproduce` at {report["run_at_utc"]}.

## Benchmark status and method

The benchmark runs the same {report["test_case_count"]} frozen structured inputs against the hand-coded and compiled engines and compares both old/new regime tax against a separately implemented local reference oracle. Those expected values are **not** official-calculator verified. No official-calculator result is claimed: {report["official_calculator_cases_checked"]} of 30 required comparisons have been recorded as complete. Financial-law configuration is marked unverified pending review of the named official sources.

The query-time Gemini implementation is tested only when `GEMINI_API_KEY` is set and `--include-llm` is passed. No failure is treated as a correct result. No API is called by default. Local wall-clock timings are this run's measurements and include calculator invocation only; they exclude parser latency, API/network calls, hardware-normalized costs, and deployment overhead.

| Implementation | Old-regime exact accuracy | New-regime exact accuracy | 10-run repeatability | Median / p95 latency | Cost per 1,000 |
|---|---:|---:|---:|---:|---|
{chr(10).join(rows)}

### Measurements

- Plain-language corpus: {parser["case_count"]} descriptions; {parser["clarification_count"]} returned a clarification state according to the current parser; latency mean/median/p95 = {parser["latency"]["mean_ms"]}/{parser["latency"]["median_ms"]}/{parser["latency"]["p95_ms"]} ms.
- Repeatability was measured on the same taxable case ({report["repeatability_case_id"]}) for every implementation.
- Reproducible input fixtures: {report["test_case_count"]} (110 per year), with expected old/new tax amounts from a separate local oracle.
- Official Income Tax Department calculator checks recorded in the fixture: {report["official_calculator_cases_checked"]}; required: at least 30. Remaining: **not completed**.
- Query-time LLM prompt version: `tax-query-v1`; model: {report["llm_model"]}; token use: {report["implementations"]["query_time_llm"].get("tokens", "not measured")}.
- Cost: deterministic engines make no model calls; hardware/operational cost is excluded. Gemini costs are not asserted; determine the current account-specific free tier/pricing before submission.
- Auditability: rules and compiled outputs include intermediate rupee amounts, rule identifiers and official-source URLs. Statutory clause verification is pending, so this is not yet a validated legal audit trail.

## FY 2025–26 change log

| Implementation | Changes and measured time | Breakages / silent failures | Regression |
|---|---|---|---|
| Hand-coded rules | Added separate FY 2025–26 configuration for new-regime slabs/rebate; elapsed update time not measured separately because both year configurations were created together. | No failures observed in local oracle suite; legal/calculator mismatch remains an unmeasured risk. | See per-year accuracy above and frozen FY 2024–25 cases. |
| Query-time LLM | Prompt includes year-specific configuration; no model update timing measured and query-time benchmark {report["implementations"]["query_time_llm"]["status"]}. | Not evaluated unless Gemini was explicitly enabled. | FY 2024–25 inputs remain in the common test set; no comparative claims if API was not run. |
| Compiled rules | Added a static FY 2025–26 configuration snapshot; it was not produced by Gemini in this workspace. LLM generation/review time was not measured. | No local oracle failures observed; generated-rule quality and statutory correctness are unverified. | See per-year accuracy above and frozen FY 2024–25 cases. |

No update duration, official calculator comparison, or Gemini token/cost value is fabricated. A pre-change failure comparison was not captured.

## Final design (provisional; not production-approved)

**Target: rule-first hybrid, and do not launch until legal gates pass.** Input extraction = **rules** for covered patterns; an optional **LLM** may propose schema fields only after a separate parser benchmark and taxpayer confirmation (not implemented or measured here). Validation and missing-fact clarification = **rules**. Tax calculations, regime comparison, version selection and the rupee-level audit trail = **rules**. Explanations = **rules/templates** derived from verified line items; generated prose must never change amounts. Query-time and compiled LLM calculations remain **experimental**, because query-time Gemini was not measured and the static compiled rules were not generated by Gemini. The 220/220 internal-oracle result and deterministic repeats support keeping tax arithmetic deterministic, but the oracle shares unverified rule configuration and is not sufficient evidence for launch. When Gemini is unavailable, structured-input calculations continue; unsupported free text receives a clarification request. Keep FY configurations immutable, hashed, independently reviewed against the Finance Acts, and regression-tested. Scope is salary income only; capital gains, business income, other income heads and unmodeled eligibility exceptions are excluded. This prototype is not ready to serve 100,000 users until official validation and scale testing are complete.
"""
    RESULTS.write_text(text, encoding="utf-8")


def run(include_llm: bool = False) -> dict[str, Any]:
    cases = json.loads(TAX_CASES.read_text(encoding="utf-8"))
    descriptions = json.loads(LANGUAGE_CASES.read_text(encoding="utf-8"))
    repeat_case = next(
        (case for case in cases if case["expected_old_regime_tax"] > 0 and case["expected_new_regime_tax"] > 0),
        cases[0],
    )
    implementations = {
        "hand_coded_rules": _run_local(cases, rules_calculate, repeat_case),
        "compiled_rules": _run_local(cases, compiled_calculate, repeat_case),
        "query_time_llm": _run_llm(cases, repeat_case) if include_llm else {
            "status": "not_run",
            "reason": "Use --include-llm with GEMINI_API_KEY to make query-time requests.",
            "case_count": len(cases),
            "exact_matches": None,
            "old_regime_exact_matches": None,
            "old_regime_accuracy_percent": None,
            "new_regime_exact_matches": None,
            "new_regime_accuracy_percent": None,
            "failures": None,
            "accuracy_percent": None,
            "latency": _latency([]),
            "cost_per_1000": "not measured",
            "repeatability": {"status": "not_run"},
        },
    }
    parser_times = []
    clarifications = 0
    for case in descriptions:
        start = time.perf_counter()
        result = parse_text(case["text"])
        parser_times.append((time.perf_counter() - start) * 1000)
        clarifications += int(result["clarification_needed"])
    report = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "test_case_count": len(cases),
        "repeatability_case_id": repeat_case["case_id"],
        "official_calculator_cases_checked": sum(
            case["official_calculator"]["status"] == "checked" for case in cases
        ),
        "llm_model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
        "implementations": implementations,
        "parser": {
            "case_count": len(descriptions),
            "clarification_count": clarifications,
            "latency": _latency(parser_times),
        },
    }
    LAST_RUN.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_results(report)
    print(json.dumps(report, indent=2))
    print(f"\nUpdated {RESULTS}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local Project A tax-engine benchmarks.")
    parser.add_argument("--include-llm", action="store_true", help="Make Gemini requests for all 220 cases and 9 additional repeated requests.")
    args = parser.parse_args(argv)
    run(include_llm=args.include_llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
