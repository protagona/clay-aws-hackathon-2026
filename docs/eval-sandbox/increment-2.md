# Increment 2: STAC tools, process robustness, batch runner

**Track:** Quick (three small, well-understood additions to an existing design)   **Status:** Draft   **Date:** 2026-10-30

These are the user's picks for mechanical and functional work. The scoring work waits for Clay's rubric.

## Problem

1. **One tool server.** The agent can only *discover* datasets (RODA). It can't find actual scenes for an area and time range. So multi-server tool routing and tracing are unproven, which is backlog item B-5.
2. **Process rough edges.**
   - A `foundry run` killed with SIGKILL leaves its MCP server processes running.
   - The MCP start-failure path prints a `RuntimeWarning: coroutine … never awaited`.
   - The UI never updates while a run or evaluation is in progress.
3. **No batch runner.** Every multi-run comparison so far needed a hand-written shell loop with hand-made configs and hand-made tables.

## Acceptance criteria

### Second MCP server: STAC (`foundry.mcp_servers.stac`)

- [ ] **S1:** `python -m foundry.mcp_servers.stac` is a stdio MCP server with three tools: `list_collections`, `search_items` and `get_item`. It needs no credentials. The STAC API comes from `STAC_API_URL`, default `https://earth-search.aws.element84.com/v1` (Earth Search, public).
- [ ] **S2:** `search_items(collection, bbox, datetime, limit=5, max_cloud_cover=None)` returns compact JSON:
  - `matched`: the total number of matching items
  - `returned`
  - per item: `id`, `collection`, `datetime`, `cloud_cover`, `bbox`, the asset keys, and the hrefs of a few key assets

  `limit` is capped at 20. For 5 Sentinel-2 items the response is **≤ 6,000 characters**; the raw STAC response is about 110 KB.
- [ ] **S3:** Bad input (unknown collection, a malformed bbox or datetime), HTTP errors and timeouts (20 s) come back as **MCP tool errors**, with a message saying what to fix. The server never crashes.
- [ ] **S4:** `config/offline.toml` and `config/bedrock*.toml` register both servers. `run.json.mcp_servers` lists both, and STAC tool calls appear in the trace with `server: "stac"`.
- [ ] **S5:** A new prompt, `adb-006`, asks for specific cloud-free scenes. A live Kimi K3 run on it calls at least one STAC tool. This is a best-effort demonstration; agent behavior isn't deterministic.

### Process robustness

- [ ] **R1:** When `foundry run` is killed with SIGKILL mid-run, every MCP server process it started is gone **within 5 s**. Each server runs under a small reaper process (`python -m foundry.harness.reaper -- <command…>`) that watches its parent and kills the server's process group when the parent disappears.
- [ ] **R2:** Normal runs are unchanged:
  - a server's stderr still goes to `mcp-<name>.log`
  - `MCPClient.stop` still terminates servers
  - a server that crashes on start is still recorded as AC9c
  - the full existing suite passes
- [ ] **R3:** A `foundry run` whose MCP server crashes on start prints no `RuntimeWarning` on stderr. The warning comes from a strands 1.62.0 bug (`mcp_client.py:684-690` schedules a coroutine on a stopped loop). Only that exact message is filtered, and the failure itself is still recorded (AC9c). *(The AC was originally "passes with `-W error`"; pytest's `-W` overrides module-level filters in-process, so the AC now checks the user-visible CLI output.)*
- [ ] **R4:** UI auto-refresh, with no JavaScript (`<meta http-equiv="refresh" content="3">`):
  - The index refreshes while any card is `running` or `evaluating`.
  - A run page refreshes while that run is `running` or being evaluated.
  - Neither refreshes otherwise.

### Batch runner (`foundry batch`)

- [ ] **B1:**
  ```
  foundry batch --config <file> [--config <file> …] [--models <id,id…>]
                --prompts <id,id…|all> [--repeats N] [--concurrency K]
                [--evaluate [--eval-config <file>]] [--max-cost USD] --out <dir>
  ```
  `--models` derives one config per model from the first `--config` by changing only `[model] id`. Derived configs are written to `<out>/configs/` for provenance. Without `--models`, each `--config` is one variant.
- [ ] **B2:** Each job gets a deterministic run id, `<variant>_<prompt>_r<k>`, under `<out>/runs/`. Re-running the same command **resumes**: it skips jobs whose run finished and, with `--evaluate`, is evaluated. Jobs left `running` by a crash are re-run under a fresh `-retry<n>` id; the crashed directory is kept.
- [ ] **B3:** **Budget.** Before launching each job, the batch adds up the recorded cost of its runs so far: the trace's `cost_usd` plus `scores.json`'s `evaluation.cost_usd`. If that has reached `--max-cost`, it launches nothing more, and the remaining jobs are reported as `skipped (budget)`. Overshoot is bounded by the jobs already in flight (at most `concurrency − 1` runs).
- [ ] **B4:** **Concurrency.** Never more than `--concurrency` runs execute at once; the default is 2.
- [ ] **B5:** **Summary.** `<out>/summary.csv` and `<out>/summary.md` get one row per job: variant, model, prompt, repeat, status, model calls, tool calls, tool errors, tokens in and out, cost, workflow, output, overall, duration, and run directory. `summary.md` also has a per-variant aggregate: succeeded out of total, mean overall, mean cost per run, and mean model calls. The summary is rewritten as jobs finish, so it is valid if the batch is interrupted.
- [ ] **B6:** **Exit codes.**
  - 0 when every job ran, whatever each run's own status.
  - 1 when any job couldn't run (its `foundry run` exited 2), or when jobs were skipped by the budget.
  - 2 on usage errors: an unknown prompt, a missing config, or a bad `--models` value.
- [ ] **B7:** **Testing.** Tested offline against the fault server: resume, budget stop, concurrency cap, and summary contents. One live batch is shown in Verify.

## Non-goals

- Scoring, rubric or judge changes. These wait for Clay's rubric.
- A STAC client that downloads or processes pixels. The server returns metadata and hrefs only.
- Distributed or cloud batch execution (A-3). The batch runs locally as subprocesses.
- Third-party MCP packages. Running unvetted MCP servers from PyPI or npm was refused by the session's safety check, and our own ~150-line server built on the MCP SDK plus `httpx` (both already dependencies) is easier to trust.

## Assumptions

- Earth Search v1 stays public and keyless. It was verified on 2026-10-30: STAC 1.0.0, item-search conformance, 9 collections including `sentinel-2-l2a`, `landsat-c2-l2` and `naip`. If it changes, `STAC_API_URL` points the server elsewhere, for example Microsoft Planetary Computer.
- On macOS there's no parent-death signal, so the reaper polls `getppid()` every 0.5 s.

## Plan

1. **R1–R4:** reaper module plus MCP launch wrapper, the warning fix, UI meta refresh, tests. Commit.
2. **S1–S5:** STAC server, config registration, the `adb-006` prompt, tests against a local fake STAC API (offline) plus one network test against Earth Search. Commit.
3. **B1–B7:** `foundry batch`, tests. Commit.
4. **Verify:** full suite, a live batch (2 models × 2 prompts, with evaluation, `--max-cost 2`), and a SIGKILL check against real RODA. Update README and the handoff.
