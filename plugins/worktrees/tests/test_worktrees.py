import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "worktrees.py"
spec = importlib.util.spec_from_file_location("worktrees", SCRIPT)
wt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wt)


class Repositories(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.git("init", "-b", "primary-checkout")
        self.git("config", "user.name", "Worktree tests")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "tracked.txt").write_text("initial\n")
        self.git("add", ".")
        self.git("commit", "-m", "Initial work")
        self.primary = self.repo
        # Most tests run from a linked worktree. Naming it main also guards
        # against hiding branches by name instead of hiding the primary entry.
        self.repo = self.add_worktree(name="main", dirname="active worktree")

    def git(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, check=True, env=env).stdout

    def add_worktree(self, name="feature", dirname="linked worktree"):
        path = self.root / dirname
        self.git("worktree", "add", "-b", name, str(path))
        return path

    def test_inventory_scope_current_subdirectory_and_summary(self):
        linked = self.add_worktree()
        (linked / "feature.txt").write_text("feature\n")
        self.git("add", ".", cwd=linked)
        self.git("commit", "-m", "Add a feature", cwd=linked)
        nested = linked / "nested"
        nested.mkdir()
        rows = wt.inventory(nested)["worktrees"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["path"], str(linked))
        self.assertTrue(rows[0]["isCurrent"])
        self.assertEqual(rows[0]["summary"], "Latest: Add a feature")
        self.assertFalse(rows[1]["isCurrent"])

    def test_primary_hidden_from_both_checkouts_linked_main_still_visible(self):
        for cwd in (self.primary, self.repo):
            with self.subTest(cwd=cwd):
                rows = wt.inventory(cwd)["worktrees"]
                self.assertEqual([row["path"] for row in rows], [str(self.repo)])
                self.assertEqual(rows[0]["branch"], "main")
                self.assertEqual(rows[0]["isCurrent"], cwd == self.repo)
        with patch.object(wt, "storage", wraps=wt.storage) as scan, patch.object(wt, "pull_request", wraps=wt.pull_request) as prs:
            wt.inventory(self.primary)
        self.assertEqual([call.args[0] for call in scan.call_args_list], [str(self.repo)])
        self.assertEqual([call.args[1]["path"] for call in prs.call_args_list], [str(self.repo)])
        with self.assertRaisesRegex(wt.GitError, "registered linked worktree"):
            wt.inspect(self.repo, self.primary)

    def test_no_linked_worktrees_returns_empty_without_scanning_primary(self):
        self.git("worktree", "remove", str(self.repo), cwd=self.primary)
        with patch.object(wt, "summarize") as summarize:
            self.assertEqual(wt.inventory(self.primary), {"worktrees": []})
        summarize.assert_not_called()

    def test_staged_unstaged_and_untracked_unusual_names(self):
        (self.repo / "tracked.txt").write_text("staged\n")
        self.git("add", "tracked.txt")
        (self.repo / "tracked.txt").write_text("unstaged\n")
        name = "new ' file\nwith newline.txt"
        (self.repo / name).write_text("untracked body")
        info = wt.inspect(self.repo, self.repo)
        self.assertEqual(info["totalFiles"], 2)
        self.assertIn({"status": "MM", "path": "tracked.txt"}, info["files"])
        self.assertIn({"status": "??", "path": name}, info["files"])
        self.assertIn("Staged\n", info["diff"])
        self.assertIn("Unstaged\n", info["diff"])
        self.assertNotIn("untracked body", info["diff"])

    def test_rename_record_does_not_swallow_next_file(self):
        self.git("mv", "tracked.txt", "renamed file.txt")
        (self.repo / "untracked").write_text("new")
        info = wt.inspect(self.repo, self.repo)
        self.assertEqual(info["totalFiles"], 2)
        self.assertEqual(info["files"][0]["path"], "renamed file.txt")
        self.assertEqual(info["files"][1]["path"], "untracked")

    def test_detached_locked_and_missing_worktrees(self):
        linked = self.add_worktree()
        self.git("checkout", "--detach", cwd=linked)
        self.git("worktree", "lock", "--reason", "Keep this\nwork", str(linked))
        rows = wt.inventory(self.repo)["worktrees"]
        detached = next(row for row in rows if row["path"] == str(linked))
        self.assertTrue(detached["detached"])
        self.assertEqual(detached["locked"], "Keep this\nwork")
        self.git("worktree", "unlock", str(linked))
        shutil.rmtree(linked)
        missing = wt.inventory(self.repo)["worktrees"][1]
        self.assertIsNotNone(missing["prunable"])
        self.assertIsNone(missing["storageBytes"])
        self.assertIn("missing", missing["error"])

    def test_worktree_path_ending_in_newline(self):
        linked = self.add_worktree(dirname="linked\n")
        rows = wt.inventory(linked)["worktrees"]
        self.assertEqual(rows[0]["path"], str(linked))
        self.assertTrue(rows[0]["isCurrent"])

    def test_unborn_repository_and_bare_repository(self):
        unborn = self.root / "unborn"
        unborn.mkdir()
        self.git("init", "-b", "main", cwd=unborn)
        (unborn / "first.txt").write_text("first\n")
        self.git("add", ".", cwd=unborn)
        self.assertEqual(wt.inventory(unborn), {"worktrees": []})
        bare = self.root / "bare.git"
        self.git("clone", "--bare", str(self.repo), str(bare))
        self.assertEqual(wt.inventory(bare), {"worktrees": []})
        linked = self.root / "bare-linked"
        self.git("worktree", "add", "-b", "bare-feature", str(linked), cwd=bare)
        rows = wt.inventory(bare)["worktrees"]
        self.assertEqual([row["path"] for row in rows], [str(linked)])
        self.assertFalse(rows[0]["bare"])

    def test_unborn_linked_worktree_still_has_summary_and_staged_diff(self):
        self.git("checkout", "--orphan", "unborn-linked")
        row = wt.inventory(self.repo)["worktrees"][0]
        self.assertEqual(row["summary"], "No commits yet.")
        self.assertEqual(row["tracked"], 1)
        self.assertIn("initial", wt.inspect(self.repo, self.repo)["diff"])

    def test_inspection_rejects_unregistered_directory(self):
        with self.assertRaisesRegex(wt.GitError, "registered linked worktree"):
            wt.inspect(self.repo, self.root)

    def test_diff_is_bounded_and_external_diff_is_disabled(self):
        self.git("config", "diff.external", "false")
        (self.repo / "tracked.txt").write_text("long changed line\n" * 5000)
        info = wt.inspect(self.repo, self.repo)
        self.assertTrue(info["truncated"])
        self.assertLess(len(info["diff"]), wt.DIFF_LIMIT * 2 + 50)

    def test_git_environment_does_not_redirect_worktree(self):
        with patch.dict(os.environ, {"GIT_DIR": "/nonexistent", "GIT_WORK_TREE": "/nonexistent"}):
            self.assertEqual(wt.inventory(self.repo)["worktrees"][0]["branch"], "main")

    def test_storage_counts_ignored_files_excludes_git_and_follows_no_links(self):
        content = self.root / "storage"
        content.mkdir()
        file = content / "data"
        file.write_bytes(b"x" * 16384)
        baseline, partial = wt.storage(content)
        self.assertFalse(partial)
        self.assertGreater(baseline, 0)
        os.link(file, content / "hardlink")
        self.assertEqual(wt.storage(content)[0], baseline)
        shared = content / ".git"
        shared.mkdir()
        (shared / "objects").write_bytes(b"x" * 32768)
        self.assertEqual(wt.storage(content)[0], baseline)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "large").write_bytes(b"x" * 65536)
        link = content / "symlink"
        link.symlink_to(outside, target_is_directory=True)
        link_info = link.lstat()
        expected = baseline + (link_info.st_blocks * 512 if hasattr(link_info, "st_blocks") else link_info.st_size)
        self.assertEqual(wt.storage(content)[0], expected)
        (self.repo / ".gitignore").write_text("ignored\n")
        before = wt.inventory(self.repo)["worktrees"][0]["storageBytes"]
        (self.repo / "ignored").write_bytes(b"x" * 65536)
        after = wt.inventory(self.repo)["worktrees"][0]["storageBytes"]
        self.assertGreater(after, before)
        self.assertTrue(wt.storage(content, seconds=0)[1])
        self.assertTrue(wt.storage(content, max_entries=0)[1])

    def test_upstream_and_ahead_behind(self):
        self.git("branch", "base")
        self.git("branch", "--set-upstream-to", "base")
        (self.repo / "tracked.txt").write_text("advance\n")
        self.git("commit", "-am", "Advance main")
        row = wt.inventory(self.repo)["worktrees"][0]
        self.assertEqual((row["upstream"], row["ahead"], row["behind"]), ("base", 1, 0))

    def test_conflicts_and_file_preview_limit(self):
        self.git("checkout", "-b", "other")
        (self.repo / "tracked.txt").write_text("other branch\n")
        self.git("commit", "-am", "Other branch")
        self.git("checkout", "main")
        (self.repo / "tracked.txt").write_text("main branch\n")
        self.git("commit", "-am", "Main branch")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("merge", "other")
        for i in range(wt.FILE_LIMIT + 5):
            (self.repo / f"untracked-{i}").touch()
        info = wt.status(self.repo)
        self.assertEqual(info["conflicts"], 1)
        self.assertEqual(info["untracked"], wt.FILE_LIMIT + 5)
        self.assertEqual(len(info["files"]), wt.FILE_LIMIT)

    def test_unreadable_storage_is_a_partial_lower_bound(self):
        with patch.object(wt.os, "scandir", side_effect=PermissionError):
            self.assertEqual(wt.storage(self.repo), (0, True))

    def test_nonrepo_cli_fails_cleanly(self):
        ran = subprocess.run([sys.executable, "-I", str(SCRIPT), "list", "--cwd", str(self.root)], capture_output=True)
        self.assertEqual(ran.returncode, 1)
        self.assertIn(b"not a git repository", ran.stderr)
        self.assertNotIn(b"Traceback", ran.stderr)


class PullRequests(unittest.TestCase):
    record = {"branch": "feature", "head": "abc"}

    def response(self, prs):
        return subprocess.CompletedProcess([], 0, json.dumps(prs).encode(), b"")

    def pr(self, state="MERGED", **extra):
        return {"state": state, "number": 42, "url": "https://github.com/owner/repo/pull/42",
                "headRefOid": "abc", "isDraft": False, "updatedAt": "2026-11-20",
                "isCrossRepository": False, "headRepositoryOwner": {"login": "owner"}, **extra}

    def test_open_preferred_to_merged_and_forks_excluded(self):
        prs = [self.pr(), self.pr("OPEN", number=43, updatedAt="2026-11-19"),
               self.pr("OPEN", number=44, isCrossRepository=True)]
        with patch.object(wt.subprocess, "run", return_value=self.response(prs)) as run:
            result = wt.pull_request("owner/repo", self.record)
        self.assertEqual((result["state"], result["number"]), ("OPEN", 43))
        self.assertIn("github.com/owner/repo", run.call_args.args[0])
        self.assertIn("feature", run.call_args.args[0])

    def test_merged_draft_no_pr_and_changed_branch(self):
        for prs, state in [([self.pr()], "MERGED"), ([self.pr("OPEN", isDraft=True)], "DRAFT"), ([], "NONE")]:
            with self.subTest(state=state), patch.object(wt.subprocess, "run", return_value=self.response(prs)):
                self.assertEqual(wt.pull_request("owner/repo", self.record)["state"], state)
        with patch.object(wt.subprocess, "run", return_value=self.response([self.pr(headRefOid="old")])):
            self.assertFalse(wt.pull_request("owner/repo", self.record)["headMatches"])

    def test_auth_failure_or_missing_gh_is_unavailable(self):
        with patch.object(wt.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"auth failed")):
            self.assertEqual(wt.pull_request("owner/repo", self.record)["state"], "UNAVAILABLE")
        with patch.object(wt.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(wt.pull_request("owner/repo", self.record)["state"], "UNAVAILABLE")
        self.assertEqual(wt.pull_request(None, self.record)["state"], "UNAVAILABLE")
        self.assertEqual(wt.pull_request("owner/repo", {"detached": True})["state"], "N/A")

    def test_github_remote_formats(self):
        for url in ["git@github.com:owner/repo.git", "https://github.com/owner/repo.git", "ssh://git@github.com/owner/repo", "https://github.com/owner/repo"]:
            with self.subTest(url=url), patch.object(wt, "git", return_value=(url.encode(), False)):
                self.assertEqual(wt.github_repository("."), "owner/repo")
        with patch.object(wt, "git", return_value=(b"https://other.example/owner/repo", False)):
            self.assertIsNone(wt.github_repository("."))


if __name__ == "__main__":
    unittest.main()
