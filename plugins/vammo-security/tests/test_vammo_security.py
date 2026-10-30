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
                        "git \\\n  push", "git -C $(pwd) push"):
            self.assertKinds(command, {"push"})

    def test_pr_creation_forms(self) -> None:
        for command in (f"{PR} --fill", PR_REPO_FIRST, "gh -R leopardaelectric/x pr create",
                        f"os.system('{PR}')", "gh api repos/o/n/pulls", "gh api repos/o/n/pulls -X GET",
                        "gh api repos/o/n/pulls/12/comments", "EP=repos/o/n/pulls; gh api \"$EP\"",
                        "gh api graphql -f query='mutation{createPullRequest(input:{})}'"):
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


class FollowedPlans(TempDirs):
    """Plain command lines: the targets are followed exactly."""

    def assertFollowed(self, command: str, plan: list) -> None:
        self.assertEqual(self.plan(command), (plan, True), command)

    def test_cd_and_dash_c(self) -> None:
        self.assertFollowed(f"cd {self.other} && {PR} --fill", [("pr", self.other, None)])
        self.assertFollowed(f"cd ../other; {PR} --fill", [("pr", self.other, None)])
        self.assertFollowed(f"git -C {self.other} push", [("push", self.other, None)])
        self.assertFollowed("git -C ../other push origin br", [("push", self.other, None)])
        self.assertFollowed(f"cd {self.root} && git -C other push", [("push", self.other, None)])
        self.assertFollowed(f"heavy-lock.sh run \"x\" -- git -C {self.other} push origin br",
                            [("push", self.other, None)])
        self.assertFollowed(f"{PR} --title 'fix #12' 2>&1 >/dev/null", [("pr", self.session, None)])

    def test_review_mixed_push_and_pr_gates_both(self) -> None:
        # Review: a push in the same command as a PR was dropped.
        self.assertFollowed(f"git -C {self.other} push && {PR}", [("push", self.other, None), ("pr", self.session, None)])
        self.assertFollowed(f"git push && {PR} -R someone/else", [("push", self.session, None), ("pr", None, None)])
        self.assertFollowed(f"git push origin HEAD; {PR} -R someone/else --fill",
                            [("push", self.session, None), ("pr", None, None)])

    def test_review_quoted_operators_are_text(self) -> None:
        self.assertFollowed("echo '>' && git push origin HEAD", [("push", self.session, None)])
        self.assertFollowed(f"echo '>' && {PR} --fill", [("pr", self.session, None)])

    def test_review_flag_values_are_not_repo_flags(self) -> None:
        self.assertFollowed(f"{PR} --title \"-Rsomeone/else\" --fill", [("pr", self.session, None)])

    def test_review_repo_flag_between_pr_and_create(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        self.assertFollowed(PR_REPO_FIRST, [("pr", None, "leopardaelectric/x")])
        self.assertFollowed("gh -R leopardaelectric/x pr create --fill", [("pr", None, "leopardaelectric/x")])

    def test_review_api_formatting_args_dont_hide_the_endpoint(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        command = "gh api repos/leopardaelectric/target/pulls -X POST --template \"--method=GET\" --input p.json"
        self.assertFollowed(command, [("pr", None, "leopardaelectric/target")])
        self.assertFollowed("gh api repos/leopardaelectric/session-repo/pulls -X GET", [("pr", self.session, None)])

    def test_repo_flag(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo")
        self.assertFollowed(f"{PR} -R leopardaelectric/other-repo", [("pr", None, "leopardaelectric/other-repo")])
        self.assertFollowed(f"{PR} --repo=https://github.com/LeopardaElectric/other-repo.git",
                            [("pr", None, "leopardaelectric/other-repo")])
        self.assertFollowed(f"{PR} --repo leopardaelectric/session-repo", [("pr", self.session, None)])
        self.assertFollowed(f"{PR} -R someone/else", [("pr", None, None)])
        self.assertFollowed(f"cd {self.other} && {PR} -R leopardaelectric/other-repo", [("pr", self.other, None)])

    def test_mcp_owner_and_repo(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")

        def plan(tool_input: dict) -> tuple[list, bool]:
            return vs.gate_plan({"pr"}, "mcp__github__create_pull_request", tool_input, self.session)

        self.assertEqual(plan({"owner": "leopardaelectric", "repo": "other-repo"}),
                         ([("pr", None, "leopardaelectric/other-repo")], True))
        self.assertEqual(plan({"owner": "leopardaelectric", "repo": "session-repo"}), ([("pr", self.session, None)], True))
        self.assertEqual(plan({"owner": "someone", "repo": "else"}), ([("pr", None, None)], True))
        self.assertEqual(plan({"title": "no owner"}), ([("pr", self.session, None)], True))


class UnionPlans(TempDirs):
    """Anything else: the session's checkout and every checkout named, never fewer."""

    def assertUnion(self, command: str, kind: str, named: list[str] | None = None) -> list:
        plan, followed = self.plan(command)
        self.assertFalse(followed, command)
        judged = [d for k, d, held in plan if k == kind and not held]
        self.assertEqual(judged, [self.session] + (named or []), command)
        return plan

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

    def test_any_exception_in_the_parser_falls_back_to_the_union(self) -> None:
        with mock.patch.object(vs, "simple_calls", side_effect=KeyError("boom")):
            self.assertUnion(f"cd {self.other} && git push", "push", [self.other])


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

        def record(kind: str, top: str, session: str, tool_input: dict, followed: bool) -> bool:
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
        self.assertEqual(self.gated(f"git -C {self.other} push && {PR}"),
                         [("push", self.other, True), ("pr", self.session, True)])
        self.assertEqual(self.gated(f"bash -c 'cd {self.other} && git push && {PR}'"),
                         [("push", self.session, False), ("push", self.other, False),
                          ("pr", self.session, False), ("pr", self.other, False)])

    def test_first_deny_wins(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo.git")
        self.assertEqual(self.gated(f"git -C {self.other} push && {PR}", deny_first=True), [("push", self.other, True)])

    def test_one_gate_per_checkout(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        os.makedirs(os.path.join(self.session, "sub"))
        self.assertEqual(self.gated(f"bash -c 'cd {self.session}/sub && git push'"), [("push", self.session, False)])

    def test_an_exception_in_the_target_logic_gates_the_session(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")
        with mock.patch.object(vs, "gate_plan", side_effect=RuntimeError("boom")):
            self.assertEqual(self.gated(f"cd {self.other} && git push && {PR}"),
                             [("push", self.session, False), ("pr", self.session, False)])

    def test_a_command_with_nothing_gated_exits_before_any_git_call(self) -> None:
        with mock.patch.object(vs, "git", side_effect=AssertionError("git called")):
            self.assertIsNone(self.run_hook("git status && ls -la && gh pr view 12"))

    def test_scan_request_explains_the_union(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git", commit=True)
        with mock.patch.object(vs, "secrets_gate", return_value=False), \
                mock.patch.object(vs, "claude_security_installed", return_value=True):
            union = self.run_hook(f"bash -c '{PR} --fill'")["permissionDecisionReason"]
            followed = self.run_hook(f"{PR} --fill")["permissionDecisionReason"]
        self.assertIn("has not been scanned", union)
        self.assertIn("This command's shape isn't one the gate follows", union)
        self.assertNotIn("checkout at", union)
        self.assertNotIn("This command's shape", followed)


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
