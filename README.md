# Vammo agent plugins

Plugins that connect Claude Code and Codex to Vammo systems. The repository is public so that everyone at Vammo can install it without GitHub access; it contains no credentials. Every tool call still requires signing in with a Vammo account and runs with that person's own permissions.

## vammo-work-tracker

The plugin installs:
- the remote MCP server of the [Vammo Work Tracker](https://work.vammo.com) (`https://services.vammo.com/ms-work-tracker/mcp`, Vammo sign-in);
- the `vammo-work-tracking` skill, which tracks every coding task: work item, session, progress, and the branch, commits and pull requests behind it;
- in Claude Code, a session-start reminder so tracking happens without being asked.

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

### Moving from a manual setup

If you added the server by hand or copied the skill, remove them so tools are not duplicated:
- `claude mcp remove vammo`
- delete `~/.claude/skills/vammo-work-tracking/`
- in Codex, remove the `[mcp_servers.vammo]` block from `~/.codex/config.toml`.

## Changing a plugin

Bump `version` in the plugin's `.claude-plugin/plugin.json` and in `.claude-plugin/marketplace.json`, so clients pick up the update. Check with:

```bash
claude plugin validate ./plugins/vammo-work-tracker
claude plugin validate .
```
