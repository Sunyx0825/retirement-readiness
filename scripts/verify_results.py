"""Recalculate all synthetic households using a fixed assessment date."""

import asyncio
import json
import math
from datetime import date
from pathlib import Path

from retirement_readiness.pipeline import assess

ROOT = Path(__file__).resolve().parents[1]
expected = json.loads((ROOT / "results/cases.json").read_text(encoding="utf-8"))
for name, reference in expected.items():
    case = json.loads((ROOT / f"examples/{name}.json").read_text(encoding="utf-8"))
    _, report = asyncio.run(assess(case["answers"], as_of=date(2026, 9, 1)))
    actual = report["headline_metrics"]
    for key, value in reference["coverage_indices"].items():
        assert math.isclose(actual["coverage_indices"][key], value, rel_tol=1e-10), (name, key)
    assert actual["scores"] == reference["scores"], name
    assert report["mode"] == "offline_rules"
print("Three synthetic households reproduce the reference calculations and scores.")
