#!/usr/bin/env python3
"""Vammo security gates for Claude Code: secrets before push and PR, and a scan before PR.

Two PreToolUse gates, both deterministic: no model runs inside this script.

- Before `git push`: TruffleHog runs on the commits being pushed. A secret holds the push.
- Before a pull request is created (`gh pr create`, or a GitHub MCP `create_pull_request`
  tool), two checks run:
  1. TruffleHog runs on the branch's commits. A secret holds the PR.
  2. The branch's HEAD must be "covered": it was scanned by the claude-security plugin, or
     it is the commit that applied that scan's patches, or the person explicitly chose to
     skip. If it isn't, Claude is told to run the pre-pr-security skill first.

Secrets: a verified (live) secret always holds the push or PR. An unverified hit holds it
until the person confirms it's a false positive (`accept-secret`); after that, neither gate
raises it again. Messages name the detector, file, line and commit, never the value.

TruffleHog: a `trufflehog` on PATH is used as is. Otherwise the pinned release (the same
version shared-ci's CI gate runs) is downloaded once into the plugin's data dir, and its
checksum is verified. If TruffleHog can't run, the secret gate is skipped with a note and
never holds anything.

State lives in the repository's git dir (`git rev-parse --git-path vammo-security/state.json`),
per worktree and never committed.

Subcommands:
  hook                  PreToolUse hook (reads the hook JSON on stdin).
  base                  Prints the newest covered ancestor of HEAD (incremental-scan base).
  cover REPORT_DIR      Records HEAD as covered by the claude-security scan in REPORT_DIR.
  skip REASON           Records that the person chose to open the PR without a scan.
  accept-secret ID WHY  Records an unverified secret hit as a false positive.
  summary               Prints the "Security pre-check" section for the PR body.

Opt-outs: VAMMO_SECURITY=off in the environment, or a folder that
~/.vammo/tracked-folders (the Work Tracker's scope file) leaves untracked.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PR_COMMAND = re.compile(r"(^|[\s;&|(])gh\s+pr\s+create(\s|$)")
PUSH_COMMAND = re.compile(r"(^|[\s;&|(])git\s+(?:-C\s+\S+\s+)?push(\s|$)")
PR_BASE_FLAG = re.compile(r"\s(?:--base|-B)[=\s]+([A-Za-z0-9._/-]{1,200})")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

# Same pin as shared-ci's security-trufflehog.yml, so local and CI gates agree.
TRUFFLEHOG_VERSION = "3.97.5"
TRUFFLEHOG_RELEASES = "https://github.com/trufflesecurity/trufflehog/releases/download"
PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)


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


def note(msg: str) -> None:
    print(f"vammo-security: {msg}", file=sys.stderr)


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


# --- TruffleHog -------------------------------------------------------------------------


def data_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PLUGIN_DATA") or Path.home() / ".vammo" / "vammo-security")


def release_asset() -> str | None:
    osname = {"darwin": "darwin", "linux": "linux"}.get(sys.platform.rstrip("0123456789"))
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine().lower())
    if not osname or not arch:
        return None
    return f"trufflehog_{TRUFFLEHOG_VERSION}_{osname}_{arch}.tar.gz"


def download_trufflehog(target: Path) -> None:
    asset = release_asset()
    if not asset:
        raise RuntimeError(f"no TruffleHog release for {sys.platform}/{platform.machine()}")
    base = f"{TRUFFLEHOG_RELEASES}/v{TRUFFLEHOG_VERSION}"
    sums = urllib.request.urlopen(f"{base}/trufflehog_{TRUFFLEHOG_VERSION}_checksums.txt", timeout=30).read().decode()
    expected = next((line.split()[0] for line in sums.splitlines() if line.strip().endswith(asset)), None)
    if not expected:
        raise RuntimeError(f"{asset} is not in the release checksums")
    blob = urllib.request.urlopen(f"{base}/{asset}", timeout=120).read()
    if hashlib.sha256(blob).hexdigest() != expected:
        raise RuntimeError(f"checksum mismatch for {asset}; refusing to install it")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        member = tf.getmember("trufflehog")
        if not member.isfile():
            raise RuntimeError("the release archive's trufflehog is not a regular file")
        body = tf.extractfile(member).read()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.chmod(0o755)
    tmp.replace(target)


def trufflehog_bin() -> str | None:
    own = shutil.which("trufflehog")
    if own:
        return own
    pinned = data_dir() / f"trufflehog-{TRUFFLEHOG_VERSION}" / "trufflehog"
    if pinned.is_file():
        return str(pinned)
    try:
        note(f"downloading TruffleHog {TRUFFLEHOG_VERSION} (one time) into {pinned.parent}")
        download_trufflehog(pinned)
        return str(pinned)
    except Exception as e:  # network down, unsupported platform, bad checksum: never hold work on it
        note(f"TruffleHog unavailable ({e}); the secret check was skipped")
        return None


def secret_hits(top: str, base: str) -> list[dict] | None:
    """Secrets TruffleHog finds in the commits base..HEAD (None when it couldn't run)."""
    binary = trufflehog_bin()
    if not binary:
        return None
    cmd = [binary, "git", f"file://{top}", "--json", "--no-update",
           "--since-commit", base, "--branch", "HEAD", "--config", str(PLUGIN_ROOT / "config" / "trufflehog.yaml")]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=100)
    except (OSError, subprocess.TimeoutExpired) as e:
        note(f"TruffleHog failed to run ({e}); the secret check was skipped")
        return None
    hits, seen = [], set()
    for line in out.stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        g = ((e.get("SourceMetadata") or {}).get("Data") or {}).get("Git") or {}
        detector = str((e.get("ExtraData") or {}).get("name") or e.get("DetectorName") or "secret")
        hit = {"detector": detector, "verified": bool(e.get("Verified")), "file": str(g.get("file", "?")),
               "line": g.get("line"), "commit": str(g.get("commit", ""))}
        # Never the value: the id is derived from where the hit is, not what it is.
        hit["id"] = hashlib.sha256(f"{detector}|{hit['file']}|{hit['line']}|{hit['commit']}".encode()).hexdigest()[:12]
        if hit["id"] not in seen:
            seen.add(hit["id"])
            hits.append(hit)
    return hits


def default_base(top: str, preferred: str | None = None) -> str | None:
    """Merge-base of HEAD with the PR's base branch (or the first default branch that exists)."""
    refs = ([f"origin/{preferred}", preferred] if preferred else []) + \
        ["origin/HEAD", "origin/main", "origin/master", "main", "master"]
    for ref in refs:
        try:
            git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            return git(top, "merge-base", ref, "HEAD")
        except RuntimeError:
            continue
    return None


def push_base(top: str) -> str | None:
    """The tip the remote already has for this branch, else the branch's merge-base."""
    try:
        pushed = git(top, "rev-parse", "--verify", "--quiet", "@{push}")
        git(top, "merge-base", "--is-ancestor", pushed, "HEAD")
        return pushed
    except RuntimeError:
        return default_base(top)


def secret_reason(gate: str, live: list[dict], unknown: list[dict], already_pushed: bool) -> str:
    def row(h: dict) -> str:
        kind = "LIVE: verified working" if h["verified"] else "unverified"
        return f"- {h['detector']} ({kind}) at {h['file']}:{h['line']} in commit {h['commit'][:8]} [id {h['id']}]"

    parts = [f"Vammo security check: TruffleHog found possible secrets in the commits this {gate} would publish. "
             "Never print or repeat a secret's value."]
    parts += [row(h) for h in live + unknown]
    parts.append(
        "Remove each real secret from the code (read it from the environment or the secrets manager), "
        "and take it out of the branch's HISTORY, not just the latest commit, because a later commit "
        "doesn't erase it. Do that with a fixup commit and a non-interactive autosquash rebase: "
        "`git commit --fixup=<commit>` then `GIT_SEQUENCE_EDITOR=: git rebase --autosquash <base>`, or "
        "`git commit --amend` if it's in the last commit. Tell the person before rewriting history."
    )
    if already_pushed:
        parts.append("These commits are already on GitHub, so tell the person a real secret must also be "
                     "ROTATED (revoked and reissued), and that the rewritten branch needs `git push --force-with-lease`.")
    if unknown:
        parts.append("For each UNVERIFIED hit, ask the person whether it's a real secret. If they say it's a false "
                     "positive (test fixture, placeholder), record it with "
                     f'`python3 "{PLUGIN_ROOT}/scripts/vammo_security.py" accept-secret <id> <their reason>`, '
                     "and it won't be raised again. A LIVE hit can't be accepted; it has to be removed.")
    parts.append(f"Then retry the {gate}.")
    return "\n".join(parts)


# --- Hook -------------------------------------------------------------------------------


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def classify(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        if PR_COMMAND.search(command):
            return "pr"
        if PUSH_COMMAND.search(command):
            return "push"
        return None
    # GitHub MCP servers name the tool create_pull_request (mcp__<server>__create_pull_request).
    if tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request"):
        return "pr"
    return None


def secrets_gate(top: str, gate: str, base: str | None) -> bool:
    """True when the gate denied the call over secrets."""
    if not base:
        return False
    try:
        if base == git(top, "rev-parse", "HEAD"):
            return False  # nothing new to publish
    except RuntimeError:
        return False
    hits = secret_hits(top, base)
    data = load(top)
    accepted = data.get("accepted_secrets", {})
    live = [h for h in hits or [] if h["verified"]]
    unknown = [h for h in hits or [] if not h["verified"] and h["id"] not in accepted]
    # What the PR summary reports: the result of the last check of this exact HEAD.
    status = "not run" if hits is None else ("found" if live or unknown else "clean")
    try:
        data.setdefault("secrets_checked", {})[git(top, "rev-parse", "HEAD")] = status
        save(top, data)
    except (RuntimeError, OSError):
        pass
    if status != "found":
        return False
    deny(secret_reason(gate, live, unknown, already_pushed=(gate == "pull request")))
    return True


def cmd_hook() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    kind = classify(tool_name, tool_input) if isinstance(tool_input, dict) else None
    if not kind or os.environ.get("VAMMO_SECURITY", "").lower() == "off":
        return 0
    cwd = str(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        top = git(cwd, "rev-parse", "--show-toplevel")
        head = git(cwd, "rev-parse", "HEAD")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return 0  # not a git checkout: nothing to check
    if not in_scope(top):
        return 0

    if kind == "push":
        secrets_gate(top, "push", push_base(top))
        return 0

    m = PR_BASE_FLAG.search(" " + str(tool_input.get("command", "")))
    preferred = m.group(1) if m else (str(tool_input.get("base")) if tool_input.get("base") else None)
    if secrets_gate(top, "pull request", default_base(top, preferred)):
        return 0
    if not claude_security_installed():
        note("the claude-security plugin is not installed, so the pre-PR scan was skipped. "
             "Install it with: claude plugin install claude-security@claude-plugins-official")
        return 0
    if head in load(top).get("covered", {}):
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


# --- Coverage ---------------------------------------------------------------------------


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
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
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


def cmd_accept_secret(hit_id: str, reason: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{12}", hit_id):
        raise SystemExit(f"{hit_id!r} is not a secret-hit id (12 hex characters from the gate's message)")
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
    base = default_base(top)
    hits = secret_hits(top, base) if base else None
    match = next((h for h in hits or [] if h["id"] == hit_id), None)
    if hits is not None and match is None:
        raise SystemExit(f"no current secret hit has id {hit_id}")
    if match and match["verified"]:
        raise SystemExit("that hit is a LIVE (verified) secret; it can't be accepted, it has to be removed")
    data = load(top)
    data.setdefault("accepted_secrets", {})[hit_id] = {"at": now(), "reason": reason[:500]}
    save(top, data)
    print(f"accepted {hit_id} as a false positive")
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
    data = load(top)
    entry = data.get("covered", {}).get(head)
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
             {"clean": "Secret scan (TruffleHog): no unresolved secrets in this branch's commits.",
              "not run": "Secret scan (TruffleHog) could not run for this HEAD."}.get(
                 data.get("secrets_checked", {}).get(head, ""), "Secret scan (TruffleHog): not recorded for this HEAD."),
             f"claude-security scanned this branch's changes at `{entry.get('effort')}` effort before the PR "
             f"(verification: {entry.get('verification')}): {counts}."]
    if entry.get("patched"):
        lines.append(f"Fixed with panel-verified patches: {', '.join(entry['patched'])}.")
    if entry.get("declined"):
        lines.append(f"Not auto-fixable (author decided): {', '.join(entry['declined'])}.")
    accepted = data.get("accepted_secrets", {})
    if accepted:
        lines.append(f"Secret-scanner hits confirmed as false positives by the author: {len(accepted)}.")
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
    if cmd == "accept-secret" and len(rest) >= 2:
        return cmd_accept_secret(rest[0], " ".join(rest[1:]))
    if cmd == "base":
        return cmd_base()
    if cmd == "summary":
        return cmd_summary()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
