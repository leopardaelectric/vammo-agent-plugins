"""Unit tests for vammo_security.py: which checkout a call targets, the TruffleHog install, state saves.

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
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vammo_security.py"
spec = importlib.util.spec_from_file_location("vammo_security", SCRIPT)
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

PR = "gh " + "pr " + "create"


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

    def git_repo(self, path: str, remote: str) -> None:
        subprocess.run(["git", "init", "-q", path], check=True)
        subprocess.run(["git", "-C", path, "remote", "add", "origin", remote], check=True)


class Classify(TempDirs):
    def kind(self, command: str) -> str | None:
        return vs.classify("Bash", bash(command))

    def test_direct_commands(self) -> None:
        self.assertEqual(self.kind(f"{PR} --fill"), "pr")
        self.assertEqual(self.kind("git push origin br"), "push")
        self.assertEqual(self.kind("git -C x push"), "push")
        self.assertEqual(self.kind("git -c core.x=1 --no-pager push"), "push")

    def test_wrapped_commands(self) -> None:
        self.assertEqual(self.kind("heavy-lock.sh run 'label' -- git -C x push origin br"), "push")
        self.assertEqual(self.kind("FOO=1 timeout 60 git push"), "push")
        self.assertEqual(self.kind(f"echo \"$({PR} --fill)\""), "pr")
        self.assertEqual(self.kind(f"bash -c 'cd x && {PR} --fill'"), "pr")
        self.assertEqual(self.kind(f"bash <<'EOF'\n{PR} --fill\nEOF"), "pr")

    def test_pr_body_from_heredoc_is_still_a_pr(self) -> None:
        self.assertEqual(self.kind(f"{PR} --title t --body \"$(cat <<'EOF'\n## Summary\nx\nEOF\n)\""), "pr")

    def test_text_that_only_mentions_the_commands(self) -> None:
        self.assertIsNone(self.kind(f"python3 - <<'EOF'\nprint(\"{PR}\")\n{PR} --fill\nEOF"))
        self.assertIsNone(self.kind(f"cat <<-EOF > notes.txt\n\trun {PR} then git push\n\tEOF"))
        self.assertIsNone(self.kind(f"git commit -m \"$(cat <<'EOF'\nthen {PR}\nEOF\n)\""))
        self.assertIsNone(self.kind(f"grep -rn '{PR}' . && git log --grep 'git push'"))
        self.assertIsNone(self.kind("git status && gh pr view 12"))

    def test_unparseable_falls_back_to_the_text_match(self) -> None:
        self.assertEqual(self.kind(f"{PR} --title \"unbalanced"), "pr")
        self.assertEqual(self.kind("cat <<EOF\nnever ends && git push"), "push")
        with self.assertRaises(ValueError):
            vs.gated_calls(f"{PR} --title \"unbalanced", self.session)

    def test_mcp_tool(self) -> None:
        self.assertEqual(vs.classify("mcp__github__create_pull_request", {}), "pr")
        self.assertIsNone(vs.classify("mcp__github__list_pull_requests", {}))

    def test_interpreter_inline_code_runs_the_commands(self) -> None:
        self.assertEqual(self.kind(f"python3 -c \"import os; os.system('{PR} --fill')\""), "pr")
        self.assertEqual(self.kind(f"python -c 'import os; os.system(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"/usr/bin/python3.12 -Ic 'import os; os.system(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"timeout 9 python3 -c'import os; os.system(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind("python3 -c 'import subprocess; subprocess.run([\"git\", \"push\"])'"), "push")
        self.assertEqual(self.kind("python3 -c 'import subprocess; subprocess.run([\"git\", \"-C\", \"x\", \"push\"])'"),
                         "push")
        self.assertEqual(self.kind("node -e \"require('child_process').execSync('git push')\""), "push")
        self.assertEqual(self.kind(f"node --eval 'x(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"node --eval='x(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"node -p 'x(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"node -pe 'x(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"perl -e 'system(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"perl -E 'system(\"{PR}\")'"), "pr")
        self.assertEqual(self.kind("perl -lne 'system(\"git push\")' file"), "push")
        self.assertEqual(self.kind(f"ruby -e 'system(\"/usr/local/bin/{PR}\")'"), "pr")
        self.assertEqual(self.kind(f"php -r 'exec(\"{PR}\");'"), "pr")

    def test_interpreter_words_that_are_not_inline_code(self) -> None:
        self.assertIsNone(self.kind(f"python3 -c 'print(1)' --label ' {PR} '"))
        self.assertIsNone(self.kind(f"python3 script.py '{PR}'"))
        self.assertIsNone(self.kind(f"python3 -m pytest -k '{PR}'"))
        self.assertIsNone(self.kind(f"echo \"python3 -c 'import os; os.system({PR})'\""))
        self.assertIsNone(self.kind("node -e 'x(\"gh pr view; git pushd\")'"))
        self.assertIsNone(self.kind("python3 -c 'print(\"github pr create\")'"))

    def test_gh_api_post_to_pulls_is_a_pr(self) -> None:
        for command in (
            "gh api repos/o/n/pulls -X POST --input body.json",
            "gh api /repos/o/n/pulls --method POST -f title=t",
            "gh api repos/o/n/pulls -XPOST",
            "gh api repos/o/n/pulls --method=post",
            "gh api https://api.github.com/repos/o/n/pulls -f title=t -f head=x -f base=main",
            "gh api repos/o/n/pulls -F draft=true",
            "gh api repos/o/n/pulls --raw-field title=t",
            "gh api repos/o/n/pulls --field=title=t",
            "gh api repos/o/n/pulls --input=- < body.json",
            "cd x && gh api repos/o/n/pulls/ --input body.json",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.kind(command), "pr")

    def test_gh_api_calls_that_dont_create_a_pr(self) -> None:
        for command in (
            "gh api repos/o/n/pulls",
            "gh api repos/o/n/pulls -X GET -f state=open",
            "gh api repos/o/n/pulls --method PATCH --input body.json",
            "gh api repos/o/n/pulls/12 -X PATCH -f state=closed",
            "gh api repos/o/n/pulls/12/comments -X POST -f body=x",
            "gh api repos/o/n/issues -X POST -f title=t",
            "echo 'gh api repos/o/n/pulls -X POST'",
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.kind(command))

    def test_unparseable_gh_api_to_pulls_falls_back_to_a_pr(self) -> None:
        self.assertEqual(self.kind("gh api repos/o/n/pulls -X POST -f title=\"unbalanced"), "pr")


class Targets(TempDirs):
    def targets(self, kind: str, command: str) -> list:
        return vs.gate_targets(kind, "Bash", bash(command), self.session)

    def test_cd_then_pr(self) -> None:
        self.assertEqual(self.targets("pr", f"cd {self.other} && {PR} --fill"), [(self.other, None)])
        self.assertEqual(self.targets("pr", f"cd ../other; {PR} --fill"), [(self.other, None)])

    def test_git_dash_c_push_is_relative_to_the_current_dir(self) -> None:
        self.assertEqual(self.targets("push", f"git -C {self.other} push"), [(self.other, None)])
        self.assertEqual(self.targets("push", "git -C ../other push origin br"), [(self.other, None)])
        self.assertEqual(self.targets("push", f"cd {self.root} && git -C other push"), [(self.other, None)])

    def test_wrapped_push(self) -> None:
        command = f"heavy-lock.sh run \"x\" -- git -C {self.other} push origin br"
        self.assertEqual(self.targets("push", command), [(self.other, None)])

    def test_subshell_cd_does_not_leak(self) -> None:
        self.assertEqual(self.targets("push", f"(cd {self.other} && git status); git push"), [(self.session, None)])

    def test_unresolvable_dir_keeps_the_current_one(self) -> None:
        self.assertEqual(self.targets("pr", f"cd {self.root}/missing && {PR}"), [(self.session, None)])
        self.assertEqual(self.targets("push", "git -C \"$NOT_SET_ANYWHERE\" push"), [(self.session, None)])

    def test_unparseable_judges_the_session(self) -> None:
        self.assertEqual(self.targets("pr", f"cd {self.other} && {PR} --title \"x"), [(self.session, None)])

    def test_repo_flag(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        self.assertEqual(self.targets("pr", f"{PR} -R leopardaelectric/other-repo"),
                         [(None, "leopardaelectric/other-repo")])
        self.assertEqual(self.targets("pr", f"{PR} --repo=https://github.com/LeopardaElectric/other-repo.git"),
                         [(None, "leopardaelectric/other-repo")])
        self.assertEqual(self.targets("pr", f"{PR} --repo leopardaelectric/session-repo"), [(self.session, None)])
        self.assertEqual(self.targets("pr", f"{PR} -R someone/else"), [(None, None)])

    def test_repo_flag_after_cd_compares_with_that_checkout(self) -> None:
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo")
        command = f"cd {self.other} && {PR} -R leopardaelectric/other-repo"
        self.assertEqual(self.targets("pr", command), [(self.other, None)])

    def test_mcp_owner_and_repo(self) -> None:
        self.git_repo(self.session, "https://github.com/leopardaelectric/session-repo.git")

        def target(tool_input: dict) -> list:
            return vs.gate_targets("pr", "mcp__github__create_pull_request", tool_input, self.session)

        self.assertEqual(target({"owner": "leopardaelectric", "repo": "other-repo"}),
                         [(None, "leopardaelectric/other-repo")])
        self.assertEqual(target({"owner": "leopardaelectric", "repo": "session-repo"}), [(self.session, None)])
        self.assertEqual(target({"owner": "someone", "repo": "else"}), [(None, None)])
        self.assertEqual(target({"title": "no owner"}), [(self.session, None)])

    def test_interpreter_code_is_judged_where_the_interpreter_runs(self) -> None:
        code = f"python3 -c \"import os; os.system('cd /elsewhere && {PR} -R leopardaelectric/x')\""
        self.assertEqual(self.targets("pr", code), [(self.session, None)])
        self.assertEqual(self.targets("pr", f"cd {self.other} && {code}"), [(self.other, None)])
        self.assertEqual(self.targets("push", f"(cd {self.other}; node -e 'x(\"git push\")')"), [(self.other, None)])

    def test_gh_api_repo_goes_through_the_repo_check(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        self.git_repo(self.other, "https://github.com/leopardaelectric/other-repo.git")
        post = "/pulls -X POST --input body.json"
        self.assertEqual(self.targets("pr", f"gh api repos/leopardaelectric/session-repo{post}"),
                         [(self.session, None)])
        self.assertEqual(self.targets("pr", f"gh api /repos/LeopardaElectric/other-repo{post}"),
                         [(None, "leopardaelectric/other-repo")])
        self.assertEqual(self.targets("pr", f"cd {self.other} && gh api repos/leopardaelectric/other-repo{post}"),
                         [(self.other, None)])
        self.assertEqual(self.targets("pr", f"gh api repos/someone/else{post}"), [(None, None)])
        self.assertEqual(self.targets("pr", f"gh api repos/{{owner}}/{{repo}}{post}"), [(self.session, None)])


class Hook(TempDirs):
    def run_hook(self, command: str) -> dict:
        event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.session}
        out = io.StringIO()
        with mock.patch.object(vs.sys, "stdin", io.StringIO(json.dumps(event))), \
                mock.patch.object(vs.sys, "stdout", out):
            vs.cmd_hook()
        return json.loads(out.getvalue())["hookSpecificOutput"]

    def test_unresolved_target_message_is_not_specific_to_one_command(self) -> None:
        self.git_repo(self.session, "git@github.com:leopardaelectric/session-repo.git")
        decision = self.run_hook("gh api repos/leopardaelectric/other-repo/pulls -X POST --input body.json")
        self.assertEqual(decision["permissionDecision"], "deny")
        reason = decision["permissionDecisionReason"]
        self.assertIn("targets leopardaelectric/other-repo, which is not the repository this session is in", reason)
        self.assertIn("Open it from a checkout of leopardaelectric/other-repo (`cd <its worktree> && ...`)", reason)
        self.assertNotIn(PR, reason)


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
