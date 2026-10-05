---
name: rfc
description: >-
  Draft or update an RFC (a design proposal for a feature or system change) from a PRD,
  wireframes, or a problem description, with an ADR-style decision block for every real choice.
  Triggers on: "write an RFC", "draft an RFC", "turn this PRD into an RFC", "RFC for X",
  "update the RFC", "the PRD changed, update the RFC", "break this feature down into an RFC".
  Writes the document only, never the feature code. Hands accepted decisions off to
  rubber-ducky:adr.
user-invocable: true
---

# RFC — Request for Comments

Turn a product requirement (a PRD, wireframes, a ticket, or a problem statement) into an
engineering proposal the team can review: what exists today, what will change, which decisions
it asks reviewers to make, and the order of work.

An RFC is a *proposal*. It changes as review and the PRD move. An ADR is an immutable record of
**one** decision. An RFC contains several decisions; each one gets an ADR-shaped block inside
the RFC, and the accepted ones can be promoted to standalone ADRs with `/rubber-ducky:adr`.

<HARD-GATE>
1. **The document, not the feature.** An RFC request means writing the RFC. Do not scaffold,
   stub, or write feature code. A request to "stub things out" or "get a branch ready" while
   working on an RFC means stubbing the RFC. Ask if unsure.
2. **Evidence for every claim about today's system.** Every statement about current code cites
   `path:line` (or a command and its output) gathered in this session. If something can't be
   checked, label it as an assumption.
3. **Decisions are the user's.** Draft the decision blocks from what the session established,
   then validate each one with the user, one at a time. Never present a rationale or trade-off
   the user hasn't confirmed as settled.
</HARD-GATE>

## When to Use

- A PRD or wireframes exist and engineering needs a design before tickets are created
- A feature touches several systems and needs its decisions surfaced for review
- The PRD changed and the RFC must follow it
- After `/rubber-ducky:problem-decomposition` has broken a large feature into parts

## The Process

### Step 1: Follow the repository's conventions

Before writing anything, look for how this repo already does RFCs:

- Existing RFCs: search for `*rfc*.md`, `docs/rfcs/`, `docs/rfc/`, and RFC templates.
- Process docs: ADRs or READMEs that say where RFCs live, what format they use, and how they
  are reviewed.
- The project's instructions (`CLAUDE.md`, `AGENTS.md`, contributing guides).

If existing RFCs disagree with each other, show the user the differences as a short table and
ask which to follow:

> "This repo has two RFC styles: [A] (short, numbered, deep design in a linked issue) and [B]
> (one section per feature, with Files Changed and Open Questions). Which should this follow?"

If the repo has no convention, use the template in Step 5. Either way, **add the Decision blocks**
(Step 4). They are what most RFC templates lack.

### Step 2: Gather the inputs

- **Requirements:** the PRD, ticket, wireframes, design notes. If a source can't be read (an
  auth-walled document, no connector available), ask the user to export it to a file. Don't
  guess its contents from titles or links.
- **Read the whole source.** Strip embedded base64 images from exported documents before reading.
- **Keep a copy** of the version you worked from (a scratch file), so the next PRD update can be
  diffed (Step 7).
- **Note which source is canonical** when several links point to different documents.

### Step 3: Map the current state

For each feature in the requirements, find what exists today: routes, data, triggers, jobs,
prompts, gates, and the gaps. Delegate broad code searches to subagents with a structured brief
(goal, exact paths, scope, output format with `path:line` evidence), and spot-check their key
claims before relying on them.

Record what you find as a **Current State** table: concern | today | reference.

Look especially for:
- behavior the requirements assume exists but doesn't;
- existing behavior the requirements would break;
- duplicated logic or sync points between packages or services;
- bugs found along the way. List them as prerequisites and tickets of their own; don't fold them
  silently into the design.

### Step 4: Identify the decisions

Go through every section and ask: **is there a real choice here, with more than one viable
option?**

- **Yes:** the section gets a **Decision block** (Options, then Rationale, then Implications &
  Mitigations, plus a Status).
- **No, it's a requirement or a constraint:** no block. Record it in the Design, or in Context
  if it shapes other decisions.

Typical decisions: data shape and storage, sync vs async, read model (endpoint vs projection
vs client composition), extend an existing system vs build a new one, where a rule is enforced,
rollout mechanism.

Push for at least two options per decision. If there is truly only one, it's a constraint.

Then add every decision to the **Decisions** table at the top of the RFC.

### Step 5: Draft the RFC

Use the repo's convention (Step 1), extended with the Decisions table and Decision blocks. When
there is no convention, use this template:

```markdown
# RFC: <Title>

**Status**: Draft | In Review | Accepted | Superseded
**Author**: <name>
**Date**: <Month YYYY>
**Last Updated**: <YYYY-MM-DD>
**Tracking**: <tickets> · <PRD link> · <designs>

---

## Table of Contents

## Executive Summary
<The problem, the proposal and why, in plain language. Who it's for and how success is measured.>

## Decisions
| # | Decision | Status | Section |
| --- | --- | --- | --- |
| D1 | <one line> | Proposed | [link] |

## Core Principles
| # | Principle | Implication |

## Current State
| Concern | Today | Reference |

## Section N: <Feature>

> **Story**: _As a <user>, I want <goal>._ (Quote or closely paraphrase the requirement.)

### Design
<Requirements, then the proposed design. Existing code cited with path:line.>

### API Endpoints  (when applicable)

### Decision  (only when the section involves a real choice)
**Status:** Proposed

**Options**
1. **<Option>**: <what it entails>
2. **<Option>**: <what it entails>

**Rationale:** Option X, primarily because of Y.

**Implications & Mitigations:** <trade-offs accepted> → <the work that handles each one>.

### Files Changed
- `path`: what changes

### Open Questions
- [ ] <a product or engineering question this section waits on>

---

## <Cross-cutting sections>
<Shared mechanisms several features depend on: data model, new infrastructure, migrations,
access control. Same Design / Decision / Files Changed / Open Questions shape.>

## Data Model
| Change | Where | Notes |
<plus new sync points between packages or services>

## Rollout and Testing
<flag or experiment, sequencing, deploy order, and the tests that prove it>

## Implementation Order
### Phase N: <name>
| # | Task | Dependency |

## Appendix: New Files Summary
```

**Writing rules:**
- Each feature section is self-contained, so a requirement change touches only its section.
- A design that waits on the PRD says so with an Open Question, not with an invented answer.
- Cite real paths, and check that every cited file exists before handing the draft over.
- Write for a reader six months from now. Explain *why*, not only *what*.

### Step 6: Validate the decisions with the user

Go through the Decisions table one decision at a time:

> "D2, the provided opener. I've drafted: Option 2 (the API writes the opener), primarily
> because the PRD wants the card's question verbatim. The trade-off is that the first reply
> loses the previous session's transcript; the mitigation is that the question carries that
> continuity. Does that match your reasoning? What would you change?"

Rewrite the block in the user's words. If the user is unsure, offer
`/rubber-ducky:tradeoff-analysis` or `/rubber-ducky:decision-matrix` for that decision, then come
back.

### Step 7: Keep the RFC in step with the requirements

When the user says the PRD (or the designs) changed:

1. Get the new version (an export if needed) and **diff it** against the saved copy.
2. Summarise what changed in plain language: added, removed, and reworded requirements.
3. Map each change to the RFC sections it affects. Update only those, including the TOC,
   Decisions table, Data Model and Implementation Order.
4. Flag new conflicts with current code, and any decision the change reopens.
5. Save the new version as the baseline for the next diff.

### Step 8: Hand off

| Signal | Action |
| --- | --- |
| Draft is ready for review | Offer to open a docs-only PR. Check first for an existing PR on the branch. Don't push without the user's go-ahead. |
| A decision is accepted in review | Offer `/rubber-ducky:adr` to record it as a standalone ADR, pre-populated from its Decision block. |
| RFC is accepted | Offer to turn the Implementation Order into tickets in the team's tracker. Only **after** acceptance. |
| A decision needs more thought | Route to `/rubber-ducky:tradeoff-analysis` or `/rubber-ducky:decision-matrix`. |
| Ready to build | Offer plan mode for the first phase. |

## Guard Rails

| Thought | Reality |
| --- | --- |
| "They said stub it out, so I'll scaffold the code" | In an RFC conversation, "stub" means the document. Ask before writing any code. |
| "I'll describe today's system from memory" | Every current-state claim needs `path:line` from this session. |
| "This section obviously needs a decision block" | Only real choices get one. Requirements and constraints don't. |
| "I'll fill in the rationale myself" | Draft it, then confirm it with the user. Their reasoning, their words. |
| "The PRD is ambiguous, so I'll pick an interpretation" | Write an Open Question, and quote the ambiguous text. |
| "The PRD changed, so I'll rewrite the RFC" | Diff it, then update only the affected sections. |
| "The RFC is done, so I'll create the tickets" | Tickets come after the RFC is accepted. |
| "I'll just mirror the existing RFC exactly" | Follow its structure, and still add the Decisions table and Decision blocks. |

## Key Principles

- **Requirements in, decisions out.** The value of an RFC is surfacing the choices reviewers
  must make, with honest trade-offs.
- **Evidence over memory.** Current state is cited, not recalled.
- **Sections track the source.** One feature, one section, so changes stay local.
- **RFCs evolve; ADRs record.** Iterate on the RFC freely. Promote settled decisions to ADRs.
