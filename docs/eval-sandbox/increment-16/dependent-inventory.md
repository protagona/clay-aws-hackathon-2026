# Removing the classic UI (`/classic`) — Dependent Inventory and Removal Plan

## Why

The server-rendered UI from increments 1–8 duplicates every screen of the TypeScript UI (increment 9 onward). Keeping both in step is why `ui/app.py` mixes three layers. The user decided on 2026-10-31 to remove it. **Replacement:** the TypeScript UI at `/`, which has had full parity since increment 9 and is ahead of it since increment 12.

## Dependent inventory

| Dependent | Type | Migration path | Status |
|---|---|---|---|
| `/classic/*` routes in `ui/app.py` (about 40 handlers) | code/API | Same screens at `/`; the same data at `/api/*` | remove |
| `ui/templates/*.html` (15 files) | code | none needed | remove |
| `ui/static/monitor.js` and the `/classic/static` route | code | the SPA's live polling | remove |
| The Jinja `markdown` filter and `FEED_TEXT_MAX` | code | `render()` stays, used by `/api/runs/{id}` (`text_html`, `report_html`) | remove the filter; keep `render` in `api/` |
| `Job.failed_visibly`, `last_stderr_line` (classic banners) | code | The SPA's toasts use `/api/jobs` `failed` and `message`. Whichever of these the API still uses survives in `services/jobs.py` (checked in Build). | move or remove |
| `_draft_from_form` (classic rubric review form) | code | The SPA posts JSON (`PUT /api/rubric/drafts/{id}`) | remove |
| `conftest.classic_client` | test | API clients | remove |
| `tests/test_ui.py` (42 tests) | test | per-test mapping below | port or drop |
| `tests/test_dashboard.py` HTML assertions | test | port to `GET /api/dashboard`; the pure `dashboard.build` unit tests stay | port |
| `README.md` "Classic UI at `/classic`" | doc | delete the line | update |
| `docs/eval-sandbox/increment-*/` mentions | doc | historical records; left as they are | none |
| User bookmarks of `/classic/...` | user | `/classic/*` now falls through to the SPA, which shows "Page not found" with a link to Runs | accepted |
| Data | data | The classic UI owns no data; runs and rubrics are shared and untouched | none |

**`tests/test_ui.py`, test by test.** "Covered" names the `/api` test that already proves the same check. "Port" means a new or extended `/api` or unit test is written before deletion. "Drop" covers checks of classic HTML only.

| Classic test | Ruling |
|---|---|
| `binds_localhost_only` | Covered by `test_origin_and_host_checked_on_every_write` (Host check). The bind address is the CLI's (`127.0.0.1`): **port** a CLI unit assertion. |
| `empty_state`, `cards_states_and_order` | Covered by `test_runs_kpis_and_columns` (columns and order from the API). |
| `report_page_and_unevaluated_page` | Covered by `test_run_detail_and_404`. |
| `bad_run_ids_404`, `links_use_directory_name_not_run_json_run_id`, `malformed_files_do_not_break_the_index`, `symlinked_files_and_dirs_are_never_followed`, `untrusted_content_rendered_inert`, `feed_renders_model_markdown_safely`, `live_fragment_renders_untrusted_content_inert` | Covered by `test_must_deny_paths`, `test_must_deny_paths_continued` and `test_must_deny_paths_classic_parity` (ported in increment 9). |
| `host_origin_csrf_checks`, `origin_checked_on_evaluate_post`, `browser_form_post_is_not_refused_as_cross_origin` | Covered by `test_writes_need_csrf_header_and_json` and `test_origin_and_host_checked_on_every_write`. The form-post case was classic-only: **drop**. |
| `start_run_validation`, `start_run_then_409_then_card`, `evaluate_from_ui_holds_and_releases_lock`, `evaluate_refused_for_running_run` | Covered by `test_start_run_and_evaluate_through_api`, `test_second_run_while_one_is_running_is_409` and `test_must_deny_paths_continued`. The lock release after evaluation: **port** an assertion to the API run-and-evaluate test. |
| `failed_job_banner`, `ordinary_failed_run_has_no_banner` | **Port:** `/api/jobs` reports exit 2 as `failed` with the stderr message, and an exit-1 run as not failed. |
| `ui_pages_never_show_credentials` | **Port:** `/api/session`, `/api/runs/{id}` and the job messages never contain the AWS keys from the environment. |
| `security_headers_and_framing`, `csp_and_static_allowlist` | Headers covered by `test_csp_and_bundle`; **port** a `X-Frame-Options: DENY` and `Referrer-Policy` assertion there. The static allowlist was classic-only: **drop**. |
| `render_blocks_protocol_relative_links` | **Port** as a unit test of `render()`. |
| `auto_refresh_only_while_in_progress`, `nav_on_every_page`, `live_fragment_and_script_only_while_live`, `live_fragment_bad_ids_404`, `run_page_compare_form` | Classic HTML only: **drop**. The SPA polling cadence is covered by Vitest. |
| `batch_from_the_ui`, `batch_form_validation` | Covered by `test_compare_dashboard_batches_receiver`, with the batch start; **port** the validation messages to `/api/batches` 400s. |
| `import_upload_from_the_ui`, `receiver_start_receive_stop_from_the_ui` | Covered by `test_compare_dashboard_batches_receiver` (import and receiver). |
| `compare_page_rows_and_timelines`, `compare_unevaluated_run_shows_dashes`, `compare_bad_ids_404`, `compare_hostile_scores_do_not_break_the_page` | Rows are covered by `test_compare_rows_say_which_way_is_better`, and bad ids by `test_must_deny_paths`. **Port:** the dashes for an unevaluated run, and hostile scores not breaking `/api/compare`. |
| `pipeline_run_page_compare_and_cards` | **Port:** `/api/runs/{id}` for a pipeline run shows its agents, and the card shows `agents_mode`. |
| `rubric_import_review_activate_rollback`, `rubric_routes_deny`, `malformed_model_drafts_open_and_can_be_discarded` | Covered by `test_rubric_flow_through_api` and `test_must_deny_paths`. **Port:** a malformed model draft opens through `/api` and can be discarded. |
| `run_page_names_the_rubric_that_scored_it` | **Port:** `rubric_info` in `/api/runs/{id}`. |

## Removal sequence

1. Port every "Port" row to `/api` or unit tests, and see them pass against the current code, before anything is removed.
2. Remove the routes, templates, static file and Jinja dependency in the same change as the layer move.
3. Grep for `classic`, `TEMPLATES`, `/classic`, `jinja` and `templates/` in `src/` and `tests/`, and expect nothing.

Staged deprecation (warn, then flag, then remove) isn't used: this is a single-user local tool, the user decided on removal, and a revert restores it.

## Migration guide

Use `http://127.0.0.1:8765/`, the same pages under the new menu. For scripts, use `/api/*`, the same data as JSON.

## Retention obligations

None. The classic UI stores nothing. Runs, batches, rubrics and prompts are shared files and are untouched.

## Verification before removal

- `grep -rn "classic" src tests` returns nothing outside comments that explain the history.
- The full suite passes, including the ported tests.
- Both e2e scripts pass.

## Sunset gate record

**Pass 1: all findings resolved. This section is authoritative where it differs from the tables above.**

**Ports, now written and passing against today's code.** `tests/test_api_ports.py` holds 30 tests. `tests/test_dashboard.py` page tests now go through `/api/dashboard`.

| Classic check | Now proven by |
|---|---|
| Batch, import and receiver end to end, which were wrongly marked "covered" | `test_batch_runs_end_to_end`: batch run naming `{batch_id}_*`, and batch runs listed as ordinary cards. `test_import_a_trace_end_to_end`. `test_receiver_binds_localhost_and_receives`, which **asserts the 127.0.0.1 bind** and the receive-to-board path. |
| Symlinked run directories in the list | `test_symlinked_run_dirs_never_listed` (`/api/runs` and `/api/dashboard`) |
| Malformed `scores.json` | `test_malformed_scores_do_not_break_the_board` |
| Card order and the "incomplete" headline | `test_cards_order_and_headlines` |
| Escaped text and Markdown kept, not only stripped; an unscored hostile run | `test_run_text_is_inert_but_still_markdown` |
| A wrong port, `localhost` allowed, and the Host check on SPA paths | `test_host_checks_on_every_path` |
| Config traversal | `test_start_run_rejects_config_traversal` |
| Compare's five bad-id cases | `test_compare_bad_ids_404` (parametrised) |
| The double-click 409 while scoring | `test_evaluate_holds_and_releases_the_lock` |
| Job log and dismiss | `test_failed_start_is_reported_then_dismissed` (`GET /api/jobs/{id}/log`, `POST /api/jobs/{id}/dismiss`) |
| Headers, including `nosniff`, and on a 404 | `test_security_headers_and_framing` |
| `run_page_names_the_rubric_that_scored_it` | Also covered by `test_run_detail_and_404`, and ported anyway |
| Dashboard: `test_filter_by_rubric`, `test_empty_and_hostile_runs`, `test_empty_state_right_after_an_activation`, page from real runs | Rewritten in `tests/test_dashboard.py` against `/api/dashboard`: KPIs, `rubric_options`, `mixed`, the junk-`rubric` fallback, `total_runs` |
| Dashboard: `test_model_filter_on_runs_tab` | **Dropped:** filtering happens on the client (Vitest "key numbers follow the model filter") |

**Found while porting:** `/api/receiver` reported the *configured* port instead of the one it bound (port 0 means an OS-assigned port). It now reports the bound port, as the classic page did. Fixed in `ui/api.py` and proven by the receiver port test.

**Dependents that survive the move (never deleted with the classic router):**
- the `guard` middleware: the Host and Origin checks, CSP, nosniff, Referrer-Policy and X-Frame-Options
- `Job` with `failed_visibly`, `last_stderr_line` and `dismissed` (**kept**: `/api/jobs` uses all three)
- `UiState`, `_spawn`, `save_upload` and `read_rubric_upload`
- `JOB_ID_RE`, `BATCH_ID_RE` and `CONFIG_STEM_RE`
- the 19 `_core()` helpers
- `create_app`'s callers: `cli.py`, every test, and `test_tools.py`'s patch of `_spawn`

Each one's new home is in the design's gate record.

**Classic code outside `app.py`, also removed:**
- In `ui/api.py` `mount_spa`, the `rest == "classic"` 307 redirect is deleted, so `/classic…` falls through to the SPA.
- The 503 "UI bundle not built … or use the classic UI" page now reads "The UI bundle isn't built. Run `cd web && npm ci && npm run build`." `web_dist` is committed, and CI's `web` job checks that it matches a fresh build.

**Tests that change beyond their imports (B1 reworded in the spec):**
- `test_api.py::test_spa_served_and_fallback`: the `/classic` assertions become "`/classic/` serves the SPA's index".
- `test_api.py::test_must_deny_paths_classic_parity` is renamed `test_must_deny_paths`, keeping its body.

**The verification command, exact, with its exclusions.** It must print nothing:

```
grep -rnE "classic|TEMPLATES|Jinja2Templates|templates/[a-z_]+\.html|monitor\.js" src tests scripts web/src README.md \
  | grep -vE "^(docs/|src/foundry/ui/web_dist/)" | grep -viE "#.*(classic|increment)|// .*classic|was the classic|classic\) was dropped"
```

- Plus `grep -n jinja2 pyproject.toml uv.lock` must print nothing (`jinja2` is removed from the dependencies).
- `src/foundry/templates/` (the judge and summarizer prompts) isn't matched: the pattern matches `templates/<name>.html` only, and those prompts are `.txt`.

**Pass 1 questions:**
- **`render`:** services return Markdown, and the API renders it (design gate record, item 1).
- **The 503 page:** it gives only the build command (above).
- **Migration notice for bookmarks:** the README's "Classic UI" line is replaced by one sentence: "The classic UI (`/classic`) was removed in increment 16; use `/`." "Page not found" on an old bookmark is accepted.

**Pass 2: all blockers resolved. These replace the corresponding pass 1 items.**
1. **No name collision:** `test_must_deny_paths_classic_parity` becomes `test_must_deny_paths_parity`. `test_must_deny_paths` already exists at `test_api.py:229`, and a duplicate name would silently shadow it. Ruff's F811 check is in the verify run.
2. **The verification is a test, not a grep.** `tests/test_architecture.py::test_no_classic_ui_left` scans `src/`, `tests/`, `scripts/`, `web/src/` and `README.md` (text files only, skipping `__pycache__` and `web_dist`) for `classic`, `Jinja2Templates`, `TEMPLATES`, `monitor.js`, `/classic`, `jinja2` and `markupsafe`. It fails on any hit not in an explicit allowlist of `(path, exact substring)` pairs, each with a reason:
   - README's removal sentence
   - `test_api.py::test_spa_served_and_fallback`'s `/classic` fall-through assertions
   - `test_api_ports.py`'s docstring
   - `test_dashboard.py`'s two history comments
   - this test's own pattern list

   An allowlisted pair that no longer matches anything also fails, so the list can't go stale. This replaces both grep commands, and the comment-line exclusion is gone. `grep -n "jinja2\|markupsafe" pyproject.toml uv.lock` must print nothing (`jinja2` is a direct dependency only).
3. **The fall-through is tested fully:** `test_spa_served_and_fallback` asserts that `/classic`, `/classic/` and `/classic/runs/x` each return 200 with the SPA index (no 307).

**Pass 2 questions, decided:**
- **The static allowlist checks move to `/assets`,** not dropped: `test_spa_served_and_fallback` adds `/assets/..%2Fapi.py`, `/assets/%2e%2e/%2e%2e/pyproject.toml` and a null byte, each refused with 404 and never a file's contents.
- **The removal is its own commit (slice 5b),** after the layer move (5a), so it can be reverted alone.
- **Stale security docstrings:** `monitor.py` (now `services/run_view.py`), `actions.py` and `api.py` say untrusted text is "left to Jinja's autoescaping". They're rewritten to state the real rule: services return raw text; only `api/render.py` produces HTML (`text_html`, `report_html`); the SPA renders everything else as text.
- **`markupsafe`** leaves with `jinja2`; it's imported only by the classic template filter.
- **Migration guide, addition:** scripts that posted classic forms must call `/api/*` with the `x-csrf-token` header (from `GET /api/session`) and a JSON body (multipart for uploads).
- **A same-origin write being accepted** is covered: both e2e scripts make real browser writes (New run, prompts, rubric draft, steps), and `test_api.py` sends writes without an `Origin` header. The client-side model filter is covered by Vitest ("key numbers follow the model filter").

**Pass 3 (the last allowed pass): two blockers, taken to the user with fixes. The user approved them (2026-10-31): "Yes, build with these fixes".**
1. **`/assets` with a null byte** (`/assets/%00`, `/assets/a%00b`) returns 500 today, because `Path.resolve` raises `ValueError`. **Fix in slice 4:** the assets handler returns a plain 404. This is the second allowed behaviour change (spec B1), and `test_spa_served_and_fallback` asserts it.
2. **Living docs, rewritten in slice 4:**
   - `docs/security/threat-model.md`: "Jinja autoescape" becomes the real rule: services return raw text, only `api/render.py` makes HTML, and the SPA renders the rest as text. Its coverage points at `test_run_text_is_inert_but_still_markdown`, `test_must_deny_paths`, `test_must_deny_paths_parity` and `test_security_headers_and_framing`, replacing the deleted `test_ui.py` tests.
   - `README.md`: line 405 ("FastAPI and Jinja2, server-rendered, no JavaScript build") becomes "FastAPI JSON API serving a prebuilt TypeScript app", and line 68 gets the removal sentence.

**Pass 3 questions, decided:**
- **`test_no_classic_ui_left`** is **case-insensitive, on whole words.** Its patterns are `\bclassic\b`, `\bjinja2?\b`, `\bmarkupsafe\b`, `monitor\.js` and `Jinja2Templates`. `TEMPLATES` is dropped as a pattern, because it would match the prompt-template package. It **lands in slice 4,** together with the rewording of every hit outside its allowlist:
  - the docstrings in `ui/actions.py`, `ui/api.py` and `ui/monitor.py`
  - the comments in `test_api.py` and `test_api_ports.py`
  
  Rewording comments is allowed under B1, since it changes no assertion.
- **Duplicate test names:** `test_architecture.py::test_no_duplicate_test_names` uses `ast` over `tests/` and fails on two top-level `def test_x` with the same name in one module, so the F811 risk is caught by the suite itself, not by a manual ruff run.
