# Increment 12: every screen explains itself — Design

**Spec:** none (see below)   **Last updated:** 2026-10-31   **Status:** Current (built and verified 2026-10-31)   **Track:** Standard

Skipping spec.md: this change stays inside the existing design language (copy, captions, section structure), so the problem, acceptance criteria and non-goals are in this file.

## Problem

Someone seeing the UI for the first time can't tell what most sections are for or what their numbers mean. Evidence from a pass over all 14 screens on the live server (2026-10-31, screenshots in the session scratchpad):

- **No page explains itself.** The Runs board has no title at all. The Dashboard, Batches and Compare pages have a bare title and no line saying what the page is for.
- **Shorthand an observer can't decode:**
  - run cards: "W 1.00 · O 0.67"
  - KPIs: "Mean overall", "Live now"
  - batch table: "Stdev", "Variant" next to "Model", "Calls 4 / 12", "(1 ×)"
  - Compare: "Δ (B − A)", "llm_call", "agent_step", "ms"
  - rubric chips: "data-fit · 1–4 · ×1"
  - Import: "Prices from"
- **Sections run together:**
  - The run panel's Overview stacks rings, gauges and a line of "Cost · Out tokens · Tools · Servers" with no headings saying what each block shows.
  - The Timeline tab has no caption, and step numbers run into names ("2model").
- **Raw values:**
  - batch scores like 0.9584 next to 1.0
  - workflow-stage labels like `(get_dataset_details|preview_dataset|sample_dataset)`
- **A real bug:** Compare colors every positive difference green, so a run that cost $0.17 more shows a green "+$0.172".

**Who has it:** anyone watching a demo or reading the UI cold, such as hackathon judges, a teammate, or Clay's people. The builder knows what "W" means; nobody else does.

## Acceptance criteria

- **C1 Page header:** every page has one `h1` and a one-line caption under it saying what the page is for (copy in the page map below). This covers:
  - Runs, Batches, a batch, Dashboard, Rubric
  - a rubric draft, a rubric view (active, history, or a run's copy)
  - Import, Compare, and Not found
- **C2 Sections:**
  - On a page with two or more blocks of content, each block is a numbered section: the number (`01`, `02`…), an `h2` heading, a one-line caption, and a thin rule under them. The content follows, with charts and tables inside a panel.
  - A page with one block of content uses the same heading, caption and rule with no number.
  - Each section is a `<section aria-labelledby>` pointing at its `h2`.
  - **Exemptions:** the board's four status columns stay `<section aria-label>` with an `h3`, nested inside "02 Runs by status". The e2e's C2 check targets `section[aria-labelledby]` only.
  - **Kept as they are, because tests and screen readers use them:**
    - the KPI grid becomes `<div role="group" aria-label="Key numbers">` inside "01 At a glance"
    - the score rings keep `aria-label="overall .97"`, and their visible labels are separate text
- **C3 Run panel:**
  - The Overview tab is split into captioned, unnumbered sub-sections ("Scores", "Limits used", "Agents" for pipeline runs only, "Latest steps").
  - The Timeline, Steps, Report and Rubric tabs each start with a one-line caption.
  - An unscored, finished run's Overview shows "Not scored yet" with a **Score this run** button where the scores would be.
  - An interrupted scoring shows "Scoring was interrupted" with the same button.
  - The Overview's Scores section and the Report tab use the same words for each state:
    - "Not scored yet"
    - "Being scored…" (spinner label "Being scored")
    - "Scoring was interrupted"
- **C4 Board:**
  - Each status column has a one-line caption.
  - Scored cards read "Workflow 1.00 · Output 0.92".
  - An empty column says "None right now".
  - The "Evaluating" column and status read "Being scored".
- **C5 Plain words:** every label in the glossary below is replaced everywhere it shows. None of these appear as visible text on any page: `llm_call`, `tool_call`, `agent_step`, `Stdev`, `Δ`, `Mean overall`, `Live now`, `Nothing here`, `judging…`, `W 0.`, `W 1.`, `Prices from`, `Variant`, `Leaderboard`, `Missed workflow stages`, `Evaluate →`, `Evaluate this run`, `Not evaluated yet`, `Evaluation was interrupted`, `runs + evaluations`. A table header whose exact text is `ms`, `Stdev`, `Mean`, `Min` or `Max` also fails the scan; headers are checked by exact text, not substring.
- **C6 Compare colors:**
  - The difference column (formatted by the server, with a sign of `+`, `−` U+2212, or a plain `0`) is green when B is better, red when B is worse, and grey when neither: equal, or a row where more isn't better or worse.
  - Higher is better for scores. Lower is better for cost and duration. Model and tool calls are neither.
  - The API's compare rows carry this as `better: "higher" | "lower" | null` on **every** row:
    - Overall, Workflow, each workflow component, Output, and **each output criterion** (built outside `add()`, so set explicitly): "higher"
    - Cost, Duration: "lower"
    - Model calls, Tool calls: null
- **C7 Numbers:**
  - Scores show two decimals everywhere, including the batch tables.
  - In Compare's steps tables, the "ms" column becomes **Time** showing seconds with one decimal ("1.9 s"), formatted on the client.
  - The server's Duration row ("21 s") is unchanged.
- **C8 Nothing breaks:**
  - No horizontal page scroll at 375 px or 1440 px, in light or dark.
  - Reduced motion is still respected.
  - Every existing test passes, with only label updates.
  - The e2e still starts, scores and inspects a run.

## Non-goals

- New data, KPIs or charts, apart from the Dashboard's "Best model" card, which is in the approved mockup and replaces the "Scored" card. The scored count moves to the Runs card's sub-line.
- Changing API field names or the board's column ids. `evaluating` stays the id, and only its label changes.
- Explanatory tooltips, onboarding tours or a help page.
- The classic UI at `/classic`, apart from the shared compare rows: they gain the additive `better` field, which the classic template ignores, and the label "Tool calls (errors)" becomes "Tool calls (failed)" on both UIs (`tests/test_ui.py` updated).
- Translation.

## Experience

### Design review

- **Approved (2026-10-31): direction B, "Sections with dividers".**
  - Mockups: `mockups/directions.html` (the three directions on the Dashboard) and `mockups/refined-b.html` (B on the Runs board with the run panel, a batch, and Compare). Rendered PNGs are next to them.
  - Alternatives shown:
    - A, captioned panels: the existing layout with a title and caption per panel.
    - C, question headings: section headings phrased as questions, with a data-driven "Answer" caption.
  - Steering: the user picked B as recommended, then approved the refined version as shown. That version includes the renamed statuses ("Being scored", "In progress"), the section numbers, and the Compare color fix.
- **Pages not in the mockup** (Dashboard sections 03–04, Rubric, Import, the rubric views, New run) follow the same pattern; their copy is below.

### The pattern

```
Runs                                                    ← h1, text-2xl semibold
Every agent run, grouped by where it is. Click a run…   ← caption, text-sm slate-500/400

01  At a glance   Counts and totals for the runs below.   [actions →]   ← mono number, h2, caption, optional right-side controls
────────────────────────────────────────────────────────────────────── ← 1px rule (slate-200 / slate-800)
<content: KPI cards, a panel with a chart or table, a form…>             ← 16px below the rule; 40px between sections
```

- **Run panel (narrow):** the number is left out. Sub-sections use an `h3`, text-sm semibold, with a text-xs caption under it, a rule, and 12px before the content. They are 28px apart.
- **Phone width:** the number, heading, caption and controls wrap onto as many lines as needed, and the caption never truncates.

### Page map and copy

**Shell header**
- "rubric adb-rubric-v0 · v0.1" becomes **"Scoring with adb-rubric-v0 v0.1"**.
- "AWS protagona-sandbox-1" becomes **"AWS profile protagona-sandbox-1"**.
- The New run dialog gets the caption "Run one prompt with one set of settings."
  - The Prompt field's hint: "The question the agent answers."
  - The Config field becomes **"Settings (config)"**, keeping today's Bedrock/offline note.

**Runs** (`/`)
- **Header:** "Runs" / "Every agent run, grouped by where it is. Click a run to see its details."
- **01 At a glance:** "Counts and totals for the runs shown below."
  - **Runs:** the count; "of N · +n today" when filtered, otherwise "+n today" or "none today".
  - **Average score:** "n scored · active rubric". It still averages only runs scored with the active rubric, as today. This keeps today's "active rubric" context instead of the mockup's "0 = poor, 1 = perfect"; the scale is explained on the run panel and the Dashboard.
  - **In progress:** "n running · n being scored".
  - **Spent:** "agent runs + judging".
- **02 Runs by status:** "Filter to narrow the board and the totals above." It holds the filters, then the four columns:

  | Column | Caption |
  |---|---|
  | Running | The agent is working now. |
  | Being scored | The judge is grading the result. |
  | Scored | Finished and graded. Ring = overall score. |
  | Needs attention | Failed, or not scored yet. |

- **Card:**
  - "Workflow 1.00 · Output 0.92"
  - "judging…" becomes "scoring…"
  - "Evaluate →" becomes "Score it →"
  - an empty column reads "None right now"
- **When there are no runs at all:** the existing empty state, under the header.

**Run panel** (`/runs/:id`)
- **Header and tabs:** unchanged, except the status pill, which shows words instead of raw statuses:

  | Raw status | Pill |
  |---|---|
  | running | running |
  | evaluating | being scored |
  | succeeded | succeeded |
  | failed | failed |
  | timeout | timed out |
  | anything else | shown as is |
- **Overview sub-sections:**

  | Sub-section | Caption | Content |
  |---|---|---|
  | Scores | How the judge graded this run, from 0 to 1. | Three rings labelled Overall, Workflow and Output under each ring. Side note, as in the mockup: "Workflow: how the agent worked. Output: how good its plan is. Rubric <id> v<ver>". |
  | Limits used | How much of each budget the run used. | The gauges, then one line: "Cost $0.270 · 7.2k tokens out · 17 tool calls, all ok" (or "…, 2 failed"), then "Servers: roda 9 · stac 8" (kept from today's panel; an addition to the mockup). |
  | Agents | Time each agent took, in order. | Pipeline runs only. |
  | Latest steps | The last things the agent did. All steps are on the Steps tab. | The latest steps. |

  - **Unscored, finished run:** the Scores section says "Not scored yet." and has a **Score this run** button.
  - **While it's being scored:** "Being scored…" with a spinner.
  - **While it's running:** "Scores appear after the run finishes and is graded."
- **Tab captions:**

  | Tab | Caption |
  |---|---|
  | Timeline | Each bar is one step, placed by when it ran. Longer bars took longer. |
  | Steps | Every step in order: what the model said and which tools it called. |
  | Report | The judge's written report on this run. |
  | Rubric | Keeps its existing line, reworded for an unscored run: "Not scored yet: this run will be scored with the active rubric, <id> v<ver>." |

- **Timeline legend:** "model call · tool call · error". There's a space between the step number and its name.
- **Steps:** "1450 in → 60 out" becomes "1450 tokens in → 60 out".
- **Report tab:** "Evaluate this run" becomes "Score this run".

**Batches** (`/batches`)
- **Header:** "Batches" / "Run several prompts on several models at once, then compare the results."
- **01 New batch:** "Each model runs each prompt, once per repeat." Fields:
  - **Settings (config)**
  - **Models**, with the hint "Leave empty to use the config's model."
  - **Prompts**
  - **Repeats**, with the hint "Runs per model and prompt"
  - **At once** (was Parallel)
  - **Stop at ($)** (was Cost cap $)
  - the checkbox **"Score each run with the judge"**
- **02 Past batches:** "Newest first. Click one to see its results." The two sections sit side by side on wide screens, as today.

**A batch** (`/batches/:id`)
- **Header:** "Batch <id>" (the id may break anywhere on narrow screens). The back link "← All batches" sits on the right, and the progress bar sits under the header.
- **The caption** is built from parts:
  - "<total> runs across <m> models", where `total` is `meta.jobs`, or the row count when that's missing, and `m` is the number of variants. Then ", " and the status word:
    - "running" when the batch is running
    - "finished" when `meta.status` is `finished`
    - "starting" when the status is missing
    - otherwise the status as it is
  - The money part:
    - with both `spent_usd` and `max_cost`: " $x of the $y cap spent."
    - with only `spent_usd`: " $x spent."
    - otherwise nothing
- **01 Results by model:** "Average overall score per model. Spread needs 2+ runs."
  - Columns: Model, Succeeded, Average, Lowest, Highest, Spread, Cost / run.
  - The Model cell shows the short model name, and the variant in muted text under it only when the variant isn't just the model id.
- **02 Every run:** "One row per run. Click a prompt to open the run."
  - Columns: Model, Prompt, Repeat (an addition to the mockup: batches can repeat a prompt), Status, Overall, Workflow, Output, Model calls, Tool calls, Cost.
  - Tool calls read "10 (1 failed)".
  - Scores show two decimals. The batch rows carry scores as strings, so the client parses them and shows "—" for an empty or non-numeric value.

**Dashboard** (`/dashboard`)
- **Header:** "Dashboard" / "How each model scored, what it cost, and where agents went wrong."
- **01 Summary:** "Runs scored with the selected rubric."
  - This rewords the mockup's "All runs scored with the active rubric", because the select can pick another rubric.
  - The "Scored with" select and "View rubric →" sit on the right of the heading row.
  - **Section 01 always renders, with its select, even with no data.** The mixed-rubric warning and the empty states ("No runs yet"; "No runs scored with this rubric yet" with "Show all runs") go inside section 01, under the rule. Sections 02–04 render only when there is data.
  - The KPI grid is a `<div role="group" aria-label="Summary numbers">`.
  - **Runs:** the count; "n scored". This replaces the mockup's "all scored with the active rubric", which is wrong when not every run is scored.
  - **Average score:** "0 = poor, 1 = perfect".
  - **Best model:** the short name of the top leaderboard row; "x.xx average". It reads "—" when there are no rows or the top row has no average. A tie means the same average at two decimals, and then the sub-line reads "x.xx average, tied with n more".
  - **Spent:** "agent runs + judging".
- **02 Model ranking:** "Average score per model. Higher is better."
  - A column header row: Model · Average score (line = lowest to highest) · Score · Cost / run · Succeeded.
  - The last header is "Succeeded" rather than the mockup's "Runs", because its cells read "12/12" (succeeded of runs).
  - The Cost / run and Succeeded headers hide below `sm`, as their cells already do.
- **03 Cost vs score:** "Each dot is one run. Up and to the left is best: high score, low cost."
  - Axis titles: "Cost per run" under the x axis and "Overall score" beside the y axis. The legend and hover stay.
- **04 Where agents struggle:** "The most common problems across all runs." Three columns, each with a caption:

  | Column | Caption |
  |---|---|
  | Tool errors | Failed tool calls, by tool |
  | Skipped workflow steps | Required steps a run left out |
  | Weakest output criteria | Average judge score, lowest first |

  - In step labels, `a|b|c` reads "a, b or c".
- **Empty states are kept:**
  - "No runs yet"
  - "No runs scored with this rubric yet" with "Show all runs"

**Rubric** (`/rubric`)
- **Header:** "Rubric" / "The rules the judge uses to score each run's output."
- **01 Active rubric:** "Used to score new runs. Past runs keep the rubric that scored them."
  - Chips read "Data fit · 1–4 · weight 1".
- **02 History:** "Earlier rubrics. Activate one to switch back." The empty text stays.
- **03 Import a rubric:** "A model turns your document into a draft. Nothing changes until you review and activate it."
  - The Config field becomes "Settings (config)", with the hint "Its judge model writes the draft."
- **04 Drafts waiting for review:** "Open a draft to check it and activate it." It shows only when there are drafts.
- **Layout:** 01 and 02 on the left, 03 and 04 on the right, on wide screens.

**Rubric draft** (`/rubric/drafts/:id`)
- **Header:** "Review draft" / "Check the drafted criteria against the source, fix anything wrong, then activate."
- **01 Criteria:** "Edit any field. Problems show next to the field." It holds the model-notes callout, now titled "Notes from the model", the form, and the buttons.
- **02 Source:** "What the draft was made from." The source panel stays sticky on wide screens.

**Rubric views** (`/rubric/active`, `/rubric/history/:file`, `/rubric/runs/:id`)
- **Header:** "<rubric_id> v<version>", with one caption per view:
  - active: "The rubric used to score new runs."
  - history: "An earlier version of the rubric."
  - a run's copy: "The exact rubric that scored run <id>."
- The ✓ active pill, the sha and the back link stay in the header row.
- **One unnumbered section, Criteria:** "Each criterion is scored on its own scale. The levels say what each score means."

**Import** (`/import`)
- **Header:** "Import" / "Score agents built elsewhere by bringing in their traces."
- **01 Where imported runs go:** "Imported runs are filed under this task and prompt id." Fields:
  - Task
  - Prompt id
  - **Price list (config)**, with the hint "Used to work out cost. Leave as none to skip it."
- **02 Upload a trace:** "Drop an OpenTelemetry (OTLP/JSON) file. Each agent run in it becomes a run on the board."
- **03 Live receiver:** "Point an OpenTelemetry-instrumented agent here. Each run it sends shows up on the board."
- **Layout:** 02 and 03 side by side on wide screens, as today.

**Compare** (`/compare`)
- **Header:** "Compare two runs" / "Pick two runs to see their scores, cost and steps side by side."
- **Run pickers:** the two selects (Run A, Run B) sit directly under the header with no section of their own, matching the mockup. The empty state "Pick two runs to compare" follows them.
- **01 Scores and cost:** "B minus A. Green means B did better, red means worse." This adds "red means worse" to the mockup's caption.
  - Columns: (label) · A · <label> · B · <label> · Difference.
  - Component and criterion labels are capitalised ("Completion").
- **02 Steps side by side:** "Every step each run took, in order."
  - Columns: Step · Agent (pipeline only) · Kind · Name · Status · Time.
  - Kind is one of: model call, tool call, agent, note.
  - Time reads "1.9 s".

**Not found:** "Page not found" / "This address doesn't match any page.", plus the "Back to runs" link.

### Glossary

| Was | Now |
|---|---|
| W 1.00 · O 0.67 | Workflow 1.00 · Output 0.67 |
| Mean overall | Average score |
| Live now | In progress |
| Evaluating, evaluating (sub-line), judging…, Evaluating… | Being scored, being scored, scoring…, Being scored… |
| Not evaluated yet, Evaluation was interrupted | Not scored yet, Scoring was interrupted |
| Evaluate →, Evaluate this run, Evaluate each run | Score it →, Score this run, Score each run with the judge |
| runs + evaluations | agent runs + judging |
| Nothing here | None right now |
| Variant (batch tables) | Model (with the variant under it when it differs) |
| Mean / Min / Max / Stdev | Average / Lowest / Highest / Spread |
| Calls 4 / 12, (1 ×) | Model calls 4, Tool calls 12 (1 failed) |
| Compare's "Tool calls (errors)": 12 (1) | "Tool calls (failed)": 12 (1). The server label changes; the values stay as they are |
| Δ (B − A) | Difference |
| llm_call, tool_call, agent_step, note | model call, tool call, agent, note |
| ms | Time (s) |
| Base config, Config | Settings (config) |
| Prices from | Price list (config) |
| Leaderboard | Model ranking |
| Missed workflow stages | Skipped workflow steps |
| Tool errors by tool | Tool errors |
| data-fit · 1–4 · ×1 | Data fit · 1–4 · weight 1 |

### States

- Every page keeps its loading skeleton, error banner and empty state.
- The page header always renders, and the skeleton or error goes where the sections would be. The header shows even while data loads, so an observer always knows where they are.
- The batch page's caption needs data, so it shows "Loading…" until the data arrives. If the fetch fails, the header reads "Batch <id>" / "Couldn't load this batch.", with the error banner (and its Retry) below.
- The Rubric page's old `h1` ("Active rubric") becomes the `h2` of section 01 under the new "Rubric" `h1`. Tests that target headings are updated with it.

## Architecture

**New components** in `web/src/components/ui.tsx`, next to `Panel`, `Kpi` and the rest:
- `PageHeader({ title, caption, children? })`: the `h1`, the caption, and optional right-side content such as a back link, pills or controls.
- `Section({ n?, title, caption, actions?, children })`: `<section aria-labelledby>` with the number, an `h2`, the caption, the actions, the rule and the content. The id comes from `useId()`.
- `SubSection({ title, caption, children })`: the run panel's version. It is a `<div role="group" aria-labelledby>` with an `h3`, not a `<section>`, so the C2 check (`main section[aria-labelledby]` must hold an `h2`) doesn't apply to it.
- **Score this run** is a real `<button class="btn-primary">` with the standard visible focus ring, in the Overview and in the Report tab alike.

Every page swaps its ad-hoc headings for these.

**Docs:** `README.md`'s UI tour (the status names, "Evaluate →", "Leaderboard") is updated to the new words.

**Pages and components touched:**
- `Runs.tsx`: header, two sections, the Overview sub-sections, tab captions, and the "Not scored yet" state
- `Board.tsx`: column captions and labels
- `run.tsx`:
  - gauge summary line
  - timeline legend and number spacing
  - "tokens in"
- `Batches.tsx`, `Batch.tsx`, `Dashboard.tsx`, `Rubric.tsx`, `RubricDraft.tsx`, `RubricVersion.tsx`, `Import.tsx`, `Compare.tsx`, `Shell.tsx`, `main.tsx` (Not found)
- `toasts.tsx` and `Runs.tsx` toasts:
  - "Evaluating <id>…" becomes "Scoring <id>…"
  - in `jobToasts` (`toasts.tsx`), the job word for `kind === "evaluate"` changes from "Evaluation" to "Scoring", so the toast reads "Scoring finished: <id>" (or "Scoring failed: …"), keeping the existing sentence shape
- `format.ts` gains:
  - `stepKind(type)`, mapping `llm_call` to "model call", `tool_call` to "tool call", `agent_step` to "agent" and `note` to "note"
  - `seconds(ms)`
  - `pretty(label)`: turns `-` and `_` into spaces, capitalises the first letter, and turns `|` lists into "a, b or c". So `data-fit` becomes "Data fit" and `completion` becomes "Completion".
  - Rubric chips use the criterion's `name` where it has one, and fall back to `pretty(id)`

**Backend:** `foundry.ui.app._compare_rows` adds `better` to each row:
- "higher" for score rows
- "lower" for Cost and Duration
- null for Model calls and Tool calls

Nothing else on the server changes.

Patterns followed: the existing Tailwind tokens (slate and sky), `Panel`, `Kpi`, `Empty`, `Skeleton` and `ErrorBanner`, the existing routes, and polling.

## Data and contracts

- **`GET /api/compare`:** each row gains `better: "higher" | "lower" | null`. The change is additive.
  - Consumers: the SPA's Compare page.
  - The classic `compare.html` renders `r.delta` only and ignores the new key.
- **No other API, file or schema change.** Column ids (`running`, `evaluating`, `scored`, `attention`) are unchanged.

## Decisions

### ADR-1: Shared section components rather than per-page markup
- **Context:** About 12 pages need the same header and section anatomy, and it must stay consistent.
- **Options:**
  - A) Shared `PageHeader`, `Section` and `SubSection` components.
  - B) A copy of the markup and classes on each page.
  - C) A CSS class layer (`.section-head`) applied by hand.
- **Decision:** A.
- **Why:** One place sets the spacing, rule and accessibility wiring (`aria-labelledby`, heading levels), and tests can target it. B drifts, and C can't enforce the `h2` and label.
- **Reversibility:** easy.
- **Sources:** React `useId` is in React 18 and later. The project pins react 19.3.1 (`web/package.json`); see https://react.dev/reference/react/useId (accessed 2026-10-31).

### ADR-2: Rename labels only, never ids
- **Context:** "Evaluating" becomes "Being scored", and so on. The API's column id `evaluating`, the CLI's `foundry evaluate`, and file names all use the old words.
- **Options:**
  - A) Change the UI labels only.
  - B) Rename ids throughout.
- **Decision:** A.
- **Why:** The observer only sees labels. Renaming ids would break the API contract, the classic UI and scripts for no visible gain.
- **Reversibility:** easy.
- **Sources:** n/a (internal).

### ADR-3: The server says which direction is better
- **Context:** Compare colors deltas on the client from the sign alone, which shows a cost increase as green.
- **Options:**
  - A) The client keeps a list of lower-is-better labels.
  - B) The server, which builds the rows, adds `better`.
- **Decision:** B.
- **Why:** The rows and their meaning are defined in one place (`_compare_rows`). Label matching on the client would break silently if a label changed.
- **Reversibility:** easy (an additive field).
- **Note:** the client still reads the sign from the formatted delta (`+`, U+2212 `−`, or `0`) and colors it by `better`.
- **Sources:** n/a (internal).

## Failure modes

| Failure | Detection | Behavior | Recovery |
|---|---|---|---|
| A caption or long id overflows at 375 px | e2e: horizontal-scroll check on every page at 375 and 1440 px | Header rows wrap (`flex-wrap`); captions wrap; long ids break (`break-all` on the batch id) | n/a |
| Data still loading or failed | Existing skeleton and error banner | The header renders at once; the skeleton or error sits below it | The existing Retry |
| A compare row without `better`, from an older server | n/a | Treated as null, so grey | n/a |
| No leaderboard rows, for the "Best model" card | Dashboard empty states already cover zero runs | The card shows "—" | n/a |
| Two models tie for best | n/a | "Best model" is the leaderboard's first row. The server already sorts by average, best first, then by name (`dashboard.build`). The sub-line reads "x.xx average, tied with n more" when others share the average. | n/a |

## Security and privacy

- No new inputs, endpoints or HTML sinks. Every new string is a JSX text node.
- `pretty()` only transforms text that is already rendered as text.

## Performance

- The change is presentation only, with no new requests.
- Bundle growth is expected to be under 5 KB gzipped. I'll check it with `npm run build` (today's JS is about 93 KB gzipped). If it goes over, I'll find the cause before Verify passes; it doesn't block on its own.

## Migration and rollback

- No data changes. A revert restores the previous UI completely.
- The `better` field is additive, so an old client ignores it.

## Traceability

| Acceptance criterion | Component(s) | Test(s) |
|---|---|---|
| C1 | `PageHeader` on every page | e2e `clarity` pass: on every route, exactly one `h1` and a caption (`[data-caption]`) |
| C2 | `Section` | Vitest: `Section` renders a labelled `section` with an `h2`, the caption and the number. e2e: every route's `section[aria-labelledby]` has an `h2` and a caption |
| C3 | `Runs.tsx` RunDrawer | Vitest drawer test: the Overview sub-section headings and tab captions; "Not scored yet" with a Score button for an unscored run |
| C4 | `Board.tsx` | Vitest Board tests: the column captions, "Workflow … · Output …", "Being scored", "None right now" |
| C5 | All pages, `format.ts` | e2e: the banned-word scan of `document.body.innerText` on every route, the drawer's tabs, the New run dialog, a batch and Compare. Vitest: `stepKind` and `pretty` |
| C6 | `_compare_rows`, `Compare.tsx` | pytest: every compare API row carries `better` (cost and duration "lower"; overall, a workflow component and an output criterion "higher"; model and tool calls null). Vitest: the delta color helper |
| C7 | `Batch.tsx`, `Compare.tsx`, `format.ts` | Vitest: `seconds()` and two-decimal batch scores. e2e: an exact-text table-header check (no `ms` header) |
| C8 | All | The existing e2e (start, score, Rubric tab), the 375 and 1440 px scroll checks in both themes, the reduced-motion check, and the full pytest and Vitest suites |

## Risks and spikes

- **The riskiest assumption:** that captions on every section stay brief and don't become noise.
  - Each caption is one line at 1440 px: at most about 90 characters, plain words, saying what the section shows or how to read it, never restating the heading.
  - I'll check this by eye on the rendered screenshots before Verify.
- **Label changes break tests that select by text.** The e2e selects by `aria-label` of the column `section`s ("Needs attention" and "Scored" stay, but "Evaluating" isn't used), and Vitest uses "Evaluating". Both are updated in the same slice.

## Implementation plan

1. Add `PageHeader`, `Section`, `SubSection` and the `format.ts` helpers, with Vitest tests.
2. Runs board and run panel: header, sections, column captions, card copy, the Overview sub-sections, tab captions, the unscored state, and `run.tsx` copy. Update the Board and drawer tests.
3. Dashboard, Batches and Batch.
4. Rubric, the rubric draft, the rubric views, Import, Compare (with the `better` field and its pytest test), the Shell header, the New run dialog, and Not found.
5. README tour wording. The e2e: the board wait selector stays `[aria-label="Key numbers"]`. Then the e2e clarity pass (header and caption per route, the banned-word scan, scroll checks), screenshots, and a side-by-side comparison with the approved mockups.

## Design gate record

- **Pass 1 (blocking):** e2e selectors, board columns vs C2, Report-tab wording, criterion rows' `better`, the duration format.
  - Resolved in place: the exemptions under C2, the C3 wording, C6 every row, C7.
- **Pass 1 (mockup questions):** Compare stays numbered as in the mockup, and the additions (Repeat column, Servers line, "red means worse") are declared inline.
- **Pass 2 (blocking):** the Rubric-tab line, the drawer pill, where the Dashboard select lives when empty, toasts, the batch caption parts, and `SubSection`'s element.
  - Resolved in place.
- **Pass 3 (blocking, the last allowed pass):** the two blockers below, taken to the user with proposed resolutions. **The user approved them (2026-10-31): "Yes, build with these fixes".**
  1. **Server wording on cards and in Compare's picker.** `format.ts` gains `statusWord(s)`, which maps:

     | Server word | Shown as |
     |---|---|
     | "not evaluated" | "not scored yet" |
     | "evaluating" | "being scored" |
     | "incomplete" | "partly scored" |
     | "timeout" | "timed out" |
     | anything else | itself |

     It's used by the board pill, the card's `aria-label`, the Compare run options, and the drawer pill. The banned-word scan is case-insensitive and adds "not evaluated", "evaluating" and "incomplete" as whole words.
  2. **The e2e enforces C1, C2, C5 and C8 on every route.**
     - A new `clarity` stage in `scripts/ui_e2e.mjs` always runs, not only with a screenshot directory.
     - It finds its ids through the API:
       - the newest batch from `/api/batches`
       - the first draft and history version from `/api/rubric?task=adb`
       - a scored run from `/api/runs`
     - When one doesn't exist, it skips that route with a printed `SKIP <route>: none available`.
     - **Routes:**
       - `/`, `/runs/<scored>` (all five tabs), `/batches`, `/batches/<newest>`
       - `/dashboard`, `/rubric`, `/rubric/active`, `/rubric/runs/<scored>`, `/rubric/history/<file>`, `/rubric/drafts/<id>`
       - `/import`, `/compare?a=&b=`, `/compare` (empty), `/nope` (not found), and the New run dialog
     - **Checks at 375 and 1440 px, in light and dark:**
       - a horizontal page scroll **fails** the run
       - exactly one `h1` and a `[data-caption]`
       - every `main section[aria-labelledby]` holds an `h2` and a caption
       - the banned-word and exact-header scans
- **Defaults applied from pass 3's questions and minors:**
  - **An empty Runs page** keeps both sections, with zero KPIs, the filters, and the empty state inside 02.
  - **The batch Model cell** shows the variant line only when the variant isn't `slug(model)` (the batch's own naming). `m` counts distinct models.
  - **The batch caption** reads "all finished" when finished, as in the mockup.
  - **The Overview Scores state, in order of precedence:**
    1. being scored
    2. scoring was interrupted
    3. scored: when `overall` is null, it reads "Partly scored: some criteria couldn't be scored." with the rings that exist
    4. running
    5. not scored yet
  - **C7's two decimals** apply to tables and text. The rings keep ".97", and the Dashboard criteria keep percentages.
  - **`agent_step` reads "agent"**, not "agent start", because in imported traces it is a real span.
  - **Leftover phrases reworded:**
    - "Future evaluations…" (Rubric and the rubric draft) becomes "New runs are scored with the active rubric…"
    - Import's "Evaluate an agent we didn't build" is replaced by the section caption
    - The rubric draft's "← Rubric" link and Valid pill go in its `PageHeader` row
  - **Matching:**
    - Text assertions match case-insensitively, since the column `h3`s are uppercase through CSS.
    - `pretty()` on Compare labels runs on the client.
    - Compare rows are keyed by index.
  - **The live indicator** ("live · updating every 2 s") sits in section 02's actions slot.
  - **`types.ts`:** the compare row type gains `better?: "higher" | "lower" | null`.
