# Increment 16: clear layers — Design

**Spec:** ./spec.md   **Sunset inventory:** ./dependent-inventory.md   **Last updated:** 2026-10-31   **Status:** Draft

## Overview

This is a pure restructure. Modules move with `git mv`, so history follows them. Then imports are rewritten, and three files that mix layers are split by responsibility: `ui/app.py`, `ui/actions.py` and `ui/api.py`. The classic UI is deleted after its unique checks are ported to `/api`. A new `tests/test_architecture.py` enforces the layer table on every test run, so the boundaries stay clear. No behaviour changes, which B1–B3 prove: the existing API, CLI and engine tests and both e2e scripts pass with only import edits.

## Target tree

```
src/foundry/
  config.py  contract.py            kernel: settings, run-record schemas (any layer may import)
  cli.py  __main__.py  server.py    entry points: may import any layer
  data/                             files only: safe reads/writes, no HTTP, no model calls
    runs.py         run dirs: ids, RunDir, dump_line, utc_now (was runstore.py) + safe readers
                    is_regular, read_json, read_run, read_scores, run_dirs (from ui/app.py)
    batches.py      batch dirs: list, rows, variant stats (from ui/app.py), BATCH_ID_RE
    tasks.py        prompts/expected/rubric/system parsing (was tasks.py)
    prompts.py      PromptStore (was prompts/store.py)
    rubrics.py      rubric Store (was rubrics/store.py) + check/form_shape/canonical, Source,
                    DraftResult (pure records and validation moved down from rubrics/draft.py
                    and sources.py, so data never imports engine)
    locks.py        evaluation lock (was evaluate/lock.py)
    storage.py      S3 mirror (was storage.py)
    tool_cache.py   read/validate runs/.ui-tools.json (from ui/actions.py)
  engine/                           doing the work: agents, models, scoring, batch, import
    harness/        runner, hooks, mcp, reaper, roles, tools (was harness/)
    models/         (was models/)
    mcp_servers/    stac (was mcp_servers/)
    tracing/        (was tracing/)
    evaluate/       evaluate, judge, report, summarize, view, workflow (lock moved to data)
    batch.py  importer.py  receiver.py  external.py
    rubrics/        draft.py (the model call), sources.py (fetching files/URLs/MCP), prompts/
  services/                         what the app does; raises ServiceError(status, message, field)
    errors.py       ServiceError (was UiError)
    workspace.py    Workspace(runs_dir, root, jobs, lock, receiver...) (was UiState minus csrf)
    jobs.py         Job, spawn(), list/dismiss/log (from ui/app.py)
    configs.py      configs, task ids, prompt ids, model choices, eval config for a run (from ui/app.py)
    runs.py         board cards + KPIs + card extras (from api.py), run detail, start run,
                    start evaluation, compare (from ui/app.py and actions.py)
    run_view.py     live monitor view model (was ui/monitor.py)
    dashboard.py    (was ui/dashboard.py) + rubric options, scored_by
    batches.py  imports.py  rubrics.py  prompts.py  tools.py   (from actions.py; rubrics.py also
                    absorbs rubrics/service.py)
  api/                              HTTP only
    routes.py       every /api route (was make_router in ui/api.py), request models
    guard.py        Host / Origin / CSRF / JSON checks, security headers
    errors.py       ServiceError and validation errors to JSON {error, field}
    render.py       markdown to safe HTML for run text and reports (was ui/render.py)
  ui/
    spa.py          serve web_dist (assets immutable, index.html fallback) (was mount_spa)
    web_dist/
```

`server.py` holds `create_app(runs_dir, root, port, receiver_port)`. It builds the `Workspace`, adds the guard middleware, and includes `api.routes` and `ui.spa`. `foundry ui` (in `cli.py`) calls it.

## Layer rules (L1), enforced by L2

| From | May import |
|---|---|
| `foundry.config`, `foundry.contract` | each other |
| `foundry.data.*` | kernel, data |
| `foundry.engine.*` | kernel, data, engine |
| `foundry.services.*` | kernel, data, engine, services |
| `foundry.api.*` | kernel, services, api |
| `foundry.ui.*` | nothing in foundry |
| `foundry.server`, `foundry.cli`, `foundry.__main__` | anything |

**Extra rules:**
- `fastapi` and `starlette` may be imported only by `api/`, `ui/` and `server.py`.
- `jinja2` may not be imported anywhere.
- No `from foundry.X import _name` across packages: a private name is private.
- `api/` reaches data only through services. For example, the run detail route asks `services.runs.detail()` and never opens a file.

## How `tests/test_architecture.py` works

- It walks `src/foundry/**/*.py`, parses each with `ast`, and collects `Import` and `ImportFrom` nodes, resolving relative imports.
- It classifies the importer and the imported module by package prefix, then checks the table and the extra rules.
- Imports inside functions count too: a late import is still an edge.
- Failures read `foundry/api/routes.py:42 imports foundry.data.runs: api may import kernel, services, api`.
- A second test runs the checker on a tiny synthetic tree with a bad edge, and asserts that it reports it. This proves the checker isn't a no-op.

## Moves and splits (complete mapping)

| From | To |
|---|---|
| `runstore.py` | `data/runs.py` |
| `tasks.py` | `data/tasks.py` |
| `prompts/store.py` | `data/prompts.py` |
| `rubrics/store.py` | `data/rubrics.py` |
| `storage.py` | `data/storage.py` |
| `evaluate/lock.py` | `data/locks.py` |
| `harness/`, `models/`, `mcp_servers/`, `tracing/` | `engine/harness/`, `engine/models/`, `engine/mcp_servers/`, `engine/tracing/` |
| `evaluate/*` (minus lock) | `engine/evaluate/` |
| `batch.py`, `importer.py`, `receiver.py`, `external.py` | `engine/` |
| `rubrics/draft.py`, `rubrics/sources.py`, `rubrics/prompts/` | `engine/rubrics/` |
| `rubrics/service.py` | `services/rubrics.py` (merged) |
| `rubrics/cli.py` | `cli.py` (the `rubric` subcommand calls services) |
| `ui/monitor.py` | `services/run_view.py` |
| `ui/dashboard.py` | `services/dashboard.py` |
| `ui/render.py` | `api/render.py` |
| `ui/actions.py` | split into `services/{runs,batches,imports,rubrics,prompts,tools,configs}.py` |
| `ui/api.py` | split into `api/routes.py`, `api/errors.py` and `ui/spa.py`. `_card_extras` and the `runs()` assembly go to `services/runs.py`. |
| `ui/app.py` | split: `Job` and `_spawn` to `services/jobs.py`; `UiState` to `services/workspace.py`; the readers to `data/runs.py` and `data/batches.py`; configs and choices to `services/configs.py`; compare to `services/runs.py`; rubric options and info to `services/dashboard.py` and `services/runs.py`; the guard to `api/guard.py`; `create_app` to `server.py`; the classic routes, templates and static files deleted |

**Outside `src/`:**
- `config/*.toml`: `args = ["-m", "foundry.engine.mcp_servers.stac"]`. **The `package` label stays `foundry.mcp_servers.stac`** (A3, B3).
- `engine/harness/mcp.py`: the reaper path.
- The tests' imports are rewritten by the same script.
- `scripts/e2e/tools-cache.json`: the package label stays as it is.

## Decisions

### ADR-1: Move with git mv, then rewrite imports with a script
- **Options:**
  - A) move and rewrite imports, with no compatibility shims
  - B) leave re-export shims at the old paths
- **Decision:** A.
- **Why:** Shims would keep the old, unclear paths importable, which is exactly what this increment removes. Nothing outside the repo imports `foundry.*` (spec A2).
- **Reversibility:** easy (a revert).

### ADR-2: The data layer owns safe reads of untrusted run files
- **Decision:** the existing checks move to `data/runs.py` unchanged: regular files only, no symlinks, JSON shape checks, and the run-id regex.
- **Why:** they're a property of the files, not of HTTP. Every service gets them by construction.
- **Reversibility:** easy.

### ADR-3: Rubric validation moves down to data
- **Context:** `rubrics/store.py` imports `check`, `form_shape` and `DraftResult` from `draft.py`, and `Source` from `sources.py`. The store is data; drafting calls a model (engine).
- **Decision:** the pure validation functions and the two records move to `data/rubrics.py`, and engine's `draft.py` imports them from there.
- **Why:** without this, data imports engine.
- **Reversibility:** easy.

### ADR-4: render.py goes to the API
- **Decision:** `api/render.py`.
- **Why:** turning run text into safe HTML is how the API represents it. No service or engine module needs HTML: the engine writes Markdown reports.

### ADR-5: The CSRF token belongs to the API
- **Decision:** `Workspace` holds no CSRF token. `api/guard.py` creates and checks it, and `GET /api/session` returns it, as today.
- **Why:** CSRF is an HTTP concern.

## Failure modes and risk

- **The riskiest part is a silent behaviour change while splitting the three mixed files.**
  - Mitigation: functions move verbatim with their names made public, and only call sites change.
  - B1 runs the unchanged API tests.
  - The e2e scripts cover the real paths.
  - Build goes in slices, with the full suite after each.
- **A missed import path,** in a lazy import or a `-m` string. The full suite plus a `python -c "import <every module>"` sweep catches import errors. `grep -rn "foundry\.\(harness\|models\|evaluate\|tracing\|mcp_servers\|runstore\|tasks\|storage\|prompts\.store\|rubrics\.\|ui\.\(app\|actions\|api\|monitor\|dashboard\|render\)\)"` must return nothing outside the docs.
- **Spawned subprocesses:** `python -m foundry ...` is unchanged, and the in-repo MCP server's new `-m` path is used through the configs. The e2e covers both, through a run with the real `stac` server.

## Traceability

| AC | Component | Proof |
|---|---|---|
| L1, L3 | the tree above | `test_architecture.py` passes; `grep _core` finds nothing |
| L2 | `tests/test_architecture.py` | its self-test (a synthetic bad edge is reported) |
| B1 | `api/`, `services/` | `test_api.py`, `test_prompts.py` and `test_tools.py` pass with only their imports changed |
| B2 | everything | both e2e scripts pass |
| B3 | `cli.py`, `engine/`, `config/*.toml` | the CLI and engine tests pass with only their imports changed; an offline `foundry run` and `foundry evaluate` through the CLI; `foundry tools` lists `stac` |
| S1, S2 | deletions, ported tests | the inventory's port list implemented and passing before deletion; the grep for `classic` |
| D1 | `docs/architecture.md`, README | read by the verify reviewer |

## Implementation plan

1. **Port first:** every "Port" row of the inventory as `/api` or unit tests, passing against today's code.
2. **Kernel and data:** move `runstore`, `tasks`, `prompts/store`, `rubrics/store` (with ADR-3), `storage` and `lock`. Extract the safe readers and batch readers from `ui/app.py` into `data/`. Rewrite the imports. Run the full suite.
3. **Engine:** move the engine packages and modules, update `config/*.toml` and the reaper path, and rewrite the imports. Run the full suite and an offline run through the CLI.
4. **Services:** create `services/` from `actions.py`, the services parts of `app.py`, `api.py`'s card logic, `monitor`, `dashboard` and `rubrics/service`. Rename `UiError` to `ServiceError` and `UiState` to `Workspace`.
5. **API, UI and server:** split `api.py` into `api/routes.py`, `api/errors.py` and `api/guard.py`, plus `ui/spa.py` and `server.py`. Delete the classic UI. Run the full suite.
6. **Enforcement and docs:** `tests/test_architecture.py`, `docs/architecture.md` and README. Then the full suite, both e2e scripts, Vitest, the build, and the handoff.

## Design gate record

**Pass 1: all blockers resolved. This section is authoritative where it differs from the text above.**

1. **`render` and services (ADR-4 corrected).** The claim that no service needs HTML was wrong. `services.runs.detail()` now returns the report as Markdown (`report_md`) and the feed text as plain text. `api/routes.py` renders both with `api/render.py`, as it already does for `text_html`. The response shape is unchanged: `report_html` and `text_html` are filled by the route.
2. **`_variant_stats`** goes to `services/batches.py` and imports `engine.batch.spread` (services may import engine). `data/batches.py` only reads `batch.json` and the rows CSV.
3. **Uploads.**
   - `save_upload` and `read_rubric_upload` become `api/uploads.py` (HTTP: `UploadFile`).
   - The size limits come through services at call time: `services.imports.max_trace_bytes()` returns `engine.importer.MAX_FILE_BYTES`, and `services.rubrics.max_source_bytes()` returns `engine.rubrics.sources.MAX_BYTES`.
   - Both read the module attribute at call time, so `test_api.py`'s monkeypatch of the limit still works. Its target becomes `foundry.engine.importer.MAX_FILE_BYTES`.
4. **`external.py`** imports `cli._print_scores`. That function moves to `engine/evaluate/report.py` as the public `print_scores`, and both `cli.py` and `engine/external.py` import it from there.
5. **Data functions take paths, not the workspace.**
   - `data.runs.run_dirs(runs_dir)`
   - `data.batches.batch_list(batches_dir)`
   - `data.batches.batch_rows(path)`

   Services pass `ws.runs_dir` and `ws.batches_dir`. This changes signatures, not logic, and data never imports services.
6. **Tests and patch targets.**
   - Services call the job launcher through its module: `from foundry.services import jobs` … `jobs.spawn(...)`, never `from …jobs import spawn`. So `monkeypatch.setattr(foundry.services.jobs, "spawn", …)` intercepts every caller. `test_tools.py`'s target becomes `foundry.services.jobs.spawn`.
   - `server.create_app` keeps `app.state.ui` holding the `Workspace`, which tests and routes read.
   - Module-path strings in tests change with the move. These are `-m foundry.engine.mcp_servers.stac` and `-m foundry.engine.harness.reaper` in `test_tools.py`, `test_stac.py` and `test_robustness.py`.
   - B1 and B3 are reworded in the spec to allow import lines, module-path strings, patch targets, and the two listed `/classic` assertions.
7. **The grep gates.** These replace the one in "Failure modes". Each matches only import or launch forms, never the `package` label, which stays `foundry.mcp_servers.stac`:

   ```
   grep -rnE "(from|import) foundry\.(harness|models|evaluate|tracing|mcp_servers|runstore|tasks|storage|batch|importer|receiver|external|prompts|rubrics|ui\.(app|actions|api|monitor|dashboard|render))\b" src tests scripts
   grep -rnE "\-m\", \"foundry\.(mcp_servers|harness)|-m foundry\.(mcp_servers|harness)" src tests scripts config
   ```

   Both must print nothing. The inventory's classic grep has exact exclusions (its gate record).

**Pass 1 questions, decided:**
- **`src/foundry/templates/`** (the judge and summarizer prompts) moves to `engine/evaluate/templates/`. `files("foundry.templates")` becomes `files("foundry.engine.evaluate.templates")` in `judge.py` and `summarize.py`. The `test_judge` and summarizer tests load the templates, so a wrong string fails them.
- **"Data owns every read" is enforced.** `services/` and `api/` make no filesystem calls. `test_architecture.py` fails on any call to `open`, `read_text`, `read_bytes`, `write_text`, `write_bytes`, `iterdir`, `glob`, `rglob`, `mkdir`, `unlink`, `os.replace`, `shutil.*` or `subprocess.*` in those packages, apart from an explicit allowlist, each with a reason:
  - `services/jobs.py`: starting subprocesses and their log files is its job
  - `api/uploads.py`: streaming an upload to a temporary file

  The reads services did directly (`report.md`, `trace.jsonl` in `run_view` and card extras, the tool cache, the rubric draft source) move into `data/runs.py`, `data/tool_cache.py` and `data/rubrics.py` functions.
- **The board and dashboard cluster.**
  - `services/runs.py` holds `entries`, `cards`, `board`, `board_kpis`, `card_extras`, `short` and `when`.
  - `services/rubrics.py` holds `rubric_options`, `rubric_info`, the overview and drafts.
  - `services/dashboard.py` holds `build` and `dashboard_view`, and imports `services.runs.entries` and `services.rubrics.rubric_options`. `runs` imports `rubrics.rubric_options`.
  - So `runs` imports `rubrics`, and `dashboard` imports `runs` and `rubrics`, with no cycle.
- **`foundry/__init__.py`** (`__version__` only) is kernel.

**Pass 1 minors, applied:**
- **`test_architecture.py`:**
  - It resolves `ImportFrom` as `module` plus `.` plus each alias when that's a submodule (`from foundry import storage`, `from foundry.engine import harness`).
  - It handles relative imports at any level, and package `__init__` files.
  - It **fails on any unclassified `foundry.*` target**.
  - String-based loads (`files(...)`, `-m` arguments, `importlib`) are out of its scope. The full suite and an import sweep (`python -c` importing every module) cover them.
- **Build order:**
  - `test_architecture.py` lands in slice 2 with an allowlist of known violations, which every later slice shrinks; it must be empty by slice 6.
  - **Each slice is its own commit, so any slice can be reverted alone.** They're pushed together at the end, after Verify.
- **ADR-3 scope:** the whole validation group moves: `check`, `_check`, `form_shape`, `canonical`, `validation_error`, `as_rubric_json`, `NAME_RE`, `VERSION_RE`, `MAX_WEIGHT`, `MAX_LEVELS`, `Source` and `DraftResult`. `test_rubrics.py`'s imports are split to match.
- **`ui/spa.py`:**
  - It serves files only, and gives an unknown asset a plain 404 of its own.
  - `server.py` calls `api.errors.install(app)` and includes the api router **before** the SPA catch-all, keeping today's route order.
  - The 503 text and the `/classic` redirect are fixed, as in the inventory.
- **Also updated:** `scripts/judge_sanity.py`'s imports, the `harness/mcp.py:72` comment, the `config/*.toml` comments naming `src/foundry/mcp_servers/stac.py`, and `tests/test_api_ports.py`.

**Pass 2: blockers resolved. These replace the corresponding pass 1 items.**
1. **No services cycle.** The pure aggregation goes to a leaf module, `services/stats.py`: `build`, `costs`, `model_key`, `MAX_COLOURED`, `_nice_axis` and the scatter and struggle helpers, everything in today's `ui/dashboard.py` except the view assembly. It imports only `engine.batch.spread` and the kernel.
   - `services/runs.py` (board, cards, compare) imports `stats`.
   - `services/dashboard.py` (`dashboard_view` only) imports `stats`, `runs.entries` and `rubrics.rubric_options`.
   - `services/rubrics.py` imports neither.

   The final graph: `stats` ← `runs` ← `dashboard`, with `rubrics` ← `runs` and `rubrics` ← `dashboard`. There's no cycle, and `test_architecture.py` gets a cycle check over `foundry.services.*` to prove it.
2. **Every filesystem call in services moves to data,** where it's named:
   - `data/project.py`: `config_stems(root)` (was `_configs`'s glob), `task_ids(root)` (was `_task_ids`'s glob), `active_rubric_sha(root, task)` (was `_rubric_options`' `read_bytes`), and `config_path(root, stem)`
   - `data/rubrics.py`: `Store.write_draft_meta(...)` (was `rubrics/service.create_draft`'s `meta.json` write)
   - `services/jobs.py`: reads the job log (was `api.py:354`), on its allowlist with the subprocesses
   - `data/runs.py`: `read_report(path)` and `read_trace_steps(path)` (for `run_view` and card extras)
   - `data/tool_cache.py`: `read(runs_dir)`
   - `data/rubrics.py`: `read_source(path, limit)` (for the draft source text)

   If the rule finds any other call, it's a build failure to fix in that slice, never to allowlist.
3. **The grep gate and the test name** are resolved in the inventory's pass 2 record: a pytest with an explicit allowlist replaces the grep, and the test is renamed `test_must_deny_paths_parity`.
4. **`_check` stays in `engine/rubrics/draft.py`,** since only `draft()` uses it. `data/rubrics.py` takes only `check`, `form_shape`, `canonical`, `validation_error`, `as_rubric_json`, the regexes, the limits, `Source` and `DraftResult`, and no private names cross a layer.

**Pass 2 questions, decided:**
- **`run_rubric`** goes to `services/runs.py`. It uses `data.runs` and `data.rubrics` directly, and `runs` imports `rubrics` for `rubric_info` and `rubric_options`. `rubrics` never imports `runs`, so there's no cycle.
- **Batch rows:** `data.batches.batch_rows(path, runs_dir)` takes `runs_dir` explicitly to decide each row's `link`, exactly as today. `batch_list(batches_dir)` is unchanged.

**Pass 2 minors, applied:**
- **Patching the model provider:** services reach `build_provider` the way the code does today, as the module attribute `registry.build_provider(...)` at call time, never `from … import build_provider` at the top. So `test_api_ports.py`'s patch of `foundry.engine.models.registry.build_provider` still intercepts it.
- **The run-detail route** renders `report_md` into `report_html` and removes `report_md`, keeping the response shape. `test_api.py::test_run_detail_and_404` gains `assert "report_md" not in body`.
- **Build order, revised:**
  - slice 2: data
  - slice 3: engine
  - **slice 4: remove the classic UI** (its own commit; the ports already pass)
  - slice 5: services, api, ui and server
  - slice 6: the architecture test made strict (its allowlist emptied) and the docs

  Deleting the classic UI before slice 5 means its handlers are never rewired. The inventory's "5b" becomes slice 4.
- **Public helpers in `data.runs`:** `num` and `float_or_none` (were `_num` and `_float`), with the other readers.
- **Old package directories:** after each `git mv`, any directory left holding only `__pycache__` is deleted (`find src/foundry -type d -empty -delete` after removing the `__pycache__` dirs). Otherwise the old paths would still import as namespace packages. The import sweep then asserts that `import foundry.harness` fails.
- **The jinja2 rule** catches only a direct `import jinja2`. With the classic UI deleted and `jinja2` out of the dependencies, nothing can pull it in indirectly.

**Pass 3 (the last allowed pass): one blocker, taken to the user with a fix. The user approved it (2026-10-31): "Yes, build with these fixes".**
- **The `runs` ↔ `prompts` cycle.** `task_prompts`, `_check_runnable` (renamed `check_runnable`) and `_rel` (renamed `relative_paths`) go to `services/configs.py`, a leaf that imports only data, engine and kernel. **The full services import graph** is enforced by a no-cycles test over `foundry.services.*`:

  | Module | Imports |
  |---|---|
  | `errors`, `stats`, `run_view`, `configs`, `workspace` | no other services module (`stats` and `run_view` use engine, data and kernel only) |
  | `jobs` | `workspace` (`Workspace` is used only as a string annotation, so `workspace` never imports `jobs`) |
  | `rubrics` | `configs`, `errors` |
  | `runs` | `rubrics`, `stats`, `run_view`, `jobs`, `configs`, `errors` |
  | `prompts` | `runs`, `stats`, `configs`, `errors` |
  | `tools` | `prompts`, `runs`, `jobs`, `configs`, `errors` |
  | `batches` | `configs`, `jobs`, `errors` |
  | `imports` | `configs`, `errors` |
  | `dashboard` | `stats`, `runs`, `rubrics` |

**Pass 3 questions and minors, applied:**
- **Static string checks:** besides the architecture test, `test_architecture.py::test_no_stale_module_paths` fails on any `-m foundry.(harness|mcp_servers)` or `files("foundry.templates")` in `src/`, `tests/`, `scripts/` or `config/`. It replaces pass 1's `-m` grep. Stale imports are caught by the "unclassified target" rule.
- **Every module under `src/foundry` must be classified by its own path.** An orphan such as `rubrics/__init__.py` or a leftover `evaluate/__init__.py` fails the test. Each new package has its own `__init__.py` with a one-line description of the layer.
- **The kernel may read files:** `contract.validate_run_dir`, `contract.evaluation_interrupted` and `config.load_config` read run files and TOML as the shared kernel. Spec L1's "data owns every read" is narrowed to "every read by services, api and ui", which `test_architecture.py` enforces.
- **`GET /api/rubric/drafts/{id}/source`:** the route gets the bytes from `services.rubrics.draft_source()`, which calls `data.rubrics.read_source(path, None)`. The route makes no filesystem call.
- **`data.runs.read_trace_lenient(path)`** keeps `_card_extras`' lenient parse exactly as it is: raw lines, `errors="replace"`, dicts only, bad lines skipped. `run_view` keeps using `RunDir.read_steps`. These are two named functions, so there's no behaviour change.
- **`/assets` traversal checks** target files that exist: `/assets/..%2Findex.html` and `/assets/%2e%2e/%2e%2e/spa.py` must give 404 without the file's contents.
- **A visible change outside `/api`:** a missing asset now returns a plain 404, no longer JSON. B1 covers only `/api`.
- **Stale path text** is updated in slice 3 (engine): README lines 120, 206 and 208, the `pyproject.toml:49` comment, and `web/src/types.ts:1`.
