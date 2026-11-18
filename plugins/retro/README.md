# Retro

Review work already attempted or completed: what was delivered, what worked, where the
agent or user contributed to friction, and what should change. Findings use evidence from
the conversation and relevant artifacts rather than assuming both parties were at fault.

## Install

```bash
command claude plugin marketplace update apato-plugins
command claude plugin install retro@apato-plugins
```

For local development, register the clone as a directory marketplace as described in the
[marketplace README](../../README.md#developing-locally), install the plugin once, and run
`/reload-plugins` after edits.

## Use

The skill can activate for retrospective requests or be invoked explicitly with the
Claude Code plugin command `/retro:retro`:

```text
/retro:retro
/retro:retro Review this session, especially the rework after the requirements changed.
/retro:retro Why did we drift from the approved spec, and what needs to change in the spec itself?
/retro:retro Review the supplied session transcript and propose improvements to our tooling and instructions.
```

The review covers:

- Intended and actual outcomes, incomplete work, and successful practices worth keeping.
- Agent deviations, unsupported assumptions, shortcuts, and verification gaps.
- User ambiguity, conflicting requirements, and decisions that contributed to rework.
- Tool failures, incorrect tool use, environment limits, and handoff problems.
- Whether authoritative artifacts were followed and whether those artifacts need improving.

Each important finding connects evidence and impact to a contributing cause. The action
plan specifies a concrete change, a proposed owner, a target, a verification method, and
its status. Sources of truth can be designs, tickets, docs, tests, or other agreed artifacts;
the skill has no dependency on a particular tool.

The default scope is the current session's stored transcript, including original turns
before compaction. A bundled Python 3 reader resolves the exact session ID, freezes a
review cutoff, and pages through messages and tool evidence without dropping middle turns
or clipping large records. It labels compaction summaries and reports malformed records,
unresolved parent links, replayed UUIDs, and subagent transcripts not yet reviewed. The
skill checks summaries against original evidence and keeps a temporary continuation note
when the review itself may compact. It needs no other plugin.

You can also select a source explicitly:

```text
/retro:retro --session <exact-session-id>
/retro:retro --transcript /absolute/path/session.jsonl
```

Transcripts are read from `$CLAUDE_CONFIG_DIR/projects` or `~/.claude/projects`, or from the
supplied path. The helper is read-only. It excludes reasoning and binary payloads, and
reports bookkeeping/request record categories it omits. Relevant child transcripts are
reviewed separately. A completed pass covers available text; deleted originals cannot be
recovered from a summary. Another runtime needs its native transcript reader or an export.

When history or artifacts are missing, the report states its limits. It does not infer
mistakes from missing context, force equal
blame, or treat normal iteration as failure. It proposes changes without editing project
rules, persistent memory, or shared artifacts unless fixes were also requested.

## Validation

```bash
python3 -I -m unittest discover -s plugins/retro/tests
command claude plugin validate plugins/retro
command claude plugin validate .
```
