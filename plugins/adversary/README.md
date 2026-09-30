# Adversary

An adversarial reviewer and red-teamer for Claude Code. The default stance is to **break things**,
not bless them — hunt bugs, failure modes, and hidden assumptions instead of rubber-stamping.

## Skills

| Skill | Command | Purpose |
|-------|---------|---------|
| **Adversarial Review** | `/adversary:adversarial-review` | Attack a PR, diff, or existing code — find bugs, edge cases, and broken assumptions. |
| **Red-Team Build** | `/adversary:red-team-build` | Pre-mortem a design, plan, or feature before/while building — surface failure modes up front. |

## How it works

1. **Adversarial Review** auto-triggers on "review my PR", "poke holes in this", "try to break
   this", "find the bugs". It reads the actual diff, enumerates the attack surface, and reports each
   finding with a concrete failure scenario — labeled **CONFIRMED** or **PLAUSIBLE**.
2. **Red-Team Build** auto-triggers on "pre-mortem", "stress-test this design", "what could break
   this", "before I build this". It writes an incident retro *from the future*, attacks the design
   across scale/concurrency/failure/security/ops dimensions, and audits load-bearing assumptions.

## Philosophy

- **A failure scenario or it didn't happen.** Every finding is reproducible on paper.
- **CONFIRMED vs PLAUSIBLE, always labeled.** A guess is never laundered as a fact.
- **Steelman first, then break.** Understand the strongest case for the code, then attack it.
- **A fair adversary.** Attack the work, not the author — and end with fixes, not just doom.

## Relationship to other tools

- The built-in `/code-review` skill does the systematic mechanical sweep of a diff. **Adversarial
  Review** is the *stance* layered on top — run `/code-review` for coverage, then attack.
- The `rubber-ducky` plugin explores a problem space openly. **Red-Team Build** attacks a *chosen*
  direction, so it comes after a direction exists.
