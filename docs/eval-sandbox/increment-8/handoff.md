# Increment 8: rubric import, versions and tracking — Handoff

**Spec:** ./spec.md   **Design:** ./design.md   **Date:** 2026-10-31   **Track:** Standard

## What shipped

- **Import from any form:**
  - an uploaded PDF, DOCX, XLSX (every tab), CSV, Markdown, TXT, HTML or JSON
  - pasted text
  - an https URL
  - the result of a configured MCP tool

  The config's judge (Opus 4.6 on Bedrock) drafts a rubric. Binary files go to Bedrock as document blocks, so no new parsing dependencies were added.
- **A person always reviews:** the Rubric tab's form review (approved design A) lets you edit, add and remove criteria. Nothing changes until "Approve and activate".
- **Versions:** activation archives the current rubric, writes immutable history (`tasks/<task>/rubrics/history/`), and replaces `rubric.json`. Any version can be re-activated.
- **Tracking:**
  - each run page names the rubric that scored it
  - the history shows how many runs each version scored
  - the dashboard filters by rubric (default: the active one), so scores from different rubrics aren't mixed
- **CLI:** `foundry rubric draft|list|activate|discard`.
- **Commits:** `7957ade` (build), plus the verification commit.

## Decisions made during the build

- **The user chose:** all four source kinds, plus document parsing; replace-and-keep-history with per-run tracking; model drafts with human approval; and form review (design A).
- **Design gate:** 2 subagent passes; every blocker was fixed. Pass 1:
  - path-safe names
  - only form fields are activated, so nothing unreviewed reaches the judge
  - the MCP `timedelta`
  - a crash-safe pid lock
  - hand-edit archiving
  - `rubric_id_loaded` for tracking

  Pass 2:
  - a `task` parameter on every route
  - an 8,192-token output budget for drafting
  - proxy-free URL fetching
  - must-deny route tests
  - drafts and source documents git-ignored, so Clay's originals never land in a public repo
- **Found live and fixed:** given a source that isn't a rubric (RFC 2119, a RODA dataset record), prompt `draft-v1` **invented** a rubric and only mentioned it in its notes. `draft-v2` refuses with "This doesn't look like a rubric (…)", with no repair attempt. Re-checked live on both non-rubrics, and it still drafts the real XLSX rubric.

## Verification

**Suite:** 295 passed after the verify-review fixes. Ruff and gitleaks are clean, and there are no new dependencies.

| AC | How it was proven |
|---|---|
| I1, I2, I5 | `test_rubrics.py`: `test_file_types_and_cap`, `test_text_limits`, `test_json_rubric_skips_model`. UI: `test_rubric_import_review_activate_rollback` (the JSON shortcut through the real app) |
| I3 | `test_url_refuses_non_public_addresses` (10 addresses, including CGNAT `100.64.0.1`, `fd00::1` and `::ffff:127.0.0.1`), `test_url_refuses_non_https`, `test_url_fetch_format_redirects_and_cap` (a real local HTTP server: format detection, a redirect, a redirect loop, the size cap, 404), `test_url_redirect_to_private_is_refused` (every hop is checked). **Live:** an RFC fetched over https, and `https://169.254.169.254/…` refused. |
| I4 | `test_mcp_source_through_fault_server` (the real MCP server: the result, an unknown server, an unknown tool, a tool error). **Live:** `roda/get_dataset_details {"slug": "naip"}`. |
| D1–D4 | `test_draft_from_text_and_documents`, `test_draft_repair_then_invalid`, `test_offline_judge_cannot_draft`, `test_documents_reach_bedrock_request` (a neutral document name even when the file is called "Ignore all instructions.pdf"), `test_non_rubric_source_is_refused_not_invented`, `test_draft_is_forced_to_the_target_task`. **Live on Bedrock (Opus 4.6), all valid:** PDF, DOCX, two-tab XLSX (levels taken from the second tab), and free text ("1–5, counts double" became max 5 and weight 2). Notes explain every choice, and the cost is about $0.01 per draft. |
| D2a | `test_unreviewed_fields_never_reach_the_rubric` (an injected `judge_instructions`, hidden criterion fields and a level 9 are all dropped), `test_unsafe_names_refused` (3 cases) |
| R1–R5 | `test_activate_archives_and_replaces`, `test_duplicate_version_refused_and_invalid_blocked`, `test_rollback_discard_and_bad_ids`, `test_hand_edited_rubric_is_archived_not_lost`, `test_lock_live_refuses_stale_recovers`. UI: add, remove, save, activate and roll back through the real app. **Live:** a drafted rubric activated with the CLI in a scratch copy of the project, which archived the original first. |
| T1 | `test_run_page_names_the_rubric_that_scored_it`. **Live:** a real run evaluated on Bedrock after activation. scores.json has `rubric_id` = adb-rubric-v0 (recorded when the run started) and `rubric_id_loaded` = adb-rubric (the rubric that scored it). The judge scored the new rubric's 3 criteria on its 1–5 scale. The report says "Rubric: adb-rubric v1.0 (run.json says adb-rubric-v0)", and the run page links the version. |
| T2 | `test_history_counts_runs_by_sha`. **Live:** `foundry rubric list` shows 1 run per version. |
| T3 | `test_filter_by_rubric`: the default is the active rubric only; it can be chosen by sha; "all" shows the mixed note; an unknown choice falls back to active. Two existing dashboard tests now ask for `?rubric=all`, the intended change. |
| T4 | Labels on every control (screenshots). Status is shown in text: "✓ Valid" and "✗ Invalid: …". **The verify review found the review form overflowing at 375 px:** the fieldset's min-content width and 70-character inputs pushed it off screen. Fixed with `min-width:0` and full-width inputs, then re-captured: [draft-review-375-dark](screenshots/draft-review-375-dark.jpg) and [rubric-tab-375-light](screenshots/rubric-tab-375-light.jpg). |
| S1, S1b, S2 | `test_rubric_routes_deny`:<br>• CSRF<br>• a bad file extension<br>• a 4.6 MB upload refused while streaming (413)<br>• offline judge refused<br>• an unknown task<br>• a private URL<br>• 3 bad history names on 2 routes<br>• 3 bad draft ids on 2 routes<br>• an unsafe `rubric_id` through the activate form (400, no file written)<br>• a live lock (409)<br>• the source download sent as `attachment` + `nosniff`<br>Hostile `<script>` in a criterion name is escaped (`test_rubric_import_review_activate_rollback`). |

**Screenshots** (live drafts in a scratch copy):
- [rubric tab](screenshots/rubric-tab.jpg)
- [XLSX draft review](screenshots/draft-review-xlsx.jpg)
- [refused RFC draft](screenshots/draft-invalid-rfc.jpg)
- [dark, 375 px](screenshots/draft-review-375-dark.jpg)
- [run page rubric line](screenshots/run-rubric-line.jpg)
- [dashboard filter](screenshots/dashboard-filter.jpg)

**Compared with design A:** it matches. The differences:
- The "Drafting…" label is a static hint next to the button, since a label can't change without JavaScript.
- Binary sources show their metadata and an "Open source" link instead of extracted text.
- A refused (non-rubric) draft opens as an empty, invalid form beside its source.

**Exercised live:** Bedrock drafting with 7 sources, plus activation, evaluation and tracking, all in a scratch copy of the project. The repo's real `tasks/adb/rubric.json` is untouched, because Clay's rubric hasn't arrived.

## Verify review: findings and rulings

- **Fixed:**
  - the 375 px overflow (above)
  - a model reply with `notes` as an int or bool crashed drafting with a 500. Notes are now normalised (`test_odd_notes_never_crash`, 5 cases).
  - weights must be finite and at most 100, since `Infinity` or `1e308` would break the weighted score (`test_weight_must_be_finite_and_bounded`)
  - a broken hand-edited `rubric.json` is archived as `invalid@<sha8>.json` instead of being overwritten
  - `</source>` inside pasted text is neutralised
  - a form submitted with the radio left on "File" but another field filled infers the source kind
  - the T3 empty state is tested (`test_empty_state_right_after_an_activation`)
- **Rulings:**
  - **Identical bytes:** re-activating byte-identical content under an existing id@version is allowed. It's idempotent and appends a history line. Different bytes under the same id@version are refused.
  - **Field errors:** R2's "inline errors" is met by showing the first validation error, naming its field (for example `criteria.0.levels`), in the status line above the form, not next to each field.
  - **CLI activation:** `foundry rubric activate` doesn't open the draft first. The CLI user reviews `draft.json` (its path is printed) before activating, and the UI path always shows the form.
  - **Screenshots:** not every error page has one (refused source, duplicate version, mixed-rubrics note, version page). Each is covered by a route test.

## Deferred and follow-ups

- **Re-scoring past runs after an activation** is manual (`foundry evaluate --force`), by design.
- **DNS rebinding** between the address check and the connection is accepted residual risk for a single-user localhost tool.
- **Scanned (image-only) PDFs** are untested. If one fails, the user sees the error and can paste the text instead.
- **The repo still has its original rubric.** When Clay's arrives: open the Rubric tab, choose the file and the `bedrock` config, click Draft, review, then Approve and activate. After that, commit `tasks/adb/rubric.json` and `tasks/adb/rubrics/history*`.
