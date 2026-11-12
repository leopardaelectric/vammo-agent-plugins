#!/usr/bin/env python3
"""Classify a person's open Vammo Work Tracker items from collected evidence.

Input: a JSON file assembled by the agent from read-only tracker calls
(listWorkItems + listPullRequests per item, optionally getDeliveryScope).
Output: one proposal per item, as JSON (default) or a Markdown table.

The rules are deterministic on purpose: the agent writes the explanation,
this script decides the class. Nothing here calls the tracker or GitHub.

Usage:
  classify.py INPUT.json [--format json|markdown] [--now ISO8601]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

ACTIVE_DAYS = 3
STALE_DAYS = 7
SPLIT_PR_COUNT = 6

# Transition an agent may propose per policy, in order. The item's
# availableActions decide which one is actually proposed. integration_observed
# and production_verified are system-only: an agent never proposes them.
ACTION_BY_POLICY = {
    "PRODUCTION": ["editDeliveryScope"],
    "DELIVERABLE": ["complete_deliverable", "request_review"],
    "REVIEWED_DELIVERABLE": ["request_review"],
}


def pick_action(policy: str | None, available: list | None) -> str:
    wanted = ACTION_BY_POLICY.get(policy)
    if not wanted:
        return "check completion policy"
    if available is None:
        return wanted[0] + " (unverified: read availableActions)"
    names = {a.split(":", 1)[-1] for a in available}
    for action in wanted:
        if action in names:
            return action
    return "no completing action available; report availableActions"


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_days(now: datetime, ts: datetime | None) -> float | None:
    return None if ts is None else (now - ts).total_seconds() / 86400


def classify_item(item: dict, now: datetime, pr_owners: dict) -> dict:
    prs = item.get("prs") or []
    phase = item.get("phase")
    policy = item.get("completionPolicy")
    open_prs = [p for p in prs if p.get("state") == "OPEN"]
    merged = [p for p in prs if p.get("state") == "MERGED"]
    merged_by_assignee = any(p.get("authorIsAssignee") is True for p in merged)
    last_pr = max((parse_ts(p.get("updatedAt")) for p in prs if p.get("updatedAt")), default=None)
    last_activity = max(filter(None, [last_pr, parse_ts(item.get("updatedAt"))]), default=None)
    idle = age_days(now, last_activity)

    flags = []
    if not item.get("project"):
        flags.append("no_project")
    if item.get("deliveryScopeStatus") in (None, "NONE", "AWAITING_SCOPE", "UNCONFIRMED") and policy == "PRODUCTION":
        flags.append("no_delivery_scope")
    if len(prs) > SPLIT_PR_COUNT:
        flags.append("consider_split")
    shared = sorted({k for p in prs for k in pr_owners.get(pr_key(p), ()) if k != item["key"]})
    if shared:
        flags.append("shares_pr_with:" + ",".join(shared))

    if item.get("blocked"):
        cls, action = "BLOCKED", "review blocker; keep phase"
    elif phase == "INTEGRATED" and policy == "PRODUCTION":
        cls, action = "AWAITING_DEPLOY", "find deploy evidence; the tracker completes the work"
    elif phase not in ("TODO", "IN_PROGRESS"):
        cls, action = "OTHER_PHASE", "no rule; report only"
    elif not prs:
        cls = "ORPHAN" if (idle or 0) > ACTIVE_DAYS else "NEW"
        action = "ask the owner (keep, link work, or the person cancels it)" if cls == "ORPHAN" else "keep"
    elif open_prs:
        if idle is not None and idle > STALE_DAYS:
            cls, action = "STALE", "ask: merge, close or cancel the open PRs"
        else:
            cls, action = "ACTIVE", "keep"
        if merged:
            flags.append(f"partial:{len(merged)}_merged_{len(open_prs)}_open")
    elif merged and not merged_by_assignee:
        cls, action = "OWNER_CHECK", "no merged PR is known to be the assignee's; ask whether this item's own goal is done"
    elif merged:
        cls = "COMPLETABLE"
        if phase == "TODO":
            action = "start, then reread availableActions"
        else:
            action = pick_action(policy, item.get("availableActions"))
    else:
        cls, action = "ABANDONED", "all PRs closed unmerged; ask whether the person cancels it"

    if item.get("type") == "BUG" and cls == "COMPLETABLE":
        flags.append("needs_defect_cause")

    return {
        "key": item["key"],
        "title": item.get("title"),
        "phase": phase,
        "policy": policy,
        "class": cls,
        "proposed_action": action,
        "prs": {"merged": len(merged), "open": len(open_prs), "total": len(prs)},
        "idle_days": None if idle is None else round(idle, 1),
        "flags": flags,
    }


def pr_key(pr: dict) -> str:
    return f"{pr.get('repo')}#{pr.get('number')}"


def classify(data: dict, now: datetime) -> list[dict]:
    items = data["items"]
    pr_owners: dict[str, set] = {}
    for item in items:
        for pr in item.get("prs") or []:
            pr_owners.setdefault(pr_key(pr), set()).add(item["key"])
    return [classify_item(i, now, pr_owners) for i in items]


def to_markdown(rows: list[dict]) -> str:
    out = ["| Key | Class | Proposed action | PRs m/o/t | Idle d | Flags |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["class"], r["key"])):
        p = r["prs"]
        out.append(
            f"| {r['key']} | {r['class']} | {r['proposed_action']} | "
            f"{p['merged']}/{p['open']}/{p['total']} | {r['idle_days']} | {', '.join(r['flags'])} |"
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("--format", choices=["json", "markdown"], default="json")
    ap.add_argument("--now", help="ISO 8601 reference time (default: input 'now' or current time)")
    args = ap.parse_args()
    with open(args.input, encoding="utf-8") as fh:
        data = json.load(fh)
    now = parse_ts(args.now or data.get("now")) or datetime.now(timezone.utc)
    rows = classify(data, now)
    if args.format == "markdown":
        print(to_markdown(rows))
    else:
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["class"]] = counts.get(r["class"], 0) + 1
        json.dump({"now": now.isoformat(), "counts": counts, "items": rows}, sys.stdout, indent=2, ensure_ascii=False)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
