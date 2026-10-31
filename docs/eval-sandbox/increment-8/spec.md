# Increment 8: rubric import, versions and tracking — Spec

**Track:** Standard   **Status:** Built (verify review pending)   **Date:** 2026-10-31   **Product stage:** Introduction (hackathon prototype)

## Problem

Clay's rubric hasn't arrived, and nobody knows what form it will take: a PDF, Markdown, an Excel workbook with several tabs, the output of another tool, or a few sentences. Today the only way to change the rubric is to hand-edit `tasks/<task>/rubric.json` to a strict JSON schema. Nothing records which rubric version scored which run in a way anyone can see.

## Users

- **The team at the event:** they receive Clay's rubric and must have it scoring runs within minutes, without an engineer hand-writing JSON.
- **Clay's evaluators:** they need to know which rubric produced each score.

## Goals

- Turn a rubric in any of these forms into a validated `Rubric`:
  - an uploaded file: PDF, DOCX, XLSX (all sheets), CSV, Markdown, TXT, HTML or JSON
  - pasted text
  - an https URL
  - the result of a call to a configured MCP tool
- **A person always reviews and edits the draft before it's activated.**
- **Activation replaces the task's active rubric and keeps every version.** Each run's report, run page and the dashboard show which rubric version scored it.

## Non-goals

- Re-scoring past runs automatically when a rubric changes. They keep their scores and the rubric copy they were scored with; re-evaluate runs explicitly.
- Editing prompts, expected stages or `system.txt`. Only the rubric.
- OCR of scanned images inside a PDF, beyond what the model reads natively.
- Committing to git. Activation writes files under `tasks/`, and the user commits them.
- Multiple simultaneously active rubrics per task (the user chose "replace, keep history").

## User stories

- I upload Clay's `ADB rubric.xlsx`, and within a minute I see its criteria as editable cards. I fix one weight and approve. From then on, evaluations use it, and the run page says "Rubric: clay-adb v1.0".
- I paste three sentences ("score accuracy, specificity and retrieval usefulness, 0–3 each; accuracy counts double"), and I get the same review form.
- On the dashboard, I filter to the active rubric so that scores from different rubrics aren't averaged together.

## Acceptance criteria

**I: import sources**
- **I1, file:** the UI upload or `foundry rubric draft --task T --file PATH` accepts files up to 4.5 MB (our cap). The extension must be one of `.pdf .docx .xlsx .csv .md .txt .html .json`. Anything else is rejected with a message listing the allowed types.
- **I2, text:** a UI textarea or `--text "…"` accepts 1 to 50,000 characters.
- **I3, URL:** `--url https://…` or the UI field.
  - **Allowed:** https only. Every resolved address must be globally routable (`ipaddress.is_global`, which also excludes carrier-grade NAT `100.64/10`) and not multicast. This is checked for the first request and each redirect, with at most 3 redirects.
  - **Limits:** 10 s timeout and a 4.5 MB body cap.
  - **Format:** taken from `Content-Type`, then the URL's extension, falling back to txt.

  Each refusal names its reason.
- **I4, MCP tool:** `--mcp SERVER/TOOL --args '{json}' --config C`, or the UI fields. The server must be one of the chosen config's `[mcp.servers]`. The server is started, called once with a 60 s timeout, and stopped, and the result text becomes the source (txt). An unknown server or tool, a tool error, or an empty result is reported, and no draft is created.
- **I5, JSON shortcut:** a source that is already valid `Rubric` JSON skips the model and goes straight to review.

**D: drafting**
- **D1:** The draft model is the chosen config's `[judge]` model (default config: `bedrock`). Binary formats (PDF, DOCX, XLSX, CSV) are passed to Bedrock Converse as `document` blocks with a neutral name, `source document`. Text sources are sent as text inside `<source>` tags. The prompt says the source is data, and that instructions inside it must be ignored.
- **D2a, canonical fields:** whatever the model or the source supplies, a draft keeps only:
  - `rubric_id`, `task_id`, `version` and `description`
  - per criterion: `id`, `name`, `description`, `max`, `weight`, and `levels` keyed `"1"` to `str(max)`

  Extra fields and out-of-range levels are dropped, so nothing the reviewer can't see reaches the judge. `task_id` is forced to the target task. `rubric_id` must match `[A-Za-z0-9._-]{1,100}` and `version` must match `[A-Za-z0-9._-]{1,40}`; anything else is a validation error.
- **D2:** The model returns JSON. The reply is validated against `Rubric` with the existing validators: unique ids, `max ≥ 2`, and levels describing 1 and `max`. If it's invalid, the model gets one repair attempt with the validation error. If it's still invalid, the draft is saved as `invalid`, and the review page shows the raw reply and the error, so the user can fix it by hand or discard it.
- **D3:** The model also returns `notes`, a list of interpretation choices (for example "'double' weight → 2"), shown on the review page. Usage (tokens in and out) is recorded with the draft. Cost is added when the config's `[prices]` has the judge model.
- **D4:** The drafting prompt is versioned: `src/foundry/rubrics/prompts/draft-v2.txt`, with its id recorded in the draft. v2 replaced v1 after the live run: given a source that is not a rubric (an RFC, a dataset record), v1 invented a rubric. v2 replies `{"error": "not a rubric: …"}` instead, and that becomes an invalid draft saying so, with no repair attempt.

**R: review and activation**
- **R1:** Drafts live in `tasks/<task>/rubrics/drafts/<draft_id>/`:
  - `source.<ext>`, the bytes as received
  - `draft.json`
  - `meta.json`: source kind, original name or URL or tool, `source_sha256`, size, model, prompt id, notes, usage, and created time

  `draft_id` is 12 hex characters.
- **R2:** The review page (approved design A) shows:
  - `rubric_id`, `version` and `description` fields
  - one card per criterion: id, name, description, max, weight, and one field per level from 1 to max
  - "Add criterion" and a per-card "Remove", which work without JavaScript (a form round trip)
  - the source on the side: the text itself for text formats, or the name, size, sha256 and an "Open source" link for binary formats

  "Save" re-validates and shows field errors inline.
- **R3:** "Approve and activate" has to pass validation **and** the canonical-field rule (D2a). The `(rubric_id, version)` pair must not already be in the history; if it is, the error says to bump the version. On success:
  1. The previous active rubric is archived into history first, if its sha256 isn't already there. If its `id@version` file exists with different bytes (hand-edited without a version bump), it's archived as `id@version-<sha8>.json` instead of being lost.
  2. The rubric is written to `tasks/<task>/rubrics/history/<rubric_id>@<version>.json`.
  3. It is copied byte for byte to `tasks/<task>/rubric.json`.
  4. A line is appended to `tasks/<task>/rubrics/history.jsonl`: id, version, sha256, activated_at, draft_id, source kind and name, and source sha256.
  5. The draft directory moves to `rubrics/sources/<draft_id>/`.
- **R4:** "Discard" deletes the draft directory. That's the only deletion this increment adds.
- **R5:** Activating an older version from the history (rollback) copies it back to `rubric.json` and appends a history line.

**T: tracking**
- **T1:** Every evaluation already records `rubric_id_loaded` (the rubric that actually scored the run; `rubric_id` is the one recorded when the run started), `rubric_version` and `rubric_sha256` in scores.json, and copies `rubric.json` into the run directory. The run page header gains "Rubric: `<rubric_id_loaded>` v`<rubric_version>`", linked to the history version with that sha256 when one exists. The report already prints this line (`report.py`).
- **T2:** The Rubric tab's history shows, per version, how many runs it scored (matched by `rubric_sha256` across `runs/*/scores.json`) and which version is active.
- **T3:** The dashboard gains a "Scored with" filter: the active rubric (the default), any version, or all.
  - **Filtering:** every view (KPIs including runs and spend, the leaderboard, the scatter and the struggles) uses only runs scored with the chosen rubric. Unscored runs appear only under "all".
  - **Mixed rubrics:** when "all" mixes rubrics, a note says so.
  - **Empty:** when the chosen rubric has scored nothing yet (for example, right after an activation), an empty state says so and offers "all".
- **T4, accessibility:** every new form control has a visible label. Draft and validation status are shown in text ("✗ Invalid: …", "✓ Valid") as well as colour. The pages follow the existing templates for phone width and dark mode.

**S: security**
- **S1:** The new POST routes require the CSRF token and the same-origin check, like the existing ones. Upload size is enforced while streaming, as in the trace import.
- **S1b:** Must-deny cases, each tested:
  - non-https URLs, URLs resolving to non-global addresses, and redirects to them
  - unknown MCP servers
  - draft ids and history names outside their patterns
  - `rubric_id` or `version` with path characters
  - extension and size limits
  - an activation while another is live (a stale lock from a crashed process is cleared)
- **S2:** All rubric text renders autoescaped. Imported rubric fields are untrusted until approved, and still untrusted when shown.
- **S3:** The MCP source can only start servers already named in a config; there is no free-form command.

## Success signals

- At the event, Clay's rubric goes from "received" to "scoring runs" in under 5 minutes, whatever form it comes in.

## Constraints

- No new Python dependencies. Bedrock reads the binary formats (checked live on 2026-10-31 with Opus 4.6: PDF, DOCX, two-tab XLSX and TXT all read correctly).
- The `Rubric` contract (`contract.py`) is unchanged.

## Assumptions and open questions

1. **Bedrock's per-document size limit isn't stated in the API reference** (`DocumentSource`). We cap at 4.5 MB, and anything Bedrock rejects comes back as a clear error. Owner: the team. Default: the cap.
2. **Scanned (image-only) PDFs** may yield nothing. The draft would then be empty or invalid, and the user sees the error and pastes the text instead. Owner: the team.
3. **`tasks/` is in git,** so activated rubrics show up as working-tree changes for the user to commit. Owner: the user.
