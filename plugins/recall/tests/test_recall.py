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


if __name__ == "__main__":
    unittest.main()
