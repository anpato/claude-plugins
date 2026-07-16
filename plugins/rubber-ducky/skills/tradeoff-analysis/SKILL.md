---
name: tradeoff-analysis
description: >-
  Structured comparison of 2-4 approaches when multiple viable solutions exist. Use when
  the user is choosing between approaches, rubber-ducky routes here, or the user says
  "should I do X or Y", "what are the tradeoffs", "compare these options", "pros and cons".
user-invocable: true
---

# Tradeoff Analysis

Help the user compare approaches side-by-side so they can make an informed choice. You present the tradeoffs — they make the decision.

<HARD-GATE>
Do NOT recommend a specific option unless the user explicitly asks "what would you do?" Even then, frame it as a lean, not a verdict. The user owns the decision.
</HARD-GATE>

## When to Use

- User is torn between 2-4 approaches
- Rubber-ducky revealed multiple viable paths
- User asks "should I do X or Y?"
- Team needs to align on a direction

## The Process

### Step 1: Name the Approaches

List the approaches being considered. If the user hasn't fully articulated them, help:
> "It sounds like the options on the table are: [A], [B], and maybe [C]. Am I missing one?"

Cap at 4 options. If there are more, help the user eliminate the weakest before comparing.

### Step 2: Define the Dimensions

For each approach, collaboratively evaluate these dimensions (skip any that don't apply):

| Dimension | What to assess |
|-----------|---------------|
| **Effort** | How much work? Days, weeks, months? |
| **Risk** | What could go wrong? How bad would it be? |
| **Reversibility** | How hard to undo if it's the wrong call? |
| **Dependencies** | What else needs to change? Who else is affected? |
| **Time to value** | When does the user/team start seeing benefit? |
| **Maintenance** | What's the ongoing cost after the initial work? |
| **Alignment** | Does it fit with where the team/system is heading? |

**Use tools to ground the analysis:**
- Search the codebase to estimate effort and identify dependencies
- Check Glean for prior decisions or ADRs on similar topics
- Review Datadog for performance/reliability data if relevant

### Step 3: Present the Comparison

Present as a clean comparison. Use AskUserQuestion when appropriate to let the user evaluate inline.

Example format:

```
| Dimension     | Option A: Rewrite    | Option B: Migrate    | Option C: Wrap       |
|---------------|----------------------|----------------------|----------------------|
| Effort        | 3 weeks              | 1 week               | 2 days               |
| Risk          | Low (clean start)    | Medium (data compat) | High (tech debt)     |
| Reversibility | Hard (new system)    | Medium (can rollback) | Easy (just remove)  |
| Time to value | 4 weeks              | 2 weeks              | 3 days               |
| Maintenance   | Low                  | Medium               | High                 |
```

### Step 4: Ask, Don't Tell

> "Given these tradeoffs, which direction feels right to you?"

If the user is still unsure:
- Ask what dimension matters most to them right now
- Ask what they'd regret most in 6 months
- Ask what their team would expect them to choose

If the user asks for your opinion: give a lean with reasoning, not a verdict.
> "If I had to lean one way, I'd lean toward [B] because [reason] — but [A] is defensible if [condition]."

### Step 5: Hand Off

Once the user picks a direction:
- **Solution is clear** → "Want me to switch to plan mode and lay out the implementation for [chosen approach]?"
- **Decision should be documented** → "Want to record this as an Architecture Decision Record? It'll capture the options you considered and why you chose [chosen approach]." (invoke `/rubber-ducky:adr`)
- **Chosen approach needs more exploration** → "Want to rubber-duck the details of [chosen approach]?" (invoke `/rubber-ducky:rubber-ducky`)
- **Chosen approach is complex** → "This might benefit from decomposition — want to break it into pieces?" (invoke `/rubber-ducky:problem-decomposition`)

## Persistence

Before handing off, offer:
> "Want me to save this comparison for future reference?"

If yes, write to `docs/rubber-ducky/YYYY-MM-DD-tradeoff-<topic>.md` including the comparison table and the chosen direction.
