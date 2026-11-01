# One code path for the laptop and AWS — Design

**Spec:** ./spec.md   **Status:** Current (gate: 3 passes; the 2 blockers left after pass 3 were brought to the user, who approved the resolutions below, 2026-11-01)   **Date:** 2026-11-01

## Overview

Services stay as they are, but every place where they branch on the cloud moves behind four seams. Each seam has a laptop and a cloud implementation, which `server.create_app` (and the worker entry point) wires in:

| Seam | Today | After |
|---|---|---|
| State: fresh reads, project writes, run records, uploads | `cloud_state.*` calls and `on(ws)` branches in 7 services | `state.of(ws)`: a `LocalState` or a `CloudState` |
| Board | `cards()`/`board_kpis()` on the laptop, `board_from_index()` in the cloud | one `runs.board()` over `state.of(ws).board_rows()`, with cards built by `services/cards.py` |
| Jobs | string kinds and `if kind == …` in 3 modules | `services/job_kinds.py`, read by both job backends and the worker |
| Batches | the `engine/batch.py` loop on the laptop, `workers.batch_*` re-implementing it in the cloud | one engine module: `prepare`, `run_item`, `write_rows`, `finalize`, called by both |

Both setups then run the same API tests through one fixture (`tests/backends.py`). The cloud API starts every request with an empty local copy, so a missed seam fails CI.

## Architecture

```
api/routes ──▶ services (runs, prompts, rubrics, batches, imports, uploads, tools, …)
                  │ state.of(ws).*     jobs.*          job_kinds.kind()
                  ▼                     ▼
      services/state.py           services/jobs.py
        LocalState (files)          LocalBackend (subprocesses)
      services/cloud_state.py     services/cloud_jobs.py     services/workers.py
        CloudState (mirror, store)  CloudBackend (JOB items)    (worker: run_job, batch_*, reindex)
      services/cards.py: card_for · card_extras · summary_for · board_row   (no jobs, no state imports)
                  └───────── engine/batch.py (prepare · run_item · write_rows · finalize · rows_from_csv) ─────────┘
```

**Services import order (no cycles), from leaf to top:**
- `errors`, `workspace`
- `job_kinds`
- `configs`, `jobs`, `stats`, `run_view`, `cards`, `uploads`
- `state`
- `cloud_jobs`, `cloud_state`
- `rubrics`, `batches`, `imports`
- `runs`
- `workers`
- `prompts`, `dashboard`
- `tools`

`state.py` imports `jobs` (for `exclusive`). `cloud_state.py` imports `state`, `jobs`, `cards` and `cloud_jobs`. Nothing below them imports them.

**Backend modules:** `services/cloud_state.py`, `services/cloud_jobs.py` and `services/workers.py`. The rule covers `services/` and `api/` only; entry points (`server.py`, `lambda_*.py`, `cloud_boot.py`) and `scripts/` are wiring and may do all three.
- Only they may import `foundry.data.store`, `foundry.data.mirror` or `foundry.data.index`.
- Only they may read an attribute named `cloud` on any expression (the AST check: `ast.Attribute` with `attr == "cloud"`).
- Outside them, only `server.py`, `lambda_worker.py`, `lambda_api.py` and `cloud_boot.py` may import them.

`tests/test_architecture.py` gets this rule, plus a negative self-test for each of the three checks.

## Decisions

### ADR-1: State behind `state.of(ws)`

- **Context:** about 40 sync calls across services, each one a contributor must remember.
- **Options:**
  1. A state object resolved from the workspace, with two implementations.
  2. Wrapping the data layer, so reads pull.
  3. Pulling everything at request start.
- **Decision:** 1.
  - Option 2 breaks the layer rules: data would need the store, and per-file pulls are slower than per-tree pulls.
  - Option 3 is too slow: a cold pull of every run took 26–34 s, per the `cloud_state.PULL_THREADS` comment.
- **The resolver:** `services/state.py` defines `LocalState`, `LOCAL = LocalState()`, `of(ws) -> ws.state or LOCAL` (the `jobs.backend(ws)` pattern; `Workspace.state` defaults to None, so `workspace.py` imports nothing new), and `expecting(base_version)`, which is moved from `cloud_state`. `CloudState(store, mirror)` lives in `services/cloud_state.py`.
- **The interface:** every method takes `ws` as its first argument, omitted in the table.

  | Method | LocalState | CloudState (from today's code) |
  |---|---|---|
  | `fresh_run(run_id)` | no-op | `pull_run` |
  | `fresh_runs()` | no-op | `pull_runs` |
  | `fresh_project(task)` | no-op | `pull_project` (skipped inside the same task's write, as today) |
  | `fresh_projects()` | no-op | `pull_projects` |
  | `fresh_batches()` | no-op | `pull_batches` |
  | `fresh_tools()` | no-op | `pull_tools` |
  | `project_version(task) -> int \| None` | None | `mirror.seen` |
  | `project_write(ws, task, hold_starts=False)` (context manager) | plain, or `jobs.exclusive` when `hold_starts` (today's local branch) | today's cloud branch: lease, pull, `expecting` check, push |
  | `record_run(run_id) -> bool` | no-op, returns False | `mirror.push_dir(..., attrs=index.run_attrs(card, summary))` with the row from `cards.board_row`. That's today's `index_run`, and it pushes changed files with the row. Returns True |
| `refresh_row(run_id) -> str` | — (cloud only, used by re-indexing) | builds the row from the pulled files, compares it with the stored card and summary, and when they differ writes them with a conditional `Update` of the TREE item (`version` equal to the version it pulled, and the version not bumped). Returns "same", "updated" or "busy" (the condition failed) |
  | `board_rows(ws) -> list[dict]` | `cards.board_row(ws, d)` per run dir | `index.run_items(store)`, mapped to the same `{"run_id", "card", "summary"}` shape |
  | `evaluating_runs(ws) -> set[str]` | run dirs where `data.locks.is_locked(d)` | LOCK `eval#` items not expired |
  | `cards_fresh: bool` | True | False |
  | `upload_ticket(ws, kind, filename, size, limit) -> dict` | today's local branch of `uploads.ticket` | today's cloud branch |
  | `upload_local_target(ws, upload_id) -> (Path, int)` | today's `local_target` | raises `ServiceError(404)` |
  | `upload_take(ws, upload_id, kind) -> (filename, Path)` | today's local branch of `uploads.take` (kind checked) | today's cloud branch (kind checked) |

- **Consequences:**
  - Services call `state.of(ws).fresh_*()` before reading files, and write project files inside `state.of(ws).project_write(...)`.
  - `imports.py` calls `state.of(ws).record_run(ws, name)` instead of importing `workers`.
  - `workers.index_run` becomes a call to `CloudState.record_run`. Callers are `workers.run_job`, the batch item, `imports.py` and `scripts/cloud_ops.py seed`. Worker workspaces in `lambda_worker.py`, `tests/test_cloud_api.py`, `test_cloud_jobs.py` and `test_cloud_batches.py` set `ws.state`.
  - The worker workspace in `lambda_worker.py` gets `ws.state = CloudState(store, mirror)`, as `create_app` does.
- **Sources:** code only (`services/cloud_state.py`, `services/uploads.py`, `data/mirror.py`). No external behaviour is assumed.

### ADR-2: One board assembly

- **Context:** the laptop and the cloud build cards, KPIs, sorting and overlays differently (`runs.py:136-337`).
- **Decision:**

  **`services/cards.py` holds the card builders:**
  - It contains `card_for(ws, d)`, without the "evaluating" check, which moves to the assembler. It also contains `when`, `short`, `card_extras`, `failure_reason`, `INTERRUPTED_REASON`, `entry_for`, `summary_for`, and `board_row(ws, d) -> {"run_id", "card", "summary"}`. It imports only `data`, `stats` and the kernel. `board_column` stays in `runs.py`.
  - `runs.py` re-exports the names other code and tests use (`card_extras`, `failure_reason`, `INTERRUPTED_REASON`, `card_for`).
  - `card_for` keeps its signature. Callers that need the evaluating headline (none after this change) get it from the assembler.

  **`runs.board(ws)` assembles the board the same way on both setups.** It reads:
  - `rows = state.of(ws).board_rows(ws)`
  - `evaluating = state.of(ws).evaluating_runs(ws)`
  - `active`: each task's `data_project.active_rubric_sha`, after `state.of(ws).fresh_projects()`. This is the same definition the laptop uses today (`rubrics.rubric_options`, `rubrics.py:235`), so the KPIs don't change.
  - `run_jobs`: `{j.run_id: j for j in jobs.of_kind(ws, "run")}`, the latest per run

  Then for each row:
  - **Unreadable:** a missing card or summary, or `card.unreadable`, gives today's unreadable stub.
  - **Evaluating:** a run in `evaluating` gets headline "evaluating" and an empty `sub`, as the laptop's `card_for` does today.
  - **Interrupted:** this applies when all of these hold:
    - the card's status is "running"
    - `run_jobs` has a job for this run
    - that job isn't alive
    - either `state.of(ws).cards_fresh` is true (the laptop: the card was just read from `run.json`) or the job's status is "interrupted" (the cloud: stored cards can lag behind a normal finish)

    The headline and status then become "interrupted", with `reason = INTERRUPTED_REASON`. A run with no job is never overlaid. The laptop `Job` gets a `status` property: "running" when alive, else "done".
  - **KPIs:** `spent` sums `summary.spent`; `mean` and `scored` cover summaries whose `rubric_sha256` is in `active`; `shares[run_id] = {spent, overall-if-active}`.
  - **Sorting:** by `card.get("started_at") or ""`, newest first.
  - **Output:** each card is passed through `CARD_FIELDS` (a tuple in `cards.py`). Only listed keys present on the card are copied, and missing keys are omitted (the routes use `response_model_exclude_unset=True`, so absent stays absent).

  **The rest of `board()`'s output** (columns, `live`, `today`) is unchanged.

  **`detail()`** uses the same interrupted rule through `runs.interrupted(ws, run_id, status) -> bool`.
- **Rejected:** a stored index on the laptop (spec open question). Computing on read keeps the laptop always fresh. The shared builder and assembler remove the drift, which was the problem.

### ADR-3: Re-indexing as an operator command

- **The worker event:** `{"kind": "reindex", "after": "<run id>" | null, "budget_s": 600}`. The handler takes a `clock` argument, so tests page with a fake clock.
  - It lists `run#` TREE items in sort-key order, after the `after` cursor if one is given.
  - For each run:
    - Right before touching a run, it **skips** it when any JOB item still queued or running names it as `run_id`. That's a direct `store.query("JOB")`, so batch items (records with `parent`) count too. It also skips a run with a live `eval#` lock.
    - It pulls the run, then calls `CloudState.refresh_row`. That compares first, so an unchanged run isn't written. It never bumps the tree version, so a worker that pulled the run earlier can still push.
    - It **counts** a failed condition as "busy" and moves on: the run changed after the pull.
  - It **stops** when `budget_s` has elapsed and returns `{"runs": n, "updated": u, "skipped": s, "busy": b, "next": "<last run id>" | null}`.
  - Running it twice in a row reports `updated: 0` the second time. That's the idempotency test.
- **The operator command:** `scripts/cloud.sh reindex` runs `scripts/cloud_ops.py reindex <worker function name>`. That uses boto3 Lambda `invoke` on qualifier `live` with `botocore.config.Config(read_timeout=900, retries={"total_max_attempts": 1})`, so a slow page isn't re-invoked by a client retry. It loops while `next` is set and prints the totals.
- **Sources:** botocore 1.43.122 (installed; `Config.__doc__`): "read_timeout … The default is 60 seconds" and "`total_max_attempts` … a value of 1 indicates that no requests will be retried". The Lambda worker timeout is 900 s (`cloud_jobs.RUN_LIMIT_S`, set by the stack), and the budget of 600 s leaves margin.
- **Time:** unmeasured. Launch records the real duration for 149 runs in the runbook. The pagination bounds each invoke regardless of run count.

### ADR-4: One job-kind registry

- **Decision:** `services/job_kinds.py` holds the registry:

  ```python
  @dataclass(frozen=True)
  class Kind:
      name: str
      singleton: bool             # one live at a time (slot)
      ordinary_exit_1: bool       # exit 1 is an ordinary failure shown on its own page, not a banner (run, batch)
      long_running: bool          # no job deadline; its workflow releases it (batch)
      busy: str | None            # the 409 message, "{}" = the holder's run id
      syncs_run: bool             # the worker pushes the run while it runs and at the end (run)
      reads_run: bool             # the worker pulls the run first and records it after (evaluate)
      output_file: str | None     # a file the worker publishes (tools: "ui-tools.json")
      cost: str                   # "run" | "evaluate" | "zero": how the worker reads the actual cost
      startable: bool = True      # False: a reservation only (draft)
  KINDS: dict[str, Kind]          # run, evaluate, batch, tools, draft
  def kind(name: str) -> Kind     # unknown → ValueError("unknown job kind: …")
  def failed_visibly(kind_name, exit_code, status, alive, dismissed) -> bool  # an unknown kind is treated as non-ordinary
  ```

- **Readers of the registry:**
  - `jobs.Job.failed_visibly` and `cloud_jobs.JobRecord.failed_visibly`, which delegate to `failed_visibly` (an unknown legacy kind never raises)
  - `BUSY` (removed, replaced by `kind(k).busy`)
  - `CloudBackend._start`: the deadline when `long_running`, and `start_batch` vs `invoke`
  - `workers.run_job`: tick when `syncs_run`, pull and record when `reads_run`, publish when `output_file`
  - `workers.actual_cost`, by `cost`
  - `jobs.start` and `jobs.reserve`, which call `kind(name)` and so reject unknown kinds. `start` also rejects a kind that isn't `startable`.
- **Worker events are a separate namespace.** `healthz`, `batch_prepare`, `batch_item`, `batch_finalize` and `reindex` are listed in `lambda_worker.EVENTS`, a dict from event kind to handler that replaces the `if` chain, and aren't job kinds.
- **Tests:**
  - An AST scan of `src/foundry/services` and `api` finds every string literal passed as the kind to `jobs.start`, `jobs.reserve`, `jobs.of_kind`, `jobs.live`, `jobs.of_run` and `jobs.alive_of`, and asserts each is in `KINDS`.
  - Each kind's `singleton` matches what its `jobs.start` call passes.
  - `lambda_worker.EVENTS` keys equal the event kinds `infra/foundry_cloud/batch_sm.py` and `cloud_ops` send.

### ADR-5: One batch orchestrator in the engine

- **Decision:** `engine/batch.py` exposes the batch as functions that both drivers call:

  | Function | Does | Used by |
  |---|---|---|
  | `add_args(parser)` / `spec_from_args(argv) -> BatchSpec` | the one parser for `foundry batch` options; `cli.py` calls `add_args`. `spec_from_args` drops a leading `"batch"` token | the CLI; `workers.batch_prepare` (replaces `_flag`) |
  | `prepare(spec, out, runs_dir) -> (jobs, meta)` | `plan`, `load_eval_config` when scoring (as `main()` does), the derived configs, `batch.json` (status running, with `runs_dir`), the summary with every row "pending" | `Batch.run`, `batch_prepare` |
  | `run_item(job, ctx) -> str` | see below | `Batch._work`, `batch_item` |
  | `summary_row(job) -> Row` | as today; a `Row` is a dict with typed values: ints for `repeat`, `model_calls`, `tool_calls`, `tool_errors`, `tokens_in`, `tokens_out`; floats for `cost_usd`, `workflow`, `output`, `overall`, `duration_s`; `""` when unknown | both |
  | `rows_from_csv(path) -> list[Row]` | reads `summary.csv` back into typed rows (an empty or unparseable cell becomes `""`) | `batch_item`, `batch_finalize`, and `data.batches` callers that rewrite rows |
  | `write_rows(out, rows)` | writes `summary.csv` and `summary.md` from typed rows (today's `write_summary` body, applied to rows) | both, after every item |
  | `finalize(out, meta, rows, spent, extra_bad=False) -> int` | `exit_code` (1 if a row is "error" or "skipped (budget)", or `extra_bad`); batch.json finished with `spent_usd` and `finished_at` | `Batch.run`, `batch_finalize` |

- **What `run_item(job, ctx)` does:**
  - **Resume (laptop only, `ctx.resume`):** `_resolve` and `_next_id` pick the attempt, as today. When `ctx.resume` is false (the cloud), the attempt is always `job.run_id`, with no glob and no `-retryN`.
  - **Budget:** under `ctx.lock`, `ctx.spent() >= max_cost` gives "skipped (budget)".
  - **Run:** `code = ctx.exec([...run args...], log_name)`. Exit code 2, or no `run.json`, gives "error".
  - **Score:** when `ctx.evaluate` and the run isn't already scored (`_evaluated`): `ctx.exec(["evaluate", …, "--config", ctx.eval_config])`.
  - It sets `job.run_dir`, `job.status` = "ran", and returns the status.
- **`ItemContext`** (a dataclass) has these fields:
  - `runs`, `evaluate`, `eval_config`, `max_cost`, `resume`
  - `exec(args, log_name) -> int`
  - `spent() -> float`
  - `lock`: a context manager
- **The two drivers:**

  | | Laptop (`Batch`) | Cloud (`batch_item`) |
  |---|---|---|
  | `exec` | writes `logs/<name>.log` | `jobs.run_foundry`; on a run, its tick calls `CloudState.record_run`, and its output goes to CloudWatch. After `run_item` returns, the item calls `record_run` once more, so the scores reach the store (today's `index_run` after the evaluate) |
  | `spent()` | sums its run dirs' costs | reads the batch JOB item's booked `spent` |
  | `lock` | an `RLock` | a no-op |
  | `resume` | True | False |

- **Cloud-only, staying in `workers`:**
  - claiming
  - the per-item JOB record (idempotency: a redelivered item returns before `run_item`)
  - booking the cost
  - the lease around the summary rewrite: `rows_from_csv`, then replace row n, then `write_rows`, then push
  - dead-item charging
  - finishing the job record
  - `batch_finalize`, which calls `finalize(out, meta, rows_from_csv(...), booked_spent, extra_bad=dead>0 or error)`
- **Event shapes are unchanged.** `batch_prepare` returns `{"batch_job_id", "concurrency", "items"}`, and `items[*]` keeps its keys. `eval_config` now carries `spec.eval_config` or the first variant's config. So no infrastructure change is needed.
- **Behaviour changes, intended (spec AC9):**
  - The cloud honours `--eval-config`.
  - The cloud skips scoring an already-scored run.
  - The cloud writes `summary.md`.
  - The cloud's batch.json gains `runs_dir`.

### ADR-6: The same tests on both setups

- **Decision:** `tests/backends.py` provides a pytest fixture `backend`, parametrized over "laptop" and "cloud", with a project root argument (default: a copy of `tasks/` and `config/`). It yields a `Harness`:

  | Member | Laptop | Cloud |
  |---|---|---|
  | `client`, `csrf` | a `TestClient` on `create_app(runs, root)` | a `TestClient` on `create_app(…, cloud=(FakeStore, Mirror(store, api_root), CloudBackend(invoke=q.append, start_batch=lambda j: q.append(j) or f"arn:test:{j}")))`. Before every request, a client hook empties only what the mirror owns (`mirror.seen.clear()`, the contents of `runs/` including `.batches`, `project/tasks/*` and `files/`), keeping `config/` and `fixtures/`. So every request starts as a cold instance and must fetch what it reads |
  | `runs_dir` | the app's runs dir | a staging dir, the runs dir of a seeding `Mirror` |
  | `sync()` | no-op | for each staged run dir: `CloudState.record_run` (push plus index) |
  | `seed(fixture_name, run_id=None)` | copy, then `sync()` | the same |
  | `drain(timeout=120)` | wait until no job is alive (poll every 0.1 s, fail at `timeout`). This polls a real process, so it uses a real clock, bounded by `timeout` | run queued work with the real worker code until the queue is empty: `workers.run_job` for a job id; for a batch id, `batch_prepare`, then `batch_item` for each item, then `batch_finalize`. The worker has its own workspace: resolved paths, its own mirror root with `config/` and `fixtures/` links, `ws.state = CloudState`, and `FOUNDRY_HOME` set to its project root while it runs, as on Lambda, so in-process `plan` reads the mirrored prompts. Real `foundry` subprocesses, with the fault-server configs; fails at `timeout` |
  | `pull(run_id)` | no-op | pull `run#<id>` into the staging dir, so a test can read files the API wrote |
  | `name` | "laptop" | "cloud" |

- **Converting tests:**
  - `test_api.py`, `test_api_contract.py` and the HTTP tests of `test_api_ports.py` move to the fixture.
  - Tests that write run files call `h.sync()` before the HTTP call.
  - Tests that read files the API wrote call `h.pull(run_id)` first.
  - Tests that inspect `app.state.ui.jobs` use `h.drain()` and the API instead.
- **Markers:**
  - `@pytest.mark.laptop_only("reason")` and `@pytest.mark.cloud_only("reason")`. A conftest hook turns each into a skip on the other parameter.
  - `test_single_setup_tests_are_few` counts the marked tests across the converted files and fails above 10.
  - The expected laptop-only tests are:
    - the receiver routes
    - the local upload route
    - binding to 127.0.0.1
    - the symlink must-deny tests (`test_must_deny_paths_continued`, `test_must_deny_paths_parity`, `test_rubric_contents_viewable`'s symlink part): the cloud's mirror can't hold symlinks, by construction
    - tests that read in-memory laptop job internals and have no API equivalent
  - If that list goes over 10, the limit is revisited with the reviewer, not silently raised.
- **Parity tests:**
  - **The board:** seed clean, recovered and failed runs, then compare `GET /api/runs` between parameters, after removing `when` and `started_at`-derived values (`today`).
  - **A batch:** one offline batch (fault configs, two prompts, `--evaluate`) through `POST /api/batches` and `h.drain()`.
  - Compare `batch.json` minus `id`, `started_at`, `finished_at`, `spent_usd`, `runs_dir` and `configs` (paths).
  - Compare `summary.csv` rows minus `duration_s`, `run_dir` and `cost_usd`.
  - Compare `exit_code`, and the run ids with the batch id prefix removed.
  - Compare the API's cards for the batch's runs (`/api/runs`, scored on both setups), so the cloud's push after scoring is covered.
- **Riskiest assumption:** the real worker plus real subprocesses on a fake store, inside pytest. Slice 1 proves it with one run and one scoring on the cloud parameter. If it fails, `drain()` uses `test_cloud_jobs.fake_runner` for runs on the cloud parameter, and the handoff states that difference.

## Resolutions after gate pass 3 (approved by the user, 2026-11-01)

1. **Project files in the harness (blocker).**
   - **Layout:** the cloud API's root is `<api mirror root>/project`, with `config/` and `fixtures/` links, as in `cloud_boot.project_root`.
   - **`Harness.project`:** the staging project root, a copy of `tasks/` and `config/`. On the laptop it *is* the app's root.
   - **Seeding:** on the cloud, `cloud_boot.seed_projects(store, staging mirror, app=staging root)` seeds the `project#` trees at fixture start.
   - **`h.sync_project()`:**
     - Laptop: a no-op.
     - Cloud: `push_dir("project#<task>")` from the staging root for each task, after first pulling, so its `seen` version is current.
   - **`h.pull_project()`:**
     - Laptop: a no-op.
     - Cloud: pull each `project#` tree into the staging root.
   - Tests that write or inspect `tasks/` call these.
2. **Config paths in a cloud batch (blocker).** `workers.batch_prepare` resolves `spec.configs` and `spec.eval_config` against `ws.root` before `prepare`, as today's `str(ws.root / config)` does. `test_cloud_batches.py` gets a test that runs `batch_prepare` with `--models` from a cwd other than the project root (`monkeypatch.chdir(tmp_path)`).
3. **Answers to the reviewer's questions:**
   - **What counts toward the limit:** single-setup tests count per test function, not per parametrized case. The receiver, bind and symlink tests are split, so only their laptop-only parts carry the marker. That keeps the non-symlink must-deny assertions on both setups.
   - **`board_row`'s card:** `card_for ∪ card_extras`, exactly what the index stores today.
   - **`runs.board_kpis`:** removed. The prompt pages' per-prompt shares come from the same `board_rows` summaries, through a small `runs.kpi_shares(rows, active)` that the board also uses. One KPI definition, and no per-run pull on the cloud for the prompt pages.
   - **The worker workspace in `drain()`:** it has `backend=CloudBackend(store)` and `ws.cloud`, as in `test_cloud_api.py:44-46`.
4. **Minor findings, folded in:**
   - `board()` reads the run jobs before the rows.
   - Import order: `uploads` sits above `imports` and `rubrics`, and `state` imports `cards`.
   - The `EVENTS` test allows `healthz`, which `cloud.sh` sends.
   - Re-indexing calls `CloudBackend.sweep()` first, and reads the JOB items once per page, not once per run.
   - `sync()` pulls before re-pushing.
   - `summary_row` coerces None to `""` and scores to float.
   - `Batch.max_active` is counted in the laptop `exec` wrapper.

## Failure modes

- **A forgotten fetch:** the cold-instance hook makes cloud-parameter tests see a 404 or empty data, and CI fails.
- **A stored card older or newer than the code:** `CARD_FIELDS` filtering means no 500. A missing field stays absent until the next `record_run` or a `reindex`.
- **Re-indexing while a run is live:** the run is skipped, batch items included. A write between its pull and its update fails the update's condition, which is counted as busy and never escapes. Re-indexing never bumps a tree's version, so it can't break a worker's later push. A timeout on the client side means re-running continues from the start; it's safe, because unchanged runs aren't written.
- **The laptop interrupted rule:** only runs with a finished UI job and a `run.json` still saying running are affected, which is exactly a crashed run. Runs without a job aren't touched.
- **An unknown legacy job kind in the table:** `failed_visibly` tolerates it, so `/api/jobs` doesn't fail.
- **A deploy or rollback with a batch in flight:** `cloud.sh deploy` refuses while jobs are in flight (O4), so a batch never spans two builds unless `--force` is used. Event shapes are compatible both ways, so even then items run.

## Security and privacy

Nothing changes: no new routes or permissions, and the upload checks move as they are, including the kind check. The `reindex` event is reachable only by an AWS identity allowed to invoke the worker, as with `batch_finalize` today.

## Performance

- **The cloud board:** it reads only the index (`board_rows` is the same query), plus one LOCK query and the job list, as today.
- **The laptop board:** it computes on read, as today.
- **Tests:** about 54 tests run twice, and the cloud parameter adds real subprocesses and cold pulls per request. The budget is the suite under 5 minutes, measured in Verify.

## Migration and rollback

- **No data migration:** the index shape (card and summary JSON) is unchanged.
- **Cards written by the new code:** they never carry the "evaluating" headline. The old code handled that anyway, because it re-indexes after the lock is released.
- **Deploy order:** deploy, then optionally `cloud.sh reindex`.
- **Rollback:** check out the previous commit and deploy. Old code reads new index items and new batch trees; the extra `summary.md` and `runs_dir` are ignored.
- **No one-way doors:** nothing here is irreversible.

## Traceability

| AC | Component |
|---|---|
| AC1 | ADR-6 (`tests/backends.py`, the converted suites, markers, count test) |
| AC2 | the backend-modules architecture rule (three checks, each with a self-test) |
| AC3 | ADR-1, and ADR-6's cold-instance hook |
| AC4 | ADR-2 (`cards.py`, `board`, `CARD_FIELDS`), the board parity test |
| AC5 | ADR-3, with tests for idempotency, skip-live and busy |
| AC6 | ADR-5, the batch parity test, the existing batch tests |
| AC7 | ADR-4, the registry tests |
| AC8 | `docs/architecture.md` "Writing a feature once"; `docs/deploy.md`: the difference table and the `reindex` runbook (duration, safe to re-run, what to do on timeout) |
| AC9 | ADR-2 (laptop interrupted), ADR-5 (cloud batch) |
| AC10 | Launch |

## Risks and spikes

- **Riskiest:** ADR-6's real worker in tests. **Spike, 2026-11-01:** done.
  - **Setup:** a cloud app on a `FakeStore`, a separate worker workspace, `workers.run_job` with the real `jobs.run_foundry`, and the fault-server config.
  - **Result:**
    - Starting a run through the API, then the worker: done in 1.2 s.
    - Scoring through the API, then the worker: done in 1.3 s.
    - The board card reached "scored" at 0.76.
  - **Two findings:**
    - **Resolved paths:** the worker workspace's paths must be resolved, because `safe_run_path` compares resolved paths and macOS's `/var` is a symlink. pytest's `tmp_path` is already resolved; the harness resolves anyway.
    - **Stale project files in production:** the cloud API only pulls project files at cold start (`cloud_boot.py:72`). So a warm instance's `start_run` checks prompts against a copy that can be stale; a prompt created on another instance is "unknown" there. The cold-instance hook exposes this, and slice 2 fixes it, because `start_run` and every project-file read call `fresh_project`. This is one of the defects the work exists to catch.
- **Second:** batch refactor regressions. The parity test, `test_batch.py` and `test_cloud_batches.py` pin them.

## Implementation plan

1. **The harness.** `tests/backends.py`, the markers, the count test, and the cold-instance hook. Convert `test_api.py`. Prove a run and its scoring on the cloud parameter. A test that fails on the cloud because of a missing seam gets `xfail(reason="seam: …", strict=True)`, removed by the slice that fixes it.
2. **State (ADR-1).** `state.py`, `CloudState`, and `cards.py` (moved functions, re-exported). Move the upload branches into the states, and `index_run` into `record_run`. Replace every `cloud_state.*` use in services and api. Add the architecture rule and its self-tests. Convert `test_api_contract.py` and the HTTP tests of `test_api_ports.py`.
3. **Board (ADR-2).** `board`, `CARD_FIELDS`, `interrupted`, `Job.status`, the board parity test, and the laptop interrupted test.
4. **Re-indexing (ADR-3).** The worker event with paging, `cloud_ops reindex`, `cloud.sh reindex`, and tests: idempotent, skip-live, busy, paging.
5. **Job kinds (ADR-4).** `job_kinds.py`, `lambda_worker.EVENTS`, and the readers; the registry tests.
6. **Batches (ADR-5).** The engine functions, typed rows, `rows_from_csv`; `Batch` and `workers.batch_*` call them; the batch parity test. `test_batch.py` and `test_cloud_batches.py` stay green.
7. **Docs (AC8)** and the CHANGELOG.
8. **Verify and Launch.** The full suite, ruff, tsc; a deploy; the live checks; `cloud.sh reindex` on real data, with its duration recorded.
