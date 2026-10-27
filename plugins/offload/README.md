# offload

Delegate a well-specified build task (code plus tests, written to a reviewed brief) from a Claude Code session to a cheaper model on Ollama Cloud (default `kimi-k3:cloud`). The delegating session spends tokens only on the brief and on reviewing the result. The worker is Claude Code itself pointed at the local Ollama daemon, run inside a macOS `sandbox-exec` profile.

Design, threat model and evidence: [DESIGN.md](./DESIGN.md).

## Requirements

- macOS with `/usr/bin/sandbox-exec`. Other platforms are refused.
- Python 3 (stdlib only) and git.
- The `claude` binary on `PATH`. Only versions in `TESTED_CLAUDE_VERSIONS` (currently `2.1.312`) run unless you pass `--allow-untested-claude`.
- A local `ollama` daemon on `localhost:11434`, signed in to Ollama Cloud, on a plan that includes the cloud model (Pro allows 3 concurrent runs).

## Install

```
claude plugin install offload@apato-plugins
```

## Usage

Ask Claude to offload a task, or use the skill (`/offload:offload`). It writes a five-section brief, runs the helper, and reviews the result.

```
offload.py run --repo . --brief brief.md --slug my-task
offload.py diff --run ID [-- PATH...]    # read the diff, with sensitive paths flagged; PATHs to review a big diff per file
offload.py test --run ID -- python3 -m pytest   # runs the committed code, sandboxed, no network; the only sanctioned way to run worker code before merge
offload.py list | status --run ID | discard --run ID
```

A run leaves the branch `offload/<slug>` in your repo. Nothing is merged or pushed; merge it yourself after review.

Other base URLs are read only from `~/.config/offload/config.json` (`{"base_urls": ["https://..."]}`), never from env or repo files, and a non-loopback host must use https. Run data lives in `~/.cache/offload` (override with the global `--home DIR`, used by the tests).

## Security model, briefly

The worker model and all it writes are untrusted.

- The worker runs under `sandbox-exec` in a per-run directory. It cannot read your home, other projects, `~/.claude*`, credentials, or the run's own git repo, and it can write only inside the run directory.
- The worker can reach one loopback port only: a filtering proxy in the helper that forwards just `POST /v1/messages` and `/v1/messages/count_tokens` to Ollama. Other Ollama endpoints (pull, delete, tags), unix sockets, the clipboard, DNS and AppleEvents are blocked. There are no MCP servers, no web tools and no subagents; the helper checks the worker's `init` event and kills it on any mismatch.
- The worker never touches your repo. A helper-owned bare clone holds its commit, which is fetched into your repo as one new branch. Every other ref is compared around the fetch.
- Every git command runs through one wrapper that disables hooks, global config, fsmonitor and attribute files.
- Text coming back is truncated and stripped of control characters, and the skill tells Claude to treat it as data.

## What the worker can do

- Work like Claude in a repo: `git` (local, no remote), `python3`, temp files (`mktemp -d "$TMPDIR/x.XXXXXX"`) and zsh heredocs all work in the sandbox. The Command Line Tools come first on its PATH.
- Install dependencies from pypi, npm, crates.io and maven through the same loopback port. The proxy serves read-only `GET`/`HEAD` on six fixed routes (`/r/pypi/`, `/r/pyfiles/`, `/r/npm/`, `/r/crates-index/`, `/r/crates-static/`, `/r/maven/`), rebuilds the headers, blocks private addresses and does not follow redirects. There is no CONNECT and no `HTTPS_PROXY`. Go modules are not fetched: use `--allow-read ~/go/pkg/mod`.
- Read `~/.rustup` and `~/.cargo/registry` by default when they exist (public content only).
- Take up to 60 turns by default (`--max-turns`).
- Dependency files it changes (`requirements.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `*.gradle`) are flagged for review.

## Limitations

- The worker has no general network. Registry downloads go through the proxy; anything else must be vendored or exposed read-only with `--allow-read`.
- The worker sees tracked files at HEAD only. Secrets committed to the repo are visible to it and leave through its model calls.
- The worker can still send extra `/v1/messages` calls through the proxy, which bill to your Ollama account.
- `--allow-read` accepts only an allowlist of toolchain caches (`~/go/pkg/mod`, `~/.cargo/registry`, `~/.cargo/git`, `~/.rustup`, `~/.m2/repository`, `~/.gradle/caches`, `~/.npm/_cacache`, and the repo's `node_modules`, `vendor`, `.venv`).
- macOS only. Linux is not supported.
