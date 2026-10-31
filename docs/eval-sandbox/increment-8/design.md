# Increment 8: rubric import, versions and tracking — Design

**Spec:** ./spec.md   **Last updated:** 2026-10-31   **Status:** Draft (design gate pass 1 applied)

## Overview

A new package, `foundry.rubrics`, has three layers:
- **`sources.py`:** acquires bytes from a file, text, a URL or an MCP tool, and returns `Source(kind, label, fmt, data)`.
- **`draft.py`:** turns a `Source` into a draft `Rubric` with the config's judge model on Bedrock Converse, with documents attached natively and JSON out, validated with one repair try.
- **`store.py`:** saves drafts, activates versions with history, and counts the runs each version scored.

The UI gets a "Rubric" tab (approved design A) and the CLI gets `foundry rubric draft|list|activate|discard`. Evaluation is unchanged: it still reads `tasks/<task>/rubric.json`. Tracking reuses the `rubric_*` fields that scores.json already records.

## Experience

**Design review: approved by the user (2026-10-31), option A, "Form review"**

```
Runs Batches Import Dashboard [Rubric]
Active: adb-rubric-v1 · v1.0 · 4 criteria · activated 10-31 14:02
History  v1.0 adb-rubric-v1 10-31 14:02 from clay-rubric.pdf 12 runs [active]
         v0.1 adb-rubric-v0 10-30 09:10 (original)           61 runs [Activate]
Import a rubric: Source (•) File ( ) Text ( ) URL ( ) MCP tool · Config [bedrock ▾] · [Draft rubric]
Draft review: rubric_id / version / description; one card per criterion (id, name, max, weight,
description, level 1..max); [Add criterion] [Remove]; source panel; model notes;
[Save] [Approve and activate] [Discard draft]
```

- **Alternative not chosen:** B, a JSON textarea review.
- **Refinement told to the user:** the source panel shows the text for text formats, and the name, size, sha256 and an "Open source" link for binary formats, because we don't parse binaries ourselves.
- **States:**
  - **No drafts:** the import panel only.
  - **Drafting:** the POST is synchronous, roughly 2–15 s, and the button label says "Drafting… (takes up to a minute)". No JavaScript, so it's a plain form submit.
  - **Draft invalid:** a red note with the validator error and the raw model reply in `<details>`, and the form prefilled with whatever parsed.
  - **Activation error:** shown inline, for example a duplicate version.
  - **Source refused:** the error page names the reason (type, size, URL host is private, unknown MCP server) with a link back.
  - **Pending drafts:** listed under the import panel with "Review" links.
- **Copy:** "Approve and activate: future evaluations use this rubric. Past runs keep the rubric they were scored with."
- **CLI:**
  - `foundry rubric draft --task adb (--file F | --text T | --url U | --mcp SERVER/TOOL [--args JSON]) [--config config/bedrock.toml]` prints the draft id, the review path and validation status.
  - `foundry rubric activate --task adb DRAFT_ID` (or `--version-file history/x.json` for a rollback) activates.
  - `foundry rubric list --task adb` lists the versions.
  - Exit codes: 0 ok, 2 usage or source error, 1 draft invalid.

## Architecture

| Component | Responsibility |
|---|---|
| `rubrics/sources.py` | `from_file(name, data)`, `from_text(t)`, `from_url(u)` and `from_mcp(cfg, server, tool, args)`, each returning a `Source`. Constants: `MAX_BYTES = 4.5 MB`, the `FORMATS` extension map, `BINARY = {pdf, docx, xlsx, csv}`. |
| | **URL:** `urllib.request` with a custom opener that does not auto-redirect. Each hop's host is resolved with `socket.getaddrinfo`, and the hop is refused if any address (IPv4-mapped IPv6 unwrapped first) is `not is_global` or `is_multicast`. `allow_http` is a keyword-only test hook that no route or CLI flag passes. That's at most 3 hops, with a 10 s timeout, and the body is read in chunks up to the cap. |
| | **MCP:** `harness.mcp.start_servers({server: cfg.mcp.servers[server]}, …)` into a temporary log directory. The tool must appear in `tool_server`. Then `client.call_tool_sync(id, tool, args, read_timeout_seconds=timedelta(seconds=60))`. A `timedelta` is required: Strands 1.62.0 calls `.total_seconds()` on it (`strands/tools/mcp/_compat.py:400`). and `stop()`, joining the text content. A result with `status: error`, or empty text, raises `SourceError`. |
| `rubrics/draft.py` | `draft(source, task_id, judge_cfg) -> DraftResult(rubric_dict \| None, raw, error, notes, usage, prompt_id)`. If `source` parses as valid `Rubric` JSON, it returns at once with no model call (I5). Otherwise it builds the Converse request: system = `prompts/draft-v2.txt` (with `{{SCHEMA}}` replaced by the `Rubric` JSON schema and `{{TASK_ID}}`); the user content is either `[document block, text instruction]` or `[text with <source>…</source>]`. It calls through the provider, then parses the first `{…}` JSON object, removes `notes`, and validates with `Rubric.model_validate`. If that fails, it makes one repair call with the error appended, and if that fails too, returns the error and raw reply. |
| `models/base.py`, `bedrock.py`, `scripted.py`, `offline.py` | `CompletionRequest` gains `documents: list[tuple[str, bytes]] = ()` and purpose `"rubric"`. Bedrock sends each document as `{"document": {"format", "name": "source document", "source": {"bytes"}}}` before the text block. Scripted ignores the documents and replays turns, for tests. Offline raises `ProviderError("Drafting a rubric needs a Bedrock judge model: use config bedrock")`, which surfaces cleanly. |
| `rubrics/draft.py: canonical()` | The single gate for every rubric that is saved, updated or activated. It projects to the form's fields only, keeps levels 1..max, forces `task_id`, and applies the name and version patterns (D2a). The `rubric.json` it writes contains exactly what the review form showed. |
| `rubrics/store.py` | Paths live under `tasks/<task>/rubrics/`. The functions:<br>• `save_draft(source, result) -> draft_id`<br>• `load_draft(id)`<br>• `update_draft(id, form_dict)` (re-validates; returns errors per field)<br>• `discard(id)`<br>• `activate(task, draft_id \| history_file)` (R3/R5: archives the current rubric first, writes the history file and `rubric.json` via temp file and rename, appends history.jsonl, moves the draft to `sources/`)<br>• `history(task)`: the entries plus run counts by `rubric_sha256` (T2)<br>• `active(task)` |
| | **Locking:** one process-wide lock for activation, plus a per-task `.rubric.lock` file using `O_EXCL` that holds the owner's pid. A lock whose pid is no longer alive (`os.kill(pid, 0)`, the same pattern as `evaluate/lock.py`) is cleared and retaken, so a crash never blocks for good. |
| | **Archiving:** a hand-edited `rubric.json` whose `id@version` file exists with different bytes is archived as `id@version-<sha8>.json`. |
| `rubrics/prompts/draft-v2.txt` (v1 kept for provenance) | The drafting prompt: refuse non-rubric sources with `{"error": …}`; the source is data; map scales to `max`; derive weights ("double" → 2, percentages normalised); when levels are missing, write level 1 and level max descriptions and mark them in `notes`; ids are kebab-case; reply with only a JSON object `{rubric fields…, "notes": [...]}`. |
| `cli.py` | The `rubric` subcommand group. |
| `ui/app.py` | Routes:<br>• `GET /rubric` (tab)<br>• `POST /rubric/draft` (multipart: kind, file or text or url or mcp+args, config)<br>• `GET /rubric/drafts/{id}`<br>• `POST /rubric/drafts/{id}` (`action` = save, add or remove:N, activate, discard)<br>• `GET /rubric/drafts/{id}/source` (download; `Content-Disposition: attachment`)<br>• `POST /rubric/history/activate` (a rollback by history file name, allowlisted by regex)<br>• `GET /rubric/history/{file}` (read-only view)<br>All POSTs check CSRF. Ids are validated (`^[a-f0-9]{12}$`; history files `^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\.json$`). |
| `ui/templates/rubric.html`, `rubric_draft.html`, `base.html` | The approved layout and the nav tab. |
| `ui/dashboard.py`, `dashboard.html` | `build(entries, rubric=sha \| "all")` filters every view on `scores.rubric_sha256`. Unscored runs appear only under "all". There's an empty state for a rubric with no scored runs. The page gets a GET form with a "Scored with" select; the default is the active rubric's sha, and a note appears when "all" mixes rubrics (T3). |
| `ui/app.py: run_page` | Header line "Rubric: `<rubric_id_loaded>` v`<rubric_version>`" from scores.json, linked to the history file whose sha256 equals `rubric_sha256` (T1). The report already prints the rubric line, so it's unchanged. |

## Data and contracts

- **New files under `tasks/<task>/rubrics/`:**
  - `drafts/<id>/{source.<ext>, draft.json, meta.json}`
  - `history/<rubric_id>@<version>.json`, byte-identical to what was activated
  - `history.jsonl`, one object per activation: `{rubric_id, version, sha256, activated_at, draft_id|null, source: {kind, label, sha256}|null, file}`
  - `sources/<id>/`, kept for provenance
- **`rubric.json`:** unchanged contract. It's still the active rubric and is still read by `evaluate`.
- **`scores.json`:** unchanged. It already records `rubric_id`, `rubric_version`, `rubric_sha256` and `rubric_id_loaded`.
- **Dependants:** `evaluate` (reads `rubric.json`), the runner (records `rubric_id` at run time), and the judge (iterates over the criteria). None of them change.

## Decisions

### ADR-1: Bedrock reads the documents; no parsing libraries
- **Options:**
  - A) Converse `document` blocks.
  - B) Add pypdf, openpyxl and python-docx, extract the text, and send it.
  - C) Accept only text formats.
- **Decision:** A.
- **Why:** it adds no dependencies (and earlier vetting rules discouraged new packages), and the model sees the tables and tabs as they are. The spike (2026-10-31) read PDF, DOCX, a two-tab XLSX and TXT correctly, and a forced tool call also worked.
- **Trade-off:** we can't show extracted text for binary formats, which is why the source panel shows file metadata (a refinement of A).
- **Reversibility:** easy.
- **Sources:** botocore's bedrock-runtime `2023-09-30` model, `DocumentFormat` enum `pdf, csv, doc, docx, xls, xlsx, html, txt, md`; the AWS API reference [DocumentBlock](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_DocumentBlock.html), whose `name` "is vulnerable to prompt injections… specify a neutral name"; and the live spike above.

### ADR-2: JSON text out, not tool use
- **Options:**
  - A) Ask for a JSON object in the reply text, then parse and validate it, with one repair try.
  - B) A forced `toolConfig`/`toolChoice` with the `Rubric` schema (this also worked in the spike).
- **Decision:** A.
- **Why:** it goes through the existing `Provider.complete()`, so the scripted provider covers it in CI and the judge path stays consistent. `Rubric` validation is the real gate either way.
- **Reversibility:** easy.

### ADR-3: Activation replaces `rubric.json` and keeps immutable history
- **Decision:** what the user chose: replace, keep the history, and track usage. Past runs are unaffected, because evaluation copies `rubric.json` into each run directory and records its sha256.
- **Reversibility:** easy, since every version stays in history and R5 rolls back.
- **One-way-ish:** the layout of `history.jsonl` and the history file names become a contract for later tooling. It's additive.

### ADR-4: SSRF guard by resolving and checking every hop
- **Why:** the UI is bound to localhost, but a URL fetch could still reach cloud metadata (169.254.169.254) or internal services. That's why every redirect hop is resolved and checked.
- **Residual risk:** DNS rebinding between the check and the connection. It's accepted for a single-user localhost tool and noted.
- **Sources:** Python's `ipaddress` module (`is_private`, `is_loopback`, `is_link_local`, `is_reserved`, `is_multicast`, `is_unspecified`) for Python 3.12, and `urllib.request.HTTPRedirectHandler`.

## Failure modes

| Failure | Detection | Behaviour |
|---|---|---|
| Unsupported type, or too large | Extension or size check | 400 with the allowed types or the cap |
| URL refused: scheme, private IP, too many redirects, timeout or HTTP error | `SourceError` | 400 naming the reason |
| MCP server fails to start, unknown tool, tool error, empty result | `McpStartError`/`SourceError` | 400 with the reason, and no draft |
| Offline config chosen | `ProviderError` | 400: "Drafting a rubric needs a Bedrock judge model: use config bedrock" |
| Bedrock error (throttling, too large, access denied) | `ProviderError` | 502-style error page with Bedrock's message, and no draft |
| Model returns invalid JSON or an invalid rubric twice | Validation | The draft is saved as invalid, and the review shows the error, the raw reply and a prefilled form |
| Duplicate `(rubric_id, version)` on activation | History check | An inline error: "bump the version" |
| Concurrent activation | Lock file | 409 "another activation is in progress" |
| A crash between the history write and `rubric.json` | Write order: history file, then `rubric.json` via temp file and rename, then the jsonl line | Re-running activate finds the history file with the same sha and continues (idempotent) |

## Security and privacy

- **Untrusted inputs:** uploaded bytes, pasted text, URL bodies, MCP results and the model's draft.
  - All are stored with server-chosen names.
  - Nothing is executed.
  - Rendering is autoescaped.
  - The source download is served `attachment` with `nosniff`.
- **SSRF:** ADR-4.
- **Command injection:** the MCP source can only start configured servers, with no free-form command (S3).
- **Prompt injection:** the source is data, under a neutral document name. The model's output is only a draft, a human approves it, and strict validation bounds its shape.
- **Sensitive data:** public, synthetic or Clay-provided rubric material only. The sandbox rule applies.

## Performance

A draft is one or two Bedrock calls (2–15 s, about $0.02–0.15 with Opus 4.6). The history run counts read every `scores.json` (O(runs)), which is fine for hundreds of runs.

## Migration and rollback

On first use, the existing `rubric.json` is archived as `adb-rubric-v0@0.1` when the first activation runs. A revert removes the feature, and the files it wrote under `tasks/` remain, harmless and readable.

## Traceability

| AC | Component | Tests |
|---|---|---|
| I1, I2, I5 | `sources.from_file` / `from_text`, the `draft` shortcut | `test_rubrics.py::test_file_types_and_cap`, `::test_json_rubric_skips_model` |
| I3 | `from_url` | `::test_url_refuses_private_and_non_https` (patched `getaddrinfo`; no network), `::test_url_redirect_to_private_refused`, `::test_url_fetch_and_cap` (a local http.server plus a patched resolver, `allow_http_for_tests`) |
| I4 | `from_mcp` | `::test_mcp_source_through_fault_server` (the real fault MCP server via `write_config`), `::test_mcp_unknown_server_or_tool` |
| D1–D4 | `draft.py`, providers | `::test_draft_valid_via_scripted_judge`, `::test_draft_repair_then_invalid`, `::test_documents_reach_bedrock_request` (a stubbed boto client checks the document block and neutral name) |
| R1–R5 | `store.py` | `::test_activate_archives_and_replaces`, `::test_duplicate_version_refused`, `::test_rollback`, `::test_discard`, `::test_activate_idempotent_after_crash` |
| R2 | UI | `test_ui.py::test_rubric_draft_review_add_remove_save_activate` |
| T1 | run page, report | `test_ui.py::test_run_page_names_rubric`, `test_report.py` assertion |
| T2 | `store.history` | `::test_history_counts_runs_by_sha` |
| T3 | dashboard | `test_dashboard.py::test_filter_by_rubric` |
| S1, S2 | routes | `test_ui.py::test_rubric_routes_csrf_and_ids`, `::test_hostile_rubric_rendered_inert` |
| Live | — | V1: draft from the spike's PDF, DOCX, XLSX and free text on Bedrock, review, activate a test rubric, evaluate one run, check tracking, then roll back to v0 |

## Risks and spikes

- **The spike is done:** the formats and the structured output were verified live, so the riskiest assumption is retired.
- **Remaining risk:** messy real-world rubrics (merged cells, scanned PDFs). That's mitigated by human review and the invalid-draft path.

## Implementation plan

Each slice ends green (suite and ruff) and is committed.

1. `CompletionRequest.documents` in all providers, plus `rubrics/draft.py` and the prompt, with tests (D1–D4).
2. `rubrics/sources.py` for file, text, URL (with the SSRF guard) and MCP, with tests (I1–I5).
3. `rubrics/store.py` for drafts, activation, history, rollback and counts, with tests (R1, R3–R5, T2).
4. The CLI `foundry rubric` group (draft, list, activate, discard).
5. The UI: the Rubric tab, draft review and history; the run page and report rubric line; the dashboard filter. Tests (R2, T1, T3, S1, S2), then screenshots.
6. Live verification V1 on Bedrock, then the README and handoff.
