# Requirement gaps: Launch

## Rollout

- **Mechanism:** direct, with `make deploy NAME=<deployment>`. The image carries the code, the dependencies (rasterio and pystac-client), expat, and the caption task pack.
- **Order for each deployment:**
  1. Pass the caption scorer check locally first (`scripts/caption_scorer_sanity.py`, AC13). The caption data is frozen once a deployment seeds it (ADR-8).
  2. Deploy: `AWS_PROFILE=<p> make deploy NAME=<name>`. The build runs the rasterio smoke check; a missing system library fails here.
  3. Upload the sample: `AWS_PROFILE=<p> uv run python scripts/stage_naip_tiles.py --upload <name>`. This makes no NAIP reads; it uses the local `samples/naip-captions/`. If that's missing, re-stage first with `--cog 32115/m_3211513_se_11_060_20200524 --row0 0 --col0 27`, which reads about 10 MB requester-pays, at cents. A new staging changes `sample_id`, so update `scorer.json` too.
  4. Smoke run: `foundry remote run start --prompt cap-01 --config bedrock-captions --wait`, then `foundry remote evaluate <run_id> --wait`. Expect `naip_tile` ok, `images=1`, and a Retrieval section.
- **Existing deployments:**
  - A deployment that already has an `adb` tree still gets `captions` seeded, because `captions` has no tree there yet.
  - A deployment that has already seeded `captions` keeps its references and `scorer.json` (ADR-8). To change them, use a fresh deployment, or a follow-up re-seed command.
- **Laptop:** the laptop stack's bucket no longer exists. Caption runs read the local `samples/naip-captions/` (git-ignored), or `FOUNDRY_SAMPLES_DIR`.
- **Config levers:**
  - `[checks] requester_pays = []` turns off signed NAIP lists.
  - `FOUNDRY_PATH_CHECK=off` turns off the whole path check.
  - The caption task is used only by the `*-captions.toml` configs.

## Rollback

- **Code:** revert the commits from `7835d32` through the last fix, and redeploy. There's no data migration. Runs scored in the meantime keep their additive keys (`images`, `retrieval`, `retrieval.json`, `path_checker` v2), which no reader requires.
- **Signed NAIP lists only:** set `requester_pays = []` in the Bedrock configs and redeploy. The IAM statement disappears with the list.
- **Caption task only:** stop using the `*-captions.toml` configs. On a deployment where `captions` is seeded, its prompts stay visible but unused.

## Observability

- **Success signals:**
  - In caption runs, `scores.json` `output.retrieval.rank` and `output.score`.
  - In ADB runs, `paths.items[].reason == "found (requester pays)"` for NAIP.
  - `scripts/judge_sanity.py` and `scripts/caption_scorer_sanity.py` stay green.
- **Logs:**
  - The run's `mcp-naip.log` holds the server's stderr. It is where the `libexpat` error showed.
  - The trace note `sample` appears when the sample isn't staged.
  - Path-check reasons `requester-pays check failed (<code>)` and `no AWS credentials for requester pays`.
- **Alerts:** nothing new. Worker errors already alarm. A caption run whose `naip_tile` fails still "succeeds" (the agent answers without the image), and its workflow score drops. Check `naip_tile` status after each deploy.
- **Watch:**
  - Worker `Max Memory Used`: 389 MB on caption runs, 699 MB on ADB runs, out of 2,048 MB.
  - The first `naip_tile` in a cold worker takes about 17 s (sample download plus loading rasterio).

## Communication

- [x] CHANGELOG (Unreleased)
- [x] README (tools, how scores work)
- [x] `docs/trace-contract.md` (`images`, `reference_ids`, retrieval fields, nullable judge fields, `retrieval.json`, path checker v2)
- [x] `docs/security/attack-surface.md`
- [x] `docs/requirements.md` (where each expected geospatial library is used)
- [ ] **For Clay:** the nullable judge fields are a reader-facing contract change.
- [ ] PR description (when `prototype` is merged)

## Known limitations

- **Stand-in references:** the 64 reference captions are stand-ins written by Claude. One 64-tile sample from one place (El Centro, CA, 2020).
- **Frozen data:** caption references and `scorer.json` are frozen per deployment once seeded.
- **Vision models:** only Claude models read images from tools. Kimi and Qwen reject them; Nova ignores them.
- **Imported runs:** they are evaluated with `config/offline.toml` (path check off), which is existing behaviour.
- **Scorer gaming:** a keyword-stuffed caption fully games 2 of 12 tiles (recorded in the scorer sanity check).

## Post-launch checks

- **After each deploy:** one caption smoke run. `naip_tile` should be `ok` with `images=1`. `mcp-naip.log` showing an ImportError means a system library is missing: roll back, then add it to `Dockerfile.lambda`.
- **After the first ADB batch with the new configs:** no NAIP location should show `unverified`. A `requester-pays check failed (AccessDenied)` means the worker role lacks `s3:ListBucket`; redeploy, since IAM is derived from the configs.
