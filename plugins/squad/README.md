# squad

A Claude Code mod: a live panel of the session's subagents, each drawn as a tiny animated
vehicle that shows what it is doing.

| Agent is… | Vehicle |
|---|---|
| Searching or reading (Read, Grep, Glob) | Submarine |
| Editing (Edit, Write) | Bulldozer |
| Running commands (Bash) | Race car |
| On the web or using MCP tools | Rocket |
| Writing its final answer | Typewriter |
| Not started yet | Parked car |

Each vehicle is painted in its model's color (Opus clay, Sonnet blue, Haiku green; grey if it
failed). In Ghostty and kitty the vehicles are real images; every other terminal gets the same
vehicles in block characters.

Each row also shows the title, agent type, model and effort, steps reported so far with the
agent's note, context use, tokens and elapsed time, plus an activity bar that sweeps while the
agent works. Press a title to open that agent's transcript. When the agents don't fit, rows
collapse to title and bar. Finished rows clear themselves an hour after the agent ends.

## Use

| Command | Does |
|---|---|
| `/squad` | open the panel |
| `/squad close` | close it |
| `/squad reset` | clear every row |

The panel also opens by itself when an agent starts, and a one-line band above the prompt shows
how many are running.

## Settings (`/config`)

- `autoOpen` (default on): open the panel when an agent starts.
- `askForSteps` (default off): register the `step` tool and add one line to each subagent's prompt asking it to
  report its steps through it. Off, the tool doesn't exist, so agents spend no calls on it. Rows already show `N calls` from the agent's real tool
  calls, so this only adds a `done/total` plan and a note.
- `stallAfterSec` (default 120): a running subagent with no tool call or model request for this
  long shows `idle 3m` (yellow) in its row and `1 idle` in the band above the prompt.

## Offload runs

Runs under `~/.cache/offload/runs/*/run.json` (checked every 5 s, only if the directory exists)
that this session started (their `session_id` matches) appear as `offload` rows: `prepared` or `running` runs while live, and `ok`, `failed` or `timeout`
runs for an hour after they end. A missing or malformed `run.json` is skipped.

## Codex

Codex can't host a pane, so the same panel runs as its own program in a terminal split beside
Codex. It needs [Bun](https://bun.sh) and reads only what Codex already writes: which subagents a
session spawned, with their names, model and effort (`~/.codex/state_N.sqlite`), and each one's
tool calls, tokens and finish (its `rollout-*.jsonl`, appended live).

```bash
ln -s ~/projects/claude-plugins/plugins/squad/codex/squad-codex ~/.local/bin/squad-codex
squad-codex            # follows the newest Codex session started in this directory
squad-codex --session <thread id>
squad-codex --blocks   # block vehicles even in Ghostty/kitty
```

Rows show the task name Codex gave the agent and its Codex nickname. Vehicles come from the tools
inside Codex's `exec` script: `apply_patch` edits, reading commands (`cat`, `rg`, `sed`…) search,
other commands run, MCP and web tools fly. Codex has no step tool, no transcript to open, and
no band above its prompt, so those parts are Claude Code only. `q` quits.

## Install

```bash
claude plugin marketplace add anpato/claude-plugins
claude plugin install squad@apato-plugins
```

Mods are an early-access Claude Code feature, and their API can change between releases.

## Develop

`claude plugin validate plugins/squad` and `claude plugin test plugins/squad`. The Codex viewer's
tests are `bun test plugins/squad/codex` (named `.spec.ts`: `claude plugin test` loads every
`*.test.ts` as a mod test and rejects `bun:test`).
