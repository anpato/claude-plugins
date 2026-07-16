---
name: decision-matrix
description: >-
  Weighted scoring for complex decisions with many factors. Use when tradeoff-analysis
  isn't structured enough, the user needs to evaluate many options against many criteria,
  or when the user says "help me decide", "score these options", "weighted comparison",
  "decision matrix". More rigorous than tradeoff-analysis — use when the stakes are high
  or the team needs an auditable decision process.
user-invocable: true
---

# Decision Matrix

A structured, weighted scoring framework for complex decisions. The numbers are a thinking aid — they surface hidden preferences and force explicit tradeoffs. They are not the answer.

## When to Use

- More than 3 options with more than 4 evaluation criteria
- High-stakes decisions that need an auditable trail
- Team decisions where alignment on criteria matters more than the final score
- Tradeoff-analysis didn't produce a clear winner
- The user's gut says one thing but they can't articulate why

## The Process

### Step 1: List the Options

Enumerate the options being evaluated. Help the user articulate if needed.

If there are more than 5 options, first eliminate the weakest:
> "Before we score everything, are there any options we can rule out immediately?"

### Step 2: Define Criteria

Collaboratively identify what matters. Ask:
> "When you imagine looking back on this decision in 6 months, what would make you say 'we chose well'? What would make you say 'we chose badly'?"

Common criteria (offer as starting points, let the user add/remove):
- Cost / effort
- Time to deliver
- Risk / failure impact
- Reversibility
- Team capability / learning curve
- User impact
- Maintenance burden
- Strategic alignment

Cap at 7 criteria. More than that dilutes the signal.

### Step 3: Weight the Criteria

Ask the user to rate each criterion's importance from 1-5:

> "Not all of these matter equally. Let's weight them. For each criterion, rate its importance from 1 (nice to have) to 5 (make or break)."

Use AskUserQuestion for each criterion to make this interactive.

### Step 4: Score Each Option

For each option, score it against each criterion from 1-5:

**Use evidence where possible:**
- Check the codebase to estimate effort
- Review Datadog metrics for performance/reliability claims
- Search Glean for team capability or prior experience data

Present one option at a time:
> "Let's score [Option A]. For [Criterion 1: Cost], how would you rate it? 1 = very expensive, 5 = very cheap."

### Step 5: Calculate and Present

Calculate weighted scores: `score * weight` for each cell, sum per option.

Present as a table:

```
| Criteria (weight)        | Option A | Option B | Option C |
|--------------------------|----------|----------|----------|
| Cost (4)                 | 3 (12)   | 5 (20)   | 2 (8)    |
| Time to deliver (5)      | 2 (10)   | 4 (20)   | 5 (25)   |
| Risk (3)                 | 4 (12)   | 3 (9)    | 2 (6)    |
| Team capability (2)      | 5 (10)   | 3 (6)    | 4 (8)    |
| Strategic alignment (4)  | 3 (12)   | 4 (16)   | 2 (8)    |
|--------------------------|----------|----------|----------|
| **TOTAL**                | **56**   | **71**   | **55**   |
```

### Step 6: Gut Check

This is the most important step. Ask:

> "Option [B] scored highest at [71]. Does that match your gut feeling? If not — that's interesting. What does your gut know that the matrix doesn't?"

If the result and the gut disagree, explore why:
- Are the weights wrong? ("Maybe strategic alignment should be a 5, not a 4")
- Is a criterion missing? ("We didn't account for team morale")
- Is one criterion actually a dealbreaker? ("If risk is above 3, nothing else matters")

Adjust and re-score if the user identifies a flaw. The matrix is a tool for thinking, not a calculator.

### Step 7: Hand Off

Once the user is comfortable with the decision:
- **Ready to implement** → "Want me to switch to plan mode for [chosen option]?"
- **Decision should be documented** → "This was a thorough evaluation — want to capture it as an Architecture Decision Record?" (invoke `/rubber-ducky:adr`)
- **Needs more exploration** → "Want to rubber-duck the details of [chosen option]?" (invoke `/rubber-ducky:rubber-ducky`)
- **Needs to be broken down** → "This is a big undertaking — want to decompose it?" (invoke `/rubber-ducky:problem-decomposition`)

## Persistence

Before handing off, offer:
> "Want me to save this decision matrix for future reference? It's useful as an audit trail for the team."

If yes, write to `docs/rubber-ducky/YYYY-MM-DD-decision-<topic>.md` including:
- The options considered
- The weighted criteria
- The full scored matrix
- The gut-check discussion
- The final decision and reasoning
