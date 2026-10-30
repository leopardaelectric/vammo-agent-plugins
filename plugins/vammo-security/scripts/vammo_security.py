#!/usr/bin/env python3
"""Vammo security gates for Claude Code: secrets before push and PR, and a scan before PR.

Two PreToolUse gates, both deterministic: no model runs inside this script.

- Before `git push`: TruffleHog runs on the commits being pushed. A secret holds the push.
- Before a pull request is created (`gh pr create`, a `gh api` POST to `repos/<owner>/<name>/pulls`,
  or a GitHub MCP `create_pull_request` tool), two checks run:
  1. TruffleHog runs on the branch's commits. A secret holds the PR.
  2. The branch's HEAD must be "covered": it was scanned by the claude-security plugin, or
     it is the commit that applied that scan's patches. If it isn't, Claude is told to run
     the pre-pr-security skill first. There is no skip.

Both gates judge the checkout the command targets, not the session's: `cd <dir> && ...` and
`git -C <dir> push` are followed. A pull request for another Vammo repository (`gh pr create -R`,
the `gh api` endpoint's owner/name, or the MCP tool's owner/repo) whose checkout can't be
resolved is held, with instructions to open it from that checkout. Text that only mentions the
commands (quoted, or a heredoc body) doesn't trigger a gate. Inline code an interpreter runs
(`python3 -c`, `node -e`, `perl -e`, `ruby -e`, `php -r`) does when it contains them, and is
judged in the directory the interpreter runs in.

Secrets: a verified (live) secret always holds the push or PR. An unverified hit holds it
until the person confirms it's a false positive (`accept-secret`); after that, neither gate
raises it again. Messages name the detector, file, line and commit, never the value.

TruffleHog: a `trufflehog` on PATH is used as is. Otherwise the pinned release (the same
version shared-ci's CI gate runs) is downloaded once into the plugin's data dir, and its
checksum is verified. If TruffleHog can't run, the gate fails closed: it holds the push or
PR and says what to fix.

State lives in the repository's git dir (`git rev-parse --git-path vammo-security/state.json`),
per worktree and never committed.

Subcommands:
  hook                  PreToolUse hook (reads the hook JSON on stdin).
  base                  Prints the newest covered ancestor of HEAD (incremental-scan base).
  cover REPORT_DIR      Records HEAD as covered by the claude-security scan in REPORT_DIR.
  accept-secret ID WHY  Records an unverified secret hit as a false positive.
  summary               Prints the "Security pre-check" section for the PR body.

Scope: every repository with a remote on github.com/leopardaelectric, and nothing else.
There is no opt-out switch. The person's only exception is accepting an unverified secret
hit as a false positive, which is recorded with where it is and why.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
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
    # A temp file of its own: two hooks may save the same repo's state at once.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def note(msg: str) -> None:
    print(f"vammo-security: {msg}", file=sys.stderr)


VAMMO_REMOTE = re.compile(r"github\.com[:/]leopardaelectric/", re.IGNORECASE)


def is_vammo_repo(top: str) -> bool:
    """A Vammo repository is one with any remote on github.com/leopardaelectric."""
    try:
        return bool(VAMMO_REMOTE.search(git(top, "remote", "-v")))
    except RuntimeError:
        return False


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
    # A temp file of its own: two hooks may install at once, and with a shared name one renames
    # the other's file away mid-install.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    except OSError:
        if not target.is_file():  # else another install finished first, with the same release
            raise
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class ScannerUnavailable(RuntimeError):
    """TruffleHog couldn't be found, installed or run."""


def trufflehog_bin() -> str:
    own = shutil.which("trufflehog")
    if own:
        return own
    pinned = data_dir() / f"trufflehog-{TRUFFLEHOG_VERSION}" / "trufflehog"
    if pinned.is_file():
        return str(pinned)
    note(f"downloading TruffleHog {TRUFFLEHOG_VERSION} (one time) into {pinned.parent}")
    try:
        download_trufflehog(pinned)
    except Exception as e:  # network, platform, checksum: the gate fails closed on any of them
        if pinned.is_file():  # a concurrent hook installed it
            return str(pinned)
        raise ScannerUnavailable(f"couldn't install TruffleHog {TRUFFLEHOG_VERSION}: {e}") from e
    return str(pinned)


def secret_hits(top: str, base: str) -> list[dict]:
    """Secrets TruffleHog finds in the commits base..HEAD. Raises ScannerUnavailable."""
    binary = trufflehog_bin()
    cmd = [binary, "git", f"file://{top}", "--json", "--no-update",
           "--since-commit", base, "--branch", "HEAD", "--config", str(PLUGIN_ROOT / "config" / "trufflehog.yaml")]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=100)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ScannerUnavailable(f"TruffleHog failed to run: {e}") from e
    if out.returncode != 0 and not out.stdout.strip():
        raise ScannerUnavailable(f"TruffleHog exited {out.returncode}: {out.stderr.strip()[-300:]}")
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


OPERATOR_CHARS = ";&|()<>\n"
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
GIT_VALUE_OPTIONS = {"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
# Quoted strings are matched first, so a `<<` inside one isn't taken for a heredoc.
HEREDOC_SCAN = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*"|(?<!<)<<(?!<)(-?)[ \t]*(?:'([^']+)'|"([^"]+)"|\\?([\w.-]+))""")
GITHUB_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
REPO_SPEC = re.compile(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
VAMMO_OWNER = "leopardaelectric"
MAX_NESTING = 4
API_PULLS = re.compile(r"(?:https://api\.github\.com)?/?repos/([\w.{}:-]+)/([\w.{}:-]+)/pulls/?(?:\?.*)?",
                       re.IGNORECASE)
API_PULLS_TEXT = re.compile(r"(^|[\s;&|(])gh\s+api\s.*repos/[^\s/]+/[^\s/]+/pulls\b")
API_PARAM_OPTIONS = ("-f", "-F", "--field", "--raw-field", "--input")
INTERPRETER = re.compile(r"(python)(?:\d+(?:\.\d+)*)?|(node|perl|ruby|php)")
# Per interpreter: the short options whose value is code it runs, and the long ones.
INLINE_CODE_OPTIONS = {
    "python": ("c", ()),
    "node": ("ep", ("--eval", "--print")),
    "perl": ("eE", ()),
    "ruby": ("e", ()),
    "php": ("r", ()),
}
# In code, a command sits inside a string or a list (`os.system('gh pr create')`,
# `["git", "push"]`): any non-word character may precede it, and list items may split its words.
CODE_SEP = r"""(?:\s+|["']\s*,\s*["'])"""
CODE_PR = re.compile(rf"(?<![\w.-])gh{CODE_SEP}pr{CODE_SEP}create(?![\w-])")
CODE_PUSH = re.compile(rf"(?<![\w.-])git{CODE_SEP}(?:-C{CODE_SEP}[^\s,]+{CODE_SEP})?push(?![\w-])")


def strip_heredocs(command: str) -> tuple[str, list[str]]:
    """The command without its heredoc operators and bodies, and the bodies.

    Raises ValueError when a heredoc never ends."""
    kept, bodies, body, pending = [], [], [], []
    for line in command.split("\n"):
        if pending:
            strip_tabs, delimiter = pending[0]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                bodies.append("\n".join(body))
                body = []
                pending.pop(0)
            else:
                body.append(line)
            continue
        pieces, last = [], 0
        for m in HEREDOC_SCAN.finditer(line):
            delimiter = m.group(2) or m.group(3) or m.group(4)
            if delimiter:
                pieces.append(line[last:m.start()])
                last = m.end()
                pending.append((m.group(1) == "-", delimiter))
        kept.append("".join(pieces) + line[last:])
    if pending:
        raise ValueError("unterminated heredoc")
    return "\n".join(kept), bodies


def shell_words(text: str) -> list[str]:
    """Shell words and operator runs (`&&`, `;`, `|`, `(`, newline, `>`...), quotes removed."""
    lex = shlex.shlex(text.replace("\\\n", " "), posix=True, punctuation_chars=OPERATOR_CHARS)
    lex.whitespace, lex.whitespace_split, lex.commenters = " \t\r", True, ""
    return list(lex)


def resolve_dir(here: str, path: str) -> str:
    """path (relative to here, ~ and $VARS expanded) when it's a directory, else here."""
    full = os.path.normpath(os.path.join(here, os.path.expanduser(os.path.expandvars(path))))
    return full if os.path.isdir(full) else here


def dir_after(argv: list[str], here: str) -> str:
    """The directory the following commands run in after argv (`cd`/`pushd`)."""
    if argv[0] not in ("cd", "pushd") or "-" in argv[1:]:
        return here
    args = [a for a in argv[1:] if not a.startswith("-")]
    return resolve_dir(here, args[0] if args else "~")


def inner_scripts(argv: list[str], bodies: list[str]) -> list[str]:
    """Shell code a simple command runs from its words: `$(...)`, backticks, `sh -c`, `eval`,
    and the heredoc bodies when a shell reads its script from stdin."""
    scripts = []
    for word in argv:
        if "$(" in word:
            scripts.append(word[word.index("$(") + 2:])
        scripts += word.split("`")[1::2]
    if argv[0] == "eval":
        scripts.append(" ".join(argv[1:]))
    for i, word in enumerate(argv):
        if os.path.basename(word) not in SHELLS:
            continue
        rest = argv[i + 1:]
        for j, opt in enumerate(rest):
            if not opt.startswith("-"):
                break
            if not opt.startswith("--") and "c" in opt and j + 1 < len(rest):
                scripts.append(rest[j + 1])
                break
        else:
            scripts += bodies
    return scripts


def simple_commands(command: str, cwd: str, depth: int = 0) -> list[tuple[str, list[str]]]:
    """(directory it runs in, argv) for each simple command a shell command line runs.

    Raises ValueError when the line can't be parsed."""
    if depth > MAX_NESTING:
        raise ValueError("commands nested too deep")
    text, bodies = strip_heredocs(command)
    found, argv, here, scopes, redirect = [], [], cwd, [], False
    for word in shell_words(text) + [";"]:
        if word and not set(word) - set(OPERATOR_CHARS):
            if word in ("&", "&&") or set(word) & set(";|\n()"):
                if argv:
                    found.append((here, argv))
                    here = dir_after(argv, here)
                    argv = []
                for ch in word:
                    if ch == "(":
                        scopes.append(here)
                    elif ch == ")" and scopes:
                        here = scopes.pop()
            else:
                redirect = True  # the next word is where the redirection goes, not an argument
            continue
        if redirect:
            redirect = False
        else:
            argv.append(word)
    nested = [(here, script) for here, argv in found for script in inner_scripts(argv, bodies)]
    for here, script in nested:
        found += simple_commands(script, here, depth + 1)
    return found


def inline_code(args: list[str], letters: str, long_options: tuple[str, ...]) -> list[str]:
    """Every word of an interpreter's arguments that may be code it runs inline.

    Over-inclusive on purpose: a cluster like `-pe` or `-Ic` offers both its attached rest and
    the next word, since only a match against the gated commands makes a word count."""
    short = re.compile(rf"-(?!-)[A-Za-z]*?[{letters}](.*)", re.DOTALL)
    codes = []
    for j, word in enumerate(args):
        following = args[j + 1:j + 2]
        for option in long_options:
            if word == option:
                codes += following
            elif word.startswith(option + "="):
                codes.append(word[len(option) + 1:])
        m = short.fullmatch(word)
        if m:
            codes += ([m.group(1)] if m.group(1) else []) + following
    return codes


def api_pull_creation(args: list[str]) -> tuple[bool, str | None]:
    """(whether it creates a pull request, owner/name it names) for a `gh api` call's arguments.

    The default method is GET, or POST once a field or an input is given; -X/--method overrides it.
    owner/name is None when the endpoint uses gh's placeholders for the current repository."""
    endpoint = next((m for m in map(API_PULLS.fullmatch, args) if m), None)
    if not endpoint:
        return False, None
    method, has_params = None, False
    for j, arg in enumerate(args):
        if arg in ("-X", "--method"):
            method = args[j + 1] if j + 1 < len(args) else method
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1]
        elif arg.startswith("-X"):
            method = arg[2:].lstrip("=")
        elif arg in API_PARAM_OPTIONS or arg.startswith(("--field=", "--raw-field=", "--input=", "-f", "-F")):
            has_params = True
    creates = method.upper() == "POST" if method else has_params
    owner, name = endpoint.groups()
    return creates, None if set(owner + name) & set("{}:") else f"{owner}/{name}"


def gated_calls(command: str, cwd: str) -> list[tuple[str, str, list[str]]]:
    """(kind, directory, arguments) for each push or PR creation a shell command line runs.

    A command counts wherever it starts in a simple command's words, so wrappers (`env`,
    `timeout`, `<tool> -- git push`) are covered; quoted text and heredoc bodies never count.
    Inline interpreter code that contains a gated command counts, in the interpreter's directory,
    with no arguments. A `gh api` PR creation carries the repository it names as `--repo`.
    Raises ValueError when the line can't be parsed."""
    calls = []
    for here, argv in simple_commands(command, cwd):
        for i, word in enumerate(argv):
            name = os.path.basename(word)
            if name == "gh" and argv[i + 1:i + 3] == ["pr", "create"]:
                calls.append(("pr", here, argv[i + 3:]))
                continue
            if name == "gh" and argv[i + 1:i + 2] == ["api"]:
                creates, repo = api_pull_creation(argv[i + 2:])
                if creates:
                    calls.append(("pr", here, ["--repo", repo] if repo else []))
                continue
            interpreter = INTERPRETER.fullmatch(name)
            if interpreter:
                letters, long_options = INLINE_CODE_OPTIONS[interpreter.group(1) or interpreter.group(2)]
                for code in inline_code(argv[i + 1:], letters, long_options):
                    calls += [(kind, here, []) for kind, pattern in (("pr", CODE_PR), ("push", CODE_PUSH))
                              if pattern.search(code)]
                continue
            if name != "git":
                continue
            where, j = here, i + 1
            while j < len(argv) and argv[j].startswith("-"):
                if argv[j] == "-C" and j + 1 < len(argv):
                    where = resolve_dir(where, argv[j + 1])
                j += 2 if argv[j] == "-C" or argv[j] in GIT_VALUE_OPTIONS else 1
            if j < len(argv) and argv[j] == "push":
                calls.append(("push", where, argv[j + 1:]))
    return calls


def classify(tool_name: str, tool_input: dict) -> str | None:
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        try:
            kinds = {kind for kind, _, _ in gated_calls(command, os.getcwd())}
        except ValueError:
            # Unparseable: match the text anywhere, so the gate fails closed.
            kinds = {kind for kind, pattern in (("pr", PR_COMMAND), ("pr", API_PULLS_TEXT), ("push", PUSH_COMMAND))
                     if pattern.search(command)}
        return next((kind for kind in ("pr", "push") if kind in kinds), None)
    # GitHub MCP servers name the tool create_pull_request (mcp__<server>__create_pull_request).
    if tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request"):
        return "pr"
    return None


def repo_flag(args: list[str]) -> str | None:
    """The `-R`/`--repo` value of a `gh pr create`."""
    for i, arg in enumerate(args):
        if arg in ("-R", "--repo"):
            return args[i + 1] if i + 1 < len(args) else None
        if arg.startswith("--repo="):
            return arg.split("=", 1)[1]
        if arg.startswith("-R") and len(arg) > 2:
            return arg[2:]
    return None


def github_repos(directory: str) -> set[str]:
    """owner/name (lowercase) of every github.com remote of the checkout at directory."""
    try:
        lines = git(directory, "remote", "-v").splitlines()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return set()
    matches = (GITHUB_REMOTE.search(line.split()[1]) for line in lines if len(line.split()) > 1)
    return {f"{m.group(1)}/{m.group(2)}".lower() for m in matches if m}


def pr_target(spec: str | None, directory: str) -> tuple[str | None, str | None]:
    """(checkout to judge, None), or (None, owner/name) for a Vammo repository that isn't the
    checkout's, or (None, None) for a repository outside Vammo."""
    m = REPO_SPEC.search(spec.strip()) if spec else None
    repo = f"{m.group(1)}/{m.group(2)}".lower() if m else None
    if not repo or repo in github_repos(directory):
        return directory, None
    if repo.split("/")[0] != VAMMO_OWNER:
        return None, None
    return None, repo


def gate_targets(kind: str, tool_name: str, tool_input: dict, cwd: str) -> list[tuple[str | None, str | None]]:
    """What each push or PR in the call targets, as pr_target returns it."""
    if tool_name != "Bash":
        owner, repo = tool_input.get("owner"), tool_input.get("repo")
        return [pr_target(f"{owner}/{repo}" if owner and repo else None, cwd)]
    try:
        calls = [(where, args) for k, where, args in gated_calls(str(tool_input.get("command", "")), cwd) if k == kind]
    except ValueError:
        calls = []
    if not calls:
        return [(cwd, None)]  # unparseable: judge the session's checkout
    if kind == "push":
        return list(dict.fromkeys((where, None) for where, _ in calls))
    return list(dict.fromkeys(pr_target(repo_flag(args), where) for where, args in calls))


def secrets_gate(top: str, gate: str, base: str | None) -> bool:
    """True when the gate denied the call over secrets."""
    if not base:
        return False
    try:
        if base == git(top, "rev-parse", "HEAD"):
            return False  # nothing new to publish
    except RuntimeError:
        return False
    try:
        hits = secret_hits(top, base)
    except ScannerUnavailable as e:
        # Fail closed: an unchecked push or PR is exactly what this gate exists to stop.
        deny(f"Vammo security check: the secret scan couldn't run, so this {gate} is held. {e}. "
             "Tell the person, and help them fix it: check the network (the first run downloads "
             "TruffleHog from GitHub), or install it (`brew install trufflehog`). Then retry the "
             f"{gate}. There is no way to skip this check.")
        return True
    data = load(top)
    accepted = data.get("accepted_secrets", {})
    live = [h for h in hits if h["verified"]]
    unknown = [h for h in hits if not h["verified"] and h["id"] not in accepted]
    status = "found" if live or unknown else "clean"
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
    if not kind:
        return 0
    cwd = str(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    for directory, unresolved in gate_targets(kind, tool_name, tool_input, cwd):
        if unresolved:
            deny(f"Vammo pre-PR security check: this pull request targets {unresolved}, which is not the "
                 "repository this session is in, so its HEAD can't be checked from here and the pull "
                 f"request is held. Open it from a checkout of {unresolved} (`cd <its worktree> && ...`), "
                 "so the checks run on that checkout's own HEAD. There is no way to skip this check.")
            return 0
        if directory and gate_checkout(kind, directory, cwd, tool_input):
            return 0
    return 0


def gate_checkout(kind: str, directory: str, session_dir: str, tool_input: dict) -> bool:
    """Runs the push or PR gate on the checkout at directory. True when it denied the call."""
    try:
        top = git(directory, "rev-parse", "--show-toplevel")
        head = git(directory, "rev-parse", "HEAD")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False  # not a git checkout: nothing to check
    if not is_vammo_repo(top):
        return False

    if kind == "push":
        return secrets_gate(top, "push", push_base(top))

    m = PR_BASE_FLAG.search(" " + str(tool_input.get("command", "")))
    preferred = m.group(1) if m else (str(tool_input.get("base")) if tool_input.get("base") else None)
    if secrets_gate(top, "pull request", default_base(top, preferred)):
        return True
    if not claude_security_installed():
        deny("Vammo pre-PR security check: the claude-security plugin isn't installed, so this pull "
             "request is held. Tell the person to install it with "
             "`claude plugin install claude-security@claude-plugins-official` and restart Claude Code, "
             "then retry. There is no way to skip this check.")
        return True
    entry = load(top).get("covered", {}).get(head)
    if entry and not entry.get("skipped"):  # "skipped" entries come from 0.2.0 and no longer count
        return False
    deny(
        f"Vammo pre-PR security check: HEAD {head[:12]}{checkout_note(top, session_dir)} has not been "
        "scanned yet. Before opening this "
        "pull request, load the `vammo-security:pre-pr-security` skill and follow it: scan the branch's "
        "changes with claude-security at medium effort, fix the findings with its verified patches, "
        "commit and push, record coverage, and then create the pull request again, adding the skill's "
        "summary to the body. Start the scan without asking first. There is no way to skip this check."
    )
    return True


def checkout_note(top: str, session_dir: str) -> str:
    """Names the checkout in a message when it isn't the session's own."""
    try:
        same = os.path.realpath(git(session_dir, "rev-parse", "--show-toplevel")) == os.path.realpath(top)
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        same = False
    return "" if same else f" of the checkout at {top}"


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


def cmd_accept_secret(hit_id: str, reason: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{12}", hit_id):
        raise SystemExit(f"{hit_id!r} is not a secret-hit id (12 hex characters from the gate's message)")
    top = git(os.getcwd(), "rev-parse", "--show-toplevel")
    base = default_base(top)
    if not base:
        raise SystemExit("couldn't resolve the branch's base to check the hit against")
    try:
        hits = secret_hits(top, base)
    except ScannerUnavailable as e:
        raise SystemExit(f"can't confirm the hit, because the secret scan couldn't run: {e}")
    match = next((h for h in hits if h["id"] == hit_id), None)
    if match is None:
        raise SystemExit(f"no current secret hit has id {hit_id}")
    if match["verified"]:
        raise SystemExit("that hit is a LIVE (verified) secret; it can't be accepted, it has to be removed")
    data = load(top)
    where = {k: match[k] for k in ("detector", "file", "line", "commit")}
    data.setdefault("accepted_secrets", {})[hit_id] = {"at": now(), "reason": reason[:500], **where}
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
    if not entry or entry.get("skipped"):
        print("(HEAD is not covered yet)")
        return 1
    f = entry.get("findings", {})
    counts = ", ".join(f"{n} {s}" for s, n in f.items() if n) or "no findings"
    lines = ["### 🛡️ Security pre-check",
             "",
             {"clean": "Secret scan (TruffleHog): no unresolved secrets in this branch's commits.",
              }.get(
                 data.get("secrets_checked", {}).get(head, ""), "Secret scan (TruffleHog): not recorded for this HEAD."),
             f"claude-security scanned this branch's changes at `{entry.get('effort')}` effort before the PR "
             f"(verification: {entry.get('verification')}): {counts}."]
    if entry.get("patched"):
        lines.append(f"Fixed with panel-verified patches: {', '.join(entry['patched'])}.")
    if entry.get("declined"):
        lines.append(f"Not auto-fixable (author decided): {', '.join(entry['declined'])}.")
    accepted = data.get("accepted_secrets", {})
    if accepted:
        lines.append("Secret-scanner hits the author confirmed as false positives:")
        for hit_id, a in accepted.items():
            where = f"`{a['file']}:{a.get('line')}` ({a.get('detector')})" if a.get("file") else f"id {hit_id}"
            lines.append(f"- {where}: {a.get('reason', '')}")
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
