# Attack surface

| Entry point | Exposure | Auth required | Added | Removed | Next pentest scope |
|---|---|---|---|---|---|
| `GET /` (UI runs list) | local only (bound to 127.0.0.1; Host must be 127.0.0.1/localhost:port) | no (single-user local tool) | 2026-10-30 | — | no (not public) |
| `GET /runs/{run_id}` | local only | no | 2026-10-30 | — | no |
| `POST /runs` (starts `foundry run` subprocess) | local only; Origin + CSRF token | no | 2026-10-30 | — | no |
| `POST /runs/{run_id}/evaluate` (starts `foundry evaluate`) | local only; Origin + CSRF token | no | 2026-10-30 | — | no |
| `GET /jobs/{job_id}/log`, `POST /jobs/{job_id}/dismiss` | local only; dismiss needs Origin + CSRF | no | 2026-10-30 | — | no |
| `foundry` CLI (`run`, `evaluate`, `validate`, `ui`) | local user | n/a | 2026-10-30 | — | no |
| Run directories read by `evaluate`/UI (possibly from other harnesses or other people) | local files | n/a | 2026-10-30 | — | no |
| MCP server subprocesses (RODA 0.1.7 via uvx) | outbound HTTPS to registry.opendata.aws, unsigned S3 | none (env allowlist; no AWS creds passed) | 2026-10-30 | — | no |
| S3 runs bucket + HarnessRole (CDK, not yet deployed) | AWS account | IAM | 2026-10-30 | — | yes, once deployed (L4) |
