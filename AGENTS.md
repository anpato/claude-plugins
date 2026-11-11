# claude-plugins

Andre's personal Claude Code plugin marketplace (`apato-plugins`, GitHub `anpato/claude-plugins`). Plugins: `adversary`, `offload`, `recall`, `rubber-ducky`, `squad`.

## Layout
- `.claude-plugin/marketplace.json`: the catalog; every plugin needs an entry here (`source: ./plugins/<name>`).
- `plugins/<name>/.claude-plugin/plugin.json`: per-plugin manifest and `version`.
- `plugins/<name>/skills/<skill>/SKILL.md`: skills (adversary, offload, recall, rubber-ducky).
- `plugins/{recall,squad}/hooks/`: TypeScript "mod" panes (`register.tsx`, `register.test.ts`, `hooks.json`); types come from `.claude-plugin/types` or `types/`.
- `plugins/squad/codex/`: the squad panel as a standalone Bun program beside Codex (`main.ts`); it reuses `hooks/sprite.ts`, `hires.ts`, `format.ts`.
- `plugins/offload/scripts/offload.py`, `plugins/recall/scripts/recall.py`: Python helpers; tests in each plugin's `tests/`.

## Commands
- `claude plugin validate .` (marketplace) or `claude plugin validate plugins/<name>` (README.md "Developing locally").
- `python3 -m pytest plugins/offload/tests -q` (plugins/offload/tests/test_offload.py:3).
- `python3 -I -m unittest discover -s plugins/recall/tests` (plugins/recall/README.md:100).
- `claude plugin test plugins/recall`, `claude plugin test plugins/squad` (README of each).
- `bun test plugins/squad/codex` (Codex viewer; its tests are `.spec.ts` because `claude plugin test` rejects `bun:test` in `*.test.ts`).

## Conventions
- Shipping a change: bump `version` in the plugin's `plugin.json` (history: `e0c2175` "bump to 0.4.1 so installs pick up the lsof fallback"; `0a187c5` rubber-ducky 1.3.0), open a PR to `main` (history is PR merges, e.g. #21), then update installs (see Done means).
- Installs track `main` on GitHub. Without a version bump installed copies do not pick up the change.
- Local dev: register the clone as a directory marketplace; plugins load in place, so edits need only `/reload-plugins` (README.md "Developing locally"). A new plugin must be installed once.
- New plugin: add `plugin.json`, README, skills, and a `marketplace.json` entry.
- Python bytecode is ignored (`.gitignore`); don't commit `__pycache__`.

## Gotchas
- Plugin and marketplace names differ from the repo name: marketplace is `apato-plugins`, repo is `claude-plugins`.
- rubber-ducky skills are `disable-model-invocation: true` (slash-command only); keep that when editing them (`0a187c5`).
- Offload workers are not sandboxed since 0.6.0 (`c03ca5f`); review their output as untrusted.

## Done means
- `claude plugin validate plugins/<name>` passes, plus that plugin's tests (Commands).
- `version` bumped in its `plugin.json` if installs should get the change.
- Shipped: PR merged to `main`, then on each machine `command claude plugin marketplace update apato-plugins`
  and `command claude plugin update <name>@apato-plugins` (`command` skips Andre's `claude` alias, whose
  `--channels` flag breaks `plugin` subcommands).
