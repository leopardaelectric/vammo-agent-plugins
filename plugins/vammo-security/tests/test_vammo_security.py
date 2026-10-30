"""Unit tests for vammo_security.py: which calls are gated, which checkouts they are judged on,
the TruffleHog install and state saves.

Run: python3 -m unittest discover -s plugins/vammo-security/tests -v
No network: downloads are served by a fake urlopen.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN / "scripts" / "vammo_security.py"
spec = importlib.util.spec_from_file_location("vammo_security", SCRIPT)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

PR = "gh " + "pr " + "create"
PR_REPO_FIRST = "gh " + "pr --repo leopardaelectric/x " + "create"
GIT_ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def bash(command: str) -> dict:
    return {"command": command}


class TempDirs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.session = os.path.join(self.root, "session")
        self.other = os.path.join(self.root, "other")
        os.makedirs(self.session)
        os.makedirs(self.other)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def git_repo(self, path: str, remote: str, commit: bool = False) -> None:
        subprocess.run(["git", "init", "-q", path], check=True, env=GIT_ENV)
        subprocess.run(["git", "-C", path, "remote", "add", "origin", remote], check=True, env=GIT_ENV)
        if commit:
            subprocess.run(["git", "-C", path, "commit", "-q", "--allow-empty", "-m", "c"], check=True, env=GIT_ENV)

    def plan(self, command: str) -> tuple[list, bool]:
        return vs.gate_plan(vs.detect(command), "Bash", bash(command), self.session)


class Detect(unittest.TestCase):
    def assertKinds(self, command: str, kinds: set[str]) -> None:
        self.assertEqual(vs.detect(command), kinds, command)

    def test_push_with_widened_boundaries_and_global_options(self) -> None:
        for command in ("git push origin br", "git -C x push", "git -c core.x=1 --no-pager push",
                        "git --git-dir=x/.git push", "git --work-tree x push", "git -C \"a b\" push",
                        "/usr/bin/git push", "\"git\" \"push\"", "echo $(git push)", "x=`git push`",
                        "{git,push}", "! git push", "subprocess.run(['git', '-C', d, 'push'])",
                        "git \\\n  push", "git -C $(pwd) push",
                        "git " + " ".join(f"-c {k}.x=1" for k in "abcdefghij") + " push",
                        "git -c \"x.y=a b c d e f g\" push"):
            self.assertKinds(command, {"push"})

    def test_pr_creation_forms(self) -> None:
        for command in (f"{PR} --fill", PR_REPO_FIRST, "gh -R leopardaelectric/x pr create",
                        f"os.system('{PR}')", "gh api repos/o/n/pulls", "gh api repos/o/n/pulls -X GET",
                        "gh api repos/o/n/pulls/12/comments", "EP=repos/o/n/pulls; gh api \"$EP\"",
                        "gh api graphql -f query='mutation{createPullRequest(input:{})}'",
                        "gh pr new --fill", "gh -R x pr new"):
            self.assertKinds(command, {"pr"})

    def test_text_that_only_mentions_the_commands_triggers_the_gate(self) -> None:
        # Decision R3: mentions are an accepted false positive, so nothing that runs can slip by.
        self.assertKinds(f"cat <<'EOF' > notes.txt\nthen run {PR} --base main\nEOF", {"pr"})
        self.assertKinds(f"python3 -c 'print(1)' --label ' {PR} '", {"pr"})
        self.assertKinds(f"git commit -m 'then {PR} and git push'", {"pr", "push"})

    def test_calls_that_are_not_gated(self) -> None:
        for command in ("git status && gh pr view 12", "ls -la", "git commit -m 'push it'",
                        "gh api repos/o/n/issues -X POST", "node -e 'x(\"gh pr view; git pushd\")'",
                        "github pr create", "git log --oneline"):
            self.assertKinds(command, set())

    def test_detection_takes_linear_time(self) -> None:
        text = "echo 'git -C x " * 3000 + "\n" + "word " * 50000 + "gh -R " * 5000 + " git -C '" * 3000
        start = time.perf_counter()
        vs.detect(text)
        self.assertLess(time.perf_counter() - start, 1.0)


class ExactForms(TempDirs):
    """Only four exact forms are judged on their target alone; anything else adds the session."""

    def setUp(self) -> None:
        super().setUp()
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo.git")

    def assertExact(self, command: str, plan: list) -> None:
        self.assertEqual(self.plan(command), (plan, True), command)

    def assertNotExact(self, command: str) -> None:
        plan, _ = self.plan(command)
        self.assertIn(self.session, [d for _, d, _ in plan], command)

    def test_the_four_forms(self) -> None:
        o = self.other
        self.assertExact(f"cd {o} && {PR} --title 'fix #12 (x)' --body-file b.md", [("pr", o, None)])
        self.assertExact("cd ../other && git push -u origin br", [("push", o, None)])
        self.assertExact(f"git -C {o} push origin br", [("push", o, None)])
        self.assertExact("git -C ../other push", [("push", o, None)])
        self.assertExact(f"git -C '{o}' push", [("push", o, None)])
        self.assertExact(f"heavy-lock.sh run 'push other' -- git -C {o} push -u origin br", [("push", o, None)])
        self.assertExact(f"/opt/x/heavy-lock.sh run x -- git -C {o} push", [("push", o, None)])

    def test_symlinks(self) -> None:
        os.makedirs(os.path.join(self.session, "sub"))
        os.symlink(os.path.join(self.session, "sub"), os.path.join(self.other, "lnk"))
        self.assertExact(f"git -C {self.other}/lnk/.. push", [("push", self.session, None)])
        self.assertExact(f"cd {self.other}/lnk/.. && git push", [("push", self.other, None)])

    def test_near_misses_judge_the_session_too(self) -> None:
        o = self.other
        for command in (
            f"cd {o} && {PR} -R leopardaelectric/other-repo", f"cd {o} && {PR} --repo=leopardaelectric/other-repo",
            f"cd {o}; git push", f"cd {o} && git push; git push", f"cd {o} && git push | cat",
            f"cd {o} && git push 2>&1", f"cd {o} && git push > log", f"cd {o} && git -c x=1 push",
            f"cd {o} && git push \"$BR\"", f"cd {o} && {PR} --body \"$(cat b.md)\"", f"cd {o} && {PR} --body `cat b`",
            f"cd {o} && {PR} --title 'git push'", f"/usr/bin/cd {o} && git push", f"builtin cd {o} && git push",
            f"cd -P {o} && git push", f"false && cd {o} && git push", f"cd {o} && {PR} && git push",
            f"git -C {o} -c x=1 push", f"git -C {o} --git-dir=x push", f"heavy-lock.sh run a b -- git -C {o} push",
            f"heavy-lock.sh -- git -C {o} push", f"timeout 60 git -C {o} push",
            f"GIT_DIR={self.session}/.git git -C {o} push", f"git -C {o} push\ngit push", f"git -C {o} push & git push",
            f"git -C {o}/missing push", f"git -C {self.root} push", f"{PR} --fill",
            f"cd {o} && gh api repos/leopardaelectric/other-repo/pulls -X POST --input b.json",
            f"cd {o} && {PR} -dR leopardaelectric/x", f"cd {o} && {PR} -fwRleopardaelectric/x",
            f"cd {o} && gh pr new --fill",
        ):
            self.assertNotExact(command)

    def test_redirecting_environment_turns_the_forms_off(self) -> None:
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GH_REPO"):
            with mock.patch.dict(os.environ, {name: "x"}):
                self.assertNotExact(f"git -C {self.other} push")

    def test_non_vammo_targets_still_judge_the_session(self) -> None:
        self.assertEqual(self.plan(f"{PR} -R someone/else --fill"), ([("pr", self.session, None)], True))
        self.assertEqual(self.plan(f"git push; {PR} -R someone/else"),
                         ([("push", self.session, None), ("pr", self.session, None)], True))

    def test_mcp_owner_and_repo(self) -> None:
        def plan(tool_input: dict) -> tuple[list, bool]:
            return vs.gate_plan({"pr"}, "mcp__github__create_pull_request", tool_input, self.session)

        self.assertEqual(plan({"owner": "leopardaelectric", "repo": "other-repo"}),
                         ([("pr", None, "leopardaelectric/other-repo"), ("pr", self.session, None)], True))
        self.assertEqual(plan({"owner": "leopardaelectric", "repo": "session-repo"}), ([("pr", self.session, None)], True))
        self.assertEqual(plan({"owner": "someone", "repo": "else"}), ([("pr", self.session, None)], True))
        self.assertEqual(plan({"title": "no owner"}), ([("pr", self.session, None)], True))


class UnionPlans(TempDirs):
    """Anything else: the session's checkout and every checkout named, never fewer."""

    def assertUnion(self, command: str, kind: str, named: list[str] | None = None) -> list:
        plan, _ = self.plan(command)
        judged = [d for k, d, held in plan if k == kind and not held]
        self.assertEqual(judged, [self.session] + (named or []), command)
        return plan

    def assertJudged(self, command: str, kind: str, named: list[str]) -> None:
        """The session's checkout first, and every one of named somewhere after it."""
        plan, _ = self.plan(command)
        judged = [d for k, d, held in plan if k == kind and not held]
        self.assertEqual(judged[:1], [self.session], command)
        for d in named:
            self.assertIn(d, judged, command)

    def test_review_shell_options_here_strings_heredocs_and_backticks(self) -> None:
        o = self.other
        self.assertUnion("bash -euo pipefail -c 'git push'", "push")
        self.assertUnion(f"bash -o pipefail -c 'cd {o} && git push'", "push", [o])
        self.assertUnion("bash --rcfile f -c 'git push'", "push")
        self.assertUnion("bash -c -- 'git push'", "push")
        self.assertUnion("bash -s x <<EOF\ngit push\nEOF", "push")
        self.assertUnion(f"bash <<< 'cd {o} && git push'", "push", [o])
        self.assertUnion(f"python3 - <<'PY'\nimport os\nos.system('cd {o} && git push')\nPY", "push", [o])
        self.assertUnion(f"x=`cd {o} && git push`", "push", [o])

    def test_review_unresolvable_directories(self) -> None:
        o = self.other
        self.git_repo(o, "https://github.com/leopardaelectric/other-repo.git")
        self.assertUnion(f"if cd {o}; then git push; fi", "push", [o])
        self.assertUnion(f"{{ cd {o}; }}; git push", "push", [o])
        self.assertUnion(f"D={o}; cd \"$D\" && git push", "push", [o])
        self.assertUnion(f"GIT_DIR={o}/.git git push", "push", [o])
        self.assertUnion(f"git --git-dir={o}/.git push", "push", [o])
        self.assertUnion(f"cd {o} && cd - && git push", "push", [o])
        self.assertUnion("cat <<EOF\nnever ends && git push", "push")
        self.assertUnion(f"cd {self.root}/missing && {PR}", "pr")
        self.assertUnion("git -C \"$NOT_SET_ANYWHERE\" push", "push")
        os.makedirs(os.path.join(self.session, "sub"))
        self.assertUnion("cd sub && git push", "push", [os.path.join(self.session, "sub")])  # may follow CDPATH
        self.assertUnion(f"env -C {o} git push", "push", [o])
        self.assertUnion(f"builtin cd {o}; git push", "push", [o])

    def test_review_cd_options_quoting_escapes_and_chdir(self) -> None:
        o = self.other
        spaced = os.path.join(self.root, "my repo")
        os.makedirs(spaced)
        self.assertJudged(f"cd -- {o} && git push && echo", "push", [o])
        self.assertJudged(f"cd -P {o} && git push && echo", "push", [o])
        self.assertJudged(f"pushd -q {o} && git push", "push", [o])
        self.assertJudged(f"chdir {o} && git push", "push", [o])
        with mock.patch.dict(os.environ, {"XB": self.root}):
            self.assertJudged("cd \"$XB\"/other && git push && echo", "push", [o])
        self.assertJudged(f"cd '{self.root}'/other && git push && echo", "push", [o])
        self.assertJudged(f"cd {self.root}/my\\ repo && git push && echo", "push", [spaced])

    def test_review_every_value_of_a_variable_and_cdpath(self) -> None:
        o = self.other
        self.assertJudged(f"D={o}; git -C $D push; D={self.session}", "push", [o])
        with mock.patch.dict(os.environ, {"CDPATH": self.root}):
            self.assertJudged("cd other && git push && echo", "push", [o])

    def test_review_bundled_repo_flags_are_held(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        plan = self.assertUnion(f"{PR} -dR leopardaelectric/x", "pr")
        self.assertIn(("pr", None, "leopardaelectric/x"), plan)

    def test_review_heredoc_expansions_and_interpreter_stdin(self) -> None:
        self.assertUnion("cat <<EOF\n$(git push origin HEAD)\nEOF", "push")
        self.assertUnion("python3 - <<'PY'\nimport os\nos.system(' git push origin HEAD ')\nPY", "push")

    def test_review_pipeline_and_background_cd(self) -> None:
        self.assertUnion(f"cd {self.other} | cat; git push origin HEAD", "push", [self.other])
        self.assertUnion(f"cd {self.other} & wait; git push origin HEAD", "push", [self.other])

    def test_review_directory_restoration(self) -> None:
        self.assertUnion(f"cd {self.other}; cd -; {PR} --fill", "pr", [self.other])
        self.assertUnion(f"pushd {self.other}; popd; {PR} --fill", "pr", [self.other])

    def test_review_inline_interpreter_code(self) -> None:
        o, s = self.other, self.session
        plan = self.assertUnion(f"cd {o} && python3 -c 'import os; os.system(\"cd {s} && git push origin HEAD\")'",
                                "push", [o])
        self.assertIn(("push", s, None), plan)
        self.assertUnion(f"python3 -c \"import subprocess; subprocess.run(['git', '-C', '{o}', 'push'])\"", "push", [o])
        self.assertUnion("python3 -c 'import os; os.system(\"git -c k=v push\")'", "push")
        self.assertUnion("python3 -c 'import os; os.system(\"git --no-pager push\")'", "push")
        self.assertUnion("node --eval 'require(\"child_process\").execSync(\"git push origin HEAD\")'", "push")
        self.assertUnion(f"php -r 'exec(\"{PR}\");'", "pr")

    def test_review_graphql_pr_creation(self) -> None:
        self.assertUnion("gh api graphql -f query='mutation{createPullRequest(input:{})}'", "pr")

    def test_review_partial_placeholders_and_gh_repo(self) -> None:
        self.git_repo(self.session, "https://github.com/someone/target.git")
        plan = self.assertUnion("gh api repos/leopardaelectric/{repo}/pulls -X POST --input p.json", "pr")
        self.assertIn(("pr", None, "leopardaelectric/{repo}"), plan)
        plan = self.assertUnion(f"GH_REPO=leopardaelectric/x {PR}", "pr")
        self.assertIn(("pr", None, "leopardaelectric/x"), plan)

    def test_placeholders_in_a_vammo_checkout_judge_it(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        plan = self.assertUnion("gh api repos/{owner}/{repo}/pulls -X POST --input p.json", "pr")
        self.assertEqual([step for step in plan if step[2]], [])

    def test_repeated_repo_flags_hold_every_unknown_vammo_repo(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        plan = self.assertUnion(f"{PR} -R leopardaelectric/a -R leopardaelectric/session-repo", "pr")
        self.assertEqual([step for step in plan if step[2]], [("pr", None, "leopardaelectric/a")])

    def test_a_mention_the_parser_cant_place_is_judged_on_the_session(self) -> None:
        self.assertUnion(f"git commit -m 'then {PR}'", "pr")
        self.assertUnion("echo git push", "push")

    def test_review_mixed_push_and_pr_gates_both(self) -> None:
        plan = self.assertUnion(f"git -C {self.other} push && {PR}", "push", [self.other])
        self.assertEqual([d for k, d, _ in plan if k == "pr"], [self.session, self.other])


class Hook(TempDirs):
    def run_hook(self, command: str) -> dict | None:
        event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.session}
        out = io.StringIO()
        with mock.patch.object(vs.sys, "stdin", io.StringIO(json.dumps(event))), \
                mock.patch.object(vs.sys, "stdout", out):
            self.assertEqual(vs.cmd_hook(), 0)
        return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None

    def gated(self, command: str, deny_first: bool = False) -> list[tuple[str, str, bool]]:
        calls = []

        def record(kind: str, top: str, session: str, tool_input: dict, followed: bool, timeout: float) -> bool:
            calls.append((kind, top, followed))
            return deny_first

        with mock.patch.object(vs, "gate_checkout", record):
            self.run_hook(command)
        return calls

    def test_unresolved_target_message_is_not_specific_to_one_command(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        decision = self.run_hook("gh api repos/leopardaelectric/other-repo/pulls -X POST --input body.json")
        self.assertEqual(decision["permissionDecision"], "deny")
        reason = decision["permissionDecisionReason"]
        self.assertIn("targets leopardaelectric/other-repo, which is not the repository this session is in", reason)
        self.assertIn("Open it from a checkout of leopardaelectric/other-repo (`cd <its worktree> && ...`)", reason)
        self.assertNotIn(PR, reason)

    def test_every_push_and_pr_target_is_gated(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo.git")
        self.assertEqual(self.gated(f"git -C {self.other} push"), [("push", self.other, True)])
        self.assertEqual(self.gated(f"git -C {self.other} push && {PR}"),
                         [("push", self.session, False), ("push", self.other, False),
                          ("pr", self.session, False), ("pr", self.other, False)])
        self.assertEqual(self.gated(f"bash -c 'cd {self.other} && git push && {PR}'"),
                         [("push", self.session, False), ("push", self.other, False),
                          ("pr", self.session, False), ("pr", self.other, False)])

    def test_first_deny_wins(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo.git")
        self.assertEqual(self.gated(f"git -C {self.other} push && {PR}", deny_first=True),
                         [("push", self.session, False)])

    def test_one_gate_per_checkout(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        os.makedirs(os.path.join(self.session, "sub"))
        self.assertEqual(self.gated(f"bash -c 'cd {self.session}/sub && git push'"), [("push", self.session, False)])

    def test_any_exception_holds_the_call(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git", commit=True)
        for target in ("gate_plan", "load", "secret_hits"):
            with mock.patch.object(vs, "secret_hits", return_value=[]), \
                    mock.patch.object(vs, target, side_effect=RuntimeError("boom")), \
                    mock.patch.object(vs, "default_base", return_value="0" * 40), \
                    mock.patch.object(vs, "claude_security_installed", return_value=True):
                decision = self.run_hook(f"{PR} --fill")
            self.assertEqual(decision["permissionDecision"], "deny", target)
            self.assertIn("couldn't complete (RuntimeError: boom)", decision["permissionDecisionReason"], target)

    def test_a_git_timeout_holds_the_call(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        with mock.patch.object(vs, "git", side_effect=vs.subprocess.TimeoutExpired("git", 10)):
            decision = self.run_hook("git push")
        self.assertIn("couldn't complete (TimeoutExpired", decision["permissionDecisionReason"])

    def test_running_out_of_time_holds_the_call(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo.git")
        clock = iter([0, 150, 195])
        timeouts = []

        def record(kind: str, top: str, session: str, tool_input: dict, followed: bool, timeout: float) -> bool:
            timeouts.append(timeout)
            return False

        with mock.patch.object(vs.time, "monotonic", lambda: next(clock)), mock.patch.object(vs, "gate_checkout", record):
            decision = self.run_hook(f"git push && git -C {self.other} push")
        self.assertEqual(timeouts, [50])
        self.assertIn("more checkouts than can be checked in one call", decision["permissionDecisionReason"])

    def test_a_command_with_nothing_gated_exits_before_any_git_call(self) -> None:
        with mock.patch.object(vs, "git", side_effect=AssertionError("git called")):
            self.assertIsNone(self.run_hook("git status && ls -la && gh pr view 12"))

    def test_scan_request_explains_the_union(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git", commit=True)
        with mock.patch.object(vs, "secrets_gate", return_value=False), \
                mock.patch.object(vs, "claude_security_installed", return_value=True):
            union = self.run_hook(f"bash -c 'cd {self.other} && {PR} --fill'")["permissionDecisionReason"]
            followed = self.run_hook(f"{PR} --fill")["permissionDecisionReason"]
        self.assertIn("has not been scanned", union)
        self.assertIn("This command isn't one of the forms the gate follows exactly", union)
        self.assertNotIn("checkout at", union)
        self.assertNotIn("forms the gate follows", followed)


class HooksJson(unittest.TestCase):
    def test_review_every_bash_call_starts_the_script(self) -> None:
        entries = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        bash_hooks = [h for e in entries if e["matcher"] == "Bash" for h in e["hooks"]]
        self.assertEqual(len(bash_hooks), 1)
        self.assertNotIn("if", bash_hooks[0])
        self.assertIn("vammo_security.py\" hook", bash_hooks[0]["command"])
        self.assertIn("mcp__.*__create_pull_request", [e["matcher"] for e in entries])


class SaveState(TempDirs):
    def setUp(self) -> None:
        super().setUp()
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        self.state = vs.state_path(self.session)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.state.parent.iterdir() if p != self.state)

    def test_second_save_lands_while_the_first_holds_its_temp_file(self) -> None:
        real_replace, calls = os.replace, []

        def interleaved(src: str, dst: str) -> None:
            calls.append(src)
            if len(calls) == 1:
                vs.save(self.session, {"from": "second"})  # the other hook saves in between
            real_replace(src, dst)

        with mock.patch.object(os, "replace", interleaved):
            vs.save(self.session, {"from": "first"})
        self.assertEqual(len(set(calls)), 2)
        self.assertTrue(all(os.path.dirname(src) == str(self.state.parent) for src in calls))
        self.assertEqual(vs.load(self.session), {"from": "first"})
        self.assertEqual(self.leftovers(), [])

    def test_failed_rename_cleans_up_and_raises(self) -> None:
        vs.save(self.session, {"kept": True})
        with mock.patch.object(os, "replace", side_effect=OSError("busy")), self.assertRaises(OSError):
            vs.save(self.session, {"kept": False})
        self.assertEqual(vs.load(self.session), {"kept": True})
        self.assertEqual(self.leftovers(), [])


class Install(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "trufflehog-x" / "trufflehog"
        self.body = b"#!/bin/sh\necho fake\n"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("trufflehog")
            info.size = len(self.body)
            tf.addfile(info, io.BytesIO(self.body))
        blob = buf.getvalue()
        sums = f"{hashlib.sha256(blob).hexdigest()}  {vs.release_asset()}\n".encode()

        def fake_urlopen(url: str, timeout: int = 0) -> io.BytesIO:
            return io.BytesIO(sums if url.endswith("checksums.txt") else blob)

        patcher = mock.patch.object(urllib.request, "urlopen", fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.target.parent.iterdir() if p != self.target)

    def test_second_install_lands_while_the_first_holds_its_temp_file(self) -> None:
        real_replace, calls = os.replace, []

        def interleaved(src: str, dst: str) -> None:
            calls.append(src)
            if len(calls) == 1:
                vs.download_trufflehog(self.target)  # the other hook installs in between
            real_replace(src, dst)

        with mock.patch.object(os, "replace", interleaved):
            vs.download_trufflehog(self.target)
        self.assertEqual(len(set(calls)), 2)
        self.assertEqual(self.target.read_bytes(), self.body)
        self.assertTrue(os.access(self.target, os.X_OK))
        self.assertEqual(self.leftovers(), [])

    def test_download_stops_at_the_deadline(self) -> None:
        with mock.patch.object(vs, "_deadline", time.monotonic() - 1), self.assertRaises(RuntimeError):
            vs.download_trufflehog(self.target)
        self.assertFalse(self.target.exists())

    def test_failed_rename_is_fine_when_the_binary_is_already_there(self) -> None:
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(self.body)
        with mock.patch.object(os, "replace", side_effect=OSError("busy")):
            vs.download_trufflehog(self.target)
        self.assertEqual(self.leftovers(), [])

    def test_failed_rename_without_a_binary_raises(self) -> None:
        with mock.patch.object(os, "replace", side_effect=OSError("busy")), self.assertRaises(OSError):
            vs.download_trufflehog(self.target)
        self.assertEqual(self.leftovers(), [])

    def test_trufflehog_bin_uses_a_binary_a_concurrent_install_left(self) -> None:
        pinned = Path(self.tmp.name) / f"trufflehog-{vs.TRUFFLEHOG_VERSION}" / "trufflehog"

        def lost_the_race(target: Path) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.body)
            raise FileNotFoundError("the other install renamed it")

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": self.tmp.name}), \
                mock.patch.object(vs.shutil, "which", return_value=None), \
                mock.patch.object(vs, "download_trufflehog", lost_the_race):
            self.assertEqual(vs.trufflehog_bin(), str(pinned))

    def test_trufflehog_bin_fails_closed_without_a_binary(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_DATA": self.tmp.name}), \
                mock.patch.object(vs.shutil, "which", return_value=None), \
                mock.patch.object(vs, "download_trufflehog", side_effect=OSError("offline")), \
                self.assertRaises(vs.ScannerUnavailable):
            vs.trufflehog_bin()


if __name__ == "__main__":
    unittest.main()
