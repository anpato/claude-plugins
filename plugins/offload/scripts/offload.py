#!/usr/bin/env python3
"""Delegate a build task to a worker agent CLI, in an isolated git copy, then review the result.

The worker runs directly (no offload sandbox): a Claude Code worker pointed at a local/Ollama model,
or `codex exec` on the user's subscription. The worker reaches the network and runs commands freely;
the safety model is provider security + nothing-runs-until-you-merge + reviewing the diff. Everything
the worker writes (code, tests, result text, commits) is untrusted and must be reviewed before merge.
See ../DESIGN.md for the threat model and the run flow. macOS/Linux; no platform-specific jail.

  offload.py [--home DIR] run --repo R --brief F [--slug S] [--kind claude|codex] [--model M]
                              [--worker NAME] [--base-url URL] [--max-turns N] [--timeout-min T]
                              [--max-thinking-tokens N]
  offload.py [--home DIR] test --run ID [--timeout-s N] -- CMD...
  offload.py [--home DIR] diff --run ID [-- PATH...]
  offload.py [--home DIR] list
  offload.py [--home DIR] workers
  offload.py [--home DIR] status --run ID
  offload.py [--home DIR] discard --run ID

OFFLOAD_HOME is ~/.cache/offload unless --home DIR is given (used by the tests). It never
affects the base URL, which comes only from the default or ~/.config/offload/config.json.
Every subcommand prints JSON to stdout; `diff` prints the diff text and then a JSON trailer.
"""
import argparse
import getpass
import hashlib
import json
import os
import pwd
import re
import secrets
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import unicodedata
from urllib.parse import urlsplit

DEFAULT_MODEL = "kimi-k3:cloud"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MAX_TURNS = 60
DEFAULT_TIMEOUT_MIN = 45
DEFAULT_MAX_THINKING = 8000
UNTRUSTED_LIMIT = 4096
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
# First on the worker's PATH only (never the helper's): the real python3/git, not the xcrun shims.
CLT_BIN = "/Library/Developer/CommandLineTools/usr/bin"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
KINDS = ("claude", "codex")
ALLOWED_TOOLS = ("Bash", "Read", "Edit", "Write", "Glob", "Grep")
DENIED_TOOLS = ("WebFetch", "WebSearch", "Agent", "Skill")
# USD per 1M tokens: (input, cache read, output). codex runs on a flat subscription: no per-token estimate.
PRICES = {"kimi-k3:cloud": (3.0, 0.30, 15.0)}
OUTPUT_RING = 64 * 1024
SENSITIVE_DIRS = {".claude", ".github", ".husky", ".githooks", ".vscode", ".idea", ".cargo", ".mvn", ".codex"}
SENSITIVE_BASENAMES = {"Makefile", "package.json", "conftest.py", "setup.py", "build.rs", ".envrc",
                       ".pre-commit-config.yaml", "pyproject.toml", "setup.cfg", "sitecustomize.py",
                       "usercustomize.py", "Justfile", "Rakefile", "Dockerfile", ".npmrc",
                       "requirements.txt", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
                       "settings.gradle", "settings.gradle.kts", ".pypirc",
                       "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
                       "go.sum", "poetry.lock", "uv.lock", "Pipfile", "Pipfile.lock", ".pnpmfile.cjs",
                       "gradle-wrapper.properties"}
SENSITIVE_PREFIXES = ("lefthook", "docker-compose", ".yarnrc", "requirements")
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
            kept.append("⟨U+%04X⟩" % ord(ch))
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
    """USD estimate from the price table, or None for a model with no known price (e.g. codex)."""
    price = PRICES.get(model)
    if not price:
        return None
    p_in, p_cache, p_out = price
    cost = (usage.get("input", 0) * p_in + usage.get("cache_read", 0) * p_cache
            + usage.get("output", 0) * p_out) / 1_000_000
    return round(cost, 6)


def sensitive_paths(paths):
    """Paths whose change can run code outside a later build. Entries are worker-controlled: sanitize."""
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

def config_file():
    return os.path.join(real_home(), ".config", "offload", "config.json")


def read_config(path=None):
    """The user's config, or {} when missing or unreadable. Only ever read from the user's home."""
    try:
        with open(path or config_file()) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def list_workers(path=None):
    """Named claude/ollama workers from config `workers`: {name: {"base_url", "model"[, "context_tokens"]}}.
    Malformed entries are skipped; an out-of-range context_tokens is dropped."""
    raw = read_config(path).get("workers")
    out = {}
    for name, w in (raw.items() if isinstance(raw, dict) else []):
        if (isinstance(w, dict) and isinstance(w.get("base_url"), str) and isinstance(w.get("model"), str)
                and MODEL_RE.match(w["model"])):
            out[str(name)] = {"base_url": w["base_url"].rstrip("/"), "model": w["model"]}
            ct = w.get("context_tokens")
            if isinstance(ct, int) and not isinstance(ct, bool) and 4096 <= ct <= 2_000_000:
                out[str(name)]["context_tokens"] = ct
    return out


def resolve_worker(name, path=None):
    workers = list_workers(path)
    if name not in workers:
        raise OffloadError(f"unknown worker {name!r}; configured: {sorted(workers)}")
    return workers[name]


def worker_up(base_url, model, timeout=3):
    """True if the Ollama behind base_url answers and has `model`. A dead tunnel, a stopped WSL or a missing model
    all read as down. /api/show reads the manifest only; it does not load the model."""
    import urllib.request
    req = urllib.request.Request(base_url + "/api/show", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def resolve_base_url(requested=None, config_path=None):
    """Return {'url', 'host', 'port'}. Never reads the environment for the URL.

    The default is local ollama. Any other URL must be listed in `base_urls` (or be a worker's
    `base_url`) in ~/.config/offload/config.json, and a non-loopback host must use https. The worker
    connects to this URL directly (there is no proxy), so a remote endpoint needs its own credential."""
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
        path = config_path or config_file()
        cfg = read_config(path)
        allowed = cfg.get("base_urls", []) if isinstance(cfg.get("base_urls"), list) else []
        allowed = allowed + [w["base_url"] for w in list_workers(path).values()]
        if url not in [str(a).rstrip("/") for a in allowed if isinstance(a, str)]:
            raise OffloadError(f"base URL {url!r} is not in the base_urls allowlist in {path}")
    if host not in {"localhost", "127.0.0.1", "::1"} and parts.scheme != "https":
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


# ----------------------------------------------------------------- worker binaries

def _bin_version(real, args=("--version",)):
    try:
        p = subprocess.run([real, *args], capture_output=True, text=True, shell=False, cwd="/",
                           env={"PATH": SAFE_PATH, "HOME": "/var/empty"}, timeout=30)
        return p.stdout.strip().split()[0] if p.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def resolve_claude():
    link = shutil.which("claude")
    if not link:
        raise OffloadError("claude binary not found on PATH")
    real = os.path.realpath(link)
    return {"kind": "claude", "link": os.path.abspath(link), "real": real, "version": _bin_version(real)}


def codex_home():
    return os.path.join(real_home(), ".codex")


def resolve_codex():
    link = shutil.which("codex")
    if not link:
        raise OffloadError("codex binary not found on PATH; install codex and run `codex login`")
    auth = os.path.join(codex_home(), "auth.json")
    if not os.path.exists(auth):
        raise OffloadError(f"no codex auth at {auth}; run `codex login` first")
    real = os.path.realpath(link)
    return {"kind": "codex", "link": os.path.abspath(link), "real": real, "version": _bin_version(real)}


def build_claude_argv(claude_real, max_turns, add_dirs=()):
    dirs = [a for d in add_dirs for a in ("--add-dir", d)]
    return [claude_real, "-p", "--bare", "--strict-mcp-config", "--setting-sources", "",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose",
            "--max-turns", str(int(max_turns)), "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--tools", ",".join(ALLOWED_TOOLS), "--allowedTools", ",".join(ALLOWED_TOOLS),
            "--disallowedTools", ",".join(DENIED_TOOLS), "--disable-slash-commands", *dirs]


def build_codex_argv(codex_real, src, model, last_msg_path):
    """codex exec, non-interactive, writing JSONL to stdout and the final message to a file. Workspace-write
    confines its shell writes to `src` + temp; its user config/hooks/rules are ignored (auth still used)."""
    argv = [codex_real, "exec", "--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
            "--color", "never", "-C", src, "-s", "workspace-write", "-o", last_msg_path]
    if model:
        argv += ["-m", model]
    return argv


def clt_bin_dir():
    """The Command Line Tools bin dir if it exists and is root-owned, else None. Hard-coded on purpose:
    never taken from `xcode-select -p`, which the environment can influence."""
    try:
        st = os.stat(CLT_BIN)
    except OSError:
        return None
    return CLT_BIN if st.st_uid == 0 and os.path.isdir(CLT_BIN) else None


def worker_path(clt):
    """User toolchains first (Homebrew: current python3 and git, no xcrun shim), then the CLT, then the system."""
    head = ["/opt/homebrew/bin", "/usr/local/bin"] + ([clt] if clt else [])
    rest = [d for d in SAFE_PATH.split(":") if d not in head]
    return ":".join(head + rest)


def build_worker_env(ctx):
    """The environment the worker runs with. Network is open (no proxy). Nothing is inherited from
    os.environ except an optional LANG. The worker is not jailed, so this only steers config and tooling."""
    run_dir = ctx["run_dir"]
    tmp = os.path.join(run_dir, "tmp")
    env = {
        "PATH": worker_path(clt_bin_dir()),
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": os.path.join(run_dir, "home"),
        "LANG": os.environ.get("LANG") or "en_US.UTF-8",
        "TMPDIR": tmp, "TMPPREFIX": os.path.join(tmp, "zsh"),
        "USER": getpass.getuser(), "LOGNAME": getpass.getuser(),
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    }
    if ctx["kind"] == "codex":
        # codex reads its subscription auth from CODEX_HOME even with --ignore-user-config.
        env["CODEX_HOME"] = codex_home()
    else:
        env.update({
            "CLAUDE_CODE_TMPDIR": tmp,
            "CLAUDE_CONFIG_DIR": os.path.join(run_dir, "config"),
            "ANTHROPIC_BASE_URL": ctx["base"]["url"], "ANTHROPIC_API_KEY": "ollama",
            "ANTHROPIC_MODEL": ctx["model"], "ANTHROPIC_SMALL_FAST_MODEL": ctx["model"],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": ctx["model"], "ANTHROPIC_DEFAULT_SONNET_MODEL": ctx["model"],
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": ctx["model"],
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "MAX_THINKING_TOKENS": str(int(ctx.get("max_thinking") or DEFAULT_MAX_THINKING)),
        })
        if ctx.get("context_tokens"):
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(int(ctx["context_tokens"]))
    return env


def check_init(event, allowed_tools=ALLOWED_TOOLS):
    """Violations in a Claude Code stream's system/init event (empty means fine)."""
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


# -------------------------------------------------------------------- work area

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
    """Atomic: temp file in the same dir (mode 0600), then os.replace. Readers never see a partial file."""
    tmp = os.path.join(run_dir, "run.json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, os.path.join(run_dir, "run.json"))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


RUN_JSON_MIN_INTERVAL_S = 2.0


def sum_usage(per_msg):
    """{input, cache_read, output} summed over per-message usage dicts (null fields read as 0)."""
    tot = lambda k: sum(int(u.get(k) or 0) for u in per_msg)
    return {"input": tot("input_tokens"), "cache_read": tot("cache_read_input_tokens"), "output": tot("output_tokens")}


class Progress:
    """Live fields of run.json: tool_calls, usage, context_tokens and last_activity_at, written at most once per
    interval."""

    def __init__(self, run_dir, record, interval=RUN_JSON_MIN_INTERVAL_S, clock=time.monotonic):
        self.run_dir, self.record, self.interval, self.clock = run_dir, record, interval, clock
        self.last_write = None

    def tool_use(self, n):
        self.record["tool_calls"] = self.record.get("tool_calls", 0) + n
        self._touch()

    def usage(self, per_msg, last):
        """per_msg: every message's usage so far; last: the latest one, whose prompt is the live context size."""
        self.record["usage"] = sum_usage(per_msg)
        self.record["context_tokens"] = sum(int(last.get(k) or 0) for k in
                                            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        self._touch()

    def _touch(self):
        self.record["last_activity_at"] = time.time()
        now = self.clock()
        if self.last_write is None or now - self.last_write >= self.interval:
            self.last_write = now
            write_run_json(self.run_dir, self.record)


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


def prepare_run(repo, slug, ohome, kind, worker_bin, model=None, base=None, max_thinking=DEFAULT_MAX_THINKING,
                max_turns=DEFAULT_MAX_TURNS, context_tokens=None):
    """Build the isolated work area and the run record. Returns the run context."""
    validate_slug(slug)
    if model and not MODEL_RE.match(model):
        raise OffloadError(f"invalid model name {model!r}")
    os.makedirs(ohome, mode=0o700, exist_ok=True)
    root = validate_repo(repo, ohome)
    run_id = run_id_for(root, slug)
    run_dir = os.path.join(ohome, "runs", run_id)
    if os.path.lexists(run_dir):
        raise OffloadError(f"run directory already exists: {run_dir}")
    check_branch_free(root, slug, "/var/empty")
    os.makedirs(os.path.dirname(run_dir), mode=0o700, exist_ok=True)
    os.mkdir(run_dir, 0o700)
    for d in ("src", "tmp", "home", "config"):
        os.mkdir(os.path.join(run_dir, d), 0o700)
    ghome = os.path.join(run_dir, "home")
    ctx = {"run_id": run_id, "run_dir": run_dir, "repo": root, "slug": slug, "kind": kind, "model": model,
           "base": base, "worker_bin": worker_bin, "max_thinking": max_thinking, "max_turns": max_turns,
           "context_tokens": context_tokens,
           "git_dir": os.path.join(run_dir, "git"), "src": os.path.join(run_dir, "src"),
           "ghome": ghome}
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
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    ctx["record"] = {"run_id": run_id, "run_dir": run_dir, "repo": root, "slug": slug, "kind": kind,
                     "model": model, "branch": f"offload/{slug}", "base_sha": ctx["base_sha"],
                     "base_url": base["url"] if base else None, "status": "prepared", "created": time.time(),
                     "max_turns": max_turns, "worker": worker_bin, "context_window": context_tokens,
                     "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID")}
    write_run_json(run_dir, ctx["record"])
    return ctx


def init_worker_git(ctx):
    """Give the worker a local repo in src: init and a `base` commit through the safe-git wrapper."""
    gh = ctx["ghome"]
    gd = os.path.join(ctx["src"], ".git")
    git = ["--git-dir", gd, "--work-tree", ctx["src"]]
    safe_git(git + ["init", "-q"], gh)
    safe_git(git + ["config", "user.name", "offload"], gh)
    safe_git(git + ["config", "user.email", "offload@localhost"], gh)
    safe_git(git + ["config", "commit.gpgsign", "false"], gh)
    safe_git(git + ["add", "-A"], gh)
    safe_git(git + ["commit", "-q", "--no-verify", "--allow-empty", "-m", "base"], gh)


def commit_work(ctx):
    """Commit src/ onto branch `work` in the helper-owned repo. Returns head sha."""
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
    """Fetch `work` into the source repo as offload/<slug> without ever overwriting.

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
    """The unexpected ref changes seen around the fetch (empty means fine)."""
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


CLAUDE_PREAMBLE = ("You are working in a copy of the repo, in the current directory. git works locally: there is "
                   "no remote. Make temp files with `mktemp -d \"$TMPDIR/x.XXXXXX\"`. Write the code and tests the "
                   "brief asks for, run the tests, commit if you like, and finish with a short summary.\n\n")
CODEX_PREAMBLE = ("You are working in a copy of the repo at the current directory. git works locally; there is no "
                  "remote. Write the code and tests the brief asks for, run the tests, and finish with a short "
                  "summary of what you changed.\n\n")


def _claude_handler(out, per_msg, progress):
    def handle_line(raw):
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
                progress.usage(per_msg.values(), msg["usage"])
            n_tools = 0
            for blk in msg.get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    out["text"] = blk.get("text", "")
                elif isinstance(blk, dict) and blk.get("type") == "tool_use":
                    n_tools += 1
            if n_tools:
                progress.tool_use(n_tools)
        elif ev.get("type") == "result":
            out["result"] = ev
        return False
    return handle_line


def _codex_usage(u):
    """codex turn.completed usage -> the shape sum_usage/Progress expect."""
    return {"input_tokens": u.get("input_tokens"), "cache_read_input_tokens": u.get("cached_input_tokens"),
            "cache_creation_input_tokens": u.get("cache_write_input_tokens"), "output_tokens": u.get("output_tokens")}


def _codex_handler(out, per_msg, progress):
    def handle_line(raw):
        try:
            ev = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(ev, dict):
            return False
        t = ev.get("type")
        if t == "thread.started":
            out["saw_init"] = True
        elif t == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                out["text"] = item["text"]
            elif item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "patch_apply"):
                progress.tool_use(1)
        elif t == "turn.completed":
            u = ev.get("usage")
            if isinstance(u, dict):
                mapped = _codex_usage(u)
                per_msg[len(per_msg)] = mapped
                progress.usage(per_msg.values(), mapped)
                out["result"] = ev
                out["turns"] = out.get("turns", 0) + 1
        elif t == "error":
            out["error_event"] = sanitize_untrusted(str(ev.get("message") or ev), 500)
        return False
    return handle_line


def launch_worker(ctx, brief_text, timeout_s):
    """Run the worker, reading its event stream incrementally. Returns an outcome dict."""
    env = build_worker_env(ctx)
    log_path = os.path.join(ctx["run_dir"], "worker.jsonl")
    last_msg = os.path.join(ctx["run_dir"], "last_message.txt")
    out = {"init_problems": [], "saw_init": False, "timed_out": False, "result": None, "usage": {},
           "text": "", "log_path": log_path, "survivors_killed": [], "turns": 0}
    per_msg = {}
    buf = [b""]
    progress = Progress(ctx["run_dir"], ctx["record"])
    if ctx["kind"] == "codex":
        argv = build_codex_argv(ctx["worker_bin"]["real"], ctx["src"], ctx["model"], last_msg)
        preamble = CODEX_PREAMBLE
        handle_line = _codex_handler(out, per_msg, progress)
    else:
        argv = build_claude_argv(ctx["worker_bin"]["real"], ctx["max_turns"])
        preamble = CLAUDE_PREAMBLE
        handle_line = _claude_handler(out, per_msg, progress)

    with open(log_path, "wb") as log, open(os.path.join(ctx["run_dir"], "worker.stderr"), "wb") as errf:
        ctx["record"].update(status="running", started_at=time.time(), tool_calls=0)
        write_run_json(ctx["run_dir"], ctx["record"])
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
            if len(buf[0]) > 16 * 1024 * 1024:
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
    # Prefer the final-message file (codex) for the summary text.
    try:
        with open(last_msg, encoding="utf-8", errors="replace") as f:
            txt = f.read().strip()
            if txt:
                out["text"] = txt
    except OSError:
        pass
    res = out["result"] or {}
    if ctx["kind"] == "codex":
        out["usage"] = sum_usage(list(per_msg.values()))
    else:
        u = res.get("usage") if isinstance(res.get("usage"), dict) else None
        out["usage"] = sum_usage([u] if u is not None else list(per_msg.values()))
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
    return {"run_id": rec["run_id"], "run_dir": rd, "repo": rec["repo"], "slug": rec["slug"],
            "kind": rec.get("kind", "claude"), "model": rec.get("model"), "worker_bin": rec.get("worker"),
            "git_dir": os.path.join(rd, "git"), "src": os.path.join(rd, "src"),
            "ghome": os.path.join(rd, "home"), "max_thinking": DEFAULT_MAX_THINKING}


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
    with open(args.brief, encoding="utf-8") as f:
        brief = f.read()
    slug = validate_slug(args.slug or default_slug(args.brief))
    kind = args.kind or "claude"
    if kind not in KINDS:
        raise OffloadError(f"invalid --kind {kind!r}; choose from {', '.join(KINDS)}")
    base = None
    context_tokens = None
    if kind == "codex":
        if args.worker or args.base_url:
            raise OffloadError("--kind codex uses your codex subscription; don't pass --worker or --base-url")
        worker_bin = resolve_codex()
        model = args.model
    else:
        worker_bin = resolve_claude()
        if args.worker:
            if args.base_url or args.model:
                raise OffloadError("--worker sets the base URL and model; don't pass --base-url or --model with it")
            w = resolve_worker(args.worker)
            if not worker_up(w["base_url"], w["model"]):
                raise OffloadError(f"worker {args.worker!r} is down: {w['base_url']} does not answer for "
                                   f"{w['model']}; pick another worker (offload.py workers) or the cloud model")
            args.base_url, args.model = w["base_url"], w["model"]
            context_tokens = w.get("context_tokens")
        model = args.model or DEFAULT_MODEL
        base = resolve_base_url(args.base_url)

    ctx = prepare_run(args.repo, slug, ohome, kind, worker_bin, model, base, args.max_thinking_tokens,
                      args.max_turns, context_tokens)
    rec = ctx["record"]

    def finish(status, extra=None):
        rec["status"] = status
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        rec["ended_at"] = time.time()
        rec.update(extra or {})
        write_run_json(ctx["run_dir"], rec)

    out = {"status": None, "run_id": ctx["run_id"], "repo": ctx["repo"], "branch": f"offload/{slug}",
           "base_sha": ctx["base_sha"], "head_sha": None, "kind": kind, "model": model, "turns": 0,
           "duration_s": 0, "usage": {"input": 0, "cache_read": 0, "output": 0}, "est_cost_usd": None,
           "untrusted_diffstat": "", "untrusted_sensitive_paths": [], "untrusted_worker_text": "",
           "format_chars": 0, "survivors_killed": [], "log_path": os.path.join(ctx["run_dir"], "worker.jsonl")}
    head = None
    try:
        init_worker_git(ctx)
        w = launch_worker(ctx, brief, args.timeout_min * 60)
        out.update(turns=w["turns"], usage=w["usage"], est_cost_usd=estimate_cost(model, w["usage"]),
                   survivors_killed=w["survivors_killed"])
        if kind == "claude" and (w["init_problems"] or not w["saw_init"]):
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
    """Run a command against the committed `work` tree in a throwaway checkout. The worker's code runs
    directly (no jail): only use this once you have reviewed the diff, or on code you are willing to run."""
    rec = load_run(ohome, args.run)
    ctx = ctx_from_record(rec)
    ctx["base_sha"] = rec["base_sha"]
    cmd = list(args.test_cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        raise OffloadError("no command given: offload.py test --run ID -- CMD...")
    n = 1
    while True:
        testsrc = os.path.join(ctx["run_dir"], f"testsrc-{n}")
        try:
            os.mkdir(testsrc, 0o700)
            break
        except FileExistsError:
            n += 1
    killed = []
    try:
        # Test what was committed, not whatever the worker left in src/.
        proc = subprocess.Popen(safe_git_argv(["--git-dir", ctx["git_dir"], "archive", "work"], ctx["ghome"]),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        extract_tar_safely(proc.stdout, testsrc)
        err = proc.stderr.read()
        if proc.wait() != 0:
            raise OffloadError(f"git archive failed: {err.decode(errors='replace')[:300]}")
        env = build_worker_env(dict(ctx, base={"url": DEFAULT_BASE_URL}))
        proc = subprocess.Popen(cmd, cwd=testsrc, env=env, shell=False,
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
        runs.append({k: r.get(k) for k in ("run_id", "status", "repo", "branch", "kind", "model", "head_sha",
                                            "created")})
    emit({"runs": runs})
    return 0


def cmd_workers(args, ohome):
    emit({"workers": [{"name": n, "model": w["model"], "base_url": w["base_url"],
                       "context_tokens": w.get("context_tokens"), "up": worker_up(w["base_url"], w["model"])}
                      for n, w in sorted(list_workers().items())]})
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
    r.add_argument("--kind", choices=KINDS, default="claude")
    r.add_argument("--model")
    r.add_argument("--worker", help="a named worker from config `workers` (claude kind; sets base URL and model)")
    r.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    r.add_argument("--timeout-min", type=float, default=DEFAULT_TIMEOUT_MIN)
    r.add_argument("--max-thinking-tokens", type=int, default=DEFAULT_MAX_THINKING)
    r.add_argument("--base-url")
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
    sub.add_parser("workers")
    return ap


COMMANDS = {"run": cmd_run, "test": cmd_test, "diff": cmd_diff, "list": cmd_list, "status": cmd_status,
            "discard": cmd_discard, "workers": cmd_workers}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args, offload_home(args.home)) or 0
    except OffloadError as e:
        emit({"status": "error", "error": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
