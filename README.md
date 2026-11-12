# Vammo agent plugins

Plugins that connect Claude Code, Codex and Claude chat to Vammo systems: the Vammo Work Tracker and Vammo Apps. The repository is public so that everyone at Vammo can install it without GitHub access; it contains no credentials. Every tool call still requires signing in with a Vammo account and runs with that person's own permissions.

## vammo-work-tracker

The plugin installs:
- the remote MCP server of the [Vammo Work Tracker](https://work.vammo.com) (`https://services.vammo.com/ms-work-tracker/mcp`, Vammo sign-in);
- the `vammo-work-tracker-writing` skill for drafting and materially reviewing WorkItems and Requests, including business tasks and unsaved drafts;
- the `vammo-work-tracking` skill, which tracks every coding task: work item, session, progress, and the branch, commits and pull requests behind it. It chooses the completion policy by what the work delivers and proposes the completing action when the work is finished;
- the `vammo-work-review` skill, which reviews a person's open work items against their pull requests and applies only the changes the person approves. Its classifier needs Python 3;
- in Claude Code and Codex, a session-start reminder so tracking happens without being asked, only in the folders you choose (see [Tracking only work folders](#tracking-only-work-folders)).

### Claude Code

```bash
claude plugin marketplace add leopardaelectric/vammo-agent-plugins
claude plugin install vammo-work-tracker@vammo
```

Restart Claude Code, run `/mcp`, select `plugin:vammo-work-tracker:vammo` and choose **Authenticate**. You sign in once per computer and stay signed in while you use it.

### Codex

```bash
codex plugin marketplace add leopardaelectric/vammo-agent-plugins
codex plugin add vammo-work-tracker@vammo
codex mcp login vammo
```

In Codex, an MCP server that is not logged in shows no tools and gives no sign-in prompt. If the `vammo` tools are missing from a session, first start a new session. If they are still missing, the MCP login may be missing for that session's `CODEX_HOME`: run `codex mcp login vammo` in that same `CODEX_HOME` and start a new session.

Where the login is kept depends on `mcp_oauth_credentials_store` in the Codex `config.toml`:
- `"file"`: the login is stored in the `CODEX_HOME` where you ran `codex mcp login`. Apps such as Orca run Codex with one `CODEX_HOME` per account, so each account, including one added later, needs its own login: `CODEX_HOME=<that home> codex mcp login vammo`.
- `"keyring"`: the login is stored in the operating system's credential store, keyed by server name and URL, not by `CODEX_HOME`. One login serves every `CODEX_HOME` on the machine, including homes created later (verified with Codex CLI 0.166.0 on macOS; on Linux it needs a Secret Service provider such as GNOME Keyring). If sessions from different homes renew the same expired token at the same moment, one of them can lose the server for that session; a new session works without a new login.

### For the whole organization (Claude Team or Enterprise)

An Owner adds this to **claude.ai → Organization settings → Claude Code → Managed settings**. Every member who uses Claude Code with the organization account then gets the plugin installed and enabled automatically:

```json
{
  "extraKnownMarketplaces": {
    "vammo": {
      "source": { "source": "github", "repo": "leopardaelectric/vammo-agent-plugins" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "vammo-work-tracker@vammo": true }
}
```

Each person still authenticates once with `/mcp`.

### Claude chat on the web and desktop (Claude Team or Enterprise)

Organization marketplaces on claude.ai cannot sync from a public repository, so the plugin is uploaded as a ZIP whose root holds `.claude-plugin/plugin.json` (build it with forward-slash paths; PowerShell's `Compress-Archive` writes backslashes).

1. **Organization settings → Connectors**: the Vammo Work Tracker connector with URL `https://services.vammo.com/ms-work-tracker/mcp`.
2. **Organization settings → Plugins & skills → Add → Upload**: the ZIP, availability **Installed by default**.

In chat, the skill and the connector load; the session-start hook does not run there. Skills need code execution enabled. The claude.ai plugin also syncs into Claude Code as `vammo-work-tracker@synced`; a marketplace-installed copy takes precedence, so it is not loaded twice. After changing the plugin, upload the new ZIP in each organization with **Upload new version**.

### Tracking only work folders

By default every coding task is tracked. To track only company work, create `~/.vammo/tracked-folders` (on Windows `%USERPROFILE%\.vammo\tracked-folders`) with one folder per line:

```text
# Track everything under C:\vammo
C:\vammo
# except this folder
!C:\vammo\personal
```

A folder line covers everything below it, `!` excludes, `#` is a comment and the longest matching line wins. Once the file exists, folders that match no line are not tracked, unless you ask the agent to track that task. You can also ask the agent to track or stop tracking a folder from now on; it edits the file. Set `VAMMO_TRACKED_FOLDERS` to use another file. The scope applies when a Claude Code or Codex session starts. Codex asks you to trust the plugin hook again after each plugin update that changes it.

### Moving from a manual setup

If you added the server by hand or copied the skill, remove them so tools are not duplicated:
- `claude mcp remove vammo`
- delete `~/.claude/skills/vammo-work-tracking/`
- in Codex, remove the `[mcp_servers.vammo]` block from `~/.codex/config.toml`.

## vammo-apps

The plugin installs:
- the remote MCP server of [Vammo Apps](https://admin.apps.vammo.com) (`https://api.apps.vammo.com/mcp`, Vammo sign-in), the internal platform where anyone at Vammo builds apps with company data without programming;
- the `vammo-apps` skill: Claude interviews the person, proposes the design in plain language, writes the code, deploys it to the app's dev environment, shows the result, requests the permissions it needs (sensitive data, external hosts, secrets) and promotes it to production. Secrets are never typed in the chat: Claude hands out a one-time link of the portal.

People only need to say what they want ("quiero una app que…").

### Claude Code

```bash
claude plugin marketplace add leopardaelectric/vammo-agent-plugins
claude plugin install vammo-apps@vammo
```

Restart Claude Code, run `/mcp`, select `plugin:vammo-apps:vammo-apps` and choose **Authenticate**.

### Codex

```bash
codex plugin marketplace add leopardaelectric/vammo-agent-plugins
codex plugin add vammo-apps@vammo
codex mcp login vammo-apps
```

### Claude chat on the web and desktop (Claude Team or Enterprise)

Most Vammo Apps users are not engineers, so this is the main channel:

1. **Organization settings → Connectors**: add the Vammo Apps connector with URL `https://api.apps.vammo.com/mcp`.
2. **Organization settings → Plugins & skills → Add → Upload**: a ZIP of `plugins/vammo-apps` whose root holds `.claude-plugin/plugin.json` (forward-slash paths), availability **Installed by default**.

Each person connects once (Vammo sign-in) and can then ask Claude for an app in any chat. ChatGPT users follow the steps on [admin.apps.vammo.com/conectar](https://admin.apps.vammo.com/conectar).

### For the whole organization in Claude Code

Add `"vammo-apps@vammo": true` to `enabledPlugins` in the managed settings shown for the work tracker above.

### Keeping the skill in sync

The skill's source of truth is `integrations/claude-skill/vammo-apps` in the Vammo Apps repository (it is tested against the platform's real tool list). Copy it here when it changes and bump `version` in `plugins/vammo-apps/.claude-plugin/plugin.json`, `.mcp.json` (`X-Vammo-Plugin`) and `.claude-plugin/marketplace.json`.

## Changing a plugin

The plugin's `.mcp.json` sends `X-Vammo-Plugin: vammo-work-tracker@<version>` so the work tracker can show administrators who connected through the plugin. Claude Code reads `headers` and Codex reads `http_headers`; keep both, with the plugin version.

Bump `version` in the plugin's `.claude-plugin/plugin.json` and in `.claude-plugin/marketplace.json`, so clients pick up the update. Check with:

```bash
claude plugin validate ./plugins/vammo-work-tracker
claude plugin validate .
```

### Writing WorkItems and Requests (candidate 1.3.0)

Ask normally to draft or materially review a Work Tracker title, description, scope, acceptance
criteria, triage question or progress note. For explicit invocation, use
`/vammo-work-tracker:vammo-work-tracker-writing` in Claude Code or
`$vammo-work-tracker-writing` in Codex.

English is recommended for new cards without a language choice. Explicit language requests win;
revisions preserve the existing language. A Portuguese conversation or source notes alone do not
choose the new card's language; an explicit request such as “em português” does. Portuguese is
accepted immediately and uses an identified clarity adaptation of ASD-STE100. English uses the official
English standard, with rule and dictionary verification recorded separately from card text. No template
or language rule blocks intake.
The English guide requires a per-artifact word/form/construction review using permitted access to
the official manual. Its editing examples do not replace that manual, which is not bundled. If
normative access is unavailable, drafting continues with verification pending; no compliance claim follows.

The writing skill can draft in a folder with tracking off. A draft does not authorize saving,
submission or session tracking. Progress notes add to human descriptions and criteria. The hook
only supplies a reminder; discovery, loading, hook execution and behavior are separate checks.
Native trust or managed policy can prevent the local candidate from loading.
Native project, owner, priority, type and status already supplied by the host stay outside the
entire draft delivery, including separate metadata blocks, introductions and recaps. Keep an
actionable dependency on another project. A comparison does not add a purchase decision; an
observation-only progress note does not add instructions from the existing criteria.

The November 4 review update carries the final November 3 local writing corrections. The recorded
six synthetic native outputs received five partial grades and one pass in independent review.
English vocabulary/construction faults and technical-term evidence remain open; package integrity
does not establish editorial acceptance. The source review must resolve or explicitly accept these
limits before publication. Release provenance requires approved canonical integration and
authenticated immutable readback, recorded in `writing-source.json`. A `published` source status
describes the canonical source, not native-client or editorial acceptance. No private test output
or official manual is bundled.

After an authorized release, update and restart:

```sh
claude plugin marketplace update vammo
claude plugin update vammo-work-tracker@vammo
codex plugin marketplace upgrade vammo
```

Check the installed version, loaded skill and a real draft separately. These commands do not prove
organization-wide adoption. Hook changes can require native trust again.

### Maintaining the generated writing skill

The three writing files are canonical in the Tracker repository at
`agent-integrations/shared/skills/vammo-work-tracker-writing/`. Edit that source first. In this repo:

```sh
node plugins/vammo-work-tracker/scripts/sync-writing-skill.mjs --source <tracker-checkout>
node plugins/vammo-work-tracker/scripts/sync-writing-skill.mjs --source <tracker-checkout> --check
node plugins/vammo-work-tracker/scripts/sync-writing-skill.mjs --verify-package
node --test plugins/vammo-work-tracker/scripts/*.test.mjs
```

Sync copies only `SKILL.md` and its English/Portuguese references, without network access. It emits
`WORKTREE` / `development` provenance in `writing-source.json`. `--check` compares supplied source
bytes and package hashes; `--verify-package` checks local integrity, not authenticated upstream
origin. Neither command changes the source repository or other plugin files. Unknown files are
preserved; package verification refuses unmanifested files inside the writing skill.

The workflow above is a draft check: it verifies package integrity but accepts development provenance.
There is no automated final-release workflow. After the canonical source is approved and integrated,
use this release sequence:

1. In an authenticated corporate Tracker checkout, fetch the full 40-character source commit. Read
   each of the three canonical files from that commit and compare its SHA-256 with the candidate bytes.
   For example:

   ```sh
   git -C <tracker-checkout> show <source-commit>:agent-integrations/shared/skills/vammo-work-tracker-writing/SKILL.md | shasum -a 256
   ```

   Repeat for `references/english-ste.md` and `references/portuguese-writing.md`.
2. After all three hashes match, set `sourceRevision` in `writing-source.json` to that full commit and
   `sourceStatus` to `published`. A new sync resets both fields to development provenance.
3. Run the source and package checks, then the final release gate:

   ```sh
   node plugins/vammo-work-tracker/scripts/sync-writing-skill.mjs --source <tracker-checkout> --check
   node plugins/vammo-work-tracker/scripts/sync-writing-skill.mjs --verify-package --require-published
   ```

   The final command is the documented release-readiness entry point. It checks the declaration and
   package integrity; it does not prove authenticated readback.
4. Complete the independent source and package review. Publish only through the separately authorized
   release process.
5. After publication, install the approved package, restart each client, and check its installed
   version, loaded skill, and a real draft separately. Keep the prior full package/ref until rollback
   has been checked in a temporary profile.

CI checks local integrity only. The sync command cannot self-claim publication.

For rollback, retain the previous full plugin package/ref before updating. Restore it in a temporary
profile first, then use the authorized client update mechanism and a new session. Rollback changes
plugin instructions, never cards. General managed rollout and managed rollback need separate approval.

## vammo-claude-skills

Shared Claude Code skills, commands and agents for the engineering team: `local-dev-cycle` (a feature from ticket to PR through specialized agents), `dev-qa-live-tests` (live QA of a branch on the docker stack or on dev) and `deployment-agent` (`/track-deploy`). Source and docs: [leopardaelectric/vammo-claude-skills](https://github.com/leopardaelectric/vammo-claude-skills) (private: the install needs read access to the `leopardaelectric` organization on GitHub).

```bash
claude plugin marketplace add leopardaelectric/vammo-agent-plugins
claude plugin install vammo-claude-skills@vammo
```

Restart Claude Code. The skills appear as `/vammo-claude-skills:<name>`; the agents as `vammo-claude-skills:<name>`. Claude Code only.

## vammo-security

Security gates for work done in Claude Code. Two of them cost no model tokens: TruffleHog runs on the commits being published, before every `git push` and again before every PR. It uses the same version and custom detectors as the CI secret gate.

- A **live** (verified) secret always holds the push or PR. Claude removes it from the branch's history, and tells the person to rotate it if it was already pushed.
- An **unverified** hit holds until the person confirms whether it's real. A confirmed false positive isn't raised again.
- TruffleHog isn't vendored. A `trufflehog` on PATH is used, or the pinned release is downloaded once and its checksum verified. If it can't run, the push or PR is held until it can (it fails closed).

Then, before every pull request: when Claude is about to open a PR (`gh pr create`, or a GitHub MCP `create_pull_request` tool) and the branch's HEAD hasn't been scanned yet, the plugin holds the PR. Claude then runs the `pre-pr-security` skill without asking:

1. It scans the branch's changes with Anthropic's [claude-security](https://claude.com/product/claude-security) plugin ("scan changes", medium effort).
2. It fixes the findings with claude-security's own patches. Before a patch is written, independent agents check that it closes the finding, introduces no new vulnerability and leaves behaviour unchanged. A finding it won't patch is the one point where Claude stops and asks the person.
3. It commits and pushes the fixes as one commit.
4. It records the result as a check on the Vammo Work Tracker work item, when one is active.
5. It opens the PR with a short "Security pre-check" section in the body.

Coverage is kept per worktree in the git dir, so:

- the same HEAD is never scanned twice;
- a later push scans only the new commits.

The scan runs on the person's own Claude Code plan, so there is no per-PR CI cost. Measured on 2026-10-23 at medium effort:

- a clean branch takes about 3–4 minutes;
- a branch with findings takes longer. A 28-line branch with 3 planted vulnerabilities took 17 minutes end to end (about $8.8 at list price): about 4 minutes of scan, and the rest building and verifying the 3 patches. This is a guide, not a gate: a PR opened outside Claude Code isn't checked.

Claude Code only. It needs Python 3 (claude-security needs it too).

```bash
claude plugin marketplace add leopardaelectric/vammo-agent-plugins
claude plugin install vammo-security@vammo
```

claude-security is a declared dependency from `claude-plugins-official`, so it is installed with the plugin. The marketplace allows that cross-marketplace dependency. Restart Claude Code afterwards. For the whole organization, add `"vammo-security@vammo": true` to `enabledPlugins` in the managed settings shown above.

The gates fail closed. The hook reads a command's whole text, so a command that only mentions a push or a PR creation (quoted, in a heredoc body, in a commit message) triggers the gate too, as before. Opening a PR includes `gh pr create` (and its alias `gh pr new`), any `gh api` call to `.../pulls`, and a GraphQL `createPullRequest`. Only four exact forms of a whole command, with plain literal words, are judged on the checkout they target alone, not the one the session was opened in: `cd <dir> && gh pr create …` (without `-R`), `cd <dir> && git push …`, `git -C <dir> push …`, and `heavy-lock.sh run <label> -- git -C <dir> push …`. Anything else (a `;`, a pipe, a redirection, a variable, a subshell, `bash -c`, inline interpreter code, `-R`, `gh api`) is judged on the session's checkout and on every checkout the command names after `cd`, `-C`, `--git-dir` or `GIT_DIR`, so the worst case is an extra check, never a skipped one. An error inside the hook, or more checkouts than fit in its time limit, holds the call. A PR for another Vammo repository that none of those checkouts is (`-R`, the `gh api` endpoint's owner and name, or a GitHub MCP tool's owner and repo) is held, with instructions to open it from that repository's checkout so its own HEAD is checked.

Scope and exceptions:

- It runs in every repository with a remote on `github.com/leopardaelectric`, and in no other.
- There is no off switch and no per-PR skip. The only exception is an unverified secret hit that the person confirms as a false positive. It is recorded with its file, line and reason, listed in the PR summary, and noted on the Work Tracker.
- If claude-security isn't installed, the PR is held until it is.
- This covers what goes through Claude Code. A push or PR made outside it isn't checked here, and the CI gates (TruffleHog in shared-ci) remain the backstop.
