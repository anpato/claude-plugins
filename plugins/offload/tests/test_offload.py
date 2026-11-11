"""Tests for scripts/offload.py. No network and no real model.

Run: python3 -m pytest plugins/offload/tests -q

The integration tests run the real run flow with a *fake* worker binary (a small script that emits the
worker's event stream and writes a file), so they exercise prepare_run/launch_worker/commit_work/fetch_work
without Claude Code, codex or any model. The OFFLOAD_HOME override is the global `--home DIR` option (the
`ohome` argument of the functions); it is not an environment variable.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import offload  # noqa: E402

HOME = offload.real_home()


# ------------------------------------------------------------------ git helpers

def git(repo, *args, env=None):
    e = {"PATH": offload.SAFE_PATH, "HOME": str(repo), "GIT_CONFIG_GLOBAL": "/dev/null",
         "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if env:
        e.update(env)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=e, check=True)


def make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


# ------------------------------------------------------------------ slug, run id

@pytest.mark.parametrize("slug", ["a", "feature-x", "a1", "x" * 40, "0abc"])
def test_slug_ok(slug):
    assert offload.validate_slug(slug) == slug


@pytest.mark.parametrize("slug", ["..", "Upper", "x" * 41, "-lead", "trail-", "a/b", "", "a b", "a_b"])
def test_slug_bad(slug):
    with pytest.raises(offload.OffloadError):
        offload.validate_slug(slug)


def test_run_id_deterministic():
    a = offload.run_id_for("/repo/one", "s")
    assert a == offload.run_id_for("/repo/one", "s")
    assert a != offload.run_id_for("/repo/two", "s")
    assert offload.RUN_ID_RE.match(a) and a.endswith("-s") and len(a.split("-")[0]) == 8


def test_default_slug():
    assert offload.default_slug("/x/My Brief!.md") == "my-brief"
    assert offload.SLUG_RE.match(offload.default_slug("/x/....md"))  # falls back to task-<hash>


# ------------------------------------------------------------------ sanitize, cost, sensitive

def test_sanitize_strips_ansi_and_control():
    out, st = offload.sanitize_untrusted_ex("\x1b[31mred\x1b[0m\x07\tok\n")
    assert out == "red\tok\n" and st["format_chars"] == 0


def test_sanitize_marks_format_chars():
    out, st = offload.sanitize_untrusted_ex("a‮b")  # RLO bidi override
    assert "⟨U+202E⟩" in out and st["format_chars"] == 1


def test_sanitize_truncates():
    out, st = offload.sanitize_untrusted_ex("x" * 100, limit=20)
    assert st["truncated"] and out.endswith("[truncated]") and len(out.encode()) <= 20


def test_estimate_cost_known_and_unknown():
    c = offload.estimate_cost("kimi-k3:cloud", {"input": 1_000_000, "cache_read": 0, "output": 0})
    assert c == 3.0
    assert offload.estimate_cost("some-codex-model", {"input": 10, "output": 10}) is None


def test_sensitive_paths():
    got = offload.sensitive_paths(["src/app.py", "Makefile", ".github/x.yml", "a/.git/cfg", "run.sh",
                                   "sub/pyproject.toml", ".codex/config.toml", "normal.txt"])
    assert set(got) == {"Makefile", ".github/x.yml", "a/.git/cfg", "run.sh", "sub/pyproject.toml",
                        ".codex/config.toml"}


# ------------------------------------------------------------------ workers, base url

def test_list_workers_filters(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"workers": {
        "good": {"base_url": "http://localhost:11435/", "model": "m:1", "context_tokens": 131072},
        "badmodel": {"base_url": "http://localhost:1", "model": "bad model!"},
        "nourl": {"model": "m"},
        "badctx": {"base_url": "http://localhost:2", "model": "m", "context_tokens": 10},
    }}))
    w = offload.list_workers(str(cfg))
    assert set(w) == {"good", "badctx"}
    assert w["good"] == {"base_url": "http://localhost:11435", "model": "m:1", "context_tokens": 131072}
    assert "context_tokens" not in w["badctx"]  # out of range dropped


def test_resolve_base_url_default():
    assert offload.resolve_base_url()["url"] == offload.DEFAULT_BASE_URL


def test_resolve_base_url_rejects_unlisted(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text("{}")
    with pytest.raises(offload.OffloadError):
        offload.resolve_base_url("http://localhost:9999", config_path=str(cfg))


def test_resolve_base_url_allowlist_and_https(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"base_urls": ["http://localhost:9999", "http://1.2.3.4:5"]}))
    assert offload.resolve_base_url("http://localhost:9999", config_path=str(cfg))["port"] == 9999
    with pytest.raises(offload.OffloadError):  # listed but non-loopback http
        offload.resolve_base_url("http://1.2.3.4:5", config_path=str(cfg))


# ------------------------------------------------------------------ argv, env, init check

def test_build_claude_argv():
    argv = offload.build_claude_argv("/bin/claude", 42)
    assert argv[0] == "/bin/claude" and "--max-turns" in argv and "42" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == ",".join(offload.ALLOWED_TOOLS)


def test_build_codex_argv():
    argv = offload.build_codex_argv("/bin/codex", "/run/src", "gpt-x", "/run/last.txt")
    assert argv[:2] == ["/bin/codex", "exec"]
    for flag in ("--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv
    assert argv[argv.index("-C") + 1] == "/run/src"
    assert argv[argv.index("-s") + 1] == "workspace-write"
    assert argv[argv.index("-o") + 1] == "/run/last.txt"
    assert argv[argv.index("-m") + 1] == "gpt-x"
    assert "-m" not in offload.build_codex_argv("/bin/codex", "/s", None, "/l")  # model omitted


def test_check_init():
    ok = {"mcp_servers": [], "permissionMode": "dontAsk", "tools": list(offload.ALLOWED_TOOLS)}
    assert offload.check_init(ok) == []
    bad = {"mcp_servers": [{"x": 1}], "permissionMode": "acceptEdits", "tools": ["Bash", "WebFetch"]}
    probs = offload.check_init(bad)
    assert len(probs) == 3 and any("WebFetch" in p for p in probs)


def test_build_worker_env_claude_vs_codex(tmp_path):
    base = {"url": "http://localhost:11434"}
    claude_ctx = {"run_dir": str(tmp_path), "kind": "claude", "model": "kimi:1", "base": base,
                  "max_thinking": 8000, "context_tokens": 131072}
    env = offload.build_worker_env(claude_ctx)
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434" and env["ANTHROPIC_MODEL"] == "kimi:1"
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "131072" and "CODEX_HOME" not in env
    assert env["HOME"] == os.path.join(str(tmp_path), "home")

    codex_ctx = {"run_dir": str(tmp_path), "kind": "codex", "model": None, "base": None}
    cenv = offload.build_worker_env(codex_ctx)
    assert cenv["CODEX_HOME"] == offload.codex_home() and "ANTHROPIC_BASE_URL" not in cenv


# ------------------------------------------------------------------ resolve binaries

def test_resolve_codex_no_binary(monkeypatch):
    monkeypatch.setattr(offload.shutil, "which", lambda n: None)
    with pytest.raises(offload.OffloadError, match="codex login|not found"):
        offload.resolve_codex()


def test_resolve_codex_no_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(offload.shutil, "which", lambda n: "/usr/bin/true")
    monkeypatch.setattr(offload, "codex_home", lambda: str(tmp_path / "nope"))
    with pytest.raises(offload.OffloadError, match="codex login"):
        offload.resolve_codex()


def test_resolve_codex_ok(monkeypatch, tmp_path):
    ch = tmp_path / ".codex"
    ch.mkdir()
    (ch / "auth.json").write_text("{}")
    monkeypatch.setattr(offload.shutil, "which", lambda n: "/usr/bin/true")
    monkeypatch.setattr(offload, "codex_home", lambda: str(ch))
    r = offload.resolve_codex()
    assert r["kind"] == "codex" and r["real"]


# ------------------------------------------------------------------ refs, usage

def test_compare_refs():
    before = {"refs/heads/main": "a", "refs/heads/x": "b"}
    after = {"refs/heads/main": "a", "refs/heads/x": "c", "refs/heads/offload/s": "d"}
    bad = offload.compare_refs(before, after, "s")  # offload/s is the one allowed new branch
    assert bad == [{"ref": "refs/heads/x", "before": "b", "after": "c"}]


def test_sum_usage():
    u = offload.sum_usage([{"input_tokens": 1, "cache_read_input_tokens": 2, "output_tokens": 3},
                           {"input_tokens": 4, "output_tokens": None}])
    assert u == {"input": 5, "cache_read": 2, "output": 3}


def test_codex_usage_mapping():
    m = offload._codex_usage({"input_tokens": 10, "cached_input_tokens": 7, "cache_write_input_tokens": 1,
                              "output_tokens": 2})
    assert m == {"input_tokens": 10, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 1,
                 "output_tokens": 2}


# ------------------------------------------------------------------ stream handlers

class FakeProgress:
    def __init__(self):
        self.tools = 0
    def tool_use(self, n):
        self.tools += n
    def usage(self, per_msg, last):
        pass


def test_claude_handler():
    out = {"saw_init": False, "init_problems": [], "text": "", "result": None}
    h = offload._claude_handler(out, {}, FakeProgress())
    assert h(json.dumps({"type": "system", "subtype": "init", "mcp_servers": [],
                         "permissionMode": "dontAsk", "tools": list(offload.ALLOWED_TOOLS)}).encode()) is False
    assert out["saw_init"] and out["init_problems"] == []
    h(json.dumps({"type": "assistant", "message": {"id": "1", "usage": {"input_tokens": 5},
                  "content": [{"type": "text", "text": "hi"}, {"type": "tool_use"}]}}).encode())
    assert out["text"] == "hi"
    h(json.dumps({"type": "result", "result": "final", "num_turns": 3}).encode())
    assert out["result"]["result"] == "final"


def test_claude_handler_bad_init_stops():
    out = {"saw_init": False, "init_problems": [], "text": "", "result": None}
    h = offload._claude_handler(out, {}, FakeProgress())
    stop = h(json.dumps({"type": "system", "subtype": "init", "mcp_servers": [{"x": 1}],
                        "permissionMode": "dontAsk", "tools": list(offload.ALLOWED_TOOLS)}).encode())
    assert stop is True and out["init_problems"]


def test_codex_handler():
    out = {"saw_init": False, "text": "", "result": None, "turns": 0}
    prog = FakeProgress()
    h = offload._codex_handler(out, {}, prog)
    h(json.dumps({"type": "thread.started", "thread_id": "x"}).encode())
    assert out["saw_init"]
    h(json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}).encode())
    h(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}).encode())
    assert out["text"] == "done" and prog.tools == 1
    h(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 9, "cached_input_tokens": 1,
                  "output_tokens": 2}}).encode())
    assert out["turns"] == 1 and out["result"]


# ------------------------------------------------------------------ tar safety, repo validation

def test_extract_tar_rejects_escape(tmp_path):
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("../evil")
        info.size = 0
        tf.addfile(info, io.BytesIO(b""))
    buf.seek(0)
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(offload.OffloadError):
        offload.extract_tar_safely(buf, str(dest))


def test_validate_repo_refuses_home(tmp_path):
    with pytest.raises(offload.OffloadError):
        offload.validate_repo(HOME, str(tmp_path / "oh"))


def test_validate_repo_ok(tmp_path):
    repo = make_repo(tmp_path)
    assert offload.validate_repo(str(repo), str(tmp_path / "oh")) == os.path.realpath(str(repo))


# ------------------------------------------------------------------ run.json, load_run

def test_write_and_load_run(tmp_path):
    ohome = str(tmp_path / "oh")
    rd = os.path.join(ohome, "runs", "1" * 8 + "-s")
    os.makedirs(rd)
    offload.write_run_json(rd, {"run_id": "1" * 8 + "-s", "run_dir": rd, "slug": "s"})
    assert stat.S_IMODE(os.stat(os.path.join(rd, "run.json")).st_mode) == 0o600
    rec = offload.load_run(ohome, "1" * 8 + "-s")
    assert rec["slug"] == "s"


def test_load_run_rejects_escape(tmp_path):
    ohome = str(tmp_path / "oh")
    rd = os.path.join(ohome, "runs", "2" * 8 + "-s")
    os.makedirs(rd)
    offload.write_run_json(rd, {"run_id": "2" * 8 + "-s", "run_dir": "/etc", "slug": "s"})
    with pytest.raises(offload.OffloadError):
        offload.load_run(ohome, "2" * 8 + "-s")


# ------------------------------------------------------------------ cmd_run arg validation

def _args(**kw):
    base = dict(repo="/x", brief="/b", slug=None, kind="claude", model=None, worker=None,
                max_turns=60, timeout_min=45, max_thinking_tokens=8000, base_url=None)
    base.update(kw)
    return type("A", (), base)


def test_cmd_run_codex_rejects_worker(tmp_path, monkeypatch):
    brief = tmp_path / "b.md"
    brief.write_text("do it")
    monkeypatch.setattr(offload, "resolve_codex", lambda: {"kind": "codex", "real": "/x"})
    with pytest.raises(offload.OffloadError, match="don't pass --worker"):
        offload.cmd_run(_args(kind="codex", brief=str(brief), worker="gpu-1"), str(tmp_path / "oh"))


# ------------------------------------------------------------------ integration with a fake worker

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, sys
sys.stdin.read()
open("new_feature.py", "w").write("x = 1\n")
def e(o): print(json.dumps(o), flush=True)
e({"type": "system", "subtype": "init", "mcp_servers": [], "permissionMode": "dontAsk",
   "tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]})
e({"type": "assistant", "message": {"id": "1", "usage": {"input_tokens": 10, "output_tokens": 4},
   "content": [{"type": "tool_use"}, {"type": "text", "text": "added it"}]}})
e({"type": "result", "result": "added it", "num_turns": 2, "usage": {"input_tokens": 10, "output_tokens": 4}})
"""

FAKE_CODEX = r"""#!/usr/bin/env python3
import json, sys
sys.stdin.read()
out = sys.argv[sys.argv.index("-o") + 1]
open("new_feature.py", "w").write("x = 1\n")
open(out, "w").write("added it (codex)")
def e(o): print(json.dumps(o), flush=True)
e({"type": "thread.started", "thread_id": "t"})
e({"type": "item.completed", "item": {"type": "file_change"}})
e({"type": "item.completed", "item": {"type": "agent_message", "text": "added it (codex)"}})
e({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 4}})
"""


def _fake_bin(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return {"kind": name.split("_")[-1], "link": str(p), "real": str(p), "version": "fake"}


def _run_flow(tmp_path, kind, worker_bin, model, base):
    repo = make_repo(tmp_path)
    ohome = str(tmp_path / "oh")
    ctx = offload.prepare_run(str(repo), "feat", ohome, kind, worker_bin, model, base)
    offload.init_worker_git(ctx)
    out = offload.launch_worker(ctx, "build it", timeout_s=60)
    head = offload.commit_work(ctx)
    offload.fetch_work(ctx)
    ctx["base_sha"] = ctx["record"]["base_sha"]
    stat_out, paths = offload.diff_summary(ctx)
    return repo, ohome, ctx, out, head, stat_out, paths


def test_integration_claude(tmp_path):
    wb = _fake_bin(tmp_path, "fake_claude", FAKE_CLAUDE)
    repo, ohome, ctx, out, head, stat_out, paths = _run_flow(
        tmp_path, "claude", wb, "kimi-k3:cloud", {"url": offload.DEFAULT_BASE_URL})
    assert out["saw_init"] and out["init_problems"] == []
    assert out["text"] == "added it" and out["usage"]["output"] == 4
    assert "new_feature.py" in paths
    # branch landed in the real repo, nothing else moved
    assert offload.ref_exists(str(repo), "refs/heads/offload/feat", "/var/empty")
    assert offload.verify_refs(ctx) == []
    assert offload.estimate_cost("kimi-k3:cloud", out["usage"]) is not None


def test_integration_codex(tmp_path):
    wb = _fake_bin(tmp_path, "fake_codex", FAKE_CODEX)
    repo, ohome, ctx, out, head, stat_out, paths = _run_flow(tmp_path, "codex", wb, None, None)
    assert out["saw_init"] and out["turns"] == 1
    assert out["text"] == "added it (codex)"  # read from the -o last-message file
    assert out["usage"] == {"input": 10, "cache_read": 2, "output": 4}
    assert "new_feature.py" in paths
    assert offload.ref_exists(str(repo), "refs/heads/offload/feat", "/var/empty")
    assert offload.estimate_cost(None, out["usage"]) is None  # codex: no per-token estimate


def test_integration_test_and_discard(tmp_path):
    wb = _fake_bin(tmp_path, "fake_codex", FAKE_CODEX)
    repo, ohome, ctx, out, head, stat_out, paths = _run_flow(tmp_path, "codex", wb, None, None)
    # `test` runs the committed code in a throwaway checkout
    targs = type("A", (), {"run": ctx["run_id"], "timeout_s": 30,
                           "test_cmd": ["--", sys.executable, "-c", "import new_feature; print(new_feature.x)"]})
    offload.cmd_test(targs, ohome)
    # record head_sha as cmd_run would, so discard can match and delete the branch
    rec = offload.load_run(ohome, ctx["run_id"])
    rec["head_sha"] = head
    offload.write_run_json(ctx["run_dir"], rec)
    # discard removes the run and the branch
    res = offload.discard_run(ohome, ctx["run_id"])
    assert res["branch_deleted"] is True
    assert not os.path.exists(ctx["run_dir"])
    assert not offload.ref_exists(str(repo), "refs/heads/offload/feat", "/var/empty")
