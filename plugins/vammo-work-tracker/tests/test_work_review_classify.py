#!/usr/bin/env python3
"""The vammo-work-review classifier must keep its rules on a synthetic board."""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "vammo-work-review" / "scripts"))
from classify import classify  # noqa: E402

data = json.loads((HERE / "work-review-fixture.json").read_text(encoding="utf-8"))
rows = {r["key"]: r for r in classify(data, datetime.fromisoformat("2026-01-15T12:00:00+00:00"))}

expected_class = {
    "COMPLETABLE": {"TEST-1", "TEST-2", "TEST-3", "TEST-4", "TEST-14"},
    "STALE": {"TEST-5"},
    "ORPHAN": {"TEST-6"},
    "NEW": {"TEST-7"},
    "BLOCKED": {"TEST-8"},
    "OWNER_CHECK": {"TEST-9", "TEST-15"},
    "AWAITING_DEPLOY": {"TEST-10"},
    "OTHER_PHASE": {"TEST-11"},
    "ACTIVE": {"TEST-12"},
    "ABANDONED": {"TEST-13"},
}
# The item's availableActions decide the action, not the policy alone.
expected_action = {
    "TEST-1": "editDeliveryScope",
    "TEST-2": "request_review",
    "TEST-3": "complete_deliverable",
    "TEST-14": "start, then reread availableActions",
    "TEST-10": "find deploy evidence; the tracker completes the work",
}
expected_flags = {
    "TEST-1": ["no_delivery_scope", "no_project"],
    "TEST-4": ["needs_defect_cause"],
    "TEST-5": ["shares_pr_with:TEST-12"],
    "TEST-12": ["no_delivery_scope", "consider_split", "partial:6_merged_2_open", "shares_pr_with:TEST-5"],
}

failures = []
for cls, keys in expected_class.items():
    got = {k for k, r in rows.items() if r["class"] == cls}
    if got != keys:
        failures.append(f"{cls}: expected {sorted(keys)}, got {sorted(got)}")
for key, action in expected_action.items():
    if rows[key]["proposed_action"] != action:
        failures.append(f"{key} must propose {action!r}, got {rows[key]['proposed_action']!r}")
for key, flags in expected_flags.items():
    missing = [f for f in flags if f not in rows[key]["flags"]]
    if missing:
        failures.append(f"{key} misses flags {missing}")

if failures:
    print("FAIL\n" + "\n".join(failures))
    sys.exit(1)
print(f"OK: {len(rows)} items classified as expected")
