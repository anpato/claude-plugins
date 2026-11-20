<!-- Copied from the design studio, project "claude-worktrees" (decisions). Change it in the studio; this copy is rewritten. -->

# Decisions

## D1. Which frame should surround the terminal design?

- Status: resolved
- Asked: 2026-11-20 by claude · spec worktree-viewer

The viewer runs in Claude Code. The design-studio skill asks for your device choice before drawing a new design.

Evidence: Recall and Squad both use native Claude Code panes (plugins/recall/hooks/register.tsx and plugins/squad/hooks/register.tsx).

Options:

- **Bare terminal frame** (picked): Focus on the conversation and worktree pane without a device wrapper.
- **Mac window**: Wrap the terminal design in a Mac window.

Answer (2026-11-20): Bare terminal frame

Resolution: Recorded the owner's bare terminal frame choice in the spec; the visual design uses device none.
