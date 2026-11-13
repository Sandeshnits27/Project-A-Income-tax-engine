from __future__ import annotations

import json
import unittest
from pathlib import Path
from decimal import Decimal
from copy import deepcopy

from project_a.compiled_engine.calculator import calculate_tax as compiled_calculate
from project_a.engine import IncompleteInputError, calculate
from project_a.models import TaxpayerInput
from project_a.rules_engine.calculator import calculate_tax as rules_calculate
from project_a.tests.reference_oracle import calculate_pair

ROOT = Path(__file__).resolve().parents[1]
TAX_CASES = ROOT / "tests" / "data" / "tax_cases.json"
LANGUAGE_CASES = ROOT / "data" / "natural_language_cases.json"


class TaxEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tax_cases = json.loads(TAX_CASES.read_text(encoding="utf-8"))

    def test_all_220_frozen_cases_match_separate_oracle(self):
        self.assertEqual(len(self.tax_cases), 220)
        from project_a.engine import load_config

        for case in self.tax_cases:
            with self.subTest(case=case["case_id"]):
                data = TaxpayerInput.from_dict(case["input"])
                oracle = calculate_pair(case["input"], load_config(case["financial_year"]))
                self.assertEqual(oracle["expected_old_regime_tax"], case["expected_old_regime_tax"])
                self.assertEqual(oracle["expected_new_regime_tax"], case["expected_new_regime_tax"])
                result = rules_calculate(data)
                self.assertEqual(result["old_regime"]["tax_payable"], case["expected_old_regime_tax"])
                self.assertEqual(result["new_regime"]["tax_payable"], case["expected_new_regime_tax"])
                self.assertEqual(result["preferred_regime"], case["preferred_regime"])

    def test_compiled_configuration_matches_rules_for_entire_suite(self):
        for case in self.tax_cases:
            with self.subTest(case=case["case_id"]):
                result = compiled_calculate(TaxpayerInput.from_dict(case["input"]))
                self.assertEqual(result["old_regime"]["tax_payable"], case["expected_old_regime_tax"])
                self.assertEqual(result["new_regime"]["tax_payable"], case["expected_new_regime_tax"])

    def test_query_time_engine_clarifies_before_calling_model(self):
        from project_a.llm_engine.calculator import calculate_tax as llm_calculate

        data = TaxpayerInput.from_dict(
            {"financial_year": "FY_2024_25", "annual_salary": 1_000_000, "age": 35}
        )
        response = llm_calculate(data)
        self.assertTrue(response["clarification_needed"])
        self.assertFalse(response["llm_metadata"]["api_called"])
        self.assertIn("is_resident", response["missing_fields"])

    def test_compiled_rule_candidate_validator_rejects_unordered_slabs(self):
        from project_a.compiled_engine.rule_generator import validate_compiled_config
        from project_a.engine import load_config

        config = deepcopy(load_config("FY_2024_25"))
        validate_compiled_config(config, "FY_2024_25")
        config.pop("config_checksum")
        config["regimes"]["old"]["slabs"][0]["up_to"] = 600_000
        with self.assertRaisesRegex(ValueError, "unordered"):
            validate_compiled_config(config, "FY_2024_25")

    def test_tax_engine_rejects_tampered_config(self):
        from copy import deepcopy
        from project_a.engine import load_config

        case = self.tax_cases[100]
        data = TaxpayerInput.from_dict(case["input"])
        config = deepcopy(load_config(data.financial_year))
        config["regimes"]["new"]["standard_deduction"] += 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            calculate(data, config)

    def test_unknown_deduction_is_not_silently_zero(self):
        data = TaxpayerInput.from_dict(
            {
                "financial_year": "FY_2024_25",
                "annual_salary": 1_000_000,
                "age": 35,
                "hra_received": 0,
            }
        )
        with self.assertRaises(IncompleteInputError) as error:
            calculate(data)
        self.assertIn("section_80c", error.exception.missing)
        self.assertIn("section_80ccd_1b", error.exception.missing)

    def test_parent_80d_amount_requires_parent_ages(self):
        data = TaxpayerInput.from_dict(
            {
                "financial_year": "FY_2024_25",
                "annual_salary": 1_000_000,
                "age": 35,
                "is_resident": True,
                "hra_received": 0,
                "section_80c": 0,
                "section_80ccd_1b": 0,
                "section_80d_self_family": 0,
                "section_80d_parents": 10_000,
                "home_loan_interest": 0,
                "home_loan_self_occupied": False,
                "home_loan_eligible": None,
            }
        )
        with self.assertRaises(IncompleteInputError) as error:
            calculate(data)
        self.assertIn("parents_ages", error.exception.missing)

    def test_self_80d_cap_changes_for_senior_taxpayer(self):
        base = {
            "financial_year": "FY_2024_25",
            "annual_salary": 1_000_000,
            "is_resident": True,
            "hra_received": 0,
            "section_80c": 0,
            "section_80ccd_1b": 0,
            "section_80d_self_family": 50_000,
            "section_80d_parents": 0,
            "parents_ages": [],
            "home_loan_interest": 0,
            "home_loan_self_occupied": False,
            "home_loan_eligible": None,
        }
        adult = calculate(TaxpayerInput.from_dict({**base, "age": 59}))
        senior = calculate(TaxpayerInput.from_dict({**base, "age": 60}))
        self.assertEqual(adult["old_regime"]["other_deductions"]["section_80d_self_family"], "25000")
        self.assertEqual(senior["old_regime"]["other_deductions"]["section_80d_self_family"], "50000")

    def test_hra_exemption_is_minimum_of_three_limits(self):
        data = TaxpayerInput.from_dict(
            {
                "financial_year": "FY_2024_25",
                "annual_salary": 1_200_000,
                "age": 35,
                "is_resident": True,
                "basic_salary": 600_000,
                "eligible_da": 0,
                "hra_received": 240_000,
                "annual_rent": 240_000,
                "city_category": "metro",
                "section_80c": 0,
                "section_80ccd_1b": 0,
                "section_80d_self_family": 0,
                "section_80d_parents": 0,
                "parents_ages": [],
                "home_loan_interest": 0,
                "home_loan_self_occupied": False,
                "home_loan_eligible": None,
            }
        )
        result = calculate(data)
        self.assertEqual(result["old_regime"]["hra_exemption"], "180000")
        self.assertEqual(result["new_regime"]["hra_exemption"], "0")

    def test_old_regime_senior_age_thresholds_are_applied(self):
        base = {
            "financial_year": "FY_2024_25",
            "annual_salary": 700_000,
            "is_resident": True,
            "hra_received": 0,
            "section_80c": 0,
            "section_80ccd_1b": 0,
            "section_80d_self_family": 0,
            "section_80d_parents": 0,
            "parents_ages": [],
            "home_loan_interest": 0,
            "home_loan_self_occupied": False,
            "home_loan_eligible": None,
        }
        ordinary = calculate(TaxpayerInput.from_dict({**base, "age": 59}))
        senior = calculate(TaxpayerInput.from_dict({**base, "age": 60}))
        super_senior = calculate(TaxpayerInput.from_dict({**base, "age": 80}))
        self.assertGreater(ordinary["old_regime"]["tax_payable"], senior["old_regime"]["tax_payable"])
        self.assertGreater(senior["old_regime"]["tax_payable"], super_senior["old_regime"]["tax_payable"])

    def test_section_87a_rebate_requires_resident_status(self):
        base = {
            "financial_year": "FY_2024_25",
            "annual_salary": 550_000,
            "age": 35,
            "hra_received": 0,
            "section_80c": 0,
            "section_80ccd_1b": 0,
            "section_80d_self_family": 0,
            "section_80d_parents": 0,
            "parents_ages": [],
            "home_loan_interest": 0,
            "home_loan_self_occupied": False,
            "home_loan_eligible": None,
        }
        resident = calculate(TaxpayerInput.from_dict({**base, "is_resident": True}))
        non_resident = calculate(TaxpayerInput.from_dict({**base, "is_resident": False}))
        self.assertEqual(resident["old_regime"]["tax_payable"], 0)
        self.assertGreater(non_resident["old_regime"]["tax_payable"], 0)

    def test_new_regime_rebate_marginal_relief_above_12_lakh(self):
        data = TaxpayerInput.from_dict(
            {
                "financial_year": "FY_2025_26",
                "annual_salary": 1_200_000 + 10 + 75_000,
                "age": 35,
                "is_resident": True,
                "hra_received": 0,
                "section_80c": 0,
                "section_80ccd_1b": 0,
                "section_80d_self_family": 0,
                "section_80d_parents": 0,
                "parents_ages": [],
                "home_loan_interest": 0,
                "home_loan_self_occupied": False,
                "home_loan_eligible": None,
            }
        )
        result = calculate(data)["new_regime"]
        self.assertEqual(result["taxable_income"], 1_200_010)
        self.assertEqual(result["slab_tax"], "60001.5")
        self.assertEqual(Decimal(result["slab_tax"]) - Decimal(result["rebate"]), Decimal(10))
        self.assertEqual(result["tax_payable"], 10)

    def test_surcharge_marginal_relief_caps_tax_at_threshold(self):
        data = TaxpayerInput.from_dict(
            {
                "financial_year": "FY_2024_25",
                "annual_salary": 50_000_010 + 50_000,
                "age": 35,
                "is_resident": True,
                "hra_received": 0,
                "section_80c": 0,
                "section_80ccd_1b": 0,
                "section_80d_self_family": 0,
                "section_80d_parents": 0,
                "parents_ages": [],
                "home_loan_interest": 0,
                "home_loan_self_occupied": False,
                "home_loan_eligible": None,
            }
        )
        old = calculate(data)["old_regime"]
        self.assertEqual(old["taxable_income"], 50_000_010)
        self.assertGreater(Decimal(old["surcharge_marginal_relief"]), 0)
        from project_a.engine import load_config
        from project_a.tests.reference_oracle import _income_tax

        config = load_config("FY_2024_25")
        threshold_tax = _income_tax(50_000_000, config["regimes"]["old"]["slabs"], 0)
        self.assertLessEqual(Decimal(old["slab_tax"]) + Decimal(old["surcharge"]), threshold_tax + 10)

    def test_new_regime_surcharge_is_capped_at_25_percent(self):
        for year in ("FY_2024_25", "FY_2025_26"):
            data = TaxpayerInput.from_dict(
                {
                    "financial_year": year,
                    "annual_salary": 60_000_000 + 75_000,
                    "age": 35,
                    "is_resident": True,
                    "hra_received": 0,
                    "section_80c": 0,
                    "section_80ccd_1b": 0,
                    "section_80d_self_family": 0,
                    "section_80d_parents": 0,
                    "parents_ages": [],
                    "home_loan_interest": 0,
                    "home_loan_self_occupied": False,
                    "home_loan_eligible": None,
                }
            )
            result = calculate(data)
            with self.subTest(year=year):
                self.assertEqual(result["new_regime"]["surcharge_rate"], "0.25")
                self.assertEqual(result["old_regime"]["surcharge_rate"], "0.37")

    def test_audit_has_rule_ids_and_provision_sources(self):
        case = self.tax_cases[100]
        result = calculate(TaxpayerInput.from_dict(case["input"]))
        self.assertTrue(result["old_regime"]["audit"])
        self.assertTrue(all(item.get("rule_id") for item in result["old_regime"]["audit"]))
        self.assertTrue(all(item.get("source_clause") for item in result["old_regime"]["audit"]))
        self.assertTrue(all(item.get("url") for item in result["old_regime"]["sources"]))
        self.assertEqual(len(result["configuration_checksum"]), 64)


class PlainLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshots = json.loads(LANGUAGE_CASES.read_text(encoding="utf-8"))

    def test_50_parser_snapshots_remain_stable(self):
        from project_a.parser.input_parser import parse_text

        self.assertEqual(len(self.snapshots), 50)
        for case in self.snapshots:
            with self.subTest(case=case["case_id"]):
                actual = parse_text(case["text"])
                self.assertEqual(actual["extracted_fields"], case["expected_extracted_fields"])
                self.assertEqual(actual["missing_fields"], case["expected_missing_fields"])
                self.assertEqual(actual["conflicts"], case["expected_conflicts"])
                self.assertEqual(actual["clarification_needed"], case["expected_clarification"])

    def test_complete_english_examples_are_calculation_ready(self):
        complete = [case for case in self.snapshots if case["category"] == "clear_english"]
        self.assertEqual(len(complete), 10)
        self.assertTrue(all(not case["expected_clarification"] for case in complete))

    def test_unknown_and_explicit_zero_are_distinct(self):
        from project_a.parser.input_parser import parse_text

        unknown = parse_text("FY 2024-25 salary 10 lakh, age 35.")
        explicit = parse_text("FY 2024-25 salary 10 lakh, age 35, no 80C.")
        self.assertNotIn("section_80c", unknown["extracted_fields"])
        self.assertEqual(explicit["extracted_fields"]["section_80c"], 0)

    def test_hinglish_amount_units_and_monthly_rent(self):
        from project_a.parser.input_parser import parse_text

        parsed = parse_text("FY 2025-26 salary 18 LPA, age 40, rent 20 hazaar mahina.")
        fields = parsed["extracted_fields"]
        self.assertEqual(fields["annual_salary"], 1_800_000)
        self.assertEqual(fields["annual_rent"], 240_000)

    def test_rupee_symbol_and_lakh_suffix_are_parsed(self):
        from project_a.parser.input_parser import parse_text

        parsed = parse_text("FY 2025-26 salary ₹12L, age 40.")
        self.assertEqual(parsed["extracted_fields"]["annual_salary"], 1_200_000)

    def test_basic_and_monthly_salary_are_not_mistaken_for_annual_salary(self):
        from project_a.parser.input_parser import parse_text

        parsed = parse_text("FY 2024-25 basic salary 9 lakh, salary 2 lakh per month, age 40.")
        fields = parsed["extracted_fields"]
        self.assertEqual(fields["basic_salary"], 900_000)
        self.assertEqual(fields["annual_salary"], 2_400_000)
        self.assertEqual(parsed["conflicts"], [])

    def test_conflicting_years_are_not_silently_resolved(self):
        from project_a.parser.input_parser import parse_text

        parsed = parse_text("FY 2024-25 and FY 2025-26 salary 10 lakh, age 40.")
        self.assertTrue(parsed["conflicts"])
        self.assertTrue(parsed["clarification_needed"])

    def test_conflicting_claims_are_reported(self):
        from project_a.parser.input_parser import parse_text

        hra = parse_text("FY 2024-25 salary 10 lakh, age 40, HRA 1 lakh, HRA received 2 lakh.")
        rent = parse_text("FY 2024-25 salary 10 lakh, age 40, rent 10k monthly and rent 25k monthly.")
        loan = parse_text("FY 2024-25 salary 10 lakh, age 40, home loan interest 50k, but I do not have a home loan.")
        self.assertTrue(any("different HRA" in conflict for conflict in hra["conflicts"]))
        self.assertTrue(any("different rent" in conflict for conflict in rent["conflicts"]))
        self.assertTrue(any("no home loan" in conflict for conflict in loan["conflicts"]))


if __name__ == "__main__":
    unittest.main()
