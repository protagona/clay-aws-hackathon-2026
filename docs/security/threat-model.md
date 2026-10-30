# Threat model

## UI HTTP surface (`src/foundry/ui/app.py`)
- **Threats (STRIDE):**
  - spoofing and tampering through CSRF or DNS rebinding, which could trigger paid runs
  - information disclosure through path traversal or symlinks
  - denial of service through malformed run files or many runs
- **Mitigation:**
  - binding to 127.0.0.1
  - a Host allowlist on every request
  - Origin and a per-process CSRF token on every POST
  - strict `run_id` regex, a `resolve()`/`is_relative_to` check, and no symlinks followed
  - malformed files shown as "unreadable" or "incomplete" cards
  - one UI-started run at a time
- **Covered by:** `tests/test_ui.py`, test-log 2026-10-30

## Untrusted run content in HTML (`ui/render.py`, templates, `evaluate/report.py`)
- **Threats (STRIDE):** tampering or elevation through stored XSS from model, tool or agent output.
- **Mitigation:**
  - Jinja autoescape
  - `report.md` escaping (HTML, links, images, headings, `|`)
  - markdown-it with `html=False`, images off, and only http(s) or relative links
  - CSP `default-src 'none'; frame-ancestors 'none'`
- **Covered by:** `tests/test_report.py`, `tests/test_ui.py::test_untrusted_content_rendered_inert`, test-log 2026-10-30

## Judge and summarizer prompts (`evaluate/view.py`, `templates/*.txt`)
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
