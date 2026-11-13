from __future__ import annotations

import json
from pathlib import Path

from project_a.data.natural_language_descriptions import DESCRIPTIONS
from project_a.parser.input_parser import parse_text

output = Path(__file__).resolve().parents[1] / "data" / "natural_language_cases.json"
records = []
for index, (category, text) in enumerate(DESCRIPTIONS, start=1):
    observed = parse_text(text)
    records.append(
        {
            "case_id": f"NL_{index:03d}",
            "category": category,
            "text": text,
            "expected_extracted_fields": observed["extracted_fields"],
            "expected_missing_fields": observed["missing_fields"],
            "expected_conflicts": observed["conflicts"],
            "expected_clarification": observed["clarification_needed"],
            "annotation_status": "frozen initial parser snapshot; review against legal/input requirements before relying on it",
        }
    )
output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Wrote {len(records)} parser cases to {output}")
