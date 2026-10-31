# Increment 13: manage prompts in the UI — Handoff

**Spec:** ./spec.md   **Design:** ./design.md (its "Design gate record" section is authoritative)   **Date:** 2026-10-31   **Track:** Standard

## What shipped

- **A Prompts tab** (`/prompts`): every prompt as a card, with its id, average score (active rubric), the start of its text, its run count and its number of expected steps. Active prompts are in section 01, archived ones in section 02. Layout: cards plus a separate edit page, as the user picked and approved.
- **A prompt's page** (`/prompts/<id>`):
  - **01 Text:** editable while the prompt has no runs. Once it has runs (or one is in progress), the text is locked and **Make a new version → adb-005-v2** copies the text and steps to a fresh id.
  - **02 Expected tool steps:** an ordered list of steps, each "any of" one or more tools. Tools can be added from suggestions (every tool seen in a trace) or typed, steps can be moved up or down and removed, then **Save steps**.
  - **03 Runs of this prompt:** the latest 20.
  - **Archive this prompt** or **Unarchive**.
- **New prompt** (`/prompts/new`): the id is prefilled with the next free one, and errors show next to their field.
- **Runs and batches refuse archived prompts** with a clear message. An offline (scripted) config that has no script for a prompt is refused up front ("The offline config 'offline' has no script for adb-007. Pick a Bedrock config.").
- **One store owns every write** (`foundry.prompts.store`):
  - a pid lock, a temp file plus `os.replace`, and validation with the runner's own parsers before replacing anything
  - lines it doesn't touch are copied byte for byte
  - a hand edit made during a save is detected and refused

## Decisions

- **The user chose:**
  - list, add, edit and archive, never delete
  - lock the text once a prompt has runs, and offer a new version
  - expected steps editable in the UI
  - cards plus a separate edit page
- **The design gate ran 3 passes, the maximum.** The last pass's two blockers went to the user with fixes, and the user approved them:
  - text with line-separator characters (U+2028, U+2029, U+0085) is written escaped
  - the failed-start test uses an invalid script rather than a missing one
- **Fixed along the way:** the classic `_prompt_ids` and `start_run` read prompts through `project_root()` instead of the UI's own root. Every UI read now uses `st.root`, so the UI, its spawned jobs (`FOUNDRY_HOME`) and its writes always see the same files.
- **Additions beyond the wireframes, for states the wireframes didn't show:**
  - "Showing the latest 20 of N" under a long run list
  - "Locked while a run is in progress" when the lock comes from a live job
  - an empty state for a prompt with no runs

## Verification

**Suites:**
- Python: 344 passed (20 new in `tests/test_prompts.py`; `test_ui.py::test_failed_job_banner` was updated, as approved).
- Vitest: 22 passed (4 new).
- ruff and `tsc` are clean.

**Guard:** an autouse fixture asserts the real `tasks/adb/prompts.jsonl` and `expected.jsonl` are byte-identical after every prompts test, and the prompts e2e runs on a temp copy (below). After everything, `git status tasks/` was clean.

| AC | How it was proven |
|---|---|
| P1, P2 | pytest `test_prompts_mean_uses_active_rubric_only`: a run scored with another rubric counts as a run but not in the mean, and the mean equals the active-rubric share. pytest `test_prompts_list_counts_and_create`: 6 prompts, adb-001 counts its fixture run, `next_id` is adb-007, and `root_name` is checked. Vitest "groups active and archived prompts": the card shows "2 runs · 1 expected step", and the "+ New prompt" link is checked. **The prompts e2e** sees the new prompt's card, then sees it under Archived. Screenshots: [prompts-dark](screenshots/prompts-dark.png), [prompts-light](screenshots/prompts-light.png). |
| P3 | pytest `test_create_and_errors`:<br>• a duplicate id, a reserved id (`new`, `.`), a bad id (`a/b`, `a..b`, 65 characters), empty text, and text over 20,000 characters are each refused with 400 and their own field<br>• an id with runs is refused<br>The API test checks `field` in the JSON. **e2e** creates adb-007 through the page, with the id prefilled. |
| P4 | Vitest "locks the text of a prompt with runs": the text box is read-only, "Locked: 23 runs used this text." shows, the button reads "Make a new version → adb-005-v2", and there's no Save. pytest checks `next_version_id`, and `test_prompt_view_runs_latest_20` checks that 22 runs give a total of 22, list 20, and list them newest first. Screenshot: [prompt-edit-dark](screenshots/prompt-edit-dark.png). |
| P5 | pytest `test_new_version_copies_text_and_steps`:<br>• adb-001-v2 copies the text and steps<br>• versioning v2 gives v3 (counting from the base id)<br>• a `-vN` that has runs is skipped<br>• an id too long to version is refused |
| P6 | pytest `test_prompt_text_locked_with_runs`: 409 with exactly "adb-001 has 1 run, so its text can't change. Make a new version instead.". A prompt without runs saves. |
| P7 | pytest `test_archive_hides_from_session_and_refuses_runs`:<br>• the archived prompt leaves the session's prompts<br>• runs and batches give 400 "adb-002 is archived"<br>• the CLI's `load_prompts` still resolves it<br>• unarchive restores it<br>**e2e:** after archiving, the prompt is gone from the New run picker. |
| P8, P9 | pytest `test_steps_roundtrip_limits_and_removal`:<br>• saved steps parse back with the returned `expected_sha256`<br>• duplicates within a step are dropped<br>• limits are enforced: 13 steps, 13 tools, an empty step, a bad name<br>• zero steps removes the line<br>• an unknown id gives 404<br>pytest `test_steps_through_api_and_tools` checks the suggestions (bare and `server/` names) and the 400 with `field`. Vitest StepEditor covers add, move and remove, then save. **e2e** saves two steps through the page and checks them through the API. |
| P10 | pytest `test_scripted_config_without_script_refused`: runs and batches are refused with the exact message. `test_scripted_config_with_script_allowed`: a prompt that has a script starts (201). **e2e** shows the message in the New run dialog. |
| P11 | pytest:<br>• `test_store_lock_and_atomic`: a live lock gives 409, a dead lock is cleared, and no temp or lock file is left behind<br>• `test_store_keeps_other_lines_verbatim`: an extra key, CRLF and a blank line survive, only one line changes, and the file is byte-identical after archive then unarchive<br>• `test_hand_edit_detected`: 409, and nothing is written<br>• `test_text_with_line_separators_roundtrips` |
| P12 | pytest `test_prompts_api_lock_bytes_echo_and_origin`, through the API:<br>• a held lock gives 409<br>• a refused write leaves the file byte-identical<br>• `<script>` text comes back as JSON<br>• a bad Origin gives 403, and a bad Host gives 400<br>• a lone surrogate gives 400 with its field<br>pytest `test_prompts_must_deny`:<br>• no CSRF gives 403, and `text/plain` gives 415<br>• traversal ids (`a..b`, encoded `../`, over 64 characters) give 404<br>• a bad task gives 400<br>• a corrupt file's error names `tasks/adb/prompts.jsonl:7` with no absolute path, and so does the `start_run` error<br>`test_store_errors_use_relative_paths` checks the store's message, and that a duplicate id is named with its line numbers. |
| P13 | **`scripts/ui_e2e_prompts.mjs`**: it copies `tasks/` and `config/` to a temp dir and starts its own `foundry ui` there (`FOUNDRY_HOME`). It **refuses to continue unless `/api/prompts` reports the temp root**, and that check stops the script. Then it creates a prompt, checks its card, saves two steps, sees the offline refusal, archives the prompt and checks the picker. Pages pass the title, caption and no-scroll checks at 1440 and 375 px. It cleans up its temp dir. **PASS.** The main e2e's clarity stage now also covers `/prompts`, `/prompts/adb-001` and `/prompts/new`, in both themes and at both widths. **PASS.** |

**Compared with the approved wireframes:** the cards page (id, ring, text, "n runs · k expected steps", Edit, "+ New prompt" in the heading row, Archived section) and the edit page (sections 01 Text, 02 Expected tool steps, 03 Runs, Archive at the bottom, "Make a new version → id") match. The additions are listed under Decisions.

**Exercised live:** yes. The prompts e2e drove the real `foundry ui` on a project copy, and your running server (restarted on this build) served the Prompts pages over the real runs: adb-001 shows 28 runs.

## Verify review

**The independent pass re-ran every suite and the prompts e2e:** all passed, and `tasks/` was untouched. It also probed the store against a temp copy, with no corruption and no leftover lock.

**It found, now fixed:**
- **(Blocking) Unsaved steps were wiped.** On a prompt with no saved steps, any re-render discarded them: typing, a failed save, or the suggestions loading. The editor reset on array identity. It now resets only when the saved steps actually change, and a Vitest re-render test covers it.
- **(Blocking) The handoff cited tests that didn't exist.** They're now written: `test_prompt_view_runs_latest_20`, `test_prompts_mean_uses_active_rubric_only` and `test_scripted_config_with_script_allowed`. The table above names only tests that exist.
- **(Blocking) P13's API-level items were missing.** They're now covered by `test_prompts_api_lock_bytes_echo_and_origin`.
- **A lone surrogate in the text gave 500.** It's now 400 with its field.
- **Removing steps could bring back an older duplicate line** from a hand-edited `expected.jsonl`. Duplicates there are now refused, naming the line numbers (`test_removing_steps_refuses_duplicate_lines`).
- **A non-UTF-8 `prompts.jsonl` broke `/api/session`.** That prompt list is now skipped instead.
- **The version toast** now names the new id.

**Accepted as they are:**
- Ids such as `-` and `_` are valid under the spec's regex.
- On phones, run rows hide their date.
- `set_text` holds the UI lock while it scans run directories, which is fine at today's scale.
- The text lock knows about runs started from the CLI only once their run directory exists, as recorded in the design.

## Security scans

| Category | Tool | Result |
|---|---|---|
| Secrets | gitleaks 8.30.1, `gitleaks git --staged` | clean |
| Static analysis | Semgrep CE 1.99.0, in a temp venv, with the rulesets p/python, p/security-audit, p/typescript, p/react, p/javascript and p/owasp-top-ten, over the 18 changed source files | **5 findings, all false positives after reading the code:** plain-http `fetch`/`urlopen` to loopback test servers and to headless Chrome's DevTools port in `scripts/ui_e2e*.mjs` and `tests/test_ui.py:448`. Nothing was found in the new server code or pages. |
| Dependencies, IaC, images | — | not touched by this change |

**Coverage gaps the scan reported:**
- Semgrep only partly parsed `web/src/types.ts`.
- Its default ignores skipped some test files.
- Bandit and CodeQL aren't available.

**The manual review the scan asked for was done:**
- Every prompt action goes through `prompt_store`, which rejects an unknown task, and path ids pass `_need_prompt_id` before use.
- Ids are never joined into a path, because the store addresses lines by id.
- Writes are behind the CSRF, JSON and Origin guard (tested in `test_prompts_must_deny`).
- The new pages have no `dangerouslySetInnerHTML`.

## Deferred and follow-ups

- **Unsaved steps:** leaving the page with unsaved steps doesn't warn.
- **Dynamic tool discovery from the MCP servers** (suggestions come from traces today).
- **Bulk import of prompts,** and prompts for tasks other than `adb` in the UI (the API already takes `task`).
