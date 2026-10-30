#!/usr/bin/env python3
"""Vammo security gates for Claude Code: secrets before push and PR, and a scan before PR.

Two PreToolUse gates, both deterministic: no model runs inside this script.

- Before `git push`: TruffleHog runs on the commits being pushed. A secret holds the push.
- Before a pull request is created (`gh pr create` or `gh pr new`, a `gh api` call to `.../pulls`, a GraphQL
  `createPullRequest`, or a GitHub MCP `create_pull_request` tool), two checks run:
  1. TruffleHog runs on the branch's commits. A secret holds the PR.
  2. The branch's HEAD must be "covered": it was scanned by the claude-security plugin, or
     it is the commit that applied that scan's patches. If it isn't, Claude is told to run
     the pre-pr-security skill first. There is no skip.

Both gates fail closed. Whether a Bash command pushes or opens a PR is read from its whole
text, so a command that only mentions one (quoted, in a heredoc body, in a commit message)
triggers the gate too. Which checkout is judged: only four exact forms of a whole command,
with plain literal words, are judged on the checkout they target alone: `cd <dir> && gh pr
create …` (without -R), `cd <dir> && git push …`, `git -C <dir> push …`, and
`heavy-lock.sh run <label> -- git -C <dir> push …`. Anything else is judged on the session's
checkout and on every checkout the command names after `cd`, `-C`, `--git-dir` or `GIT_DIR`.
A pull request for another Vammo repository (`-R`, the `gh api` endpoint's owner/name, or the
MCP tool's owner/repo) that none of those checkouts is, is held, with instructions to open it
from that repository's checkout. An error, or more checkouts than fit in the hook's time,
holds the call.

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

import bisect
import glob
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

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


# When the running hook must be done by (time.monotonic()); run_gates sets it.
_deadline = float("inf")


def fetch(url: str) -> bytes:
    """url's body, read in chunks so a slow download can't outlast the hook."""
    import urllib.request  # here, not at the top: it adds ~80 ms to every hook start

    chunks = []
    with urllib.request.urlopen(url, timeout=30) as response:
        while chunk := response.read(1 << 16):
            if time.monotonic() > _deadline:
                raise RuntimeError("ran out of time downloading it")
            chunks.append(chunk)
    return b"".join(chunks)


def download_trufflehog(target: Path) -> None:
    import io, tarfile  # here, not at the top: they add to every hook start

    asset = release_asset()
    if not asset:
        raise RuntimeError(f"no TruffleHog release for {sys.platform}/{platform.machine()}")
    base = f"{TRUFFLEHOG_RELEASES}/v{TRUFFLEHOG_VERSION}"
    sums = fetch(f"{base}/trufflehog_{TRUFFLEHOG_VERSION}_checksums.txt").decode()
    expected = next((line.split()[0] for line in sums.splitlines() if line.strip().endswith(asset)), None)
    if not expected:
        raise RuntimeError(f"{asset} is not in the release checksums")
    blob = fetch(f"{base}/{asset}")
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


def secret_hits(top: str, base: str, timeout: float = 100) -> list[dict]:
    """Secrets TruffleHog finds in the commits base..HEAD. Raises ScannerUnavailable."""
    binary = trufflehog_bin()
    timeout = min(timeout, _deadline - time.monotonic())
    if timeout <= 0:
        raise ScannerUnavailable("no time left to run TruffleHog in this hook call")
    cmd = [binary, "git", f"file://{top}", "--json", "--no-update",
           "--since-commit", base, "--branch", "HEAD", "--config", str(PLUGIN_ROOT / "config" / "trufflehog.yaml")]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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


# Detection reads the whole text, so it fails closed: quoted text, heredoc bodies, inline code
# and command substitutions all count. A subcommand counts right after its program, or anywhere
# after it once an option comes first (`git -C "a b" -c x=1 push`, `gh -R x pr create`), so no
# number of options or values hides it.
_WORD_SEP = re.compile(r"""[\s"',\\\[\]();&|<>`{}$=]+""")
PULLS_TEXT = re.compile(r"/pulls(?![\w-])")
GRAPHQL_PR_TEXT = re.compile(r"createPullRequest")


def detect(command: str) -> set[str]:
    """The kinds of gated call ("push", "pr") a Bash command's text contains anywhere."""
    words = [w for w in _WORD_SEP.split(command) if w]
    where: dict[str, list[int]] = {}
    for i, w in enumerate(words):
        where.setdefault(w, []).append(i)

    def then(i: int, sub: str) -> int | None:
        """Index of the `sub` that follows the program or subcommand at words[i], if any."""
        if i + 1 < len(words) and words[i + 1] == sub:
            return i + 1
        if i + 1 < len(words) and words[i + 1].startswith("-"):
            after = where.get(sub, [])
            k = bisect.bisect_right(after, i + 1)
            return after[k] if k < len(after) else None
        return None

    kinds = set()
    for i, w in enumerate(words):
        name = w.rsplit("/", 1)[-1]
        if name == "git" and then(i, "push") is not None:
            kinds.add("push")
        elif name == "gh":
            pr = then(i, "pr")
            if (pr is not None and (then(pr, "create") is not None or then(pr, "new") is not None)) or \
                    (then(i, "api") is not None and PULLS_TEXT.search(command)):
                kinds.add("pr")
    if GRAPHQL_PR_TEXT.search(command):
        kinds.add("pr")
    return kinds


# Outside quotes, the words of a command the gate follows hold only these characters.
_PLAIN = re.compile(r"[\w./:,@%+=-]+")
REDIRECTING_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GH_REPO")


def plain_commands(command: str) -> list[list[str]] | None:
    """argv of each command of a line joined only by `&&`, quotes removed. None when the line has
    anything else: another operator, a redirection, an expansion, an escape, a glob, a line break."""
    commands: list[list[str]] = [[]]
    word: str | None = None
    i = 0
    while i < len(command):
        c = command[i]
        if c in "'\"":
            end = command.find(c, i + 1)
            body = command[i + 1:end]
            if end < 0 or (c == '"' and set(body) & set("$`\\!")):
                return None
            word, i = (word or "") + body, end + 1
        elif c in " \t" or command.startswith("&&", i):
            if word is not None:
                commands[-1].append(word)
            if c == "&":
                commands.append([])
            word, i = None, i + (2 if c == "&" else 1)
        elif word is None and c == "~" and command[i + 1:i + 2] in ("", "/", " ", "\t"):
            word, i = os.path.expanduser("~"), i + 1
        else:
            m = _PLAIN.match(command, i)
            if not m or (word is None and c == "="):  # zsh expands a leading =
                return None
            word, i = (word or "") + m.group(), m.end()
    if word is not None:
        commands[-1].append(word)
    return commands if all(commands) else None


def exact_target(command: str, cwd: str) -> tuple[str, str] | None:
    """(kind, checkout) when the whole command is one of the forms the gate follows exactly:
    `cd <dir> && gh pr create …` (without -R), `cd <dir> && git push …`, `git -C <dir> push …`,
    or `<…/heavy-lock.sh> run <label> -- git -C <dir> push …`. None for anything else."""
    commands = plain_commands(command)
    if not commands or any(os.environ.get(name) for name in REDIRECTING_ENV):
        return None
    if len(commands) == 2 and len(commands[0]) == 2 and commands[0][0] == "cd":
        path, rest = commands[0][1], commands[1]
        if not (os.path.isabs(path) or path in (".", "..") or path.startswith(("./", "../"))):
            return None  # a bare name may follow CDPATH
        # cd resolves `..` before symlinks; git then runs in the physical directory.
        directory = os.path.realpath(os.path.normpath(os.path.join(cwd, path)))
        if rest[:2] == ["git", "push"]:
            kind = "push"
        elif rest[:3] == ["gh", "pr", "create"] and not any(REPO_FLAG.match(a) for a in rest[3:]):
            kind = "pr"
        else:
            return None
    elif len(commands) == 1:
        argv = commands[0]
        if os.path.basename(argv[0]) == "heavy-lock.sh" and argv[1:2] == ["run"] and argv[3:4] == ["--"]:
            argv = argv[4:]
        if len(argv) < 4 or argv[:2] != ["git", "-C"] or argv[3] != "push":
            return None
        # git -C chdir()s, so a symlink is followed before any `..`.
        kind, directory = "push", os.path.realpath(os.path.join(cwd, argv[2]))
    else:
        return None
    top = toplevel(directory) if os.path.isdir(directory) else None
    return (kind, top) if top else None


# Any directory the text names after cd/pushd/chdir/-C/--git-dir/--work-tree/GIT_DIR/GIT_WORK_TREE.
_START = r"(?<![\w.-])"
_NAMES_DIR = r"(?:cd|pushd|chdir|-C|--git-dir|--work-tree|GIT_DIR|GIT_WORK_TREE)"
NAMED_PATH = re.compile(rf"""{_START}{_NAMES_DIR}(?:=|[\s"',\\\[\]]+)([^\s"',\\\[\];&|<>`()]+)""")
NAMED_QUOTED_PATH = re.compile(rf"""{_START}{_NAMES_DIR}(?:=|\s+)(?:"([^"]+)"|'([^']+)')""")
CD_COMMANDS = {"cd", "pushd", "chdir"}
ASSIGNMENT_TEXT = re.compile(r"""(?<![\w$])([A-Za-z_]\w*)=(?:"([^"]*)"|'([^']*)'|([^\s;&|<>`()]+))""")
# gh's short flags bundle, with the value flag last: `-dR owner/name` is `-d -R owner/name`.
REPO_FLAG = re.compile(r"-[A-Za-z]*R|--repo")
REPO_TEXT = re.compile(rf"""{_START}(?:-[A-Za-z]*R|--repo|GH_REPO)(?:=|[\s"']+)?([^\s"';&|<>`()]+)|"""
                       r"""repos/([^\s/"'`]+)/([^\s/"'`]+)/""")
GITHUB_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
REPO_SPEC = re.compile(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
VAMMO_OWNER = "leopardaelectric"


def named_paths(command: str) -> list[str]:
    """Every path the command names: the raw text matches of NAMED_PATH (quoted code and heredoc
    bodies count), plus, when the line splits into shell words, the word after cd/pushd/chdir
    (past their options), -C, --git-dir or --work-tree, and the value of GIT_DIR=/GIT_WORK_TREE=.
    Shell words join quoted parts and drop escapes (`"$HOME"/x`, `a\\ b`)."""
    paths = [m.group(1) for m in NAMED_PATH.finditer(command)] + \
        [m.group(1) or m.group(2) for m in NAMED_QUOTED_PATH.finditer(command)]
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return paths
    for i, word in enumerate(words):
        name, eq, value = word.partition("=")
        if word in CD_COMMANDS:
            j = i + 1
            while j < len(words) and words[j].startswith("-") and words[j] != "-":
                j += 1
            paths += words[j:j + 1]
        elif word in ("-C", "--git-dir", "--work-tree"):
            paths += words[i + 1:i + 2]
        elif eq and name in ("GIT_DIR", "GIT_WORK_TREE", "--git-dir", "--work-tree"):
            paths.append(value)
    return paths


def named_dirs(command: str, cwd: str) -> list[str]:
    """The session's directory, then every existing directory the command names (named_paths),
    with each value the command assigns to a variable, then the environment's, expanded; each
    both as written and with its symlinks resolved, and also under every CDPATH entry."""
    assigned: dict[str, set[str]] = {}
    for m in ASSIGNMENT_TEXT.finditer(command):
        assigned.setdefault(m.group(1), set()).add(next(g for g in m.groups()[1:] if g is not None))
    dirs = [cwd]
    cdpath = [p for p in os.environ.get("CDPATH", "").split(":") if p]
    for path in named_paths(command):
        variants = {path}
        for name, values in sorted(assigned.items(), key=lambda item: -len(item[0])):
            variants = {v.replace(f"${{{name}}}", value).replace(f"${name}", value)
                        for v in variants for value in values}
        for variant in variants:
            variant = os.path.expanduser(os.path.expandvars(variant))
            for base in dirs[:] + cdpath:
                joined = os.path.join(base, variant)
                for full in (os.path.normpath(joined), os.path.realpath(joined)):
                    found = os.path.dirname(full) if os.path.basename(full) == ".git" and os.path.exists(full) else full
                    if os.path.isdir(found) and found not in dirs:
                        dirs.append(found)
    return dirs


def named_repos(command: str) -> set[str]:
    """owner/name (lowercase) of every repository the text names after -R/--repo/GH_REPO or in
    a `repos/<owner>/<name>/` path. A placeholder name (`{repo}`, `:repo`) is kept as is."""
    specs = set()
    for m in REPO_TEXT.finditer(command):
        spec = REPO_SPEC.search(m.group(1)) if m.group(1) else None
        owner, name = (spec.group(1), spec.group(2)) if spec else (m.group(2), m.group(3))
        if owner and name:
            specs.add(f"{owner}/{name}".lower())
    return specs


def held_repos(specs: set[str], dirs: list[str]) -> list[tuple[str, None, str]]:
    """A held PR step for each Vammo repository named that none of the checkouts at dirs is."""
    known = set().union(*(github_repos(d) for d in dirs))
    held = []
    for spec in sorted(specs):
        owner, name = spec.split("/", 1)
        placeholder = bool(set(name) & set("{}:"))
        if owner == VAMMO_OWNER and spec not in known and \
                not (placeholder and any(r.startswith(f"{VAMMO_OWNER}/") for r in known)):
            held.append(("pr", None, spec))
    return held


def gate_plan(kinds: set[str], tool_name: str, tool_input: dict,
              cwd: str) -> tuple[list[tuple[str, str | None, str | None]], bool]:
    """([(kind, checkout to judge, Vammo repo held as unresolved)], whether the plan judges just
    what the command targets). Held repositories come first, then pushes, then pull requests.

    Only an exact form (see exact_target) skips the session's checkout. Anything else is judged
    on the session's checkout and on every checkout the command names."""
    if tool_name != "Bash":
        owner, repo = tool_input.get("owner"), tool_input.get("repo")
        specs = {f"{owner}/{repo}".lower()} if owner and repo else set()
        return held_repos(specs, [cwd]) + [("pr", cwd, None)], True
    command = str(tool_input.get("command", ""))
    exact = exact_target(command, cwd)
    if exact and {exact[0]} == kinds:
        return [(exact[0], exact[1], None)], True
    dirs = named_dirs(command, cwd)
    plan = held_repos(named_repos(command), dirs) if "pr" in kinds else []
    return plan + [(kind, d, None) for kind in ("push", "pr") if kind in kinds for d in dirs], len(dirs) == 1


def github_repos(directory: str) -> set[str]:
    """owner/name (lowercase) of every github.com remote of the checkout at directory."""
    try:
        lines = git(directory, "remote", "-v").splitlines()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return set()
    matches = (GITHUB_REMOTE.search(line.split()[1]) for line in lines if len(line.split()) > 1)
    return {f"{m.group(1)}/{m.group(2)}".lower() for m in matches if m}


def secrets_gate(top: str, gate: str, base: str | None, timeout: float) -> bool:
    """True when the gate denied the call over secrets."""
    if not base:
        return False
    try:
        if base == git(top, "rev-parse", "HEAD"):
            return False  # nothing new to publish
    except RuntimeError:
        return False
    try:
        hits = secret_hits(top, base, timeout)
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


def toplevel(directory: str) -> str | None:
    try:
        return os.path.realpath(git(directory, "rev-parse", "--show-toplevel"))
    except RuntimeError:  # git answered: not a checkout. A timeout or a missing git holds the call.
        return None


# hooks.json gives the hook 240 s, and a hook that times out doesn't block the call.
HOOK_DEADLINE = 200
SCAN_TIMEOUT = 100


def cmd_hook() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(event, dict):
        return 0
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    if tool_name == "Bash":
        kinds = detect(str(tool_input.get("command", "")))
    # GitHub MCP servers name the tool create_pull_request (mcp__<server>__create_pull_request).
    elif tool_name.startswith("mcp__") and tool_name.endswith("__create_pull_request"):
        kinds = {"pr"}
    else:
        return 0
    if not kinds:
        return 0
    cwd = str(event.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    gate = "pull request" if "pr" in kinds else "push"
    try:
        run_gates(kinds, tool_name, tool_input, cwd, gate)
    except Exception as e:  # fail closed: an error must never let the call through unchecked
        deny(f"Vammo security check: the check couldn't complete ({type(e).__name__}: {str(e)[:300]}), so "
             f"this {gate} is held. Tell the person what failed and help them fix it, then retry the {gate}. "
             "There is no way to skip this check.")
    return 0


def run_gates(kinds: set[str], tool_name: str, tool_input: dict, cwd: str, gate: str) -> None:
    """Runs every step of the call's plan until one denies it."""
    global _deadline
    deadline = _deadline = time.monotonic() + HOOK_DEADLINE
    plan, followed = gate_plan(kinds, tool_name, tool_input, cwd)
    judged = set()
    for kind, directory, unresolved in plan:
        if unresolved:
            deny(f"Vammo pre-PR security check: this pull request targets {unresolved}, which is not the "
                 "repository this session is in, so its HEAD can't be checked from here and the pull "
                 f"request is held. Open it from a checkout of {unresolved} (`cd <its worktree> && ...`), "
                 "so the checks run on that checkout's own HEAD. There is no way to skip this check.")
            return
        top = toplevel(directory) if directory else None
        if not top or (kind, top) in judged:
            continue
        judged.add((kind, top))
        left = deadline - time.monotonic()
        if left < 10:
            deny(f"Vammo security check: this command names more checkouts than can be checked in one call, "
                 f"so the {gate} is held. Run it from the checkout it targets, one command per checkout: "
                 f"{EXACT_FORMS}. There is no way to skip this check.")
            return
        if gate_checkout(kind, top, cwd, tool_input, followed, timeout=min(SCAN_TIMEOUT, left)):
            return


def gate_checkout(kind: str, directory: str, session_dir: str, tool_input: dict, followed: bool = True,
                  timeout: float = SCAN_TIMEOUT) -> bool:
    """Runs the push or PR gate on the checkout at directory. True when it denied the call.

    followed: whether the plan judges just what the command targets; when not, the message says
    why this checkout was judged."""
    try:
        top = git(directory, "rev-parse", "--show-toplevel")
        head = git(directory, "rev-parse", "HEAD")
    except RuntimeError:
        return False  # not a git checkout: nothing to check
    if not is_vammo_repo(top):
        return False

    if kind == "push":
        return secrets_gate(top, "push", push_base(top), timeout)

    m = PR_BASE_FLAG.search(" " + str(tool_input.get("command", "")))
    preferred = m.group(1) if m else (str(tool_input.get("base")) if tool_input.get("base") else None)
    if secrets_gate(top, "pull request", default_base(top, preferred), timeout):
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
        + ("" if followed else UNION_NOTE)
    )
    return True


EXACT_FORMS = ("`cd <dir> && gh pr create …` (without -R), `cd <dir> && git push …`, `git -C <dir> push …` "
               "or `heavy-lock.sh run <label> -- git -C <dir> push …`, with plain words only")
UNION_NOTE = (f" This command isn't one of the forms the gate follows exactly ({EXACT_FORMS}), so the "
              "session's checkout and every checkout the command names are judged. To publish from another "
              "checkout, use one of those forms.")


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
