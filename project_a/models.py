from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any


AMOUNT_FIELDS = {
    "annual_salary",
    "basic_salary",
    "eligible_da",
    "hra_received",
    "annual_rent",
    "section_80c",
    "section_80ccd_1b",
    "section_80d_self_family",
    "section_80d_parents",
    "home_loan_interest",
}


@dataclass(frozen=True)
class TaxpayerInput:
    financial_year: str
    annual_salary: int
    age: int
    is_resident: bool | None = None
    basic_salary: int | None = None
    eligible_da: int | None = None
    hra_received: int | None = None
    annual_rent: int | None = None
    city_category: str | None = None
    section_80c: int | None = None
    section_80ccd_1b: int | None = None
    section_80d_self_family: int | None = None
    section_80d_parents: int | None = None
    parents_ages: tuple[int, ...] | None = None
    home_loan_interest: int | None = None
    home_loan_self_occupied: bool | None = None
    home_loan_eligible: bool | None = None
    evidence: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaxpayerInput":
        allowed = {field.name for field in fields(cls)}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"Unknown input fields: {', '.join(sorted(unknown))}")
        values = dict(raw)
        if "parents_ages" in values and values["parents_ages"] is not None:
            values["parents_ages"] = tuple(values["parents_ages"])
        instance = cls(**values)
        instance.validate()
        return instance

    def validate(self) -> None:
        if self.financial_year not in {"FY_2024_25", "FY_2025_26"}:
            raise ValueError("financial_year must be FY_2024_25 or FY_2025_26")
        for name in AMOUNT_FIELDS:
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative whole-rupee amount")
        if isinstance(self.annual_salary, bool) or not isinstance(self.annual_salary, int):
            raise ValueError("annual_salary must be a non-negative whole-rupee amount")
        if not isinstance(self.age, int) or isinstance(self.age, bool) or not 0 <= self.age <= 120:
            raise ValueError("age must be an integer from 0 to 120")
        for name in AMOUNT_FIELDS - {"annual_salary"}:
            value = getattr(self, name)
            if value is not None and not isinstance(value, int):
                raise ValueError(f"{name} must be a whole-rupee integer or null")
        if self.city_category is not None and self.city_category not in {"metro", "non_metro"}:
            raise ValueError("city_category must be metro or non_metro")
        if self.home_loan_self_occupied is not None and not isinstance(self.home_loan_self_occupied, bool):
            raise ValueError("home_loan_self_occupied must be a boolean or null")
        if self.is_resident is not None and not isinstance(self.is_resident, bool):
            raise ValueError("is_resident must be a boolean or null")
        if self.home_loan_eligible is not None and not isinstance(self.home_loan_eligible, bool):
            raise ValueError("home_loan_eligible must be a boolean or null")
        if self.parents_ages is not None and any(not isinstance(age, int) or not 0 <= age <= 120 for age in self.parents_ages):
            raise ValueError("parents_ages must contain integer ages from 0 to 120")
        if self.basic_salary is not None and self.eligible_da is not None and self.basic_salary + self.eligible_da > self.annual_salary:
            raise ValueError("basic salary plus eligible DA cannot exceed annual_salary")

    def as_dict(self) -> dict[str, Any]:
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        if self.parents_ages is not None:
            values["parents_ages"] = list(self.parents_ages)
        return values


def rupees(value: int | Decimal) -> Decimal:
    return Decimal(value)
