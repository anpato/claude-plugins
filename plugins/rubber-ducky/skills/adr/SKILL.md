---
name: adr
description: >-
  Write or update an Architecture Decision Record capturing a technical choice, its context, and
  consequences. Triggers on: "write an ADR", "record this decision", "architecture decision
  record", "document this choice", "formalize this decision", "create an ADR", "update the ADR",
  "supersede ADR". Pre-populates from tradeoff-analysis or decision-matrix artifacts when invoked
  after them.
user-invocable: true
---

# ADR — Architecture Decision Records

Create, iterate on, and manage Architecture Decision Records. ADRs capture significant technical decisions so future readers understand what was decided, why, and what the expected consequences are.

<HARD-GATE>
Do NOT auto-fill a template. When standalone, guide the user through each section Socratically — one question at a time. When arriving from tradeoff-analysis or decision-matrix, pre-populate a draft and ask the user to validate each section. The ADR should be in the user's words, not yours.
</HARD-GATE>

## When to Use

- A decision has been made and needs to be documented for the team
- Rubber-ducky, tradeoff-analysis, or decision-matrix led to a clear decision
- The user wants to propose a decision for team review (status: Proposed)
- An existing ADR needs to be updated, deprecated, or superseded

## The Process

### Step 1: Detect Context

Check whether the skill was invoked after another rubber-ducky sub-skill. If it came from an RFC (`/rubber-ducky:rfc`), pre-populate from that decision's block: its Options, Rationale, and Implications & Mitigations map directly onto the ADR's sections, and the RFC's Current State and the section's Design become the Context. If artifacts exist from a recent tradeoff-analysis or decision-matrix session in the conversation context, offer to pre-populate:

> "I see we just worked through a [tradeoff analysis / decision matrix] on [topic]. Want me to use that as the starting point for this ADR, or are you documenting a different decision?"

If pre-populating: draft Context from the problem statement, Options from the approaches considered, Decision Summary and Rationale from the chosen option, and Implications & Mitigations from the tradeoff dimensions or matrix scores. Then skip to Step 3 (Review).

If standalone or the user declines pre-population: proceed to Step 2.

If the user references an existing ADR (by number or topic): skip to **Iterating on Existing ADRs** below.

### Step 2: Build the ADR Socratically

Guide through each section one question at a time. Do NOT present a blank template.

**Title:**
> "What decision are you documenting? Give me the one-sentence version."

**Status:**
Use AskUserQuestion with choices: Proposed, Accepted, Deprecated, Superseded.

Default suggestion: "I'll mark this as Proposed — you can update it to Accepted once the team has reviewed it, or I can mark it Accepted now if this is already settled."

**Decision Summary:**
> "Give me the short version — what did you decide and what does it mean? This is the TL;DR for people who won't read the rest."

One or two sentences max. This should let a reader decide whether they need to read the full ADR.

**Context:**
> "What's the situation that led to this decision? What forces are at play — technical constraints, business requirements, team dynamics?"

This may take 2-3 exchanges. Probe until the context would make sense to someone reading it cold in 6 months. Include as much relevant input and considerations as possible.

**Options:**
> "What options did you consider? Walk me through them one at a time."

For each option, get a short description of what it entails. Number them (1, 2, 3...). If options are variants of each other, use sub-numbering (e.g., 3.1, 3.2). Probe:
> "Any other options you considered, even if you dismissed them quickly?"

**Decision — Rationale:**
> "Which option did you go with, and what was the primary reason?"

Format as: "Option X primarily because of Y." Keep it direct. If the user hedges, probe: "If you had to explain this to someone in one sentence, what would you say?"

**Decision — Implications & Mitigations:**
> "What tradeoffs are you accepting with this choice?"

Then: "What current or future work will handle those tradeoffs?"

Push for honest tradeoffs. Every decision has costs. If the user can't name any, probe harder:
> "What's the thing someone might push back on in 6 months because of this choice?"

### Step 3: Review the Draft

Present the full ADR in the standard format (see Step 5) and ask:

> "Here's the draft ADR. Read through it — does anything feel wrong, missing, or not quite right?"

Iterate until the user is satisfied. One revision at a time.

### Step 4: Number and Place

- Scan `docs/adr/` for existing ADRs to determine the next number
- If the directory doesn't exist, note that it will be created
- Number format: `0001-kebab-case-title.md` (zero-padded to 4 digits)
- Extract the max number from existing filenames by reading leading digits — handles gaps and non-standard filenames

If superseding an existing ADR, identify its number for the status cross-reference.

### Step 5: Write the ADR

Write to `docs/adr/NNNN-kebab-case-title.md`:

```markdown
# NNNN. Title

**Date:** YYYY-MM-DD

**Status:** Proposed | Accepted | Deprecated | Superseded by [NNNN](NNNN-title.md)

# Decision Summary

[Short explanation of decision and implications — the TL;DR]

# Context

[Relevant input, considerations, and forces that informed the decision]

# Options

## 1: [Option name]

[Description]

## 2: [Option name]

[Description]

## 3: [Option name]

[Description — use sub-numbering like 3.1, 3.2 for variants]

# Decision

## Rationale

Option X primarily because of Y

## Implications & Mitigations

[Tradeoffs accepted]

[Current and future work to handle the tradeoffs]
```

If superseding another ADR, also update the old ADR's status line to `Superseded by [NNNN](NNNN-new-title.md)`. Do NOT modify the old ADR's content beyond the status line.

### Step 6: Confirm

> "ADR [NNNN] written to `docs/adr/NNNN-title.md`. Want to review it in place, or are we good?"

### Step 7: Hand Off

| Signal | Action |
|--------|--------|
| Decision needs implementation | "Want me to switch to plan mode to implement this decision?" |
| Decision is one of several in a larger feature | "This looks like part of a bigger design. Want an RFC that collects the related decisions? (`/rubber-ducky:rfc`)" |
| Decision needs team review | "This is marked as Proposed. Share it with the team — when it's accepted, run `/rubber-ducky:adr` to update the status." |
| More decisions to document | "Want to write another ADR, or keep thinking through related decisions?" (invoke `/rubber-ducky:rubber-ducky`) |

## Iterating on Existing ADRs

When the user invokes the skill and references an existing ADR (by number or topic):

1. Read the existing ADR from `docs/adr/`
2. Ask what they want to do (use AskUserQuestion):
   - **Update status** — change the status line (e.g., Proposed → Accepted)
   - **Add an amendment** — append an `## Amendment` section with date and content
   - **Supersede** — create a new ADR using the old one's context as a starting point, update the old ADR's status

For status updates: change the status line, confirm with the user.

For superseding: run the full creation flow (Steps 2-6), pre-populating context from the old ADR. Update the old ADR's status to `Superseded by [NNNN](NNNN-new-title.md)`.

For amendments: append to the ADR:

```markdown
## Amendment — YYYY-MM-DD

[Amendment content]
```

## Guard Rails

| Thought | Reality |
|---------|---------|
| "Let me just fill in the template" | The ADR is the user's decision in the user's words. Guide, don't ghostwrite. |
| "This is obvious, skip the context" | Context is the most important section. Future readers need to understand WHY. |
| "We only considered one option" | Push for at least two. If there's truly only one option, it's not a decision — it's a constraint. Document that in Context instead. |
| "The implications are all positive" | Push for real tradeoffs. Every decision has costs. |
| "Let me make it sound more formal" | Match the user's voice. An ADR nobody reads because it sounds like a legal document is useless. |
| "I'll just update the status without asking" | Always confirm changes with the user before writing. |

## Key Principles

- **Context is king.** The decision itself is often obvious in hindsight — the context is what future readers need.
- **One question at a time.** Same as every rubber-ducky skill.
- **Proposed by default.** Encourage team review before finalization.
- **ADRs are immutable records** — supersede rather than rewrite. Status updates and amendments are the only acceptable modifications.
- **`docs/adr/` is the canonical location.** ADRs are permanent project documentation, not ephemeral thinking aids.
