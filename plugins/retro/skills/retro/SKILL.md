---
name: retro
description: >-
  Review work already attempted or completed to understand outcomes, agent mistakes,
  user communication gaps, tooling friction, and instruction or source-of-truth failures.
  Use for a retrospective, post-task recap, lessons learned, or a review of what went
  wrong in the collaboration and how to improve it. Not a substitute for reviewing
  code correctness or planning work that has not started.
---

# Retro

Explain what happened, why it happened, and what should change next time. Help the agent
recognize its own mistakes and help the user see where their instructions or decisions
contributed to friction. Be candid about behavior and consequences without assigning
motives, grading people, or manufacturing equal responsibility.

## Establish the evidence

Default to the work in the current conversation; honor any narrower task, time range,
or supplied session history. State the intended outcome, what was delivered, and what
remains incomplete. Include successful choices worth keeping when supported by evidence.
An unfinished or abandoned attempt can still have a useful retrospective.

Use the conversation, applicable instructions, decisions, relevant artifacts, diffs,
tool results, and verification records already available. Inspect connected sources or
local files when they can resolve a material uncertainty within the requested scope.
Keep retrieval focused on this work rather than searching unrelated session history.

For each consequential claim, point to a short quote, identifiable exchange, file
location, artifact revision, or tool result. Distinguish observations from interpretations
and unknowns. A current file or successful test does not establish what the agent knew
or checked earlier. Do not invent timing, costs, failed attempts, or hidden reasoning.

If the work predates the visible context, explain what can and cannot be assessed.
Proceed with a bounded review when possible; request missing context only when it would
materially change the findings. Absence from an incomplete transcript is not proof that
an action was skipped. Unavailable evidence is a coverage limit, not automatically a
tooling or user failure.

## Reconstruct the consequential decisions

Compare the requested outcome, the governing instructions at each decision, the agent's
actions, and the observed result. Focus on decisions that affected quality, effort,
rework, trust, or completion; avoid replaying every tool call.

Examine these lenses where relevant, without turning them into a quota of findings:

- **Agent execution:** Did the agent follow the agreed scope, consult required sources,
  surface uncertainty, and verify its claims? Look for silent substitutions, ignored
  constraints, premature completion, repeated failed approaches, skipped checks, and
  shortcuts that affected the outcome. Distinguish an authorized tradeoff from cutting
  corners. The agent owns unsupported assumptions and failure to clarify consequential
  ambiguity even when the original request was imperfect.
- **User communication and decisions:** Identify specific conflicting requirements,
  missing acceptance criteria, late decisions, or ambiguous references that caused
  avoidable work. Explain what interpretation they allowed and what wording or decision
  would have helped. Distinguish a changed requirement from an earlier error. Do not call
  ordinary iteration, concise instructions, or a correction to agent drift a user failure.
  Do not expect the user to specify routine implementation details the agent should infer.
- **Tools and environment:** Identify actual failures, capability or permission limits,
  poor discoverability, and fragile handoffs. Separate a tool failure from incorrect use
  or an avoidable retry loop. Assess whether the fallback preserved the requirement and
  whether any loss of fidelity was disclosed. Use only observed impact; do not invent
  time or token savings.
- **Instructions and shared artifacts:** Check whether rules, specs, tickets, designs,
  tests, or decision records were discoverable, usable, consistent, and maintained.
  Separate a missing rule from a clear rule the agent ignored; adding more instructions
  is not automatically the right fix.

### Check the source of truth in both directions

Identify which artifact or channel was authoritative for which decisions, based on the
instructions and agreements in effect at the time. A tool's existence does not make it
authoritative, and different artifacts may govern different parts of the work. Respect
later explicit changes; do not judge an earlier action against a later requirement.

Check whether the agent consulted and followed the relevant source, reconciled conflicts,
and carried approved decisions through the expected handoff. Also check whether the
source itself lacked states, acceptance criteria, ownership, current decisions, or enough
detail to implement and validate the work. Both failures can coexist: an incomplete spec
does not excuse ignoring its clear requirements.

For example, if an approved design was required, distinguish building without reading it
from finding that it omitted an error state. The first calls for an agent workflow fix;
the second calls for improving the artifact or its decision process. Apply this reasoning
to whatever tools and artifacts the project uses; require no particular product.

## Turn findings into changes

For each material issue, connect **expected behavior → observed behavior → consequence →
contributing cause**. Trace beyond labels such as "bad communication" or "tooling issue"
to an actionable mechanism. Consider multiple contributing causes when the evidence
supports them. Label a suspected cause as a hypothesis and say what would confirm it.
Do not keep asking why until a speculative explanation sounds certain.

Prioritize by observed impact and likelihood of recurrence. Group repeated symptoms under
the same cause. Do not inflate a minor inconvenience into a systemic failure or infer a
recurring pattern from one event without supporting history.

Make the important fixes reviewable now. For each, give:

- **Change:** A concrete behavior, tool adjustment, artifact edit, or decision process.
  Include proposed wording or a small patch when useful, rather than "communicate better"
  or "update the rules."
- **Owner and target:** Who can make the change and where it belongs. Mark ownership as
  proposed unless already agreed. Agent improvements should describe the next observable
  action, not promise permanent self-learning.
- **Verification:** How the next relevant task or a focused check will show the fix works.
- **Status:** Proposed, applied, verified, or blocked, with evidence for completed work
  and the dependency for a blocked action. Keep these states distinct.

Prefer the smallest change at the right layer: clearer task wording for a local ambiguity,
an artifact correction for a spec gap, a tool fix for a reproducible defect, or a reusable
rule for a demonstrated recurring need. Preserve practices that worked. Avoid blanket
bans, redundant checklists, or new global rules based on a single example.

A retrospective request authorizes investigation and recommendations, not automatic edits
to project rules, persistent memory, shared artifacts, or external systems. If the user
also requested fixes, apply the changes within that scope and verify them; otherwise
deliver concrete proposals without a mandatory approval ceremony. Do not claim that a
recommendation has already changed future behavior or that a fix resolves more than was
checked.

## Deliver the retrospective

Scale the report to the work. Use a brief outcome recap, useful practices to retain,
prioritized findings with evidence and responsibility, and an action plan linked to those
findings. A compact table can connect changes, proposed owners, targets, verification,
and status. Make agent and user contributions explicit where supported; say when no
material issue was found for either rather than filling a blame quota.

Close with the material unknowns or decisions that remain. Keep evidence limits visible.
For a small task, a few paragraphs and actions are enough. Deliver in the conversation
unless the user requested a saved artifact or an established workflow specifies one.
