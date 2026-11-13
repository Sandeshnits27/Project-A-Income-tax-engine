from __future__ import annotations

from project_a.engine import missing_tax_facts
from project_a.models import TaxpayerInput


QUESTIONS = {
    "financial_year": "Which financial year should I use: FY 2024–25 or FY 2025–26?",
    "annual_salary": "What is your annual gross salary for this financial year, before exemptions and deductions?",
    "age": "What was your age at the end of the financial year?",
    "is_resident": "Were you a resident of India for tax purposes in this financial year?",
    "hra_received": "How much HRA did you receive during the year? Enter 0 if none.",
    "basic_salary": "What was your annual basic salary?",
    "eligible_da": "How much dearness allowance is included in salary for HRA purposes? Enter 0 if none.",
    "annual_rent": "What was the total rent paid for the year?",
    "city_category": "Was the residence in Mumbai, Delhi, Kolkata, or Chennai (metro for this calculation), or another city?",
    "section_80c": "What amount of eligible Section 80C investments/payments should be considered? Enter 0 if none.",
    "section_80ccd_1b": "What eligible Section 80CCD(1B) contribution should be considered? Enter 0 if none.",
    "section_80d_self_family": "What eligible Section 80D self/family amount should be considered? Enter 0 if none.",
    "section_80d_parents": "What eligible Section 80D parent amount should be considered? Enter 0 if none.",
    "parents_ages": "What are the ages of the parents covered by the Section 80D amount?",
    "home_loan_interest": "What eligible self-occupied home-loan interest amount should be considered? Enter 0 if none.",
    "home_loan_self_occupied": "Was the home self-occupied during the financial year?",
    "home_loan_eligible": "Have you confirmed that the interest satisfies the modeled Section 24(b) eligibility conditions?",
}


def clarification_questions(missing: list[str]) -> list[str]:
    return [QUESTIONS.get(field, f"Please clarify {field.replace('_', ' ')}.") for field in missing]


def validation_result(data: TaxpayerInput) -> dict[str, list[str] | bool]:
    missing = missing_tax_facts(data)
    return {"valid_for_calculation": not missing, "missing_fields": missing, "conflicts": []}
