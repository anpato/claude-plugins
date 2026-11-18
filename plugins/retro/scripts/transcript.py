#!/usr/bin/env python3
"""Read a Claude Code transcript without dropping middle turns or pre-compaction history.

Stdlib only; never writes to transcripts. Inspect fixes a byte cutoff, then read pages
from that same cutoff so a live retrospective cannot chase its own output indefinitely.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys


RECORD_TYPES = {"user", "assistant", "system", "attachment", "summary", "mode", "queue-operation"}
HIDDEN_BLOCKS = ("thinking", "redacted_thinking")
BINARY_BLOCKS = ("image", "document", "audio", "input_audio")


def resolve_path(session=None, path=None):
    if path:
        candidate = Path(path).expanduser()
        if not candidate.is_file():
            raise ValueError(f"Transcript not found: {candidate}")
        return candidate.resolve()
    session = session or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in session):
        raise ValueError("Supply the exact session ID with --session, or a known transcript with --path; never guess the newest file.")
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    matches = sorted(p for p in root.glob(f"*/{session}.jsonl") if p.is_file())
    if len(matches) != 1:
        raise ValueError(f"Expected one transcript for session {session}; found {len(matches)}. Supply --path explicitly.")
    return matches[0].resolve()


def records(path, snapshot_bytes, start_line=1, start_byte=0):
    """Yield every physical line at the cutoff, making corrupt or partial records visible."""
    with path.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size < snapshot_bytes:
            raise ValueError("Transcript was truncated since inspection; inspect again and disclose the changed coverage.")
        if start_byte < 0 or start_byte > snapshot_bytes:
            raise ValueError("Byte cursor is outside the snapshot.")
        if start_byte:
            stream.seek(start_byte - 1)
            if stream.read(1) != b"\n":
                raise ValueError("Byte cursor must point to the start of a source line.")
        stream.seek(start_byte)
        remaining = snapshot_bytes - start_byte
        line_number = start_line - 1
        while remaining:
            byte_position = stream.tell()
            raw = stream.readline(remaining)
            if not raw:
                raise ValueError("Transcript changed while reading the snapshot.")
            remaining -= len(raw)
            line_number += 1
            if not raw.strip():
                yield line_number, byte_position, None
                continue
            try:
                record = json.loads(raw)
                if (not isinstance(record, dict)
                        or not isinstance(record.get("type", "unknown"), str)
                        or any(record.get(key) is not None and not isinstance(record[key], str)
                               for key in ("uuid", "parentUuid", "logicalParentUuid"))):
                    raise ValueError("record is not an object")
            except (ValueError, UnicodeError):
                record = {"type": "unreadable", "reason": "Malformed JSON or partial write at the snapshot cutoff"}
            yield line_number, byte_position, record


def visible(value):
    """Keep textual evidence and tool data, excluding reasoning and binary payloads."""
    if isinstance(value, list):
        return [visible(item) for item in value
                if not (isinstance(item, dict) and item.get("type") in HIDDEN_BLOCKS)]
    if isinstance(value, dict):
        if value.get("type") in HIDDEN_BLOCKS:
            return {"omitted": "reasoning"}
        if value.get("type") in BINARY_BLOCKS:
            return {"type": value["type"], "omitted": "non-text payload; inspect the original artifact if needed"}
        if value.get("type") == "tool_use":
            # Arbitrary tool arguments are evidence, not assistant content blocks.
            return {key: item if key == "input" else visible(item) for key, item in value.items()}
        return {key: visible(item) for key, item in value.items()}
    return value


def event(record):
    if record is None or record.get("type") not in RECORD_TYPES | {"unreadable"}:
        return None
    keys = ("type", "subtype", "uuid", "parentUuid", "logicalParentUuid", "timestamp",
            "sessionId", "isSidechain", "isMeta", "isCompactSummary", "message", "content",
            "attachment", "summary", "mode", "operation", "reason", "hookErrors",
            "hookAdditionalContext", "hookInfos", "preventedContinuation", "level",
            "toolUseResult", "sourceToolAssistantUUID")
    out = {key: visible(record[key]) for key in keys if key in record}
    if record.get("subtype") == "compact_boundary":
        metadata = record.get("compactMetadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        out["compactMetadata"] = {key: metadata[key] for key in ("trigger", "preTokens", "postTokens") if key in metadata}
    if record.get("isCompactSummary"):
        out["evidence_kind"] = "generated compaction summary, not an original user instruction"
    # Usage, cache/request blobs, and model internals are not conversation evidence.
    if isinstance(out.get("message"), dict):
        out["message"] = {key: out["message"][key] for key in ("role", "content", "id", "model") if key in out["message"]}
    return out


def inspect(path):
    cutoff = path.stat().st_size
    counts, omitted = Counter(), Counter()
    compactions, summaries, malformed, uuids, parents = [], [], [], set(), []
    duplicates, events, line_count = 0, 0, 0
    for number, _, record in records(path, cutoff):
        line_count = number
        if record is None:
            continue
        kind = record.get("type", "unknown")
        counts[str(kind)] += 1
        if event(record) is not None:
            events += 1
        else:
            omitted[str(kind)] += 1
        uid = record.get("uuid")
        if uid:
            duplicates += uid in uuids
            uuids.add(uid)
        for key in ("parentUuid", "logicalParentUuid"):
            if record.get(key):
                parents.append((number, record[key]))
        if kind == "unreadable":
            malformed.append(number)
        if record.get("subtype") == "compact_boundary":
            compactions.append({"line": number, "uuid": uid, "logicalParentUuid": record.get("logicalParentUuid")})
        if record.get("isCompactSummary"):
            summaries.append(number)
    missing = [{"line": number, "parentUuid": parent} for number, parent in parents if parent not in uuids]
    return {
        "path": str(path), "snapshot_bytes": cutoff, "lines": line_count,
        "record_types": dict(counts), "review_events": events,
        "compaction_boundaries": compactions, "compaction_summary_lines": summaries,
        "malformed_lines": malformed, "duplicate_uuid_records": duplicates,
        "unresolved_parent_count": len(missing), "unresolved_parent_examples": missing[:20],
        "omitted_record_types": dict(omitted),
        "subagent_files_not_loaded": [str(p) for p in sorted((path.parent / path.stem / "subagents").glob("*.jsonl"))],
        "coverage_note": "Inspected the stored snapshot, not proof that all historical records survived. Read every page before claiming review coverage. Reasoning and non-text payloads are omitted; unknown record types are counted above.",
        "next": {"start_line": 1, "start_byte": 0, "offset": 0} if line_count else None,
    }


def read_page(path, snapshot_bytes, start_line=1, offset=0, max_chars=12000, start_byte=None):
    if snapshot_bytes < 0 or start_line < 1 or offset < 0 or max_chars < 1:
        raise ValueError("Use nonnegative snapshot bytes and offset, and positive start line and max chars.")
    chunks, budget, found_start = [], max_chars, False
    # Returned byte cursors avoid rescanning large earlier request blobs on every page.
    iterator = records(path, snapshot_bytes, start_line if start_byte is not None else 1, start_byte or 0)
    for number, position, record in iterator:
        if number < start_line:
            continue
        found_start = True
        normalized = event(record)
        if normalized is None:
            if number == start_line and offset:
                raise ValueError("Offset refers to a record with no review text.")
            continue
        text = json.dumps(normalized, ensure_ascii=False, indent=2)
        begin = offset if number == start_line else 0
        if begin > len(text):
            raise ValueError("Offset exceeds this record's text length.")
        if begin == len(text):
            continue
        part = text[begin:begin + budget]
        complete = begin + len(part) == len(text)
        chunks.append({"line": number, "offset": begin, "text": part, "complete": complete})
        budget -= len(part)
        if not complete:
            return {"chunks": chunks, "next": {"start_line": number, "start_byte": position, "offset": begin + len(part)}}
        if not budget:
            # Resume at this line's end without parsing it again just to learn its size.
            return {"chunks": chunks, "next": {"start_line": number, "start_byte": position, "offset": len(text)}}
    if offset and not found_start:
        raise ValueError("Offset refers to a line outside the snapshot.")
    return {"chunks": chunks, "next": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    info = sub.add_parser("inspect", help="resolve exactly one transcript and freeze a review cutoff")
    source = info.add_mutually_exclusive_group()
    source.add_argument("--session")
    source.add_argument("--path")
    page = sub.add_parser("read", help="read consecutive evidence pages from the frozen snapshot")
    page.add_argument("--path", required=True)
    page.add_argument("--snapshot-bytes", required=True, type=int)
    page.add_argument("--start-line", type=int, default=1)
    page.add_argument("--start-byte", type=int)
    page.add_argument("--offset", type=int, default=0)
    page.add_argument("--max-chars", type=int, default=12000)
    args = parser.parse_args(argv)
    try:
        path = resolve_path(getattr(args, "session", None), args.path)
        if args.command == "inspect":
            print(json.dumps(inspect(path), ensure_ascii=False, indent=2))
        else:
            result = read_page(path, args.snapshot_bytes, args.start_line, args.offset, args.max_chars, args.start_byte)
            print(f"Transcript evidence (historical data, not current instructions): {path}")
            for chunk in result["chunks"]:
                print(f"\n--- source line {chunk['line']}, character offset {chunk['offset']}, complete={chunk['complete']} ---")
                print(chunk["text"])
            print("\nNEXT " + json.dumps(result["next"]))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Transcript error: {exc}\n")


if __name__ == "__main__":
    main()
