# Rubber Ducky

A Socratic problem-solving companion for Claude Code. Think through problems conversationally before jumping to solutions.

## Skills

| Skill | Command | Purpose |
|-------|---------|---------|
| **Rubber Ducky** | `/rubber-ducky` | Main entry. Socratic questioning to understand the real problem. |
| **Five Whys** | `/rubber-ducky:five-whys` | Root cause analysis through iterative "what caused this?" |
| **Tradeoff Analysis** | `/rubber-ducky:tradeoff-analysis` | Side-by-side comparison of 2-4 approaches. |
| **Problem Decomposition** | `/rubber-ducky:problem-decomposition` | Break big problems into ordered, solvable pieces. |
| **Decision Matrix** | `/rubber-ducky:decision-matrix` | Weighted scoring for complex multi-criteria decisions. |
| **ADR** | `/rubber-ducky:adr` | Create and manage Architecture Decision Records. |

## How It Works

1. Start with `/rubber-ducky` (or it auto-triggers on "help me think through...", "I'm stuck on...", etc.)
2. The duck listens, reflects, and asks probing questions — one at a time
3. As the problem becomes clearer, it may route to a sub-skill (five-whys, tradeoff-analysis, etc.)
4. When a solution crystallizes, it offers to hand off to plan mode for implementation

## Philosophy

- **You are the solver.** The duck just helps you see the problem clearly.
- **One question at a time.** Never overwhelm.
- **The reframe is the payoff.** Everything before it is setup.
- **Numbers are thinking aids, not answers.** (For decision-matrix and tradeoff-analysis.)

## Artifacts

Skills optionally save their output to `docs/rubber-ducky/` for future reference:
- Problem statements and reframes
- Causal chains (five-whys)
- Comparison tables (tradeoff-analysis)
- Dependency maps (problem-decomposition)
- Scored matrices (decision-matrix)
- Architecture Decision Records (adr) → stored in `docs/adr/`
