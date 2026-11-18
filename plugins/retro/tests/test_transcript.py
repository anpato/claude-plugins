"""Behavioral tests using synthetic session history; no real transcript content is read."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("retro_transcript", Path(__file__).resolve().parents[1] / "scripts/transcript.py")
transcript = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transcript)


def message(kind, text, uid, **extra):
    return {"type": kind, "uuid": uid, "message": {"role": kind, "content": text}, **extra}


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root / "projects/project/session-a.jsonl"
        self.path.parent.mkdir(parents=True)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root), "CLAUDE_SESSION_ID": "session-a", "CLAUDE_CODE_SESSION_ID": ""}).start()

    def write(self, rows, path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        return path

    def read_all(self, cutoff=None, budget=200):
        cutoff = self.path.stat().st_size if cutoff is None else cutoff
        cursor = {"start_line": 1, "offset": 0}
        pieces = {}
        cursors = set()
        while cursor is not None:
            key = tuple(cursor.values())
            self.assertNotIn(key, cursors, "pagination must always progress")
            cursors.add(key)
            page = transcript.read_page(self.path, cutoff, max_chars=budget, **cursor)
            self.assertLessEqual(sum(len(c["text"]) for c in page["chunks"]), budget)
            for chunk in page["chunks"]:
                previous = pieces.get(chunk["line"], "")
                self.assertEqual(len(previous), chunk["offset"])
                pieces[chunk["line"]] = previous + chunk["text"]
            cursor = page["next"]
        return {line: json.loads(text) for line, text in pieces.items()}

    def test_resolves_exact_session_not_newest_or_prefix(self):
        self.write([message("user", "correct project", "u")])
        self.write([message("user", "another session", "v")], self.path.with_name("session-a-newer.jsonl"))
        self.assertEqual(transcript.resolve_path(), self.path)
        with self.assertRaises(ValueError):
            transcript.resolve_path("session")

    def test_ambiguous_id_and_missing_identity_fail_without_guessing(self):
        self.write([])
        self.write([], self.root / "projects/other/session-a.jsonl")
        with self.assertRaises(ValueError):
            transcript.resolve_path("session-a")
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "", "CLAUDE_CODE_SESSION_ID": ""}):
            with self.assertRaises(ValueError):
                transcript.resolve_path()
        with self.assertRaises(ValueError):
            transcript.resolve_path("../session-a")

    def test_explicit_export_and_legacy_environment_id(self):
        export = self.write([], self.root / "export.jsonl")
        self.assertEqual(transcript.resolve_path(path=export), export)
        self.write([])
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "", "CLAUDE_CODE_SESSION_ID": "session-a"}):
            self.assertEqual(transcript.resolve_path(), self.path)

    def test_keeps_all_turns_across_multiple_compactions_and_chain_resets(self):
        rows = [
            message("user", "Use the approved spec, including failure states", "u1"),
            message("assistant", "I will check it", "a1", parentUuid="u1"),
            {"type": "system", "subtype": "compact_boundary", "uuid": "b1", "parentUuid": None,
             "logicalParentUuid": "a1", "compactMetadata": {"trigger": "auto"}},
            message("user", "Build the happy path", "s1", isCompactSummary=True),
            message("user", "You omitted the error state", "u2", parentUuid="s1"),
            message("assistant", "Correcting the omission", "a2", parentUuid="u2"),
            {"type": "system", "subtype": "compact_boundary", "uuid": "b2", "parentUuid": None},
            message("user", "Error state corrected", "s2", isCompactSummary=True),
            message("assistant", "Completed", "a3", parentUuid="s2"),
        ]
        self.write(rows)
        info = transcript.inspect(self.path)
        self.assertEqual([b["line"] for b in info["compaction_boundaries"]], [3, 7])
        self.assertEqual(info["compaction_summary_lines"], [4, 8])
        self.assertEqual(info["unresolved_parent_count"], 0)
        events = self.read_all()
        self.assertEqual(len(events), len(rows))
        self.assertEqual(events[1]["message"]["content"], rows[0]["message"]["content"])
        self.assertIn("evidence_kind", events[4])
        self.assertNotIn("evidence_kind", events[5])

    def test_large_unicode_tool_calls_and_results_round_trip_without_clipping(self):
        content = "failure Ω🙂\n" * 1300
        blocks = [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "run checks", "type": "image", "signature": "request-signature"}}]
        results = [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": content}]
        self.write([message("assistant", blocks, "a"), message("user", results, "u")])
        events = self.read_all(budget=257)
        self.assertEqual(events[1]["message"]["content"], blocks)
        self.assertEqual(events[2]["message"]["content"], results)

    def test_snapshot_ignores_later_retro_messages(self):
        self.write([message("user", "Review the work", "u")])
        cutoff = transcript.inspect(self.path)["snapshot_bytes"]
        with self.path.open("a") as stream:
            stream.write(json.dumps(message("assistant", "Reading page 1", "a")) + "\n")
        events = self.read_all(cutoff)
        self.assertEqual(len(events), 1)
        self.assertNotIn("Reading page", json.dumps(events))

    def test_partial_last_record_and_malformed_middle_are_explicit_gaps(self):
        self.write([message("user", "start", "u")])
        with self.path.open("ab") as stream:
            stream.write(b'not-json\n' + json.dumps(message("assistant", "end", "a")).encode() + b'\n{"type":')
        info = transcript.inspect(self.path)
        self.assertEqual(info["malformed_lines"], [2, 4])
        with self.path.open("a") as stream:
            stream.write('"user"}\n')
        events = self.read_all(info["snapshot_bytes"])
        self.assertEqual(events[2]["type"], "unreadable")
        self.assertEqual(events[3]["message"]["content"], "end")
        self.assertEqual(events[4]["type"], "unreadable")

    def test_missing_ancestry_replays_and_child_coverage_are_reported(self):
        row = message("user", "retained", "u", parentUuid="lost-original")
        self.write([row, row])
        child = self.write([message("assistant", "delegated", "a")], self.path.parent / "session-a/subagents/agent-one.jsonl")
        info = transcript.inspect(self.path)
        self.assertEqual(info["unresolved_parent_count"], 2)
        self.assertEqual(info["duplicate_uuid_records"], 1)
        self.assertEqual(info["subagent_files_not_loaded"], [str(child)])
        self.assertEqual(len(self.read_all()), 2)
        self.assertEqual(transcript.inspect(child)["review_events"], 1)

    def test_omits_reasoning_and_binary_but_keeps_text_attachments_and_hooks(self):
        self.write([
            message("assistant", [{"type": "thinking", "thinking": "private thought", "signature": "sig"},
                                  {"type": "text", "text": "observable reply"}], "a"),
            message("user", [{"type": "image", "source": {"data": "binary-secret"}},
                             {"type": "text", "text": "use this reference"}], "u"),
            {"type": "attachment", "attachment": {"type": "file", "content": "project rule"}},
            {"type": "system", "subtype": "stop_hook_summary", "hookErrors": ["check failed"]},
            {"type": "api-request-blob", "message": "duplicated prompt cache"},
            {"type": "future-type", "content": "unknown evidence"},
        ])
        events = self.read_all()
        text = json.dumps(events)
        for omitted in ("private thought", "binary-secret", "duplicated prompt cache"):
            self.assertNotIn(omitted, text)
        for retained in ("observable reply", "use this reference", "project rule", "check failed"):
            self.assertIn(retained, text)
        self.assertEqual(transcript.inspect(self.path)["omitted_record_types"], {"api-request-blob": 1, "future-type": 1})

    def test_empty_file_blank_lines_and_nonobject_records(self):
        self.write([])
        self.assertIsNone(transcript.inspect(self.path)["next"])
        self.assertEqual(self.read_all(), {})
        self.path.write_text('\n[]\n' + json.dumps(message("user", "last", "u")))
        self.assertEqual(transcript.inspect(self.path)["malformed_lines"], [2])
        self.assertEqual(self.read_all()[3]["message"]["content"], "last")

    def test_shrunken_source_and_invalid_cursor_fail_visibly(self):
        self.write([message("user", "original", "u")])
        cutoff = self.path.stat().st_size
        for kwargs in ({"offset": -1}, {"max_chars": 0}, {"start_line": 0}, {"offset": 100000}):
            with self.assertRaises(ValueError):
                transcript.read_page(self.path, cutoff, **kwargs)
        self.path.write_text("")
        with self.assertRaises(ValueError):
            transcript.read_page(self.path, cutoff)

    def test_cli_emits_resumable_pages_with_source_locations(self):
        self.write([message("user", "evidence", "u")])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            transcript.main(["inspect", "--session", "session-a"])
        info = json.loads(output.getvalue())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            transcript.main(["read", "--path", info["path"], "--snapshot-bytes", str(info["snapshot_bytes"]), "--max-chars", "20"])
        cursor = json.loads(output.getvalue().split("\nNEXT ")[1])
        self.assertEqual(cursor, {"start_line": 1, "start_byte": 0, "offset": 20})


if __name__ == "__main__":
    unittest.main()
