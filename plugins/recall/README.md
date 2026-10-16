# Recall

Pull a past Claude Code session from **any directory** into the conversation you're in now.
`--continue` and `/resume` only work for the current directory. Recall searches every project and
loads a condensed **digest** of the session you pick: its prompts, Claude's replies, the files it
edited and its PRs. Your current session keeps going.

## Skills

| Skill | Command | Purpose |
|-------|---------|---------|
| **Recall** | `/recall:recall [query \| --project <path> \| <session-id>]` | Find a past session and load its digest as context. |

```
/recall:recall                         # 10 most recent sessions, all projects
/recall:recall webhook retries         # full-text search of prompts and replies
/recall:recall --project harness       # sessions whose working dir contains "harness"
/recall:recall --project kiln routing  # search within one project
/recall:recall 699fb874                # load a session by id or id prefix
```

It also triggers on phrases like "pull in my session from the other project" or "find the
conversation where I set up X".

## How it works

`scripts/recall.py` is a stdlib-only Python 3 script and is read-only. It scans
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, or `$CLAUDE_CONFIG_DIR/projects` if that is set.

- **list** reads only the head and tail of each transcript, which is fast even for very large ones. It picks up:
  - the title: the `/rename` title, otherwise the AI-generated one
  - the real working directory, branch, last activity and last prompt
- **search** uses `rg` as a prefilter when it is installed, then matches only prompts and Claude's text replies. It skips tool output, thinking and subagent transcripts.
- **digest** streams the transcript once and builds a budgeted summary (15,000 characters by default). It keeps the first and last prompts, and drops prompts from the middle when it has to.

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
python3 -I -m unittest discover -s plugins/recall/tests
python3 plugins/recall/scripts/recall.py list --limit 5
claude plugin validate plugins/recall
```
