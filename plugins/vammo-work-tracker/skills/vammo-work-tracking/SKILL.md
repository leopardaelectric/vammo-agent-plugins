---
name: vammo-work-tracking
description: Track coherent units of engineering work in the Vammo Work Tracker through its MCP server, and link the branch, commits and pull requests behind each work item. Use at the start of a coding task, after creating or updating a work item, after each push or PR, when the scope changes, when blocked, after verification, and before ending the session.
---

# Vammo work tracking

The canonical behavior is `agent-integrations/shared/WORK_TRACKING.md` in the
`leopardaelectric/vammo-work-tracker` repository; this skill is complete on its own. The `vammo`
MCP server enforces permissions. Tracking never bypasses the host's permission, approval or sandbox
prompts.

**Client values:**

- Claude Code: `client: CLAUDE_CODE`, `clientSessionRef: claude-code:<session start ISO time>`.
- Codex: `client: CODEX`, `clientSessionRef: codex:<session start ISO time>`.

**State file:** the path printed by `git rev-parse --git-path vammo-tracker/session.json`. It is
inside `.git`, separate per worktree, and never committed.

## At the start of a task

1. Read the state file if it exists.
2. Call `get_context`.
3. If the person named a key (for example `VAM-42`), use it. Otherwise, if the state file has a
   work item for the same objective, reuse it.
4. Otherwise generate a `workIntentId` (`wi-` + UUID), **write it to the state file first**, then
   call `ensure_work_item` with the objective, repository full name, branch and
   `creationPolicy: CREATE_IF_NO_MATCH`.
5. On `needs_selection`, list the candidates and ask the person. Do not pick one yourself.
6. Call `start_work_session` with the client values above. Save `sessionId`, `workItemId` and
   `workItemKey` in the state file.
7. Link the code you are working on (next section).

## Linking code

Keep the work item's Code tab complete without asking; linking only associates code. Call
`link_code`:

- after `start_work_session`,
- after creating or updating a work item,
- after every `git push`,
- after creating or updating a pull request,
- before `finish_work_session`.

How to call it:

1. `git remote get-url origin` gives `repositoryFullName` as `owner/repo` (drop `git@<host>:`,
   `https://github.com/` and `.git`).
2. `git rev-parse --abbrev-ref HEAD` gives `branch`. On a feature branch this alone links its PRs
   and the commits ahead of the default branch.
3. On the default branch, also pass `shas`: your pushed commits for this work
   (`git log @{u} --format=%H -n 20`).
4. Call `link_code` with `workItemId` (the key works), a new `requestId` and those fields.
5. Read `skipped`:
   - `branch_not_found` or `not_found`: push and call again.
   - `not_connected`: tell the person the repository needs the GitHub App.

   A skip never fails the task.

Put the work key in every commit message and PR title, and in new branch names
(`feat/VAM-42-short-name`).

## During the task

Call `record_progress` only at milestones: meaningful scope change, blocker, verification,
PR creation or update, handoff.

- List only checks you actually ran (`claimBasis: AGENT_REPORTED`), for example `pnpm test` with
  its result.
- Link commits and PRs with `link_code`, not `attach_evidence`. `attach_evidence` is for documents,
  test reports and other links.

## Before ending

Call `link_code` once more, then `finish_work_session` with the outcome and a handoff: what
changed, what is pending, and what the next person should check. Do not move work to DONE for
production changes; deployment evidence does that.

## If the tracker is unavailable

- **Sign-in needed:** ask the person to sign in.
  - Claude Code: `/mcp`, then Authenticate for vammo.
  - Codex: `codex mcp login vammo`.
- **Otherwise:** keep working.
  - Tell the person that tracking is pending.
  - Keep at most 100 short pending summaries in `pending.json` next to the state file, for up to
    7 days.
  - Never claim registration that did not happen.
