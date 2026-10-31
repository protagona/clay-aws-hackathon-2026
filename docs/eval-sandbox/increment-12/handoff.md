# Increment 12: every screen explains itself — Handoff

**Design (with the acceptance criteria and non-goals):** ./design.md   **Date:** 2026-10-31   **Track:** Standard

## What shipped

- **Page header and sections:** every page opens with a title and a one-line caption. Content is split into numbered sections (`01`, `02`…), each with a heading, a one-line caption and a thin rule, and charts and tables sit in panels under them. This is direction B, which the user picked and approved: `mockups/directions.html`, `mockups/refined-b.html`.
- **Shared components:** `PageHeader`, `Section` and `SubSection` in `web/src/components/ui.tsx`, used by every page.
- **Run panel:** the Overview is split into captioned blocks: Scores, Limits used, Agents (pipeline runs only) and Latest steps. Every tab starts with a caption, and an unscored run offers **Score this run** right on the Overview.
- **Plain words everywhere.** For example:
  - "W 1.00 · O 0.67" became "Workflow 1.00 · Output 0.67"
  - Evaluating became Being scored
  - Live now became In progress
  - Mean, Min, Max, Stdev became Average, Lowest, Highest, Spread
  - "Δ (B − A)" became Difference
  - llm_call became model call
  - "ms" became seconds
  - Prices from became Price list (config)

  The full glossary is in the design.
- **Compare fix:** a cost increase used to show in green. The API's compare rows now say which direction is better (`better`), so a higher cost or longer duration shows red, and a better score shows green.
- **Dashboard:**
  - A "Best model" card (from the approved mockup).
  - A header row for the model ranking.
  - A caption under each struggle list.
  - The rubric selector stays visible even when no runs match it.
  - Axis titles on the chart.
- **Batch page:**
  - Scores show two decimals.
  - One Model column replaces "Variant" plus "Model".
  - Tool calls read "10 (1 failed)".
  - The caption reads like "24 runs across 4 models, all finished. $5.76 of the $10.10 cap spent."

## Decisions

- **The user approved:** direction B (sections with dividers), then the refined mockup with the renamed statuses and the Compare color fix.
- **The design gate ran 3 passes, the maximum.** The third pass's two blockers went to the user with proposed fixes, and the user approved them:
  - the server's card words are translated ("not evaluated" becomes "not scored yet", and so on)
  - the e2e now fails on sideways scroll on every page
- **Additions to the approved mockup, declared in the design:**
  - the batch Repeat column
  - the "Servers" line in Limits used
  - "red means worse" in Compare's caption
  - the Dashboard summary caption, which says "selected rubric" because the select can change it
- **Labels only, never ids:** the API's `evaluating` column, the CLI's `evaluate`, and file names are unchanged (ADR-2).
- **Compare row label:** "Tool calls (errors)" became "Tool calls (failed)" in both UIs.

## Verification

**Suites:**
- Python: 323 passed.
- Vitest: 17 passed.
- ruff and `tsc` are clean, and gitleaks found no leaks.
- The bundle is 94.81 KB gzipped, up 1.4 KB from 93.38 KB; the budget was 5 KB.

| AC | How it was proven |
|---|---|
| C1 Page header | **e2e `clarity` stage** (`scripts/ui_e2e.mjs`, live server, headless Chromium). Every page, at 1440 and 375 px and in light and dark, has exactly one page `h1`, ignoring the judge report's own heading, and a caption. The pages checked:<br>• `/`, `/batches`, `/batches/<newest>`, `/dashboard`<br>• `/rubric`, `/rubric/active`, `/rubric/runs/<id>`, `/rubric/drafts/<id>` (a temporary JSON draft, discarded afterwards)<br>• `/import`, `/compare` (empty and filled), and not found<br>The e2e creates its own rubric draft by uploading the active rubric as JSON (no model call) and discards it afterwards. `/rubric/history/<file>` was skipped: there's no history yet. |
| C2 Sections | The same e2e stage: every `main section[aria-labelledby]` has an `h2` and a caption. Vitest checks that `Section` renders a labelled region with its `02` number, an `h2` and a `data-caption`. |
| C3 Run panel | Vitest "run panel score states": **Being scored…** (spinner, no button), **Scoring was interrupted.** with Score this run, and **Partly scored** for a run whose overall is null. e2e: before scoring, the new run's card button reads "Score it →", the open panel offers **Score this run**, and the board and panel pass the banned-word scan. Every run-panel tab (Overview, Timeline, Steps, Report and Rubric) shows a caption, at both widths and in both themes. Vitest checks:<br>• the Overview groups Scores, Limits used and Latest steps<br>• an unscored run shows "Not scored yet." with **Score this run**, which calls evaluate<br>• the tool-call total reads "3 tool calls" |
| C4 Board | Vitest Board tests: the column caption "The judge is grading the result.", the empty column's "None right now", the "Being scored" region, "Workflow — · Output —" and the "Score it" button. |
| C5 Plain words | e2e banned-word scan, case-insensitive, on every page, tab and the New run dialog, at both widths and in both themes. It covers 21 shorthand patterns plus exact-text table headers (`ms`, `stdev`, `mean`, `min`, `max`, `variant`, `#`, `δ (b − a)`). What agents and the judge wrote (the report, model replies, rubric text, prompt text) is left out. Vitest covers `stepKind`, `statusWord`, `pretty` ("data-fit" becomes "Data fit", and pipe lists become "a, b or c"), `seconds` and `score2`. |
| C6 Compare colors | pytest `test_compare_rows_say_which_way_is_better`:<br>• Overall, Workflow and Output are "higher"<br>• Cost and Duration are "lower"<br>• Model calls and Tool calls are null<br>• every indented row, including the criterion `data-fit`, is "higher"<br>Vitest `deltaTone`: "+$0.172" is bad when lower is better, "−0.03" is bad when higher is better, and "0" or a null direction is grey. |
| C7 Numbers | Vitest `score2("0.9584")` gives "0.96" and `seconds(1918)` gives "1.9 s". The e2e's exact-header scan finds no "ms" header. Screenshot: the batch table shows 0.96, 0.92 and so on ([batch-dark](screenshots/batch-dark.png)). |
| C8 Nothing breaks | The full e2e passes: an offline run is started, scored, and its Rubric tab checked, with no reload. A sideways page scroll now **fails** the e2e (it used to only warn); none occurred on any page, at either width or in either theme. Every existing test passes, with only label updates (`ui.test.tsx`, `test_ui.py`'s compare label). |

**Compared with the approved mockups** (screenshots in [`screenshots/`](screenshots/)):
- The board, run panel, batch, Compare and Dashboard match direction B: page title and caption, numbered sections with rules, captions on the board columns, run-panel sub-sections, and the "Workflow · Output" cards.
- **Differences:** the declared additions listed under Decisions, and the Dashboard chart's y-axis title, which is a text line ("↑ Overall score") above the plot instead of rotated text.

**Exercised live:** yes. The real `foundry ui` served the built bundle, and the e2e drove it in headless Chromium against the user's 78 real runs, including a fresh offline run started and scored through the UI.

## Verify review

The independent pass re-ran every suite and the e2e itself: all passed, with no blocking issues. It also confirmed the live `/api/compare` rows carry `better` correctly.

It pointed out thin spots, now fixed:
- **Unscored run-panel states had no tests.** There are now three Vitest cases, plus an e2e scan of an unscored run.
- **The draft page wasn't reproducible.** The e2e now creates and discards its own draft.
- **Old wording in states the scan never reaches:**
  - "already being evaluated" is now "already being scored" (the lock message, also seen in the CLI)
  - "not evaluated yet" is now "not scored yet" on a run's rubric page, which also gained Retry
  - "evaluate it from the CLI" now reads "score it from the CLI: foundry evaluate runs/<id> --force"
- **The step feed** said "model" and showed "ms". It now says "model call" and shows seconds, matching Compare.
- **`pretty()`** only reshapes hyphenated ids now, so snake_case tool names stay as written.
- **The scan now covers the rubric view's own labels.** Only the rubric's own text is left out.
- **The batch status** goes through `statusWord`.
- **The duplicate import** in `main.tsx` is gone.

Accepted as they are:
- **Short inline hints instead of the design's full sentences.** These are Compare's "· the baseline", Batches' "runs side by side" and "cost cap", and Import's "· to work out cost; none skips it". They're shorter versions of the design's wording, so I'm recording them as deviations rather than changing them.
- **The run panel's title is still a `<div>`, not a heading.** This predates the increment and is a follow-up.

## Deferred and follow-ups

- **The rubric history page** wasn't visited by the e2e, because no history exists yet. It uses the same `PageHeader` and `Section` as the active and run-copy views, which were checked.
- **Each e2e run adds one offline "scripted-a" run** to the real board, as before.
