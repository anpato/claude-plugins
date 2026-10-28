"""Tests for scripts/recall.py against synthetic transcripts.

Run: python3 -I -m unittest discover -s plugins/recall/tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import recall  # noqa: E402

SID_A = "aaaaaaaa-1111-4111-8111-111111111111"
SID_B = "bbbbbbbb-2222-4222-8222-222222222222"
SID_CUR = "cccccccc-3333-4333-8333-333333333333"


def user(text, ts, cwd, **extra):
    return {"type": "user", "uuid": ts, "timestamp": ts, "cwd": cwd, "gitBranch": "main",
            "isSidechain": False, "message": {"role": "user", "content": text}, **extra}


def assistant(blocks, ts, cwd):
    return {"type": "assistant", "uuid": ts + "a", "timestamp": ts, "cwd": cwd, "isSidechain": False,
            "message": {"role": "assistant", "model": "claude-opus-5-5", "content": blocks}}


def tool_result(ts, cwd):
    return user([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], ts, cwd)


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        os.environ["CLAUDE_CONFIG_DIR"] = str(root)
        os.environ["CLAUDE_CODE_SESSION_ID"] = SID_CUR
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)

        # A cwd with a dash: the dir name alone can't tell it apart from a nested path.
        cwd_a = "/work/my-app"
        self.write(root / "projects/-work-my-app" / f"{SID_A}.jsonl", [
            {"type": "last-prompt", "lastPrompt": "wire up the webhook retries", "sessionId": SID_A},
            {"type": "mode", "mode": "normal", "sessionId": SID_A},
            user("<local-command-caveat>Caveat: ignore</local-command-caveat>", "2026-10-01T10:00:00Z", cwd_a, isMeta=True),
            user("<command-name>/plan</command-name>\n<command-message>plan</command-message>\n<command-args></command-args>",
                 "2026-10-01T10:00:01Z", cwd_a),
            user("Add retries to the webhook sender", "2026-10-01T10:00:02Z", cwd_a),
            assistant([{"type": "thinking", "thinking": "secret thoughts"}], "2026-10-01T10:00:03Z", cwd_a),
            assistant([{"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/work/my-app/send.py"}}],
                      "2026-10-01T10:00:04Z", cwd_a),
            tool_result("2026-10-01T10:00:05Z", cwd_a),
            assistant([{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest"}}],
                      "2026-10-01T10:00:06Z", cwd_a),
            assistant([{"type": "text", "text": "Added exponential backoff with jitter."}], "2026-10-01T10:00:07Z", cwd_a),
            {"type": "ai-title", "aiTitle": "Old title", "sessionId": SID_A},
            {"type": "ai-title", "aiTitle": "Webhook retries", "sessionId": SID_A},
            {"type": "pr-link", "prUrl": "https://github.com/x/y/pull/7", "sessionId": SID_A},
        ], trailing='{"type":"user","truncated')
        # Subagent transcript nested deeper must be ignored.
        self.write(root / "projects/-work-my-app" / SID_A / "subagents/agent-1.jsonl",
                   [user("subagent prompt about webhook", "2026-10-01T10:00:00Z", cwd_a, isSidechain=True)])

        cwd_b = "/work/other"
        self.write(root / "projects/-work-other" / f"{SID_B}.jsonl", [
            user("This session is being continued...\n## Summary\nwe did things", "2026-10-02T09:00:00Z", cwd_b,
                 isCompactSummary=True),
            user("Now fix the flaky test", "2026-10-02T09:00:01Z", cwd_b),
            assistant([{"type": "text", "text": "Fixed by awaiting the server."}], "2026-10-02T09:00:02Z", cwd_b),
            {"type": "ai-title", "aiTitle": "Flaky test", "sessionId": SID_B},
            {"type": "custom-title", "customTitle": "My renamed session", "sessionId": SID_B},
        ])
        self.write(root / "projects/-tmp-x" / f"{SID_CUR}.jsonl",
                   [user("current session", "2026-10-03T00:00:00Z", "/work/current")])

    def write(self, path, records, trailing=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records) + trailing)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                recall.main(list(argv))
            except SystemExit as e:
                buf.write(f"\n<exit {e.code}>")
        return buf.getvalue()

    def test_list_newest_first_titles_and_excludes_current(self):
        out = self.run_cli("list")
        self.assertNotIn(SID_CUR, out)
        self.assertNotIn("agent-1", out)
        self.assertLess(out.index("My renamed session"), out.index("Webhook retries"))  # custom-title wins, B newer
        self.assertNotIn("Old title", out)
        self.assertIn("/work/my-app [main]", out)

    def test_list_json_shape(self):
        rows = json.loads(self.run_cli("list", "--json"))
        self.assertEqual([r["id"] for r in rows], [SID_B, SID_A])
        a = rows[1]
        self.assertEqual(a["title"], "Webhook retries")
        self.assertEqual(a["cwd"], "/work/my-app")
        self.assertEqual(a["branch"], "main")
        self.assertEqual(a["prs"], ["https://github.com/x/y/pull/7"])
        hits = json.loads(self.run_cli("search", "backoff", "--json"))
        self.assertEqual(hits[0]["id"], SID_A)
        self.assertGreater(hits[0]["hits"], 0)
        self.assertTrue(hits[0]["snippets"])

    def test_shell_escape_prompts_never_become_titles(self):
        sid = "eeeeeeee-5555-4555-8555-555555555555"
        self.write(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects/-work-sh" / f"{sid}.jsonl", [
            {"type": "last-prompt", "lastPrompt": "! TOKEN=secret do-thing", "sessionId": sid},
            user("! TOKEN=secret do-thing", "2026-10-05T00:00:00Z", "/work/sh"),
            user("real question", "2026-10-05T00:00:01Z", "/work/sh"),
        ])
        out = self.run_cli("list", "--project", "/work/sh")
        self.assertIn("real question", out)
        self.assertNotIn("TOKEN=secret", out)

    def test_list_project_filter_uses_real_cwd(self):
        out = self.run_cli("list", "--project", "my-app")
        self.assertIn(SID_A, out)
        self.assertNotIn(SID_B, out)
        self.assertNotIn(SID_A, self.run_cli("list", "--project", "my/app"))

    def test_search_hits_text_not_thinking_or_subagents(self):
        out = self.run_cli("search", "backoff")
        self.assertIn(SID_A, out)
        self.assertIn("exponential backoff", out)
        self.assertIn("No sessions mention", self.run_cli("search", "secret thoughts"))
        self.assertIn("No sessions mention", self.run_cli("search", "subagent prompt"))

    def test_digest_contents(self):
        out = self.run_cli("digest", SID_A[:8])
        self.assertIn("# Session digest: Webhook retries", out)
        self.assertIn("project: /work/my-app (branch main)", out)
        self.assertIn("2 prompts · 1 files edited · 1 shell commands", out)
        self.assertIn("/plan", out)
        self.assertIn("Add retries to the webhook sender", out)
        self.assertIn("**Claude:** Added exponential backoff", out)
        self.assertIn("- /work/my-app/send.py", out)
        self.assertIn("https://github.com/x/y/pull/7", out)
        self.assertIn(f'cd "/work/my-app" && claude --resume {SID_A}', out)
        self.assertNotIn("Caveat", out)
        self.assertNotIn("secret thoughts", out)

    def test_digest_compact_summary_and_heading_demotion(self):
        out = self.run_cli("digest", SID_B)
        self.assertIn("## Compacted summary (latest)", out)
        self.assertIn("##### Summary", out)
        self.assertIn("Now fix the flaky test", out)

    def test_digest_budget_trims_middle(self):
        cwd = "/work/long"
        recs = []
        for i in range(30):
            recs.append(user(f"prompt number {i} " + "x" * 400, f"2026-10-04T00:{i:02d}:00Z", cwd))
            recs.append(assistant([{"type": "text", "text": f"answer {i} " + "y" * 400}], f"2026-10-04T00:{i:02d}:30Z", cwd))
        sid = "dddddddd-4444-4444-8444-444444444444"
        self.write(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects/-work-long" / f"{sid}.jsonl", recs)
        out = self.run_cli("digest", sid, "--max-chars", "6000")
        self.assertLessEqual(len(out), 6001)
        self.assertIn("prompt number 0 ", out)
        self.assertIn("prompt number 29 ", out)
        self.assertRegex(out, r"prompts \d+–\d+ omitted")

    def test_digest_ambiguous_and_missing(self):
        self.assertIn("<exit 1>", self.run_cli("digest", "ffff"))
        root = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects/-work-other"
        self.write(root / "aaaaaaaa-9999-4999-8999-999999999999.jsonl",
                   [user("dup", "2026-10-01T00:00:00Z", "/work/other")])
        out = self.run_cli("digest", "aaaaaaaa")
        self.assertIn("2 sessions match", out)
        self.assertIn("<exit 2>", out)


def usage_rec(mid, ts, model="claude-opus-5-5", cwd="/work/app", **usage):
    return {"type": "assistant", "timestamp": ts, "cwd": cwd, "message": {"id": mid, "model": model, "usage": usage}}


def ts_ago(days, hour=12):
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


class UsageTest(unittest.TestCase):
    UNITS = 10 + 1.25 * 200 + 0.1 * 5000 + 5 * 50  # 1010

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.root)

        def restore():
            if self.prev is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = self.prev
        self.addCleanup(restore)

    def write(self, rel, records, trailing=""):
        path = self.root / "projects" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records) + trailing)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                recall.main(list(argv))
            except SystemExit as e:
                buf.write(f"\n<exit {e.code}>")
        return buf.getvalue()

    def usage_json(self, *argv):
        return json.loads(self.run_cli("usage", *argv, "--json"))

    def test_duplicate_message_ids_counted_once_per_file(self):
        ts = ts_ago(1)
        recs = [usage_rec("msg_1", ts, input_tokens=10, cache_creation_input_tokens=200,
                          cache_read_input_tokens=5000, output_tokens=50)] * 3
        self.write("-work-app/sid-1.jsonl", recs)
        (row,) = self.usage_json("--days", "7")
        self.assertEqual(row["requests"], 1)
        self.assertEqual(row["units"], self.UNITS)

    def test_main_vs_subagent_split_and_file_counts(self):
        ts = ts_ago(1)
        self.write("-work-app/sid-1.jsonl",
                   [usage_rec("m1", ts, output_tokens=100)])  # 500 units, main
        self.write("-work-app/sid-1/subagents/agent-aaa.jsonl",
                   [usage_rec("a1", ts, output_tokens=100)])
        self.write("-work-app/sid-1/subagents/agent-bbb.jsonl",
                   [usage_rec("b1", ts, output_tokens=100)])
        # An agent file with no usage line must not be counted among distinct agents.
        self.write("-work-app/sid-1/subagents/agent-ccc.jsonl",
                   [{"type": "assistant", "timestamp": ts, "message": {"id": "c1"}}])
        (row,) = self.usage_json()
        self.assertEqual((row["main_units"], row["subagent_units"]), (500, 1000))
        self.assertAlmostEqual(row["subagent_share"], 2 / 3)
        self.assertEqual((row["main_files"], row["subagent_files"]), (1, 2))
        out = self.run_cli("usage")
        self.assertIn("67%", out)
        # --project filters on the encoded dir name.
        self.assertEqual(self.usage_json("--project", "nomatch"), [])
        self.assertEqual(len(self.usage_json("--project", "work-app")), 1)

    def test_day_vs_week_bucketing(self):
        # Two days guaranteed to fall in the same ISO week and within --days.
        anchor = datetime.now(timezone.utc) - timedelta(days=2)
        mon = (anchor - timedelta(days=anchor.isoweekday() - 1)).replace(hour=12, minute=0, second=0, microsecond=0)
        days = [mon, mon + timedelta(days=1)]
        stamps = [d.isoformat().replace("+00:00", "Z") for d in days]
        self.write("-work-app/sid-1.jsonl",
                   [usage_rec("m1", stamps[0], output_tokens=10), usage_rec("m2", stamps[1], output_tokens=10)])
        rows = self.usage_json()
        self.assertEqual([r["bucket"] for r in rows], [d.date().isoformat() for d in days])  # oldest first
        self.assertEqual([r["requests"] for r in rows], [1, 1])
        iso = mon.isocalendar()
        (week,) = self.usage_json("--by", "week")
        self.assertEqual(week["bucket"], f"{iso[0]}-W{iso[1]:02d}")
        self.assertEqual(week["requests"], 2)

    def test_days_cutoff(self):
        self.write("-work-app/sid-1.jsonl", [
            usage_rec("old", ts_ago(30), output_tokens=1000),
            usage_rec("new", ts_ago(2), output_tokens=100),
        ])
        rows = self.usage_json("--days", "7")
        self.assertEqual(sum(r["requests"] for r in rows), 1)
        self.assertEqual(sum(r["units"] for r in rows), 500)
        rows = self.usage_json("--days", "40")
        self.assertEqual(sum(r["requests"] for r in rows), 2)
        self.assertEqual(sum(r["units"] for r in rows), 5500)

    def test_malformed_lines_skipped(self):
        ts = ts_ago(1)
        self.write("-work-app/sid-1.jsonl", [
            usage_rec("ok", ts, output_tokens=10),
            {"type": "assistant", "timestamp": ts, "message": {"id": "no-usage"}},
            {"type": "assistant", "timestamp": ts, "message": {"usage": {"output_tokens": 10}}},  # no id
            {"type": "assistant", "timestamp": "garbage", "message": {"id": "bad-ts", "usage": {"output_tokens": 10}}},
            {"type": "user", "timestamp": ts, "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": ts, "message": "not-a-dict"},
        ], trailing='{"type":"assistant","truncated')
        # Malformed JSON must not abort the file or the run.
        (row,) = self.usage_json()
        self.assertEqual((row["requests"], row["units"]), (1, 50))

    def test_json_shape(self):
        ts = ts_ago(1)
        self.write("-work-app/sid-1.jsonl", [
            usage_rec("m1", ts, input_tokens=10, cache_creation_input_tokens=200,
                      cache_read_input_tokens=5000, output_tokens=50),  # units 1010, context 5210
        ])
        self.write("-work-app/sid-1/subagents/agent-aaa.jsonl", [
            usage_rec("a1", ts, model="claude-sonnet-5-0", output_tokens=100),  # units 500, context 0
            usage_rec("a2", ts, model="some-future-model", output_tokens=20),   # units 100, context 0
        ])
        (row,) = self.usage_json()
        self.assertEqual(set(row), {"bucket", "units", "requests", "main_units", "subagent_units",
                                    "subagent_share", "models", "main_files", "subagent_files", "median_context"})
        self.assertEqual((row["units"], row["requests"]), (1610, 3))
        self.assertEqual(set(row["models"]), {"opus", "sonnet", "haiku", "other"})
        self.assertEqual((row["models"]["opus"], row["models"]["sonnet"], row["models"]["other"]), (1010, 500, 100))
        self.assertEqual(row["models"]["haiku"], 0)
        self.assertEqual(row["median_context"], 0)  # median of [5210, 0, 0]
        self.assertEqual((row["main_files"], row["subagent_files"]), (1, 1))
        self.assertEqual(row["bucket"], (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat())


if __name__ == "__main__":
    unittest.main()
