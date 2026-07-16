---
name: problem-decomposition
description: >-
  Break big, hairy problems into manageable, ordered sub-problems. Use when the problem
  is too large to tackle at once, rubber-ducky routes here, or the user says "this is too
  big", "where do I even start", "I'm overwhelmed", "break this down for me".
user-invocable: true
---

# Problem Decomposition

Take a big, overwhelming problem and break it into pieces small enough to solve. The goal is to find the smallest thing that unblocks progress.

## When to Use

- The problem feels too big to know where to start
- Rubber-ducky identified that the scope is too large for a single approach
- Multiple independent workstreams are tangled together
- The user is overwhelmed or paralyzed by complexity

## The Process

### Step 1: State the Big Problem

Write it down in one sentence. If you can't, the problem isn't understood yet — go back to `/rubber-ducky:rubber-ducky`.

### Step 2: Identify Sub-Problems

Work with the user to break the problem into independent pieces. Ask:
> "If you could wave a magic wand and solve ONE part of this, which part would it be?"

Then: "What's left after that?"

Keep decomposing until each sub-problem is:
- **Independent** — can be solved without solving the others first (or has clear, minimal dependencies)
- **Concrete** — you could explain to someone what "done" looks like
- **Appropriately sized** — solvable in a single focused session

**Use tools to inform decomposition:**
- Read the codebase to understand component boundaries
- Search Glean for existing plans or prior decompositions of similar problems
- Check for natural seams in the system architecture

### Step 3: Map Dependencies

For each sub-problem, identify:
- Does it depend on another sub-problem being solved first?
- Does it block other sub-problems?
- Can it be worked on in parallel with others?

Present as a simple dependency list:

```
1. [Sub-problem A] — no dependencies (can start now)
2. [Sub-problem B] — depends on A
3. [Sub-problem C] — no dependencies (can start now, parallel with A)
4. [Sub-problem D] — depends on B and C
```

### Step 4: Pick the Starting Point

The first sub-problem to tackle should be:
1. **Has no dependencies** (or only resolved ones)
2. **Unblocks the most other sub-problems**
3. **Provides the most learning** (reduces uncertainty for later sub-problems)

If multiple sub-problems tie, ask the user:
> "Both [A] and [C] could be tackled first. [A] unblocks more downstream work, but [C] would answer some open questions. Which would you rather start with?"

### Step 5: Route the First Sub-Problem

For the chosen sub-problem:
- **It's clear enough to plan** → "Want me to switch to plan mode for [sub-problem]?"
- **It needs more thinking** → "Let's rubber-duck [sub-problem] to make sure we understand it." (invoke `/rubber-ducky:rubber-ducky`)
- **Multiple approaches to solve it** → "There are a few ways to approach [sub-problem] — want to compare them?" (invoke `/rubber-ducky:tradeoff-analysis`)

### Step 6: Track Progress

If the problem has multiple sub-problems that will be tackled across sessions, offer to save the decomposition:
> "Want me to save this breakdown? That way we can track which pieces are done and pick up where we left off."

## Guard Rails

**Over-decomposition:** If sub-problems are smaller than ~30 minutes of work, you've gone too far. Merge them back.

**False independence:** If "independent" sub-problems keep referencing shared state or shared decisions, they're not actually independent. Identify the shared concern and extract it as its own sub-problem that goes first.

**Analysis paralysis:** If the user is stuck on the decomposition itself, pick the most obvious sub-problem and start there. Momentum beats perfect planning.

## Persistence

Before handing off or wrapping up, offer:
> "Want me to save this decomposition for future reference?"

If yes, write to `docs/rubber-ducky/YYYY-MM-DD-decomposition-<topic>.md` including:
- The big problem statement
- Sub-problems with dependencies
- Recommended order
- Which sub-problem was tackled first (and what happened)
