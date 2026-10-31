# Increment 15 — Design

**Spec:** ./spec.md   **Last updated:** 2026-10-31   **Status:** Draft

## Overview

- **Tool menu:** a new CLI command, `foundry tools`, starts each distinct MCP server once (with the harness's own `start_servers`) and writes the full tool list with descriptions. The UI runs it as a background job, the same way it runs `foundry run`, and caches the result in the runs directory. The tools endpoint merges that menu with the tools seen in traces, and the step editor gets a searchable picker.
- **Run panel heading:** the panel title becomes an `h2`.
- **Isolated browser test:** the main e2e starts its own server on a temp copy, as `ui_e2e_prompts.mjs` already does.
- **Setup menu:** the top menu moves to three tabs plus an accessible Setup dropdown.

## Experience

### Design review

- **Menu:** approved (2026-10-31). The user chose "Main tabs + Setup menu" over "Workflow order, 3 groups" (recommended) and "Results first, setup last". Wireframe as approved:

  ```
  [F] Foundry / eval   Runs  Batches  Dashboard  Setup ▾          [▶ New run]
                                                    ├ Prompts
                                                    ├ Rubric
                                                    └ Import
  ```

  On a setup page the button reads "Setup · Prompts" (or Rubric, or Import) in the active tab style. The dropdown panel uses the existing white or dark rounded panel with a ring, and each item has a one-line caption under it:
  - **Prompts:** "What agents are asked"
  - **Rubric:** "How runs are scored"
  - **Import:** "Bring in runs from elsewhere"
- **Tool picker: design review skipped.** It extends the approved step editor ("+ tool" now opens a list instead of a free-text box) and adds no new page or layout. The user asked for exactly this: "the full tool menu and their descriptions". It shows up in the handoff screenshots. Layout:

  ```
  Step 1  any of: [search_datasets ×]  [+ tool ▾]
          ┌──────────────────────────────────────────────────────────┐
          │ [Search tools…                                         ] │
          │ roda                                                     │
          │   search_datasets       used in 41 runs                  │
          │   Search RODA datasets by keyword…                       │
          │   get_dataset_details   used in 39 runs                  │
          │   …                                                      │
          │ stac                                                     │
          │   search_items          used in 12 runs                  │
          │   Find scenes (STAC items) in one collection…            │
          │ ──────────────────────────────────────────────────────── │
          │ Listed 10-31 17:02 · Refresh tool list   + add "typed"   │
          └──────────────────────────────────────────────────────────┘
  ```

### States

- **The picker:**
  - **Loading:** "Loading tools…".
  - **Listing in progress:** "Listing tools from the servers…". It polls every 2 s until `refreshing` is false.
  - **Server errors:** an amber line per failed server: "Couldn't list tools from roda: <reason>".
  - **No match:** "No tool matches 'x'. Add it as typed: + add 'x'".
  - **Keyboard:** Escape closes it, and the arrow keys move through the options.
- **The Setup menu:** closed, open, and the active-page state.

## Architecture

**`src/foundry/harness/tools.py` (new)**
- `list_tools(configs: list[Path], log_dir) -> dict`.
- It loads each config (`load_config`) and collects `cfg.mcp.servers`, de-duplicated by `(name, package, version)`.
- For each server, it calls `start_servers({name: cfg}, cfg_limits.mcp_start_timeout_s, log_dir)` and reads `tool.tool_name` and `tool.tool_spec.get("description")`, then `stop()`. An exception is recorded as `error`, and the other servers carry on.
- It returns the T1 JSON. Descriptions are trimmed to 600 characters.

**`src/foundry/cli.py`**
- A new subcommand: `foundry tools [--out FILE]`.
- It reads every `config/*.toml` under `project_root()` that loads, and writes JSON to `--out`, or prints it.
- It writes atomically: a temp file, then `os.replace`.

**`src/foundry/ui/actions.py`**
- `tool_suggestions` becomes `tool_menu(st, task)`:
  - It reads `st.runs_dir / ".ui-tools.json"` if it's a regular file holding valid JSON, otherwise `None`.
  - It counts runs per tool from `_entries` (bare name, and the server from `step.server`).
  - It merges the two, keyed by `(server, name)`. A trace tool that the menu lacks is added with `description: None`. Tools in `expected.jsonl` that are in neither are added with `server: None`.
  - Results are sorted by server, then name.
  - It returns `{tools, listed_at, errors: [{server, error}], refreshing}`.
- If there is no cache and no live `tools` job, it starts one (`refresh_tools`).
- `refresh_tools(st)`: under `st.lock`, it returns 409 if a `tools` job is alive. Otherwise it calls `core._spawn(st, "tools", "tools", "Tool listing", ["tools", "--out", str(st.runs_dir / ".ui-tools.json")])`.

**`src/foundry/ui/api.py`**
- `GET /api/prompts/tools` returns `tool_menu`.
- `POST /api/prompts/tools/refresh` (write guard) returns `{"ok": true}`, or 409.

**Toasts:** a `tools` job's finish isn't toasted, so `jobToasts` skips `kind === "tools"`.

**Web**
- `components/ToolPicker.tsx`: the popover. It has a search input, a list grouped by server, and a footer. Its accessibility follows the WAI-ARIA combobox and listbox pattern: an `input` with `role=combobox`, `aria-expanded` and `aria-controls`; the list has `role=listbox` and each option `role=option`, with `aria-activedescendant` tracking the highlighted one.
- `StepEditor` swaps its `<input list>` for `ToolPicker` and receives `tools` as objects.
- `PromptEdit` polls `/api/prompts/tools` every 2 s while `refreshing` is true.
- `Shell.tsx`: `NAV` becomes the main tabs plus a `SETUP` list. A `SetupMenu` component follows the WAI-ARIA menu button pattern: a button with `aria-haspopup="menu"` and `aria-expanded`, a list with `role="menu"`, and links with `role="menuitem"`. It handles Escape, the arrow keys, and closing on a click outside. The active test is `pathname.startsWith` any setup route.
- `Runs.tsx` `RunDrawer`: the title `div` becomes `<h2 id={headingId}>`, and the `aside` keeps `aria-label="Run details"`.

**Isolated browser test**
- `scripts/ui_e2e.mjs`: a `startServer()` copies `tasks/`, `config/` and `runs/` (including `.batches`) into `mkdtemp`. It spawns `uv run foundry ui --port 8798 --runs-dir <tmp>/runs` with `FOUNDRY_HOME=<tmp>`.
- The guard: `/api/prompts` must report `root_name` equal to the temp directory's name.
- The URL argument is dropped, and the screenshot directory becomes the first argument.
- Cleanup waits for the processes, then deletes the copy, as in the prompts e2e.
- `fail` throws.

## Data and contracts

- **The `/api/prompts/tools` response changes shape.** `tools` was `string[]` and is now an array of objects, and the response gains `listed_at`, `errors` and `refreshing`. The only consumer is `PromptEdit`/`StepEditor`, changed in the same commit. Increment 13 shipped yesterday on this branch, so there are no other clients.
- **New endpoint:** `POST /api/prompts/tools/refresh`.
- **New file:** `<runs dir>/.ui-tools.json`. It's a regenerable cache, and the runs directory is ignored by git.
- **New CLI subcommand:** `foundry tools`.
- **`Job.kind`** gains `"tools"`.

## Decisions

### ADR-1: List tools in a background CLI job, not inside the web process
- **Options:**
  - A) a `foundry tools` subprocess spawned like runs
  - B) start the MCP servers inside the FastAPI process on request
  - C) record each run's tool list in `run.json` from now on
- **Decision:** A, with C as a possible follow-up.
- **Why:**
  - A reuses `start_servers`, including its reaper wrapper, so no MCP server can outlive or block the web server. A request never waits on `uvx`, and it uses the same job, log and failure surfacing as runs.
  - B ties server lifetimes to request threads.
  - C only helps future runs.
- **Reversibility:** easy.
- **Sources:**
  - `src/foundry/harness/mcp.py:66-97` (`start_servers`, `list_tools_sync`)
  - spec A1, verified on 2026-10-31

### ADR-2: Insert bare tool names
- **Decision:** the picker inserts bare names. The existing `expected.jsonl` uses bare names, and `match_stages` accepts a bare name from any server (`workflow.py:20-31`). Typing `server/name` still works.
- **Reversibility:** easy.

### ADR-3: The e2e copies the real runs
- **Options:**
  - A) a copy of `runs/`
  - B) an empty runs directory
- **Decision:** A.
- **Why:** the clarity stage checks pages with real data (a batch, a scored run, Compare). The copy is 4.6 MB, measured on 2026-10-31.

## Failure modes

| Failure | Behavior |
|---|---|
| A server fails to start (no network for `uvx`, a bad command) | Its `error` is recorded in the cache, the picker shows it, its trace tools still show, and Refresh retries. |
| The listing job crashes or hangs | The job ends with a non-zero exit. With no cache file written, the next request shows `refreshing: false` and `errors: [{server: "*", error: "The tool listing failed; see the job log"}]`, and Refresh is offered. A hang is bounded by `mcp_start_timeout_s` per server. |
| The cache is corrupt or a symlink | Ignored, treated as no cache, and a listing is started. |
| Two refreshes at once | The second gets 409. |
| The e2e can't start its server | It fails with "the server didn't start" and cleans up. |

## Security and privacy

- **Refresh** is a write (CSRF header, Origin and Host checks). It spawns a fixed command with no user input.
- **The cache file** is read only if it's a regular file (`core._regular`), parsed as JSON, and type-checked field by field.
- **Descriptions** come from the servers and are untrusted. They render as React text only, trimmed to 600 characters.
- **The e2e** never writes to the real project, because of the guard.

## Migration and rollback

- There's no data migration. Reverting removes the command and the picker; the stale `.ui-tools.json` is harmless.

## Traceability

| AC | Component | Test |
|---|---|---|
| T1 | `harness/tools.py`, `cli.py` | pytest `test_list_tools_from_stac` (the real in-repo `stac` server: names and descriptions); `test_list_tools_records_server_error` (a config with a bad command gives `error`, and the others still list) |
| T2 | `tool_menu`, `refresh_tools`, api | pytest `test_tool_menu_merges_cache_and_traces`, `test_tool_menu_starts_listing_when_uncached`, `test_tools_refresh_409_when_running` |
| T3, T4 | `ToolPicker`, `StepEditor` | Vitest "tool picker" (it groups, filters by description, adds on Enter or click, adds a typed name, shows a server error, and shows refreshing); e2e opens the picker on `/prompts/adb-001` and sees a description |
| H1 | `RunDrawer` | Vitest: the run panel has a level-2 heading with the prompt text |
| E1, E2 | `ui_e2e.mjs` | The e2e itself; E2 is checked by the before and after listing of `runs/` in Verify |
| M1–M3 | `SetupMenu` | Vitest "setup menu" (it opens on click and Enter, moves with the arrow keys, closes on Escape with focus back on the button, and shows "Setup · Prompts" on `/prompts`); e2e navigates to Prompts, Rubric and Import through it at 1440 and 375 px |
| Q1 | all of the above | |

## Implementation plan

1. `harness/tools.py` and `foundry tools`, with pytest against `stac`.
2. `tool_menu`, refresh and the API, with pytest.
3. `ToolPicker` in `StepEditor`, and the polling in `PromptEdit`, with Vitest.
4. The run panel `h2`, the Setup menu, and the toast skip, with Vitest.
5. `ui_e2e.mjs` moves to its own server on a copy, with picker and Setup menu checks. Then the full suites, screenshots and the handoff.


## Design gate record

**Pass 1 blockers, all resolved. This record is authoritative where it differs from text above.**

1. **When a listing starts.**
   - `tool_menu` starts a listing only when there's no cache **and no `tools` job yet in this UI session**, whether alive or finished. So a failed listing is reported once and never retried on every request.
   - **When the last `tools` job exited non-zero,** `errors` gets `{server: "*", error: "The last tool listing failed; see the job log. Refresh to try again."}`, plus " Showing the list from before." when an older cache exists.
   - **Exit codes:** `foundry tools` exits 0 when it wrote the list, even if some servers failed, and 1 only when every server failed.
   - **Job banners:** a failed `tools` job appears in `/api/jobs` and in the classic UI's job banners like any job, but it isn't toasted.
2. **Matching tools by name.** Entries merge as follows:
   - an exact `(server, name)` match comes first
   - otherwise, a trace step with no server (imported runs, `contract.py`'s `server: str | None`), or a bare name in expected steps, merges into the only menu entry with that bare name
   - `server/name` in expected steps is split before matching
   - anything still unmatched is added with no description, and the picker groups it under "other"

   This is tested by `test_tool_menu_matches_serverless_and_prefixed_names`, which really rewrites the fixture trace to `"server":null`.
3. **Tests never start a live listing.**
   - Every pytest either seeds `<runs>/.ui-tools.json` (`test_steps_through_api_and_tools`) or stubs `_spawn` (`tests/test_tools.py`'s `ui` fixture).
   - Both e2e scripts seed `scripts/e2e/tools-cache.json` into their temp runs dir.
   - Updated for the new shape: `test_prompts.py::test_steps_through_api_and_tools`, the Vitest StepEditor tests (the picker keeps the `Add a tool to step N` label and an **Add** button, so typing then Add still works), and `ui_e2e_prompts.mjs`.
4. **The phone-width menu.** The phone-width menu row is now `flex flex-wrap`, not `overflow-x-auto`. Four items fit at 375 px, so the dropdown, positioned absolutely under its button, is never clipped. The e2e reaches every setup page through it at 375 px.
5. **E2 is checked by the test itself.** `ui_e2e.mjs` records the names in the real `runs/` at the start and fails if they differ at the end.

**Pass 1 questions:**
- **H1:** the panel keeps `aria-label="Run details"`, and the `h2` sits inside it. The spec wording is corrected.
- **The captions under the Setup items** ("What agents are asked", and so on) are an addition to the approved wireframe. They're declared here and will be told to the user in the summary.
- **MCP logs from `foundry tools`** go to a temporary directory that is deleted afterwards (`cli.py`), never into the project.

**Pass 1 minors, applied:**
- **Setup menu:**
  - focus moves to the first item on open
  - Home and End work, and Tab closes the menu
  - items take `tabindex=-1`
  - ids come from `useId`, because the desktop and phone instances coexist
- **ToolPicker:** each server group is `role="group"` with a label, the input keeps focus on Escape, and Enter with nothing highlighted adds the typed text.
- **The cache is untrusted:** descriptions are trimmed to 600 characters when read too.
- **Refresh:** after a refresh, `tools.reload()` restarts the poll.
- **The e2e copy of `runs/`** leaves out `.ui-jobs`, lock files and the real `.ui-tools.json`.
- **README:** the e2e's arguments are updated.
- **`task`:** `tool_menu` ignores it, because the cache covers every config.
- **Name collisions:** two servers with the same name but different versions would collide on `server`. Today there are none, so this is recorded as a known limit.
- **The GET side effect:** a GET can start one listing per UI session (a fixed command, no input). A cross-site request could trigger it at most once, so the risk is accepted as low.

**Pass 2 blockers, both resolved.**
1. **Picker keyboard.** Nothing is highlighted until an arrow key is pressed (`active = -1`). Enter then works as follows:
   - with a highlighted tool, it picks that tool, whether or not you typed anything
   - otherwise, it adds the typed text, so a partial match like "items" is never swapped for a listed tool
   - Escape clears the highlight

   Vitest covers it: arrow then Enter, a keyboard-only pick with no typing, and a partial name kept as typed.
2. **The phone-width Setup menu.** When the menu opens, it measures its button and anchors to the right if a 15rem (240 px) panel fits there, otherwise to the left. It's also capped at `100vw - 2rem`. The e2e now opens the menu from `/` and from setup pages (where the label is longer and can wrap), at 1440 and 375 px, and **fails if the panel's bounding box leaves the viewport**.

**Pass 2 minors, applied:**
- The picker shows "Loading tools…" before the first response.
- `errors` and `listed_at` are tolerated when missing.
- From the cache, `listed_at` is accepted only as a string, and server names and errors are trimmed.
- `foundry tools` gives up after 10 minutes (`SIGALRM`), so a hung `list_tools_sync` can't leave the UI "refreshing" for the whole session. The harness's reaper takes the servers down with it.
- E2 compares run directories only, ignoring dot-files that the live server may write meanwhile.
- The guard-failure path waits for the server to exit before deleting the temp copy.
- Vitest covers the refreshing and loading states, and the Setup menu opening by click and closing on a click outside.

**Accepted as they are:**
- The two e2e scripts share ports, so they run one after the other, not in parallel.
- The picker footer reads "Listed 2026-10-31 17:00 UTC", the full date with the time zone, where the wireframe shows "Listed 10-31 17:02".
- The classic UI shows a failed listing as a job banner, as intended.

**Pass 3 (the last allowed pass): one blocker, taken to the user with the fix applied. The user accepted it (2026-10-31): "Accept, verify and ship".**
- **The blocker:** a listing in which every server fails no longer overwrites the cached list. `foundry tools --out` keeps the previous file and exits 1, so "Showing the list from before" is true. It's tested by `test_failed_listing_keeps_the_previous_list` (the old file is byte-identical, and no temp file is left behind).

**Pass 3 minors, applied:**
- The keyboard-pick test now picks a tool that isn't already in the step.
- The e2e finds Chrome before starting its server, and prints "real runs/ unchanged" only after the comparison.
- A failed tool-list request shows as a picker error instead of "Loading tools…" forever.
- The picker anchors right when it would run off screen.
- `aria-controls` is set only while the list is open.
- Tool names and servers from the cache are capped.
