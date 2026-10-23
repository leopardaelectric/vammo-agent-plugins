#!/usr/bin/env python3
"""Vammo pre-PR security check: state and hook for the vammo-security plugin.

A pull request is "covered" when its HEAD was scanned by the claude-security plugin
("scan changes"), or is the commit that applied that scan's patches, or the person
explicitly chose to skip the check for it. Coverage lives in the repository's git dir
(`git rev-parse --git-path vammo-security/state.json`): per worktree, never committed.

Subcommands:
  hook                 PreToolUse hook. Reads the hook JSON on stdin and denies a
                       pull-request creation whose HEAD is not covered, telling Claude
                       to run the pre-pr-security skill first.
  base                 Prints the newest covered commit that is an ancestor of HEAD
                       (the base for an incremental scan), or nothing.
  cover REPORT_DIR     Records HEAD as covered by the scan in REPORT_DIR (a
                       CLAUDE-SECURITY-* directory) and the patches applied from it.
                       Also covers the scanned commit itself.
  skip REASON          Records that the person chose to open the PR without a scan.
  summary              Prints the "Security pre-check" section for the PR body.

Opt-outs: VAMMO_SECURITY=off in the environment, or a folder that
~/.vammo/tracked-folders (the Work Tracker's scope file) leaves untracked.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PR_COMMAND = re.compile(r"(^|[\s;&|(])gh\s+pr\s+create(\s|$)")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def git(cwd: str, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} failed")
    return out.stdout.strip()


def state_path(cwd: str) -> Path:
    # --path-format=absolute keeps the path right when the hook runs from a subdirectory.
    return Path(git(cwd, "rev-parse", "--path-format=absolute", "--git-path", "vammo-security/state.json"))


def load(cwd: str) -> dict:
    try:
        data = json.loads(state_path(cwd).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(cwd: str, data: dict) -> None:
    path = state_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_scope(directory: str) -> bool:
    """Same rule as the Work Tracker's session-start script: longest matching line wins."""
    scope_file = os.environ.get("VAMMO_TRACKED_FOLDERS") or os.path.expanduser("~/.vammo/tracked-folders")
    if not os.path.isfile(scope_file):
        return True

    def norm(p: str) -> str:
        p = p.replace("\\", "/")
        p = re.sub(r"^([A-Za-z]):", r"/\1", p)
        return p.rstrip("/").lower()

    target, best, verdict = norm(directory) + "/", -1, False
    for raw in open(scope_file, encoding="utf-8", errors="replace"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yes = not line.startswith("!")
        line = line.lstrip("!")
        if line.startswith("~"):
            line = os.path.expanduser("~") + line[1:]
        rule = norm(line)
        if target.startswith(rule + "/") and len(rule) > best:
            best, verdict = len(rule), yes
    return verdict


def claude_security_installed() -> bool:
    try:
        data = json.loads((Path.home() / ".claude/plugins/installed_plugins.json").read_text())
    except (OSError, ValueError):
        return False
    plugins = data.get("plugins", data) if isinstance(data, dict) else {}
    return any(str(k).startswith("claude-security@") for k in plugins)


def is_pr_creation(tool_name: str, tool_input: dict) -> bool:
    if tool_name == "Bash":
        return bool(PR_COMMAND.search(str(tool_input.get("command", ""))))
    # GitHub MCP servers name the tool create_pull_request (mcp__<server>__create_pull_request).
    return tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request")


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def cmd_hook() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict) or not is_pr_creation(tool_name, tool_input):
        return 0
    if os.environ.get("VAMMO_SECURITY", "").lower() == "off":
        return 0
    cwd = str(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    # `gh pr create` can be run from another checkout with `cd x && gh pr create` or -R; the
    # session's cwd is the honest default for which checkout the PR comes from.
    try:
        top = git(cwd, "rev-parse", "--show-toplevel")
        head = git(cwd, "rev-parse", "HEAD")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return 0  # not a git checkout: nothing to check
    if not in_scope(top):
        return 0
    if not claude_security_installed():
        # Never hold a PR on a tool the person can't run; say how to get it, once per call.
        print("vammo-security: the claude-security plugin is not installed, so the pre-PR security "
              "scan was skipped. Install it with: claude plugin install claude-security@claude-plugins-official",
              file=sys.stderr)
        return 0
    covered = load(top).get("covered", {})
    if head in covered:
        return 0
    deny(
        f"Vammo pre-PR security check: HEAD {head[:12]} has not been scanned yet. Before opening this "
        "pull request, load the `vammo-security:pre-pr-security` skill and follow it: scan the branch's "
        "changes with claude-security at medium effort, fix the findings with its verified patches, "
        "commit and push, record coverage, and then create the pull request again, adding the skill's "
        "summary to the body. The person turned this on, so start the scan without asking first. If they "
        "explicitly told you in this session to skip the security check, record that with the skill's "
        "skip step instead."
    )
    return 0


def stamp_of(report_dir: str) -> dict:
    stamps = sorted(glob.glob(os.path.join(report_dir, "CLAUDE-SECURITY-REVISION-*.json")))
    if not stamps:
        raise SystemExit(f"no CLAUDE-SECURITY-REVISION-*.json in {report_dir}")
    return json.loads(Path(stamps[-1]).read_text())


def applied_patches(report_dir: str) -> tuple[list[str], list[str]]:
    """(earned patch ids, declined ids) from the suggest-patches record, when there is one."""
    record = Path(report_dir) / "patches" / "patches.jsonl"
    earned, declined = [], []
    if record.is_file():
        for line in record.read_text().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            fid = str(item.get("id") or item.get("finding_id") or "?")
            (earned if (Path(report_dir) / "patches" / f"{fid}.patch").is_file() else declined).append(fid)
    return earned, declined


def cmd_cover(report_dir: str) -> int:
    cwd = os.getcwd()
    top = git(cwd, "rev-parse", "--show-toplevel")
    head = git(top, "rev-parse", "HEAD")
    stamp = stamp_of(report_dir)
    rev = stamp.get("revision") or {}
    scanned = str(rev.get("commit") or "")
    if rev.get("dirty"):
        raise SystemExit("the scan was taken of a dirty working tree; commit and scan again")
    if scanned != head:
        # HEAD may be the commit that applied this scan's patches; nothing else is acceptable.
        try:
            parent = git(top, "rev-parse", "HEAD^")
        except RuntimeError:
            parent = ""
        if parent != scanned:
            raise SystemExit(f"report scanned {scanned[:12]}, but HEAD is {head[:12]} and its parent is not "
                             "the scanned commit; scan again")
    findings = stamp.get("findings") or {}
    earned, declined = applied_patches(report_dir)
    entry = {
        "at": now(),
        "scanned_commit": scanned,
        "base": str(rev.get("merge_base") or rev.get("base") or ""),
        "effort": stamp.get("effort"),
        "verification": (stamp.get("verification") or {}).get("status"),
        "findings": {s.lower(): int(findings.get(s.lower(), 0) or 0) for s in SEVERITIES},
        "patched": earned if scanned != head else [],
        "declined": declined,
        "report": os.path.relpath(os.path.abspath(report_dir), top),
    }
    data = load(top)
    data.setdefault("covered", {})[head] = entry
    data["covered"].setdefault(scanned, {**entry, "patched": []})
    data["last"] = head
    save(top, data)
    print(f"covered {head[:12]} (scan of {scanned[:12]}, {sum(entry['findings'].values())} finding(s), "
          f"{len(entry['patched'])} patched)")
    return 0


def cmd_skip(reason: str) -> int:
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
    head = git(top, "rev-parse", "HEAD")
    data = load(top)
    data.setdefault("covered", {})[head] = {"at": now(), "skipped": True, "reason": reason[:500]}
    data["last"] = head
    save(top, data)
    print(f"skip recorded for {head[:12]}")
    return 0


def cmd_base() -> int:
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
    head = git(top, "rev-parse", "HEAD")
    best, best_count = "", None
    for sha, entry in load(top).get("covered", {}).items():
        if sha == head or entry.get("skipped"):
            continue
        try:
            git(top, "merge-base", "--is-ancestor", sha, head)
            count = int(git(top, "rev-list", "--count", f"{sha}..{head}"))
        except (RuntimeError, ValueError):
            continue
        if best_count is None or count < best_count:
            best, best_count = sha, count
    if best:
        print(best)
    return 0


def cmd_summary() -> int:
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
    head = git(top, "rev-parse", "HEAD")
    entry = load(top).get("covered", {}).get(head)
    if not entry:
        print("(HEAD is not covered yet)")
        return 1
    if entry.get("skipped"):
        print(f"### 🛡️ Security pre-check\n\nSkipped by the author: {entry.get('reason', '')}")
        return 0
    f = entry.get("findings", {})
    counts = ", ".join(f"{n} {s}" for s, n in f.items() if n) or "no findings"
    lines = ["### 🛡️ Security pre-check",
             "",
             f"claude-security scanned this branch's changes at `{entry.get('effort')}` effort before the PR "
             f"(verification: {entry.get('verification')}): {counts}."]
    if entry.get("patched"):
        lines.append(f"Fixed with panel-verified patches: {', '.join(entry['patched'])}.")
    if entry.get("declined"):
        lines.append(f"Not auto-fixable (author decided): {', '.join(entry['declined'])}.")
    print("\n".join(lines))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "hook":
        return cmd_hook()
    if cmd == "cover" and len(rest) == 1:
        return cmd_cover(rest[0])
    if cmd == "skip" and rest:
        return cmd_skip(" ".join(rest))
    if cmd == "base":
        return cmd_base()
    if cmd == "summary":
        return cmd_summary()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
