#!/usr/bin/env python3
"""Delegate a build task to a sandboxed, cheaper model (Claude Code on Ollama Cloud).

macOS only (sandbox-exec). The worker model and everything it writes are untrusted.
See ../DESIGN.md for the threat model and the run flow.

  offload.py [--home DIR] run --repo R --brief F [--slug S] [--model M] [--max-turns N]
                              [--timeout-min T] [--max-thinking-tokens N] [--allow-read PATH]...
                              [--base-url URL] [--allow-untested-claude]
  offload.py [--home DIR] test --run ID [--timeout-s N] -- CMD...
  offload.py [--home DIR] diff --run ID [-- PATH...]
  offload.py [--home DIR] list
  offload.py [--home DIR] status --run ID
  offload.py [--home DIR] discard --run ID

OFFLOAD_HOME is ~/.cache/offload unless --home DIR is given (used by the tests). It never
affects the base URL, which comes only from the default or ~/.config/offload/config.json.
Every subcommand prints JSON to stdout; `diff` prints the diff text and then a JSON trailer.
"""
import argparse
import hashlib
import http.client
import json
import os
import pwd
import re
import secrets
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TESTED_CLAUDE_VERSIONS = {"2.1.312"}
DEFAULT_MODEL = "kimi-k3:cloud"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MAX_TURNS = 40
DEFAULT_TIMEOUT_MIN = 45
DEFAULT_MAX_THINKING = 8000
UNTRUSTED_LIMIT = 4096
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
ALLOWED_TOOLS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep")
DENIED_TOOLS = ("WebFetch", "WebSearch", "Agent", "Skill")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# USD per 1M tokens: (input, cache read, output)
PRICES = {"kimi-k3:cloud": (3.0, 0.30, 15.0)}
# --allow-read is an allowlist: paths under these (relative to $HOME) or under these repo subdirs.
ALLOW_READ_HOME = ("go/pkg/mod", ".cargo/registry", ".cargo/git", ".rustup", ".m2/repository",
                   ".gradle/caches", ".npm/_cacache")
ALLOW_READ_REPO = ("node_modules", "vendor", ".venv")
PROXY_ALLOWED_PATHS = ("/v1/messages", "/v1/messages/count_tokens")
PROXY_MAX_BODY = 32 * 1024 * 1024
PROXY_UPSTREAM_TIMEOUT = 900
OUTPUT_RING = 64 * 1024
SENSITIVE_DIRS = {".claude", ".github", ".husky", ".githooks", ".vscode", ".idea"}
SENSITIVE_BASENAMES = {"Makefile", "package.json", "conftest.py", "setup.py", "build.rs", ".envrc",
                       ".pre-commit-config.yaml", "pyproject.toml", "setup.cfg", "sitecustomize.py",
                       "usercustomize.py", "Justfile", "Rakefile", "Dockerfile", ".npmrc"}
SENSITIVE_PREFIXES = ("lefthook", "docker-compose", ".yarnrc")
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")


class OffloadError(Exception):
    """A validation or safety failure. Reported as JSON with status 'error'."""


# ---------------------------------------------------------------- basic helpers

def real_home():
    """The invoking user's home, from the password database and not from $HOME."""
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def offload_home(override=None):
    return os.path.realpath(override or os.path.join(real_home(), ".cache", "offload"))


def validate_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise OffloadError(f"invalid slug {slug!r}: must match {SLUG_RE.pattern}")
    return slug


def run_id_for(repo_root, slug):
    validate_slug(slug)
    return f"{hashlib.sha256(repo_root.encode()).hexdigest()[:8]}-{slug}"


def default_slug(brief_path):
    stem = os.path.splitext(os.path.basename(brief_path))[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")[:40].strip("-")
    if not SLUG_RE.match(slug):
        slug = "task-" + hashlib.sha256(os.path.abspath(brief_path).encode()).hexdigest()[:8]
    return slug


def is_under(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def sanitize_untrusted_ex(text, limit=UNTRUSTED_LIMIT):
    """Strip ANSI escapes and control characters, mark format (Cf) characters as visible <U+XXXX>
    tokens, then cap at `limit` UTF-8 bytes. Returns (text, {'truncated', 'format_chars'})."""
    text = ANSI_RE.sub("", text or "")
    kept = []
    fmt = 0
    for ch in text:
        if ch in "\n\t":
            kept.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat == "Cf":
            fmt += 1
            kept.append("\u27e8U+%04X\u27e9" % ord(ch))
        elif cat in ("Cc", "Cs", "Co", "Cn"):
            continue
        else:
            kept.append(ch)
    out = "".join(kept)
    raw = out.encode("utf-8")
    truncated = len(raw) > limit
    if truncated:
        marker = "\n[truncated]"
        out = raw[: limit - len(marker.encode())].decode("utf-8", errors="ignore") + marker
    return out, {"truncated": truncated, "format_chars": fmt}


def sanitize_untrusted(text, limit=UNTRUSTED_LIMIT):
    return sanitize_untrusted_ex(text, limit)[0]


def estimate_cost(model, usage):
    """USD estimate from the price table, or None for a model with no known price."""
    price = PRICES.get(model)
    if not price:
        return None
    p_in, p_cache, p_out = price
    cost = (usage.get("input", 0) * p_in + usage.get("cache_read", 0) * p_cache
            + usage.get("output", 0) * p_out) / 1_000_000
    return round(cost, 6)


def sensitive_paths(paths):
    """Paths whose change can run code outside the sandbox. Entries are worker-controlled: sanitize."""
    out = []
    for p in paths:
        parts = p.split("/")
        base = parts[-1]
        if (any(d in SENSITIVE_DIRS for d in parts[:-1]) or any(x.startswith(".git") for x in parts)
                or base in SENSITIVE_BASENAMES or base.startswith(SENSITIVE_PREFIXES)
                or base.endswith(".sh") or base.endswith(".pth")):
            out.append(sanitize_untrusted(p, 512))
    return out


# ------------------------------------------------------------------- base URL

def resolve_base_url(requested=None, config_path=None):
    """Return {'url', 'host', 'port'}. Never reads the environment for the URL.

    The default is local ollama. Any other URL must be listed in `base_urls` in
    ~/.config/offload/config.json, and a non-loopback host must use https.
    """
    url = (requested or DEFAULT_BASE_URL).rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.path or parts.query or parts.fragment:
        raise OffloadError(f"invalid base URL {url!r}")
    host = parts.hostname
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise OffloadError(f"invalid port in base URL {url!r}")
    if url != DEFAULT_BASE_URL:
        path = config_path or os.path.join(real_home(), ".config", "offload", "config.json")
        try:
            with open(path) as f:
                allowed = json.load(f).get("base_urls", [])
        except (OSError, ValueError, AttributeError):
            allowed = []
        if url not in [str(a).rstrip("/") for a in allowed if isinstance(a, str)]:
            raise OffloadError(f"base URL {url!r} is not in the base_urls allowlist in {path}")
    if host not in LOOPBACK_HOSTS and parts.scheme != "https":
        raise OffloadError(f"base URL {url!r}: a non-loopback host requires https")
    return {"url": url, "host": host, "port": port}


# ------------------------------------------------------------------- safe git

def safe_git_argv(args, home, git_bin=None):
    """The one place a git command line is built. Always env -i, no hooks, no global config."""
    git_bin = git_bin or shutil.which("git", path=SAFE_PATH) or "/usr/bin/git"
    env_prefix = ["/usr/bin/env", "-i", f"PATH={SAFE_PATH}", f"HOME={home}",
                  "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1", "GIT_TERMINAL_PROMPT=0"]
    git_prefix = [git_bin, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                  "-c", "core.attributesFile=/dev/null", "-c", "core.excludesFile=/dev/null",
                  "-c", "protocol.file.allow=always", "--no-pager"]
    args = list(args)
    if args and "diff" in args[:6]:
        i = args.index("diff")
        args[i + 1:i + 1] = ["--no-ext-diff", "--no-textconv"]
    return env_prefix + git_prefix + args


def safe_git(args, home, cwd=None, check=True, text=True):
    """Run git through the wrapper. Every git call in this file goes through here."""
    p = subprocess.run(safe_git_argv(args, home), cwd=cwd, capture_output=True, shell=False, text=text,
                       errors="replace" if text else None)
    if check and p.returncode != 0:
        err = p.stderr if text else p.stderr.decode(errors="replace")
        raise OffloadError(f"git {' '.join(map(str, args[:3]))} failed: {err.strip()[:500]}")
    return p


def snapshot_refs(repo, home):
    out = safe_git(["-C", repo, "for-each-ref", "--format=%(refname) %(objectname)"], home).stdout
    refs = {}
    for line in out.splitlines():
        name, _, sha = line.partition(" ")
        if name:
            refs[name] = sha
    return refs


def compare_refs(before, after, slug):
    """Return refs that changed, other than the one new refs/heads/offload/<slug>."""
    allowed = f"refs/heads/offload/{slug}"
    bad = []
    for name in sorted(set(before) | set(after)):
        if name == allowed and name not in before and name in after:
            continue
        if before.get(name) != after.get(name):
            bad.append({"ref": name, "before": before.get(name), "after": after.get(name)})
    return bad


# ----------------------------------------------------------------- claude binary

def resolve_claude(allow_untested=False):
    link = shutil.which("claude")
    if not link:
        raise OffloadError("claude binary not found on PATH")
    real = os.path.realpath(link)
    p = subprocess.run([real, "--version"], capture_output=True, text=True, shell=False, cwd="/",
                       env={"PATH": SAFE_PATH, "HOME": "/var/empty"}, timeout=30)
    version = p.stdout.strip().split()[0] if p.stdout.strip() else ""
    if version not in TESTED_CLAUDE_VERSIONS and not allow_untested:
        raise OffloadError(f"claude version {version or '?'} is not in TESTED_CLAUDE_VERSIONS "
                           f"{sorted(TESTED_CLAUDE_VERSIONS)}; pass --allow-untested-claude to override")
    return {"link": os.path.abspath(link), "real": real, "version": version}


def build_claude_argv(claude_real, max_turns):
    return [claude_real, "-p", "--bare", "--strict-mcp-config", "--setting-sources", "",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose",
            "--max-turns", str(int(max_turns)), "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", ",".join(ALLOWED_TOOLS), "--allowedTools", ",".join(ALLOWED_TOOLS),
            "--disallowedTools", ",".join(DENIED_TOOLS), "--disable-slash-commands"]


def build_worker_env(run_dir, model, base_url, max_thinking=DEFAULT_MAX_THINKING, lang=None):
    """Exactly the spec's key set. Nothing is inherited from os.environ except an optional LANG."""
    tmp = os.path.join(run_dir, "tmp")
    env = {
        "PATH": SAFE_PATH,
        "HOME": os.path.join(run_dir, "home"),
        "LANG": lang or os.environ.get("LANG") or "en_US.UTF-8",
        "TMPDIR": tmp, "CLAUDE_CODE_TMPDIR": tmp, "TMPPREFIX": tmp,
        "CLAUDE_CONFIG_DIR": os.path.join(run_dir, "config"),
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_API_KEY": "ollama",
        "ANTHROPIC_MODEL": model, "ANTHROPIC_SMALL_FAST_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model, "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "MAX_THINKING_TOKENS": str(int(max_thinking)),
    }
    return env


WORKER_ENV_KEYS = frozenset(build_worker_env("/x", "m", "http://localhost:11434").keys())


def check_init(event, allowed_tools=ALLOWED_TOOLS):
    """Return a list of violations in the stream's system/init event (empty means fine)."""
    problems = []
    if event.get("mcp_servers") != []:
        problems.append(f"mcp_servers is {event.get('mcp_servers')!r}, expected []")
    if event.get("permissionMode") != "dontAsk":
        problems.append(f"permissionMode is {event.get('permissionMode')!r}, expected 'dontAsk'")
    tools = event.get("tools")
    if not isinstance(tools, list):
        problems.append("tools missing from init event")
    else:
        extra = sorted(str(t) for t in tools if t not in set(allowed_tools))
        if extra:
            problems.append(f"unexpected tools: {extra}")
    return problems


# ---------------------------------------------------------------- sandbox profile

def sbpl_str(path):
    """A quoted SBPL string literal. Rejects characters that cannot be escaped safely."""
    if '"' in path or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        raise OffloadError(f"path cannot be used in a sandbox profile (quote, backslash or control char): {path!r}")
    return f'"{path}"'


def _ancestors(path):
    parts = [p for p in path.split("/") if p]
    out = ["/"]
    for i in range(1, len(parts)):
        out.append("/" + "/".join(parts[:i]))
    return out


def user_temp_dir():
    try:
        p = subprocess.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, shell=False)
        d = p.stdout.strip()
        return os.path.realpath(d).rstrip("/") + "/" if p.returncode == 0 and d else None
    except OSError:
        return None


def allow_read_roots(home, repo_root=None):
    """The allowed --allow-read roots as (real root, containing dir) pairs. A root whose real path
    leaves its containing dir ($HOME or the repo), such as a symlinked node_modules, is not allowed."""
    roots = [(os.path.realpath(os.path.join(home, r)), os.path.realpath(home)) for r in ALLOW_READ_HOME]
    if repo_root:
        rr = os.path.realpath(repo_root)
        roots += [(os.path.realpath(os.path.join(rr, r)), rr) for r in ALLOW_READ_REPO]
    return roots


def validate_allow_read(path, home=None, repo_root=None):
    """Allowlist check on the real path (symlinks resolved). Returns the real path."""
    home = os.path.realpath(home or real_home())
    real = os.path.realpath(os.path.expanduser(path))
    roots = allow_read_roots(home, repo_root)
    for root, within in roots:
        if is_under(real, root) and is_under(root, within):
            return real
    names = [f"~/{r}" for r in ALLOW_READ_HOME] + [f"<repo>/{r}" for r in ALLOW_READ_REPO]
    raise OffloadError(f"--allow-read {path!r} (resolves to {real}) is not on the allowlist: {', '.join(names)}")


def generate_profile(run_dir, claude_real, claude_link=None, allow_read=(), repo_root=None,
                     home=None, temp_dir="auto", proxy_port=None, src_dir=None):
    """Build the SBPL text. Later rules win, so narrower allows follow the broad denies.

    The only network allow is the loopback egress proxy (proxy_port); with none, all network is denied.
    src_dir is the writable code dir (RUN/src for the worker, RUN/testsrc-N for `test`)."""
    home = home or real_home()
    run_dir = os.path.realpath(run_dir)
    claude_dir = os.path.dirname(os.path.realpath(claude_real))
    src_dir = os.path.realpath(src_dir) if src_dir else os.path.join(run_dir, "src")
    sub_reads = [src_dir] + [os.path.join(run_dir, d) for d in ("tmp", "home", "config")]
    sub_reads += [os.path.realpath(p) for p in allow_read]
    literal_reads = [os.path.join(run_dir, "profile.sb")]
    if claude_link:
        literal_reads.append(claude_link)

    meta = []
    for p in [run_dir, claude_dir, *sub_reads, *literal_reads]:
        for a in _ancestors(p) + ([p] if p == run_dir else []):
            if a not in meta:
                meta.append(a)

    if temp_dir == "auto":
        temp_dir = user_temp_dir()
    writes = [f"(subpath {sbpl_str(p)})" for p in sub_reads[:4]]
    writes += ['(literal "/dev/null")', '(literal "/dev/tty")', '(regex #"^/dev/fd/")', '(regex #"^/dev/ttys")']
    if temp_dir:
        # Known limitation: this does not stop the xcrun cache noise (see DESIGN.md).
        writes.append('(regex #"^' + re.sub(r"([^A-Za-z0-9_/])", r"\\\1", temp_dir) + 'xcrun_db-")')
        sbpl_str(temp_dir)  # reject unsafe characters

    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-read* (subpath {}) (subpath \"/private/tmp\") (subpath \"/private/var/folders\") "
        "(subpath \"/Volumes\"))".format(sbpl_str(home)),
    ]
    if repo_root:
        lines.append(f"(deny file-read* (subpath {sbpl_str(os.path.realpath(repo_root))}))")
    lines += [
        "(allow file-read-metadata " + " ".join(f"(literal {sbpl_str(m)})" for m in meta) + ")",
        "(allow file-read* (subpath {}) {} {})".format(
            sbpl_str(claude_dir),
            " ".join(f"(literal {sbpl_str(p)})" for p in literal_reads),
            " ".join(f"(subpath {sbpl_str(p)})" for p in sub_reads)),
        f"(deny file-read* (subpath {sbpl_str(os.path.join(run_dir, 'git'))}) "
        f"(literal {sbpl_str(os.path.join(run_dir, 'run.json'))}))",
        '(deny file-write* (subpath "/"))',
        "(allow file-write* " + " ".join(writes) + ")",
        # IPC that reaches outside the sandbox: clipboard, LaunchServices (open), DNS, AppleEvents.
        '(deny mach-lookup (global-name "com.apple.pasteboard.1") '
        '(global-name "com.apple.coreservices.launchservicesd") (global-name "com.apple.lsd.mapdb") '
        '(global-name "com.apple.lsd.modifydb") (global-name "com.apple.mDNSResponder") '
        '(global-name "com.apple.SystemConfiguration.DNSConfiguration"))',
        "(deny appleevent-send)",
        "(deny network*)",
    ]
    if proxy_port:
        # SBPL accepts only "*" or "localhost" as the host here; 127.0.0.1 is rejected by sandbox-exec.
        lines.append(f'(allow network-outbound (remote ip "localhost:{int(proxy_port)}"))')
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ egress proxy

class EgressProxy:
    """A filtering forwarding proxy on 127.0.0.1, run in the helper for the life of the worker.

    It forwards only POST /v1/messages and POST /v1/messages/count_tokens to the configured upstream
    and answers everything else with 403, logging it to `reject_log`. The sandboxed worker's only
    network allow is this port, so the worker can't reach other Ollama endpoints (pull, delete, ...)."""

    def __init__(self, upstream_url, reject_log, max_body=PROXY_MAX_BODY):
        up = urlsplit(upstream_url)
        self.scheme, self.host = up.scheme, up.hostname
        self.port = up.port or (443 if up.scheme == "https" else 80)
        self.reject_log, self.max_body = reject_log, max_body
        self._lock = threading.Lock()
        self._conns = set()
        self._socks = set()
        self._stopped = False
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.httpd.daemon_threads = True
        self.port_local = self.httpd.server_address[1]
        self._thread = None

    def log_reject(self, method, path):
        line = "%s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), method[:16],
                               path[:512].encode("unicode_escape").decode("ascii"))
        with self._lock:
            with open(self.reject_log, "a") as f:
                f.write(line)

    def _upstream(self):
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=PROXY_UPSTREAM_TIMEOUT)

    def _handler(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def setup(self):
                super().setup()
                with proxy._lock:
                    if proxy._stopped:
                        raise ConnectionError("proxy stopped")
                    proxy._socks.add(self.request)

            def finish(self):
                with proxy._lock:
                    proxy._socks.discard(self.request)
                try:
                    super().finish()
                except OSError:
                    pass

            def _reply(self, code, text):
                body = (text + "\n").encode()
                self.close_connection = True
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _reject(self):
                proxy.log_reject(self.command, self.path)
                self._reply(403, "forbidden by offload egress proxy")

            do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _reject

            def send_error(self, code, message=None, explain=None):
                if code == 501:  # unknown method
                    proxy.log_reject(self.command or "?", self.path or "?")
                self.close_connection = True
                super().send_error(code, message, explain)

            def _read_body(self):
                """Return the request body (<= max_body) or None after replying with an error."""
                if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                    data = b""
                    while True:
                        size = int(self.rfile.readline(64).split(b";")[0].strip() or b"0", 16)
                        if size == 0:
                            while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                                pass
                            return data
                        if len(data) + size > proxy.max_body:
                            break
                        data += self.rfile.read(size)
                        self.rfile.readline(8)
                else:
                    try:
                        n = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        n = -1
                    if 0 <= n <= proxy.max_body:
                        return self.rfile.read(n)
                proxy.log_reject(self.command, self.path + " [body too large or malformed]")
                self._reply(413, "request body too large")
                return None

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                if path not in PROXY_ALLOWED_PATHS:
                    return self._reject()
                try:
                    body = self._read_body()
                except (ValueError, OSError):
                    self.close_connection = True
                    return
                if body is None:
                    return
                hdrs = {"Connection": "close"}
                for k, v in self.headers.items():
                    kl = k.lower()
                    if kl in ("content-type", "accept", "user-agent", "authorization", "x-api-key") \
                            or kl.startswith("anthropic-"):
                        hdrs[k] = v
                conn = proxy._upstream()
                with proxy._lock:
                    proxy._conns.add(conn)
                try:
                    try:
                        conn.request("POST", self.path, body=body, headers=hdrs)
                        resp = conn.getresponse()
                    except (OSError, http.client.HTTPException) as e:
                        return self._reply(502, f"upstream error: {type(e).__name__}")
                    self.close_connection = True
                    self.send_response(resp.status)
                    for k, v in resp.getheaders():
                        kl = k.lower()
                        if kl in ("content-type", "request-id", "x-request-id") or kl.startswith("anthropic-"):
                            self.send_header(k, v)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while True:
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except OSError:
                    pass  # client or upstream went away mid-stream
                finally:
                    with proxy._lock:
                        proxy._conns.discard(conn)
                    conn.close()

        return Handler

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        return self.port_local

    def stop(self):
        """Stop listening and drop every open connection, in flight or idle."""
        with self._lock:
            self._stopped = True
            conns, socks = list(self._conns), list(self._socks)
        if self._thread:
            self.httpd.shutdown()
        self.httpd.server_close()
        for c in conns:
            try:
                c.close()
            except OSError:
                pass
        for sk in socks:
            try:
                sk.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


# ------------------------------------------------------------------ work area

def extract_tar_safely(fileobj, dest):
    """Extract a tar stream with member path checks (no shell pipe, no links out of dest)."""
    dest = os.path.realpath(dest)
    with tarfile.open(fileobj=fileobj, mode="r|") as tf:
        for m in tf:
            name = os.path.normpath(m.name)
            if name in (".", "pax_global_header") or m.name == "pax_global_header":
                continue
            if name.startswith("/") or name == ".." or name.startswith("../"):
                raise OffloadError(f"unsafe path in archive: {m.name!r}")
            target = os.path.join(dest, name)
            if not is_under(os.path.dirname(target), dest):
                raise OffloadError(f"archive path escapes the work area: {m.name!r}")
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isreg():
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
            elif m.issym():
                link_target = os.path.normpath(os.path.join(os.path.dirname(target), m.linkname))
                if os.path.isabs(m.linkname) or not is_under(link_target, dest):
                    raise OffloadError(f"symlink escapes the work area: {m.name!r} -> {m.linkname!r}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.symlink(m.linkname, target)
            else:
                continue  # hardlinks, devices, fifos are not exported


def validate_repo(repo, ohome, git_home="/var/empty"):
    repo = os.path.realpath(repo)
    p = safe_git(["-C", repo, "rev-parse", "--is-inside-work-tree", "--show-toplevel"], git_home, check=False)
    lines = p.stdout.split("\n")
    if p.returncode != 0 or lines[0].strip() != "true":
        raise OffloadError(f"{repo} is not a git work tree")
    root = os.path.realpath(lines[1].strip())
    hm = real_home()
    if root == hm or root == os.path.join(hm, ".claude") or is_under(root, os.path.join(hm, ".claude")):
        raise OffloadError(f"refusing to offload from {root}")
    if is_under(root, ohome):
        raise OffloadError(f"repo {root} is inside OFFLOAD_HOME")
    return root


def write_run_json(run_dir, record):
    tmp = os.path.join(run_dir, "run.json.tmp")
    with open(tmp, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(run_dir, "run.json"))


def ref_exists(repo, ref, home):
    return safe_git(["-C", repo, "show-ref", "--verify", "--quiet", ref], home, check=False).returncode == 0


def check_branch_free(repo, slug, home):
    """Refuse before any spend if offload/<slug> or the `offload` branch (a ref/dir conflict) exists."""
    for ref in ("refs/heads/offload", f"refs/heads/offload/{slug}"):
        if ref_exists(repo, ref, home):
            raise OffloadError(f"{ref} already exists in {repo}")
    nested = safe_git(["-C", repo, "for-each-ref", "--format=%(refname)", f"refs/heads/offload/{slug}/"],
                      home).stdout.strip()
    if nested:
        raise OffloadError(f"refs/heads/offload/{slug}/ has branches under it in {repo}")


def write_profile(ctx, proxy_port=None, src_dir=None, path=None):
    claude = ctx.get("claude")
    text = generate_profile(ctx["run_dir"], claude["real"] if claude else "/usr/bin/true",
                            claude["link"] if claude else None, ctx.get("allow_read", ()),
                            repo_root=ctx["repo"], proxy_port=proxy_port, src_dir=src_dir)
    with open(path or ctx["profile"], "w") as f:
        f.write(text)


def prepare_run(repo, slug, ohome, model=DEFAULT_MODEL, base=None, claude=None, allow_read=(),
                max_thinking=DEFAULT_MAX_THINKING, max_turns=DEFAULT_MAX_TURNS):
    """Run-flow steps 1 (partly), 2, 3 and the profile of step 4. Returns the run context.

    The profile written here has no network allow; cmd_run rewrites it once the proxy port is known."""
    validate_slug(slug)
    if not MODEL_RE.match(model):
        raise OffloadError(f"invalid model name {model!r}")
    os.makedirs(ohome, mode=0o700, exist_ok=True)
    root = validate_repo(repo, ohome)
    base = base or resolve_base_url()
    run_id = run_id_for(root, slug)
    run_dir = os.path.join(ohome, "runs", run_id)
    if os.path.lexists(run_dir):
        raise OffloadError(f"run directory already exists: {run_dir}")
    allow = [validate_allow_read(p, repo_root=root) for p in allow_read]
    check_branch_free(root, slug, "/var/empty")
    os.makedirs(os.path.dirname(run_dir), mode=0o700, exist_ok=True)
    os.mkdir(run_dir, 0o700)
    for d in ("src", "tmp", "home", "config"):
        os.mkdir(os.path.join(run_dir, d), 0o700)
    ghome = os.path.join(run_dir, "home")
    ctx = {"run_id": run_id, "run_dir": run_dir, "repo": root, "slug": slug, "model": model, "base": base,
           "claude": claude, "allow_read": allow, "max_thinking": max_thinking, "max_turns": max_turns,
           "git_dir": os.path.join(run_dir, "git"), "src": os.path.join(run_dir, "src"),
           "profile": os.path.join(run_dir, "profile.sb"), "ghome": ghome, "proxy_port": None}
    try:
        ctx["base_sha"] = safe_git(["-C", root, "rev-parse", "HEAD"], ghome).stdout.strip()
        safe_git(["clone", "--bare", "--no-local", "--no-hardlinks", root, ctx["git_dir"]], ghome)
        proc = subprocess.Popen(safe_git_argv(["--git-dir", ctx["git_dir"], "archive", ctx["base_sha"]], ghome),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        extract_tar_safely(proc.stdout, ctx["src"])
        err = proc.stderr.read()
        if proc.wait() != 0:
            raise OffloadError(f"git archive failed: {err.decode(errors='replace')[:300]}")
        safe_git(["--git-dir", ctx["git_dir"], "branch", "work", ctx["base_sha"]], ghome)
        safe_git(["--git-dir", ctx["git_dir"], "symbolic-ref", "HEAD", "refs/heads/work"], ghome)
        write_profile(ctx)
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    ctx["record"] = {"run_id": run_id, "run_dir": run_dir, "repo": root, "slug": slug, "model": model,
                     "branch": f"offload/{slug}", "base_sha": ctx["base_sha"], "base_url": base["url"],
                     "status": "prepared", "created": time.time(), "max_turns": max_turns,
                     "claude": claude, "allow_read": allow}
    write_run_json(run_dir, ctx["record"])
    return ctx


def sandbox_argv(profile, argv):
    return [SANDBOX_EXEC, "-f", profile, *argv]


def worker_url(ctx):
    """The URL the sandboxed process is told to use: the local proxy, or an unreachable placeholder."""
    return f"http://127.0.0.1:{ctx.get('proxy_port') or 1}"


def run_selftest(ctx):
    """Deterministic sandbox checks, no model. Returns (ok, results).

    When ctx['proxy_port'] is set the proxy must be up; the worker profile then allows that one port."""
    env = build_worker_env(ctx["run_dir"], ctx["model"], worker_url(ctx), ctx["max_thinking"])
    hm = real_home()
    results = []
    curl = "/usr/bin/curl"

    def sb(argv, timeout=15, binary=False):
        try:
            return subprocess.run(sandbox_argv(ctx["profile"], argv), cwd=ctx["src"], env=env, shell=False,
                                  capture_output=True, text=not binary, timeout=timeout,
                                  errors=None if binary else "replace")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, b"" if binary else "", b"timeout" if binary else "timeout")

    def record(name, ok, detail=""):
        results.append({"check": name, "ok": bool(ok), "detail": detail[:200]})

    def fresh(base_dir, prefix):
        while True:
            p = os.path.join(base_dir, f"{prefix}-{secrets.token_hex(6)}")
            if not os.path.lexists(p):
                return p

    denied = lambda p: p.returncode != 0 and "not permitted" in p.stderr.lower()
    probe = next((os.path.join(hm, f) for f in (".ssh/known_hosts", ".zshrc", ".gitconfig")
                  if os.path.exists(os.path.join(hm, f))), None)
    if probe:
        p = sb(["/bin/cat", probe])
        record("read of a home file is denied", denied(p) and not p.stdout, p.stderr.strip())
    else:
        record("read of a home file is denied (no probe file exists, skipped)", True)
    p = sb(["/bin/ls", hm])
    record("ls $HOME is denied", denied(p) and not p.stdout, p.stderr.strip())
    wprobe = fresh(ctx["repo"], ".offload-probe")  # did not exist, so only what we created is removed
    p = sb(["/usr/bin/touch", wprobe])
    created = os.path.lexists(wprobe)
    if created:
        os.remove(wprobe)
    record("write into the source repo is denied", denied(p) and not created, p.stderr.strip())
    if os.path.exists(curl):
        p = sb([curl, "-m", "3", "-sS", "https://example.com"], timeout=15)
        record("network to example.com fails", p.returncode != 0 and not p.stdout, p.stderr.strip())
        p = sb([curl, "-m", "2", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1:11434/api/tags"])
        record("direct connect to localhost:11434 fails", p.returncode != 0 and p.stdout.strip() in ("", "000"),
               f"exit {p.returncode}")
        if os.path.exists("/var/run/docker.sock"):
            p = sb([curl, "-s", "-v", "-m", "2", "--unix-socket", "/var/run/docker.sock", "http://d/_ping"])
            record("unix socket connect (docker.sock) fails", p.returncode == 7 or "Connected" not in p.stderr,
                   f"exit {p.returncode}")
        if ctx.get("proxy_port"):
            p = sb([curl, "-s", "-m", "3", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST",
                    f"http://127.0.0.1:{ctx['proxy_port']}/api/pull", "-d", "{}"])
            record("proxy answers 403 for POST /api/pull", p.stdout.strip() == "403",
                   f"http {p.stdout.strip()!r} exit {p.returncode}")
    if os.path.exists("/usr/bin/pbpaste"):
        p = sb(["/usr/bin/pbpaste"], binary=True)  # never printed or stored: only the byte count
        record("pbpaste yields nothing", p.returncode != 0 or len(p.stdout) == 0, f"exit {p.returncode}, {len(p.stdout)} bytes")
    if os.path.exists("/usr/bin/dscacheutil"):
        p = sb(["/usr/bin/dscacheutil", "-q", "host", "-a", "name", "example.com"])
        record("DNS lookup of example.com fails", "ip_address" not in p.stdout and "ipv6_address" not in p.stdout,
               f"exit {p.returncode}")
    sprobe = fresh(ctx["src"], ".probe")
    p = sb(["/usr/bin/touch", sprobe])
    ok = p.returncode == 0 and os.path.exists(sprobe)
    if os.path.lexists(sprobe):
        os.remove(sprobe)
    record("write into src succeeds", ok, p.stderr.strip())
    return all(r["ok"] for r in results), results


def commit_work(ctx):
    """Step 7: commit src/ onto branch `work` in the helper-owned repo. Returns head sha."""
    gh = ctx["ghome"]
    embedded = os.path.join(ctx["src"], ".git")
    if os.path.lexists(embedded):  # a worker-made repo at the root would be added as a gitlink
        if os.path.isdir(embedded) and not os.path.islink(embedded):
            shutil.rmtree(embedded, ignore_errors=True)
        else:
            os.remove(embedded)
    git = ["--git-dir", ctx["git_dir"], "--work-tree", ctx["src"]]
    safe_git(git + ["add", "-A"], gh)
    safe_git(git + ["-c", "user.name=offload", "-c", "user.email=offload@localhost", "-c", "commit.gpgsign=false",
                    "commit", "--no-verify", "--allow-empty", "-m", f"offload: {ctx['slug']}"], gh)
    return safe_git(["--git-dir", ctx["git_dir"], "rev-parse", "refs/heads/work"], gh).stdout.strip()


def fetch_work(ctx):
    """Step 8/9: fetch `work` into the source repo as offload/<slug> without ever overwriting.

    Fetch (no +) into a temporary ref, create the branch with an expected-absent old value, delete the
    temporary ref even on failure. The ref snapshot brackets only this window; any change other than the
    new branch is recorded in ctx['ref_violations']. Returns the sha."""
    ref = f"refs/heads/offload/{ctx['slug']}"
    tmp = f"refs/offload-tmp/{ctx['run_id']}"
    gh, repo = ctx["ghome"], ctx["repo"]
    check_branch_free(repo, ctx["slug"], gh)
    before = snapshot_refs(repo, gh)
    try:
        safe_git(["-C", repo, "fetch", "--no-tags", ctx["git_dir"], f"refs/heads/work:{tmp}"], gh)
        sha = safe_git(["-C", repo, "rev-parse", "--verify", tmp], gh).stdout.strip()
        safe_git(["-C", repo, "update-ref", ref, sha, ""], gh)
    finally:
        safe_git(["-C", repo, "update-ref", "-d", tmp], gh, check=False)
        ctx["ref_violations"] = compare_refs(before, snapshot_refs(repo, gh), ctx["slug"])
    return sha


def verify_refs(ctx):
    """Step 9: the unexpected ref changes seen around the fetch (empty means fine)."""
    return ctx.get("ref_violations", [])


# --------------------------------------------------------------- worker process

def kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _run_cmd(argv, timeout=20):
    try:
        return subprocess.run(argv, capture_output=True, text=True, shell=False, timeout=timeout,
                              errors="replace", stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def find_survivors(root_pid, run_dir):
    """Best-effort: pids still tied to the run. Descendants of root_pid, members of its process group,
    and any process whose cwd is under run_dir. Never includes this process."""
    me = os.getpid()
    found = set()
    children, pgid_of = {}, {}
    for line in _run_cmd(["/bin/ps", "-axo", "pid=,ppid=,pgid="]).splitlines():
        parts = line.split()
        if len(parts) == 3 and all(x.isdigit() for x in parts):
            pid, ppid, pg = map(int, parts)
            children.setdefault(ppid, []).append(pid)
            pgid_of[pid] = pg
    todo = [root_pid]
    while todo:
        for c in children.get(todo.pop(), []):
            if c not in found:
                found.add(c)
                todo.append(c)
    found |= {pid for pid, pg in pgid_of.items() if pg == root_pid}
    if pgid_of.get(root_pid) is not None:
        found.add(root_pid)
    cur = None
    for line in _run_cmd(["/usr/sbin/lsof", "-a", "-d", "cwd", "-Fpn"], timeout=30).splitlines():
        if line.startswith("p") and line[1:].isdigit():
            cur = int(line[1:])
        elif line.startswith("n") and cur is not None and is_under(line[1:], run_dir):
            found.add(cur)
    parent_of = {c: p for p, cs in children.items() for c in cs}
    anc = me
    while anc in parent_of:  # never kill our own ancestors (e.g. a shell sitting in the run dir)
        anc = parent_of[anc]
        found.discard(anc)
    found.discard(me)
    return sorted(found)


def sweep_survivors(proc, run_dir):
    """SIGKILL whatever is left of the worker or test. Returns the pids that were still alive."""
    pids = find_survivors(proc.pid, run_dir)
    killed = []
    for pid in pids:
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    kill_group(proc)
    return killed


def pump(proc, timeout_s, on_data):
    """Read proc.stdout with selectors until EOF, the process having exited, or a hard deadline.

    A daemonized child that inherits the pipe never gives EOF, so once the main process has exited we
    stop after one more empty poll. on_data(bytes) may return True to stop early. Returns timed_out."""
    fd = proc.stdout.fileno()
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            if not sel.select(min(remaining, 0.5)):
                if proc.poll() is not None:
                    return False
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return False
            if on_data(chunk):
                return False
    finally:
        sel.close()


def launch_worker(ctx, brief_text, timeout_s):
    """Step 5/6: run the worker, reading stream-json incrementally. Returns an outcome dict."""
    env = build_worker_env(ctx["run_dir"], ctx["model"], worker_url(ctx), ctx["max_thinking"])
    argv = sandbox_argv(ctx["profile"], build_claude_argv(ctx["claude"]["real"], ctx["max_turns"]))
    preamble = ("You are working in the current directory only. There is no git and no network. "
                "Write the code and tests the brief asks for, run the tests, and finish with a short summary.\n\n")
    log_path = os.path.join(ctx["run_dir"], "worker.jsonl")
    out = {"init_problems": [], "saw_init": False, "timed_out": False, "result": None, "usage": {},
           "text": "", "log_path": log_path, "survivors_killed": []}
    per_msg = {}
    buf = [b""]

    def handle_line(raw):
        """Returns True to stop reading (init check failed)."""
        try:
            ev = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(ev, dict):
            return False
        if ev.get("type") == "system" and ev.get("subtype") == "init" and not out["saw_init"]:
            out["saw_init"] = True
            out["init_problems"] = check_init(ev)
            return bool(out["init_problems"])
        if ev.get("type") == "assistant":
            msg = ev.get("message") or {}
            if isinstance(msg.get("usage"), dict):
                per_msg[msg.get("id") or len(per_msg)] = msg["usage"]
            for blk in msg.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    out["text"] = blk.get("text", "")
        elif ev.get("type") == "result":
            out["result"] = ev
        return False

    with open(log_path, "wb") as log, open(os.path.join(ctx["run_dir"], "worker.stderr"), "wb") as errf:
        proc = subprocess.Popen(argv, cwd=ctx["src"], env=env, shell=False, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=errf, start_new_session=True)

        def feed():
            try:
                proc.stdin.write((preamble + brief_text).encode())
                proc.stdin.close()
            except OSError:
                pass

        threading.Thread(target=feed, daemon=True).start()

        def on_data(chunk):
            log.write(chunk)
            log.flush()
            buf[0] += chunk
            *lines, buf[0] = buf[0].split(b"\n")
            if len(buf[0]) > 16 * 1024 * 1024:  # a line this long is not stream-json
                buf[0] = b""
            for raw in lines:
                if handle_line(raw):
                    return True
            return False

        try:
            out["timed_out"] = pump(proc, timeout_s, on_data)
            if buf[0] and not out["init_problems"]:
                handle_line(buf[0])
        finally:
            out["survivors_killed"] = sweep_survivors(proc, ctx["run_dir"])
            try:
                proc.stdout.close()
            except OSError:
                pass
            try:
                out["returncode"] = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_group(proc)
                out["returncode"] = proc.wait()
    res = out["result"] or {}
    u = res.get("usage") if isinstance(res.get("usage"), dict) else None
    if u is None:
        u = {"input_tokens": sum(x.get("input_tokens", 0) for x in per_msg.values()),
             "cache_read_input_tokens": sum(x.get("cache_read_input_tokens", 0) for x in per_msg.values()),
             "output_tokens": sum(x.get("output_tokens", 0) for x in per_msg.values())}
    out["usage"] = {"input": int(u.get("input_tokens", 0) or 0), "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
                    "output": int(u.get("output_tokens", 0) or 0)}
    out["turns"] = int(res.get("num_turns", 0) or 0)
    if isinstance(res.get("result"), str) and res["result"]:
        out["text"] = res["result"]
    return out


# -------------------------------------------------------------------- commands

def emit(obj):
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")


def load_run(ohome, run_id):
    """Resolve a run from run.json only, and check it lives under OFFLOAD_HOME."""
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise OffloadError(f"invalid run id {run_id!r}")
    run_dir = os.path.join(ohome, "runs", run_id)
    try:
        with open(os.path.join(run_dir, "run.json")) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        raise OffloadError(f"no readable run.json for {run_id}")
    rd = rec.get("run_dir")
    if not isinstance(rd, str) or os.path.realpath(rd) != os.path.realpath(run_dir) \
            or not is_under(rd, os.path.join(ohome, "runs")) or os.path.realpath(rd) == os.path.realpath(ohome):
        raise OffloadError(f"run.json for {run_id} points outside OFFLOAD_HOME: {rd!r}")
    return rec


def ctx_from_record(rec):
    rd = rec["run_dir"]
    return {"run_id": rec["run_id"], "run_dir": rd, "repo": rec["repo"], "slug": rec["slug"], "model": rec["model"],
            "claude": rec.get("claude"), "allow_read": rec.get("allow_read") or [], "proxy_port": None,
            "git_dir": os.path.join(rd, "git"), "src": os.path.join(rd, "src"),
            "profile": os.path.join(rd, "profile.sb"), "ghome": os.path.join(rd, "home"),
            "max_thinking": DEFAULT_MAX_THINKING}


def diff_summary(ctx):
    gd = ["--git-dir", ctx["git_dir"]]
    base_sha = ctx["base_sha"]
    stat_out = safe_git(gd + ["diff", "--stat", "--no-color", base_sha, "work"], ctx["ghome"]).stdout
    names = safe_git(gd + ["diff", "--name-only", "-z", base_sha, "work"], ctx["ghome"]).stdout
    paths = [p for p in names.split("\0") if p]
    return stat_out, paths


NUMSTAT_MAX = 500


def diff_numstat(ctx):
    """Per-file [{path, added, deleted}] (None counts for binary files), capped at NUMSTAT_MAX."""
    raw = safe_git(["--git-dir", ctx["git_dir"], "diff", "--numstat", "-z", "--no-renames", ctx["base_sha"], "work"],
                   ctx["ghome"]).stdout
    rows = []
    for rec in raw.split("\0"):
        parts = rec.split("\t", 2)
        if len(parts) == 3:
            a, d, p = parts
            rows.append({"path": sanitize_untrusted(p, 512), "added": int(a) if a.isdigit() else None,
                         "deleted": int(d) if d.isdigit() else None})
    return rows[:NUMSTAT_MAX], max(0, len(rows) - NUMSTAT_MAX)


def count_format_chars(*texts):
    return sum(1 for t in texts for ch in t if unicodedata.category(ch) == "Cf")


def cmd_run(args, ohome):
    t0 = time.monotonic()
    if sys.platform != "darwin" or not os.path.exists(SANDBOX_EXEC):
        raise OffloadError("offload needs macOS with /usr/bin/sandbox-exec")
    with open(args.brief, encoding="utf-8") as f:
        brief = f.read()
    slug = validate_slug(args.slug or default_slug(args.brief))
    claude = resolve_claude(args.allow_untested_claude)
    base = resolve_base_url(args.base_url)
    ctx = prepare_run(args.repo, slug, ohome, args.model, base, claude, args.allow_read,
                      args.max_thinking_tokens, args.max_turns)
    rec = ctx["record"]

    def finish(status, extra=None):
        rec["status"] = status
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        rec.update(extra or {})
        write_run_json(ctx["run_dir"], rec)

    out = {"status": None, "run_id": ctx["run_id"], "repo": ctx["repo"], "branch": f"offload/{slug}",
           "base_sha": ctx["base_sha"], "head_sha": None, "model": args.model, "turns": 0, "duration_s": 0,
           "usage": {"input": 0, "cache_read": 0, "output": 0}, "est_cost_usd": None, "untrusted_diffstat": "",
           "untrusted_sensitive_paths": [], "untrusted_worker_text": "", "format_chars": 0,
           "survivors_killed": [], "log_path": os.path.join(ctx["run_dir"], "worker.jsonl")}
    head = None
    try:
        proxy = EgressProxy(base["url"], os.path.join(ctx["run_dir"], "proxy_rejects.log"))
        try:
            ctx["proxy_port"] = proxy.start()
            write_profile(ctx, ctx["proxy_port"])
            ok, checks = run_selftest(ctx)
            if not ok:
                out.update(status="sandbox_selftest_failed", selftest=checks)
                finish("sandbox_selftest_failed")
                out["duration_s"] = rec["duration_s"]
                emit(out)
                return 1
            w = launch_worker(ctx, brief, args.timeout_min * 60)
        finally:
            proxy.stop()  # as soon as the worker ends, times out, or fails the init check
        out.update(turns=w["turns"], usage=w["usage"], est_cost_usd=estimate_cost(args.model, w["usage"]),
                   survivors_killed=w["survivors_killed"])
        if w["init_problems"] or not w["saw_init"]:
            problems = w["init_problems"] or ["no system/init event seen before the worker ended"]
            out.update(status="init_check_failed", init_problems=problems)
            finish("init_check_failed", {"init_problems": problems, "usage": w["usage"],
                                         "survivors_killed": w["survivors_killed"]})
            out["duration_s"] = rec["duration_s"]
            emit(out)
            return 1

        res = w["result"] or {}
        status = "timeout" if w["timed_out"] else ("failed" if (w["returncode"] != 0 or res.get("is_error")
                                                                or not w["result"]) else "ok")
        head = commit_work(ctx)
        fetch_work(ctx)
        violations = verify_refs(ctx)
        ctx["base_sha"] = rec["base_sha"]
        stat_out, paths = diff_summary(ctx)
        if violations:
            status = "ref_violation"
            out["ref_violations"] = violations
        text = w["text"]
        if not text and status != "ok":
            try:
                with open(os.path.join(ctx["run_dir"], "worker.stderr"), "rb") as f:
                    text = f.read()[-UNTRUSTED_LIMIT:].decode(errors="replace")
            except OSError:
                pass
        sens = sensitive_paths(paths)
        out.update(status=status, head_sha=head, untrusted_diffstat=sanitize_untrusted(stat_out, 2000),
                   untrusted_sensitive_paths=sens, untrusted_worker_text=sanitize_untrusted(text),
                   format_chars=count_format_chars(text, stat_out, *paths))
        finish(status, {"head_sha": head, "usage": w["usage"], "turns": w["turns"],
                        "est_cost_usd": out["est_cost_usd"], "untrusted_sensitive_paths": sens,
                        "survivors_killed": w["survivors_killed"]})
    except BaseException as e:
        finish("error", {"error": sanitize_untrusted(f"{type(e).__name__}: {e}", 1000), "head_sha": head})
        raise
    out["duration_s"] = rec["duration_s"]
    emit(out)
    return 0 if status == "ok" else 1


def _rmtree_force(path, run_dir):
    """rmtree that can remove read-only dirs, and never follows a symlink to chmod something else."""
    def chmod_inside(p, mode):
        if os.path.islink(p) or not is_under(p, run_dir):
            return
        try:
            os.chmod(p, mode, follow_symlinks=False)
        except (OSError, NotImplementedError):
            pass

    def handler(func, p, _exc):
        chmod_inside(os.path.dirname(p), 0o700)
        chmod_inside(p, 0o700)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handler)
    else:
        shutil.rmtree(path, onerror=handler)


def cmd_test(args, ohome):
    rec = load_run(ohome, args.run)
    ctx = ctx_from_record(rec)
    cmd = list(args.test_cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise OffloadError("no command given: offload.py test --run ID -- CMD...")
    if not ctx["claude"]:
        raise OffloadError("run.json has no claude info; this run predates `test` exporting committed code")
    n = 1
    while True:
        testsrc = os.path.join(ctx["run_dir"], f"testsrc-{n}")
        try:
            os.mkdir(testsrc, 0o700)
            break
        except FileExistsError:
            n += 1
    tprofile = os.path.join(ctx["run_dir"], f"profile-test-{n}.sb")
    tctx = dict(ctx, src=testsrc, profile=tprofile, proxy_port=None)
    killed = []
    try:
        # Test what was committed, not whatever the worker left in src/. No proxy, no network at all.
        proc = subprocess.Popen(safe_git_argv(["--git-dir", ctx["git_dir"], "archive", "work"], ctx["ghome"]),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        extract_tar_safely(proc.stdout, testsrc)
        err = proc.stderr.read()
        if proc.wait() != 0:
            raise OffloadError(f"git archive failed: {err.decode(errors='replace')[:300]}")
        write_profile(tctx, None, src_dir=testsrc, path=tprofile)
        ok, checks = run_selftest(tctx)
        if not ok:
            emit({"status": "sandbox_selftest_failed", "selftest": checks})
            return 1
        env = build_worker_env(ctx["run_dir"], ctx["model"], worker_url(tctx))
        proc = subprocess.Popen(sandbox_argv(tprofile, cmd), cwd=testsrc, env=env, shell=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        ring, total = bytearray(), [0]

        def on_data(chunk):
            total[0] += len(chunk)
            ring.extend(chunk)
            if len(ring) > OUTPUT_RING:
                del ring[:len(ring) - OUTPUT_RING]

        try:
            timed_out = pump(proc, args.timeout_s, on_data)
        finally:
            killed = sweep_survivors(proc, ctx["run_dir"])
            try:
                proc.stdout.close()
            except OSError:
                pass
            try:
                code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_group(proc)
                code = proc.wait()
    finally:
        _rmtree_force(testsrc, ctx["run_dir"])
        if os.path.lexists(tprofile):
            os.remove(tprofile)
        if killed:
            rec["survivors_killed"] = sorted(set(rec.get("survivors_killed") or []) | set(killed))
            write_run_json(ctx["run_dir"], rec)
    text = bytes(ring).decode(errors="replace")
    emit({"status": "timeout" if timed_out else "ok", "exit_code": code, "command": cmd,
          "survivors_killed": killed, "total_output_bytes": total[0],
          "untrusted_output": sanitize_untrusted(text[-8000:], 8192)})
    return 0


def cmd_diff(args, ohome):
    rec = load_run(ohome, args.run)
    ctx = ctx_from_record(rec)
    ctx["base_sha"] = rec["base_sha"]
    want = [p for p in (args.paths or []) if p != "--"]
    spec = ["--"] + [":(literal)" + p for p in want] if want else []
    text = safe_git(["--git-dir", ctx["git_dir"], "diff", "--no-color", ctx["base_sha"], "work"] + spec,
                    ctx["ghome"]).stdout
    _, paths = diff_summary(ctx)
    numstat, omitted = diff_numstat(ctx)
    shown, st = sanitize_untrusted_ex(text, 200_000)
    sys.stdout.write(shown + "\n")
    emit({"untrusted": True, "run_id": args.run, "base_sha": ctx["base_sha"], "files_changed": len(paths),
          "truncated": st["truncated"], "format_chars": st["format_chars"] + count_format_chars(*paths),
          "paths_filter": [sanitize_untrusted(p, 512) for p in want],
          "untrusted_numstat": numstat, "numstat_omitted": omitted,
          "untrusted_sensitive_paths": sensitive_paths(paths)})
    return 0


def cmd_list(args, ohome):
    runs = []
    rdir = os.path.join(ohome, "runs")
    for name in sorted(os.listdir(rdir)) if os.path.isdir(rdir) else []:
        try:
            with open(os.path.join(rdir, name, "run.json")) as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        runs.append({k: r.get(k) for k in ("run_id", "status", "repo", "branch", "model", "head_sha", "created")})
    emit({"runs": runs})
    return 0


def cmd_status(args, ohome):
    emit(load_run(ohome, args.run))
    return 0


def discard_run(ohome, run_id):
    rec = load_run(ohome, run_id)
    rd = os.path.realpath(rec["run_dir"])
    gh = os.path.join(rd, "home")
    gh = gh if os.path.isdir(gh) else "/var/empty"
    deleted = False
    ref = f"refs/heads/offload/{rec['slug']}"
    head = rec.get("head_sha")
    if head and os.path.isdir(rec["repo"]):
        cur = safe_git(["-C", rec["repo"], "rev-parse", "--verify", "--quiet", ref], gh, check=False)
        if cur.returncode == 0 and cur.stdout.strip() == head:
            deleted = safe_git(["-C", rec["repo"], "update-ref", "-d", ref, head], gh, check=False).returncode == 0
    _rmtree_force(rd, rd)
    return {"status": "discarded", "run_id": run_id, "branch_deleted": deleted, "branch": ref}


def cmd_discard(args, ohome):
    emit(discard_run(ohome, args.run))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="offload.py", description=__doc__.split("\n")[0])
    ap.add_argument("--home", help="OFFLOAD_HOME override (default ~/.cache/offload); for tests")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--repo", required=True)
    r.add_argument("--brief", required=True)
    r.add_argument("--slug")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    r.add_argument("--timeout-min", type=float, default=DEFAULT_TIMEOUT_MIN)
    r.add_argument("--max-thinking-tokens", type=int, default=DEFAULT_MAX_THINKING)
    r.add_argument("--allow-read", action="append", default=[])
    r.add_argument("--base-url")
    r.add_argument("--allow-untested-claude", action="store_true")
    t = sub.add_parser("test")
    t.add_argument("--run", required=True)
    t.add_argument("--timeout-s", type=float, default=600)
    t.add_argument("test_cmd", nargs=argparse.REMAINDER, metavar="CMD")
    d = sub.add_parser("diff")
    d.add_argument("--run", required=True)
    d.add_argument("paths", nargs=argparse.REMAINDER, metavar="PATH")
    for name in ("status", "discard"):
        s = sub.add_parser(name)
        s.add_argument("--run", required=True)
    sub.add_parser("list")
    return ap


COMMANDS = {"run": cmd_run, "test": cmd_test, "diff": cmd_diff, "list": cmd_list, "status": cmd_status,
            "discard": cmd_discard}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args, offload_home(args.home)) or 0
    except OffloadError as e:
        emit({"status": "error", "error": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
