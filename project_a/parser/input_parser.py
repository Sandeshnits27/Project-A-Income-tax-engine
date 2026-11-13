from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from project_a.models import TaxpayerInput
from project_a.parser.validator import clarification_questions, validation_result

AMOUNT = r"(?P<amount>₹?\s*\d+(?:[,.]\d+)*\s*(?:crores?|cr|lakhs?|lacs?|lpa|l(?![a-z])|thousand|hazaar|k)?)"


def _number(raw: str) -> int:
    cleaned = raw.lower().replace(",", "").replace("₹", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lacs?|lpa|l|thousand|hazaar|k)?", cleaned)
    if not match:
        raise ValueError(f"Could not parse rupee amount: {raw}")
    amount = Decimal(match.group(1))
    unit = match.group(2) or ""
    multiplier = 1
    if unit in {"crore", "crores", "cr"}:
        multiplier = 10_000_000
    elif unit in {"lakh", "lakhs", "lac", "lacs", "lpa", "l"}:
        multiplier = 100_000
    elif unit in {"thousand", "hazaar", "k"}:
        multiplier = 1_000
    result = amount * multiplier
    if result != result.to_integral_value():
        raise ValueError(f"Rupee amount must resolve to a whole rupee: {raw}")
    return int(result)


def _capture(text: str, pattern: str) -> tuple[int, str] | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return _number(match.group("amount")), text[match.start():match.end()]


def _generic_salary(text: str) -> tuple[int, str] | None:
    pattern = rf"\b(?<!basic\s)(?<!monthly\s)salary\s*(?:is|of|:)?\s*{AMOUNT}\s*(?:per\s+year|annually|annual)?"
    monthly_suffix = re.compile(r"\s*(?:crores?|cr|lakhs?|lacs?|lpa|l|thousand|hazaar|k)?\s*(?:per\s+month|monthly)\b", re.I)
    for match in re.finditer(pattern, text, re.I):
        if monthly_suffix.match(text[match.end():]):
            continue
        return _number(match.group("amount")), text[match.start():match.end()]
    return None


def parse_text(text: str, financial_year: str | None = None) -> dict[str, Any]:
    facts: dict[str, Any] = {"financial_year": financial_year, "evidence": {}}
    conflicts: list[str] = []

    year_matches = list(re.finditer(r"\b(?:FY\s*)?(2024|2025)\s*[-–/]\s*(?:20)?(25|26)\b", text, re.I))
    year_values = {"FY_2024_25" if match.group(1) == "2024" else "FY_2025_26" for match in year_matches}
    if len(year_values) > 1:
        conflicts.append("The description names more than one financial year.")
    if financial_year is None and len(year_values) == 1:
        facts["financial_year"] = next(iter(year_values))
    elif financial_year and year_values and year_values != {financial_year}:
        conflicts.append(f"Provided financial year {financial_year} conflicts with the description.")

    age = re.search(r"\b(?:my\s+)?age\s*(?:is|of|:)?\s*(\d{1,3})\b", text, re.I)
    if age:
        facts["age"] = int(age.group(1))
        facts["evidence"]["age"] = age.group(0)
    if re.search(r"\b(?:NRI|non[- ]resident|not a resident)\b", text, re.I):
        facts["is_resident"] = False
        facts["evidence"]["is_resident"] = "explicitly stated non-resident"
    elif re.search(r"\b(?:resident Indian|Indian resident|I am resident|I am a resident)\b", text, re.I):
        facts["is_resident"] = True
        facts["evidence"]["is_resident"] = "explicitly stated resident"

    annual_salary = _capture(text, rf"\b(?:annual|yearly|gross)\s+salary\s*(?:is|of|:)?\s*{AMOUNT}")
    if annual_salary is None:
        annual_salary = _capture(text, rf"\bannual\s+pay\s*(?:is|of|:)?\s*{AMOUNT}")
    if annual_salary is None:
        annual_salary = _generic_salary(text)
    ctc = _capture(text, rf"\bCTC\s*(?:is|of|:)?\s*{AMOUNT}")
    if ctc:
        conflicts.append("CTC is not necessarily taxable salary; confirm annual taxable salary and salary breakup.")
        if annual_salary is None:
            annual_salary = ctc
    lpa = _capture(text, rf"\b(?P<amount>\d+(?:[,.]\d+)*\s*(?:LPA|per\s+annum))\b")
    if annual_salary is None and lpa:
        annual_salary = lpa
    monthly_salary = _capture(text, rf"\bmonthly\s+salary\s*(?:is|of|:)?\s*{AMOUNT}")
    if monthly_salary is None:
        monthly_salary = _capture(text, rf"\bsalary\s*(?:is|of|:)?\s*{AMOUNT}\s*(?:per\s+month|monthly)\b")
    annual_from_monthly = monthly_salary[0] * 12 if monthly_salary else None
    if annual_salary:
        facts["annual_salary"] = annual_salary[0]
        facts["evidence"]["annual_salary"] = annual_salary[1]
    if annual_from_monthly is not None:
        if annual_salary and annual_salary[0] != annual_from_monthly:
            conflicts.append(f"Annual salary ({annual_salary[0]}) conflicts with monthly salary annualized to {annual_from_monthly}.")
        elif not annual_salary:
            facts["annual_salary"] = annual_from_monthly
            facts["evidence"]["annual_salary"] = monthly_salary[1]

    hra_pattern = rf"\b(?:HRA|house\s+rent\s+allowance)\s*(?:(?:received|is|of|:)\s*)?{AMOUNT}"
    for field, pattern in (
        ("hra_received", hra_pattern),
        ("basic_salary", rf"\bbasic(?:\s+salary)?\s*(?:is|of|:)?\s*{AMOUNT}"),
        ("eligible_da", rf"\b(?:eligible\s+)?DA\s*(?:is|of|:)?\s*{AMOUNT}"),
        ("section_80c", rf"\b80\s*C\s*(?:investment|contribution|is|of|:)?\s*{AMOUNT}"),
        ("section_80ccd_1b", rf"\b(?:80\s*CCD\s*\(?\s*1B\s*\)?|NPS)\s*(?:contribution|is|of|:)?\s*{AMOUNT}"),
        ("section_80d_parents", rf"\bparents?['’]?\s+(?:health\s+)?insurance\s*(?:is|of|:)?\s*{AMOUNT}"),
        ("section_80d_self_family", rf"\b(?:self|my|family)\s+(?:health\s+)?insurance\s*(?:is|of|:)?\s*{AMOUNT}"),
        ("home_loan_interest", rf"\b(?:home|house)\s+loan\s+interest\s*(?:is|of|:)?\s*{AMOUNT}"),
    ):
        value = _capture(text, pattern)
        if value is not None:
            facts[field] = value[0]
            facts["evidence"][field] = value[1]

    hra_matches = list(re.finditer(hra_pattern, text, re.I))
    if len({_number(match.group("amount")) for match in hra_matches}) > 1:
        conflicts.append("The description gives different HRA amounts; confirm the correct amount.")

    rent_pattern = rf"\b(?:monthly\s+)?rent\s*(?:paid|is|of|:)?\s*{AMOUNT}"
    rent_matches = list(re.finditer(rent_pattern, text, re.I))
    rent_values = [_number(match.group("amount")) for match in rent_matches]
    if len(set(rent_values)) > 1:
        conflicts.append("The description gives different rent amounts; confirm the correct amount and period.")
    rent = _capture(text, rent_pattern)
    if rent:
        amount_start = text.lower().find(rent[1].lower())
        month_indicator = re.search(r"\b(?:monthly|per\s+month|a\s+month|mahina)\b", text[max(0, amount_start - 16):amount_start + len(rent[1]) + 40], re.I)
        facts["annual_rent"] = rent[0] * 12 if month_indicator else rent[0]
        facts["evidence"]["annual_rent"] = rent[1]
    city = re.search(r"\b(Mumbai|Delhi|New Delhi|Kolkata|Calcutta|Chennai|Madras|Bengaluru|Bangalore|Hyderabad|Pune)\b", text, re.I)
    if city:
        facts["city_category"] = "metro" if city.group(1).lower() in {"mumbai", "delhi", "new delhi", "kolkata", "calcutta", "chennai", "madras"} else "non_metro"
        facts["evidence"]["city_category"] = city.group(0)

    parent_ages = re.search(
        r"\bparents?\b.{0,40}?\b(?:aged?|ages?)\s+(\d{1,3})\s*(?:and|&|,)\s*(\d{1,3})|"
        r"\bparents?\b.{0,40}?\bthey\s+are\s+(\d{1,3})\s*(?:and|&|,)\s*(\d{1,3})|"
        r"\b(?:mummy|mom|mother)\s+(\d{1,3})\s*(?:and|,|aur)\s*(?:papa|dad|father)\s+(\d{1,3})",
        text,
        re.I,
    )
    if parent_ages:
        if parent_ages.group(1):
            ages = parent_ages.group(1, 2)
        elif parent_ages.group(3):
            ages = parent_ages.group(3, 4)
        else:
            ages = parent_ages.group(5, 6)
        facts["parents_ages"] = [int(ages[0]), int(ages[1])]
        facts["evidence"]["parents_ages"] = parent_ages.group(0)

    zero_phrases = {
        "section_80c": r"(?:\b(?:no|zero|nil)\s+(?:eligible\s+)?80\s*C\b|\b80\s*C\s*(?:is\s*)?(?:0|zero|nil|none)\b)",
        "section_80ccd_1b": r"(?:\b(?:no|zero|nil)\s+(?:eligible\s+)?(?:80\s*CCD\s*\(?\s*1B\s*\)?|NPS)\b|\b(?:80\s*CCD\s*\(?\s*1B\s*\)?|NPS)\s*(?:is\s*)?(?:0|zero|nil|none)\b)",
        "section_80d_self_family": r"(?:\b(?:no|zero|nil)\s+(?:(?:self|family)(?:\s*/\s*(?:self|family))?\s+)?(?:80\s*D|health\s+insurance)\b|\b(?:self|family)(?:\s*/\s*(?:self|family))?\s+(?:80\s*D|health\s+insurance)\s*(?:is\s*)?(?:0|zero|nil|none)\b)",
        "section_80d_parents": r"(?:\b(?:no|zero|nil)\s+(?:parents?|parent)\s+(?:80\s*D|health\s+insurance)\b|\b(?:parents?|parent)\s+(?:80\s*D|health\s+insurance)\s*(?:is\s*)?(?:0|zero|nil|none)\b)",
        "home_loan_interest": r"(?:\b(?:no|zero|nil)\s+(?:home[- ]loan|house[- ]loan)\s+interest\b|\b(?:home[- ]loan|house[- ]loan)\s+interest\s*(?:is\s*)?(?:0|zero|nil|none)\b)",
        "hra_received": r"(?:\b(?:no|zero|nil)\s+(?:HRA|house\s+rent\s+allowance)\b|\b(?:HRA|house\s+rent\s+allowance)\s*(?:is\s*)?(?:0|zero|nil|none)\b)",
    }
    for amount_field, zero_pattern in zero_phrases.items():
        phrase = {
            "section_80c": "80C",
            "section_80ccd_1b": "80CCD(1B)",
            "section_80d_self_family": "self/family 80D",
            "section_80d_parents": "parents 80D",
            "home_loan_interest": "home-loan interest",
            "hra_received": "HRA",
        }[amount_field]
        if re.search(zero_pattern, text, re.I):
            if facts.get(amount_field, 0) > 0:
                conflicts.append(f"The description both gives a positive {phrase} amount and says there is none.")
            elif amount_field not in facts:
                facts[amount_field] = 0
                facts["evidence"][amount_field] = f"explicitly stated no {phrase}"
    if re.search(r"\b(?:own|self[- ]occupied)\s+(?:a\s+)?house\b|\bhome\s+loan\b", text, re.I):
        if "home_loan_self_occupied" not in facts:
            says_not_self_occupied = bool(re.search(r"\b(?:not|isn't|is\s+not)\s+self[- ]occupied\b|\blet\s+out\b", text, re.I))
            facts["home_loan_self_occupied"] = bool(re.search(r"\bself[- ]occupied\b", text, re.I)) and not says_not_self_occupied
    if re.search(r"\b(?:home[- ]loan|section\s*24\s*\(?b\)?)\b.{0,30}\bnot\s+eligible\b", text, re.I):
        facts["home_loan_eligible"] = False
    elif re.search(r"\b(?:home[- ]loan|section\s*24\s*\(?b\)?)\b.{0,30}\b(?:eligible|meets\s+the\s+conditions)\b", text, re.I):
        facts["home_loan_eligible"] = True
    if facts.get("home_loan_interest", 0) > 0 and re.search(r"\b(?:do\s+not|don't|no)\s+(?:have\s+)?(?:a\s+)?home\s+loan\b", text, re.I):
        conflicts.append("The description gives home-loan interest but also says there is no home loan.")
    if re.search(r"\bsalary\s+(?:is\s+)?minus\s+\d", text, re.I):
        conflicts.append("Salary cannot be negative; please confirm the intended amount.")

    required_missing = [name for name in ("financial_year", "annual_salary", "age") if facts.get(name) is None]
    draft = dict(facts)
    for name in ("section_80c", "section_80ccd_1b", "section_80d_self_family", "section_80d_parents", "home_loan_interest", "hra_received"):
        draft.setdefault(name, None)
    draft.setdefault("is_resident", None)
    draft.setdefault("eligible_da", None)
    if "parents_ages" not in draft:
        draft["parents_ages"] = None
    if "home_loan_self_occupied" not in draft:
        draft["home_loan_self_occupied"] = None
    if "home_loan_eligible" not in draft:
        draft["home_loan_eligible"] = None
    try:
        tax_input = None if required_missing else TaxpayerInput.from_dict(draft)
        check = {"missing_fields": []} if tax_input is None else validation_result(tax_input)
        missing = required_missing + check["missing_fields"]
    except (TypeError, ValueError) as error:
        conflicts.append(str(error))
        missing = required_missing
    missing = list(dict.fromkeys(missing))
    questions = clarification_questions(missing)
    if conflicts:
        questions.extend(conflicts)
    return {
        "extracted_fields": {key: value for key, value in facts.items() if key != "evidence"},
        "evidence": facts["evidence"],
        "missing_fields": missing,
        "conflicts": conflicts,
        "clarification_needed": bool(missing or conflicts),
        "questions": questions,
    }
