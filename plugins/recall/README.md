# Recall

Pull a past Claude Code session from **any directory** into the conversation you're in now.
`--continue` and `/resume` only work for the current directory. Recall searches every project and
loads a condensed **digest** of the session you pick: its prompts, Claude's replies, the files it
edited and its PRs. Your current session keeps going.

## The pane: `/recall`

```
/recall                      # open the pane with your 30 most recent sessions, all projects
/recall webhook retries      # open it on a full-text search
/recall p:harness routing    # p:<substring> filters by project path
```

```
┌ Recall ───────────────────────────────────────────────┐
│ search all sessions · p:<project> to filter · Enter    │
│ 30 recent sessions · 1-9 pick s: search                │
│ 1: kiln-auto-mixed-stacks-testing                      │
│    ~/projects/harness [kiln-mods] · 2m ago             │
│ 2: 3D modeling for 3D printing                         │
│    ~/projects · 18m ago                                │
└────────────────────────────────────────────────────────┘
```

- The pane opens with the keyboard. Once results load, the first row has focus.
- Press `1`–`9`, or click a row, to open a session. Arrows and Tab move between rows too. The session view shows its project, branch, age, last prompt, any search snippets and its PRs.
- Press `s` to jump to the search box, type, and press Enter to search every project. An empty search lists recent sessions again.
- In a session's view, press:
  - `l` (**load into chat**) to append the session's digest to this conversation, as context the model reads. A `✓` marks sessions you've loaded.
  - `c` to copy the command that resumes the session itself.
  - `b` to go back to the list.
- Esc returns to the prompt.

## The skill: `/recall:find-session`

| Skill | Command | Purpose |
|-------|---------|---------|
| **Recall** | `/recall:find-session [query \| --project <path> \| <session-id>]` | The model finds a past session and loads its digest. Also triggers on plain requests. |

```
/recall:find-session                         # 10 most recent sessions, all projects
/recall:find-session webhook retries         # full-text search of prompts and replies
/recall:find-session --project harness       # sessions whose working dir contains "harness"
/recall:find-session --project kiln routing  # search within one project
/recall:find-session 699fb874                # load a session by id or id prefix
```

It also triggers on phrases like "pull in my session from the other project" or "find the
conversation where I set up X".

## How it works

The pane is a mod (`hooks/register.tsx`, a function-hooks module like squad's). It runs
`scripts/recall.py` with `--json` through `$.process.run`, and loads a digest with
`$.session.append`. That adds a user-role row the model reads, framed as a record of past work
and not as instructions, plus a visible notice row. The skill calls the same script through Bash.

`scripts/recall.py` is a stdlib-only Python 3 script and is read-only. It scans
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, or `$CLAUDE_CONFIG_DIR/projects` if that is set.

- **list** reads only the head and tail of each transcript, which is fast even for very large ones. It picks up:
  - the title: the `/rename` title, otherwise the AI-generated one
  - the real working directory, branch, last activity and last prompt
- **search** uses `rg` as a prefilter when it is installed, then matches only prompts and Claude's text replies. It skips tool output, thinking and subagent transcripts.
- **digest** streams the transcript once and builds a budgeted summary (15,000 characters by default). It keeps the first and last prompts, and drops prompts from the middle when it has to.
- **usage** prints a per-day (or `--by week`) table of weighted token usage, split into main session vs subagent and per model family (opus/sonnet/haiku/other), with request counts, the number of distinct session and agent files, and the median context per request. Unlike the other subcommands it also reads `<session-id>/subagents/agent-*.jsonl`. Weighting: `input + 1.25·cache_creation + 0.1·cache_read + 5·output` tokens; a repeated `message.id` is counted once per file. `--days N` (default 14) bounds the window, `--project SUBSTR` filters on the encoded project dir name, `--json` emits the buckets as JSON.

```bash
python3 plugins/recall/scripts/recall.py usage              # last 14 days, per day
python3 plugins/recall/scripts/recall.py usage --by week --days 90 [--json]
```

Projects are matched on the session's recorded `cwd`. The encoded directory name isn't used,
because it is lossy: `/`, `-`, `_` and `.` all become `-`. The current session and temp/scratch
directories are left out of listings by default.

The skill pre-approves only `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/recall.py *`, so lookups don't
trigger permission prompts.

**Privacy:** digests go into the current conversation's context. Transcripts can contain anything
you pasted, so take care when recalling sensitive sessions into shared work. The script never reads
`~/.claude/history.jsonl` and never writes files.

## Requirements

- `python3` on `PATH`.
- `rg` (ripgrep) is optional. It speeds up search.

## Install

```bash
claude plugin install recall@apato-plugins
```

## Develop

```bash
python3 -I -m unittest discover -s plugins/recall/tests   # the script
claude plugin test plugins/recall                          # the pane (hooks/register.test.ts)
python3 plugins/recall/scripts/recall.py list --limit 5 [--json]
claude plugin validate plugins/recall
```
