# Increment 16: clear layers — UI, API, services, data, engine — Spec

**Track:** Standard, with a sunset (removing the classic UI)   **Date:** 2026-10-31   **Status:** Draft

## Problem

The code has layers in name only. An engineer can't tell where a piece of logic belongs, or change one layer without touching another.

- `ui/app.py` (795 lines) is at once:
  - the classic Jinja UI
  - the data layer: safe reads of run files, scores, batches and configs
  - the job runner that starts `foundry` subprocesses
- `ui/actions.py`, the services, reaches 19 private helpers in that file through a `_core()` import-cycle workaround.
- `ui/api.py`, the HTTP layer, reads `trace.jsonl` itself, and also reaches into `ui/app.py`.
- The engine (harness, scoring, batch, import) is separate in practice, but sits at the top level beside data modules (`runstore.py`, `tasks.py`), with nothing saying which is which.

Nothing stops a new import from crossing a boundary.

**Who has it:** anyone changing the code. That's the builder now, and soon a teammate and Clay's engineers, who will read it cold.

## Acceptance criteria

**Layers (L1–L3)**
- **L1:** `src/foundry` is organised into these layers, each a package, except the kernel and the entry points:

  | Layer | Contents | May import from foundry |
  |---|---|---|
  | **kernel** | `config.py`, `contract.py`: settings and the run-record schemas | each other only |
  | **data** | `data/`: every read and write of project and run files (run dirs, run.json, scores, traces, batches, prompts, rubrics, tasks, locks, the tool cache, the S3 mirror) | kernel, data |
  | **engine** | `engine/`: harness, models, MCP servers, tracing, evaluation, the batch runner, importer, receiver, external runner, rubric drafting | kernel, data, engine |
  | **services** | `services/`: what the app does (runs, evaluation, batches, prompts, rubrics, tools, imports, dashboard, jobs), raising `ServiceError` | kernel, data, engine, services |
  | **api** | `api/`: HTTP only (routes, request and response models, the write guard, error mapping, rendering run text to safe HTML) | kernel, services, api |
  | **ui** | `ui/`: serves the built web app (`web_dist`) and nothing else | nothing |
  | **entry points** | `server.py` (builds the web app from api and ui), `cli.py` and `__main__.py` | any layer |

- **L2:** `tests/test_architecture.py` parses every module under `src/foundry` with `ast` and fails if:
  - an import breaks the table in L1
  - `fastapi` or `starlette` is imported outside `api/`, `ui/` and `server.py`
  - `jinja2` is imported anywhere

  It names the offending module, line and rule. A deliberately bad import in a scratch copy makes it fail, which proves the test actually checks something.
- **L3:** no `_core()` or other import-cycle workaround remains. No module imports another layer's private name (a leading `_`).

**Behaviour unchanged (B1–B3)**
- **B1:** every `/api/*` route keeps its method, path, request and response shapes, status codes, and error bodies. There is one fix: `/api/receiver` reports the port it actually bound, found by the ports. `test_api.py`, `test_prompts.py`, `test_tools.py` and `test_api_ports.py` pass with only these changes: import lines, module-path strings (`-m …`), patch targets, and the two `/classic` assertions listed in the inventory's gate record.
- **B2:** both e2e scripts pass unchanged. That covers the board, run, scoring, Rubric tab, logo, Setup menu, tool picker and clarity checks on every page, the prompts flow, and the real `runs/` left unchanged.
- **B3:** the CLI behaves the same: `foundry run`, `evaluate`, `batch`, `import`, `receive`, `external`, `validate`, `rubric`, `tools` and `ui`, with the same arguments and exit codes. The existing CLI and engine tests pass with only changes to import lines and module-path strings (`-m foundry.engine.mcp_servers.stac`, `-m foundry.engine.harness.reaper`). The in-repo MCP server keeps its recorded identity: `package = "foundry.mcp_servers.stac"` stays the same in configs, so runs remain comparable. Only its launch module path changes.

**The classic UI removed (S1–S2)**
- **S1:** `/classic` and everything only it used are gone:
  - its routes and Jinja templates
  - `static/monitor.js` and the `markdown` template filter
  - `Job.failed_visibly`'s classic banner use, if nothing else uses it
  - `conftest.classic_client`

  `GET /classic` and `/classic/anything` then fall through to the web app, like any unknown path.
- **S2:** each classic test's check is covered against `/api` or a unit, and the dependent inventory records which. Checks that only exercised HTML rendering of the classic pages are dropped, with the reason recorded.

**Docs (D1)**
- **D1:** `docs/architecture.md` (new) explains:
  - the layers and their import rules, with the table from L1
  - where to put new code ("a new screen: web plus api route plus service; a new file format: data; a new scorer: engine")
  - how `test_architecture.py` enforces the rules

  README's development section points to it, and loses its `/classic` mention.

## Non-goals

- Changing behaviour, the API, the UI, file formats or config semantics.
- Splitting the React app into new folders.
- Rewriting modules beyond moving them and cutting the cross-layer calls.
- New features.
- Packaging changes beyond the moved paths. The `foundry` entry point stays `foundry.cli:main`.

## Assumptions and open questions

- **A1 (user decision, 2026-10-31):** five packages with strict layers, and the classic UI removed.
- **A2:** nothing outside this repo imports `foundry.*`. Infra (`infra/stack.py`) and CI don't, as checked by grep on 2026-10-31. The only external references are the module paths in `config/*.toml` (`-m foundry.mcp_servers.stac`), which are updated.
- **A3:** past `run.json` files record `mcp_servers[].package` as a label, never as an import path, so moving the module doesn't change how they're read.
