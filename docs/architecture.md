# Architecture: the layers of `src/foundry`

The code is split into layers. Each layer may import only the layers below it, and `tests/test_architecture.py` fails the build when an import breaks that rule.

```
  web/ (React)  ──HTTP──▶  api ──▶ services ──▶ engine ──▶ data ──▶ kernel
                            │                     │          │
  ui (serves web_dist) ◀────┴── server.py (entry) ┘          │
                                cli.py      (entry) ─────────┘
```

## The layers

| Layer | Where | What lives there | May import from foundry |
|---|---|---|---|
| **kernel** | `config.py`, `contract.py` | Settings and the run-record schemas (`Step`, `run.json`, `scores.json`) | each other only |
| **data** | `data/` | Every read and write of project and run files: run dirs, `run.json`, scores, traces, batches, prompts, rubrics, tasks, locks, the tool cache. In the cloud, also the state store (`store.py`: S3 and DynamoDB, with a test fake of the same semantics), the tree mirror between the store and `/tmp` (`mirror.py`), tokens, the spend ledger and uploads. Untrusted files are read safely here (regular files only, no symlinks, malformed files return `None`). | kernel, data |
| **engine** | `engine/` | The agent harness, models, MCP servers, tracing, evaluation (scoring), the batch runner, the trace importer and receiver, the external runner, rubric drafting | kernel, data, engine |
| **services** | `services/` | What the app does: runs, scoring, batches, prompts, rubrics, the tool menu, imports, the dashboard, background jobs. Errors are raised as `ServiceError(status, message, field)`. | kernel, data, engine, services |
| **api** | `api/` | HTTP only: routes, request models, the write guard (Host, Origin, CSRF), error mapping, and rendering run text to safe HTML | kernel, services, api |
| **ui** | `ui/` | Serves the built web app (`ui/web_dist`) and nothing else | nothing |
| **entry points** | `server.py`, `cli.py`, `cli_rubric.py`, `__main__.py`; for the cloud `lambda_api.py`, `lambda_worker.py`, `lambda_probe.py`, `cloud_boot.py`; `cli_remote.py` | Wire the layers together: `server.py` builds the web server from api and ui; the CLI calls the engine and services. In AWS, `lambda_api` turns a function-URL request into an ASGI request to the same app, `lambda_worker` runs one job, `lambda_probe` checks `/healthz`, and `cloud_boot` sets up `/tmp` at a cold start. `cli_remote` is the standard-library API client served as `foundry-remote.pyz`. | any layer |

### Rules the test enforces

- **Imports point down.** An import outside the table above fails, including imports inside functions.
- **Web frameworks stay at the edge.** `fastapi` and `starlette` are imported only in `api/`, `ui/` and the entry points.
- **No server-rendered templates.** `jinja2` and `markupsafe` are imported nowhere.
- **No importing private names across layers.** `from foundry.data.runs import _atomic_write` from a service fails; make it public in its own layer first. (The check reads imports; it doesn't see attribute access such as `runs._atomic_write`, so don't do that either.)
- **Every module is in a layer.** A new top-level module that isn't in the table fails until it is placed.
- **Services and api don't touch files or processes.** They call `foundry.data` for files and `foundry.services.jobs` for subprocesses. Calls such as `open()`, `.read_text()`, `.glob()` and `.mkdir()`, any `os.*()` call (under any alias), `from os import` anything but `environ`, and importing `shutil`, `subprocess` or `tempfile` fail there. Two modules are exempt, each for one job:
  - `services/jobs.py` starts `foundry` subprocesses with their stderr in a log file.
  - `api/uploads.py` streams an upload to disk under its size cap.
- **Services have no import cycles.** Today's order, from leaf to top:
  - `errors`, `workspace`, `job_kinds`, `stats`, `run_view`
  - `cards`, `jobs`
  - `state`, `configs`
  - `cloud_jobs`, `cloud_state`
  - `rubrics`, `batches`, `imports`, `uploads`
  - `runs`
  - `workers`
  - `prompts`, `dashboard`
  - `tools`
- **Only the backend modules know the cloud.** These are `services/cloud_state.py`, `cloud_jobs.py` and `workers.py`.
  - In the rest of `services/` and in `api/`, importing `data.store`, `data.mirror`, `data.index` or a backend module fails, and so does reading an attribute named `cloud`. Function-local imports count.
  - The entry points (`server.py`, `lambda_*.py`, `cloud_boot.py`) wire the backends in, so they're exempt.

## Where new code goes

| You are adding… | Put it in |
|---|---|
| A new screen | A page in `web/src`, a route in `api/routes.py`, and a function in a `services/` module. The route only parses the request and shapes the response. |
| A new API field | The service that computes it, then its model in `api/schemas.py`, then `uv run python scripts/gen_api_types.py` (see [The API contract](#the-api-contract)) |
| A new file format, or a new file the app reads or writes | `data/`. Read untrusted files through `data.runs.is_regular` and `read_json`. |
| A new scorer, model provider, tracer or MCP server | `engine/` |
| A new background job | A service that calls `jobs.spawn(ws, kind, ...)` with a `foundry` subcommand |
| A new CLI command | `cli.py`, calling the engine or a service |
| A new security header or write check | `api/guard.py` |

When a service needs a file operation that `data/` doesn't offer yet, add a small function to `data/` rather than reading the file in the service.

## Writing a feature once (laptop and AWS)

The same app runs on a laptop and in AWS (`server.create_app`; backend-parity, `docs/eval-sandbox/backend-parity/`). The differences live behind four seams, so a feature is written once:

| You are… | Do this | Not this |
|---|---|---|
| **Reading** runs, prompts, rubrics, batches or the tool list | Call `state.of(ws).fresh_run(ws, id)` / `fresh_runs` / `fresh_project(ws, task)` / `fresh_projects` / `fresh_batches` / `fresh_tools` first, then read the files through `foundry.data`. `configs.task_ids`, `prompt_ids` and `task_prompts` already do. | Reading `ws.runs_dir` or `tasks/` cold: in AWS an instance's files are a copy that may be empty or stale |
| **Writing** a task's project files (prompts, rubrics) | Wrap it in `with state.of(ws).project_write(ws, task):` (and let routes pass `base_version` through `state.expecting`) | Writing files directly: in AWS they'd never leave the instance |
| **Writing** a run in the request (an import) | Call `state.of(ws).record_run(ws, run_id)` after | Calling the worker or the mirror |
| **Adding** a field to a board card | Compute it in `services/cards.py` (`card_for` or `card_extras`), add it to `cards.CARD_FIELDS` and to `Card` in `api/schemas.py`. After deploying, `scripts/cloud.sh reindex` updates the cards already stored | Overlaying it in `runs.board` from the run's files: the cloud board reads stored cards only |
| **Adding** a background job | A `Kind` in `services/job_kinds.py`, a `foundry` subcommand, and `jobs.start(ws, "<kind>", ...)` in a service | `if kind == ...` in a backend |
| **Changing** how a batch runs | `engine/batch.py`: `prepare`, `run_item`, `finalize`, `write_rows`. `foundry batch` and the cloud's Step Functions steps both call them | Changing `Batch` or `workers.batch_*` alone |

**Tests:** take the `backend` fixture (`tests/conftest.py`, `tests/backends.py`), and the test runs on both setups.
- **Cold copy:** every cloud request starts from an empty copy of the data, so a missed `fresh_*` call fails there.
- **Staged files:** stage run files in `h.runs_dir` and project files in `h.project`, then call `h.sync()` / `h.sync_project()`.
- **Jobs:** `h.drain()` runs queued jobs: a laptop's subprocesses, or the cloud worker's code.
- **Setup-specific tests:** a test that can only hold on one setup carries `@pytest.mark.laptop_only("why")` or `cloud_only`. `test_single_setup_tests_are_few` keeps there to at most 10.

## The API contract

Every `/api` route declares its request body and response in `src/foundry/api/schemas.py` (increment 17). FastAPI checks each response against its model before sending it. The web app's types in `web/src/types.ts` are generated from the same models.

- **Closed models** cover data the services build themselves. An unknown key or a wrong type is a bug: the route returns 500 `{"error": "Internal Server Error"}`, and the `foundry.api` logger names the route and the field (never the value).
- **Open models** cover file formats that admit keys we don't know: run files, rubric files, batch files and draft metadata. Unknown keys pass through. Only the fields the web app reads are typed, and `foundry.data` guarantees those types before data reaches a response, so a tampered run, score, batch or rubric-history file can never 500 a page. One accepted exception: a rubric draft's `meta.json` is trusted as the rubric store writes it; a hand-broken one 500s the Rubric page until the draft is discarded. A run whose typed fields are the wrong type is shown as unreadable.
- **Absent stays absent:** routes use `response_model_exclude_unset`, so a key a service leaves out never turns into `null`.
- **Request bodies forbid unknown fields** (422). Ranges live in the model; the batch limits come from `services/batches.py`, so the service and the API share one number.

**Adding or changing a field:**
1. Add it to the service's result.
2. Add it to the model in `api/schemas.py`. Use `= None` if the service sometimes leaves it out. For an Open model fed from a file, also make the reader in `foundry.data` guarantee its type.
3. Run `uv run python scripts/gen_api_types.py`. This rewrites `web/src/types.ts`; commit it.
4. Use the generated type at the call site: `usePoll<RunDetail>("/api/runs/…")`.

**The tests that hold it** (`tests/test_api_contract.py`):
- every JSON route declares a response model, with `exclude_unset`
- `types.ts` matches the models
- every `usePoll`, `get`, `post`, `put`, `del` and `upload` call names the type its route returns (inline types and untyped reads fail; a variable URL must be listed in `DYNAMIC`)
- a response that breaks its model returns a logged 500
- hostile run, score and batch files never 500 a route
- unknown body fields and out-of-range batch values return 422

## How the server is built

`foundry ui` calls `foundry.server.create_app(runs_dir, project_root, port)`, which:

1. Creates a `services.workspace.Workspace`: the project root, the runs directory, and the jobs and receiver this server started. Every service takes it as its first argument.
2. Installs `api.guard`: the Host and Origin checks, the security headers, and a fresh CSRF token.
3. Installs `api.errors`, so every `/api` error is JSON `{"error": ..., "field"?: ...}`.
4. Includes the `/api` router from `api.routes`.
5. Mounts `ui.spa` last: `/assets/*` as files, and every other path falls back to `index.html`.

In AWS the same app is built by `lambda_api`, with the state store and token checks switched on; the design is in [aws-deploy/design.md](eval-sandbox/aws-deploy/design.md).

## How the rules are checked

`tests/test_architecture.py` parses every module under `src/foundry` with `ast`, without importing any of them:

- `test_layers` checks each import against the table and reports the file, the line and the rule broken.
- `test_the_checker_catches_a_bad_edge` builds a scratch package with deliberately bad imports (data importing the engine, a stray module, `fastapi` in data, `jinja2`, another layer's private name), and proves the checker reports each, and not a good import beside them.
- `test_services_and_api_dont_touch_files` and `test_the_file_check_catches_a_read` do the same for the file and process rule.
- `test_services_have_no_import_cycles` walks the services import graph.

To run it on its own: `uv run pytest tests/test_architecture.py`.
