# Increment 13: manage prompts in the UI — Spec

**Track:** Standard   **Date:** 2026-10-31   **Status:** Built (verified 2026-10-31)
**Builds on:** increment 12 (page header and numbered-section components, plain-word copy)

## Problem

Adding, changing or retiring an evaluation prompt means hand-editing `tasks/adb/prompts.jsonl` (and `expected.jsonl` for the workflow score's expected tool steps). Nothing in the UI shows which prompts exist, how each one scores, or which have expected steps. The person running evaluations (the builder, and soon Clay's team, who will bring their own prompts) can't grow or maintain the prompt set without touching files.

## Users

- **Eval operator:** runs batches and wants to add a prompt and run it the same day.
- **Reviewer or observer:** wants to see what agents were asked and how each prompt scores.

## Goals

- See every prompt, with how it performs.
- Add a prompt, edit a prompt that hasn't been run, and version a prompt that has.
- Archive a prompt so it's no longer offered, without losing its runs.
- Edit the expected tool steps that feed the workflow score's sequence check.

## Non-goals

- Deleting prompts. Runs point at prompt ids, so nothing is ever deleted.
- Tasks other than `adb`, and creating tasks. The page is for the current task; the API takes `task`.
- Writing offline replay scripts for new prompts. New prompts run on Bedrock configs.
- Re-scoring past runs after expected steps change. New steps apply to scorings from then on, and each score already records the `expected_sha256` it used.
- Editing prompts from the classic UI or the CLI. The CLI keeps reading the same files.
- Bulk import of prompts from a file.

## User stories

- As an operator, I add a prompt on a page, then start a run of it from New run or a batch.
- As an operator, I can't silently change a prompt that already has runs. I make a new version instead, so scores never mix two wordings.
- As an operator, I archive a prompt I no longer want offered; its runs, scores and pages stay.
- As an operator, I set the expected tool steps for a prompt by picking tools that runs have actually called.
- As an observer, I open Prompts and see each prompt's text, run count and average score.

## Acceptance criteria

**Listing (P1–P2)**
- **P1:** A **Prompts** tab (nav order: Runs, Prompts, Batches, Dashboard, Rubric, Import) opens `/prompts`. It uses increment 12's page header ("Prompts" / "The questions agents are asked. Each run answers one prompt.") and two sections:
  - **01 Active prompts:** a card per active prompt with its id, its average overall score as a ring ("—" with no scored runs), the first ~120 characters of its text, "n runs · k expected steps", and an Edit link. The whole card links to `/prompts/<id>`. "+ New prompt" sits in the section's heading row.
  - **02 Archived:** shown only when some prompts are archived, with the caption "Hidden from New run and batches. Their runs are kept."
- **P2:** The average is over runs scored with the active rubric, as on the Runs board. The run count covers every run of that prompt id.

**Creating (P3)**
- **P3:** `/prompts/new` is the edit page, empty.
  - The id is prefilled with the next free `adb-NNN` and can be changed.
  - The text is required, up to 20,000 characters.
  - **Create** saves the prompt and opens `/prompts/<id>`.
  - Errors appear next to the field:
    - an id already in use, archived ones included
    - an id outside `^[A-Za-z0-9._-]{1,64}$` or containing `..`
    - empty text

**Editing and versioning (P4–P5)**
- **P4:** `/prompts/<id>` has, under its header (the id, "n runs · avg .96", and "← All prompts"):
  - **01 Text:** "What the agent is asked."
    - **0 runs:** the text is editable, with **Save**.
    - **1 or more runs:** it's read-only with the caption "Locked: n runs used this text.", plus **Make a new version**.
  - **02 Expected tool steps:** "The order a good run calls tools in. Used for the workflow score's sequence check."
  - **03 Runs of this prompt:** "Latest first. Click to open." Up to 20 runs, each showing its model, overall score and cost, linking to the run.
  - **Archive this prompt** (or **Unarchive**) at the bottom.
- **P5:** **Make a new version** creates `<id>-v2` (or the next free `-vN`, counting from the base id without its `-vN` suffix). It has the same text and the same expected steps, no runs, and the text editable. Then it opens that prompt. The original is unchanged.

**The server enforces the lock (P6)**
- **P6:** The server refuses to change the text of a prompt with runs (409: "adb-005 has 23 runs, so its text can't change. Make a new version instead."). Runs are counted from the runs directory at request time.

**Archiving (P7)**
- **P7:** Archiving keeps the prompt in `prompts.jsonl` with `"archived": true`.
  - Archived prompts are left out of the New run dialog, the Batches prompt picker, and `/api/session`'s `prompts`.
  - Starting a run or batch of an archived prompt from the API is refused with 400.
  - The CLI (`foundry run --prompt`) still accepts it, so old commands keep working.
  - Unarchive reverses it.

**Expected tool steps (P8–P9)**
- **P8:** The step editor shows an ordered list of steps. Each step is "any of" one or more tools, shown as removable chips.
  - **+ tool** opens a list of suggestions: every `server/name` seen in a run trace, plus names already in `expected.jsonl`, sorted. It also takes a typed name matching `^([A-Za-z0-9._-]{1,64}/)?[A-Za-z0-9._-]{1,128}$`.
  - Steps can be added, removed and moved up or down.
  - **Save steps** writes them.
  - Saving zero steps removes the prompt's line from `expected.jsonl`, and the page then says "No steps: this prompt's workflow score has no sequence check."
  - Limits: at most 12 steps, and at most 12 tools per step.
- **P9:** A saved line has exactly the format `load_expected` reads (`{"prompt_id", "stages": [[tool, …], …]}`). The next scoring of a run of that prompt uses it, and records the new `expected_sha256`.

**Configs that can't run a prompt (P10)**
- **P10:** If a config replays scripts (provider `scripted`) and has no script for the prompt, starting that prompt on it is refused up front with 400: "The offline config 'offline' has no script for adb-007. Pick a Bedrock config." This applies to New run and to batches. The New run dialog shows that message as its error.

**File writes (P11)**
- **P11:** Every write goes through one store module:
  - It takes an exclusive lock (the same O_EXCL pid-lock pattern as the rubric store; a stale lock is cleared). A held lock returns 409 "Prompts are being changed; try again."
  - It writes the whole file to a temporary file in the same directory, then `os.replace`s it.
  - It re-validates the result with `load_prompts` / `load_expected` before replacing; a failure leaves the file untouched and returns 400.
  - Lines for other prompts keep their order and content.

**Security (P12)**
- **P12:** Every write endpoint is protected by the existing guard: the CSRF header, a JSON body, and Origin and Host checks.
  - Ids are validated before use, never trusted from the path.
  - Prompt text is stored and shown as text only, with no HTML sinks.
  - Errors don't leak server paths.

**Tests (P13)**
- **P13:** Tests:
  - **API (pytest):** P3, P5, P6, P7, P8 and P9, P10, P11 (lock held → 409; an invalid write leaves the file byte-identical), P12 (403 without CSRF, 415 non-JSON, traversal ids → 400 or 404, text echoed as JSON only)
  - **Vitest:** the cards page groups active and archived; the edit page locks text when runs > 0 and offers the new version; the step editor adds, moves and removes steps and tools
  - **e2e:**
    1. Create a prompt.
    2. See it as a card.
    3. Set two steps.
    4. Try to run it on offline and see the P10 message.
    5. Archive it, and see it leave the New run picker.

    This runs against a temporary copy of the task files, so the real `prompts.jsonl` is never touched by tests.

## Success signals

- A new prompt can go from idea to a Bedrock run without opening a file.
- `git diff tasks/adb/` after using the page shows only the intended line changes.

## Constraints

- `tasks/adb/prompts.jsonl` and `expected.jsonl` are git-tracked repo files. Writes from the UI change the working tree, and committing them is up to the operator.
- The format must stay readable by today's `load_prompts` and `load_expected`. Extra keys (`archived`) are ignored by `load_prompts` today (`src/foundry/tasks.py:38-45` reads only `prompt_id` and `prompt`).

## Assumptions and open questions

- **A1** (default, owner: builder): "Runs of a prompt" counts every run directory whose `run.json` has that `prompt_id` and task, including failed and unreadable-but-named runs. Imported runs filed under a prompt id also count, which locks that prompt's text.
- **A2** (default, owner: builder): the new id's numbering is `adb-` plus the next unused 3-digit number. For versions it's `-v2`, `-v3`, and so on.
- **A3** (user decision, 2026-10-31): a prompt's text is locked once it has runs. A new version copies it. Steps stay editable regardless.
- **A4** (user decision, 2026-10-31): expected steps are editable in the UI.
- **A5** (user decision, 2026-10-31): the layout is cards plus a separate edit page, approved as wireframed.
