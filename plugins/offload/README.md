# offload

Delegate a well-specified build task (code plus tests, written to a reviewed brief) from a Claude Code session to another model, in an isolated git copy, then review the result before you merge. The delegating session spends tokens only on the brief and the review. Two workers:

- **`--kind claude`** (default): Claude Code pointed at a model on Ollama — the cloud model (`kimi-k3:cloud`) or a local GPU box.
- **`--kind codex`**: OpenAI `codex exec` on your ChatGPT subscription (flat-rate).

The worker is **not** jailed by offload. It runs with network and executes commands directly; the safety model is a model you already trust, nothing-runs-until-you-merge, and reviewing the diff. See [DESIGN.md](./DESIGN.md).

## Requirements

- macOS, Python 3 (stdlib only) and git. (No macOS-specific jail any more — Linux likely works, but only macOS is tested; the leftover-process sweep uses macOS tool paths and degrades to a no-op elsewhere.)
- For `--kind claude`: the `claude` binary on `PATH`, and a local `ollama` daemon on `localhost:11434` signed in to Ollama Cloud (or a local GPU worker). Each claude run checks the worker's init event (tools, permission mode, no MCP) and aborts on a mismatch.
- For `--kind codex`: the `codex` binary on `PATH` and `codex login` already done (`~/.codex/auth.json`).

## Install

```
claude plugin install offload@apato-plugins
```

## Usage

Ask Claude to offload a task, or use the skill (`/offload:offload`). It writes a five-section brief, runs the helper, and reviews the result.

```
offload.py run --repo . --brief brief.md --slug my-task            # claude kind, cloud model
offload.py run --repo . --brief brief.md --kind codex              # codex on your subscription
offload.py diff --run ID [-- PATH...]    # read the diff, with sensitive paths flagged
offload.py test --run ID -- python3 -m pytest   # run the committed code (NOT sandboxed — review first)
offload.py list | status --run ID | discard --run ID
```

A run leaves the branch `offload/<slug>` in your repo. Nothing is merged or pushed; merge it yourself after review. Run data lives in `~/.cache/offload` (override with the global `--home DIR`, used by the tests).

## Local GPU workers (claude kind)

Ollama on your own GPU machines can take runs too. Name them in `~/.config/offload/config.json`:

```json
{"workers": {
  "gpu-1": {"base_url": "http://localhost:11435", "model": "qwen3.6:35b-a3b-ctx131072", "context_tokens": 131072},
  "gpu-2": {"base_url": "http://localhost:11436", "model": "qwen3.6:35b-a3b-ctx131072", "context_tokens": 131072}
}}
```

A worker's `base_url` counts as allowlisted. Reach a LAN box through an SSH tunnel (`ssh -N -L 11435:127.0.0.1:11434 gpu-1`) so the URL stays loopback; a non-loopback URL still needs https. `offload.py workers` lists them with whether each is up, and `offload.py run --worker gpu-1 ...` uses one (it sets `--base-url` and `--model`) and refuses before any setup if that worker is down. Set `context_tokens` to the model's real window (Ollama's `num_ctx`); offload passes it as `CLAUDE_CODE_MAX_CONTEXT_TOKENS` so Claude Code compacts before the model runs out. Give each worker one run at a time: Ollama's prompt cache holds one conversation, and a second run evicts it.

Other base URLs are read only from `~/.config/offload/config.json` (`{"base_urls": ["https://..."]}`), never from env or repo files, and a non-loopback host must use https. The worker connects to that URL directly (no proxy), so a remote endpoint would need its own credential — the shipped config is all loopback.

## Security model, briefly

The worker and all it writes are untrusted, and offload does **not** sandbox the worker. Instead:

- **You already trust the worker's model** — Ollama Cloud, your own GPU box, or codex (which runs its own Seatbelt sandbox for its shell). The worker runs on your machine with network access and can read files and run commands, the same as if you ran that tool by hand.
- **Nothing the worker writes runs until you merge.** The worker never touches your repo: a helper-owned bare clone holds its commit, fetched into your repo as one new branch `offload/<slug>`. Every other ref is compared around the fetch and any change is reported as a `ref_violation`.
- **You review the diff first.** `offload.py diff` prints it with sensitive paths flagged; returned text is truncated and stripped of control characters and the skill treats it as data. `offload.py test` is the only sanctioned way to run worker code, and it runs **unsandboxed** — only after review.
- Every git command runs through one wrapper that disables hooks, global config, fsmonitor and attribute files.

What offload does **not** defend against (removed with the sandbox, by design): the worker reading your home/credentials/other projects during the run, exfiltrating via its own model calls, or running arbitrary commands on your machine while it works. Use it on repos and models you are willing to run locally.

## What the worker can do

- Work like its underlying tool in a repo copy: `git` (local, no remote), language toolchains, temp files, and the open network (install dependencies normally).
- Take up to 60 turns by default for the claude kind (`--max-turns`).
- Dependency and config files it changes (`requirements.txt`, `Cargo.toml`, `go.mod`, `pom.xml`, `*.gradle`, hooks, CI, `.codex`, …) are flagged in `untrusted_sensitive_paths` for review.

## Limitations

- The worker sees tracked files at HEAD only. Secrets committed to the repo are visible to it and leave through its model calls.
- `--kind codex` uses your ChatGPT subscription and its usage limits; `--kind claude` cloud runs bill to your Ollama account.
