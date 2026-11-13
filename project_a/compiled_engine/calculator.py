from pathlib import Path

from project_a.engine import calculate, load_config

COMPILED_DIR = Path(__file__).resolve().parent / "compiled_rules"


def calculate_tax(data):
    config = load_config(data.financial_year, COMPILED_DIR)
    return calculate(data, config)
