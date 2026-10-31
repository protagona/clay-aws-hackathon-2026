# Increment 17: API contracts — Design

**Spec:** ./spec.md   **Date:** 2026-10-31

## Overview

```
services (plain dicts) ──▶ api/routes.py ──response_model──▶ api/schemas.py (Pydantic) ──▶ JSON
                                                              │
                     scripts/gen_api_types.py ◀───────────────┘ ──▶ web/src/types.ts (generated)
                     tests/test_api_contract.py: every route typed · types.ts current · call sites match
```

## Decisions (ADRs)

**ADR-1: the response models live in the api layer (`api/schemas.py`), and services keep returning plain data.**
- **Options considered:**
  - (a) services build model instances
  - (b) the api declares the models and validates at the boundary
- **Decision:** (b). The HTTP shape is the API's contract, and (b) touches no service logic.
- **Consequence:** a service that drifts fails its route with a 500. The route tests exercise every route on fixtures, so they catch drift.
- **Sources:** FastAPI's response model docs (fastapi.tiangolo.com/tutorial/response-model, for the 0.162.0 in use); behaviour verified in scratchpad `rm_probe.py`.

**ADR-2: our own small generator, not `openapi-typescript`.**
- **Why:**
  - No new npm dependency (the repo rule against unvetted packages).
  - The output keeps today's flat interface names (`Card`, `RunDetail`), so the 20 files that import `types.ts` keep working. `openapi-typescript` emits `components["schemas"]["Card"]` and would need wrapper aliases.
- **How:** the generator reads Pydantic's JSON Schema (`pydantic.json_schema.models_json_schema`), which is stable in Pydantic 2.14. It handles only the constructs our schemas use: object, array, prefixItems (tuples), anyOf, enum/const, `$ref`, `additionalProperties`, the scalars, and `{}`.
- **Unsupported constructs:** the generator raises on them, so a new construct can't be emitted silently wrong.

**ADR-3: closed models by default, open models for every file format that is open.**
- `Closed` uses `extra="forbid"`, and covers data the services build themselves.
- `Open` uses `extra="allow"`, and covers every model that mirrors a file whose format admits keys we don't know: untrusted run files, and project files whose kernel model is `_Open`.
- **In TypeScript:** an open model becomes an interface with `[key: string]: unknown`.
- **Source of truth:** each model's fields come from its **writer**, not from captures. The captures were empty for history rows, drafts, jobs, received runs and tool errors, so the captures serve only as a check (every captured response validates and round-trips).

| Model | Open/Closed | Writer (source of truth) | Typed fields; everything else passes through | Guarantee for each typed field |
|---|---|---|---|---|
| `RunInfo` (and `PromptRun.started_at`) | Open | `run.json` (harness, importer; untrusted) | `run_id`, `prompt_id`, `status`: str. `model`: `RunModel`. `prompt`, `task_id`, `started_at`: str or null, optional. `agents`: `RunAgents` or null, optional | `run_id` is the directory name (service). `read_run` checks `prompt_id` and `status` (already) and rejects the run when an optional one is present, non-null and of the wrong type (R4). |
| `RunModel` | Open | same | `id`: str, **optional** | `read_run` rejects the run when `model` isn't a dict (already) or `id` is present with the wrong type |
| `RunAgents` | Open | same | `mode`: str, optional | `read_run` rejects the run when `mode` is present with the wrong type |
| `Scores` | Open | `scores.json` (evaluate; untrusted) | `overall`: float or null. `workflow`: `WorkflowScores`. `output`: `OutputScores`. **Not typed** (they stay `unknown`; the web doesn't read them): `rubric_version`, `rubric_id_loaded`, `components`, `sequence` | `read_scores` returns None unless `workflow` and `output` are dicts, and runs `num()` on the three scores (already) |
| `WorkflowScores`, `OutputScores` | Open | same | `score`: float or null. `criteria`: `Criterion[]`, optional | `read_scores` (R4) |
| `Criterion` (scores) | Open | same | `id`: str. `score`: float. `max`, `name`, `rationale`: optional | `read_scores` drops bad items and keys (R4), and rejects bools in `score` and `max` |
| `BatchMeta` | Open | `batch.json` (batch runner; untrusted) | `status`: str. `jobs`: int. `max_cost`, `spent_usd`: float. `models`, `configs`: str[]. All optional | `read_meta` drops each one with the wrong type (R4), bools included |
| `BatchRow` | Open | `summary.csv` (batch runner; untrusted) | the 12 known columns: str. `link`: str or null. `cost`, `overall_f`: float or null | `batch_rows` coerces the known columns to str (R4). `cost` and `overall_f` come from `num(float_or_none())` (already). |
| `Rubric`, `RubricCriterion` | **Open** | `tasks/<task>/rubric.json`, history files, run copies (kernel `Rubric` and `Criterion` are `_Open`; run copies are untrusted) | as the kernel model. The draft form shape (`form_shape`) has the same keys. `task_id` is optional (form shape omits it). | `load_rubric` validates the kernel model before any response (`with_meta`, `version`, `overview`). Draft bodies are a valid rubric or `form_shape()`, both of which yield these keys and types. |
| `RubricVersionRow`, `VersionSource` | Open | `history.jsonl` entries (`data/rubrics.py:295,305,347,367`) plus `runs` and `active` from `versions()` | `rubric_id`, `version`, `sha256`, `file`: str. `activated_at`: str or null. `runs`: int. `active`: bool. `draft_id`, `note`: str or null, optional. `source` {`kind`, `label`, `sha256`: str or null}: optional | `Store.history()` skips lines that don't match (R4). `VersionSource` fields are all `str | None` (the store writes `meta["source"].get(k)`). |
| `DraftMeta`, `DraftSource`, `DraftUsage` | Open | `meta.json` (`data/rubrics.py:197-202`, plus `usage.cost_usd` from `services/rubrics.py:65`) | as written | written only by the store, in the project's own `tasks/` folder. Accepted risk: a hand-broken `meta.json` 500s `/api/rubric`. The store already skips meta files that aren't JSON. |
| `Step`, `StepView` | Open | `trace.jsonl`, validated by the kernel `Step` in `read_steps` | the kernel's fields; `text_html` added by the route | `read_steps_safely` |
| everything else | Closed | the service named in the route table | all keys | the service code |

- **Numbers:** a `float` field serializes an integral value as `3.0` where the service sent `3`. Lax mode also turns `True` into `1.0` and `"3.5"` into `3.5`.
  - The R3 diff is therefore semantic (parsed values compared, `3 == 3.0`). No JavaScript reader can tell the difference.
  - Lax coercion of untrusted values can't happen on a typed float field: each one is guaranteed numeric and non-bool by the table above.
  - **Sources:** design-gate probe `inc17review/p1.py` (pass 1); Pydantic 2.14 conversion table (docs.pydantic.dev/2.14/concepts/conversion_table: float in lax mode accepts int, bool and numeric str).

**ADR-4: reuse `foundry.contract.Step`** (kernel, open). `StepView(Step)` adds `text_html: str | None = None` for the run feed.

**ADR-5: the data layer makes the declared types true** (spec R4).
- A response model may type an untrusted field only if the reader guarantees that type.
- The reader either rejects the record (`run.json`, as it already does for `prompt_id`) or drops the bad value (scores, batch meta).

## Components

### `src/foundry/api/schemas.py` (new)

```python
class Closed(BaseModel): model_config = ConfigDict(extra="forbid")
class Open(BaseModel):   model_config = ConfigDict(extra="allow")
Column = Literal["running", "evaluating", "scored", "attention"]
```

**Shapes:** field names and optionality follow today's JSON exactly, as captured from the live server in scratchpad `contract/*.json` and cross-checked against the services.
- **Required:** a key the service always sends.
- **`= None` default, optional in TypeScript:** a key it sometimes omits (`Card.sub` on unreadable cards, for example).
- **Numbers:** `float`, except counts the service makes from `len` or `sum` of bools, which are `int`. An integral float serializes as `3.0` (see ADR-3), so R3 compares values.

| Route | Response model |
|---|---|
| GET /session | `Session` (with `RubricLabel`) |
| GET /runs | `RunsResponse` (`Card` with `CardKpi`, `Kpis`) |
| GET /runs/{id} | `RunDetail` (`RunInfo`, `Scores`, `RubricInfo`, `Monitor` with `Gauge`, `Row`, `Working`, `AxisTick`, `FeedItem` holding a `StepView`) |
| POST /runs | `StartRunResponse` `{run_id}` |
| POST /runs/{id}/evaluate | `Ok` `{ok: Literal[True]}` |
| GET /compare | `Compare` (`CompareSide`, `CompareRow`) |
| GET /dashboard | `Dashboard` (`DashboardData`, `DashKpis`, `LeaderRow`, `Spread`, `Scatter`, `Dot`, `LegendItem`, `XTick`, `YTick`, `Bar`, `CriterionBar`, `RubricOption`) |
| GET /batches | `BatchesResponse` (`BatchSummary`) |
| POST /batches | `StartBatchResponse` `{batch_id}` |
| GET /batches/{id} | `BatchDetail` (`BatchMeta`, `BatchRow`, `Variant`, `BatchJob`) |
| POST /import | `ImportResponse` `{runs: list[str]}` |
| GET /receiver | `ReceiverStatus` |
| POST /receiver/start, /stop | `Ok` |
| GET /rubric | `RubricOverview` (`Rubric`, `RubricCriterion`, `RubricVersionRow`, `VersionSource`, `DraftMeta`, `DraftSource`, `DraftUsage`) |
| POST /rubric/drafts | `CreateDraftResponse` `{draft_id}` |
| GET /rubric/drafts/{id} | `DraftView` |
| PUT /rubric/drafts/{id} | `DraftSaveResult` `{valid, error, field, activated}`. Its 400 still goes through `error()`. |
| DELETE /rubric/drafts/{id} | `Ok` |
| GET /rubric/drafts/{id}/source | `Response` (download; exempt) |
| GET /rubric/active, /runs/{id}/rubric | `RubricWithMeta` `{rubric, sha256, active, file}` |
| GET /rubric/history/{name} | `RubricVersion` `{task, rubric, active}` |
| POST /rubric/history/activate | `Ok` |
| GET /prompts | `PromptsResponse` (`PromptRow`) |
| GET /prompts/tools | `ToolMenu` (`ToolInfo`, `ToolError`) |
| POST /prompts/tools/refresh | `Ok` |
| GET /prompts/{id} | `PromptDetail` (`PromptRecord`, `PromptRun`) |
| POST /prompts | `CreatePromptResponse` `{prompt_id}` |
| PUT /prompts/{id} | `Ok` |
| POST /prompts/{id}/version | `CreatePromptResponse` |
| POST /prompts/{id}/archive | `Ok` |
| PUT /prompts/{id}/stages | `StagesSaved` `{ok, expected_sha256}` |
| GET /jobs | `JobsResponse` (`Job`) |
| GET /jobs/{id}/log | `JobLog` `{log}` |
| POST /jobs/{id}/dismiss | `Ok` |
| /{rest:path} (catch-all) | exempt: always raises 404 |

**Request models** move from `routes.py` to `schemas.py`:
- `RunBody`, `BatchBody`, `ReceiverBody`, `DraftBody`, `PromptBody`, `TaskBody`, `ArchiveBody`, `StagesBody`, `HistoryBody`, all `Closed`.
- **`BatchBody` ranges:**
  - `repeats: int = Field(1, ge=REPEATS[0], le=REPEATS[1])`
  - `concurrency: int = Field(2, ge=CONCURRENCY[0], le=CONCURRENCY[1])`
  - `max_cost: float = Field(2.0, gt=0, le=MAX_COST)`

  The limits are imported from `services.batches` (api may import services).
- **`DraftBody.body`** stays `dict`. The store validates it and reports errors by field.

### `src/foundry/api/routes.py`

- Every JSON route gets `response_model=X, response_model_exclude_unset=True` through a small wrapper (`def route(method, path, model, **kw)`), so no route can forget `exclude_unset`.
- Responses still pass through `to_json()` first, so Paths and pydantic objects become plain data.
- `update_draft` keeps returning `error(400, ...)`. A returned `Response` bypasses the response model.
  - **Source:** FastAPI's "Return a Response Directly" (fastapi.tiangolo.com/advanced/response-directly).
  - **Proven** in scratchpad `rm_probe2.py`: a 400 `JSONResponse` from a route with `response_model` comes back unchanged.

### `src/foundry/api/errors.py`: response drift is logged

- **The handler:** a new handler for `fastapi.exceptions.ResponseValidationError` logs one line to the `foundry.api` logger, with the method, path and each error's `loc` and `type` (never the input values, which may be run text). It returns 500 `{"error": "Internal Server Error"}`.
- **Why the guard's `except Exception` doesn't hide it:** the handler runs inside FastAPI's exception middleware, before the guard ever sees the exception.
- **Proven** in `rm_probe2.py`: the handler sees `('response', 'b') extra_forbidden`.
- **The test:** `test_response_drift_is_a_500` also asserts the log line (via `caplog`).

### `src/foundry/services/batches.py`

- Adds `REPEATS = (1, 5)`, `CONCURRENCY = (1, 4)` and `MAX_COST = 50.0`.
- The existing range check uses them, and its message is generated from them.

### The data layer (R4)

- **`data/runs.read_run`:** also returns `None` when any of these is present, not `None`, and of the wrong type: `model.id`, `prompt`, `task_id`, `started_at` (str); `agents` (dict); `agents.mode` (str).
- **`data/rubrics.Store.history`:** also skips lines whose `rubric_id` or `version` isn't a str; whose `activated_at`, `note` or `draft_id` isn't a str or `None`; or whose `source` isn't `None` or a dict of str-or-`None` values. A missing `activated_at` becomes `None`. `RubricVersionRow` then holds for every line kept (design pass 3).
- **`data/runs.num`:** also returns `None` for NaN and infinity (design pass 3). So a `NaN` score is "not scored" rather than a 500, which was already a 500 before this increment.
- **`services/runs.card_extras`:** reads `workflow.cost_usd` through `data.runs.num()`, so a bool is not a cost.
- **`data/runs.read_scores`:** also normalizes `output.criteria`:
  - not a list → the key is removed
  - an item that isn't a dict with a str `id` and a numeric (non-bool) `score` → dropped
  - an item whose `max` isn't numeric → that `max` is removed
  - `name` or `rationale` that aren't strings → removed
- **`data/batches.batch_rows`:** each known column becomes a `str` (`""` when missing or `None`). The known columns are `variant`, `model`, `prompt`, `repeat`, `status`, `model_calls`, `tool_calls`, `tool_errors`, `overall`, `workflow`, `output` and `run_dir`. This also removes a latent `KeyError` in `variant_stats`.
- **`data/batches.read_meta`:** drops `status` (str), `jobs` (int), `max_cost` and `spent_usd` (numbers) and `models`/`configs` (lists of str) when their type is wrong.
- **`data/batches.batch_list`:** builds on `read_meta`.

### `scripts/gen_api_types.py` (new)

```
uv run python scripts/gen_api_types.py          # writes web/src/types.ts
uv run python scripts/gen_api_types.py --check  # exit 1 if stale (CI/tests use the function directly)
```

1. **Collect the models:** every route's `response_model` and body model (from `create_app(...).routes`), plus `EXTRA = {"Column": Column}` for the type aliases the web app imports.
2. **Get the schema:** `models_json_schema([(M, "serialization") …] + [(B, "validation") …], ref_template="#/$defs/{model}")`.
   - The generator raises when a model is used both as a response and as a request body.
   - It also raises on any `$defs` key that isn't a plain identifier or contains `__`. Pydantic qualifies the names of two classes that share one (for example, the kernel's `Criterion` beside the scores `Criterion`) instead of raising itself.
3. **Emit TypeScript:**
   - each `$defs` entry becomes an `export interface Name { … }`, sorted by name
   - a property in `required` becomes `k: T`, any other `k?: T`
   - `anyOf` with null becomes `T | null`
   - `additionalProperties: true` becomes `[key: string]: unknown`
   - a typed `additionalProperties` becomes `Record<string, T>` for a bare dict
   - `prefixItems` become `[A, B]`
   - `const`/`enum` become JSON literals (`"ok"`, `true`), so `Ok.ok` is `true`, not `"true"`
   - `{}` becomes `unknown`
4. **Write the file:** the header reads `// GENERATED by scripts/gen_api_types.py from src/foundry/api/schemas.py — do not edit.` After it comes an `ApiRoutes` interface mapping `"GET /api/runs"` to its type: documentation, and the T3 test's source of truth.
5. **The function:** `render() -> str` is what the test calls. `main()` writes the file.

### `web/src`

- `types.ts` is replaced by the generated file.
- Call sites switch to the generated names:
  - `Import.tsx`: the local `Receiver` becomes `ReceiverStatus`.
  - `Dashboard.tsx`: `D` becomes `Dashboard`.
  - `Compare.tsx`: `CompareT` becomes `Compare`.
  - `Batches.tsx`: its local type becomes `BatchesResponse`.
  - The inline write types become `StartRunResponse`, `StartBatchResponse`, `CreatePromptResponse`, `CreateDraftResponse`, `ImportResponse` and `DraftSaveResult`.
- Fix whatever `tsc` reports where the generated types are more honest than the hand-written ones (`unknown` on open models). Each fix is a type narrowing, not a behaviour change.
- The bundle is rebuilt.

### Tests

**`tests/test_api_contract.py` (new):**

| Test | What it checks |
|---|---|
| `test_every_json_route_declares_its_response` | Walks `create_app(...).routes` and requires each `/api` APIRoute to have a `response_model` or be in `EXEMPT` (the source download, the catch-all). |
| `test_types_ts_is_current` | `render() == web/src/types.ts`; the failure message says to run `uv run python scripts/gen_api_types.py`. |
| `test_call_sites_use_the_route_type` | Scans `web/src/**/*.ts(x)`, skipping tests and `types.ts`, for calls to `usePoll`, `get`, `post`, `put`, `del` and `upload`. The rules, below this table. |
| `test_response_drift_is_a_500` | A route on a toy app with a dict that breaks its model gets 500 `{"error": "Internal Server Error"}` through the real guard. |
| `test_hostile_files_never_500` | Seeds hostile `run.json` variants, scores with junk criteria, a `summary.csv` missing columns and a `batch.json` with wrong types, then hits every GET route: all 200, the bad run listed as unreadable. |
| `test_unknown_body_field_is_422` | For each JSON body route. |
| `test_batch_ranges_are_422` | Names the field, using the limits from `services.batches`. |

**The call-site rules** (`test_call_sites_use_the_route_type`):
1. **A literal `/api` URL** (string or template; `${…}` matches one path segment; the query is stripped) is matched against the routes in app order, first match wins, as FastAPI does. That puts `/api/prompts/tools` ahead of `/api/prompts/{prompt_id}`.
2. **Named type parameter:** it must equal the route's model name.
3. **Inline object type** (`<{ … }>`): the test fails. Use the generated name.
4. **No type parameter:**
   - `usePoll` and `get` fail; a read must say what it reads.
   - `post`, `put` and `del` pass, since `T` is `unknown` and the result can't be used without a type.
5. **A ternary of literals** (`cond ? "/api/a" : null`, `cond ? "/api/a" : "/api/b"`): every `/api` literal in the first argument is checked as in rules 1–2.
   - That covers `Compare.tsx:15`, `Runs.tsx:96-97` and `Import.tsx:48`.
6. **A variable URL** with a type parameter is allowed only in files listed in `DYNAMIC`, with the types each may name. Today that is only `RubricVersion.tsx` (`RubricWithMeta | RubricVersion`, since one page shows three routes). A new one fails until it is listed. A variable URL without a type parameter (`Map.get(k)`, `params.get(x)`) isn't an API call.
7. **Plumbing is skipped:** the helpers' own generic calls in `api.ts` and `usePoll.ts` (`get<T>(url)`).
8. **The test tests itself:** `test_call_site_check_catches_each_mistake` feeds one snippet per mistake and asserts it's reported:
   - an inline type
   - an untyped read
   - a wrong type
   - a wrong ternary branch
   - the first-match rule (`/api/prompts/tools` named as `PromptDetail`)
   - an unlisted variable
   - an unknown route

   It also feeds correct snippets and asserts each passes.
9. **Not vacuous:** at least 15 call sites must be found.
10. **Literals** may use double quotes, single quotes or backticks. A typed call with no `/api` literal is a variable or computed URL (rule 6): a variable, a function call, a concatenation, `${BASE}/api/...`.
11. **Known limits,** accepted. The checker reads call sites; it isn't a type checker:
    - a local interface that shadows a generated name
    - `as {…}` casts on an untyped write
    - single-quoted `import type` aliases
    - the variable branch of `cond ? "/api/x" : someVar`

**Call sites that change** (each is a type annotation only, no behaviour change):

| Site | Before | After |
|---|---|---|
| `Import.tsx` | local `Receiver` | `ReceiverStatus`; the local type is removed |
| `Dashboard.tsx`, `Compare.tsx`, `RubricVersion.tsx` | `import type { Dashboard as D }` and friends (the page component has the type's name) | kept; the checker resolves `import type { X as Y }` |
| `Batches.tsx` | local or inline list type | `BatchesResponse` |
| `toasts.tsx:27` | `get<{ jobs: Job[] }>` | `get<JobsResponse>` |
| `Runs.tsx:96-97` | `usePoll<{ rubric: Rubric; … }>` | `usePoll<RubricWithMeta>` |
| `RubricVersion.tsx` | `usePoll<{ rubric; active; sha256? }>(url)` | `usePoll<RubricWithMeta \| RubricVersion>(url)` |
| `Shell.tsx` | `post<{ run_id }>` | `post<StartRunResponse>` |
| `Batches.tsx` | `post<{ batch_id }>` | `post<StartBatchResponse>` |
| `PromptEdit.tsx` ×2 | `post<{ prompt_id }>` | `post<CreatePromptResponse>` |
| `RubricDraft.tsx` | `put<{ valid; … }>` | `put<DraftSaveResult>` |
| `Rubric.tsx` | `upload<{ draft_id }>` | `upload<CreateDraftResponse>` |
| `Import.tsx` | `upload<{ runs }>` | `upload<ImportResponse>` |

**`test_api_ports.py::test_batch_validation`:** `max_cost: 500` and `concurrency: 40` now expect 422. These are the only edits to existing assertions.

### Docs

`docs/architecture.md` gains an "API contract" section (D1). The handoff records the proof.

## Gate answers (design pass 1)

- **Q2 messages and order:** 422 messages name the body field (`max_cost: Input should be less than or equal to 50`), like every other 422 since increment 9. The batch form's own `min` and `max` already keep these values from being sent.
  - A 422 now fires before the config and prompt 400s. That's accepted: the form can't produce it, and scripts get a precise message.
- **`DraftMeta`:** accepted, as the table above says.
- **Duplicate names:** the generator raises (step 2).

## AC → component map

| AC | Component | Proof |
|---|---|---|
| R1 | `schemas.py`, `routes.py` | `test_every_json_route_declares_its_response` |
| R2 | `Closed`/`Open`, FastAPI response validation, the logging handler | `test_response_drift_is_a_500` (status, body, log line), plus every route test |
| R3 | `exclude_unset` wrapper | old-versus-new JSON diff on real `runs/` and fixtures (Verify) |
| R4 | data readers | `test_hostile_files_never_500`, plus unit tests on the readers |
| T1 | generator, `types.ts`, web call sites | `tsc`, Vitest, both e2e |
| T2 | `render()` | `test_types_ts_is_current` |
| T3 | `ApiRoutes`, call sites | `test_call_sites_use_the_route_type` |
| Q1 | `Closed` request bodies | `test_unknown_body_field_is_422` |
| Q2 | `BatchBody` plus constants | `test_batch_ranges_are_422`, the edited `test_batch_validation` |
| U1 | — | full suite, Vitest, both e2e |
| D1 | `docs/architecture.md` | read at Verify |

## Failure modes, security, rollback

- **A missed optional key** returns a 500 on that route, logged with its field. Mitigation: models from the writers (table above), checked against the captured real responses; route tests on fixtures; the R3 diff on all 65 real runs before shipping.
- **Hostile files** are R4. The guard's 500 body never leaks details (unchanged).
- **Rollback:** revert the commits. No data or file format changes, and the readers only become stricter in what they accept for display.
- **Performance:** response validation adds some milliseconds on the largest payloads (run detail, about 50 KB). It's measured at Verify, and the budget is under 20 ms added per request on the real largest run.

## One-way doors

None. Everything is reversible by a revert.
