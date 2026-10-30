# Vammo agent plugins

Plugins that connect Claude Code and Codex to Vammo systems. The repository is public so that everyone at Vammo can install it without GitHub access; it contains no credentials. Every tool call still requires signing in with a Vammo account and runs with that person's own permissions.

## vammo-work-tracker

The plugin installs:
- the remote MCP server of the [Vammo Work Tracker](https://work.vammo.com) (`https://services.vammo.com/ms-work-tracker/mcp`, Vammo sign-in);
- the `vammo-work-tracker-writing` skill for drafting and materially reviewing WorkItems and Requests, including business tasks and unsaved drafts;
- the `vammo-work-tracking` skill, which tracks every coding task: work item, session, progress, and the branch, commits and pull requests behind it;
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
revisions preserve the existing language. Portuguese is accepted immediately and uses an identified
clarity adaptation of ASD-STE100. English uses the official English standard, with rule and
dictionary verification recorded separately from card text. No template or language rule blocks intake.

The writing skill can draft in a folder with tracking off. A draft does not authorize saving,
submission or session tracking. Progress notes add to human descriptions and criteria. The hook
only supplies a reminder; discovery, loading, hook execution and behavior are separate checks.
Native trust or managed policy can prevent the local candidate from loading.

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

Before release, integrate the canonical source, read all three files back from its full immutable
commit through authenticated corporate access, and compare their hashes. Only after that separate
review may the manifest be promoted to `sourceStatus: published` with the full `sourceRevision`.
The sync command cannot self-claim publication and resets provenance to development on a new sync.
Use `--verify-package --require-published` as an additional release check; it validates the declaration
and integrity, not the authenticated readback. CI checks local integrity only.

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

Scope and exceptions:

- It runs in every repository with a remote on `github.com/leopardaelectric`, and in no other.
- There is no off switch and no per-PR skip. The only exception is an unverified secret hit that the person confirms as a false positive. It is recorded with its file, line and reason, listed in the PR summary, and noted on the Work Tracker.
- If claude-security isn't installed, the PR is held until it is.
- This covers what goes through Claude Code. A push or PR made outside it isn't checked here, and the CI gates (TruffleHog in shared-ci) remain the backstop.
