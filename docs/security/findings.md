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
| F-010 | Checkov CKV_AWS_18 / Trivy AWS-0089 | Low | CDK runs bucket | No S3 server access logging | fixed in code (increment 6), pending deploy | 2026-10-30 | The runs bucket logs to `AccessLogsBucket` under `runs-bucket/`, with 90-day expiry. Delivery uses a bucket policy limited to the logging service, the runs bucket ARN and the account. | 2026-10-30 (code) |
| F-011 | Trivy AWS-0132 (scanner-rated HIGH) | High (scanner) / Low (our triage) | CDK runs bucket and access-log bucket | SSE-S3 instead of a customer-managed KMS key | accepted | 2026-10-30 | **Accepted by the user on 2026-10-30.** Sandbox with public and synthetic data only. The bucket is encrypted (SSE-S3), private, SSL-only and versioned. / owner: user / review-by: before any non-sandbox deploy | — |
| F-012 | Manual (security scan) | Low | CDK HarnessRole | Trusts the account root; Bedrock actions on `*` | Bedrock part fixed in code (increment 6), pending deploy; root trust still accepted | 2026-10-30 | Bedrock is limited to the `us.` inference profiles and in-region models named in `config/bedrock*.toml`. Foundation models are reachable only through those profiles (`bedrock:InferenceProfileArn`). Trusting the account root is the standard pattern, and IAM in the account still governs who can assume the role. / owner: user | 2026-10-30 (code) |
| F-014 | Checkov CKV_AWS_18 / Trivy AWS-0089 | Low | CDK access-log bucket | The log bucket has no access logging of its own | accepted | 2026-10-30 | This is inherent: logging a log bucket needs yet another bucket. It is private, SSL-only and versioned, and its only writer is the S3 logging service. / owner: user | — |

### Open

| ID | Source | Severity | Component | Class | Status | Found | Accepted | Fixed |
|---|---|---|---|---|---|---|---|---|
| F-013 | Manual (security scan) | Low | `.github/workflows/ci.yml` | Actions pinned to tags, not commit SHAs | open | 2026-10-30 | Follow-up: CI changes need the user's approval | — |
