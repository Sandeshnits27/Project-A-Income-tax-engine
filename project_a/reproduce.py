from __future__ import annotations

import argparse
import sys
import unittest

from project_a.benchmarks.run_benchmark import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run tests and reproduce the Project A measurements.")
    parser.add_argument("--include-llm", action="store_true", help="Opt in to Gemini API calls for the full query-time test set.")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover("project_a/tests", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    run(include_llm=args.include_llm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
