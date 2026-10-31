# Increment 15: full tool menu, run panel heading, isolated e2e, Setup menu — Handoff

**Spec:** ./spec.md   **Design:** ./design.md (its "Design gate record" is authoritative)   **Date:** 2026-10-31   **Track:** Standard

## What shipped

1. **The full tool menu, with descriptions.**
   - A new `foundry tools` command starts each MCP server the configs use (once each), lists every tool with its description, and stops them.
   - The UI runs it in the background the first time a prompt page needs it, and caches the result in `runs/.ui-tools.json`.
   - The step editor's **+ tool** box is now a searchable picker: every tool grouped by server, with its description and "used in N runs" (or "not used yet"), a search that matches names and descriptions, "Refresh tool list", and per-server errors. Typing any other name still works.
   - **Live on your server:** 13 tools (10 from `roda`, 3 from `stac`), all with descriptions, listed in about 20 seconds.
2. **The run panel heading.** The run panel's title (the prompt) is now a level-2 heading, so screen readers can jump to it. The panel keeps its "Run details" name.
3. **The browser test no longer touches your board.**
   - `scripts/ui_e2e.mjs` starts its own server on a temp copy of `tasks/`, `config/` and `runs/`, and refuses to go on unless that server reports the temp copy.
   - It checks at the end that your real `runs/` has the same run directories.
   - It's run as `node scripts/ui_e2e.mjs [screenshot-dir]`, without a URL.
   - **Why the scripted-a runs appeared:** the test proved "start a run, then score it" on whatever server it was pointed at, which was yours. **The 18 scripted-a test runs were deleted** at your request, and the board went from 83 to 65 runs.
4. **The menu, your pick.** It reads **Runs · Batches · Dashboard · Setup ▾**, and Setup holds Prompts, Rubric and Import.
   - On a setup page the button reads "Setup · Prompts" and looks active.
   - Keyboard: Enter, Space or Down opens it; the arrow keys, Home and End move; Escape closes it and returns focus; Tab and a click outside close it.
   - It anchors itself so it stays on screen at phone width.

## Decisions

- **The user chose:**
  - "Main tabs + Setup menu", over workflow order (recommended) and results first
  - delete all 18 scripted-a runs
- **A difference from the picker wireframe:** the "+ tool" box is itself the search box, with no second "Search tools…" field inside the panel. One box searches, picks and accepts typed names.
- **Additions to the approved menu wireframe:** a one-line caption under each Setup item ("What agents are asked", "How runs are scored", "Bring in runs from elsewhere").
- **Tool picker: no separate design review.** It extends the approved step editor and is exactly what you asked for. The screenshots are below.
- **How the listing runs** (ADR-1): as a background CLI job, not inside the web server. It reuses the harness's server start, including its reaper, so a server can't outlive or block the UI.
  - It lists once per UI session.
  - A failed listing is reported, not retried on every request.
  - It gives up after 10 minutes.
- **Bare tool names** are inserted, as `expected.jsonl` already uses them, and `server/name` can still be typed. Tools seen in a trace without a server, or named as `server/name` in steps, merge into the matching menu entry instead of showing twice.
- **The design gate ran 3 passes.**
  - Pass 1's five blockers were fixed: listing once, matching tools by name, tests never starting a live listing, the phone menu, and E2 checked by the test itself.
  - Pass 2's two blockers were fixed: picker keyboard selection, and the phone menu running off screen.
  - Pass 3's one blocker went to you with the fix applied, and you accepted it: a failed refresh no longer wipes the cached tool list.

## Verification

**Suites:**
- Python: 352 passed (10 new in `tests/test_tools.py`).
- Vitest: 26 passed (5 new).
- ruff and `tsc` are clean.

| AC | How it was proven |
|---|---|
| T1 | pytest `test_list_tools_from_stac`: the real in-repo `stac` server, listed once across two configs, with names and descriptions. `test_list_tools_records_server_error`: a server whose command doesn't exist gets `error`, the others still list, and a config that won't load is skipped. **Live:** `foundry tools` against the real servers gave 13 tools, no errors. |
| T2 | pytest `test_tool_menu_merges_cache_and_traces` (the full menu including unused tools, run counts, trace-only tools, per-server errors); `test_tool_menu_starts_listing_when_uncached` (one listing, never two); `test_tools_refresh_409_when_running` (CSRF, 409, a failed listing reported and not relisted); `test_failed_listing_keeps_the_previous_list` (a refresh that lists nothing leaves the old file byte-identical); `test_tool_menu_matches_serverless_and_prefixed_names`, which rewrites the fixture trace to `"server":null` and asserts it does so. **Live:** your server listed in the background and then served 13 described tools. |
| T3, T4 | Vitest "tool picker":<br>• tools are grouped by server, with descriptions and "used in 41 runs" or "not used yet"<br>• search matches descriptions<br>• arrow then Enter picks; a keyboard-only pick needs no typing<br>• a partial name is kept as typed; a name that matches nothing is added as typed<br>• a server error shows; the listing date shows<br>Vitest also covers the loading and listing states, with Refresh disabled. **e2e:** the picker on `/prompts/adb-001` shows `list_collections` with its description, and at 375 and 320 px the panel stays inside the screen. Screenshots: [tool-picker-dark](screenshots/tool-picker-dark.png), [tool-picker-375-dark](screenshots/tool-picker-375-dark.png). |
| H1 | Vitest: the "Run details" panel contains a level-2 heading named with the prompt. |
| E1, E2 | `scripts/ui_e2e.mjs`: its own server on a copy, with the guard. It ends with "real runs/ unchanged" and PASS. The run count was 65 before and after, and no temp directories were left behind. |
| M1–M3 | Vitest "setup menu": the "Setup · Prompts" label on `/prompts`, `aria-haspopup`, Down opening it with focus on the first item, the arrow keys, Escape returning focus, opening by click, and closing on a click outside. **e2e:** it reaches Prompts, Rubric and Import through the menu from `/` and from setup pages, at 1440 and 375 px, and **fails if the menu's box leaves the viewport**. Screenshots: [setup-menu-dark](screenshots/setup-menu-dark.png), [setup-menu-light](screenshots/setup-menu-light.png). |
| Q1 | All of the above. The prompts e2e passes too, now seeded with a tool list. The clarity stage passes on 15 pages, 5 tabs and the dialog at both widths and in both themes. |

**Exercised live:** yes.
- The real `foundry tools` ran against `uvx` roda and the in-repo `stac` server.
- Your restarted server listed tools in the background, then served them.
- Both browser tests ran on their own servers and copies.

## Verify review

**The independent pass:**
- **It re-ran everything and all passed:** every suite, both e2e scripts (one after the other), and `foundry tools` on a temp `FOUNDRY_HOME`.
- **A hostile tool-list file was handled safely:** wrong types, a 99,000-character description, an HTML-looking name, a file that isn't a dict, and a symlink. Every field was capped, and an invalid file was ignored.
- **Nothing was touched:** the real run directories, `tasks/` and the real tool list kept the same hashes before and after, and no ports or temp copies were left behind.

**It found, now fixed:**
- **(Blocking) The picker ran off the left edge at 375 px** and cut off the start of every tool name. It now uses the panel's real width and shifts it just enough to stay 16 px inside the screen. The e2e opens it at 375 and 320 px and fails if it leaves the screen.
- **A duplicate entry, depending on order:** a tool seen first without a server, then named as `server/name` in steps, gave two entries. They now merge (`test_tool_menu_serverless_first_then_prefixed_is_one_entry`).
- **An e2e cleanup gap:** if Chrome's DevTools endpoint never answered, the e2e left its server and temp copy behind. It now stops them and cleans up.

**Accepted as they are:**
- At 375 px on a setup page, "Setup · Prompts" wraps to its own row. The menu still stays on screen, as the e2e checks.
- The "*" error mentions the job log without linking it; Refresh is the way forward.
- `foundry tools` uses `SIGALRM`, which works on macOS and Linux only.
- After you choose a Setup item, focus lands on the new page's body, as with any route change.

## Follow-ups

- **Record each run's tool list in `run.json`** (ADR-1 option C), so the menu could also show which tools existed at the time of a run.
- **A live listing per prompt or task** isn't needed today, because every config uses the same two servers.
- **The two "scripted-pipeline" runs** from increment 7 remain on the board (not asked about).
