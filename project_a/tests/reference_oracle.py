"""Separate reference implementation used to freeze local expected tax values.

This is not an official-calculator result and has not been legally validated.
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _rounded(value: Decimal, unit: int) -> Decimal:
    return (value / Decimal(unit)).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal(unit)


def _income_tax(income: int, brackets: list[dict[str, Any]], exemption: int) -> Decimal:
    tax = Decimal(0)
    lower_bound = exemption
    for bracket in brackets:
        upper_bound = bracket["up_to"]
        if upper_bound is not None and upper_bound <= lower_bound:
            continue
        if upper_bound is None:
            portion = max(income - lower_bound, 0)
            tax += Decimal(portion) * Decimal(bracket["rate"])
            break
        if income <= lower_bound:
            break
        portion = min(income, upper_bound) - lower_bound
        tax += Decimal(max(0, portion)) * Decimal(bracket["rate"])
        lower_bound = upper_bound
    return tax


def _compute(data: dict[str, Any], config: dict[str, Any], regime_name: str) -> int:
    regime = config["regimes"][regime_name]
    deductions: dict[str, Decimal] = {}
    exempt_hra = Decimal(0)
    if regime_name == "old":
        if data["hra_received"]:
            salary_component = Decimal(data["basic_salary"] + data["eligible_da"])
            percentage = Decimal(config["hra"]["metro_cap_fraction"] if data["city_category"] == "metro" else config["hra"]["non_metro_cap_fraction"])
            ceiling_by_rent = max(Decimal(0), Decimal(data["annual_rent"]) - salary_component * Decimal(config["hra"]["rent_less_salary_fraction"]))
            exempt_hra = min(Decimal(data["hra_received"]), ceiling_by_rent, salary_component * percentage)
        caps = regime["deductions"]
        parent_senior = any(age >= 60 for age in (data.get("parents_ages") or []))
        deductions["80C"] = min(Decimal(data["section_80c"]), Decimal(caps["80c"]))
        deductions["80CCD(1B)"] = min(Decimal(data["section_80ccd_1b"]), Decimal(caps["80ccd_1b"]))
        deductions["80D:self"] = min(Decimal(data["section_80d_self_family"]), Decimal(caps["80d_self_senior" if data["age"] >= 60 else "80d_self_regular"]))
        deductions["80D:parents"] = min(Decimal(data["section_80d_parents"]), Decimal(caps["80d_parent_senior" if parent_senior else "80d_parent_regular"]))
        mortgage_cap = caps["home_loan_interest"] if data["home_loan_self_occupied"] and data["home_loan_eligible"] else 0
        deductions["24(b)"] = min(Decimal(data["home_loan_interest"]), Decimal(mortgage_cap))
    taxable_before_rounding = max(
        Decimal(0),
        Decimal(data["annual_salary"]) - exempt_hra - Decimal(regime["standard_deduction"]) - sum(deductions.values(), Decimal(0)),
    )
    taxable = int(_rounded(taxable_before_rounding, config["round_total_income_to"]))

    basic_exemption = 0
    if regime_name == "old":
        for tier in regime.get("senior_slabs", []):
            if data["age"] >= tier["minimum_age"]:
                basic_exemption = tier["exemption_limit"]
    tax_before_rebate = _income_tax(taxable, regime["slabs"], basic_exemption)
    rebate_terms = regime["rebate"]
    if data["is_resident"] and taxable <= rebate_terms["income_limit"]:
        relief = min(tax_before_rebate, Decimal(rebate_terms["maximum"]))
    elif data["is_resident"] and rebate_terms.get("marginal_relief", False):
        relief = max(Decimal(0), tax_before_rebate - Decimal(taxable - rebate_terms["income_limit"]))
    else:
        relief = Decimal(0)
    taxable_tax = max(Decimal(0), tax_before_rebate - relief)

    rate = Decimal(0)
    cutoff = None
    for tier in regime["surcharge"]:
        if taxable > tier["above"]:
            cutoff = tier["above"]
            rate = Decimal(tier["rate"])
    add_on = taxable_tax * rate
    allowed_relief = Decimal(0)
    if add_on and cutoff is not None:
        boundary_tax = _income_tax(cutoff, regime["slabs"], basic_exemption)
        absolute_cap = boundary_tax + Decimal(taxable - cutoff)
        allowed_relief = max(Decimal(0), taxable_tax + add_on - absolute_cap)
    surcharge = max(Decimal(0), add_on - allowed_relief)
    payable_before_rounding = taxable_tax + surcharge + (taxable_tax + surcharge) * Decimal(config["cess_rate"])
    return int(_rounded(payable_before_rounding, config["round_final_tax_to"]))


def calculate_pair(data: dict[str, Any], config: dict[str, Any]) -> dict[str, int | str]:
    old_tax = _compute(data, config, "old")
    new_tax = _compute(data, config, "new")
    preferred = "old" if old_tax < new_tax else "new" if new_tax < old_tax else "tie"
    return {"expected_old_regime_tax": old_tax, "expected_new_regime_tax": new_tax, "preferred_regime": preferred}
