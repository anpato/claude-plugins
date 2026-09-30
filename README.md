# apato-plugins

A personal [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugins). Each plugin
lives under [`plugins/`](./plugins) and is registered in
[`.claude-plugin/marketplace.json`](./.claude-plugin/marketplace.json).

## Plugins

| Plugin | What it does |
|--------|--------------|
| [`rubber-ducky`](./plugins/rubber-ducky) | Socratic problem-solving companion — think a problem through before writing code, with sub-skills for root-cause, tradeoffs, decomposition, decision matrices, and ADRs. |
| [`adversary`](./plugins/adversary) | Adversarial reviewer and red-teamer — attack a PR/diff (`adversarial-review`) or pre-mortem a design before building it (`red-team-build`). |

See each plugin's own README for its skills and triggers.

## Quick start

Add this repo as a marketplace (once), then install the plugins you want:

```bash
# Register this directory as a marketplace
claude plugin marketplace add /Users/andrepato/projects/claude-plugins

# Install plugins (marketplace name is "apato-plugins")
claude plugin install rubber-ducky@apato-plugins
claude plugin install adversary@apato-plugins
```

The same actions are available in-session via `/plugin`. Confirm what's registered with
`claude plugin marketplace list` and `claude plugin list`.

## Developing locally

This is a **local-directory marketplace**, so its plugins load **in place** from this source tree —
they are not copied into a cache. That makes the edit/test loop short:

1. Edit a plugin's files (e.g. a `SKILL.md`).
2. Apply the change: run `/reload-plugins` in a session, or start a new session.

Notes for local development:

- **No version bump or reinstall** is needed for edits to existing relative-path plugins — the
  files load from source.
- **A brand-new plugin** still has to be installed once (`claude plugin install <name>@apato-plugins`)
  after its entry is added to `marketplace.json`; the catalog entry itself is available immediately.
- `/reload-plugins --force` reloads even when it would invalidate the prompt cache (costs one
  uncached request). If a reload doesn't pick up a change, restart the session.
- Validate manifests before committing: `claude plugin validate .` (marketplace) or
  `claude plugin validate plugins/<name>` (a single plugin).

## Adding a plugin

1. Create `plugins/<name>/` with `.claude-plugin/plugin.json`, a `README.md`, and
   `skills/<skill>/SKILL.md` files (see existing plugins for the shape).
2. Append an entry to the `plugins` array in `.claude-plugin/marketplace.json`
   (`name`, `source: ./plugins/<name>`, `description`, `category`, `keywords`).
3. `claude plugin validate .`, then install and `/reload-plugins`.
