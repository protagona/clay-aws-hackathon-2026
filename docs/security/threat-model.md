# Threat model

## UI HTTP surface (`src/foundry/server.py`, `src/foundry/api/guard.py`, `src/foundry/api/routes.py`)
- **Threats (STRIDE):**
  - spoofing and tampering through CSRF or DNS rebinding, which could trigger paid runs
  - information disclosure through path traversal or symlinks
  - denial of service through malformed run files or many runs
- **Mitigation:**
  - binding to 127.0.0.1
  - a Host allowlist on every request
  - Origin and a per-process CSRF token (the `X-CSRF-Token` header) on every write (POST, PUT, DELETE), JSON bodies only
  - strict `run_id` regex, a `resolve()`/`is_relative_to` check, and no symlinks followed
  - malformed files shown as "unreadable" or "incomplete" cards
  - one UI-started run at a time
- **Covered by:**
  - `tests/test_api.py` (`test_writes_need_csrf_header_and_json`, `test_origin_and_host_checked_on_every_write`, `test_must_deny_paths`, `test_must_deny_paths_continued`, `test_must_deny_paths_parity`)
  - `tests/test_api_ports.py` (`test_ui_binds_localhost_only`, `test_host_checks_on_every_path`, `test_symlinked_run_dirs_never_listed`, `test_malformed_scores_do_not_break_the_board`)
  - test-log 2026-10-30
  - The server-rendered UI and its `tests/test_ui.py` were removed in increment 16; their checks were ported to the tests above.

## Untrusted run content in HTML (`api/render.py`, `engine/evaluate/report.py`, the web app)
- **Threats (STRIDE):** tampering or elevation through stored XSS from model, tool or agent output.
- **Mitigation:**
  - Services return run text as plain text. Only `api/render.py` (the API's renderer) turns it into HTML (`text_html`, `report_html`); the web app renders every other field as text.
  - `report.md` escaping (HTML, links, images, headings, `|`)
  - markdown-it with `html=False`, images off, and only http(s) or relative links
  - CSP `default-src 'none'; frame-ancestors 'none'`
- **Covered by:** `tests/test_report.py`, `tests/test_api_ports.py::test_run_text_is_inert_but_still_markdown`, `tests/test_api_ports.py::test_security_headers_and_framing`, test-log 2026-10-30

## Judge and summarizer prompts (`engine/evaluate/view.py`, `engine/evaluate/templates/*.txt`)
- **Threats (STRIDE):** tampering through prompt injection in the agent's output or tool results that steers the score.
- **Mitigation:**
  - run content goes only inside a `<run_data>` block, with closing tags neutralized case- and whitespace-insensitively
  - the instructions tell the model to treat that block as data
  - strict JSON validation of the judge's reply
- **Covered by:** `tests/test_evaluate.py::test_injection_only_inside_data_block`. A live check against a real judge (L5) is pending, because there are no credentials yet.

## MCP subprocesses (`harness/mcp.py`)
- **Threats (STRIDE):** information disclosure, i.e. credential exfiltration by a third-party server; tampering through malicious tool output.
- **Mitigation:**
  - an environment allowlist (PATH, HOME, TMPDIR, LANG, UV_*), so no `AWS_*` variables are passed
  - pinned server versions, with `@latest` rejected
  - tool results capped at 8,000 characters for the agent
- **Covered by:** `tests/test_secrets.py`, test-log 2026-10-30

## Run files (`contract.py`, `runstore.py`)
- **Threats (STRIDE):** information disclosure through planted symlinks that carry outside files into reports or judge prompts.
- **Mitigation:** symlinked run files are validation errors, and `evaluate` validates before reading anything.
- **Covered by:** `tests/test_contract.py::test_symlinked_run_files_are_violations`
