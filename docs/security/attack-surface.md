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
| `stac` MCP server (in repo) | outbound HTTPS to the STAC API (Earth Search by default) | none (env allowlist; no AWS creds passed) | 2026-10-30 | — | no |
| `embeddings` MCP server (in repo) | outbound HTTPS: unsigned S3 list/read of `us-west-2.opendata.source.coop` (Clay/LGND embeddings, ~330 MB per partition-month); on laptops, `extensions.duckdb.org` once for DuckDB httpfs (baked into the Lambda image instead). Inputs are numbers, `YYYY-MM` months and bounded `k`; SQL values are bound parameters; file paths are built only from a validated geohash and integers | none (env allowlist; empty DuckDB secret) | 2026-11-02 | — | no |
| Path check in `foundry evaluate` (laptop, worker Lambda, one CI network test) | outbound unsigned S3 `ListObjectsV2` for up to 25 bucket/prefix pairs taken from the agent's output (untrusted): bucket names regex-validated, keys capped at 512 chars, endpoints chosen by boto3, no object reads. A prompt-injected agent could cite a bucket it owns and see the listed prefix in its access logs (small leak channel, accepted) | none (unsigned; no identity) | 2026-11-02 | — | no |
| Path check: signed requester-pays lists (requirement-gaps, 2026-11-03) | outbound signed S3 `ListObjectsV2` with `RequestPayer=requester`, only for buckets in `[checks] requester_pays` (the three NAIP buckets), up to 25 per evaluation. Billed to the account ($0.000005 each). An agent citing any other requester-pays bucket triggers nothing signed. IAM: `s3:ListBucket` on those buckets only | the caller's AWS identity (worker role / laptop credentials) | 2026-11-03 | — | no |
| `naip` MCP server (in repo) | none outbound: reads only `$NAIP_SAMPLE_DIR/{sample.tif,tiles.json}`, a local directory the runner resolves. `tile_id` is regex-validated (`t-r0[0-7]-c0[0-7]`) and looked up in `tiles.json`, so no path is built from input. It checks that the two files match | none (env allowlist; no AWS creds) | 2026-11-03 | — | no |
| Sample cache (`foundry.data.samples`, runner and evaluator) | outbound S3 HEAD/GET of `samples/<name>/` in the deployment's own runs bucket, with a fixed key set. The sample name is validated (`[a-z0-9-]`) | the caller's AWS identity | 2026-11-03 | — | no |
| Retrieval scorer's text embedder | outbound Bedrock `InvokeModel` (Titan Text Embeddings V2): the agent's caption (untrusted text, embedded only, never executed or rendered unescaped) and the reference captions. 60 s budget per evaluation | the caller's AWS identity | 2026-11-03 | — | no |
| S3 runs bucket + HarnessRole (CDK, not yet deployed) | AWS account | IAM | 2026-10-30 | — | yes, once deployed (L4) |
