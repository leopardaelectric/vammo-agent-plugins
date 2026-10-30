#!/usr/bin/env python3
"""Vammo security gates for Claude Code: secrets before push and PR, and a scan before PR.

Two PreToolUse gates, both deterministic: no model runs inside this script.

- Before `git push`: TruffleHog runs on the commits being pushed. A secret holds the push.
- Before a pull request is created (`gh pr create`, a `gh api` call to `.../pulls`, a GraphQL
  `createPullRequest`, or a GitHub MCP `create_pull_request` tool), two checks run:
  1. TruffleHog runs on the branch's commits. A secret holds the PR.
  2. The branch's HEAD must be "covered": it was scanned by the claude-security plugin, or
     it is the commit that applied that scan's patches. If it isn't, Claude is told to run
     the pre-pr-security skill first. There is no skip.

Both gates fail closed. Whether a Bash command pushes or opens a PR is read from its whole
text, so a command that only mentions one (quoted, in a heredoc body, in a commit message)
triggers the gate too. Which checkout is judged: a plain command line (`cd <dir>`,
`git -C <dir> push`, `gh pr create [-R <repo>]`, `gh api repos/<owner>/<name>/pulls`, joined
only by `&&` or `;`) is followed exactly, so `cd <dir> && gh pr create` judges `<dir>`. Any
other shape (a pipe, a subshell, `bash -c`, inline interpreter code, a heredoc, a variable)
is judged on the session's checkout and on every checkout the command names after `cd`, `-C`,
`--git-dir` or `GIT_DIR`. A pull request for another Vammo repository (`-R`, the `gh api`
endpoint's owner/name, or the MCP tool's owner/repo) that none of those checkouts is, is held,
with instructions to open it from that repository's checkout.

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
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
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


def download_trufflehog(target: Path) -> None:
    import io, tarfile, urllib.request  # here, not at the top: they add ~80 ms to every hook start

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


# Detection reads the whole text, so it fails closed: quoted text, heredoc bodies, inline code
# and command substitutions all count. Between words: blanks, quotes, list punctuation (`"git",
# "push"`) and backslashes. Up to six global options may sit between the program and its
# subcommand, each with up to four words of value (`git -C "a b" push`, `gh -R x pr create`).
_SEP = r"""[\s"',\\\[\]]+"""
_OPT = r"""-[^\s"',\\\[\]]*"""
_VAL = r"""[^\s"',\\\[\]-][^\s"',\\\[\]]*"""
_OPTIONS = rf"(?:{_SEP}{_OPT}(?:{_SEP}{_VAL}){{0,4}}){{0,6}}?"  # bounded: linear time on any text
_START, _END = r"(?<![\w.-])", r"(?![\w-])"
PUSH_TEXT = re.compile(rf"{_START}git{_OPTIONS}{_SEP}push{_END}")
PR_TEXT = re.compile(rf"{_START}gh{_OPTIONS}{_SEP}pr{_OPTIONS}{_SEP}create{_END}")
API_TEXT = re.compile(rf"{_START}gh{_OPTIONS}{_SEP}api{_END}")
PULLS_TEXT = re.compile(r"/pulls(?![\w-])")
GRAPHQL_PR_TEXT = re.compile(r"createPullRequest")


def detect(command: str) -> set[str]:
    """The kinds of gated call ("push", "pr") a Bash command's text contains anywhere."""
    kinds = set()
    if PUSH_TEXT.search(command):
        kinds.add("push")
    if PR_TEXT.search(command) or GRAPHQL_PR_TEXT.search(command) or \
            (API_TEXT.search(command) and PULLS_TEXT.search(command)):
        kinds.add("pr")
    return kinds


class NotSimple(Exception):
    """The command isn't a plain one whose targets the gate follows exactly."""


# Outside quotes, any of these makes a command line not simple: expansions, subshells, groups,
# pipes, negation, escapes, globs, comments, zsh's `^`, line breaks.
NOT_SIMPLE_CHARS = set("$`(){}|!\\*?[]#^\n\r")
NOT_SIMPLE_COMMANDS = {"if", "then", "else", "elif", "fi", "while", "until", "for", "do", "done", "case", "esac",
                       "select", "function", "time", "coproc", "eval", "exec", "source", ".", "builtin", "command",
                       "pushd", "popd", "alias"}
# Shells, interpreters and wrappers that may run code or change directory (`env -C`, `sudo -D`).
RUNS_CODE = re.compile(r"(?:ba|z|da|k|c|tc|fi)?sh|env|sudo|doas|xargs|parallel|"
                       r"(?:python|pypy|node|nodejs|deno|bun|perl|ruby|php|lua|osascript|pwsh)[\d.]*")
# Wrappers known to run their command in the current directory.
WRAPPERS = {"timeout", "nice", "nohup", "caffeinate", "heavy-lock.sh"}
GIT_FLAGS = {"--no-pager", "-P", "-p", "--paginate", "--no-optional-locks", "--no-replace-objects"}
GH_PR_VALUE_FLAGS = {"-a", "--assignee", "-B", "--base", "-b", "--body", "-F", "--body-file", "-H", "--head",
                     "-l", "--label", "-m", "--milestone", "-p", "--project", "-r", "--reviewer", "-T", "--template",
                     "-t", "--title", "-R", "--repo", "--recover"}
GH_PR_FLAGS = {"-d", "--draft", "--dry-run", "-e", "--editor", "-f", "--fill", "--fill-first", "--fill-verbose",
               "--no-maintainer-edit", "-w", "--web"}
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
API_ENDPOINT = re.compile(r"(?:https://api\.github\.com)?/?repos/([\w.-]+)/([\w.-]+)/pulls(?:[/?].*)?",
                          re.IGNORECASE | re.DOTALL)
GITHUB_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
REPO_SPEC = re.compile(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
VAMMO_OWNER = "leopardaelectric"


def simple_commands(command: str) -> list[list[str]]:
    """argv of each command of a line of plain commands joined by `&&` or `;`, quotes removed.

    Redirections to a file or a descriptor are dropped. Raises NotSimple for anything else."""
    commands: list[list[str]] = [[]]
    word: str | None = None
    redirect = False  # the next word is where a redirection goes, not an argument

    def finish() -> None:
        nonlocal word, redirect
        if word is not None:
            if redirect:
                redirect = False
            else:
                commands[-1].append(word)
        word = None

    i = 0
    while i < len(command):
        c = command[i]
        if c in "'\"":
            end = command.find(c, i + 1)
            body = command[i + 1:end]
            if end < 0 or (c == '"' and set(body) & set("$`\\!")):
                raise NotSimple("unbalanced quote, or an expansion inside double quotes")
            word, i = (word or "") + body, end + 1
        elif c in " \t":
            finish()
            i += 1
        elif c == ";" or command.startswith("&&", i):
            finish()
            if redirect:
                raise NotSimple("redirection without a target")
            commands.append([])
            i += 1 if c == ";" else 2
        elif c in "<>":
            if word is not None and word.isdigit():
                word = None  # the descriptor of `2>`
            finish()
            if redirect or command[i:i + 2] in ("<<", "<>", "<(", ">(", ">|"):
                raise NotSimple("heredoc, here-string or process substitution")
            i += 2 if command.startswith(">>", i) else 1
            dup = re.match(r"&(\d+|-)", command[i:])
            if command.startswith("&", i) and not dup:
                raise NotSimple("redirection to an unknown descriptor")
            i, redirect = (i + dup.end(), False) if dup else (i, True)
        elif c == "&" or c in NOT_SIMPLE_CHARS:
            raise NotSimple(f"{c!r} outside quotes")
        elif word is None and c == "~":
            if command[i + 1:i + 2] not in ("", "/", " ", "\t", ";", "&", "<", ">"):
                raise NotSimple("~user")
            word, i = os.path.expanduser("~"), i + 1
        elif word is None and c == "=":
            raise NotSimple("zsh =command expansion")
        else:
            word, i = (word or "") + c, i + 1
    finish()
    if redirect:
        raise NotSimple("redirection without a target")
    return [argv for argv in commands if argv]


def literal_dir(here: str, path: str, bare_name: bool) -> str:
    """here/path when it names an existing directory literally. Raises NotSimple otherwise.

    A bare relative name is only allowed where no search path applies (`git -C x`, not `cd x`,
    which may follow CDPATH)."""
    if not path or path.startswith(("-", "+")):
        raise NotSimple(f"directory {path!r}")
    if not bare_name and not (os.path.isabs(path) or path in (".", "..") or path.startswith(("./", "../"))):
        raise NotSimple(f"cd {path} may follow CDPATH")
    full = os.path.normpath(os.path.join(here, path))
    if not os.path.isdir(full):
        raise NotSimple(f"{full} is not a directory")
    return full


def git_push_dir(args: list[str], here: str) -> str | None:
    """The directory `git <args>` pushes from, or None when it doesn't push. Raises NotSimple."""
    i = 0
    while i < len(args):
        if args[i] in ("-C", "-c") and i + 1 < len(args):
            here = literal_dir(here, args[i + 1], bare_name=True) if args[i] == "-C" else here
            i += 2
        elif args[i] in GIT_FLAGS:
            i += 1
        elif args[i].startswith("-"):
            raise NotSimple(f"git {args[i]}")  # --git-dir, --work-tree and the like
        else:
            return here if args[i] == "push" else None
    return None


def gh_pr_repos(args: list[str]) -> list[str | None]:
    """[its -R repo, or None] when `gh <args>` creates a pull request, else []. Raises NotSimple."""
    words = [a for a in args if not a.startswith("-")]
    if words[:1] == ["api"]:
        if any(a.startswith("--hostname") for a in args):
            raise NotSimple("gh api --hostname")
        repos = {f"{m.group(1)}/{m.group(2)}".lower() for m in map(API_ENDPOINT.fullmatch, args) if m}
        if len(repos) > 1:
            raise NotSimple("more than one pulls endpoint")
        return list(repos)
    if "pr" not in words or "create" not in words:
        return []
    words, repos, i = [], [], 0
    while i < len(args):
        arg = args[i]
        name, eq, value = arg.partition("=") if arg.startswith("--") else (arg[:2], "", arg[2:].removeprefix("="))
        if not arg.startswith("-") or arg == "-":
            words.append(arg)
        elif name in GH_PR_VALUE_FLAGS:
            if not (eq or value):
                i += 1
                if i == len(args):
                    raise NotSimple(f"{name} without a value")
                value = args[i]
            if name in ("-R", "--repo"):
                repos.append(value)
        elif name not in GH_PR_FLAGS or eq or value:
            raise NotSimple(f"gh flag {arg}")
        i += 1
    if words != ["pr", "create"] or len(repos) > 1:
        raise NotSimple("not a plain pull request creation")
    return repos or [None]


def simple_calls(command: str, cwd: str) -> list[tuple[str, str, str | None]]:
    """(kind, directory, repo spec) for each push and PR creation of a plain command line.

    Follows `cd <dir>`, `git -C <dir> push`, `gh pr create [-R <repo>]` (`-R` may also sit before
    `create`) and `gh api repos/<owner>/<name>/pulls`, alone or after a known wrapper. Raises
    NotSimple for anything else."""
    here, calls = cwd, []
    for argv in simple_commands(command):
        n = 0
        while n < len(argv) and ASSIGNMENT.match(argv[n]):
            if argv[n].startswith(("GIT_", "GH_")):
                raise NotSimple(f"{argv[n].split('=')[0]} changes the repository")
            n += 1
        words = argv[n:]
        names = [os.path.basename(w) for w in words]
        if not words:
            continue
        if names[0] == "cd":
            if n or len(words) != 2:
                raise NotSimple("cd with options, no argument or an environment")
            here = literal_dir(here, words[1], bare_name=False)
            continue
        if names[0] in NOT_SIMPLE_COMMANDS or "cd" in names[1:] or os.path.isdir(os.path.join(here, words[0])) \
                or any(RUNS_CODE.fullmatch(name) for name in names):
            raise NotSimple(f"{names[0]} may run code or change directory")
        start = next((j for j, name in enumerate(names) if name in ("git", "gh")), None) \
            if names[0] in WRAPPERS else 0
        if start is None:
            continue
        if names[start] == "git":
            where = git_push_dir(words[start + 1:], here)
            calls += [("push", where, None)] if where else []
        elif names[start] == "gh":
            calls += [("pr", here, repo) for repo in gh_pr_repos(words[start + 1:])]
        elif "git" in names or "gh" in names:
            raise NotSimple("git or gh where the gate doesn't follow it")
    return calls


# Any directory the text names after cd/pushd/-C/--git-dir/--work-tree/GIT_DIR/GIT_WORK_TREE.
_NAMES_DIR = r"(?:cd|pushd|-C|--git-dir|--work-tree|GIT_DIR|GIT_WORK_TREE)"
NAMED_PATH = re.compile(rf"""{_START}{_NAMES_DIR}(?:=|[\s"',\\\[\]]+)([^\s"',\\\[\];&|<>`()]+)""")
NAMED_QUOTED_PATH = re.compile(rf"""{_START}{_NAMES_DIR}(?:=|\s+)(?:"([^"]+)"|'([^']+)')""")
ASSIGNMENT_TEXT = re.compile(r"""(?<![\w$])([A-Za-z_]\w*)=(?:"([^"]*)"|'([^']*)'|([^\s;&|<>`()]+))""")
REPO_TEXT = re.compile(rf"""{_START}(?:-R|--repo|GH_REPO)(?:=|[\s"']+)?([^\s"';&|<>`()]+)|"""
                       r"""repos/([^\s/"'`]+)/([^\s/"'`]+)/""")


def named_dirs(command: str, cwd: str) -> list[str]:
    """The session's directory, then every existing directory the command names (see NAMED_PATH),
    with variables the command assigns, then the environment's, expanded."""
    assigned = {m.group(1): next(g for g in m.groups()[1:] if g is not None) for m in ASSIGNMENT_TEXT.finditer(command)}
    raw = [(m.start(), m.group(1)) for m in NAMED_PATH.finditer(command)] + \
        [(m.start(), m.group(1) or m.group(2)) for m in NAMED_QUOTED_PATH.finditer(command)]
    dirs = [cwd]
    for _, path in sorted(raw):
        for name, value in sorted(assigned.items(), key=lambda item: -len(item[0])):
            path = path.replace(f"${{{name}}}", value).replace(f"${name}", value)
        path = os.path.expanduser(os.path.expandvars(path))
        for base in list(dirs):
            full = os.path.normpath(os.path.join(base, path))
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


def union_plan(kinds: set[str], command: str, cwd: str) -> list[tuple[str, str | None, str | None]]:
    """The plan for a command whose targets can't be followed: every detected gate on the
    session's checkout and every checkout the command names; and, for a PR, every Vammo
    repository named that none of those checkouts is, held."""
    dirs = named_dirs(command, cwd)
    plan = [(kind, d, None) for kind in ("push", "pr") if kind in kinds for d in dirs]
    if "pr" in kinds:
        known = set().union(*(github_repos(d) for d in dirs))
        for spec in sorted(named_repos(command)):
            owner, name = spec.split("/", 1)
            if owner != VAMMO_OWNER:
                continue
            placeholder = bool(set(name) & set("{}:"))
            if spec not in known and not (placeholder and any(r.startswith(f"{VAMMO_OWNER}/") for r in known)):
                plan.append(("pr", None, spec))
    return plan


def gate_plan(kinds: set[str], tool_name: str, tool_input: dict,
              cwd: str) -> tuple[list[tuple[str, str | None, str | None]], bool]:
    """([(kind, checkout to judge, Vammo repo held as unresolved)], whether the targets were
    followed exactly), pushes first. Falls back to union_plan on anything it can't follow."""
    if tool_name != "Bash":
        owner, repo = tool_input.get("owner"), tool_input.get("repo")
        return [("pr", *pr_target(f"{owner}/{repo}" if owner and repo else None, cwd))], True
    command = str(tool_input.get("command", ""))
    try:
        calls = simple_calls(command, cwd)
        if not kinds <= {kind for kind, _, _ in calls}:
            raise NotSimple("the text has a gated command the parser didn't place")
        plan = [("push", where, None) if kind == "push" else ("pr", *pr_target(repo, where))
                for kind, where, repo in calls]
        return sorted(plan, key=lambda step: step[0] != "push"), True
    except Exception:  # anything unexpected fails closed onto the union, never open
        return union_plan(kinds, command, cwd), False


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


def toplevel(directory: str) -> str | None:
    try:
        return os.path.realpath(git(directory, "rev-parse", "--show-toplevel"))
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None


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
    try:
        plan, followed = gate_plan(kinds, tool_name, tool_input, cwd)
    except Exception:  # never let an error in the target logic skip the gates
        plan, followed = [(kind, cwd, None) for kind in ("push", "pr") if kind in kinds], False
    judged = set()
    for kind, directory, unresolved in plan:
        if unresolved:
            deny(f"Vammo pre-PR security check: this pull request targets {unresolved}, which is not the "
                 "repository this session is in, so its HEAD can't be checked from here and the pull "
                 f"request is held. Open it from a checkout of {unresolved} (`cd <its worktree> && ...`), "
                 "so the checks run on that checkout's own HEAD. There is no way to skip this check.")
            return 0
        top = toplevel(directory) if directory else None
        if not top or (kind, top) in judged:
            continue
        judged.add((kind, top))
        if gate_checkout(kind, top, cwd, tool_input, followed):
            return 0
    return 0


def gate_checkout(kind: str, directory: str, session_dir: str, tool_input: dict, followed: bool = True) -> bool:
    """Runs the push or PR gate on the checkout at directory. True when it denied the call.

    followed: whether the command's targets were followed exactly; when not, the message says why
    this checkout was judged."""
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
        + ("" if followed else UNION_NOTE)
    )
    return True


UNION_NOTE = (" This command's shape isn't one the gate follows (only plain `cd <dir>`, `git -C <dir> push`, "
              "`gh pr create [-R <repo>]` and `gh api repos/<owner>/<name>/pulls` commands joined by `&&` or `;` "
              "are), so the session's checkout and every checkout the command names are judged. To publish "
              "from another checkout, run a plain command from it instead.")


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
