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
        "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "MAX_THINKING_TOKENS",
        "PIP_INDEX_URL", "PIP_TRUSTED_HOST", "NPM_CONFIG_REGISTRY", "CARGO_HOME", "GOPROXY", "GOFLAGS",
        "GOSUMDB", "GOCACHE"}
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
    assert env["HOME"] == "/r/home" and env["TMPDIR"] == "/r/tmp" == env["CLAUDE_CODE_TMPDIR"]
    assert env["TMPPREFIX"] == "/r/tmp/zsh"
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
        ok, results = offload.run_selftest(ctx, online=False)
        assert ok, results
        names = " | ".join(r["check"] for r in results)
        for needle in ("proxy answers 403", "direct connect to localhost:11434", "pbpaste", "DNS lookup",
                       "CONNECT pypi.org:443", "absolute-form GET", "PUT /r/npm/x", "/r/pypi/../../x",
                       "/r/nope/", "(offline, skipped)", "33rd concurrent"):
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


# ------------------------------------------------------------------ run.json live state

def _assistant(n_tools, mid="m"):
    blocks = [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}} for i in range(n_tools)]
    return json.dumps({"type": "assistant", "message": {"id": mid, "content": blocks or [{"type": "text", "text": "hi"}]}})


def _read_run_json(d):
    with open(os.path.join(d, "run.json")) as f:
        return json.load(f)


def test_write_run_json_atomic_and_private(tmp_path):
    offload.write_run_json(str(tmp_path), {"status": "prepared", "n": 1})
    offload.write_run_json(str(tmp_path), {"status": "running", "n": 2})
    assert os.listdir(tmp_path) == ["run.json"]  # temp file is gone
    assert _read_run_json(tmp_path) == {"status": "running", "n": 2}
    assert os.stat(tmp_path / "run.json").st_mode & 0o777 == 0o600


def test_write_run_json_failure_leaves_old_file_and_no_tmp(tmp_path):
    offload.write_run_json(str(tmp_path), {"ok": 1})
    with pytest.raises(TypeError):
        offload.write_run_json(str(tmp_path), {"bad": object()})
    assert os.listdir(tmp_path) == ["run.json"] and _read_run_json(tmp_path) == {"ok": 1}


def test_progress_counts_and_throttles(tmp_path):
    now = [100.0]
    rec = {"status": "running"}
    p = offload.Progress(str(tmp_path), rec, interval=2.0, clock=lambda: now[0])
    p.tool_use(2)
    assert _read_run_json(tmp_path)["tool_calls"] == 2
    now[0] = 101.0
    p.tool_use(1)  # inside the window: in-memory only
    assert rec["tool_calls"] == 3 and _read_run_json(tmp_path)["tool_calls"] == 2
    now[0] = 102.5
    p.tool_use(1)
    on_disk = _read_run_json(tmp_path)
    assert on_disk["tool_calls"] == 4 and on_disk["last_activity_at"] > 0


def _fake_launch(monkeypatch, tmp_path, lines):
    script = tmp_path / "fake.sh"
    script.write_text("#!/bin/sh\ncat >/dev/null\n" + "".join(f"printf '%s\\n' '{ln}'\n" for ln in lines))
    script.chmod(0o755)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(offload, "sandbox_argv", lambda profile, argv: [str(script)])
    monkeypatch.setattr(offload, "worker_url", lambda ctx: "http://127.0.0.1:1")
    monkeypatch.setattr(offload, "sweep_survivors", lambda proc, rd: [])
    monkeypatch.setattr(offload, "check_init", lambda ev: [])
    rec = {"status": "prepared"}
    offload.write_run_json(str(run_dir), rec)
    ctx = {"run_dir": str(run_dir), "model": "m", "max_thinking": 1000, "profile": "p", "src": str(tmp_path / "src"),
           "claude": {"real": "/usr/bin/true"}, "max_turns": 1, "record": rec}
    return ctx, run_dir


def test_launch_worker_status_running_and_tool_calls(monkeypatch, tmp_path):
    init = json.dumps({"type": "system", "subtype": "init"})
    res = json.dumps({"type": "result", "num_turns": 2, "result": "done", "usage": {}})
    ctx, run_dir = _fake_launch(monkeypatch, tmp_path, [init, _assistant(2, "a"), _assistant(0, "b"), _assistant(1, "c"), res])
    seen = []
    real_popen = subprocess.Popen

    def spy(*a, **kw):
        seen.append(dict(_read_run_json(run_dir)))  # run.json as of just before the worker starts
        return real_popen(*a, **kw)

    monkeypatch.setattr(offload.subprocess, "Popen", spy)
    assert _read_run_json(run_dir)["status"] == "prepared"
    w = offload.launch_worker(ctx, "brief", 30)
    assert w["saw_init"] and w["turns"] == 2
    assert seen[0]["status"] == "running" and isinstance(seen[0]["started_at"], float)
    assert ctx["record"]["tool_calls"] == 3  # text-only message not counted
    assert ctx["record"]["last_activity_at"] >= seen[0]["started_at"]
    assert _read_run_json(run_dir)["tool_calls"] >= 1  # at least the first (unthrottled) write landed
    assert [f for f in os.listdir(run_dir) if f.startswith("run.json")] == ["run.json"]


# ================================================================== v0.3: working like Claude

import http.client  # noqa: E402
import http.server  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402


# ------------------------------------------------------------------ worker env, PATH, preamble, turns

def test_defaults_v03():
    assert offload.DEFAULT_MAX_TURNS == 60
    assert offload.build_parser().parse_args(["run", "--repo", "r", "--brief", "b"]).max_turns == 60
    assert offload.DEFAULT_READ_HOME == (".rustup",)
    assert offload.WORKER_PREAMBLE == (
        "You are working in a copy of the repo, in the current directory. git works locally: there is no remote. "
        "There is no network except the model. Make temp files with `mktemp -d \"$TMPDIR/x.XXXXXX\"`. Write the "
        "code and tests the brief asks for, run the tests, commit if you like, and finish with a short summary.\n\n")


def test_clt_first_on_worker_path_only_when_root_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(offload, "CLT_BIN", "/usr/bin")  # exists and is root-owned
    env = offload.build_worker_env("/r", "m", "http://127.0.0.1:1")
    assert env["PATH"] == f"/usr/bin:{offload.SAFE_PATH}"
    monkeypatch.setattr(offload, "CLT_BIN", str(tmp_path))  # exists, owned by the test user
    assert offload.build_worker_env("/r", "m", "http://127.0.0.1:1")["PATH"] == offload.SAFE_PATH
    monkeypatch.setattr(offload, "CLT_BIN", str(tmp_path / "missing"))
    assert offload.build_worker_env("/r", "m", "http://127.0.0.1:1")["PATH"] == offload.SAFE_PATH
    # the helper's own git keeps SAFE_PATH
    assert f"PATH={offload.SAFE_PATH}" in offload.safe_git_argv(["status"], "/h", git_bin="/usr/bin/git")


def test_clt_constant_is_hardcoded():
    src = Path(offload.__file__).read_text()
    assert "xcode-select" not in src.replace("never taken from `xcode-select -p`", "")
    assert "/Library/Developer/CommandLineTools/usr/bin" in src


def test_registry_env_points_at_proxy():
    env = offload.build_worker_env("/r", "m", "http://127.0.0.1:5555")
    assert env["PIP_INDEX_URL"] == "http://127.0.0.1:5555/r/pypi/simple" and env["PIP_TRUSTED_HOST"] == "127.0.0.1"
    assert env["NPM_CONFIG_REGISTRY"] == "http://127.0.0.1:5555/r/npm/"
    assert env["CARGO_HOME"] == "/r/home/.cargo"
    assert (env["GOPROXY"], env["GOFLAGS"], env["GOSUMDB"], env["GOCACHE"]) == ("off", "-mod=mod", "off", "/r/tmp/gocache")
    assert "GOMODCACHE" not in env and "CARGO_NET_OFFLINE" not in env
    assert not [k for k in env if k.upper() in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")]
    assert "HTTPS_PROXY" not in Path(offload.__file__).read_text()


def test_gomodcache_only_when_allowed(tmp_path):
    mod = os.path.realpath(os.path.join(HOME, "go", "pkg", "mod"))
    env = offload.build_worker_env("/r", "m", "http://127.0.0.1:1", allow_read=[mod])
    assert env["GOMODCACHE"] == mod
    other = offload.build_worker_env("/r", "m", "http://127.0.0.1:1", allow_read=[str(tmp_path)])
    assert "GOMODCACHE" not in other


def test_write_registry_configs(tmp_path):
    offload.write_registry_configs(str(tmp_path), "http://127.0.0.1:5555")
    cargo = (tmp_path / "home" / ".cargo" / "config.toml").read_text()
    assert 'replace-with = "offload"' in cargo and 'registry = "sparse+http://127.0.0.1:5555/r/crates-index/"' in cargo
    mvn = (tmp_path / "home" / ".m2" / "settings.xml").read_text()
    assert "<mirrorOf>*</mirrorOf>" in mvn and "<url>http://127.0.0.1:5555/r/maven/</url>" in mvn


@needs_sandbox
@pytest.mark.skipif(not os.path.isdir(offload.CLT_BIN), reason="needs the Command Line Tools")
def test_integration_worker_shell_python_git_and_temp(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    env = offload.build_worker_env(ctx["run_dir"], "m", "http://127.0.0.1:1")
    offload.write_profile(ctx)

    def sb(*argv):
        return subprocess.run(["/usr/bin/sandbox-exec", "-f", ctx["profile"], *argv], cwd=ctx["src"], env=env,
                              capture_output=True, text=True, timeout=60)
    r = sb("/bin/zsh", "-c", "cat <<EOF\nheredoc ok\nEOF")
    assert r.returncode == 0 and "heredoc ok" in r.stdout, r.stderr
    r = sb("/bin/sh", "-c", 'mktemp -d "$TMPDIR/x.XXXXXX"')
    assert r.returncode == 0 and r.stdout.strip().startswith(os.path.join(ctx["run_dir"], "tmp") + "/x."), r.stderr
    r = sb("/bin/sh", "-c", "command -v python3 git; python3 -c 'import tempfile; print(tempfile.mkdtemp())'")
    assert r.returncode == 0 and offload.CLT_BIN + "/python3" in r.stdout and "xcrun" not in r.stderr, r.stderr
    offload.init_worker_git(ctx)
    r = sb("/bin/sh", "-c", "git status --short; git log --format=%s; echo x > n.txt; git add n.txt; "
                            "git commit -qm worker && git log --format=%s")
    assert r.returncode == 0 and "base" in r.stdout and "worker" in r.stdout and "xcrun" not in r.stderr, r.stderr


# ------------------------------------------------------------------ default read roots

def test_default_read_roots_skip_with_warning(tmp_path):
    home, outside = tmp_path / "h", tmp_path / "outside"
    for d in (home, outside, home / ".rustup"):
        d.mkdir(parents=True)
    (home / ".cargo").mkdir()
    roots, warnings = offload.default_read_roots(home=str(home))
    assert roots == [os.path.realpath(home / ".rustup")] and warnings == []
    (home / ".rustup").rmdir()
    (home / ".rustup").symlink_to(outside)  # leaves $HOME: fails validation
    roots, warnings = offload.default_read_roots(home=str(home))
    assert roots == [] and len(warnings) == 1 and ".rustup" in warnings[0]
    assert offload.default_read_roots(home=str(tmp_path / "empty")) == ([], [])  # absent roots are silent


def test_default_read_roots_both_present(tmp_path):
    (tmp_path / ".rustup").mkdir()
    (tmp_path / ".cargo" / "registry").mkdir(parents=True)
    (tmp_path / ".cargo" / "git").mkdir()
    roots, warnings = offload.default_read_roots(home=str(tmp_path))
    assert roots == [os.path.realpath(tmp_path / ".rustup")]
    assert warnings == []  # ~/.cargo/registry and ~/.cargo/git are opt-in, never defaults


def test_prepare_run_adds_defaults_and_keeps_warnings(tmp_path, repo, monkeypatch):
    extra = os.path.realpath(tmp_path / "d")
    os.mkdir(extra)
    monkeypatch.setattr(offload, "default_read_roots", lambda home=None, repo_root=None: ([extra], ["w1"]))
    _, ctx = prepare(tmp_path, repo)
    assert extra in ctx["allow_read"] and ctx["warnings"] == ["w1"]
    assert ctx["record"]["warnings"] == ["w1"] and extra in ctx["record"]["allow_read"]
    assert f'(subpath "{extra}")' in Path(ctx["profile"]).read_text()


# ------------------------------------------------------------------ worker-local git, commit_work

def test_init_worker_git_and_commit_work_removes_it_first(tmp_path, repo, monkeypatch):
    _, ctx = prepare(tmp_path, repo)
    src = Path(ctx["src"])
    offload.init_worker_git(ctx)
    assert (src / ".git").is_dir()
    assert git(src, "log", "--format=%s").strip() == "base"
    assert git(src, "status", "--porcelain").strip() == ""
    assert git(src, "config", "user.name").strip() == "offload"
    (src / "new.txt").write_text("n\n")
    seen = []
    real = offload.safe_git

    def spy(args, home, *a, **k):
        if "add" in args:
            seen.append((src / ".git").exists() or (src / ".git").is_symlink())
        return real(args, home, *a, **k)
    monkeypatch.setattr(offload, "safe_git", spy)
    offload.commit_work(ctx)
    assert seen == [False]  # src/.git was gone before `add -A`
    assert not (src / ".git").exists()
    assert "new.txt" in git(ctx["git_dir"], "show", "--stat", "--format=", "work")


def test_commit_work_removes_symlink_or_file_dot_git(tmp_path, repo):
    _, ctx = prepare(tmp_path, repo)
    src = Path(ctx["src"])
    (src / ".git").symlink_to(tmp_path)
    offload.commit_work(ctx)
    assert not os.path.lexists(src / ".git")


# ------------------------------------------------------------------ sensitive paths

def test_sensitive_paths_v03_additions():
    names = ["requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
             "settings.gradle", "settings.gradle.kts", ".pypirc", "sub/requirements-dev.txt"]
    assert offload.sensitive_paths(names + ["plain.py", "README.md"]) == names


# ------------------------------------------------------------------ registry proxy

class FakeRegistry:
    """A local plain-HTTP upstream. `pages` maps a path to (status, headers, body)."""

    def __init__(self, delay=0.0):
        self.seen = []
        self.pages = {}
        self.active = self.peak = 0
        self.delay = delay
        self._lock = threading.Lock()
        reg = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _any(self):
                with reg._lock:
                    reg.active += 1
                    reg.peak = max(reg.peak, reg.active)
                    reg.seen.append((self.command, self.path, dict(self.headers)))
                try:
                    time.sleep(reg.delay)
                    status, hdrs, body = reg.pages.get(self.path.split("?")[0], (404, {}, b"nope"))
                    self.send_response(status)
                    for k, v in hdrs.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(body)
                finally:
                    with reg._lock:
                        reg.active -= 1

            do_GET = do_HEAD = _any

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def fake_connector(reg):
    return lambda host, port, scheme: http.client.HTTPConnection("127.0.0.1", reg.port, timeout=10)


def test_routes_in_design_table():
    assert offload.REGISTRY_ROUTES == {
        "/r/pypi/": "https://pypi.org/", "/r/pyfiles/": "https://files.pythonhosted.org/",
        "/r/npm/": "https://registry.npmjs.org/", "/r/crates-index/": "https://index.crates.io/",
        "/r/crates-static/": "https://static.crates.io/", "/r/maven/": "https://repo.maven.apache.org/maven2/"}
    assert (offload.PROXY_MAX_CONNS, offload.PROXY_MAX_UPSTREAM) == (32, 8)
    assert (offload.PROXY_CLIENT_TIMEOUT, offload.PROXY_HEADER_DEADLINE) == (30, 60)
    assert offload.PROXY_RUN_BUDGET == 4 * 1024 ** 3 and offload.PROXY_REWRITE_CAP == 64 * 1024 * 1024


@pytest.fixture
def reg_proxy(tmp_path):
    reg = FakeRegistry()
    made = []

    def make(**kw):
        routes = kw.pop("routes", None) or {
            "/r/pypi/": f"http://127.0.0.1:{reg.port}/", "/r/pyfiles/": f"http://localhost:{reg.port}/",
            "/r/npm/": f"http://127.0.0.1:{reg.port}/npm/", "/r/crates-index/": f"http://127.0.0.1:{reg.port}/idx/",
            "/r/crates-static/": f"http://localhost:{reg.port}/static/"}
        px = offload.EgressProxy("http://127.0.0.1:9", str(tmp_path / "rej.log"), routes=routes,
                                 connector=kw.pop("connector", fake_connector(reg)), **kw)
        made.append(px)
        return px, px.start()
    yield reg, make, tmp_path / "rej.log"
    for px in made:
        px.stop()
    reg.close()


def get(port, path, method="GET", headers=None, body=None):
    c, r = http_req(port, method, path, body, headers)
    data = r.read()
    c.close()
    return r, data


def test_registry_get_rebuilds_headers_and_rewrites_pypi(reg_proxy):
    reg, make, log = reg_proxy
    px, port = make()
    reg.pages["/simple/pip/"] = (200, {"Content-Type": "text/html"},
                                 b'<a href="https://files.pythonhosted.org/packages/ab/pip-1.whl">x</a>')
    r, data = get(port, "/r/pypi/simple/pip/?x=1", headers={
        "Authorization": "Bearer s", "Cookie": "c=1", "X-Evil": "1", "Accept": "text/html",
        "Accept-Encoding": "gzip", "If-None-Match": '"e"', "If-Modified-Since": "Mon, 01 Jan 2024 00:00:00 GMT"})
    assert r.status == 200 and r.getheader("Content-Length") == str(len(data))
    assert data == f'<a href="http://127.0.0.1:{port}/r/pyfiles/packages/ab/pip-1.whl">x</a>'.encode()
    method, path, hdrs = reg.seen[0]
    assert (method, path) == ("GET", "/simple/pip/?x=1")
    low = {k.lower(): v for k, v in hdrs.items()}
    assert set(low) == {"host", "user-agent", "accept", "accept-encoding", "if-none-match", "if-modified-since",
                        "connection"}
    assert low["user-agent"] == "offload" and low["accept-encoding"] == "identity" and low["accept"] == "text/html"
    assert not log.exists()


def test_registry_rewrites_npm_crates_and_streams_binaries(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make()
    reg.pages["/npm/left-pad"] = (200, {"Content-Type": "application/vnd.npm.install-v1+json"},
                                  b'{"tarball":"https://registry.npmjs.org/left-pad/-/left-pad-1.tgz"}')
    r, data = get(port, "/r/npm/left-pad")
    assert data == f'{{"tarball":"http://127.0.0.1:{port}/r/npm/left-pad/-/left-pad-1.tgz"}}'.encode()
    reg.pages["/idx/config.json"] = (200, {"Content-Type": "application/json"},
                                     b'{"dl":"https://static.crates.io/crates","api":"https://crates.io"}')
    r, data = get(port, "/r/crates-index/config.json")
    assert json.loads(data) == {"dl": f"http://127.0.0.1:{port}/r/crates-static/crates", "api": None}
    blob = b"https://registry.npmjs.org/ stays\x00"
    reg.pages["/npm/left-pad/-/left-pad-1.tgz"] = (200, {"Content-Type": "application/octet-stream"}, blob)
    r, data = get(port, "/r/npm/left-pad/-/left-pad-1.tgz")
    assert data == blob  # not rewritten
    r, data = get(port, "/r/npm/left-pad/-/left-pad-1.tgz", method="HEAD")
    assert r.status == 200 and data == b"" and r.getheader("Content-Length") == str(len(blob))


@pytest.mark.parametrize("method", ["PUT", "POST", "DELETE", "PATCH", "CONNECT", "OPTIONS", "FOO"])
def test_registry_other_methods_403(reg_proxy, method):
    reg, make, log = reg_proxy
    px, port = make()
    c, r = http_req(port, method, "/r/npm/x", b"{}" if method in ("PUT", "POST", "PATCH") else None)
    assert r.status == 403
    c.close()
    assert reg.seen == [] and method in log.read_text()


def test_registry_connect_authority_form_403(reg_proxy):
    reg, make, log = reg_proxy
    px, port = make()
    c, r = http_req(port, "CONNECT", "pypi.org:443")
    assert r.status == 403
    c.close()
    assert reg.seen == []


def test_registry_get_with_body_refused(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make()
    r, _ = get(port, "/r/pypi/simple/pip/", body=b"data", headers={"Content-Length": "4"})
    assert r.status == 403
    r, _ = get(port, "/r/pypi/simple/pip/", headers={"Transfer-Encoding": "chunked"})
    assert r.status == 403
    assert reg.seen == []


@pytest.mark.parametrize("target", [
    "http://pypi.org/simple/pip/", "http://example.com/", "https://evil.example/r/pypi/simple/",
    "/r/pypi/../../x", "/r/pypi/a/../b", "/r/pypi/a//b", "/r/pypi/a%2e%2e/b", "/r/pypi/%2e%2e%2fx",
    "/r/pypi/a%5cb", "/r/pypi/a\\b", "/r/pypi/%2Fblob", "/r/pypi/a%00b", "/r/pypi/a%0ab", "/r/pypi/%ff", "/r/nope/", "/r/pypi",
    "/r/", "/r", "/R/pypi/simple/", "/v1/messages", "/r/pypi/x?a=\\b", "/r/pypi/x?a=\x01"])
def test_registry_path_and_target_rules_403(reg_proxy, target):
    reg, make, log = reg_proxy
    px, port = make()
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode("latin-1"))
    status = s.recv(100).split(b"\r\n")[0]
    s.close()
    assert b" 403 " in status, status
    assert reg.seen == []


def test_registry_upstream_url_is_constant_base_plus_requoted_rest(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make()
    reg.pages["/simple/a%20b/@evil.com/"] = (200, {"Content-Type": "text/plain"}, b"ok")
    r, data = get(port, "/r/pypi/simple/a%20b/@evil.com/")
    assert r.status == 200 and data == b"ok"
    assert reg.seen[0][1] == "/simple/a%20b/@evil.com/"
    assert reg.seen[0][2]["Host"] == f"127.0.0.1:{reg.port}"  # the table's host, never the worker's
    r, _ = get(port, "/r/pypi/simple/x/", headers={"Host": "evil.example"})
    assert reg.seen[-1][2]["Host"] == f"127.0.0.1:{reg.port}"


def test_registry_redirects_rewritten_or_502(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make()
    reg.pages["/to-files"] = (302, {"Location": f"http://localhost:{reg.port}/packages/p.whl"}, b"")
    reg.pages["/to-evil"] = (302, {"Location": "https://evil.example/x"}, b"")
    reg.pages["/relative"] = (301, {"Location": "/simple/pip/"}, b"")
    r, _ = get(port, "/r/pypi/to-files")
    assert r.status == 302 and r.getheader("Location") == f"http://127.0.0.1:{port}/r/pyfiles/packages/p.whl"
    r, _ = get(port, "/r/pypi/to-evil")
    assert r.status == 502 and r.getheader("Location") is None
    r, _ = get(port, "/r/pypi/relative")
    assert r.status == 301 and r.getheader("Location") == f"http://127.0.0.1:{port}/r/pypi/simple/pip/"
    assert len(reg.seen) == 3  # nothing followed


def test_registry_non_global_address_is_502():
    def fake(addr):
        return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port))]
    for bad in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "100.64.0.1"):
        with pytest.raises(offload.UpstreamBlocked):
            offload.connect_registry("pypi.org", resolver=fake(bad))
    mixed = lambda host, port, **kw: [(2, 1, 6, "", ("8.8.8.8", port)), (2, 1, 6, "", ("10.0.0.5", port))]
    with pytest.raises(offload.UpstreamBlocked):
        offload.connect_registry("pypi.org", resolver=mixed)
    v6 = lambda host, port, **kw: [(socket.AF_INET6, 1, 6, "", ("::1", port, 0, 0))]
    with pytest.raises(offload.UpstreamBlocked):
        offload.connect_registry("pypi.org", resolver=v6)
    conn = offload.connect_registry("pypi.org", resolver=fake("151.101.0.223"))
    assert conn.host == "pypi.org" and conn.port == 443  # not connected yet; SNI/verification name is the host


def test_registry_proxy_returns_502_for_blocked_address(reg_proxy):
    reg, make, _ = reg_proxy
    private = lambda host, port, **kw: [(2, 1, 6, "", ("10.0.0.5", port))]
    px, port = make(connector=lambda h, p, s: offload.connect_registry(h, p, resolver=private))
    r, _ = get(port, "/r/pypi/simple/pip/")
    assert r.status == 502 and reg.seen == []


def test_registry_rewrite_cap_and_other_responses_stream(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make(rewrite_cap=50)
    reg.pages["/simple/big/"] = (200, {"Content-Type": "text/html"}, b"x" * 51)
    reg.pages["/simple/ok/"] = (200, {"Content-Type": "text/html"}, b"x" * 50)
    reg.pages["/blob"] = (200, {"Content-Type": "application/octet-stream"}, b"y" * 500)
    assert get(port, "/r/pypi/simple/big/")[0].status == 502
    assert get(port, "/r/pypi/simple/ok/")[0].status == 200
    r, data = get(port, "/r/pypi/blob")
    assert r.status == 200 and data == b"y" * 500


def test_registry_run_download_budget(reg_proxy):
    reg, make, log = reg_proxy
    px, port = make(run_budget=100)
    reg.pages["/blob"] = (200, {"Content-Type": "application/octet-stream"}, b"z" * 80)
    assert get(port, "/r/pypi/blob")[0].status == 200
    assert get(port, "/r/pypi/blob")[0].status == 200  # 80 < 100 when it started
    r, _ = get(port, "/r/pypi/blob")
    assert r.status == 403 and len(reg.seen) == 2 and "budget" in log.read_text()
    assert get(port, "/v1/messages")[0].status == 403  # unrelated rules unchanged


def test_proxy_connection_cap_closes_extras_and_logs(reg_proxy):
    reg, make, log = reg_proxy
    px, port = make(max_conns=4)
    held = [socket.create_connection(("127.0.0.1", port), timeout=3) for _ in range(4)]
    time.sleep(0.2)
    extra = socket.create_connection(("127.0.0.1", port), timeout=3)
    assert extra.recv(1) == b""  # accepted and closed at once
    extra.close()
    assert "connection limit" in log.read_text()
    held.pop().close()
    time.sleep(0.3)
    reg.pages["/simple/pip/"] = (200, {"Content-Type": "text/plain"}, b"ok")
    assert get(port, "/r/pypi/simple/pip/")[1] == b"ok"  # a freed slot works again
    for c in held:
        c.close()


def test_registry_upstream_concurrency_cap():
    reg = FakeRegistry(delay=0.3)
    reg.pages["/blob"] = (200, {"Content-Type": "application/octet-stream"}, b"b")
    tmp = Path(__import__("tempfile").mkdtemp())
    px = offload.EgressProxy("http://127.0.0.1:9", str(tmp / "l"), routes={"/r/pypi/": f"http://127.0.0.1:{reg.port}/"},
                             connector=fake_connector(reg), max_upstream=2)
    port = px.start()
    try:
        results = []
        ts = [threading.Thread(target=lambda: results.append(get(port, "/r/pypi/blob")[0].status)) for _ in range(6)]
        [t.start() for t in ts]
        [t.join(20) for t in ts]
        assert results == [200] * 6 and reg.peak <= 2
    finally:
        px.stop()
        reg.close()


def test_proxy_header_deadline_closes_slow_client(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make(header_deadline=0.5)
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(b"GET /r/pypi/simple/pip/ HTTP/1.1\r\nHost: x\r\n")  # headers never finish
    t0 = time.monotonic()
    assert s.recv(10) == b""
    assert time.monotonic() - t0 < 4
    s.close()
    assert reg.seen == []


def test_proxy_client_socket_timeout_is_30s():
    px = offload.EgressProxy("http://127.0.0.1:9", os.devnull)
    try:
        assert px.httpd.RequestHandlerClass.timeout == 30
    finally:
        px.httpd.server_close()


@needs_sandbox
def test_integration_cmd_run_inits_worker_git_writes_configs_and_reports_warnings(tmp_path, repo, monkeypatch, capsys):
    ohome = os.path.realpath(tmp_path / "ohome")
    brief = tmp_path / "brief.md"
    brief.write_text("do it\n")
    seen = {}
    monkeypatch.setattr(offload, "resolve_claude", lambda allow=False: fake_claude())
    monkeypatch.setattr(offload, "default_read_roots", lambda home=None, repo_root=None: ([], ["default root skipped"]))
    monkeypatch.setattr(offload, "run_selftest", lambda c: (True, []))

    def fake_launch(ctx, brief_text, timeout_s):
        src, home = Path(ctx["src"]), Path(ctx["run_dir"]) / "home"
        seen["log"] = git(src, "log", "--format=%s").strip()
        seen["cargo"] = (home / ".cargo" / "config.toml").read_text()
        seen["mvn"] = (home / ".m2" / "settings.xml").read_text()
        seen["port"] = ctx["proxy_port"]
        return {"init_problems": [], "saw_init": True, "timed_out": False, "result": {"is_error": False}, "usage": {},
                "text": "", "returncode": 0, "turns": 1, "survivors_killed": []}
    monkeypatch.setattr(offload, "launch_worker", fake_launch)
    assert offload.main(["--home", ohome, "run", "--repo", str(repo), "--brief", str(brief), "--slug", "job"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["warnings"] == ["default root skipped"] and out["status"] == "ok"
    assert seen["log"] == "base"
    assert f"sparse+http://127.0.0.1:{seen['port']}/r/crates-index/" in seen["cargo"]
    assert f"http://127.0.0.1:{seen['port']}/r/maven/" in seen["mvn"]


# ------------------------------------------------------------------ fix pass (Opus review of 7f02271)

def test_registry_304_passes_through(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make()
    reg.pages["/blob"] = (304, {"ETag": '"e1"', "Last-Modified": "Mon, 01 Jan 2024 00:00:00 GMT"}, b"")
    r, data = get(port, "/r/pypi/blob", headers={"If-None-Match": '"e1"'})
    assert r.status == 304 and data == b"" and r.getheader("ETag") == '"e1"'
    assert r.getheader("Last-Modified")


def test_registry_relative_location_does_not_double_base_path(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make(routes={"/r/maven/": f"http://127.0.0.1:{reg.port}/maven2/"})
    reg.pages["/maven2/a/b"] = (302, {"Location": "c"}, b"")
    r, _ = get(port, "/r/maven/a/b")
    assert r.status == 302 and r.getheader("Location") == f"http://127.0.0.1:{port}/r/maven/a/c"


def test_registry_budget_counts_rewrite_bytes_including_oversize(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make(rewrite_cap=50)
    reg.pages["/simple/big/"] = (200, {"Content-Type": "text/html"}, b"x" * 60)
    reg.pages["/simple/ok/"] = (200, {"Content-Type": "text/html"}, b"y" * 40)
    assert get(port, "/r/pypi/simple/big/")[0].status == 502
    assert px.downloaded == 51  # cap + 1 bytes were read before the cap check
    assert get(port, "/r/pypi/simple/ok/")[0].status == 200
    assert px.downloaded == 91  # counted once, not again when sent


@pytest.mark.parametrize("addr", ["::127.0.0.1", "64:ff9b::7f00:1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::10.0.0.1"])
def test_embedded_ipv4_in_ipv6_is_blocked(addr):
    res = lambda host, port, **kw: [(socket.AF_INET6, 1, 6, "", (addr, port, 0, 0))]
    with pytest.raises(offload.UpstreamBlocked):
        offload.connect_registry("pypi.org", resolver=res)


def test_connect_tries_each_vetted_address(monkeypatch):
    tried = []

    def fake_cc(addr, timeout=None):
        tried.append(addr)
        if addr[0] == "151.101.0.1":
            raise ConnectionRefusedError()
        raise ssl.SSLError("stop here")  # past the connect loop; enough to prove the second address was used
    import ssl as _ssl
    globals()["ssl"] = _ssl
    res = lambda host, port, **kw: [(2, 1, 6, "", ("151.101.0.1", port)), (2, 1, 6, "", ("151.101.0.2", port))]
    monkeypatch.setattr(offload.socket, "create_connection", fake_cc)
    conn = offload.connect_registry("pypi.org", resolver=res)
    with pytest.raises(OSError):
        conn.connect()
    assert tried == [("151.101.0.1", 443), ("151.101.0.2", 443)]


import ssl  # noqa: E402


def test_pinned_connect_verifies_certificate_and_uses_vetted_ip(monkeypatch, tmp_path):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                    "-subj", "/CN=pypi.org", "-days", "1"], check=True, capture_output=True)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(str(crt), str(key))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def serve():
        try:
            c, _ = srv.accept()
            try:
                sctx.wrap_socket(c, server_side=True)
            except OSError:
                pass
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True).start()
    real_cc, seen = socket.create_connection, []

    def fake_cc(addr, timeout=None):
        seen.append(addr)
        return real_cc(("127.0.0.1", srv.getsockname()[1]), timeout)
    monkeypatch.setattr(offload.socket, "create_connection", fake_cc)
    cctx = ssl.create_default_context()  # default verification: the self-signed cert is not trusted
    names, orig = [], cctx.wrap_socket
    cctx.wrap_socket = lambda sock, **kw: (names.append(kw.get("server_hostname")), orig(sock, **kw))[1]
    res = lambda host, port, **kw: [(2, 1, 6, "", ("151.101.0.223", port))]
    conn = offload.connect_registry("pypi.org", resolver=res, ssl_context=cctx, timeout=5)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            conn.connect()
    finally:
        srv.close()
    assert seen == [("151.101.0.223", 443)] and names == ["pypi.org"]


def test_registry_pipelined_second_request_is_not_served(reg_proxy):
    reg, make, _ = reg_proxy
    px, port = make()
    reg.pages["/simple/pip/"] = (200, {"Content-Type": "text/plain"}, b"ok")
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    one = b"GET /r/pypi/simple/pip/ HTTP/1.1\r\nHost: x\r\n\r\n"
    s.sendall(one + one)
    buf = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    assert buf.count(b"HTTP/1.1 ") == 1 and len(reg.seen) == 1


def test_sensitive_lists_fix_pass():
    names = ["package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "go.sum",
             "poetry.lock", "uv.lock", "Pipfile", "Pipfile.lock", ".pnpmfile.cjs", "gradle-wrapper.properties",
             ".cargo/config.toml", "a/.mvn/maven.config", ".mvn/jvm.config"]
    assert offload.sensitive_paths(names) == names
    assert {".cargo", ".mvn"} <= offload.SENSITIVE_DIRS


def test_registry_request_log_and_summary(reg_proxy):
    reg, make, log = reg_proxy
    px, port = make()
    reg.pages["/npm/left-pad"] = (200, {"Content-Type": "application/json"}, b"{}")
    get(port, "/r/npm/left-pad")
    get(port, "/r/npm/left-pad/-/left-pad-1.tgz")
    get(port, "/r/npm/other")
    get(port, "/r/pypi/simple/pip/")
    assert px.request_log == str(log.parent / "registry_requests.log")
    lines = Path(px.request_log).read_text().splitlines()
    assert len(lines) == 4
    assert "GET /r/npm/left-pad 200 2" in lines[0] and " 404 " in lines[1]
    assert px.registry_summary() == {"count": 4, "packages": ["left-pad", "other", "simple"]}
    for i in range(60):
        px.log_request("GET", f"/r/npm/p{i}", 200, 0, f"p{i}")
    assert len(px.registry_summary()["packages"]) == 50 and px.registry_summary()["count"] == 64
    px.log_request("GET", "/r/npm/a\x1b[31mb", 200, 0, "a\x1b[31mb")
    assert "\x1b" not in Path(px.request_log).read_text()


@needs_sandbox
def test_cmd_run_reports_registry_requests(tmp_path, repo, monkeypatch, capsys):
    ohome = os.path.realpath(tmp_path / "ohome")
    brief = tmp_path / "brief.md"
    brief.write_text("do it\n")
    monkeypatch.setattr(offload, "resolve_claude", lambda allow=False: fake_claude())
    monkeypatch.setattr(offload, "run_selftest", lambda c: (True, []))
    monkeypatch.setattr(offload, "launch_worker", lambda *a, **k: {
        "init_problems": [], "saw_init": True, "timed_out": False, "result": {"is_error": False}, "usage": {},
        "text": "", "returncode": 0, "turns": 1, "survivors_killed": []})
    assert offload.main(["--home", ohome, "run", "--repo", str(repo), "--brief", str(brief), "--slug", "job"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["registry_requests"] == {"count": 0, "packages": []}
    rec = json.loads((Path(ohome) / "runs" / out["run_id"] / "run.json").read_text())
    assert rec["registry_requests"] == {"count": 0, "packages": []}
