# Findings register

**Severity scale:** Critical, High, Medium, Low, Info.

**Columns:**
- **Accepted** = reason / owner / review-by.
- **Fixed** = fix commit / re-test date.

### Fixed

| ID | Source | Severity | Component | Class | Status | Found | Accepted | Fixed |
|---|---|---|---|---|---|---|---|---|
| F-001 | ship-it probe | Low | UI `GET /runs/{id}` | Local file read via symlinked run file | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |
| F-002 | ship-it probe | Info | UI `GET /` | Index followed symlinked run dirs | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |
| F-003 | ship-it probe | Low | UI templates | `run.json` `run_id` drove link and form targets | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |
| F-004 | ship-it probe | Low | UI `GET /` | One malformed `scores.json` caused a 500 for the whole index | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |
| F-005 | ship-it probe | Info | UI middleware | 500 responses lacked security headers | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |
| F-006 | ship-it probe | Info | `ui/render.py` | Protocol-relative and backslash links allowed | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |
| F-007 | ship-it probe | Info | UI headers | No `frame-ancestors` / X-Frame-Options | fixed | 2026-10-30 | — | 8c515c6 / 2026-10-30 |

### Accepted

| ID | Source | Severity | Component | Class | Status | Found | Accepted | Fixed |
|---|---|---|---|---|---|---|---|---|
| F-008 | OSV-Scanner / Trivy | Medium | `mcp` 2.1.1 (CVE-2026-116686) | SSRF-style `$ref` fetch during client output-schema validation | accepted | 2026-10-30 | Exploiting it needs a malicious MCP server, and we only run the pinned AWS Labs RODA 0.1.7. The fix (mcp 2.2.0) is blocked because strands-agents 1.62.0 pins `mcp<2.2`. Not in KEV; EPSS 0.003. / owner: user / review-by 2026-11-30 | — |
| F-009 | OSV-Scanner / Trivy | Medium | `mcp` 2.1.1 (CVE-2026-116685, -116684, -59951) | HTTP client redirects; server auth; StreamableHTTP server sessions | accepted | 2026-10-30 | Not reachable: we use the stdio client only and run no MCP server. Upgrade together with F-008. / owner: user / review-by 2026-11-30 | — |
| F-010 | Checkov CKV_AWS_18 / Trivy AWS-0089 | Low | CDK runs bucket | No S3 server access logging | accepted | 2026-10-30 | Sandbox bucket holding synthetic and public-data runs. Add logging before any production use. / owner: user / review-by before L4 deploy outside the sandbox | — |
| F-012 | Manual (security scan) | Low | CDK HarnessRole | Trusts the account root; Bedrock actions on `*` | accepted | 2026-10-30 | This is the standard pattern: assumption is still governed by IAM policies in the account. Scoping to model and inference-profile ARNs is a follow-up. / owner: user / review-by before L4 | — |

### Open

| ID | Source | Severity | Component | Class | Status | Found | Accepted | Fixed |
|---|---|---|---|---|---|---|---|---|
| F-011 | Trivy AWS-0132 (scanner-rated HIGH) | Low (our triage; **needs the user's ruling**) | CDK runs bucket | SSE-S3 instead of a customer-managed KMS key | open | 2026-10-30 | Proposed: accept. The bucket is encrypted (SSE-S3), private, SSL-only and versioned. A CMK only adds key-level access control and audit. Because the scanner rates it High, only the user can accept it. | — |
| F-013 | Manual (security scan) | Low | `.github/workflows/ci.yml` | Actions pinned to tags, not commit SHAs | open | 2026-10-30 | Follow-up: CI changes need the user's approval | — |
