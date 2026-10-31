# Increment 17: API contracts — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Date:** 2026-10-31   **Track:** Standard

## What shipped

1. **Every `/api` route declares its response,** and FastAPI checks each response against it.
   - 35 JSON routes use 89 models in `src/foundry/api/schemas.py`. The rubric-source download and the unknown-route catch-all are exempt.
   - Data the app builds is **closed**: an unknown key or a wrong type returns 500, and `foundry.api` logs the route and field.
   - Files the app only reads are **open**: unknown keys pass through, and the fields the web reads are typed.
2. **The data layer makes the open types true** (R4), so a tampered file can't 500 a page.
   - **`run.json`:** a run with a wrong-typed `model.id`, `prompt`, `task_id`, `started_at`, `agents` or `agents.mode` shows as unreadable.
   - **`scores.json`:** junk judge criteria are dropped, and a boolean cost isn't a cost.
   - **Batch files:** `summary.csv` columns are always strings, and wrong-typed `batch.json` fields are dropped.
   - **`history.jsonl`:** malformed lines are skipped.
3. **The web app's types are generated.**
   - **The generator:** `scripts/gen_api_types.py`, with no new dependency, writes `web/src/types.ts` from the models. It keeps every name the app imported, adds names for the responses that were inline, and ends with an `ApiRoutes` map.
   - **The call sites:** 14 call sites now use the generated names, and three local types are gone.
   - **The tests:** one fails when `types.ts` is stale; another fails when a call site names the wrong type, an inline type, or no type for a read.
4. **Request bodies are strict.**
   - An unknown field returns 422 `"<field>: Extra inputs are not permitted"`.
   - Batch ranges live in `BatchBody` (repeats 1–5, concurrency 1–4, 0 < cost cap ≤ 50) and return 422 naming the field. The limits are shared constants in `services/batches.py`.
5. **Docs:** `docs/architecture.md` has an "API contract" section: closed versus open models, how to add a field, and the tests that hold it.

## Decisions

- **The user chose:** build steps 1–3 together.
- **Response models live in the api layer, and services keep returning plain data** (ADR-1). The HTTP shape is the API's contract, and no service logic changed.
- **Our own generator,** not `openapi-typescript` (ADR-2). No new npm dependency, and it keeps today's flat type names, so 20 importing files didn't churn.
- **Open models for every file format that admits unknown keys** (ADR-3), each typed field backed by a data-layer guarantee (the design's table).
- **Numbers:** an integral `float` serializes as `3.0` where the service sent `3`. JavaScript can't tell them apart, so R3's comparison is by value.
- **Routes are read from `make_router()`.** FastAPI 0.162 keeps an included router as one opaque `_IncludedRouter` entry in `app.routes`, so the generator and tests read routes from the public router factory that `server.py` includes, not from FastAPI internals.
- **Pages named like their type** (`Compare`, `Dashboard`, `RubricVersion`) import it under an alias, and the call-site test resolves `import type { X as Y }`.
- **The one status change** (Q2): an out-of-range batch value is now 422, not 400. The form's own limits already prevent it.

## Verification

**Suites:**
- Python: 353 passed (349 offline plus 4 `network` tests). 19 of them are new, in `tests/test_api_contract.py`.
- Vitest: 26 passed.
- `tsc`, ruff and the bundle build are clean.
- Both e2e scripts PASS.

| AC | How it was proven |
|---|---|
| R1 | `test_every_json_route_declares_its_response`: every route from `make_router()` has a response model and `exclude_unset`, the exempt list is current, and there are at least 30 routes. |
| R2 | `test_response_drift_is_a_500`: a service returning an extra key, or a wrong type (`"maybe"` for a bool), gets 500 `{"error": "Internal Server Error"}`, and the log line reads `GET /api/receiver: response.renamed extra_forbidden`. |
| R3 | **Live, on your real `runs/`:** the new server (port 8799) against the increment 16 server (port 8765), compared by value on **184 responses**: every run's detail, every scored run's rubric, 32 compare pairs, every batch, every prompt, the dashboard for each rubric option, and the board, session, tools, rubric, receiver and jobs. Every response was the same except `/session.aws_profile`, because the comparison server was started without `AWS_PROFILE` set (an environment difference, not code). |
| R4 | `test_hostile_files_never_500` seeds six hostile `run.json` variants, junk criteria, a boolean cost, a wrong-typed `batch.json` and a ragged `summary.csv`, then makes 17 GET requests (every GET route that reads them): all 200, the six runs unreadable, and the criteria reduced to the one valid item. |
| T1 | `types.ts` is generated; `tsc` is clean; Vitest and both e2e pass. |
| T2 | `test_types_ts_is_current`, plus `test_generator_rejects_what_it_cannot_express`. |
| T3 | `test_call_sites_use_the_route_type` finds the call sites and checks each one. `test_call_site_check_catches_each_mistake` proves it reports an inline type, an untyped read, a wrong type, a wrong ternary branch, the first-match rule, an unlisted variable URL and an unknown route, and passes correct snippets. |
| Q1 | `test_unknown_body_field_is_422`, for all 10 JSON body routes. |
| Q2 | `test_batch_ranges_are_422` (six out-of-range values, each naming its field); `test_batch_validation` now expects 422 for `max_cost: 500` and `concurrency: 40`. |
| U1 | The full suite. The only edit to an existing pytest assertion is Q2's. The Vitest card fixture gained `unreadable` and `kpi`, which the API always sends and the generated `Card` now requires. Both e2e pass unchanged. |
| D1 | `docs/architecture.md`, "The API contract". |
| Success signal | Shown, then reverted. Renaming `count` in `services/imports.py` failed 4 tests. Renaming it in the model failed `test_types_ts_is_current`, and after regenerating, `tsc` reported `Property 'count' does not exist on type 'ReceiverStatus'` at `Import.tsx:86`. |
| Performance | The worst added latency in the R3 comparison was +10.4 ms (`/prompts/adb-001`, 25 → 35 ms). The largest run detail went 6 → 14 ms. Both are under the 20 ms budget. |

## Gates

- **Design gate, 3 passes:**
  - Pass 1's six blockers were fixed: the rubric models made open; models taken from the writers, not the captures; the float claim corrected; a table of guarantees per open field; the call-site rules; drift logging.
  - Pass 2's two blockers were fixed: R4 now covers `started_at`, and the call-site test actually implements its rules.
  - **Pass 3 still had two blockers,** and by the process they come to you. Both were bugs with one obvious fix, so I applied the fix and record it here for your review:
    1. A `history.jsonl` line with no `activated_at`, or a non-string `source.kind`, 500d `/api/rubric`. Now the reader fills `activated_at` with null and checks every `source` value; `test_hostile_history_never_500` covers it.
    2. The call-site checker skipped single-quoted and computed URLs. Now any typed call without an `/api` literal counts as a variable URL, and it fails unless listed; the self-test has four new cases.
  - **Also taken from pass 3:** `NaN` and infinity in `scores.json` now count as "not scored", where they were already a 500 before this increment, and a vacuous assertion was made real.
- **Verify gate, pass 1:** no blockers.
  - It re-ran the suite, `tsc`, Vitest and the generator check.
  - It rebuilt the bundle and got a byte-identical file.
  - It compared old and new on 172 scratch requests covering live, pipeline, locked, interrupted, failed, imported and empty states, a running batch, jobs, and drafts and history through activations. Every difference was one R4 intends.
  - Its minor findings were fixed or recorded below.

**Value changes beyond R4, all harmless, recorded for R3:**
- An invalid draft's `weight: true` now shows as `1.0`, and a string `usage.cost_usd` of `"0.1"` as `0.1` (lax coercion on store-written files).
- `Infinity` in `run.json` limits, or `nan` in a `summary.csv` cost, was a 500 and is now a 200.
- The real `runs/` holds only succeeded runs, so the other run states were proven on fixtures and scratch copies.

## Deferred and follow-ups

- Query parameters (`task`, `rubric`, compare ids) are still validated in the services (a non-goal).
- `DraftMeta` is trusted as the store writes it. A hand-broken `tasks/adb/rubrics/drafts/*/meta.json` would 500 `/api/rubric` (an accepted risk, recorded in the design).
- A typed fetch helper (`get("/api/runs")` inferring `RunsResponse` from `ApiRoutes`) would make T3's test unnecessary. The test covers the same risk today.
