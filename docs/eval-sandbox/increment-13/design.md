# Increment 13: manage prompts in the UI — Design

**Spec:** ./spec.md   **Last updated:** 2026-10-31   **Status:** Current

## Overview

A new `foundry.prompts` store owns every write to `tasks/<task>/prompts.jsonl` and `expected.jsonl`. It works under a pid lock, writes atomically, and validates the result with the existing loaders before replacing a file. Thin `actions` functions add run counts and scores from the runs directory. New `/api/prompts…` routes sit behind the existing write guard. The SPA gains a Prompts tab: a cards page and an edit page, both built from increment 12's `PageHeader` and `Section`. Archiving is a flag on the prompt line that the loaders ignore, so the CLI and older code keep working.

## Experience

### Design review

**Approved (2026-10-31):** cards, with editing on a separate page.

**Alternatives shown:**
- (recommended) a list and an editor side by side
- rows that expand in place

**Wireframes as approved:**

```
Prompts
The questions agents are asked. Each run answers one prompt.

01 Active prompts   6 prompts                                    [+ New prompt]
──────────────────────────────────────────────────────────────────────────────
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│adb-001   (.97)│ │adb-002   (.93)│ │adb-003   (.95)│
│We want to map│ │A development │ │Plan a dataset│
│cropland…     │ │bank wants…   │ │of building…  │
│14 runs · 2 expected steps    Edit │ …
└──────────────┘ └──────────────┘ └──────────────┘

02 Archived   Hidden from New run and batches. Their runs are kept.
──────────────────────────────────────────────────────────────────────────────
(same cards, muted)
```

```
← All prompts
adb-005                                       23 runs · avg .96
Compare at least two open sources of imagery…   (the first 120 characters, as the caption)

01 Text   What the agent is asked. Locked: 23 runs used this text.
──────────────────────────────────────────────────────────────────
[ read-only text box ]                       [Make a new version → adb-005-v2]
   (0 runs: an editable text box and [Save])

02 Expected tool steps   The order a good run calls tools in. Used for the workflow score's sequence check.
──────────────────────────────────────────────────────────────────
Step 1  any of: [search_datasets ×] [list_datasets ×] [+ tool ▾]   ↑ ↓ ✕
Step 2  any of: [get_dataset_details ×] [preview_dataset ×] [+ tool ▾]   ↑ ↓ ✕
[+ Add step]                                              [Save steps]
(no steps: "No steps: this prompt's workflow score has no sequence check.")

03 Runs of this prompt   Latest first. Click to open.
──────────────────────────────────────────────────────────────────
kimi-k3 · .96 · $0.115 · 10-31 14:02   (up to 20 links)

                                                   [Archive this prompt]
```

### States

- **The cards page:**
  - **Loading:** the header plus skeleton cards.
  - **Error:** the header plus an error banner with Retry.
  - **No prompts:** an empty state, "No prompts yet", with **+ New prompt**.
  - **No archived prompts:** section 02 is hidden.
- **The edit page:**
  - **Unknown id:** the header "Prompt not found" / "No prompt has the id <id>." with "← All prompts".
  - **Saving:** the button reads "Saving…" and is disabled.
  - **Success:** a toast ("Saved adb-007", "Created adb-005-v2", "Archived adb-007", "Steps saved").
  - **Field errors** show under the field, in the shape `{error, field}` that the rubric draft form already uses.
  - **A 409 (lock or locked text):** an error banner with the server's message.
- **The step editor:**
  - **Suggestions loading:** "+ tool" still accepts typed names.
  - **Dirty state:** "Unsaved changes" next to Save steps. Leaving with unsaved changes doesn't prompt; this is noted as a follow-up.
- **The New run dialog:** an archived prompt doesn't appear. A P10 refusal shows as the dialog's error.

### Copy

- The nav label is "Prompts".
- The button labels are:
  - Create
  - Save
  - Make a new version
  - Save steps
  - + Add step
  - + tool
  - Archive this prompt
  - Unarchive
- All the captions are given in the spec's ACs and in the wireframes above.

## Architecture

**`src/foundry/prompts/store.py` (new):** `PromptStore(task_dir)`.
- `list() -> list[PromptRow]`, where each row is `{prompt_id, prompt, archived, stages | None}`, in file order.
- `get(id)`.
- `create(id, text)`.
  - **Reserved ids:** `new` and `tools` collide with the `/prompts/new` route and `GET /api/prompts/tools`, so they are refused with 400 and the message "'new' is reserved; pick another id".
  - `copy_version` refuses with 400 when `<base>-vN` would pass 64 characters: "The id is too long to version; create a new prompt instead".
- `set_text(id, text)`: the caller checks the lock.
- `copy_version(id) -> new_id`: copies the text and the steps.
- `set_archived(id, bool)`.
- `set_stages(id, stages | [])`.
- `next_id(prefix="adb-", taken)`: the next `adb-NNN` that is neither in the file nor among the prompt ids of existing runs (the caller passes `taken`), so a new prompt is never born locked.
- **Each write runs under `_lock()`**, which copies `RubricStore._lock` with its own lock file, `tasks/<task>/.prompts.lock`.
- **Each write follows the same steps:**
  1. Read the current lines.
  2. Make the change.
  3. **Copy every untouched line through verbatim, byte for byte, blank lines included.** Only the target line is rebuilt: its parsed object is updated in place, which keeps any extra keys and their order, and `archived` is added when true or removed when false. A new prompt is appended as a new line. Lines are written with `json.dumps(obj, ensure_ascii=False)`, and both files are read and written as `encoding="utf-8"` (the `_parse_*` refactor passes `encoding="utf-8"` too).
     - **Hand-edit detection:** the store records the file's sha256 when it reads, and checks it again just before `os.replace`. If it changed, it refuses with 409: "prompts.jsonl changed on disk while saving; reload and try again."
     - **Uniqueness:** the store refuses a write that would leave two lines with the same `prompt_id` (400).
  4. Write to `<file>.tmp-<pid>`.
  5. Validate the temporary file with the parsing core of `load_prompts` or `load_expected`. These are refactored to take a path (`tasks._parse_prompts(path)` and `tasks._parse_expected(path)`), and the existing loaders call them.
  6. `os.replace` the temporary file over the original.
  7. On any error, delete the temporary file and raise `StoreError(status, msg, field)`.
- **Text:** the text is stripped. Empty text and text over 20,000 characters are rejected.
- **Steps:** at most 12 steps, each with 1–12 tools, every tool matching `TOOL_RE`. Duplicate tools within a step are dropped, keeping order.

**`src/foundry/tasks.py`:**
- `load_prompts` is unchanged in behavior. It ignores `archived`, so the CLI keeps accepting archived ids.
- `active_prompts(task_id)` is new and leaves out lines whose `archived` is true.

**`src/foundry/ui/actions.py`:**
- `prompts_view(st, task)` returns `{prompts: [{prompt_id, prompt, archived, stages, runs, scored, mean}], next_id}`.
  - Run counts come from the run directories, matching `run.json` `prompt_id` and `task_id`.
  - `mean` comes from the board's active-rubric shares (`board_kpis`), which covers P2.
  - The cards' `runs` and `mean` come from one pass over `_entries`.
- `prompt_view(st, task, id)` adds that prompt's runs: the latest 20, each with `run_id`, `model`, `overall`, `cost` and `started_at`, newest first.
- The write functions map store errors to `UiError`.
  - `UiError` gains an optional `field` (`UiError(status, message, field=None)`), and the `/api` error handler adds `"field"` to the JSON body when it's set. That's the `{error, field}` shape the page reads.
  - **Paths in errors:** every store and loader message passes through `_rel(msg, root)`, which replaces the absolute project root with nothing and the temporary file name with the real file name. So errors read `tasks/adb/prompts.jsonl:7: …`.
- **`set_text` refuses (409, the P6 message)** when the prompt "has runs or has a run on the way":
  - **Runs on disk:** run directories whose `run.json` names the prompt. A run directory whose `run.json` is unreadable counts when its name contains `_<id>_`, from the run-id format `<stamp>_<prompt_id>_<hex>` (`runstore.py:25`) and batch ids `<batch>_<variant>_<prompt_id>_rN`.
  - **Runs on the way:**
    - any live UI job (`st.jobs`, `alive`) of kind `run` whose run id contains `_<id>_`
    - any live batch job whose `--prompts` argument lists the id. Queued batch jobs don't have run directories yet.
- `tool_suggestions(st, task)`: the sorted set of `server/name` from every `tool_call` step in `_entries`, plus tools in `expected.jsonl`, capped at 500.
- **`start_run` and `start_batch` gain two checks:**
  - **The prompt must be active.** `start_run` switches from `load_prompts` to `active_prompts` (`actions.py:101`), and `core._prompt_ids` (used by `start_batch`, the session and the classic UI) does the same.
  - Under P10, a config whose `model.provider == "scripted"` needs `model.script.format(prompt_id=…)` to exist under the repo root.

**`src/foundry/ui/api.py` routes** (writes behind `Depends(require_write)`):

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/prompts?task=adb` | — | `prompts_view` |
| `GET /api/prompts/{id}?task=adb` | — | `{prompt, runs, locked, version_id}`, or 404 |
| `GET /api/prompts/tools?task=adb` | — | `{tools: [...]}` |
| `POST /api/prompts` | `{task, prompt_id, prompt}` | 201 `{prompt_id}` |
| `PUT /api/prompts/{id}` | `{task, prompt}` | 200 |
| `POST /api/prompts/{id}/version` | `{task}` | 201 `{prompt_id}` |
| `POST /api/prompts/{id}/archive` | `{task, archived: bool}` | 200 |
| `PUT /api/prompts/{id}/stages` | `{task, stages: [[str]]}` | 200 `{stages, expected_sha256}` |

- **Route order:** `/api/prompts/tools` is declared before `/{id}`.
- **Path ids** are validated with `PROMPT_ID_RE` and the `..` check before any lookup, or the route returns 404.
- **The task** must be in `core._task_ids(st)`, or the route returns 400.

**`/api/session`:** `prompts` lists active prompts only.

**Web:**
- **Pages:**
  - `pages/Prompts.tsx`, the cards page
  - `pages/PromptEdit.tsx`, the edit page, which also serves `/prompts/new`
  - `components/StepEditor.tsx`
- **Routes:** `/prompts`, `/prompts/new` and `/prompts/:id` in `main.tsx`.
- **Nav:** the Prompts entry goes in `Shell.tsx`'s nav, after Runs. The "Runs" tab's active test stays as it is.
- **Session cache:**
  - `api.ts` gains `onSessionChange(fn)`, and `getSession(true)` notifies its subscribers after refetching.
  - `Shell` subscribes on mount, so its New run dialog's prompt list updates. Batches calls `getSession` on mount, so it's fresh on navigation.
  - After a create, a new version, an archive or an unarchive, the page calls `getSession(true)`.
  - This also fixes the same staleness for the rubric shown in the header after an activation.

## Data and contracts

- **`prompts.jsonl` gains an optional key, `"archived": true`.**
  - Consumers: `load_prompts` (ignores it), `active_prompts` (new), and the classic UI via `_prompt_ids`, which then lists active prompts only.
  - Older checkouts of the code read the file fine.
- **The `expected.jsonl` format is unchanged.** The store writes only lines `load_expected` already accepts. `expected_sha256` hashes the whole file, so saving any prompt's steps changes the sha that future scorings of every prompt record. That's expected under P9, and past scores keep the sha they used.
- **New API routes are listed above.** `/api/session.prompts` narrows to active prompts; it's consumed only by this SPA.
- **These are repo files.** UI writes show up in `git diff`.

## Decisions

### ADR-1: Archive as a flag on the line, not a separate file
- **Options:**
  - A) `"archived": true` on the prompt's line
  - B) a separate `archived.json`
  - C) moving the line to `prompts.archived.jsonl`
- **Decision:** A.
- **Why:** One file holds the whole truth about a prompt. The loaders already ignore unknown keys, so the CLI keeps resolving archived ids. With C, `foundry run --prompt adb-001` would break for an archived id.
- **Reversibility:** easy. Removing the key unarchives the prompt.
- **Sources:** `src/foundry/tasks.py:29-46` (it reads only `prompt_id` and `prompt`).

### ADR-2: Lock text by runs counted at request time
- **Options:**
  - A) count runs on each write
  - B) store a `locked` flag in the file
- **Decision:** A.
- **Why:** The runs directory is the truth, and a flag could go stale, for example after runs are deleted or imported. Counting costs one directory scan, which `/api/runs` already does every 2–5 s.
- **Reversibility:** easy.
- **Sources:** internal.

### ADR-3: One store with lock, temporary file and replace, validated with the real loaders
- **Options:**
  - A) a store like the rubric store
  - B) direct writes in the API handlers
- **Decision:** A.
- **Why:** It matches increment 8's proven pattern (`RubricStore._lock`, atomic writes). Validating with the same parser the runner uses means the UI can't write a file the CLI then rejects.
- **Reversibility:** easy.
- **Sources:**
  - `os.replace` is atomic when source and target are on the same filesystem: https://docs.python.org/3/library/os.html#os.replace (Python 3.12 docs, accessed 2026-10-31). The temporary file lives in the same directory.
  - `O_EXCL` lock: as in `src/foundry/rubrics/store.py:197-216`.

### ADR-4: Tool suggestions from traces, with free typing allowed
- **Options:**
  - A) suggestions from past traces and existing steps, plus free text
  - B) a live MCP `list_tools` call (dynamic discovery is a known, unbuilt follow-up)
  - C) free text only
- **Decision:** A.
- **Why:** No new process starts from the UI. It covers every tool agents actually used, and typing covers tools not yet seen.
- **Reversibility:** easy.
- **Sources:** `match_stages` accepts "name" or "server/name" (`src/foundry/evaluate/workflow.py:20-31`).

## Failure modes

| Failure | Detection | Behavior | Recovery |
|---|---|---|---|
| Two UI writers at once (two tabs) | pid lock | The second write gets 409 "Prompts are being changed; try again." | Retry; a stale lock is cleared |
| A hand edit between the store's read and its replace | sha check before `os.replace` | 409 "prompts.jsonl changed on disk while saving; reload and try again." Nothing is written | Reload, then save again |
| A hand edit left the file invalid | `load_prompts` raises | List and writes return 400 with the loader's line-numbered message; no write happens | Fix the file by hand |
| A crash mid-write | temporary file plus replace | The original stays intact; the stray `.tmp-*` is ignored by the loaders, which read only the exact file names | Delete the stray temp file (the store deletes it on the next error path) |
| Text edited while a run or batch that includes the prompt is starting or queued | `set_text` checks live UI jobs (runs by run id, batches by their `--prompts`) as well as run directories | 409 with the P6 message | Wait for the job, or make a new version |
| A run started from the CLI in the gap before its `run.json` exists | not detectable | Accepted risk: the CLI run records the text it actually used in its own `run.json` | n/a |
| A runs dir with thousands of runs | the same scan as `/api/runs` | Measured in Verify; today 75 runs take 17 ms | n/a |
| Unreadable run directories | already skipped by `_entries` | Not counted | n/a |
| A batch running while a prompt is archived | the batch resolved its prompts at start | It finishes its jobs | n/a |

## Security and privacy

- **New writes** are under the existing guard: CSRF header, JSON only, and Origin and Host checks.
- **The path `id`** is validated before use and never joined into a path; the store addresses lines by id only.
- **`task`** comes from a whitelist (`_task_ids`).
- **Size limits:** text up to 20,000 characters (about the CLI's limit for a prompt), and steps capped. A request body over the existing size limits is refused.
- **Display:** text is shown as React text nodes only, with no `dangerouslySetInnerHTML`.
- **Errors** carry the loaders' messages, which use paths relative to the repo. The handlers strip the absolute root, as `UiError` already does for rubric errors; P12 tests this.

## Performance

- `prompts_view` makes one pass over `_entries`, the same cost as the dashboard.
- Tool suggestions use the same pass and are capped at 500.
- No polling: the pages load once and reload after a write.

## Migration and rollback

- There's no data migration. The `archived` key appears only when someone archives a prompt.
- Reverting the code leaves any `archived` keys in place; the old `load_prompts` ignores them, so archived prompts simply reappear in the pickers.
- Text and step edits are ordinary changes to git-tracked files. `git checkout tasks/adb/` undoes them.

## Traceability

| AC | Component(s) | Test(s) |
|---|---|---|
| P1, P2 | `Prompts.tsx`, `prompts_view` | pytest `test_prompts_list_counts_and_mean`; Vitest "cards page groups active and archived"; e2e |
| P3 | `PromptEdit.tsx`, `store.create`, `next_id` | pytest `test_prompt_create_and_errors` (duplicate, bad id, empty text) |
| P4 | `PromptEdit.tsx`, `prompt_view` | Vitest "edit page locks text when runs > 0" |
| P5 | `store.copy_version` | pytest `test_prompt_new_version_copies_text_and_steps` (-v2, then -v3) |
| P6 | `actions.set_text` | pytest `test_prompt_text_locked_with_runs` (409 message) |
| P7 | `store.set_archived`, `active_prompts`, `_prompt_ids` | pytest `test_archive_hides_from_session_and_refuses_runs` (session, start_run 400, start_batch 400, `load_prompts` still resolves); e2e |
| P8, P9 | `StepEditor.tsx`, `store.set_stages`, `tool_suggestions` | pytest `test_steps_roundtrip_and_scoring` (save → `load_expected` returns them → `workflow.score` records the new sha; zero steps removes the line); Vitest StepEditor |
| P10 | `start_run`, `start_batch` | pytest `test_scripted_config_without_script_refused`; e2e (offline → message) |
| P11 | `PromptStore` | pytest `test_store_lock_and_atomic` (lock held → 409; invalid write leaves bytes identical; other lines unchanged) |
| P12 | api routes | pytest `test_prompts_must_deny` (403 no CSRF, 415, traversal ids, unknown task, no absolute paths) |
| P13 | the above | e2e prompts flow against a temporary project root (`FOUNDRY_ROOT`-style copy, see the plan) |

## Risks and spikes

- **The riskiest part is that the e2e must not write the real `tasks/adb/prompts.jsonl`.**
  - The mechanism exists today: `project_root()` reads `FOUNDRY_HOME` (`src/foundry/config.py:22-24`), `foundry ui` passes it to `create_app` (`cli.py:163`), and spawned jobs inherit the environment.
  - A new `scripts/ui_e2e_prompts.mjs`:
    1. Copies `tasks/` and `config/` into a fresh `mkdtemp` directory.
    2. Starts its own `uv run foundry ui --port 8799 --runs-dir <tmp>/runs` with `FOUNDRY_HOME=<tmp>`.
    3. Before any write, checks that `/api/prompts` reports a `root` under the temp directory (`prompts_view` returns `root_name`, the root's base name, which the script compares with its temp directory's name). Otherwise it aborts.
    4. Kills the server and deletes the temp directory at the end.
  - The prompts flow never runs against the dev server.
- **Run counting by `prompt_id`** also counts imported runs filed under a reused id (A1). That's accepted, and visible in the count.

## Implementation plan

1. `.gitignore` gains `tasks/*/.prompts.lock` and `tasks/*/*.tmp-*`.
2. `tasks._parse_*` refactor, `active_prompts`, and `PromptStore`, with store tests (P5, P8 format, P11).
3. `actions` and API routes, the P6, P7 and P10 checks, and the session change, with API tests (P1–P12).
4. Web: the Prompts cards page, the edit page, StepEditor, the nav and routes, with Vitest.
5. The e2e prompts flow on the temporary root; screenshots; README "Prompts" paragraph.

## Design gate record

**Pass 1 blockers, resolved inline:**
- `start_run` uses `active_prompts`.
- `Shell` refreshes the session through `onSessionChange`.
- The store copies untouched lines verbatim and checks the file's sha before replacing it.
- The text lock also covers live runs and batch jobs, and counts unreadable run directories by name.
- The e2e runs on a temporary `FOUNDRY_HOME` with a guard.

**Pass 1 questions, decided by default:**
- `next_id` skips ids that already have runs.
- `new` and `tools` are reserved ids.
- A version id longer than 64 characters is refused.
- Hand edits are detected by the sha check.

**Pass 1 minors, applied:**
- **Request limits:** the pydantic request models carry them, so oversized bodies get 422 (rewritten to 400 JSON by the existing handler) before reaching the store.
  - `prompt: str = Field(min_length=1, max_length=20000)`
  - `stages: list[list[Annotated[str, Field(max_length=193)]]]`, with `max_length=12` on both list levels
  - `prompt_id: str = Field(max_length=64)`
- **Tool suggestions:**
  - A step with no server suggests its bare `name`.
  - Each tool is suggested both as `name` and as `server/name`, so the existing bare-name style of `expected.jsonl` is easy to keep.
- **Edit page states:**
  - **Loading:** header placeholders ("Loading…") plus skeletons.
  - **Fetch error:** an error banner with Retry.
  - **`/prompts/new` when `next_id` can't load:** the id field starts empty, with the hint "Pick an id, like adb-007".
- **Wireframe copy:** the "6 prompts" count in section 01's caption and the button label "Make a new version → <next version id>" are intended, as approved.
- **Added tests:**
  - `test_steps_limits` (13 steps, 13 tools, or a bad tool name → 400)
  - `test_prompt_view_runs_latest_20`
  - a mixed-rubric fixture in `test_prompts_list_counts_and_mean`
  - `test_scripted_config_with_script_allowed`
  - `test_store_errors_use_relative_paths` (an invalid write's message names `tasks/adb/prompts.jsonl`, not the temporary file or an absolute path)
  - `test_store_keeps_other_lines_verbatim` (an extra key and a blank line survive)
  - `test_hand_edit_detected`

**Pass 2 blockers, resolved.** This section is authoritative where it differs from text above.

1. **One root for reads and writes: `st.root`.**
   - Every UI-side read of prompts and steps uses `st.root/"tasks"/<task>/…` through `tasks._parse_prompts(path)` and `tasks._parse_expected(path)`. That covers the session's `prompts`, `_prompt_ids`, `start_run` and `start_batch`'s active check, `prompts_view`, `tool_suggestions`, and the P10 script path, which resolves `model.script.format(prompt_id=…)` against `st.root`.
   - `active_prompts(task_id)` is used only by the CLI, and resolves through `project_root()` as today.
   - `foundry ui` already passes `create_app(…, project_root())` (`cli.py:163`), and spawned jobs get `FOUNDRY_HOME=st.root` (`app.py:501`). So the UI and its subprocesses read the same files.
   - **Tests:** every prompts test uses `_project(tmp_path)`, a copy of `tasks/`, as the app root, with `monkeypatch.setenv("FOUNDRY_HOME", str(root))`. An autouse fixture in `tests/test_prompts.py` records the sha256 of the real `tasks/adb/prompts.jsonl` and `expected.jsonl` before each test, and asserts they're unchanged afterwards.
2. **Validation returns 400 with `field`, never 422.**
   - The request models carry no length or shape constraints beyond the types: `prompt: str`, `prompt_id: str | None`, `stages: list[list[str]]`, `archived: bool`.
   - The store enforces every limit and raises `UiError(400, msg, field)`:
     - empty or over-20,000-character text → `prompt`
     - a bad, reserved, duplicate or over-64-character id → `prompt_id`
     - more than 12 steps or 12 tools per step, an empty step, or a bad tool name → `stages`, with the message naming the step number
   - A wrong JSON type still gets the existing 422.
3. **One shared run count.**
   - `actions.run_count(st, task, id) -> {"runs": int, "pending": bool}` counts run directories whose readable `run.json` names the prompt, plus unreadable run directories whose name contains `_<id>_`. `pending` is true when a live UI run job or a live batch job lists the id.
   - This count feeds the cards (`runs`), the edit page (`runs`, `locked = runs > 0 or pending`) and `set_text`.
   - The cards' `mean` still comes from the board's active-rubric shares.
   - **The P6 409 message has two forms:**
     - "adb-005 has 23 runs, so its text can't change. Make a new version instead."
     - "adb-005 has a run in progress, so its text can't change. Try again when it finishes, or make a new version."
   - **Batch jobs record their prompts:** `Job` gains `prompts: list[str] | None = None`, set by `start_batch`.
   - **`set_text` holds `st.lock`** across the check and the write, so a `start_run` can't slip in between.

**Pass 2 questions, decided:**
- **Archived prompts** follow the same edit rules as active ones (text editable only with no runs; steps always editable).
- **`foundry batch --prompts all` from the CLI** still includes archived prompts. The CLI is unchanged (spec non-goal), and this is noted in the README's Prompts paragraph.
- **Saving steps for an id not in `prompts.jsonl`** returns 404.
- **Orphan steps:** `next_id` also skips ids that already have a line in `expected.jsonl`. Creating a prompt whose id has such an orphan line is refused (400, `prompt_id`): "adb-009 already has expected steps from an earlier prompt; pick another id."

**Pass 2 minors, applied:**
- **`GET /api/prompts/{id}` returns:**
  - `prompt`: `{prompt_id, prompt, archived, stages}`
  - `runs_total`, `scored`, `mean`, `pending`, `locked`, `next_version_id`
  - `runs`: the latest 20 as `{run_id, model, overall, cost, started_at}`, newest first
- **`prompts_view` returns:** `{prompts, next_id, root_name}`.
- **`copy_version`:**
  - It writes `expected.jsonl` first, then `prompts.jsonl`. If the second write fails, it restores `expected.jsonl` from the bytes it read.
  - Version ids skip any `-vN` that has runs or steps.
- **Verbatim copy:**
  - Untouched lines keep their own line endings.
  - An append first adds `\n` if the file doesn't end with one.
  - The file mode is copied to the temporary file (`shutil.copymode`) before `os.replace`.
- **Cards:** each card is a single `<Link>` to `/prompts/<id>`. "Edit" is a visual label inside it, not a second link, so there are no nested anchors.
- **`next_id`** uses `<task>-` plus three digits, growing to four after 999.
- **Stale-lock race:** this is inherited from `RubricStore._lock`. It's accepted for a single-user local tool, and noted.

**Pass 3 (the last allowed pass): two blockers, taken to the user with proposed fixes. The user approved them (2026-10-31): "Yes, build with these fixes".**
1. **Line separators in text.** The store writes each line with `json.dumps(obj, ensure_ascii=False)`, then replaces U+2028, U+2029 and U+0085 with their `\uXXXX` escapes, so `str.splitlines()` in `load_prompts` never splits inside a line. It's tested with `test_text_with_line_separators_roundtrips`.
2. **`tests/test_ui.py::test_failed_job_banner`** gets its exit-2 failed start from a script file that exists but is invalid JSON, instead of a missing path, so the new P10 pre-check doesn't intercept it.

**Pass 3 defaults applied:**
- **Ids with runs:** `create` refuses an id that already has runs (400, `prompt_id`): "adb-009 already has runs from an earlier prompt; pick another id".
- **Reserved ids:** `.` is reserved alongside `new` and `tools`.
- **New run dialog:** it resets its selected prompt when that prompt is no longer in `session.prompts`.
- **Archived prompts:** `start_run` and `start_batch` refuse one with "adb-007 is archived. Unarchive it on the Prompts page to run it."
- **Paths in errors:** every `ConfigError` or `RubricError` message from `start_run`, `start_batch`, the session and the new routes passes through `_rel`.
- **Locking:** `run_count` reads `st.jobs` without taking `st.lock` (as existing code does), because `set_text` already holds it.
- **The Security section's body-size sentence is wrong:** `/api` has no JSON body size limit. It's accepted for a localhost tool behind CSRF.
- **`active_prompts`** is dropped, because it has no consumer.
- **`archived`** counts only when it is exactly `true` (`is True`).
- **Duplicates:** the duplicate-id 400 names the duplicate id and its line numbers.
- **Unreadable runs:** counting them by `_<id>_` in their directory name can over-count. That's accepted, because it only errs toward locking.
