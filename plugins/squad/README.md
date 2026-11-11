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

## Install

```bash
claude plugin marketplace add anpato/claude-plugins
claude plugin install squad@apato-plugins
```

Mods are an early-access Claude Code feature, and their API can change between releases.

## Develop

`claude plugin validate plugins/squad` and `claude plugin test plugins/squad`.
