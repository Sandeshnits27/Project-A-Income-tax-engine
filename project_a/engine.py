from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from project_a.configuration import configuration_checksum
from project_a.models import TaxpayerInput

ROOT = Path(__file__).resolve().parent
ROUNDING = ROUND_HALF_UP
ZERO = Decimal("0")


class IncompleteInputError(ValueError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("Missing information required for a reliable comparison: " + ", ".join(missing))


def load_config(financial_year: str, config_dir: Path | None = None) -> dict[str, Any]:
    directory = config_dir or ROOT / "config"
    path = directory / f"{financial_year.lower()}.json"
    if not path.is_file():
        raise ValueError(f"No versioned tax configuration for {financial_year}")
    with path.open(encoding="utf-8") as file:
        config = json.load(file)
    if config.get("financial_year") != financial_year:
        raise ValueError(f"Configuration year mismatch in {path}")
    expected_checksum = config.get("config_checksum")
    if not expected_checksum:
        raise ValueError(f"Configuration checksum is missing in {path}")
    if configuration_checksum(config) != expected_checksum:
        raise ValueError(f"Configuration checksum mismatch in {path}")
    return config


def missing_tax_facts(data: TaxpayerInput) -> list[str]:
    required = [
        "is_resident",
        "section_80c",
        "section_80ccd_1b",
        "section_80d_self_family",
        "section_80d_parents",
        "home_loan_interest",
    ]
    missing = [name for name in required if getattr(data, name) is None]
    if data.hra_received is None:
        missing.append("hra_received")
    elif data.hra_received > 0:
        for name in ("basic_salary", "eligible_da", "annual_rent", "city_category"):
            if getattr(data, name) is None:
                missing.append(name)
    if data.section_80d_parents and not data.parents_ages:
        missing.append("parents_ages")
    if data.home_loan_interest and data.home_loan_self_occupied is None:
        missing.append("home_loan_self_occupied")
    if data.home_loan_interest and data.home_loan_eligible is None:
        missing.append("home_loan_eligible")
    return missing


def _round_to(value: Decimal, unit: int) -> Decimal:
    quantum = Decimal(unit)
    return (value / quantum).quantize(Decimal("1"), rounding=ROUNDING) * quantum


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _slab_tax(income: int, slabs: list[dict[str, Any]], exemption_limit: int = 0) -> Decimal:
    previous = exemption_limit
    tax = ZERO
    for slab in slabs:
        ceiling = slab["up_to"]
        if ceiling is not None and int(ceiling) <= previous:
            continue
        end = income if ceiling is None else min(income, int(ceiling))
        taxable_band = max(0, end - previous)
        tax += Decimal(taxable_band) * Decimal(slab["rate"])
        if ceiling is None or income <= ceiling:
            break
        previous = int(ceiling)
    return tax


def _hra_exemption(data: TaxpayerInput, config: dict[str, Any]) -> Decimal:
    if not data.hra_received:
        return ZERO
    if data.basic_salary is None or data.annual_rent is None or data.city_category is None:
        raise IncompleteInputError(["basic_salary", "annual_rent", "city_category"])
    limits = config["hra"]
    city_fraction = Decimal(limits["metro_cap_fraction"] if data.city_category == "metro" else limits["non_metro_cap_fraction"])
    salary = Decimal(data.basic_salary + (data.eligible_da or 0))
    rent_excess = max(ZERO, Decimal(data.annual_rent) - salary * Decimal(limits["rent_less_salary_fraction"]))
    return min(Decimal(data.hra_received), rent_excess, salary * city_fraction)


def _old_deductions(data: TaxpayerInput, rules: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Decimal], list[dict[str, Any]]]:
    limits = rules["deductions"]
    hra = _hra_exemption(data, config)
    self_is_senior = data.age >= 60
    parent_is_senior = bool(data.parents_ages) and any(age >= 60 for age in data.parents_ages)
    self_80d_cap = limits["80d_self_senior"] if self_is_senior else limits["80d_self_regular"]
    parent_80d_cap = limits["80d_parent_senior"] if parent_is_senior else limits["80d_parent_regular"]
    eligible = {
        "hra_exemption": hra,
        "section_80c": min(Decimal(data.section_80c or 0), Decimal(limits["80c"])),
        "section_80ccd_1b": min(Decimal(data.section_80ccd_1b or 0), Decimal(limits["80ccd_1b"])),
        "section_80d_self_family": min(Decimal(data.section_80d_self_family or 0), Decimal(self_80d_cap)),
        "section_80d_parents": min(Decimal(data.section_80d_parents or 0), Decimal(parent_80d_cap)),
        "home_loan_interest": min(
            Decimal(data.home_loan_interest or 0),
            Decimal(limits["home_loan_interest"]) if data.home_loan_self_occupied and data.home_loan_eligible else ZERO,
        ),
    }
    audit = [
        {"rule_id": "ITAA_10_13A_HRA", "source_clause": "Income-tax Act, Section 10(13A)", "amount": _decimal_text(hra), "description": "Exempt HRA: least of HRA, rent less 10% eligible salary, and city salary limit."},
        {"rule_id": "ITAA_80C", "source_clause": "Income-tax Act, Section 80C", "amount": _decimal_text(eligible["section_80c"]), "description": "Eligible Section 80C amount subject to the aggregate cap."},
        {"rule_id": "ITAA_80CCD_1B", "source_clause": "Income-tax Act, Section 80CCD(1B)", "amount": _decimal_text(eligible["section_80ccd_1b"]), "description": "Eligible additional NPS contribution subject to its cap."},
        {"rule_id": "ITAA_80D", "source_clause": "Income-tax Act, Section 80D", "amount": _decimal_text(eligible["section_80d_self_family"] + eligible["section_80d_parents"]), "description": "Eligible health-insurance amounts; age-based caps applied separately."},
        {"rule_id": "ITAA_24_B", "source_clause": "Income-tax Act, Section 24(b)", "amount": _decimal_text(eligible["home_loan_interest"]), "description": "Self-occupied home-loan interest, subject to eligibility and cap."},
    ]
    return eligible, audit


def _regime_result(data: TaxpayerInput, config: dict[str, Any], regime_name: str) -> dict[str, Any]:
    rules = config["regimes"][regime_name]
    standard = Decimal(rules["standard_deduction"])
    allowed: dict[str, Decimal] = {}
    audit: list[dict[str, Any]] = []
    if regime_name == "old":
        allowed, audit = _old_deductions(data, rules, config)
    else:
        audit = [
            {"rule_id": "ITAA_16_IA", "source_clause": "Income-tax Act, Section 16(ia)", "amount": _decimal_text(standard), "description": "Standard deduction under the selected new regime."},
            {"rule_id": "REGIME_EXCLUSIONS", "source_clause": "Finance Act 2024/2025 regime provisions", "amount": "0", "description": "HRA, 80C, 80CCD(1B), 80D and self-occupied interest claims are not deducted in this scope."},
        ]
    gross = Decimal(data.annual_salary)
    hra = allowed.get("hra_exemption", ZERO)
    salary_after_exemption = max(ZERO, gross - hra)
    income_after_standard = max(ZERO, salary_after_exemption - standard)
    other_deductions = sum((amount for name, amount in allowed.items() if name != "hra_exemption"), ZERO)
    unrounded_income = max(ZERO, income_after_standard - other_deductions)
    income = int(_round_to(unrounded_income, int(config["round_total_income_to"])))

    exemption_limit = 0
    if regime_name == "old":
        for item in rules.get("senior_slabs", []):
            if data.age >= int(item["minimum_age"]):
                exemption_limit = int(item["exemption_limit"])
    slab_tax = _slab_tax(income, rules["slabs"], exemption_limit)
    rebate_rules = rules["rebate"]
    rebate = ZERO
    if data.is_resident and income <= int(rebate_rules["income_limit"]):
        rebate = min(slab_tax, Decimal(rebate_rules["maximum"]))
    elif data.is_resident and rebate_rules.get("marginal_relief"):
        excess_income = Decimal(income - int(rebate_rules["income_limit"]))
        rebate = max(ZERO, slab_tax - excess_income)
    tax_after_rebate = max(ZERO, slab_tax - rebate)

    surcharge_rate = ZERO
    surcharge_threshold = None
    for item in rules["surcharge"]:
        if income > int(item["above"]):
            surcharge_threshold = int(item["above"])
            surcharge_rate = Decimal(item["rate"])
    surcharge_before_relief = tax_after_rebate * surcharge_rate
    surcharge_relief = ZERO
    if surcharge_before_relief and surcharge_threshold is not None:
        threshold_tax = _slab_tax(surcharge_threshold, rules["slabs"], exemption_limit)
        maximum_tax_at_income = threshold_tax + Decimal(income - surcharge_threshold)
        surcharge_relief = max(ZERO, tax_after_rebate + surcharge_before_relief - maximum_tax_at_income)
    surcharge = max(ZERO, surcharge_before_relief - surcharge_relief)
    cess = (tax_after_rebate + surcharge) * Decimal(config["cess_rate"])
    final_tax = _round_to(tax_after_rebate + surcharge + cess, int(config["round_final_tax_to"]))

    legal_audit = audit + [
        {"rule_id": "ITAA_16_IA", "source_clause": "Income-tax Act, Section 16(ia)", "amount": _decimal_text(standard), "description": "Standard deduction."},
        {"rule_id": "ITAA_288A", "source_clause": "Income-tax Act, Section 288A", "amount": str(income), "description": "Total income rounded to the configured statutory unit."},
        {"rule_id": "FIRST_SCHEDULE", "source_clause": "Applicable Finance Act, First Schedule", "amount": _decimal_text(slab_tax), "description": "Tax computed progressively using the year/regime slab rates."},
        {"rule_id": "ITAA_87A", "source_clause": "Income-tax Act, Section 87A", "amount": _decimal_text(rebate), "description": "Section 87A rebate, including configured marginal relief."},
        {"rule_id": "SURCHARGE_MARGINAL_RELIEF", "source_clause": "Applicable Finance Act, First Schedule", "amount": _decimal_text(surcharge), "description": "Surcharge after marginal relief."},
        {"rule_id": "ITAA_2_11_CESS", "source_clause": "Applicable Finance Act, Section 2(11)", "amount": _decimal_text(cess), "description": "Health and education cess applied after rebate and surcharge."},
        {"rule_id": "ITAA_288B", "source_clause": "Income-tax Act, Section 288B", "amount": _decimal_text(final_tax), "description": "Final tax rounded to the configured statutory unit."},
    ]
    return {
        "regime": regime_name,
        "gross_salary": int(gross),
        "hra_exemption": _decimal_text(hra),
        "standard_deduction": _decimal_text(standard),
        "other_deductions": {name: _decimal_text(amount) for name, amount in allowed.items() if name != "hra_exemption"},
        "total_income_before_rounding": _decimal_text(unrounded_income),
        "taxable_income": income,
        "slab_tax": _decimal_text(slab_tax),
        "rebate": _decimal_text(rebate),
        "surcharge_rate": str(surcharge_rate),
        "surcharge": _decimal_text(surcharge),
        "surcharge_marginal_relief": _decimal_text(surcharge_relief),
        "cess": _decimal_text(cess),
        "tax_payable": int(final_tax),
        "audit": legal_audit,
        "sources": config["sources"],
        "configuration_version": config["version"],
        "configuration_checksum": config["config_checksum"],
    }


def calculate(data: TaxpayerInput, config: dict[str, Any] | None = None) -> dict[str, Any]:
    data.validate()
    missing = missing_tax_facts(data)
    if missing:
        raise IncompleteInputError(missing)
    active_config = config or load_config(data.financial_year)
    if active_config.get("financial_year") != data.financial_year:
        raise ValueError("Taxpayer financial year does not match the tax configuration")
    if not active_config.get("config_checksum") or configuration_checksum(active_config) != active_config["config_checksum"]:
        raise ValueError("Tax configuration checksum is missing or does not match its contents")
    old_result = _regime_result(data, active_config, "old")
    new_result = _regime_result(data, active_config, "new")
    preferred = "old" if old_result["tax_payable"] < new_result["tax_payable"] else "new"
    if old_result["tax_payable"] == new_result["tax_payable"]:
        preferred = "tie"
    return {
        "financial_year": data.financial_year,
        "old_regime": old_result,
        "new_regime": new_result,
        "preferred_regime": preferred,
        "difference_in_tax": abs(old_result["tax_payable"] - new_result["tax_payable"]),
        "configuration_version": active_config["version"],
        "configuration_checksum": active_config["config_checksum"],
        "scope": "salary income only; capital gains, business income and other income heads excluded",
    }
