# Security test log

Append-only; newest at the bottom.

## 2026-10-30: eval-sandbox (prototype branch, through 8c515c6)

**Scope note:** self-started target, so none was needed. Local UI on 127.0.0.1:8791 with a temp runs directory. Non-destructive only: no load/DoS and no brute force.

**Targets:**
- `uv run foundry ui --runs-dir <tmp> --port 8791`
- the CLI, the CDK template and the dependency lockfiles

**Tools:**
- gitleaks 8.30.1 (Homebrew, pre-existing)
- Semgrep CE 1.181.0, Checkov 3.3.28 (`uv tool`, scratch)
- OSV-Scanner 2.7.0, Trivy 0.76.0 (release binaries, SHA256-verified, scratch)
- curl and Python stdlib for the probe

**Tests run:**
- secrets: git history and working tree
- SCA: `uv.lock`, `infra/package-lock.json`
- SAST: Semgrep `p/python`, `p/security-audit`, `p/secrets`, `p/fastapi`, `p/jwt`
- IaC: Checkov and Trivy on the synthesized CloudFormation; Checkov on the GitHub Actions workflow
- probe (WSTG):
  - ATHZ-01 path traversal and symlinks
  - ATHZ-04 job id tampering
  - INPV-02 stored XSS (index, run page before and after evaluation)
  - CLNT-01/04 Markdown link and image filtering
  - CONF-02 / INPV-17 Host header (DNS rebinding)
  - SESS-05 CSRF and Origin on all POSTs
  - INPV-11/12 config and prompt validation, command injection
  - BUSL-07 concurrency
  - CONF-12/14 headers
  - ERRH-01/02 error handling
  - CONF-06 methods
  - INFO-03 API docs disabled

**Results:**
- secrets: clean
- SCA: F-008, F-009
- SAST: one warning (`| safe` on sanitized report HTML), a false positive after reading the code
- IaC: F-010, F-011; the workflow is clean
- probe: F-001 through F-007, all fixed in 8c515c6; everything else passed

**Re-test, 2026-10-30, after 8c515c6:**
- F-001/F-002: `tests/test_ui.py::test_symlinked_files_and_dirs_are_never_followed`, `tests/test_contract.py::test_symlinked_run_files_are_violations`
- F-003: `::test_links_use_directory_name_not_run_json_run_id`
- F-004: `::test_malformed_files_do_not_break_the_index`
- F-005/F-007: `::test_security_headers_and_framing`
- F-006: `::test_render_blocks_protocol_relative_links`
- All pass. Reproduction for F-001: plant `<run>/report.md` as a symlink to a file outside the runs directory and remove `report_sha256` from `scores.json`. GET `/runs/<run>` then rendered the outside file. It now returns no content from the link.

**CVE triage:** CISA KEV catalog 2026.10.29 has none of the four `mcp` CVEs. EPSS scores: 0.00337, 0.00399, 0.00365, 0.00458.

**Not probed:**
- DoS through FIFO or `/dev/zero` symlinks; now refused by the symlink checks
- real-browser DOM rendering; there is no browser tool on the allowlist (CSP backstops it)
- `POST /runs` with Bedrock configs; avoided paid calls, and there are no credentials
