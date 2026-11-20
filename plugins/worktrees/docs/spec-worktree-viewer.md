<!-- Copied from the design studio, project "claude-worktrees" (spec worktree-viewer, approved, version 1). Change it in the studio; this copy is rewritten. -->

# Worktree viewer

## Purpose
A Claude Code plugin named `worktrees` opens a native pane with `/worktrees`. It helps the user see which Git worktrees belong to the current repository, inspect their local changes, and copy a shell command to start Claude in one.

## Presentation decision
The owner chose a bare terminal frame (D1). Use a dark monospace Claude Code layout, with a conversation on the left and the Worktrees pane on the right. The `viewer-flow` diagram documents navigation.

## Layout and behavior
The pane is scoped to the Git repository of Claude's current directory. A compact terminal-compatible list shows each branch, current-worktree marker, tracked/untracked change counts, and ahead/behind counts when an upstream exists. The overview omits repository labels and paths; the detail view includes the full worktree path. Each worktree has a brief factual summary: the latest commit subject and whether it is clean or how many tracked/untracked paths changed. The detail view repeats the full summary. These summaries come from Git, without model calls or reading file contents as instructions. Selecting a row reveals its path, HEAD, lock/prunable reason, changed files and a bounded Git diff. Actions: Back, Refresh, Copy open command, Close. Search filters branch names and paths. The viewer does not change directories in the current Claude session.

## GitHub merge status
Each worktree row shows the associated GitHub PR state: green pull-request symbol for Open, purple merge symbol for Merged, red for Closed, neutral for Draft, No PR, or unavailable status. Include text labels so color is not required. Align the PR badge to the right of the branch title when it fits; place it underneath in narrow panes. Keep storage on a separate line so it remains visible at every pane width. Use the authenticated `gh` CLI with read-only queries against the repository's GitHub origin; select the most recently updated PR for that branch in the same repository, preferring an open PR. Show PR number and link in the detail view. Missing CLI/authentication, non-GitHub remotes, detached HEADs, and offline errors never imply that a branch was merged. No merge or push action is provided. Refresh rereads PR status.

## Storage usage
Each row and its detail view show allocated storage for that worktree directory, including ignored dependencies and build output. Exclude the root `.git` entry so the main checkout does not absorb the shared Git object store. Do not follow symlinks, and count hard-linked files once per worktree. Use filesystem allocated blocks where available, otherwise file sizes. Scans are bounded to 1.5 seconds or 100,000 entries per worktree; a partial or unreadable scan is explicitly marked as a lower bound. Missing directories show storage unavailable. This is a directory allocation estimate: shared Git storage and filesystem clone/compression savings are not attributed to individual worktrees.

## States
Loading, ready, single worktree, no search matches, not a Git repository, and Git errors have explicit messages. Missing/prunable paths remain visible with their reason. Detached HEADs and bare repositories have explicit labels. Individual worktree inspection failures do not discard the repository list.

## Implementation
Follow the existing Recall and Squad mods with TypeScript hooks and native UI components. A Python 3 standard-library helper invokes Git with argv arrays. Parse NUL-delimited porcelain output to preserve spaces and special path characters. Git reads run with optional locks disabled. Inspection and diff are read-only and bounded; refresh occurs on request. Copy-open uses POSIX shell quoting. No creation, removal, pruning or branch checkout is included.

## Validation
Exercise real temporary Git repositories for multiple worktrees, changes, detached HEADs, locks, missing paths, unusual paths, and non-repositories. Native mod tests cover opening, selecting, searching, refresh, errors and copying an open command. Validate plugin and marketplace manifests.
