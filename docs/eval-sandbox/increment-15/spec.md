# Increment 15: full tool menu, run panel heading, isolated e2e, Setup menu — Spec

**Track:** Standard   **Date:** 2026-10-31   **Status:** Built (verified 2026-10-31)

## Problem

Four things the user hit after increments 12–14:

1. **Tools:** the expected-steps editor only suggests tool names seen in past runs. You can't see every tool the agent has, or what a tool does, so steps get written from memory. Runs record only which servers they used (`run.json` `mcp_servers`), not their tools.
2. **Run panel:** the run panel's title (the prompt text) is a plain `<div>`. A screen reader user can't jump to it, and it doesn't name the panel (the increment 12 verify review left this as a follow-up).
3. **Test runs:** the main browser test (`scripts/ui_e2e.mjs`) runs against the live server, so every pass left an offline "scripted-a" run on the real board (18 so far; deleted at the user's request on 2026-10-31).
4. **Menu:** the top menu (Runs, Prompts, Batches, Dashboard, Rubric, Import) mixes setup pages, doing, and results in no clear order.

## Acceptance criteria

**Tool menu (T1–T4)**
- **T1:** `foundry tools [--out FILE]` lists every tool of every MCP server the configs in `config/` use. It starts each distinct server (by name, package and version) once and prints JSON `{"listed_at", "servers": [{"name", "package", "version", "error"}], "tools": [{"server", "name", "description"}]}`, then stops the servers. A server that fails to start appears with its `error`, and the others still list.
- **T2:** `GET /api/prompts/tools?task=adb` returns `{"tools": [{"name", "server", "description", "runs"}], "listed_at", "errors", "refreshing"}`. Every tool is listed:
  - the full menu from the last `foundry tools` listing, cached at `<runs dir>/.ui-tools.json`
  - plus any tool seen in a trace or in `expected.jsonl` that the menu lacks, with `description` null
  - `runs` is how many runs called the tool
  - with no cache yet, the first request starts the listing as a background job and returns the trace tools with `refreshing: true`
  - `POST /api/prompts/tools/refresh` starts a new listing (409 if one is running)
- **T3:** in the step editor, **+ tool** opens a picker:
  - every tool grouped by server, each with its description and "used in N runs"
  - a search box filtering by name or description
  - choosing a tool adds its bare name to the step, as `expected.jsonl` does today
  - a typed name that isn't listed can still be added
  - the picker says when the list was made and offers **Refresh tool list**
  - while a listing runs it says "Listing tools from the servers…"
- **T4:** a server that can't be listed shows "Couldn't list tools from <server>: <reason>" in the picker, and its tools seen in traces still appear.

**Run panel heading (H1)**
- **H1:** the run panel's title (the prompt) is an `h2` inside the panel. The panel (`aside`) keeps `aria-label="Run details"` as its accessible name, and the line above the heading (prompt id · model) is unchanged.

**Isolated browser test (E1–E2)**
- **E1:** `scripts/ui_e2e.mjs` starts its own `foundry ui` on a temp copy of `tasks/`, `config/` and `runs/` (batches included), with `FOUNDRY_HOME` set and the server on port 8798.
  - It refuses to continue unless the server reports the temp root.
  - It never needs, or touches, the live server.
  - It deletes the temp copy when it ends.
  - Its checks are unchanged: run, score, the Rubric tab, logo, clarity on every page.
- **E2:** after a full pass, the real `runs/` has the same run directories as before.

**Menu (M1–M3)**
- **M1:** the menu reads **Runs · Batches · Dashboard · Setup ▾**, and Setup opens **Prompts, Rubric, Import**. This is the user's pick from three arrangements.
- **M2:** on a Prompts, Rubric or Import page, the Setup button looks active and shows which page you're on ("Setup · Prompts"). Compare stays under Runs.
- **M3:** Setup works by keyboard and mouse:
  - it's a button with `aria-expanded` and `aria-haspopup`
  - Enter, Space or Down opens it; the arrow keys move between items; Escape closes it and returns focus to the button
  - a click outside or choosing an item closes it
  - it works the same in the phone-width menu row

**Tests (Q1)**
- **Q1:**
  - **pytest:** T1 against the in-repo `stac` server (no network); T2's merge, cache, refreshing and 409; the error case.
  - **Vitest:** the picker groups, filters, adds, and shows an error; the heading; the Setup menu's keyboard behaviour.
  - **e2e (isolated):** the picker on a prompt page shows descriptions; the Setup menu reaches each page; and E2.

## Non-goals

- Live re-listing on every request, or listing tools per prompt.
- Editing tool descriptions.
- Changing the format of expected steps: bare names stay the default, and `server/name` can still be typed.
- Moving Compare or New run.
- Removing the remaining "scripted-pipeline" runs (not asked).

## Assumptions and open questions

- **A1 (grounded):** each tool's description is in strands' `tool.tool_spec["description"]`, as returned by `MCPClient.list_tools_sync()`. Verified on 2026-10-31 against the in-repo `stac` server (strands-agents 1.62.0, mcp 2.1.1): its `tool_spec` keys are `description`, `inputSchema` and `name`.
- **A2:** listing starts the same servers a run starts. The `roda` server comes from `uvx awslabs.roda-mcp-server@0.1.7` and needs network on first use. The listing runs as a background job, so the page never waits on it.
- **A3 (user decision, 2026-10-31):** the menu is three main tabs plus a Setup dropdown. The 18 scripted-a runs were deleted at the user's request.
