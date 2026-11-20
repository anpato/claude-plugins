# worktrees

A Claude Code pane for the current repository's linked Git worktrees. Run `/worktrees`
to see each branch, a brief summary, local changes, allocated storage, and GitHub PR status.

The overview stays scoped to the repository of Claude's current directory. The
primary checkout and bare repository entry are hidden, regardless of their branch
name. A linked worktree on `main` is still shown. Counts, storage scans and PR
lookups cover only linked worktrees. Select a
branch to see its full path, latest commit, upstream, changed files and staged/unstaged
diff. Copy an open command to start Claude there in another terminal.

## Use

| Command or key | Action |
|---|---|
| `/worktrees` | Open and refresh the viewer |
| `/worktrees <branch or path>` | Open with a filter |
| `/worktrees close` | Close the viewer |
| `1`–`9`, or Tab/arrows and Enter | Inspect a row |
| `s` | Focus search; Enter applies the filter |
| `a` | Clear the active filter |
| `n` / `p` | Next / previous page |
| `b` | Return from details |
| `c` | Copy a POSIX shell command to open Claude in that worktree |
| `r` | Refresh the repository list, storage and PR status |
| `q` / Esc | Close the pane |

Printable shortcuts apply when focus is outside the search field. The native pane
scrolls when its content exceeds the available height. PR badges sit to the right
of the branch title when they fit, and underneath in a narrow pane.

## What the fields mean

- **Summary:** latest commit subject, alongside tracked/untracked change counts.
  An unborn branch shows “No commits yet.”
- **Storage:** allocated bytes of the worktree directory, including ignored
  dependencies and build output. The root `.git` entry is excluded, symlinks are
  counted without following them, and hard links count once within each worktree.
  Scans stop after 1.5 seconds or 100,000 entries; `≥ … (partial)` means a lower
  bound, including when a directory is unreadable. Filesystem clone/compression
  savings and shared Git storage are not attributed to worktrees. Systems without
  allocated-block metadata use file sizes.
- **PR status:** green `↗ Open`, purple `⤵ Merged`, red `↗ Closed`, or neutral
  Draft / No PR / unavailable. `gh` queries the GitHub `origin` repository and
  branch, excluding same-named fork branches. An open PR takes precedence;
  otherwise the most recently updated of up to 20 matching PRs is shown. If a
  merged PR's head differs from the local HEAD, the badge says the branch changed
  since merge. This describes the PR state, not a guarantee that every local
  commit or uncommitted edit was merged.
- **Diff:** separate staged and unstaged sections, each limited to 24 KB.
  Untracked contents and submodule changes are excluded. The viewer never checks
  out, creates, deletes, prunes, pushes or merges a worktree.

Git reads time out after five seconds per operation; PR lookups after eight seconds.
Refresh scans up to eight worktrees concurrently. Nothing polls in the background.

## Install

Requires Python 3.9+, Git with `worktree list --porcelain -z`, and Claude Code mods
support (v2.1.299+). For PR status, install GitHub CLI and sign in with `gh auth login`.
Local viewing works without GitHub authentication or a GitHub remote.

Once this version is available on the marketplace's `main` branch:

```bash
command claude plugin marketplace add anpato/claude-plugins
command claude plugin install worktrees@apato-plugins
```

To try the source immediately, start Claude from the repository you want to inspect:

```bash
command claude --plugin-dir /path/to/claude-plugins/plugins/worktrees
```

Then run `/worktrees`. A local-directory marketplace can also load it in place;
install this new plugin once, then use `/reload-plugins` after edits.

If mods are unavailable, `/worktrees:inspect-worktrees` provides a text view.

## Design and development

Designed first in Design Studio, project `claude-worktrees`, frame `Worktrees`.
The **Viewer states** and **Pane widths** boards cover states and responsive layout.
The exported spec is in [docs/spec-worktree-viewer.md](docs/spec-worktree-viewer.md).

```bash
command claude plugin validate plugins/worktrees
command claude plugin test plugins/worktrees
python3 -I -m unittest discover -s plugins/worktrees/tests
```
