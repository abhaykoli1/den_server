"""Runs last (alphabetical) — reports the total check count."""
from conftest import CHECKS


def test_summary():
    print(f"\n\n  ✔ e2e checks passed: {CHECKS['n']}\n")
    assert CHECKS["n"] >= 100
