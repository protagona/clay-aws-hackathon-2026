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
| **data** | `data/` | Every read and write of project and run files: run dirs, `run.json`, scores, traces, batches, prompts, rubrics, tasks, locks, the tool cache, the S3 mirror. Untrusted files are read safely here (regular files only, no symlinks, malformed files return `None`). | kernel, data |
| **engine** | `engine/` | The agent harness, models, MCP servers, tracing, evaluation (scoring), the batch runner, the trace importer and receiver, the external runner, rubric drafting | kernel, data, engine |
| **services** | `services/` | What the app does: runs, scoring, batches, prompts, rubrics, the tool menu, imports, the dashboard, background jobs. Errors are raised as `ServiceError(status, message, field)`. | kernel, data, engine, services |
| **api** | `api/` | HTTP only: routes, request models, the write guard (Host, Origin, CSRF), error mapping, and rendering run text to safe HTML | kernel, services, api |
| **ui** | `ui/` | Serves the built web app (`ui/web_dist`) and nothing else | nothing |
| **entry points** | `server.py`, `cli.py`, `cli_rubric.py`, `__main__.py` | Wire the layers together: `server.py` builds the web server from api and ui; the CLI calls the engine and services | any layer |

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
  - `errors`, `workspace`
  - `configs`, `jobs`, `stats`, `run_view`
  - `rubrics`, `batches`, `imports`
  - `runs`
  - `prompts`, `dashboard`
  - `tools`

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

## How the rules are checked

`tests/test_architecture.py` parses every module under `src/foundry` with `ast`, without importing any of them:

- `test_layers` checks each import against the table and reports the file, the line and the rule broken.
- `test_the_checker_catches_a_bad_edge` builds a scratch package with deliberately bad imports (data importing the engine, a stray module, `fastapi` in data, `jinja2`, another layer's private name), and proves the checker reports each, and not a good import beside them.
- `test_services_and_api_dont_touch_files` and `test_the_file_check_catches_a_read` do the same for the file and process rule.
- `test_services_have_no_import_cycles` walks the services import graph.

To run it on its own: `uv run pytest tests/test_architecture.py`.
