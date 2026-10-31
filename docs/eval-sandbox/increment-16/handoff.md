# Increment 16: clear layers — UI, API, services, data, engine — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Inventory:** ./dependent-inventory.md (each has an authoritative gate record)   **Date:** 2026-10-31   **Track:** Standard, with a sunset

## What shipped

1. **Five layers, each a package, with import rules a test enforces.** `src/foundry` is now:

   | Layer | Where | What |
   |---|---|---|
   | kernel | `config.py`, `contract.py` | settings, run-record schemas |
   | data | `data/` | every read and write of project and run files |
   | engine | `engine/` | harness, models, MCP servers, tracing, scoring, batch, import, receiver, rubric drafting |
   | services | `services/` | what the app does, raising `ServiceError` |
   | api | `api/` | HTTP only: routes, write guard, error mapping, safe HTML |
   | ui | `ui/` | serves the built web app; imports nothing from foundry |
   | entry points | `server.py`, `cli.py`, `cli_rubric.py`, `__main__.py` | wire the layers together |

   - `ui/app.py` (the data reads, the job runner and the classic UI in one file), `ui/actions.py` (with its `_core()` import-cycle workaround) and `ui/api.py` are gone.
   - Their code was split across `data/`, `services/`, `api/`, `ui/spa.py` and `server.py`.
2. **The classic UI is removed.** `/classic`, its Jinja templates, `static/monitor.js`, `jinja2` and `markupsafe` are gone. `/classic` now opens the web app like any unknown path. Each classic test's check was ported to `/api` (`tests/test_api_ports.py`, 30 checks) or dropped with its reason recorded in the inventory.
3. **`docs/architecture.md`.** The layers, the rules, where new code goes, how the server is built, and how the test checks it. The README's Tests section links to it.

Nothing a user sees changed. Every `/api` route returns the same JSON, apart from one allowed fix: `/api/receiver` reports the port it actually bound.

## Decisions

- **The user chose:** five packages with strict layers, and removing the classic UI (A1). After pass 3 of the define and design gates, the user approved building with the proposed fixes, twice.
- **Services return Markdown, the API renders it.** `services.runs.detail()` returns `report_md`. The route turns it into `report_html` and model text into `text_html`. Only `api/render.py` makes HTML, as the threat model says.
- **Services and api don't touch files or processes.** They go through `data/` and `services/jobs.py`. Two modules are exempt, each for one job: `services/jobs.py` (subprocesses and their logs) and `api/uploads.py` (streaming an upload to disk under its cap).
- **The MCP server keeps its identity.** Configs launch `-m foundry.engine.mcp_servers.stac`, but `package = "foundry.mcp_servers.stac"` stays, so old and new runs remain comparable (B3).
- **`cli_rubric.py` is a fourth entry point.** The design said `rubrics/cli.py` would fold into `cli.py`; it moved beside it instead, unchanged, to keep `cli.py` from growing by 150 lines. The test's `ENTRY`, `docs/architecture.md` and this handoff list it.
- **Rubric drafting's entry (`acquire`, `create_draft`) sits in `services/rubrics.py`,** as the design approved; the drafting itself (`draft`, `sources`) stays in `engine/rubrics/`.
- **Allowed fixes found along the way:** `/api/receiver` reports the bound port, and `/assets/%00` returns 404, not 500.
- **Each slice is its own commit,** so any slice can be reverted alone:

  | Commit | Slice |
  |---|---|
  | `81739ae` | 1: the classic checks ported to `/api` |
  | `f2eed17`, `c0ca098`, `25c5d0b` | 2a–2c: `data/` |
  | `22e4c45` | the architecture test |
  | `7d2213c` | 3: `engine/` |
  | `4a0fba2` | 4: the classic UI removed |
  | `a6cbf56` | 5: `services/`, `api/`, `ui/spa.py`, `server.py` |
  | the final commit | 6: docs, this handoff, and the verify gate's fixes |

## Verify gate

- **Pass 2** found no blockers. It proved the rule in a scratch copy: five bad imports reported, including relative and submodule ones, and no false positives. The check covers imports only, not attribute access such as `runs._atomic_write`; the doc says so.
- **Pass 1** found one blocker: the private-name rule wasn't enforced. `imports()` dropped the imported name, so `from foundry.data.runs import _atomic_write` in a service passed, though the docs said it fails. **Fixed:** each imported name is now checked, and the self-test asserts it, along with `jinja2`.
- **Minors fixed:**
  - The file check missed `import os as o`, `from os import replace` and `tempfile`; it catches them now, with a self-test.
  - `import_saved_trace` loads the config's prices before reading the trace again, so when both are bad, the same error wins as before.
  - The handoff's wording was corrected.
- **Accepted as is:** `importlib` and `__import__` aren't checked (design line 223), and nothing in `src` uses them.
- **The reviewer's own B1 check:** 76 requests against the code before slice 5 and after, through FastAPI's test client. These covered every GET route; the 404, 400, 409, 415 and 422 paths; bad Host; cross-origin writes; a missing CSRF token; the wrong content types; upload errors; `/classic`; and `/assets/%00`. Status codes, security headers and bodies were identical.

## Verification

**Suites:**
- Python: 334 passed (330 offline plus 4 `network` tests). The count fell from 384 because the classic UI's tests went with it; their checks are ported, as listed in the inventory.
- Vitest: 26 passed.
- ruff is clean, and gitleaks found no leaks.

| AC | How it was proven |
|---|---|
| L1 | The tree matches the table. `test_layers` passes with `UNMOVED` empty, and `test_every_module_has_moved` asserts it stays empty. |
| L2 | `tests/test_architecture.py::test_layers` checks every import, including those inside functions, against the table. It also checks `fastapi`/`starlette` outside `api/`, `ui/` and the entry points, and `jinja2`/`markupsafe` anywhere. `test_the_checker_catches_a_bad_edge` builds a scratch package with a data→engine import, a stray module and a `fastapi` import in data, and asserts all three are reported with file and line. |
| L3 | `grep _core` finds nothing in `src`. `test_layers` fails on any import of another layer's private name (`from foundry.data.runs import _atomic_write`); the self-test proves it. `test_services_have_no_import_cycles` walks the services graph. Added beyond the spec: `test_services_and_api_dont_touch_files`, with its own self-test. |
| B1 | `test_api.py`, `test_prompts.py`, `test_tools.py` and `test_api_ports.py` pass. Their only changes are import lines, the patch target (`foundry.services.jobs.spawn`), the two `/classic` assertions, and one new assertion that `report_md` never reaches the client. **Live, against your real `runs/` (65 runs):** the new server (port 8799) and the increment 15 server (port 8765) returned byte-identical JSON for all 14 endpoints checked: `runs`, a run's detail, its rubric, `dashboard` (active and all), `batches`, `prompts`, `prompts/adb-001`, `rubric`, `rubric/active`, `receiver`, `compare`, and both 404s. |
| B2 | `node scripts/ui_e2e.mjs` and `node scripts/ui_e2e_prompts.mjs` both PASS unchanged. That covers a real offline run and its scoring, the Rubric tab, logo, Setup menu, tool picker, clarity on 15 pages at both widths and in both themes, the prompts flow, and "real runs/ unchanged". |
| B3 | The CLI and engine tests pass, with only import and `-m` path changes. `test_no_stale_module_paths` guards the `-m` strings. `foundry rubric --help` and `foundry ui --help` print as before. A real `foundry ui` started through the CLI and served every page. A real offline `foundry run` ran in slice 3. |
| S1 | `test_no_classic_ui_left` scans `src`, `tests`, `scripts`, `web/src` and README for leftovers, with an allowlist that can't go stale. `/classic`, `/classic/` and `/classic/runs/x` return the web app (`test_api.py`). `jinja2` and `markupsafe` are gone from `pyproject.toml` and `uv.lock`. |
| S2 | `dependent-inventory.md` maps each classic test to its port, or gives the reason it was dropped. |
| D1 | `docs/architecture.md`; the README links to it. |

**Exercised live:** yes, on the real server through `foundry ui`, against the real `runs/`, reading only.

## Deferred and follow-ups

- **Your UI server (port 8765)** still runs the increment 15 code. It's restarted on this code after the push, once no jobs are running.
- **Still open from earlier increments:**
  - whether the "Spent" KPI should cover only scored runs
  - the 2 remaining scripted-pipeline runs
- **Possible next steps** (not built):
  - Split `services/runs.py` (about 300 lines) into board and compare modules if it keeps growing.
  - Type the service return values (TypedDicts) so the API's shapes are declared once.
