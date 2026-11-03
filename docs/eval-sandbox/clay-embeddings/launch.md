# Clay embeddings and path checks: Launch

## Rollout

- **Mechanism:** direct. It ships with the next `make deploy`. The path check is on in the `bedrock*.toml` configs and off in the `offline*.toml` configs. `FOUNDRY_PATH_CHECK=off` turns it off anywhere.
- **Order:** none needed. The Lambda image carries the code, DuckDB and httpfs together.
- **New task files:**
  - A fresh deployment seeds `tasks/` from the image.
  - An existing deployment keeps its stored project tree, so new prompts and the system prompt don't reach it automatically. Add the prompts through the UI's prompt editor, or deploy fresh.
- **Compatibility:**
  - Old runs simply have no `paths` key, and readers accept that (additive contract).
  - Old configs without the embeddings server keep working.

## Rollback

- Revert the commits from `b5fc933` through the last fix, then `make deploy`. No data migration.
- Runs evaluated in the meantime keep their `paths` key, which nothing requires.
- To roll back only the judge-side behaviour, set `[checks] paths = false` in the Bedrock configs.

## Observability

- **Success signals:**
  - `scores.json` `paths.not_found` across a batch (`foundry batch` summary, or a jq over runs).
  - The live runs' `embeddings/*` steps in the trace.
- **Logs:** the worker's job start and end lines. The embeddings server's own stderr goes to `mcp-embeddings.log` in the run directory.
- **Alerts:** none new. Worker errors already alarm.
- **Watch:** `Max Memory Used` in the worker's `REPORT` lines. Above about 1,900 MB means lower `EMBEDDINGS_MEMORY_LIMIT` or raise the worker's memory.

## Communication

- [x] CHANGELOG (Unreleased)
- [x] README (Tools the agent can use; How scores work)
- [x] `docs/trace-contract.md` (`paths`, `path_checker`)
- [x] `docs/security/attack-surface.md`
- [ ] PR description (when `prototype` is merged)

## Known limitations

- Embeddings cover Sentinel-2 only (LGND monthly set, 1280 m, 256 dims). NAIP embeddings exist (`clay-v1-5-naip-2`) but aren't wired in.
- Some months are missing, such as June 2024 and June 2025. The data ends April 2026.
- Boxes are capped at 1° and 2 partitions. Each query reads about a whole partition-month (~330 MB): seconds in AWS, a minute or more on a laptop.
- Requester-pays buckets are always `unverified`.
- The path check can't tell a template path the agent didn't mark from a wrong path. The judge is told the difference.

## Post-launch checks

- **First demo run:** the trace shows `embeddings/*` calls with status `ok`, and the report has a "Cited S3 locations" table. A `not_found` on a path that exists means a bug in extraction. Check its `|`, placeholder and punctuation handling.
- **After any `embedding_change` across two partitions:** worker `Max Memory Used` stays under about 1,900 MB.
