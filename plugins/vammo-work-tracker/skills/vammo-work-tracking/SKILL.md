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

## Tracking scope

A person can limit tracking to their work folders with `~/.vammo/tracked-folders` (or the file in
`$VAMMO_TRACKED_FOLDERS`). One folder per line; a plain line tracks the folder and everything
below it, a line starting with `!` excludes it, `#` starts a comment, and the longest matching line
wins. Paths compare without case; `C:\vammo` and `C:/vammo` are the same.

- Without the file, every folder is tracked.
- With the file, track only when the working directory matches a tracked line. The session-start
  reminder (Claude Code and Codex) already says whether tracking is off for the folder; if there
  was no reminder, read the file yourself.
- Out of scope, do not call any tracking tool unless the person explicitly asks to track this work.
  Then track that task only.
- When the person asks to track a folder or project from now on, add its absolute path as a line
  (create the file if it is missing). When they ask to stop tracking one, add `!` and its path, or
  remove the line that included it. Tell them the line you changed.

## Writing WorkItems and Requests

Before drafting or materially reviewing titles, descriptions, scope, acceptance criteria, triage
questions or progress notes, use **vammo-work-tracker-writing**. It is included in this plugin.
English is recommended; an explicit language request wins, and revisions preserve the existing
language. A requested draft is available even with tracking off and does not authorize saving,
submission or session tracking. Preserve the human description and criteria when adding progress.
All existing host permissions, confirmations, transitions and completion rules remain in force.

## At the start of a task

1. Check the tracking scope. Out of scope, stop here.
2. Read the state file if it exists.
3. Call `get_context`.
4. If the person named a key (for example `VAM-42`), use it. Otherwise, if the state file has a
   work item for the same objective, reuse it.
5. Otherwise generate a `workIntentId` (`wi-` + UUID), **write it to the state file first**, then
   call `ensure_work_item` with the objective, repository full name, branch,
   `creationPolicy: CREATE_IF_NO_MATCH`, `proposedWork.completionPolicy` (next section) and, when
   the person already named the team that owns the work,
   `proposedWork.responsibility: { kind: "team", teamId }`. Send
   `{ kind: "personal", personalOwnerId }` only when they ask for personal work. When you tell the
   person you are creating the item, name the policy, and the reviewer for `REVIEWED_DELIVERABLE`.
6. On `needs_selection`, list the candidates and ask the person. Do not pick one yourself.
   On `created` with `teamMissing: true`, the work item exists but has no team: list `teams`
   (`member: true` marks the person's own) and ask which one owns it. Do not pick one yourself.
   Assign it with `write_operation` `updateWorkItem`: `responsibility: { kind: "team", teamId }` and
   the version from `get_work_item`.
7. Call `start_work_session` with the client values above. Save `sessionId`, `workItemId` and
   `workItemKey` in the state file.
8. Link the code you are working on (see Linking code).

## Completion policy

The completion policy decides what moves the work to DONE. Choose it by what the work delivers,
not by its type or repository. Always send it: without it, the tracker uses a default by type, and
that default is `PRODUCTION` for every FEATURE, BUG and MAINTENANCE, also for CI or documentation
work.

| The work | Policy |
| --- | --- |
| Changes a service or app that is deployed to production | `PRODUCTION` |
| Changes only CI, documentation, scripts or tooling, or is research, analysis, a plan or an operational task | `DELIVERABLE` |
| Must be accepted by another person whom the person names | `REVIEWED_DELIVERABLE` |

- If you cannot tell whether the change reaches production, ask the person. Do not choose
  `DELIVERABLE` to avoid deployment verification.
- Use `REVIEWED_DELIVERABLE` only with a named reviewer. Without a reviewer, the work cannot close.
- Choose at creation. Later, only a person can move work away from `PRODUCTION`, with a reason.

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

## When the work is finished

When every pull request linked to the work item is merged or closed and the objective is met:

1. Call `get_work_item` and read `availableActions` and the acceptance criteria.
2. Propose the next step to the person. Act only after they agree, and only with an action that
   `availableActions` lists:
   - `DELIVERABLE`: `set_work_phase` with `complete_deliverable`, an outcome and the status of each
     criterion (`MET`, `NOT_MET` or `WAIVED`). If a criterion is not covered by evidence, say so
     and do not complete.
   - `REVIEWED_DELIVERABLE`: `set_work_phase` with `request_review`, the reviewer the person names
     and the outcome (`deliverable.outcomeMarkdown`). Never approve your own deliverable.
   - `PRODUCTION`: if the delivery scope is not confirmed, propose it. Deployment evidence then
     moves the work to DONE.
   - A BUG you close also needs its cause (`defectCause`) unless one is already recorded. Ask the
     person; never invent it.

To review all of a person's open work items, use **vammo-work-review**. It is included in this
plugin.

## Before ending

Call `link_code` once more, check [When the work is finished](#when-the-work-is-finished), then
call `finish_work_session` with the outcome and a handoff: what changed, what is pending, and what
the next person should check. Do not move work to DONE for production changes; deployment evidence
does that.

## If the tracker is unavailable

- **Sign-in needed:** ask the person to sign in.
  - Claude Code: `/mcp`, then Authenticate for vammo.
  - Codex: `codex mcp login vammo`.
- **Codex, `vammo` tools missing:** Codex lists no tools for an MCP server that is not logged in,
  so no tool can ask for sign-in. Do not wait for one. First ask the person to start a new
  session; a session can lose the server on its own, for example in a refresh race between homes.
  If the tools are still missing, the MCP login may be missing for this home: ask the person to
  run `codex mcp login vammo` in that same `CODEX_HOME`, then start a new session.
  - Apps such as Orca run Codex with one `CODEX_HOME` per account. With
    `mcp_oauth_credentials_store = "file"`, each home keeps its own login, so each account needs
    its own `codex mcp login vammo`.
  - With `mcp_oauth_credentials_store = "keyring"`, one login serves every home on the machine
    (verified with Codex CLI 0.166.0).
- **Otherwise:** keep working.
  - Tell the person that tracking is pending.
  - Keep at most 100 short pending summaries in `pending.json` next to the state file, for up to
    7 days.
  - Never claim registration that did not happen.
