---
name: five-whys
description: >-
  Iterative root cause analysis through "what caused this?" chains. Use when the user
  has a problem with an unclear root cause, or when rubber-ducky routes here. Works for
  bugs, incidents, process failures, recurring issues, or any situation where the surface
  problem isn't the real problem.
user-invocable: true
---

# Five Whys

Trace a problem back to its root cause by repeatedly asking "what caused this?" — not literally "why" (which puts people on the defensive), but the same iterative deepening.

## When to Use

- A bug keeps recurring after being "fixed"
- An incident happened and you want to prevent the next one
- The user describes a symptom but not the cause
- Rubber-ducky routed here because root cause is unclear

## The Process

### Step 1: State the Problem

Write down the problem as observed — specific, factual, no interpretation.

Bad: "The deploy system is broken"
Good: "The staging deploy failed at 2pm with error X in service Y"

**If the problem involves systems**, proactively gather evidence:
- Check Datadog logs/metrics for the relevant timeframe
- Search the codebase for the error message or failing component
- Search Glean for prior incidents with similar symptoms

Present what you found: "Here's what I see in the logs — does this match what you observed?"

### Step 2: First Level — "What caused this?"

Ask the user what they believe caused the observed problem. One question, one answer.

If they don't know, help them narrow it down with evidence. Don't guess — investigate.

### Step 3: Dig Deeper

For each answer, ask what caused *that*. Keep a running chain:

```
Problem: Staging deploy failed with timeout error
  -> What caused this? The health check didn't pass in time
    -> What caused that? The service took 45s to start instead of the usual 10s
      -> What caused that? A new dependency added 35s of initialization
        -> What caused that? The dependency loads its full config from a remote endpoint on startup
          -> ROOT CAUSE: No caching or lazy loading for the config fetch
```

### Step 4: Recognize the Root Cause

You've hit a root cause when:
- The answer is something **actionable** — you can change it
- Going deeper would be philosophical ("why do we use computers?")
- The cause is a **decision or design choice**, not another symptom

Typical depth: 3-7 levels. If you're past 7, you're probably going in circles.

### Guard Rails

**Going in circles:** If the user gives an answer that points back to something already in the chain, call it out:
> "That circles back to [earlier cause]. Let me re-read the chain — I think the root cause is actually at level [N]."

**Branching causes:** If one level has multiple causes, track them but pick the most impactful branch to follow:
> "There are two things at play here: [A] and [B]. Which one do you think has more impact? Let's follow that branch first."

**Speculative answers:** If the user says "I think maybe..." — push for evidence:
> "Can we verify that? Let me check [logs/code/metrics] to confirm."

### Step 5: Summarize the Causal Chain

Once you've hit root cause, present the full chain clearly:

> **Causal Chain:**
> 1. [Observed problem]
> 2. Caused by: [first cause]
> 3. Caused by: [second cause]
> 4. Caused by: [third cause]
> 5. **Root cause:** [actionable root cause]

### Step 6: Hand Off

Offer the user a choice:
- **Root cause is clear, fix is obvious** → "Want me to switch to plan mode and lay out the fix?"
- **Root cause found, but solution needs more thought** → "Want to go back to rubber-ducking to think through how to address this?" (invoke `/rubber-ducky:rubber-ducky`)
- **Multiple possible fixes** → "There are a few ways to address this — want to run a tradeoff analysis?" (invoke `/rubber-ducky:tradeoff-analysis`)

## Persistence

Before handing off, offer to save:
> "Want me to save this causal chain for future reference?"

If yes, write to `docs/rubber-ducky/YYYY-MM-DD-five-whys-<topic>.md`.

## What This Is NOT

- **Not systematic-debugging.** That skill is a rigid 4-phase process for actively fixing bugs. Five-whys is for understanding *why* something happened — often after the immediate fix is already in place.
- **Not a blame exercise.** If the chain leads to "person X made a mistake," go one level deeper: what about the system made that mistake easy to make?
