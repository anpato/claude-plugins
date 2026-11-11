# offload — design

Status: rewritten 2026-11-10 after the user's decision to drop offload's own sandbox and egress proxy
(user decision 2026-11-10). Earlier versions jailed the worker in `sandbox-exec` behind a filtering proxy;
that machinery is gone. See the end for what changed and why.

## Goal

Delegate a well-specified build task (code plus tests, written to a reviewed brief) from a Claude Code
session to another model, run it in an isolated git copy, and review the result before merge. The
delegating session spends tokens only on the brief and the review. Works in any git repo; tested on macOS
(nothing is macOS-specific any more except the best-effort leftover-process sweep, so Linux likely works but
is untested).

Two worker kinds:

- **claude** (default): Claude Code (`claude -p … --output-format stream-json`) pointed via
  `ANTHROPIC_BASE_URL` at a model on Ollama — the cloud model `kimi-k3:cloud` or a local GPU worker.
- **codex**: OpenAI `codex exec --json` on the user's ChatGPT subscription (`~/.codex/auth.json`,
  `auth_mode: chatgpt`). Flat-rate, so no per-token cost estimate.

## Non-goals

- Trust-boundary design or security review. Those stay on the delegating human.
- Merging, pushing or opening PRs. Merge is the trust point: worker code runs in your environment only
  after you review the diff and merge it yourself.
- Sandboxing the worker. offload no longer provides a jail (see Threat model).

## Threat model

The worker and everything it writes (code, tests, result text, commit content) are **untrusted**. offload
does **not** confine the worker process. The controls that remain are:

1. **You already trust the worker's model.** The claude kind runs against Ollama Cloud or your own GPU box;
   the codex kind runs codex, which applies its own Seatbelt sandbox (`-s workspace-write`) to the shell
   commands it generates. Running these on your machine is the same exposure as running that tool by hand.
2. **Nothing the worker writes runs until you merge.** The worker commits into a helper-owned bare clone,
   which is fetched into your repo as exactly one new branch `offload/<slug>`. It never writes to your
   repo's refs or working tree. Every other ref is compared around the fetch; any change is a
   `ref_violation` and fails the run.
3. **You review the diff first.** `offload.py diff` prints it with sensitive paths flagged; returned text
   is truncated, stripped of control characters and marked untrusted so the delegating session treats it
   as data, not instructions. `offload.py test` is the only sanctioned way to run worker code — and it runs
   **unsandboxed**, so only after review.
4. **Every git command runs through one wrapper** (`safe_git`) with `env -i`, hooks/global-config/fsmonitor
   /attribute-files disabled, and `GIT_TERMINAL_PROMPT=0`.

What is explicitly **not** defended, by the user's decision: the worker reading `$HOME`, credentials or
other projects during the run and exfiltrating through its own model calls; the worker running arbitrary
commands on the machine while it works. Use offload on repos and models you are willing to run locally.

## Architecture

```
<OFFLOAD_HOME = ~/.cache/offload>/runs/<run-id>/
  git/        helper-owned bare clone of the repo at <base>; holds the worker's commit on branch `work`
  src/        exported tracked files at <base>; the worker's cwd and only intended write area
  testsrc-N/  per-`test` export of the committed `work` branch; removed after the test
  tmp/        per-run TMPDIR
  home/       the worker's HOME (fake, so it doesn't pick up the user's claude config); for codex, auth
              still comes from the real ~/.codex via CODEX_HOME
  config/     CLAUDE_CONFIG_DIR (claude kind, empty)
  run.json    run record: repo root, base sha, slug, kind, model, status, timings, usage, session id
  worker.jsonl    raw event stream from the worker
  last_message.txt  the worker's final message (codex writes it via -o; used as the summary)
  worker.stderr   the worker's stderr
```

`run-id` = `<sha256(repo_root)[:8]>-<slug>`; slug matches `^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$`.

## Run flow (`offload.py run --repo R --brief F [--kind K] [--model M] [--worker W] …`)

1. **Validate.** `R` is a git work tree whose realpath is not `$HOME`, `~/.claude`, or under `OFFLOAD_HOME`.
   The run dir doesn't exist. `refs/heads/offload/<slug>` and `refs/heads/offload` are free. The worker
   binary resolves: claude on PATH, or codex on PATH with `~/.codex/auth.json` present.
2. **Build the work area** (`prepare_run`). All git goes through `safe_git`.
   - `git clone --bare --no-local --no-hardlinks R runs/<id>/git`
   - `git --git-dir=git archive <base> | tar -x` into `src/` via Python's `tarfile` with member-path checks
     (tracked files only, no escape, no absolute/hardlink/device members).
   - `git --git-dir=git branch work <base>`; `symbolic-ref HEAD refs/heads/work`.
   - `init_worker_git`: give `src/` a local repo with a `base` commit so the worker has working git.
3. **Launch the worker** directly (`Popen`, `start_new_session=True`, cwd `src/`), no jail. The brief
   (with a short preamble) is fed on stdin.
   - **claude:** `claude -p --bare --strict-mcp-config --setting-sources "" --output-format stream-json
     --max-turns N --permission-mode dontAsk --tools/allowedTools <6> --disallowedTools <web/agent/skill>`,
     env `ANTHROPIC_BASE_URL` = the resolved base URL (direct, no proxy), `ANTHROPIC_API_KEY=ollama`. The
     stream's `system/init` event is checked (mcp_servers empty, permissionMode dontAsk, only the allowed
     tools); a mismatch fails the run (`init_check_failed`).
   - **codex:** `codex exec --json --skip-git-repo-check --ignore-user-config --ignore-rules -C src
     -s workspace-write -o last_message.txt [-m MODEL]`, env `CODEX_HOME` = real `~/.codex` for auth.
     JSONL events parsed: `thread.started`, `item.completed` (agent_message → summary text; command/file/
     tool items → tool count), `turn.completed` (usage).
   - Both: output is pumped incrementally with a hard timeout; `run.json` gets live `tool_calls`/`usage`/
     `context_tokens`; on exit, survivors (descendants, process group, or anything with cwd under the run
     dir) are SIGKILLed.
4. **Collect** (`commit_work` → `fetch_work`). Delete any `src/.git` the worker made, `add -A`, commit onto
   `work` in the bare repo, then fetch `work` into the source repo as `offload/<slug>` without overwriting
   (fetch into a temp ref, `update-ref` with expected-absent old value, delete the temp ref). The ref
   snapshot around the fetch yields `ref_violations`.
5. **Report.** `status` is `ok` / `timeout` / `failed` / `init_check_failed` (claude) / `ref_violation` /
   `error`. The output carries the diffstat, numstat, sensitive paths, the worker's summary text (all
   sanitized and marked untrusted), token usage, and `est_cost_usd` (null for codex).

## `test` and `diff`

- `test --run ID -- CMD…` exports the committed `work` branch into a throwaway `testsrc-N/` and runs CMD
  there directly (no network isolation, no jail). This executes worker code, so it is for after review.
- `diff --run ID [-- PATH…]` prints the sanitized diff between `base_sha` and `work`, then a JSON trailer
  with numstat, sensitive paths and a truncation flag.

## What changed from the sandboxed design (and why)

The earlier design jailed the worker in a generated `sandbox-exec` (SBPL) profile and routed its only
network through an in-process filtering proxy that also mirrored pypi/npm/crates/maven. That delivered read
confinement, write confinement and network confinement for a worker that was Claude Code (which has no
sandbox of its own). It was ~1400 lines of security-critical, macOS-only, empirically fragile code (SBPL
size/regex limits, single-port deny leaks).

It was removed (user decision 2026-11-10) on the view that each supported backend is already trusted —
Ollama Cloud and local models are the user's own infrastructure, and codex self-sandboxes — so offload's
own jail was redundant maintenance. The cost accepted is the loss of read confinement during a run (the
worker can read the disk and exfiltrate via model calls). The git-isolation and review-before-merge
controls, which are what actually gate code into the user's tree, are unchanged.
