"""Tests for scripts/offload.py. Unit tests need no network and no model.

Run: python3 -m pytest plugins/offload/tests -q

Integration tests run the real sandbox-exec (darwin only) and never start claude.
The OFFLOAD_HOME override is the global `--home DIR` option (or the `ohome` argument of the
functions); it is not an environment variable and cannot influence the base URL.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import offload  # noqa: E402

HOME = offload.real_home()
CAN_SANDBOX = sys.platform == "darwin" and os.path.exists("/usr/bin/sandbox-exec")
needs_sandbox = pytest.mark.skipif(not CAN_SANDBOX, reason="needs darwin and /usr/bin/sandbox-exec")


def fake_claude():
    link = shutil.which("claude")
    if link:
        return {"link": os.path.abspath(link), "real": os.path.realpath(link), "version": "test"}
    return {"link": "/usr/bin/true", "real": "/usr/bin/true", "version": "test"}


def profile_for(tmp_path, **kw):
    run = tmp_path / "run"
    cl = fake_claude()
    return offload.generate_profile(str(run), cl["real"], cl["link"], **kw)


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


# ------------------------------------------------------------------ SBPL, profile

def test_sbpl_escaping_and_rejection():
    assert offload.sbpl_str("/a/b c") == '"/a/b c"'
    for bad in ('/a/"b', "/a/b\\c", "/a/\nb"):
        with pytest.raises(offload.OffloadError):
            offload.sbpl_str(bad)


def test_profile_rejects_quote_in_path(tmp_path):
    with pytest.raises(offload.OffloadError):
        offload.generate_profile(str(tmp_path / 'ru"n'), "/usr/bin/true")


def test_profile_contents(tmp_path):
    run = os.path.realpath(tmp_path / "run")
    text = profile_for(tmp_path, repo_root="/some/repo", temp_dir="/private/var/folders/ab/cd/T/")
    lines = text.splitlines()
    assert lines[0] == "(version 1)" and lines[1] == "(allow default)"
    deny_read = [ln for ln in lines if ln.startswith("(deny file-read*")]
    assert f'(subpath "{HOME}")' in deny_read[0]
    allow_read = [ln for ln in lines if ln.startswith("(allow file-read* ")]
    assert allow_read and all(f"{run}/git" not in ln and "run.json" not in ln for ln in allow_read)
    for d in ("src", "tmp", "home", "config"):
        assert f'(subpath "{run}/{d}")' in allow_read[0]
    assert f'(literal "{run}/profile.sb")' in allow_read[0]
    assert any(f'(subpath "{run}/git")' in ln and 'run.json' in ln for ln in deny_read)
    # network: everything denied, no unix sockets, and no allow at all without a proxy
    net = [ln for ln in lines if ln.startswith(("(deny network", "(allow network"))]
    assert net == ["(deny network*)"]
    assert "unix-socket" not in text
    assert "(deny appleevent-send)" in lines
    mach = next(ln for ln in lines if ln.startswith("(deny mach-lookup"))
    for name in ("com.apple.pasteboard.1", "com.apple.coreservices.launchservicesd", "com.apple.lsd.mapdb",
                 "com.apple.lsd.modifydb", "com.apple.mDNSResponder",
                 "com.apple.SystemConfiguration.DNSConfiguration"):
        assert f'(global-name "{name}")' in mach
    # writes only inside the run's dirs and devices
    write = next(ln for ln in lines if ln.startswith("(allow file-write*"))
    assert f'(subpath "{run}/src")' in write and f'(subpath "{run}/git")' not in write
    assert 'xcrun_db-' in write
    # ancestor metadata literals
    meta = next(ln for ln in lines if ln.startswith("(allow file-read-metadata"))
    for anc in offload._ancestors(run) + [run]:
        assert f'(literal "{anc}")' in meta


def test_profile_network_is_only_the_proxy_port(tmp_path):
    text = profile_for(tmp_path, proxy_port=9999)
    net = [ln for ln in text.splitlines() if ln.startswith(("(deny network", "(allow network"))]
    assert net == ["(deny network*)", '(allow network-outbound (remote ip "localhost:9999"))']


def test_profile_src_dir_replaces_src(tmp_path):
    run = os.path.realpath(tmp_path / "run")
    text = profile_for(tmp_path, src_dir=str(Path(run) / "testsrc-1"))
    write = next(ln for ln in text.splitlines() if ln.startswith("(allow file-write*"))
    assert f'(subpath "{run}/testsrc-1")' in write and f'(subpath "{run}/src")' not in write
    read = next(ln for ln in text.splitlines() if ln.startswith("(allow file-read* "))
    assert f'(subpath "{run}/src")' not in read


def test_profile_allow_read_adds_subpath_and_ancestors(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    text = profile_for(tmp_path, allow_read=[str(cache)])
    assert f'(subpath "{os.path.realpath(cache)}")' in text
    assert f'(literal "{os.path.dirname(os.path.realpath(cache))}")' in text


@pytest.mark.parametrize("sub", [".ssh", ".aws", ".claude", ".claude.json", ".config/x", "Library/y", ".kube",
                                 ".docker", ".netrc", ".cargo", ".cargo/credentials.toml", "go", "", "Documents"])
def test_allow_read_refuses_everything_off_the_allowlist(sub):
    with pytest.raises(offload.OffloadError, match="allowlist"):
        offload.validate_allow_read(os.path.join(HOME, sub), home=HOME)


@pytest.mark.parametrize("sub", ["go/pkg/mod", "go/pkg/mod/x", ".cargo/registry", ".cargo/git", ".rustup",
                                 ".m2/repository", ".gradle/caches", ".npm/_cacache"])
def test_allow_read_accepts_toolchain_caches(sub):
    assert offload.validate_allow_read(os.path.join(HOME, sub), home=HOME) == os.path.realpath(os.path.join(HOME, sub))


def test_allow_read_repo_dirs_and_symlink_escape(tmp_path):
    home = tmp_path / "h"
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    for d in (home, repo / "node_modules", outside):
        d.mkdir(parents=True)
    kw = {"home": str(home), "repo_root": str(repo)}
    assert offload.validate_allow_read(str(repo / "node_modules"), **kw) == os.path.realpath(repo / "node_modules")
    with pytest.raises(offload.OffloadError, match="allowlist"):
        offload.validate_allow_read(str(repo / "src"), **kw)
    # a symlink under the allowlist that points outside it is refused
    (repo / "node_modules" / "link").symlink_to(outside)
    with pytest.raises(offload.OffloadError, match="allowlist"):
        offload.validate_allow_read(str(repo / "node_modules" / "link"), **kw)
    # so is an allowlisted root that is itself a symlink leaving the repo
    (repo / "vendor").symlink_to(outside)
    with pytest.raises(offload.OffloadError, match="allowlist"):
        offload.validate_allow_read(str(repo / "vendor"), **kw)
    # and a symlink in the home allowlist pointing at a secret dir
    (home / ".cargo").mkdir()
    (home / ".cargo" / "registry").symlink_to(outside)
    with pytest.raises(offload.OffloadError, match="allowlist"):
        offload.validate_allow_read(str(home / ".cargo" / "registry" / "x"), home=str(home))


# ------------------------------------------------------------------ env and argv

def test_worker_env_exact_keys_and_no_inherited_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    env = offload.build_worker_env("/r", "kimi-k3:cloud", "http://localhost:11434")
    assert set(env) == {
        "PATH", "HOME", "LANG", "TMPDIR", "CLAUDE_CODE_TMPDIR", "TMPPREFIX", "CLAUDE_CONFIG_DIR",
        "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "MAX_THINKING_TOKENS"}
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert env["HOME"] == "/r/home" and env["TMPDIR"] == "/r/tmp" == env["CLAUDE_CODE_TMPDIR"] == env["TMPPREFIX"]
    assert env["MAX_THINKING_TOKENS"] == "8000"
    assert all(env[k] == "kimi-k3:cloud" for k in env if k.startswith("ANTHROPIC_") and "MODEL" in k)


def test_claude_argv():
    argv = offload.build_claude_argv("/real/claude", 12)
    assert argv[0] == "/real/claude" and argv[0] != "claude"
    for flag in ("--bare", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    i = argv.index("--setting-sources")
    assert argv[i + 1] == ""
    assert argv[argv.index("--tools") + 1] == "Bash,Read,Edit,Write,Glob,Grep"
    assert argv[argv.index("--max-turns") + 1] == "12"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--disallowedTools") + 1] == "WebFetch,WebSearch,Agent,Skill"
    assert offload.sandbox_argv("/p.sb", argv)[:3] == ["/usr/bin/sandbox-exec", "-f", "/p.sb"]


# ------------------------------------------------------------------ safe git

def test_safe_git_prefix():
    argv = offload.safe_git_argv(["-C", "/r", "status"], "/run/home", git_bin="/usr/bin/git")
    assert argv[:9] == ["/usr/bin/env", "-i", f"PATH={offload.SAFE_PATH}", "HOME=/run/home",
                        "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1", "GIT_TERMINAL_PROMPT=0",
                        "/usr/bin/git", "-c"]
    joined = " ".join(argv)
    for opt in ("core.hooksPath=/dev/null", "core.fsmonitor=false", "core.attributesFile=/dev/null",
                "core.excludesFile=/dev/null", "protocol.file.allow=always", "--no-pager"):
        assert opt in joined
    assert argv[-3:] == ["-C", "/r", "status"]


def test_safe_git_ignores_planted_excludes(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    ghome = tmp_path / "run" / "home"
    (ghome / ".config" / "git").mkdir(parents=True)
    (ghome / ".config" / "git" / "ignore").write_text("*\n")
    offload.safe_git(["-C", str(work), "init", "-q"], str(ghome))
    (work / "new.txt").write_text("x\n")
    offload.safe_git(["-C", str(work), "add", "-A"], str(ghome))
    staged = offload.safe_git(["-C", str(work), "diff", "--cached", "--name-only"], str(ghome)).stdout
    assert staged.split() == ["new.txt"]


def test_safe_git_diff_flags():
    argv = offload.safe_git_argv(["--git-dir", "/g", "diff", "a", "b"], "/h", git_bin="/usr/bin/git")
    i = argv.index("diff")
    assert argv[i + 1:i + 3] == ["--no-ext-diff", "--no-textconv"]


# ------------------------------------------------------------------ init check, refs

GOOD_INIT = {"type": "system", "subtype": "init", "mcp_servers": [], "permissionMode": "dontAsk",
             "tools": ["Bash", "Read", "Edit"]}


def test_init_check_ok():
    assert offload.check_init(GOOD_INIT) == []


def test_init_check_rejects_mcp_and_extra_tools():
    bad = dict(GOOD_INIT, mcp_servers=[{"name": "x", "status": "connected"}])
    assert any("mcp_servers" in p for p in offload.check_init(bad))
    bad = dict(GOOD_INIT, tools=["Bash", "WebFetch", "mcp__x__y"])
    assert any("WebFetch" in p for p in offload.check_init(bad))
    assert offload.check_init(dict(GOOD_INIT, permissionMode="default"))
    assert offload.check_init({"type": "system", "subtype": "init"})


def test_ref_compare():
    before = {"refs/heads/main": "a" * 40, "refs/heads/dev": "b" * 40}
    ok = dict(before, **{"refs/heads/offload/s": "c" * 40})
    assert offload.compare_refs(before, ok, "s") == []
    moved = dict(ok, **{"refs/heads/main": "d" * 40})
    assert [v["ref"] for v in offload.compare_refs(before, moved, "s")] == ["refs/heads/main"]
    assert offload.compare_refs(before, dict(ok, **{"refs/heads/extra": "e" * 40}), "s")
    gone = {k: v for k, v in ok.items() if k != "refs/heads/dev"}
    assert [v["ref"] for v in offload.compare_refs(before, gone, "s")] == ["refs/heads/dev"]


# ------------------------------------------------------------------ text, cost

def test_sanitize_strips_control_and_truncates():
    dirty = "ok\x1b[31mred\x1b[0m\x00\x07 bell ‮RTL​\ttab\nline"
    clean = offload.sanitize_untrusted(dirty)
    assert clean == "okred bell ⟨U+202E⟩RTL⟨U+200B⟩\ttab\nline"
    assert not any(ord(c) < 32 and c not in "\n\t" for c in clean)
    assert "‮" not in clean and "​" not in clean
    _, stats = offload.sanitize_untrusted_ex(dirty)
    assert stats == {"truncated": False, "format_chars": 2}
    assert offload.sanitize_untrusted_ex("é" * 10000)[1]["truncated"] is True
    big = offload.sanitize_untrusted("é" * 10000)
    assert len(big.encode()) <= offload.UNTRUSTED_LIMIT and big.endswith("[truncated]")
    assert offload.sanitize_untrusted("short") == "short"


def test_cost_estimate():
    usage = {"input": 1_000_000, "cache_read": 2_000_000, "output": 100_000}
    assert offload.estimate_cost("kimi-k3:cloud", usage) == pytest.approx(3.0 + 0.6 + 1.5)
    assert offload.estimate_cost("kimi-k3:cloud", {}) == 0
    assert offload.estimate_cost("unknown-model", usage) is None


def test_sensitive_paths():
    paths = ["src/a.py", ".husky/pre-commit", "Makefile", "sub/run.sh", ".github/workflows/x.yml",
             "package.json", "conftest.py", ".gitattributes", "lefthook.yml", "docs/readme.md", "a/.claude/x"]
    got = offload.sensitive_paths(paths)
    assert got == [p for p in paths if p not in ("src/a.py", "docs/readme.md")]
    new = [".envrc", ".vscode/tasks.json", ".idea/x.xml", ".pre-commit-config.yaml", "pyproject.toml", "a/setup.cfg",
           "x.pth", "sitecustomize.py", "usercustomize.py", "Justfile", "Rakefile", "Dockerfile",
           "docker-compose.yml", "docker-compose.override.yml", ".npmrc", ".yarnrc", ".yarnrc.yml"]
    assert offload.sensitive_paths(new) == new
    assert offload.sensitive_paths(["a\x1b[31m/Makefile", "ev\u202eil.sh"]) == ["a/Makefile", "ev⟨U+202E⟩il.sh"]


# ------------------------------------------------------------------ base URL

def write_cfg(tmp_path, urls):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"base_urls": urls}))
    return str(p)


def test_base_url_default_and_env_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setenv("OFFLOAD_BASE_URL", "https://evil.example")
    monkeypatch.setenv("OFFLOAD_HOME", str(tmp_path))
    b = offload.resolve_base_url(config_path=str(tmp_path / "none.json"))
    assert b == {"url": "http://localhost:11434", "host": "localhost", "port": 11434}


def test_base_url_allowlist(tmp_path):
    cfg = write_cfg(tmp_path, ["https://ollama.example.com"])
    assert offload.resolve_base_url("https://ollama.example.com", cfg)["port"] == 443
    with pytest.raises(offload.OffloadError):
        offload.resolve_base_url("https://other.example.com", cfg)
    with pytest.raises(offload.OffloadError):
        offload.resolve_base_url("http://localhost:9999", str(tmp_path / "missing.json"))


def test_base_url_http_non_loopback_rejected(tmp_path):
    cfg = write_cfg(tmp_path, ["http://ollama.example.com:11434"])
    with pytest.raises(offload.OffloadError, match="https"):
        offload.resolve_base_url("http://ollama.example.com:11434", cfg)


def test_base_url_loopback_http_allowed_when_listed(tmp_path):
    cfg = write_cfg(tmp_path, ["http://127.0.0.1:8080"])
    assert offload.resolve_base_url("http://127.0.0.1:8080", cfg)["port"] == 8080


# ------------------------------------------------------------------ discard

def make_run(ohome, run_dir_in_json, run_id="deadbeef-s"):
    d = Path(ohome) / "runs" / run_id
    d.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"run_id": run_id, "run_dir": str(run_dir_in_json), "slug": "s",
                                            "repo": "/nonexistent", "head_sha": "x"}))
    return d


def test_discard_refuses_path_outside_home(tmp_path):
    ohome = os.path.realpath(tmp_path / "oh")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    make_run(ohome, victim)
    with pytest.raises(offload.OffloadError, match="outside"):
        offload.discard_run(ohome, "deadbeef-s")
    assert (victim / "keep").exists()


def test_discard_rejects_traversal_run_id(tmp_path):
    with pytest.raises(offload.OffloadError):
        offload.discard_run(str(tmp_path), "../../etc")


def test_discard_removes_run_dir(tmp_path):
    ohome = os.path.realpath(tmp_path / "oh")
    d = make_run(ohome, os.path.join(ohome, "runs", "deadbeef-s"))
    out = offload.discard_run(ohome, "deadbeef-s")
    assert out["status"] == "discarded" and not d.exists()


def test_discard_never_chmods_through_symlinks(tmp_path):
    ohome = os.path.realpath(tmp_path / "oh")
    d = make_run(ohome, os.path.join(ohome, "runs", "deadbeef-s"))
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    outside.chmod(0o644)
    ro = d / "ro"
    ro.mkdir()
    (ro / "link").symlink_to(outside)
    ro.chmod(0o555)  # unlink needs the parent writable, so rmtree calls the error handler
    out = offload.discard_run(ohome, "deadbeef-s")
    assert out["status"] == "discarded" and not d.exists()
    assert outside.exists() and (outside.stat().st_mode & 0o777) == 0o644


def test_extract_rejects_traversal(tmp_path):
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("../evil")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with pytest.raises(offload.OffloadError):
        offload.extract_tar_safely(buf, str(tmp_path / "dest"))


# ------------------------------------------------------------------ egress proxy

class FakeUpstream:
    """An http.server on an ephemeral port that records requests and streams an SSE-like reply."""

    def __init__(self):
        import http.server
        import threading
        self.seen = []
        self.release = threading.Event()
        up = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                up.seen.append((self.command, self.path, body, dict(self.headers)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("anthropic-ratelimit-x", "1")
                self.send_header("request-id", "req_1")
                self.send_header("X-Secret-Header", "nope")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for part in (b"data: one\n\n", b"data: two\n\n"):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                    self.wfile.flush()
                    if part.endswith(b"one\n\n"):
                        up.release.wait(10)  # the second chunk waits until the test saw the first
                self.wfile.write(b"0\r\n\r\n")

            do_GET = do_POST = _any

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def proxy_env(tmp_path):
    up = FakeUpstream()
    log = tmp_path / "proxy_rejects.log"
    px = offload.EgressProxy(f"http://127.0.0.1:{up.port}", str(log))
    port = px.start()
    yield up, px, port, log
    px.stop()
    up.close()


def http_req(port, method, path, body=None, headers=None):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    return c, c.getresponse()


def test_proxy_forwards_and_streams_allowed_path(proxy_env):
    up, px, port, log = proxy_env
    c, r = http_req(port, "POST", "/v1/messages?beta=true", b'{"a":1}',
                    {"Content-Type": "application/json", "anthropic-version": "2023-06-01", "X-Api-Key": "ollama",
                     "X-Evil": "1"})
    assert r.status == 200
    assert r.getheader("content-type") == "text/event-stream" and r.getheader("request-id") == "req_1"
    assert r.getheader("anthropic-ratelimit-x") == "1" and r.getheader("X-Secret-Header") is None
    # streamed: the first event arrives while the upstream is still holding the second back
    assert r.read1(100) == b"data: one\n\n"
    assert not up.release.is_set()
    up.release.set()
    assert r.read() == b"data: two\n\n"
    c.close()
    method, path, body, hdrs = up.seen[0]
    assert (method, path, body) == ("POST", "/v1/messages?beta=true", b'{"a":1}')
    assert hdrs.get("anthropic-version") == "2023-06-01" and "X-Evil" not in hdrs
    up.release.set()
    c, r = http_req(port, "POST", "/v1/messages/count_tokens", b"{}")
    assert r.status == 200 and r.read()
    c.close()
    assert not log.exists()


@pytest.mark.parametrize("method,path", [("POST", "/api/pull"), ("GET", "/api/tags"), ("GET", "/v1/messages"),
                                         ("DELETE", "/api/delete"), ("POST", "/v1/messages/"),
                                         ("POST", "http://evil.example/v1/messages")])
def test_proxy_rejects_and_logs_everything_else(proxy_env, method, path):
    up, px, port, log = proxy_env
    c, r = http_req(port, method, path, b"{}" if method == "POST" else None)
    assert r.status == 403
    c.close()
    assert up.seen == []
    line = log.read_text().splitlines()[-1]
    assert method in line and path in line


def test_proxy_caps_request_body(tmp_path):
    up = FakeUpstream()
    up.release.set()
    px = offload.EgressProxy(f"http://127.0.0.1:{up.port}", str(tmp_path / "rej.log"), max_body=1000)
    port = px.start()
    try:
        c, r = http_req(port, "POST", "/v1/messages", b"x" * 1001)
        assert r.status == 413
        c.close()
        assert up.seen == [] and "too large" in (tmp_path / "rej.log").read_text()
        c, r = http_req(port, "POST", "/v1/messages", b"x" * 1000)
        assert r.status == 200
        c.close()
    finally:
        px.stop()
        up.close()
    assert offload.PROXY_MAX_BODY == 32 * 1024 * 1024


def test_proxy_stops_listening(tmp_path):
    px = offload.EgressProxy("http://127.0.0.1:9", str(tmp_path / "l"))
    port = px.start()
    px.stop()
    import socket
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2)


# ------------------------------------------------------------------ pump, survivors

def test_pump_does_not_hang_on_inherited_pipe():
    """A daemonized child keeps the pipe open after the main process exits: no EOF ever comes."""
    p = subprocess.Popen(["/bin/sh", "-c", "(sleep 30 &) ; echo hi"], stdout=subprocess.PIPE, start_new_session=True)
    got = bytearray()
    t0 = __import__("time").monotonic()
    try:
        timed_out = offload.pump(p, 20, got.extend)
        assert not timed_out and bytes(got) == b"hi\n"
        assert __import__("time").monotonic() - t0 < 10
    finally:
        offload.kill_group(p)
        p.stdout.close()
        p.wait()


def test_pump_hard_deadline():
    p = subprocess.Popen(["/bin/sh", "-c", "while :; do sleep 1; done"], stdout=subprocess.PIPE, start_new_session=True)
    try:
        assert offload.pump(p, 1, lambda c: None) is True
    finally:
        offload.kill_group(p)
        p.stdout.close()
        p.wait()


def test_sweep_kills_daemonized_survivor_by_cwd(tmp_path):
    run = tmp_path / "run"
    (run / "src").mkdir(parents=True)
    # the survivor leaves the process group (setsid) and is reparented, so only its cwd ties it to the run
    p = subprocess.Popen(["/bin/sh", "-c", "cd \"$1\" && (/usr/bin/python3 -c 'import os,time; os.setsid(); time.sleep(60)' >/dev/null 2>&1 &) ; echo $$",
                          "sh", str(run / "src")], stdout=subprocess.PIPE, start_new_session=True)
    p.stdout.read()
    p.wait()
    import time
    time.sleep(1)
    killed = offload.sweep_survivors(p, str(run))
    p.stdout.close()
    assert killed, "the survivor was not found"
    time.sleep(0.3)
    for pid in killed:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


# ------------------------------------------------------------------ integration

def git(repo, *args, env_extra=None):
    env = {"PATH": offload.SAFE_PATH, "HOME": str(repo), "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", **(env_extra or {})}
    return subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "hello.txt").write_text("hello\n")
    (r / "src").mkdir()
    (r / "src" / "app.py").write_text("print('hi')\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def prepare(tmp_path, repo, slug="job"):
    ohome = os.path.realpath(tmp_path / "ohome")
    claude = fake_claude()
    ctx = offload.prepare_run(str(repo), slug, ohome, claude=claude)
    return ohome, ctx


@needs_sandbox
def test_integration_selftest_passes_and_isolation(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    up = FakeUpstream()
    px = offload.EgressProxy(f"http://127.0.0.1:{up.port}", os.path.join(ctx["run_dir"], "proxy_rejects.log"))
    try:
        ctx["proxy_port"] = px.start()
        offload.write_profile(ctx, ctx["proxy_port"])
        ok, results = offload.run_selftest(ctx)
        assert ok, results
        names = " | ".join(r["check"] for r in results)
        for needle in ("proxy answers 403", "direct connect to localhost:11434", "pbpaste", "DNS lookup"):
            assert needle in names, names
        assert up.seen == []
        assert "/api/pull" in (Path(ctx["run_dir"]) / "proxy_rejects.log").read_text()
    finally:
        px.stop()
        up.close()
    assert (Path(ctx["src"]) / "hello.txt").read_text() == "hello\n"
    assert not [p for p in Path(ctx["src"]).iterdir() if p.name.startswith(".probe")]
    assert not [p for p in repo.iterdir() if p.name.startswith(".offload-probe")]
    sb = lambda *a: subprocess.run(["/usr/bin/sandbox-exec", "-f", ctx["profile"], *a], capture_output=True, text=True)
    assert sb("/bin/cat", str(repo / "hello.txt")).returncode != 0
    ok_read = sb("/bin/cat", os.path.join(ctx["src"], "src", "app.py"))
    assert ok_read.returncode == 0 and "print" in ok_read.stdout
    assert sb("/bin/cat", os.path.join(ctx["git_dir"], "HEAD")).returncode != 0
    assert sb("/bin/cat", os.path.join(ctx["run_dir"], "run.json")).returncode != 0
    assert sb("/bin/ls", ctx["run_dir"]).returncode != 0


@needs_sandbox
def test_integration_no_unix_sockets_and_proxy_only(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    up = FakeUpstream()
    up.release.set()
    px = offload.EgressProxy(f"http://127.0.0.1:{up.port}", os.path.join(ctx["run_dir"], "proxy_rejects.log"))
    try:
        ctx["proxy_port"] = px.start()
        offload.write_profile(ctx, ctx["proxy_port"])
        sb = lambda *a: subprocess.run(["/usr/bin/sandbox-exec", "-f", ctx["profile"], *a], cwd=ctx["src"],
                                       capture_output=True, text=True, timeout=30)
        # a unix socket that the worker could otherwise reach (relative path, so the length is fine)
        import socket
        srv = socket.socket(socket.AF_UNIX)
        here = os.getcwd()
        os.chdir(ctx["src"])
        try:
            srv.bind("s.sock")
        finally:
            os.chdir(here)
        srv.listen(1)
        code = "import socket; s=socket.socket(socket.AF_UNIX); s.connect('s.sock')"
        control = subprocess.run(["/usr/bin/python3", "-c", code], cwd=ctx["src"], capture_output=True)
        assert control.returncode == 0, control.stderr
        denied = sb("/usr/bin/python3", "-c", code)
        assert denied.returncode != 0 and "not permitted" in denied.stderr.lower(), denied.stderr
        srv.close()
        # only the proxy port is reachable; the upstream's own port is not
        ok_port = sb("/usr/bin/curl", "-s", "-m", "3", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST",
                     f"http://127.0.0.1:{ctx['proxy_port']}/v1/messages", "-d", "{}")
        assert ok_port.stdout == "200", ok_port
        direct = sb("/usr/bin/curl", "-s", "-m", "3", "-o", "/dev/null", "-w", "%{http_code}",
                    f"http://127.0.0.1:{up.port}/")
        assert direct.returncode != 0 and direct.stdout == "000"
    finally:
        px.stop()
        up.close()


@needs_sandbox
def test_integration_test_cmd_runs_committed_code_without_network(tmp_path, repo, capsys):
    ohome, ctx = prepare(tmp_path, repo)
    (Path(ctx["src"]) / "committed.txt").write_text("in the commit\n")
    head = offload.commit_work(ctx)
    offload.fetch_work(ctx)
    ctx["record"].update(head_sha=head, status="ok")
    offload.write_run_json(ctx["run_dir"], ctx["record"])
    # written after the commit: must not be visible to `test`
    (Path(ctx["src"]) / "late.txt").write_text("late\n")
    cmd = ["/bin/sh", "-c", "cat committed.txt; test ! -e late.txt && echo no-late; pwd >&2; "
                            "curl -s -m 2 http://127.0.0.1:11434/ ; echo curl=$?"]
    assert offload.main(["--home", ohome, "test", "--run", ctx["run_id"], "--", *cmd]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and "in the commit" in out["untrusted_output"] and "no-late" in out["untrusted_output"]
    assert "curl=7" in out["untrusted_output"]
    assert "testsrc-1" in out["untrusted_output"]
    assert not list(Path(ctx["run_dir"]).glob("testsrc-*")) and not list(Path(ctx["run_dir"]).glob("profile-test-*"))
    assert (Path(ctx["src"]) / "late.txt").exists()


@needs_sandbox
def test_integration_test_cmd_survives_daemonizing_and_floods(tmp_path, repo, capsys):
    ohome, ctx = prepare(tmp_path, repo)
    head = offload.commit_work(ctx)
    offload.fetch_work(ctx)
    ctx["record"].update(head_sha=head, status="ok")
    offload.write_run_json(ctx["run_dir"], ctx["record"])
    cmd = ["/bin/sh", "-c", "(sleep 60 &); head -c 300000 /dev/zero | tr '\\0' 'x'; echo END"]
    t0 = __import__("time").monotonic()
    assert offload.main(["--home", ohome, "test", "--run", ctx["run_id"], "--timeout-s", "30", "--", *cmd]) == 0
    assert __import__("time").monotonic() - t0 < 25
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok" and out["total_output_bytes"] >= 300000 and "END" in out["untrusted_output"]
    assert out["survivors_killed"]
    assert json.loads((Path(ctx["run_dir"]) / "run.json").read_text())["survivors_killed"] == out["survivors_killed"]


@needs_sandbox
def test_integration_commit_fetch_verify(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    before = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    (Path(ctx["src"]) / "new_feature.py").write_text("x = 1\n")
    head = offload.commit_work(ctx)
    offload.fetch_work(ctx)
    assert offload.verify_refs(ctx) == []
    assert git(repo, "rev-parse", "refs/heads/offload/job").strip() == head
    after = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    changed = set(after.splitlines()) ^ set(before.splitlines())
    assert {ln.split()[0] for ln in changed} == {"refs/heads/offload/job"}
    assert "new_feature.py" in git(repo, "show", "--stat", "--format=", "refs/heads/offload/job")
    assert git(repo, "for-each-ref", "refs/offload-tmp").strip() == ""
    # a second fetch must refuse to overwrite
    with pytest.raises(offload.OffloadError, match="already exists"):
        offload.fetch_work(ctx)
    assert git(repo, "rev-parse", "refs/heads/offload/job").strip() == head


@needs_sandbox
def test_integration_update_ref_is_create_only_and_tmp_ref_cleaned(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    head = offload.commit_work(ctx)
    # lose the race: the branch appears after the pre-check but before update-ref
    real = offload.check_branch_free
    def racing(*a, **k):
        real(*a, **k)
        git(repo, "branch", "offload/job", "main")
    offload.check_branch_free = racing
    try:
        with pytest.raises(offload.OffloadError):
            offload.fetch_work(ctx)
    finally:
        offload.check_branch_free = real
    assert git(repo, "rev-parse", "refs/heads/offload/job").strip() == git(repo, "rev-parse", "main").strip() != head
    assert git(repo, "for-each-ref", "refs/offload-tmp").strip() == ""


@needs_sandbox
@pytest.mark.parametrize("existing", ["offload", "offload/job", "offload/job/x"])
def test_integration_prepare_refuses_branch_collisions_before_any_spend(tmp_path, repo, existing):
    git(repo, "branch", existing, "main")
    ohome = os.path.realpath(tmp_path / "ohome")
    with pytest.raises(offload.OffloadError, match="already exists|under it"):
        offload.prepare_run(str(repo), "job", ohome, claude=fake_claude())
    assert not os.path.exists(os.path.join(ohome, "runs"))


@needs_sandbox
def test_integration_ref_violation_inside_the_fetch_window(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    offload.commit_work(ctx)
    real = offload.safe_git
    def meddling(args, home, *a, **k):
        p = real(args, home, *a, **k)
        if "update-ref" in args and "refs/heads/offload/job" in args:
            git(repo, "branch", "sneaky", "main")
        return p
    offload.safe_git = meddling
    try:
        offload.fetch_work(ctx)
    finally:
        offload.safe_git = real
    assert [v["ref"] for v in offload.verify_refs(ctx)] == ["refs/heads/sneaky"]


@needs_sandbox
def test_integration_hooks_do_not_run(tmp_path, repo):
    marker = tmp_path / "hook-ran"
    hooks = repo / ".husky"
    hooks.mkdir()
    for name in ("pre-commit", "reference-transaction", "post-checkout", "post-commit", "commit-msg"):
        h = hooks / name
        h.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
        h.chmod(0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "hooks")
    git(repo, "config", "core.hooksPath", ".husky")
    # control: the hook really fires when git is run unsafely in this repo
    git(repo, "update-ref", "refs/heads/control", "HEAD")
    assert marker.exists(), "test setup is wrong: the hook did not fire"
    marker.unlink()
    git(repo, "update-ref", "-d", "refs/heads/control")
    marker.unlink(missing_ok=True)
    _, ctx = prepare(tmp_path, repo)
    (Path(ctx["src"]) / "x.py").write_text("y = 2\n")
    offload.commit_work(ctx)
    offload.fetch_work(ctx)
    assert offload.verify_refs(ctx) == []
    assert not marker.exists()


@needs_sandbox
def test_integration_cli_list_status_diff_discard(tmp_path, repo, capsys):
    ohome, ctx = prepare(tmp_path, repo)
    (Path(ctx["src"]) / "added.sh").write_text("echo hi\n")
    head = offload.commit_work(ctx)
    offload.fetch_work(ctx)
    ctx["record"].update(head_sha=head, status="ok")
    offload.write_run_json(ctx["run_dir"], ctx["record"])
    assert offload.main(["--home", ohome, "list"]) == 0
    assert ctx["run_id"] in capsys.readouterr().out
    assert offload.main(["--home", ohome, "diff", "--run", ctx["run_id"]]) == 0
    out = capsys.readouterr().out
    assert "added.sh" in out and '"untrusted_sensitive_paths"' in out
    assert "added.sh" in out.split('"untrusted_sensitive_paths"')[1]
    trailer = json.loads(out[out.index("\n{") + 1:])
    assert trailer["truncated"] is False and trailer["format_chars"] == 0
    assert trailer["untrusted_numstat"] == [{"path": "added.sh", "added": 1, "deleted": 0}]
    assert offload.main(["--home", ohome, "diff", "--run", ctx["run_id"], "--", "hello.txt"]) == 0
    out = capsys.readouterr().out
    assert "added.sh" not in out.split("\n{")[0] and "added.sh" in out.split("\n{")[1]
    assert offload.main(["--home", ohome, "discard", "--run", ctx["run_id"]]) == 0
    assert not os.path.exists(ctx["run_dir"])
    assert subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "refs/heads/offload/job"],
                          capture_output=True).returncode != 0


@needs_sandbox
def test_integration_diff_truncation_flag_and_path_filter(tmp_path, repo, capsys):
    ohome, ctx = prepare(tmp_path, repo)
    (Path(ctx["src"]) / "big.txt").write_text("line\n" * 60000)
    (Path(ctx["src"]) / "small.txt").write_text("zero\u200bwidth\n")
    head = offload.commit_work(ctx)
    offload.fetch_work(ctx)
    ctx["record"].update(head_sha=head, status="ok")
    offload.write_run_json(ctx["run_dir"], ctx["record"])
    assert offload.main(["--home", ohome, "diff", "--run", ctx["run_id"]]) == 0
    out = capsys.readouterr().out
    trailer = json.loads(out[out.rindex("\n{") + 1:])
    assert trailer["truncated"] is True and trailer["format_chars"] == 1
    assert {r["path"] for r in trailer["untrusted_numstat"]} == {"big.txt", "small.txt"}
    assert offload.main(["--home", ohome, "diff", "--run", ctx["run_id"], "--", "small.txt"]) == 0
    out = capsys.readouterr().out
    trailer = json.loads(out[out.rindex("\n{") + 1:])
    assert trailer["truncated"] is False and "zero⟨U+200B⟩width" in out and trailer["paths_filter"] == ["small.txt"]


@needs_sandbox
def test_integration_errors_are_recorded_in_run_json(tmp_path, repo, monkeypatch):
    ohome, ctx = prepare(tmp_path, repo)
    brief = tmp_path / "brief.md"
    brief.write_text("do it\n")
    # exercise cmd_run's error path with a fake worker that succeeds and a fetch that fails
    monkeypatch.setattr(offload, "resolve_claude", lambda allow=False: fake_claude())
    monkeypatch.setattr(offload, "launch_worker", lambda *a, **k: {
        "init_problems": [], "saw_init": True, "timed_out": False, "result": {"is_error": False}, "usage": {},
        "text": "", "returncode": 0, "turns": 1, "survivors_killed": []})
    monkeypatch.setattr(offload, "run_selftest", lambda c: (True, []))
    def boom(c):
        raise offload.OffloadError("fetch exploded")
    monkeypatch.setattr(offload, "fetch_work", boom)
    shutil.rmtree(ctx["run_dir"])
    assert offload.main(["--home", ohome, "run", "--repo", str(repo), "--brief", str(brief), "--slug", "job"]) == 2
    rec = json.loads((Path(ohome) / "runs" / ctx["run_id"] / "run.json").read_text())
    assert rec["status"] == "error" and "fetch exploded" in rec["error"] and len(rec["head_sha"]) == 40
