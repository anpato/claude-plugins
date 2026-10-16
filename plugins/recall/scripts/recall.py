#!/usr/bin/env python3
"""Find past Claude Code sessions in any project and print a digest of one.

Read-only. Scans ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl (top level
only; subagent transcripts live deeper and are skipped).

  recall.py list   [--project SUBSTR] [--limit N] [--include-tmp]
  recall.py search QUERY [--project SUBSTR] [--limit N] [--include-tmp]
  recall.py digest ID_OR_PREFIX [--max-chars N]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HEAD_BYTES = 8 * 1024
TAIL_BYTES = 64 * 1024
TMP_MARKERS = ("/private/tmp/", "/private/var/folders/", "/scratch-workspaces/")
NOISE_PREFIXES = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<task-notification>",
    "<system-reminder>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "Caveat:",
)
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>.*?(?:<command-args>(.*?)</command-args>)?", re.S)
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
UUID_RE = re.compile(r"^[0-9a-f-]{4,36}$", re.I)


def projects_root():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return Path(base) / "projects"


def session_files(root):
    if not root.is_dir():
        return []
    return [p for p in root.glob("*/*.jsonl") if p.is_file()]


def parse_lines(chunk, drop_first=False, drop_last=False):
    """Parse JSONL records from a byte chunk, skipping malformed lines."""
    lines = chunk.split(b"\n")
    if drop_first and lines:
        lines = lines[1:]
    if drop_last and lines:
        lines = lines[:-1]
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def iter_records(path):
    """Stream every well-formed record, skipping malformed lines (e.g. a live session mid-write)."""
    with open(path, "rb") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                yield rec


def text_of(content):
    """Plain text from a message's content (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def user_prompt(rec):
    """The human-typed prompt in a user record, or None for tool results, meta and noise."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isSidechain"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = text_of(content).strip()
    if not text:
        return None
    m = COMMAND_RE.search(text)
    if m and text.lstrip().startswith(("<command-name>", "<command-message>")):
        args = (m.group(2) or "").strip()
        return (m.group(1).strip() + (" " + args if args else "")).strip()
    if text.startswith(NOISE_PREFIXES):
        return None
    return text


def one_line(s, limit):
    s = " ".join((s or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def age(dt):
    if dt is None:
        return "?"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


def summarize(path):
    """Cheap metadata for one session from the head and tail of its file."""
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(HEAD_BYTES)
        if size > HEAD_BYTES + TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            tail = fh.read()
            records = parse_lines(head, drop_last=True) + parse_lines(tail, drop_first=True)
        else:
            records = parse_lines(head + fh.read())
    info = {
        "id": path.stem,
        "path": str(path),
        "size": size,
        "cwd": None,
        "branch": None,
        "first_ts": None,
        "last_ts": None,
        "title": None,
        "ai_title": None,
        "last_prompt": None,
        "first_prompt": None,
        "prs": [],
    }
    for rec in records:
        t = rec.get("type")
        if rec.get("cwd") and not info["cwd"]:
            info["cwd"] = rec["cwd"]
        if rec.get("gitBranch"):
            info["branch"] = rec["gitBranch"]
        ts = parse_ts(rec.get("timestamp"))
        if ts:
            info["first_ts"] = info["first_ts"] or ts
            info["last_ts"] = ts
        if t == "custom-title" and rec.get("customTitle"):
            info["title"] = rec["customTitle"]
        elif t == "ai-title" and rec.get("aiTitle"):
            info["ai_title"] = rec["aiTitle"]
        elif t == "last-prompt" and rec.get("lastPrompt"):
            # Shell escapes ("! cmd") make poor titles and may carry secrets.
            if not rec["lastPrompt"].startswith("!"):
                info["last_prompt"] = rec["lastPrompt"]
        elif t == "pr-link" and rec.get("prUrl") and rec["prUrl"] not in info["prs"]:
            info["prs"].append(rec["prUrl"])
        elif info["first_prompt"] is None:
            prompt = user_prompt(rec)
            if prompt and not prompt.startswith("!"):
                info["first_prompt"] = prompt
    info["title"] = info["title"] or info["ai_title"] or one_line(info["first_prompt"] or info["last_prompt"] or "(untitled)", 60)
    if info["last_ts"] is None:
        info["last_ts"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return info


def is_tmp(cwd):
    return bool(cwd) and any(m in cwd + "/" for m in TMP_MARKERS)


def collect(args):
    current = getattr(args, "exclude", None) or os.environ.get("CLAUDE_CODE_SESSION_ID")
    sessions = []
    for path in session_files(projects_root()):
        if path.stem == current:
            continue
        try:
            info = summarize(path)
        except OSError:
            continue
        if not getattr(args, "include_tmp", False) and is_tmp(info["cwd"]):
            continue
        if args.project and args.project.lower() not in (info["cwd"] or path.parent.name).lower():
            continue
        sessions.append(info)
    sessions.sort(key=lambda s: s["last_ts"], reverse=True)
    return sessions


def short_cwd(cwd):
    if not cwd:
        return "?"
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd.startswith(home) else cwd


def print_row(n, s, extra=None):
    branch = f" [{s['branch']}]" if s["branch"] and s["branch"] != "HEAD" else ""
    print(f"{n}. {one_line(s['title'], 70)}")
    print(f"   {short_cwd(s['cwd'])}{branch} · {age(s['last_ts'])} · {s['id']}")
    if s["last_prompt"]:
        print(f"   last: {one_line(s['last_prompt'], 110)}")
    for line in extra or []:
        print(f"   > {line}")


def as_json(s, hits=None, snippets=None):
    row = {
        "id": s["id"],
        "title": s["title"],
        "cwd": s["cwd"],
        "project": short_cwd(s["cwd"]),
        "branch": s["branch"] if s["branch"] != "HEAD" else None,
        "lastActive": s["last_ts"].isoformat(),
        "age": age(s["last_ts"]),
        "lastPrompt": s["last_prompt"],
        "prs": s["prs"],
    }
    if hits is not None:
        row["hits"] = hits
        row["snippets"] = snippets
    return row


def cmd_list(args):
    sessions = collect(args)[: args.limit]
    if args.json:
        print(json.dumps([as_json(s) for s in sessions]))
        return
    if not sessions:
        print("No sessions found.")
        return
    for n, s in enumerate(sessions, 1):
        print_row(n, s)


def search_text_records(path, needle):
    """Count case-insensitive hits in user prompts and assistant text; return (hits, snippets)."""
    hits, snippets = 0, []
    with open(path, "rb") as fh:
        for raw in fh:
            if needle.encode() not in raw.lower():
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("isSidechain"):
                continue
            if rec.get("type") == "user":
                text = user_prompt(rec) or ""
            elif rec.get("type") == "assistant":
                text = text_of((rec.get("message") or {}).get("content"))
            else:
                continue
            low = text.lower()
            i = low.find(needle)
            if i < 0:
                continue
            hits += low.count(needle)
            if len(snippets) < 2:
                lo, hi = max(0, i - 120), min(len(text), i + len(needle) + 120)
                snippets.append(("…" if lo else "") + one_line(text[lo:hi], 260) + ("…" if hi < len(text) else ""))
    return hits, snippets


def rg_candidates(paths, needle):
    """Files containing the needle, via ripgrep when available; None means 'scan everything'."""
    rg = shutil.which("rg")
    if not rg or not paths:
        return None
    try:
        out = subprocess.run(
            [rg, "-l", "-i", "-F", "--no-messages", "--", needle, *paths],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode not in (0, 1):
        return None
    return set(out.stdout.splitlines())


def cmd_search(args):
    needle = args.query.lower()
    sessions = collect(args)
    ranked = []
    meta_hits = []
    for s in sessions:
        meta = " ".join(filter(None, [s["title"], s["ai_title"], s["last_prompt"], s["cwd"], s["branch"]])).lower()
        if needle in meta:
            meta_hits.append(s["id"])
    candidates = rg_candidates([s["path"] for s in sessions], needle)
    for s in sessions:
        if candidates is not None and s["path"] not in candidates and s["id"] not in meta_hits:
            continue
        hits, snippets = search_text_records(s["path"], needle)
        bonus = 1000 if s["id"] in meta_hits else 0
        if hits or bonus:
            ranked.append((bonus + hits, hits, s, snippets))
    ranked.sort(key=lambda r: (r[0], r[2]["last_ts"]), reverse=True)
    if args.json:
        print(json.dumps([as_json(s, hits, snippets) for _, hits, s, snippets in ranked[: args.limit]]))
        return
    if not ranked:
        print(f"No sessions mention {args.query!r}.")
        return
    for n, (_, hits, s, snippets) in enumerate(ranked[: args.limit], 1):
        s = dict(s, title=f"{s['title']}  ({hits} hits)")
        print_row(n, s, snippets)


def resolve(prefix):
    prefix = prefix.lower().strip()
    matches = [p for p in session_files(projects_root()) if p.stem.lower().startswith(prefix)]
    return matches


def clip(s, limit):
    s = (s or "").strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def build_digest(path, max_chars):
    info = summarize(path)
    turns = []  # [prompt, last assistant text after it]
    preamble_answer = None
    compact = None
    files, bash_count, models = [], 0, set()
    for rec in iter_records(path):
        if rec.get("isSidechain"):
            continue
        t = rec.get("type")
        if t == "user":
            if rec.get("isCompactSummary"):
                compact = text_of((rec.get("message") or {}).get("content"))
                continue
            prompt = user_prompt(rec)
            if prompt:
                turns.append([prompt, None])
        elif t == "assistant":
            msg = rec.get("message") or {}
            if msg.get("model") and not msg["model"].startswith("<"):
                models.add(msg["model"])
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    if turns:
                        turns[-1][1] = b["text"]
                    else:
                        preamble_answer = b["text"]
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    if b.get("name") in EDIT_TOOLS:
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp and fp not in files:
                            files.append(fp)
                    elif b.get("name") == "Bash":
                        bash_count += 1

    head = [
        f"# Session digest: {info['title']}",
        f"- id: {info['id']}",
        f"- project: {info['cwd'] or '?'}" + (f" (branch {info['branch']})" if info["branch"] else ""),
        f"- when: {info['first_ts']:%Y-%m-%d %H:%M} → {info['last_ts']:%Y-%m-%d %H:%M} UTC ({age(info['last_ts'])})"
        if info["first_ts"] else f"- last active: {age(info['last_ts'])}",
        f"- turns: {len(turns)} prompts · {len(files)} files edited · {bash_count} shell commands"
        + (f" · models: {', '.join(sorted(models))}" if models else ""),
    ]
    if info["prs"]:
        head.append("- PRs: " + ", ".join(info["prs"]))
    head.append(f"- resume it instead: cd {json.dumps(info['cwd'] or '.')} && claude --resume {info['id']}")

    tail = []
    if files:
        shown = files[:40]
        tail.append("\n## Files edited")
        tail += [f"- {f}" for f in shown]
        if len(files) > len(shown):
            tail.append(f"- … and {len(files) - len(shown)} more")

    budget = max_chars - sum(len(x) + 1 for x in head + tail)
    sections = []
    if compact:
        # Demote the summary's own headings so they nest under this section.
        c = re.sub(r"(?m)^(#{1,4}) ", r"###\1 ", clip(compact, max(2000, budget // 3)))
        sections.append("\n## Compacted summary (latest)\n" + c)
        budget -= len(sections[-1])
    if preamble_answer and not turns:
        turns = [["(no typed prompt)", preamble_answer]]

    rendered = []
    for i, (prompt, answer) in enumerate(turns):
        last = i == len(turns) - 1
        block = f"\n### Prompt {i + 1}\n{clip(prompt, 500)}"
        if answer:
            block += f"\n**Claude:** {clip(answer, 2500 if last else 600)}"
        rendered.append(block)
    # Keep turns from both ends (newest first) until the budget runs out; drop a contiguous middle.
    budget -= 60
    front, back = 0, len(rendered)
    used = 0
    take_back = True
    while front < back:
        idx = back - 1 if take_back else front
        if used + len(rendered[idx]) > budget and (front > 0 or back < len(rendered)):
            break
        used += len(rendered[idx])
        if take_back:
            back -= 1
        else:
            front += 1
        take_back = not take_back
    if front < back:
        span = f"prompt {back}" if back - front == 1 else f"prompts {front + 1}–{back}"
        rendered = rendered[:front] + [f"\n[… {span} omitted to fit the budget …]"] + rendered[back:]
    if rendered:
        sections.append("\n## Conversation (prompts and Claude's last reply to each)" + "".join(rendered))
    out = "\n".join(head) + "\n" + "\n".join(sections) + "\n" + "\n".join(tail)
    return clip(out, max_chars)


def cmd_digest(args):
    matches = resolve(args.id)
    if not matches:
        print(f"No session id starts with {args.id!r}.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"{len(matches)} sessions match {args.id!r}; use a longer prefix:")
        for n, p in enumerate(matches[:10], 1):
            print_row(n, summarize(p))
        sys.exit(2)
    print(build_digest(matches[0], args.max_chars))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, limit):
        p.add_argument("--project", help="substring of the session's working directory")
        p.add_argument("--limit", type=int, default=limit)
        p.add_argument("--include-tmp", action="store_true", help="include sessions from temp/scratch dirs")
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--exclude", help="session id to leave out (default: $CLAUDE_CODE_SESSION_ID)")

    common(sub.add_parser("list", help="recent sessions across all projects"), 20)
    sp = sub.add_parser("search", help="full-text search of prompts and replies")
    sp.add_argument("query")
    common(sp, 10)
    dp = sub.add_parser("digest", help="condensed transcript of one session")
    dp.add_argument("id", help="session id or unique prefix")
    dp.add_argument("--max-chars", type=int, default=15000)

    args = ap.parse_args(argv)
    {"list": cmd_list, "search": cmd_search, "digest": cmd_digest}[args.cmd](args)


if __name__ == "__main__":
    main()
