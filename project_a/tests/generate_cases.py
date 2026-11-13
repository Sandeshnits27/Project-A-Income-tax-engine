from __future__ import annotations

import json
from pathlib import Path
import random

from project_a.engine import load_config
from project_a.tests.reference_oracle import calculate_pair

OUTPUT = Path(__file__).resolve().parent / "data" / "tax_cases.json"


def _complete_input(financial_year: str, salary: int, index: int, clean: bool = False) -> dict:
    parent_amount = (0, 25000, 40000, 60000)[index % 4]
    parent_ages = [] if parent_amount == 0 else ([55, 58] if index % 2 == 0 else [58, 64])
    data = {
        "financial_year": financial_year,
        "annual_salary": salary,
        "age": (35, 65, 82)[index % 3],
        "is_resident": True,
        "basic_salary": None,
        "eligible_da": None,
        "hra_received": 0,
        "annual_rent": None,
        "city_category": None,
        "section_80c": (0, 100000, 150000, 180000)[index % 4],
        "section_80ccd_1b": (0, 25000, 50000, 75000)[index % 4],
        "section_80d_self_family": (0, 20000, 30000, 60000)[index % 4],
        "section_80d_parents": parent_amount,
        "parents_ages": parent_ages,
        "home_loan_interest": (0, 100000, 200000, 250000)[index % 4],
        "home_loan_self_occupied": index % 3 != 0,
        "home_loan_eligible": index % 3 != 0,
    }
    if index % 5 == 0:
        data.update(
            basic_salary=int(salary * 0.55),
            eligible_da=0,
            hra_received=int(salary * 0.18),
            annual_rent=int(salary * 0.22),
            city_category="metro" if index % 10 == 0 else "non_metro",
        )
    if clean:
        data.update(
            basic_salary=None,
            eligible_da=None,
            hra_received=0,
            annual_rent=None,
            city_category=None,
            section_80c=0,
            section_80ccd_1b=0,
            section_80d_self_family=0,
            section_80d_parents=0,
            parents_ages=[],
            home_loan_interest=0,
            home_loan_self_occupied=False,
            home_loan_eligible=None,
        )
    return data


def build_cases() -> list[dict]:
    cases = []
    rng = random.Random(20261113)
    for year in ("FY_2024_25", "FY_2025_26"):
        config = load_config(year)
        old_targets = [250000, 300000, 500000, 1000000, 5000000, 10000000, 20000000, 50000000]
        new_targets = (
            [300000, 700000, 1000000, 1200000, 1500000, 5000000, 10000000, 20000000]
            if year == "FY_2024_25"
            else [400000, 800000, 1200000, 1600000, 2000000, 2400000, 5000000, 10000000, 20000000]
        )
        index = 0
        for regime_name, targets in (("old", old_targets), ("new", new_targets)):
            standard = config["regimes"][regime_name]["standard_deduction"]
            for target in targets:
                for offset in (-10, -1, 0, 1, 10):
                    salary = max(0, target + standard + offset)
                    data = _complete_input(year, salary, index, clean=True)
                    cases.append(_case(year, data, index))
                    index += 1

        edge_inputs = []
        for field, values in (
            ("section_80c", [149999, 150000, 150001]),
            ("section_80ccd_1b", [49999, 50000, 50001]),
            ("section_80d_self_family", [24999, 25000, 25001]),
            ("section_80d_parents", [24999, 25000, 25001]),
            ("section_80d_parents", [49999, 50000, 50001]),
            ("home_loan_interest", [199999, 200000, 200001]),
        ):
            for value in values:
                edge_inputs.append({field: value})
        edge_inputs.extend(
            [
                {"basic_salary": 500000, "eligible_da": 0, "hra_received": 99999, "annual_rent": 150000, "city_category": "metro"},
                {"basic_salary": 500000, "eligible_da": 0, "hra_received": 100000, "annual_rent": 150000, "city_category": "metro"},
            ]
        )
        for override in edge_inputs:
            data = _complete_input(year, 1_000_000, index, clean=True)
            data.update(override)
            if override.get("section_80d_parents"):
                data["parents_ages"] = [55, 58] if override["section_80d_parents"] <= 25001 else [58, 64]
            if override.get("home_loan_interest"):
                data["home_loan_self_occupied"] = True
                data["home_loan_eligible"] = True
            cases.append(_case(year, data, index))
            index += 1

        while index < 110:
            salary = rng.randint(180000, 60000000)
            data = _complete_input(year, salary, index)
            cases.append(_case(year, data, index))
            index += 1

    return cases


def _case(year: str, data: dict, index: int) -> dict:
    config = load_config(year)
    return {
        "case_id": f"{year[-2:]}_{index + 1:03d}",
        "financial_year": year,
        "input": data,
        **calculate_pair(data, config),
        "reference_status": "local_separate_reference_oracle; not official-calculator verified",
        "official_calculator": {"status": "not_checked", "date_checked": None, "notes": None},
    }


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    OUTPUT.write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {OUTPUT}")
