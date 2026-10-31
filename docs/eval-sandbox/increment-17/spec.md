# Increment 17: API contracts — Spec

**Track:** Standard   **Date:** 2026-10-31   **Status:** Draft

## Problem

The JSON API has no declared response shapes. Services return plain dicts, routes pass them through, and the web app's types are hand-written guesses that `api.ts` trusts with a cast. A renamed or dropped key fails no test and no compiler; the bug shows up only in a browser, if a page happens to be covered.

- **Responses:** none of the 36 routes declares a response model.
- **Web types:** `web/src/types.ts` is 31 hand-written interfaces that nothing checks against Python. Call sites choose a type by hand (`usePoll<T>(url)`), and three pages define their own local types.
- **Requests:** request bodies accept unknown fields silently. Batch ranges are checked inside the service rather than in the body model.

**Who has it:** anyone changing an API field or a page: the builder now, and soon a teammate and Clay's engineers.

**User decision (2026-10-31):** build steps 1–3 of the recommendation together:

1. response models
2. TypeScript generated from them
3. stricter request bodies

## Acceptance criteria

**Response contracts (R1–R4)**
- **R1:** every `/api` route declares its response.
  - JSON routes declare a Pydantic response model from `foundry/api/schemas.py`.
  - The rubric-source download and the unknown-route catch-all are exempt, and declare `Response` and a 404 error respectively.
  - A test lists the app's routes and fails if a JSON route has no response model.
- **R2:** FastAPI checks every JSON response against its model at runtime.
  - Models of data the app itself builds forbid unknown keys.
  - Models of file formats that admit unknown keys allow them, but type every field the web app reads. That covers the untrusted run files (`run.json`, `scores.json`, `trace.jsonl` steps, `batch.json`, `summary.csv` rows) and the rubric store's files (rubrics, `history.jsonl`, draft `meta.json`).
  - A logged line names the route and the field of any response that breaks its model.
  - A response that breaks its model returns 500 `{"error": "Internal Server Error"}`; a test proves this.
- **R3:** keys a service doesn't send stay absent (`response_model_exclude_unset`). Against the same `runs/`, every GET route returns the same JSON as increment 16, except where R4 or Q2 change it. Equality is semantic: parsed JSON compared as values, so `3` equals `3.0`, which JavaScript can't tell apart either. This is proven by diffing old and new servers on the real `runs/` and on the test fixtures.
- **R4:** an untrusted file can't make a route return 500. The data layer guarantees each declared type before data reaches a response:
  - **`run.json`:** a run is unreadable, like today's rule for `prompt_id` and `status`, when any of these is present, not `null`, and of the wrong type: `model.id`, `prompt`, `task_id`, `started_at` (str), `agents` (object) or `agents.mode` (str). `null` stays allowed, as the run contract allows it.
  - **`history.jsonl`:** a line without a string `rubric_id` and `version` is skipped, like a line without `file` and `sha256` today. So is one whose `activated_at`, `note` or `draft_id` isn't a string or `null`, or whose `source` isn't an object or `null`.
  - **Non-finite numbers:** `NaN` and infinity in `scores.json` count as missing, like a non-number.
  - **`scores.json` workflow cost:** a boolean `workflow.cost_usd` is not a cost; the board card falls back to the trace's cost, as for a missing one.
  - **`scores.json`:**
    - `output.criteria` that isn't a list is dropped.
    - Items without a string `id` and a numeric `score` are dropped.
    - A non-numeric (or boolean) `score` drops the item. A non-numeric or boolean `max`, or a non-string `name` or `rationale`, is dropped from its item.
  - **`summary.csv`:** each row's known columns are strings, `""` when missing.
  - **`batch.json`:** `status`, `jobs`, `max_cost`, `spent_usd` and `models` are dropped when they have the wrong type.
  - **The test:** hostile fixtures for each case pass through every GET route with 200.

**Generated web types (T1–T3)**
- **T1:** `web/src/types.ts` is generated from the schemas by `scripts/gen_api_types.py`, with no new dependency.
  - It keeps every exported name the web app uses today: `Card`, `Column`, `RunDetail` and the rest.
  - It adds names for the responses that were inline or local: `StartRunResponse`, `ReceiverStatus` and the like.
  - A header says it is generated.
  - `tsc` and Vitest pass.
- **T2:** a pytest test regenerates the types in memory and fails if the committed `types.ts` differs, naming the command that fixes it.
- **T3:** every `usePoll<T>`, `get<T>`, `post<T>`, `put<T>` and `upload<T>` call with a literal `/api` URL names exactly the type its route declares. A pytest test enforces this, and the three local types are removed.

**Request contracts (Q1–Q2)**
- **Q1:** every JSON request body forbids unknown fields. One returns 422 `"<field>: Extra inputs are not permitted"`.
- **Q2:** batch ranges live in `BatchBody`: repeats 1–5, concurrency 1–4, and 0 < max_cost ≤ 50.
  - The limits are constants in `services/batches.py`, used by both the model and the service.
  - An out-of-range value now returns **422** naming the field, where it was 400. This is the one approved status change.

**Unchanged (U1)**
- **U1:** everything else behaves as before.
  - The full suite passes. Its only changes are the two batch-range assertions (400 → 422) and new tests.
  - Both e2e scripts pass unchanged.
  - The web app looks and works the same.

**Docs (D1)**
- **D1:** `docs/architecture.md` gains an "API contract" section: where the models live, open versus closed models, how to add a field (schema → service → `scripts/gen_api_types.py`), and the tests that hold it.

## Success signal

- Renaming a response key in a service fails pytest at once. Response validation fails on that route's tests, the staleness test fails, and `tsc` fails once the types are regenerated. Before this change, nothing failed.
- Each of these is demonstrated once during Verify, then reverted.

## Non-goals

- Typing query parameters. They stay validated in the services.
- Making services return model instances. Services keep returning plain data, and the API checks it at the boundary.
- Serving OpenAPI. `openapi_url` stays off.
- A typed route map in `api.ts`. T3's test covers the same risk.
- Multipart forms (`/api/import`, `/api/rubric/drafts`).
- Changing any value, wording or page.

## Assumptions and open questions

- **A1 (verified, scratchpad `rm_probe.py`, FastAPI 0.162.0, Pydantic 2.14.2):**
  - An unknown key under `extra="forbid"`, or a wrong type, returns 500.
  - `extra="allow"` passes unknown keys through.
  - `response_model_exclude_unset=True` keeps exactly the keys returned.
- **A2:** the web app is the only client. The 400 → 422 change in Q2 affects only out-of-range batch forms, and the form's own limits already prevent those. Owner: builder. Default: accept.
- **A3:** `data.runs.read_run()` and `read_scores()` (the dict readers) are used only by services. The engine reads runs through `RunDir`, so R4's tightening doesn't touch scoring (checked by grep, 2026-10-31).
