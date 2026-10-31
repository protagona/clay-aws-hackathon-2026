# Increment 10: see any rubric's contents in the UI

**Track:** Quick   **Status:** Built (verified 2026-10-31)   **Date:** 2026-10-31

## Problem

Three kinds of rubric can't be read in the UI:
- **The active rubric** shows only its criterion ids until it has a history entry, which happens only after the first activation.
- **The rubric that scored a run** is copied into the run directory as `rubric.json`, but the run drawer shows only its id and version.
- **Rubrics in the dashboard's "Scored with" filter** can't be opened.

Drafts and history versions already have full views.

## Acceptance criteria

- **R1:** `GET /api/rubric/active?task=` returns `{rubric, sha256, file}`. `file` is the history file with that sha, or null. The Rubric tab's active rubric always links to a full view at `/rubric/active`.
- **R2:** `GET /api/runs/{id}/rubric` returns the rubric copied into that run (`{rubric, sha256, active, file}`), read with the same untrusted-file checks as the rest of the run: a regular file only, validated by `load_rubric`. It returns 404 when the run is unknown, has no rubric copy, or the copy is invalid.
- **R3:** The run drawer gains a **Rubric** tab with the rubric that scored the run: every criterion with its max, weight, description and every level. For a scored run, each criterion also shows the judge's score and rationale (from `scores.output.criteria`), and the level the run reached is highlighted. An unscored run says "Not evaluated yet: this run will be scored with the active rubric", and shows the active rubric.
- **R4:** The dashboard's "Scored with" filter gets a "View rubric" link for the selected rubric. It opens `/rubric/runs/<a run scored with it>`, a full view of that run's rubric copy, so even rubrics never in the history are viewable.
- **R5:** One shared `RubricView` component renders every rubric view (history versions, active, a run's rubric, the drawer tab), so they look the same. Rubric text is escaped, with no HTML sinks.
- **R6:** Tests:
  - API: R1, R2, the 404s, and a symlinked or invalid run `rubric.json` refused
  - Vitest: `RubricView` renders levels and highlights the scored level
  - e2e: open a scored run's Rubric tab and check its criteria and scores

## Non-goals

- No editing here; editing stays in draft review.
- No diffing of two rubrics (a possible follow-up).

## Plan

1. API: `/api/rubric/active` and `/api/runs/{id}/rubric` in `api.py`, using `actions` helpers.
2. UI: `components/RubricView.tsx`; the drawer's Rubric tab; the `/rubric/active` and `/rubric/runs/:id` routes (reusing `RubricVersion` with a different data URL); the links on the Rubric tab and the dashboard.
3. Tests, build, e2e, screenshots, docs, then commit.

## Verification (2026-10-31)

**Suites:** Python 321 passed; Vitest 9 passed; ruff and gitleaks clean.

| AC | How it was proven |
|---|---|
| R1, R2 | `test_api.py::test_rubric_contents_viewable` covers:<br>• the active rubric in full (sha, no history file yet)<br>• a run's own copy, matching the active sha<br>• 404 for an unevaluated run, a symlinked copy, an unknown or invalid run id, an invalid copy and an unknown task |
| R3 | **e2e** (`scripts/ui_e2e.mjs`, live server, headless Chromium): after the new run is scored, the drawer's **Rubric** tab shows its 4 criteria, each with "scored n/max". Screenshot: [drawer-rubric-tab](increment-10-screenshots/drawer-rubric-tab.jpg). An unscored run's tab shows the active rubric with a "Not evaluated yet" note. |
| R4 | `scored_by` in `/api/dashboard` is asserted in the same API test. The dashboard shows "View rubric →" for the active rubric or a selected sha. |
| R5 | Vitest `RubricView`: every level listed, the reached level marked with `aria-current`, the judge's score and rationale shown, and description text rendered as text, not HTML. The history, active and run-copy pages and the drawer tab all use `RubricView`. |

**Self-review (Quick):**
- Every AC has a test or live proof.
- No new write paths: both endpoints are GET-only, and read untrusted files through the existing checks (a regular file only, validated by `load_rubric`).
- No HTML sinks were added.
- Out of scope, noted as a follow-up: diffing two rubrics.
