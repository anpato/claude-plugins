#!/usr/bin/env python3
"""Read-only Git worktree inventory and inspection; Python standard library only."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

GIT_TIMEOUT = 5
STATUS_LIMIT = 4 * 1024 * 1024
DIFF_LIMIT = 24_000  # per staged/unstaged section; fits the native UI's text budget
FILE_LIMIT = 150
STORAGE_SECONDS = 1.5
STORAGE_ENTRIES = 100_000


class GitError(Exception):
    pass


def git(cwd, *args, limit=STATUS_LIMIT, allow_truncated=False):
    # An inherited GIT_DIR/WORK_TREE/INDEX_FILE must not redirect another worktree.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    argv = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
            "-c", "core.untrackedCache=false", "-C", os.fspath(cwd), *args]
    # Git can produce a huge diff. Keep process output out of memory and only
    # return a bounded preview. The timeout also bounds slow repositories.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            ran = subprocess.run(argv, stdout=out, stderr=err, env=env, timeout=GIT_TIMEOUT)
        except FileNotFoundError as exc:
            raise GitError("Git is not installed or is not on PATH.") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError("Git timed out. Refresh to try again.") from exc
        except OSError as exc:
            raise GitError(str(exc)) from exc
        if ran.returncode:
            err.seek(0)
            message = err.read(2048).decode("utf-8", "replace").strip()
            raise GitError(message or f"Git exited {ran.returncode}.")
        out.seek(0)
        data = out.read(limit + 1)
    truncated = len(data) > limit
    if truncated and not allow_truncated:
        raise GitError("Git output is too large to inspect safely.")
    return data[:limit], truncated


def parse_worktrees(data):
    """--porcelain -z: each field ends in NUL, empty field ends a record."""
    result, record = [], {}
    for field in data.split(b"\0"):
        if not field:
            if record:
                result.append(record)
                record = {}
            continue
        key, _, value = field.partition(b" ")
        key = key.decode("ascii")
        text = os.fsdecode(value)
        if key == "worktree":
            record["path"] = text
        elif key == "HEAD":
            record["head"] = text
        elif key == "branch":
            record["branch"] = text.removeprefix("refs/heads/")
        elif key in ("bare", "detached"):
            record[key] = True
        elif key in ("locked", "prunable"):
            record[key] = text or key.capitalize()
    return result


def parse_status(data):
    result = {"tracked": 0, "untracked": 0, "conflicts": 0,
              "upstream": None, "ahead": None, "behind": None, "files": []}
    fields = iter(data.split(b"\0"))
    for field in fields:
        if field.startswith(b"# branch.upstream "):
            result["upstream"] = os.fsdecode(field[18:])
        elif field.startswith(b"# branch.ab "):
            ahead, behind = field[12:].split()
            result["ahead"], result["behind"] = int(ahead), abs(int(behind))
        elif field.startswith((b"1 ", b"2 ", b"u ", b"? ")):
            kind = field[:1]
            if kind == b"?":
                code, name = "??", field[2:]
                result["untracked"] += 1
            else:
                # Fixed field counts preserve spaces and newlines in filenames.
                parts = field.split(b" ", {b"1": 8, b"2": 9, b"u": 10}[kind])
                code, name = parts[1].decode("ascii"), parts[-1]
                result["tracked"] += 1
                if kind == b"u":
                    result["conflicts"] += 1
                if kind == b"2":
                    next(fields, None)  # the original path in a rename/copy
            if len(result["files"]) < FILE_LIMIT:
                result["files"].append({"status": code, "path": os.fsdecode(name)})
    return result


def status(path):
    data, _ = git(path, "status", "--porcelain=v2", "--branch", "-z",
                  "--untracked-files=all", "--ignore-submodules=all")
    return parse_status(data)


def storage(path, seconds=STORAGE_SECONDS, max_entries=STORAGE_ENTRIES):
    """Allocated directory bytes; exclude shared Git metadata and external links."""
    total, entries, partial = 0, 0, False
    seen, pending = set(), [os.fspath(path)]
    deadline = time.monotonic() + seconds
    root = os.fspath(path)
    while pending:
        if time.monotonic() >= deadline:
            return total, True
        directory = pending.pop()
        try:
            with os.scandir(directory) as children:
                for child in children:
                    if directory == root and child.name == ".git":
                        continue
                    entries += 1
                    if entries > max_entries or time.monotonic() >= deadline:
                        return total, True
                    try:
                        info = child.stat(follow_symlinks=False)
                    except OSError:
                        partial = True
                        continue
                    inode = (info.st_dev, info.st_ino)
                    if inode in seen:
                        continue
                    seen.add(inode)
                    total += info.st_blocks * 512 if hasattr(info, "st_blocks") else info.st_size
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(child.path)
        except OSError:
            partial = True
    return total, partial


def github_repository(cwd):
    try:
        remote, _ = git(cwd, "remote", "get-url", "origin")
    except GitError:
        return None
    # Explicit host keeps gh's default-repo and GH_HOST settings out of scoping.
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", remote.decode().strip())
    return match[1] if match else None


def pull_request(repo, record):
    empty = {"state": "UNAVAILABLE", "number": None, "url": None, "headMatches": True}
    if not record.get("branch") or record.get("bare"):
        return {**empty, "state": "N/A"}
    if not repo:
        return empty
    try:
        ran = subprocess.run([
            "gh", "pr", "list", "--repo", f"github.com/{repo}", "--head", record["branch"],
            "--state", "all", "--limit", "20", "--json",
            "number,url,state,isDraft,updatedAt,headRefOid,isCrossRepository,headRepositoryOwner",
        ], capture_output=True, timeout=8, env={**os.environ, "GH_PROMPT_DISABLED": "1"})
        if ran.returncode:
            return empty
        # Same-named branches on forks are not this local origin's branch.
        prs = [pr for pr in json.loads(ran.stdout) if not pr.get("isCrossRepository")
               and pr.get("headRepositoryOwner", {}).get("login", "").lower() == repo.split("/")[0].lower()]
        if not prs:
            return {**empty, "state": "NONE"}
        pr = max(prs, key=lambda p: (p["state"] == "OPEN", p.get("updatedAt", "")))
        return {"state": "DRAFT" if pr["state"] == "OPEN" and pr.get("isDraft") else pr["state"],
                "number": pr["number"], "url": pr["url"], "headMatches": pr.get("headRefOid") == record.get("head")}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError, AttributeError):
        return empty


def summarize(record, current, repo=None):
    row = {"path": record["path"], "head": record.get("head", ""),
           "branch": record.get("branch"), "bare": record.get("bare", False),
           "detached": record.get("detached", False),
           "locked": record.get("locked"), "prunable": record.get("prunable"),
           "isCurrent": Path(record["path"]).resolve() == current,
           "tracked": None, "untracked": None, "conflicts": 0,
           "upstream": None, "ahead": None, "behind": None, "error": None,
           "summary": "No commits yet.", "storageBytes": None, "storagePartial": False,
           "pullRequest": pull_request(repo, record)}
    if record.get("head") and set(record["head"]) != {"0"}:
        try:
            subject, _ = git(current, "log", "-1", "--no-show-signature", "--format=%s",
                             record["head"], "--", limit=2048, allow_truncated=True)
            row["summary"] = "Latest: " + subject.decode("utf-8", "replace").strip()[:240]
        except GitError:
            row["summary"] = "Latest commit summary unavailable."
    if row["bare"]:
        return row
    if not Path(row["path"]).is_dir():
        row["error"] = "Worktree directory is missing."
        return row
    row["storageBytes"], row["storagePartial"] = storage(row["path"])
    try:
        info = status(row["path"])
        row.update({key: value for key, value in info.items() if key != "files"})
    except GitError as exc:
        row["error"] = str(exc)
    return row


def inventory(cwd):
    data, _ = git(cwd, "worktree", "list", "--porcelain", "-z")
    records = parse_worktrees(data)
    # Bare repos have no toplevel; a session opened there still has a current row.
    try:
        root, _ = git(cwd, "rev-parse", "--show-toplevel")
        current = Path(os.fsdecode(root.removesuffix(b"\n"))).resolve()
    except GitError:
        current = Path(cwd).resolve()
    repo = github_repository(cwd)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda record: summarize(record, current, repo), records))
    rows.sort(key=lambda row: not row["isCurrent"])  # preserve Git's order otherwise
    return {"worktrees": rows}


def inspect(cwd, path):
    # Re-read membership: stale UI state cannot inspect an unrelated repository.
    data, _ = git(cwd, "worktree", "list", "--porcelain", "-z")
    target = Path(path).resolve()
    record = next((r for r in parse_worktrees(data) if Path(r["path"]).resolve() == target), None)
    if record is None:
        raise GitError("This worktree is no longer registered. Refresh the list.")
    if record.get("bare"):
        return {"files": [], "totalFiles": 0, "diff": "", "truncated": False,
                "note": "Bare repository: no working files to inspect."}
    info = status(record["path"])
    sections, truncated = [], False
    for label, extra in (("Staged", ["--cached"]), ("Unstaged", [])):
        data, cut = git(record["path"], "diff", "--no-ext-diff", "--no-textconv",
                        "--no-color", "--ignore-submodules=all", *extra, "--",
                        limit=DIFF_LIMIT, allow_truncated=True)
        if data:
            sections.append(f"{label}\n{data.decode('utf-8', 'replace')}")
        truncated |= cut
    return {"files": info["files"], "totalFiles": info["tracked"] + info["untracked"],
            "diff": "\n".join(sections), "truncated": truncated,
            "note": "Untracked file contents and submodule changes are not included in the diff."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "inspect"])
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--path")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect" and args.path is None:
            parser.error("inspect requires --path")
        value = inventory(args.cwd) if args.command == "list" else inspect(args.cwd, args.path)
        print(json.dumps(value))
        return 0
    except GitError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
