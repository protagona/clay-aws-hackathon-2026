# Requirement gaps: Design

**Spec:** ./spec.md   **Last updated:** 2026-11-03
**Status:** Current (Design gate: 3 subagent passes; the pass-3 blocker was resolved by the user, 2026-11-03, see ADR-8)

## Overview

There are four independent slices. Each follows a pattern already in the repo.

1. **NAIP path verification.** The path check lists allowlisted requester-pays buckets with a signed request that names the requester as payer. Every other bucket keeps the anonymous list.
2. **Judge case F.** A copy of judge case A whose S3 locations don't exist. `judge_sanity.py` gains a "below" check: F must score under A.
3. **Caption task.** A new task pack, `tasks/captions/`, with:
   - an 8 × 8-tile NAIP crop and the matching Clay v1.5 NAIP embeddings, staged once into each deployment's runs bucket (`samples/naip-captions/`). The harness caches them locally before the run starts its MCP servers;
   - a new MCP server, `naip`, that returns a tile as a PNG and is used by a Claude Sonnet 4.6 agent (the only tested vision path that works, see ADR-7);
   - a retrieval output scorer chosen by the task pack (`scorer.json`) instead of the judge;
   - a swappable text embedder: Bedrock Titan V2 live, a deterministic hash embedder offline.
4. **Geospatial libraries.** The STAC server moves from raw httpx to pystac-client, with the same tool contract. rasterio does real work in `naip` and in the staging script.

ADB evaluation is untouched unless a task pack has a `scorer.json`.

## Experience

This is CLI and config work; there are no UI changes. The UI already lists every config and shows the prompts of the chosen config's task (`src/foundry/services/configs.py:51`), so picking `bedrock-captions` shows the caption prompts.

- **Run:** `foundry run --config config/bedrock-captions.toml --prompt cap-01`, then `foundry evaluate runs/<id> --config config/bedrock-captions.toml`.
- **Offline:** `foundry run --config config/offline-captions.toml --prompt cap-01` uses a scripted model and the hash embedder, with no AWS calls.
- **Staging (one time, needs AWS credentials and costs a few cents):**

  ```
  AWS_PROFILE=<p> uv run python scripts/stage_naip_tiles.py --cog 32115/m_3211513_se_11_060_20200524 --row0 0 --col0 27 [--sheets DIR]
  ```

  This writes `samples/naip-captions/{sample.tif,tiles.json}` locally (`samples/` is git-ignored). `--sheets DIR` writes 4×4 contact-sheet PNGs, labelled with tile ids, for writing reference captions.
  - **Upload** (once per deployment, no NAIP reads):

    ```
    AWS_PROFILE=<p> uv run python scripts/stage_naip_tiles.py --upload <deployment name | laptop>
    ```

    The target is a deployment name. The bucket is the `Bucket` output of the `Foundry-<name>-Data` stack in us-west-2, as `scripts/cloud.sh:74` reads it. The script uploads the two files under `samples/naip-captions/`.
  - **Laptop CLI runs:** these use the local staged copy in `samples/naip-captions/` (see the sample cache). The laptop stack's bucket no longer exists: `head-bucket` returned 404 on 2026-11-03, after main's teardown on 2026-11-02.
- **Sanity checks:**
  - `uv run python scripts/caption_scorer_sanity.py --config config/bedrock-captions.toml` prints a 12-row table and exits 0 or 1.
  - `scripts/judge_sanity.py` is unchanged on the command line. Its table gains the case F rows.
- **Error copy:**
  - **`naip_tile` with an unknown id:** `Unknown tile 'x'. Tile ids look like t-r03-c05 (row and column 00–07); the prompt names the tile to caption.`
  - **Sample not staged:**
    - **Run note:** `sample naip-captions is not staged in s3://<bucket>/samples/naip-captions/ (run: scripts/stage_naip_tiles.py --upload <deployment>)`.
    - **`naip_tile` error:** `NAIP sample not available (NAIP_SAMPLE_DIR is unset, empty, or has no sample.tif); the run note says how to stage it.`
  - **Scorer with no target for the prompt:** the output stays unscored, with error `no retrieval target for prompt 'X' (add it under "targets" in tasks/captions/scorer.json)`.
  - **Scorer with no embedder configured:** output error `no [embedder] in config <path>; the retrieval scorer needs one`.
  - **Embedder failure:** output error `embedder <id>: <ExceptionName>: <message ≤200 chars>`. The score and the overall are null, as when the judge fails.
  - **Requester-pays failures (path-check reasons):** `found (requester pays)`, `requester-pays check failed (<code>)`, `no AWS credentials for requester pays`.
- **Report:** caption runs get a `## Retrieval` section in `report.md`:
  - a line: "Target t-r03-c05 ranked 2 of 8 (score 0.50)";
  - then a table with columns candidate, Clay similarity, caption similarity and target, sorted by caption similarity.

## Architecture

```
runner ──> samples.ensure("naip-captions") ──> s3://<runs bucket>/samples/naip-captions/ ──> $TMPDIR cache
       └─> naip MCP server (new, NAIP_SAMPLE_DIR=<cache>, no AWS env) ──> sample.tif (rasterio window read, PNG)
evaluate ─┬─ workflow (unchanged; expected [["naip_tile"]])
          ├─ paths.check (+ signed requester-pays list for allowlisted buckets)
          ├─ output: scorer.json present?
          │     yes ──> retrieval.score ──> embedder (bedrock Titan V2 | hash)
          │     no  ──> judge (unchanged)
          └─ summarizer, report (+ Retrieval section)
stac MCP server: httpx ──> pystac-client (same tools)
```

### Slice A: NAIP path verification (`src/foundry/engine/evaluate/paths.py`)

- **Config:** `ChecksCfg.requester_pays: list[str] = []` holds bucket names, each validated with `_BUCKET`. All `bedrock*.toml` set `requester_pays = ["naip-analytic", "naip-visualization", "naip-source"]`. The offline configs keep `paths = false`.
- **New argument:** `check(refs, lister=None, limit=25, budget_s=30, requester_pays=frozenset(), rp_lister=None)`.
  - Refs whose bucket is in `requester_pays` go to `rp_lister`.
  - The default `rp_lister` is a boto3 S3 client pinned to `us-west-2`, where all three NAIP buckets are. It uses the default credential chain and the same timeouts and single attempt as `default_lister`. It calls `list_objects_v2(Bucket, Prefix=key, MaxKeys=1, RequestPayer="requester")`.
  - A signed requester-pays list with one attempt succeeded from both a `us-east-1` client and a `us-west-2` client (live, 2026-11-03), so the region pin is for clarity, not correctness.
  - No unsigned request is sent for these buckets.
- **Mapping:**
  - `KeyCount ≥ 1`: `found`, reason `found (requester pays)`.
  - A key with `KeyCount == 0`: `not_found`, reason `no object with this prefix`.
  - A bucket-only ref with `KeyCount == 0`: `found`, reason `bucket exists (empty)`.
  - `NoSuchBucket`: `not_found`, reason `bucket does not exist`.
  - `NoCredentialsError` or `PartialCredentialsError`: `unverified`, reason `no AWS credentials for requester pays`.
  - Any other `ClientError`: `unverified`, reason `requester-pays check failed (<Code>)`.
  - A timeout or other exception: same as today (`timed out`, `error: X`).
- **Unchanged:** the limit, the budget, ordering and the executor all work as before, and both listers run in the same pool.
- **Version:** `paths.VERSION = "v2"`, recorded as `scores.path_checker`.
- **Caller:** `evaluate.py` passes `frozenset(cfg.checks.requester_pays)`.
- **IAM:** a new `requester_pays_buckets(config_dir)` in `infra/stack.py` takes the union of `checks.requester_pays` across `bedrock*.toml`, following the `bedrock_models()` pattern. The cloud worker (`infra/foundry_cloud/stacks.py`) and the laptop-stack role (`infra/stack.py`) each get `s3:ListBucket` on `arn:aws:s3:::<bucket>` for those buckets only. The API Lambda doesn't evaluate, so it gets nothing.

### Slice B: judge case F

- **New fixture:** `fixtures/judge-cases/F-missing-paths/`, copied from `A-strong`. Only `output.json` changes. A's `trace.jsonl` names none of the cited buckets (grep count 0), so the trace neither confirms nor contradicts any location in either case.
- **Replacements:** exact-string replacements scoped to the `arn:aws:s3:::<bucket>[/<key>]` forms in A's table. Nothing else is touched, such as the `…/stac-aws/collections/io-10m-annual-lulc/items` URL. The new names are plausible, keep A's versions and dates, and don't exist; each returned `NoSuchBucket` to an anonymous list on 2026-11-03.
  - `arn:aws:s3:::lp-prod-protected/HLSS30.020` becomes `arn:aws:s3:::nasa-hls-cogs/HLSS30.020`.
  - `arn:aws:s3:::lp-prod-protected/HLSL30.020` becomes `arn:aws:s3:::nasa-hls-cogs/HLSL30.020`.
  - `arn:aws:s3:::sentinel-cogs` becomes `arn:aws:s3:::sentinel-s2-l2a-cogs-us-west-2`.
  - `arn:aws:s3:::io-10m-annual-lulc` becomes `arn:aws:s3:::io-lulc-annual-10m`.
- **Safety check:** a unit test asserts that F's output differs from A's only in those four strings.
- **Path-check guard:** before the `below` check, `judge_sanity.py` asserts that every path item of each F repeat is `not_found`. That rules out a timeout, or a bucket created later, silently weakening the case. The result is a check named `paths F-missing-paths all not_found`.
- **`expected.json`:** add `"F-missing-paths": {"below": ["A-strong", "F-missing-paths@nocheck"], "criteria": ["data-fit"], "control": "nocheck", "about": "…"}`. F has no `band`.
- **`judge_sanity.py`:**
  - **Control scoring:** a case with `"control": "nocheck"` is scored twice. The usual scoring runs with the path check as configured (`bedrock.toml`: on, so the judge input carries `<evaluator_checks>`). An extra scoring runs under the name `<case>@nocheck` with the path check off: `evaluate()` is called while `os.environ["FOUNDRY_PATH_CHECK"]="off"` is set. These control repeats run after the parallel pool, serially, so the env change can't leak into other cases.
  - **No band:** `check()` skips the band check when a case has no `band`.
  - **New check:** for each name in `below`, `below <case> < <other> <criterion|output>` passes when `max(case repeats) < min(other repeats)`. This is the strict reading of "every time": no repeat of F may reach any repeat of the other. It runs for each listed criterion and for the output score.
  - **A failed control** (F@nocheck already low) is a failed check, reported like any other. AC7 then records it as a finding. No judge-template change in this round.
  - **Consistency:** F and F@nocheck also get the existing consistency checks (spread at most 0.25, criterion spread at most 1), like every scored case.
- **Tests:** `tests/test_judge_sanity.py` gets offline unit tests for the `below` logic and the band-less case, using synthetic results.

### Slice C: caption task

**Task pack:**
- **`prompts.jsonl`:** `cap-01` … `cap-12`, each: `Write a retrieval caption for NAIP tile t-rRR-cCC.`
- **`system.txt`:**
  1. Call `naip_tile` with the tile id from the prompt.
  2. Look at the image.
  3. Reply with only one caption of at most 60 words that names land cover, notable objects (roads, rails, buildings, water, fields, crops, bare ground), their layout and their density.
  4. Describe only what is visible; no place names unless the tool's metadata gives them.
- **`expected.jsonl`:** `{"prompt_id": "cap-NN", "stages": [["naip_tile"]]}` for each prompt.
- **`rubric.json`:** `rubric_id: "captions-retrieval-v0"`, version `"0.1"`, one criterion `retrieval` ("Retrieval"), max 4, weight 1. The levels match the rank bands below. The description states that the retrieval scorer fills this criterion and the judge is not called. The file exists because `evaluate` copies the active rubric into every run directory (provenance) and the rubric UI expects one.
- **`scorer.json`:** `{"kind": "retrieval", "references": "references.jsonl", "references_id": "captions-stand-in-v0", "sample": {"name": "naip-captions", "sample_id": "<tiles.json sample_id>"}, "negatives": 7, "targets": {"cap-01": "t-r..-c..", …}}`.
  - `references` is a path in the task pack.
  - `tiles.json` comes only from the sample, through `samples.ensure(sample.name)`.
  - `sample_id` must equal the `sample_id` in that `tiles.json`. If it doesn't, the output is unscored with error `sample naip-captions is <found id>, but scorer.json expects <id>: re-stage the sample or update scorer.json`.
- **`references.jsonl`:** one line per tile, all 64, because negatives need references too. Each line is `{"tile_id", "caption", "source": "stand-in: written by Claude from the tile image, 2026-11-03; replace with Clay's"}`.
- **`references_id`:** in `scorer.json` (`"captions-stand-in-v0"`). The runner copies it into `run.json` `reference_ids` (an optional field the contract already reserves for "example captions and retrieval sets"). It does so when the task has a `scorer.json`.
- **Staged sample (not in git):** `s3://<runs bucket>/samples/naip-captions/` holds two files.
- **`sample.tif`:** an RGB GeoTIFF crop of 2,048 × 2,048 px (8 × 8 tiles of 256 px) from `naip-analytic/ca/2020/60cm/rgbir_cog/32115/m_3211513_se_11_060_20200524.tif` (El Centro, CA). The window is rows 0–7, cols 27–34 of the image's 256-px tile grid. It has the source CRS (EPSG:26911) and transform, JPEG compression at quality 90, and an expected size under 3 MB.
- **`tiles.json`:** `{"sample_id", "source": {"cog", "embeddings", "window": {"row0", "col0", "size": 8}, "staged_at"}, "tiles": [{"tile_id", "row", "col", "bbox": [w, s, e, n], "embedding": [1024 floats, 5 dp]}]}`. Expected size is about 650 KB.
  - **Tile id:** `t-r{row:02d}-c{col:02d}`, with row and col 00–07 within the crop.
  - **`sample_id`:** `<image name>-r<row0>-c<col0>-<staged_at compact>`. Each staging run makes a new one.
  - **Mapping:** source tile index `i = (row0 + row) * ncols + (col0 + col)`, with `ncols = ⌊width / 256⌋` read from the COG. This was verified on `m_3211401_ne`: 41 × 48 = 1,968 = the parquet row count, row-major from the north-west.
  - **Script checks:** it asserts `ncols * nrows == parquet rows`, and reads rows ordered by `file_row_number` (`read_parquet(…, file_row_number=true)`), so row order is pinned. It then cross-checks every selected tile: the row's geometry bbox must match the tile's pixel window transformed to EPSG:4326 within 1e-5°.
  - **Evidence for that check:** for the staged El Centro sample, the largest difference over 64 tiles was 5e-7° (about 6 cm). That also confirms the parquet geometry is lon/lat (2026-11-03).
- **Seeding:** the text files of the task pack (prompts, references, rubric, scorer, scripts) reach the deployed worker from the image (`COPY tasks /app/tasks`) and from cloud seeding of `tasks/`. `seed_projects` reads files as bytes, so this is generic. The task pack holds no binary data: the sample lives in the bucket.

**Synthetic sample for tests and offline runs:**
- `tests/naip_fixture.py` has `make_sample(dir, seed=0)`. It writes a 2,048² RGB GeoTIFF of 64 solid-colour tiles in EPSG:26911, plus a `tiles.json` with random unit embeddings and a fixed `sample_id`. Tests point `FOUNDRY_SAMPLES_DIR` at it.
- The offline CI caption run (AC15) uses the same generator, with a `scorer.json` override in a temp task dir.
- The offline demo uses the locally staged real sample (`samples/naip-captions/`), or `FOUNDRY_SAMPLES_DIR`.

**Sample cache (`src/foundry/data/samples.py`):** `ensure(name, storage_cfg) -> Path | None`.
- **Lookup order:**
  1. `$FOUNDRY_SAMPLES_DIR/<name>/`, then `project_root()/samples/<name>/` (the staging script's local output, used for laptop runs), if it has both files. These local copies are used as they are.
  2. A runs bucket, when one is known: `$STATE_BUCKET` in the cloud, else the bucket in `[storage] s3_uri` with `{account}` filled in (the `storage.py` pattern).
     - The function sends a HEAD for both objects and compares their ETags with `<cache>/.etags.json`. The cache is `tempfile.gettempdir()/foundry-samples/<name>/`, since Lambda sets `TMPDIR=/tmp`, but the code doesn't rely on that.
     - On a match, the cache is used. On a mismatch or no cache, the objects are downloaded.
     - So a re-stage is picked up by warm Lambdas on their next run, for two HEAD requests per run.
  3. A bucket that can't be reached falls back to an existing cache, with a warning note. With no cache, the result is None.
- **Client:** boto3 with connect and read timeouts of 5 s / 30 s and standard retries (2 attempts, botocore backoff).
- **Writes:** files go to `.part` first and are renamed after each download, so a crash never leaves a half file that looks valid.
- **Callers:**
  - **Placeholder resolution:** `harness/mcp.py` `server_env(cfg, samples=None)` replaces each env value of the form `{sample:<name>}`.
    - **When `samples` is given** (a resolver callable): the value becomes `str(samples(name) or "")`.
    - **When it isn't** (`harness/tools.py:38` tool listing, `rubrics/sources.py:136`): the value becomes `""`. The `naip` server starts and lists its tool without a sample, and only `naip_tile` calls fail.
  - **Runner:** `start_servers(servers, timeout, log_dir, samples=None)` gains the resolver argument and passes it to `server_env`. The runner passes a resolver that calls `ensure(name, cfg.storage)`. When the result is None, it records `note name="sample"` with the copy above.
  - **Concurrency:** downloads go to unique temp names (`tempfile.mkstemp` in the cache dir) and are renamed into place. `.etags.json` is written the same way. The `naip` server checks that `tiles.json` matches `sample.tif` (the crop's bounds equal the union of the tile bboxes, within 1e-5°), so it never serves one staging's pixels with another's footprints.
  - **Time:** the resolution happens after `t_start`, so it counts toward `max_seconds`. It's about 1 MB in-region (sample.tif 821 KB, tiles.json about 650 KB) plus two HEADs.
  - **Retrieval scorer:** reads `tiles.json` through `ensure` (same name, from `scorer.json` `"sample"`).
- **IAM:**
  - The cloud worker already has read/write on its bucket (`infra/foundry_cloud/stacks.py:144`).
  - The laptop-stack role already has `GetObject` and `ListBucket` on `samples/*` (`infra/stack.py:94-99`).

**Staging script (`scripts/stage_naip_tiles.py`):** a one-time job; it never runs in the product.
1. Open the COG with rasterio, with `AWS_REQUEST_PAYER=requester` and the caller's credentials.
2. Window-read bands 1–3 at `(col0*256, row0*256, 2048, 2048)` and write `samples/naip-captions/sample.tif`.
3. Read the matching parquet with DuckDB (anonymous, `URL_STYLE 'path'`). Assert `ncols * nrows` = the row count, then select rows `i` and write `tiles.json`, with each bbox taken from the row's geometry.
4. Optionally write contact sheets.
5. `--upload <target>` copies the two local files to the target bucket (see Experience).

The window was chosen from a gridded mosaic of the COG's overview, read on 2026-11-03.

**MCP server (`src/foundry/engine/mcp_servers/naip.py`, `MCPServer("naip")`):**
- **Tool:** `naip_tile(tile_id: str)` returns `[Image(data=png_bytes, format="png"), json_text]`, through `mcp.server.mcpserver.Image` (mcp 2.1.1, `utilities/types.py:9`; used in `evidence/vision-spike.md`).
  - The JSON carries `tile_id`, `bbox` (lon/lat), `acquired` (from the COG name, `2020-05-24`), `source` (COG key), `gsd_m: 0.6` and `size_px: 256`.
  - The PNG is encoded with rasterio (`MemoryFile`, driver `PNG`) from a window read of `sample.tif`.
- **Sample location:** `$NAIP_SAMPLE_DIR` only. The config sets `env = { NAIP_SAMPLE_DIR = "{sample:naip-captions}" }`, and the runner resolves it. The server never receives AWS credentials (`ENV_ALLOWLIST`, `harness/mcp.py:26`) and never reads S3.
- **Validation:** `tile_id` must match `^t-r0[0-7]-c0[0-7]$` and exist in `tiles.json`. Everything else is a `ToolError` with the copy above.
- **Configs:** `config/bedrock-captions.toml` and `config/offline-captions.toml`, with `[task] id = "captions"`.
  - The only MCP server is `naip` (`command = "{python}"`, `args = ["-m", "foundry.engine.mcp_servers.naip"]`, `error_detection = "mcp"`).
  - **`[model]`:** `us.anthropic.claude-sonnet-4-6`, without temperature (ADR-7).
  - **Judge, summarizer, limits and storage:** as in `bedrock.toml` / `offline.toml`, with `max_model_calls = 4`.
  - The offline config uses a scripted model with scripts under `tasks/captions/scripts/scripted-a/`: call `naip_tile`, then answer with a fixed caption.

**Trace: images in tool results (`src/foundry/engine/harness/hooks.py`):**
- **New field:** `after_tool` counts the result's content blocks that have an `image` key. When the count is above zero it adds `images: <n>` to the `tool_call` step. This is a new optional trace field; the contract gets a v0.2 additive note.
- **The cap rebuild** (`len(text) > cap`) keeps every non-text block of the original content, and replaces only the text with the truncated text. So an image is never stripped.

**Text embedder (`src/foundry/engine/models/embedder.py`):**
- **Config:** `EmbedderCfg(_Strict)` has these fields:
  - `provider: "bedrock" | "hash"`, `id: str`, `region: str | None`;
  - `dimensions: int = 1024`, validated: 256, 512 or 1024 for bedrock (Titan V2 rejects others, tested 2026-11-03), any 16–4096 for hash;
  - `timeout_s: int = 10`, `retries: int = 1`, `budget_s: int = 60`.

  `EvalConfig.embedder: EmbedderCfg | None = None`.
- **Budget:** `embed` stops starting calls once `budget_s` has passed and raises `EmbedderError("budget of 60s exhausted after k of n texts")`. Worst case, about 60 s plus one call's timeout, well inside the 900 s worker.
- **API:** `build_embedder(cfg) -> Embedder`, with `embed(texts) -> list[list[float]]` (unit vectors) plus `calls` (input tokens each).
- **`bedrock`:** `bedrock-runtime.invoke_model` with `amazon.titan-embed-text-v2:0` and body `{"inputText", "dimensions", "normalize": true}`, one call per text. It reads `embedding` (length = dimensions) and `inputTextTokenCount`. The botocore config has `connect_timeout=5`, `read_timeout=timeout_s` and `retries={"mode": "standard", "total_max_attempts": 1 + retries}`, so botocore's exponential backoff applies. Response shape checked live on 2026-11-03.
- **`hash`:**
  1. Lowercase the text and split it into `[a-z0-9]+` tokens.
  2. Drop a trailing "s" on tokens longer than 3 characters.
  3. Hash each token into a bucket (`sha1(token) mod dimensions`), and add the token's count there.
  4. L2-normalise. Pure, deterministic, offline.
- **Which configs get it:** every config gets an `[embedder]` block (bedrock: Titan V2, 1024; offline: hash, 512), so any config can evaluate a caption run.
- **IAM:** `bedrock_models()` also collects `embedder.id` when the provider is `bedrock`, so Titan gets the existing in-region `InvokeModel` grant.
- **Price:** `[prices]` gains Titan V2's input price, from the AWS Price List API, so caption evaluations have a cost and count against the spend cap. The evaluation reservation (`eval_reservation`) still reserves for a judge call. That over-reserves slightly for caption runs, which is safe.

**Retrieval scorer (`src/foundry/engine/evaluate/retrieval.py`, `VERSION = "retrieval-v1"`):**
- **`load(task_id) -> ScorerSpec | None`:** reads `tasks/<task>/scorer.json`. No file means None (judge path). A malformed file is an `EvaluateError`, naming the file and field.
- **`score(spec, run, output, embedder) -> dict`** builds the `output` section.
  1. **Target:** `spec.targets[run.prompt_id]`. If it's missing, the output is unscored with an error.
  2. **Unscored output:** if `output.kind != "text"` or the text is blank, the score is null with error `no caption`.
  3. **Candidates:** the target, plus the `negatives` tiles with the highest Clay cosine to the target. Ties go by `tile_id`.
  4. **Embed:** the caption and each candidate's reference caption, in one `embed` call of 1 + 8 texts.
  5. **Rank:** `rank = 1 + #{c ≠ target : sim(c) ≥ sim(target)}`. Ties count against the caption.
  6. **Score:** `1 / rank`.
  7. **Criterion:** `{"id": "retrieval", "name": "Retrieval", "score": round(4 / rank, 2), "max": 4, "rationale": "Target ranked R of 8 …"}`. The criterion's score/max equals `output.score`, so the two never disagree. The rubric levels describe ranks 1, 2, 3–4 and 5–8.
  8. **Extra keys:** `"scorer": "retrieval-v1"`; `"retrieval": {"target", "rank", "candidates": [{"tile_id", "clay_similarity", "caption_similarity"}], "embedder": {"provider", "id"}, "references_sha256", "tiles_sha256"}`; `"judge": null`; `"rubric_version"`.
- **Re-evaluation:** a later `foundry evaluate` uses the task pack's current references and the current sample, the same rule as the rubric, which is the active `rubric.json` unless `--rubric` is given. `retrieval.json` makes every past evaluation auditable: exactly which references and similarities produced its score. Re-scoring from `retrieval.json` alone is a follow-up (Known limitations).
- **Provenance:** `score` also returns `retrieval.json` bytes: `{"scorer", "target", "references_id", "candidates": [{"tile_id", "reference", "clay_similarity"}], "caption"}`. `evaluate` writes it with `rubric.json`, `report.md` and `scores.json`, through the same atomic multi-write with `scores.json` last. Its hash goes into `scores.retrieval_sha256`. A later re-score can therefore use the run directory alone.
- **`evaluate.py` wiring:** `spec = retrieval.load(run.task_id)`.
  - **With a scorer:** the output section comes from `retrieval.score`, and the judge is neither built nor called.
    - `templates.judge`, `evaluation.judge` and `output.judge` are null.
    - The "judge model is the agent model" warning is skipped.
    - `report.py` prints `Judge: not used (retrieval scorer)` where it names the judge.
  - **Without a scorer:** the existing judge code runs unchanged.
  - **`overall`:** uses the same weights.
  - **Cost:** the evaluation cost counts the embedder's tokens when they're priced, and `evaluation.embedder` records `{provider, id}` when it was used.
- **`report.py`:** `_retrieval_section(scores)` renders when `scores["output"].get("retrieval")`. Every string goes through `cell()`.

**Scorer sanity (`scripts/caption_scorer_sanity.py`):** for each of the 12 targets it scores three captions with `retrieval.score` and the configured embedder:
- the target's own reference;
- the generic `An aerial image of land.`;
- the reference of a seeded-random other tile (`random.Random(12)`).

It checks the AC13 thresholds and prints the table (`--md` writes it). An offline test runs it with the hash embedder and asserts that the own-reference score is 1.0 for all 12.

### Slice D: STAC server on pystac-client (`src/foundry/engine/mcp_servers/stac.py`)

- **Client:** `_io = StacApiIO(timeout=TIMEOUT_S, max_retries=0)`. `_client()` lazily runs `Client.open(_api(), stac_io=_io)`. Both arguments exist in 0.9.1 (signature inspected, 2026-11-03). `max_retries=0` keeps the existing single-attempt behaviour and timing. The default of 5 retries would multiply the timeout.
- **Raw JSON, never pystac objects:** pystac 1.15.2 migrates objects it parses (`pystac/stac_io.py:162`; for example `proj:epsg` becomes `proj:code`), and fails on objects it can't parse (`client.py:440-447`). So the tools read raw dicts through `_io`, the same `StacApiIO` the client uses.
  - **Collections:** `_io.read_json(f"{api}/collections")`.
  - **Item:** `_io.read_json(f"{api}/collections/{c}/items/{quote(id)}")`.
  - **Search:** pages come from `pages_as_dicts()`, which yields the server's JSON unparsed.
  - **What pystac-client does here:** it opens the API (landing page and conformance) and builds and pages the search.
- **`list_collections`:** `_io.read_json(…/collections)["collections"]`, cached as today. The output is identical.
- **`search_items`:** `search = client.search(collections=[c], bbox=box, datetime=_interval(dt), limit=limit, max_items=limit, query=…)`.
  - The first page is `next(search.pages_as_dicts())`, a single POST with `limit` in the body.
  - `features` and `numberMatched` (or `context.matched`) are read from that page dict, exactly as today.
  - `search.matched()` is **not** called: it would send an extra `limit: 1` request (pystac-client `item_search.py:775`).
- **`get_item`:** `_io.read_json(…/items/<id>)`. A 404 raises `APIError` with `status_code=404`, which gives today's `HTTP 404` copy. The output is identical.
- **Error mapping:** pystac-client 0.9.1's `StacApiIO.request` wraps every transport exception as `APIError(str(err))` without a `status_code` (`stac_api_io.py:208-223`), and HTTP errors as `APIError.from_response` with `status_code` set. So the server catches `APIError`:
  - **`getattr(e, "status_code", None)` is set:** `STAC API returned HTTP <code> for <path>: <text ≤300>` (today's copy).
  - **It's None and `"timed out"` is in `str(e).lower()`:** today's timeout copy, `STAC API timed out after <n>s (<url>). Try a smaller bbox or date range.`
  - **It's None otherwise:** `STAC API unreachable (<url>): APIError`.
  - **A `ParametersError` or any other exception:** `ToolError("STAC client error: <Name>")`.

  The assertions at `tests/test_stac.py:152,157` ("timed out after 1s", "unreachable") therefore hold.
- **Unchanged:** the validation, `_compact`, the tool names, signatures, docstrings and output JSON. httpx was never a direct dependency (it arrives through `mcp`), and nothing else in `src/` uses it (checked in Build).
- **Tests:** `tests/test_stac*.py` assertions stay unchanged. The fake server gains `GET /` (a landing page with `conformsTo` listing STAC API core, item-search, collections and features) and `GET /conformance`. That is a fixture change, not an assertion change.

## Data and contracts

All changes are additive (`docs/trace-contract.md`, Rules 1):
- **`scores.json`:**
  - `path_checker` may be `"v2"`.
  - `output.scorer` is optional (`"retrieval-v1"`).
  - `output.retrieval` is optional, with the shape above.
  - `output.judge` may be null.
  - `evaluation.embedder` and `retrieval_sha256` are optional.
  - `templates.judge`, `evaluation.judge` and `output.judge` may be null for retrieval-scored runs. **This is a change for readers:** before this, these were always objects or strings. `report.py` is updated, and `docs/trace-contract.md` says readers must accept null. Flagged for Clay as a contract note, though no field is removed or renamed.
- **Run directory:** `retrieval.json` is an optional evaluation file.
- **`trace.jsonl`:** optional `images` on a `tool_call`.
- **`run.json`:** `reference_ids` is filled for tasks with a `scorer.json`. The field was already reserved.
- **Path-check items:** the three status values are unchanged. The new reasons are display text.
- **Task pack:** `scorer.json` is a new optional file. With no file, the judge scores the output, as today.
- **Config:** `[checks] requester_pays` and `[embedder]` are new and optional, with defaults that keep today's behaviour.
- **Dependencies:** `rasterio==1.5.2` and `pystac-client==0.9.1`, with `uv.lock` updated.

## Decisions

### ADR-1: Pick the output scorer in the task pack, not in config
- **Context:** the output scorer depends on the task: ADB uses the judge, captions use retrieval. One config can evaluate runs of any task, and Clay will swap a task's references without touching configs.
- **Options:**
  - A) `scorer.json` in the task pack.
  - B) `[scoring] output = …` in config.
  - C) A field in `rubric.json`.
- **Decision:** A.
- **Why:** it lives with the reference data it uses and leaves the rubric schema alone. A config can't pick the wrong scorer for a task.
- **Reversibility:** easy.
- **Sources:** this codebase (`src/foundry/data/tasks.py:1`, `evaluate.py:62-146`).

### ADR-2: The NAIP sample lives in each deployment's runs bucket, cached locally by the harness
- **Context:**
  - The user chose "copy once into our runs bucket" and confirmed it on 2026-11-03, after being offered a repo commit.
  - MCP servers never receive AWS credentials (`ENV_ALLOWLIST`, `src/foundry/engine/harness/mcp.py:26`). That is an existing security rule.
- **Options:**
  - A) The `naip` server reads S3 itself. That needs AWS credentials in a server env, which breaks the rule.
  - B) The trusted harness downloads the sample to a local cache and passes the server a path.
  - C) Commit the sample to the repo (declined by the user).
- **Decision:** B.
- **Why:** it honours the user's choice and the credentials rule. One generic `{sample:<name>}` placeholder serves any future sample. The scorer reuses the same cache.
- **Trade-off:** each deployment needs one upload (`--upload <name>`). `launch.md` lists it as a rollout step.
- **Reversibility:** easy.
- **Sources:**
  - This codebase (`harness/mcp.py:26,35-39`; `infra/foundry_cloud/stacks.py:144`; `infra/stack.py:94-99`).
  - The COG size was verified by a header read on 2026-11-03.

### ADR-3: Allowlisted requester-pays buckets go straight to a signed list
- **Options:**
  - A) Anonymous first, then signed on AccessDenied (Requester Pays).
  - B) Signed directly for allowlisted buckets.
- **Decision:** B.
- **Why:** one request instead of two, the same verdicts, and a simpler budget. The allowlist bounds who can make the account pay, so an agent citing an arbitrary requester-pays bucket triggers nothing signed.
- **Reversibility:** easy.
- **Sources:** botocore `list_objects_v2` takes `RequestPayer`. Signed requester-pays LIST checked live on 2026-11-03 against naip-analytic, -visualization and -source. An unsigned request returns "Anonymous users cannot invoke requests against Requester Pays buckets".

### ADR-4: Text-to-text retrieval with Clay-chosen hard negatives
- **Context:**
  - Clay v1.5 NAIP embeddings are image-only; no text encoder ships in the bucket (claim 4 unverified, see Risks).
  - Clay's retrieval set is pending.
- **Options:**
  - A) Rank the target among Clay-nearest look-alikes by caption-to-reference text similarity.
  - B) Correlate caption similarity with Clay similarity across tile pairs.
  - C) LLM judge with the image.
- **Decision:** A.
- **Why:** it measures what the task asks ("captions that support retrieval"). It's deterministic for a given embedder, gives one score per run, and uses Clay's embeddings where they add the most: making the distractors hard. B needs a corpus of captions per score. C needs an image-aware judge, a non-goal.
- **Reversibility:** easy. `scorer.json` `kind` can name another scorer later.
- **Sources:** `clay-v1-5-naip-2` README and schema (DuckDB `DESCRIBE`, 2026-11-03); Titan V2 is on demand in us-west-2 (`list-foundation-models`, 2026-11-03).

### ADR-5: A deterministic hash embedder for offline configs
- **Why:** M6 (swappable providers), M7 (replay without network) and the tests need an embedder with no AWS. A hashed bag of words is crude but deterministic, and enough for the "own reference ranks first" sanity check.
- **Reversibility:** easy.
- **Sources:** none (pure code).

### ADR-6: STAC server on pystac-client
- **Context:** the requirements list pystac as an expected library. The STAC server hand-rolls HTTP.
- **Decision:** use pystac-client (pystac underneath) and keep the tool contract identical.
- **Why:** it adopts the expected library where it does real work, and gets conformance-aware search and paging for free.
- **Trade-off:** requests becomes a dependency, and the test fake needs a landing page.
- **Reversibility:** easy (one module).
- **Sources:** PyPI pystac-client 0.9.1 (uploaded 2026-10-20); `Client.search` accepts `query` (inspected the signature, 2026-11-03).

### ADR-7: Claude Sonnet 4.6 is the caption agent
- **Context:** the default agent, Kimi K3, is listed by Bedrock with image input.
- **Options:**
  - A) Kimi K3 or K2.5.
  - B) Qwen3-VL.
  - C) Nova 2 Lite.
  - D) Claude Haiku 4.5.
  - E) Claude Sonnet 4.6.
- **Decision:** E, with D as the cheaper alternative.
- **Why:** a spike (2026-11-03, Strands 1.62.0, MCP image tool result, ConverseStream) found:
  - A and B fail with ValidationException "This model doesn't support the image field for user messages".
  - C accepts the request but answers as if no image was sent.
  - D and E correctly name the tile's colour.
  - Sonnet 4.6 is already priced and IAM-granted (`config/bedrock.toml` `[prices]`).
- **Reversibility:** easy (config).
- **Sources:** the spike scripts, run against Bedrock us-west-2 on 2026-11-03, recorded in `evidence/vision-spike.md`.

### ADR-8: Freeze the caption data before the first deploy that seeds it
- **Context:** a deployment seeds a task's files only when its store has no tree for that task (`src/foundry/cloud_boot.py:44-45`). Later redeploys don't update `scorer.json` or `references.jsonl`. The scorer refuses a sample whose `sample_id` doesn't match `scorer.json`.
- **Options:**
  - A) Freeze the sample, references and `scorer.json`, and pass AC13 locally, before the first deploy that seeds `captions`.
  - B) Move references into the bucket sample, so swaps are uploads.
  - C) A re-seed command.
- **Decision:** A (the user, 2026-11-03).
- **Why:** it's the smallest change for the hackathon and needs no new mechanism.
- **Consequences:**
  - Re-staging or swapping references on a seeded deployment needs a fresh deployment, or a follow-up re-seed command. This is listed in Known limitations and in `launch.md`.
  - The "delete `scorer.json`" rollback lever works only locally or on a fresh deployment.
- **Reversibility:** easy (B or C later).

### ADR-9: Install expat in the Lambda image, and smoke-test rasterio at build time (Verify, 2026-11-03)
- **Context:** the first deployed caption runs failed at `import rasterio` with `libexpat.so.1: cannot open shared object file`. The manylinux wheel expects libexpat from the system, and the Lambda base image (Amazon Linux 2023) doesn't ship it. Tests and the local CLI didn't show this, because macOS and the dev machine have it.
- **Options:**
  - A) `dnf install -y expat` in `Dockerfile.lambda`.
  - B) Vendor libexpat into the image by hand.
  - C) Build rasterio from source against system GDAL.
- **Decision:** A, plus a `RUN python3 -c "…"` smoke check after the dependency install: rasterio writes a PNG and pystac-client imports. A missing library then fails the build instead of the first run.
- **Why:** smallest change. The base image's own package manager gives a patched, tracked expat (2.8.3-1.amzn2023).
- **Reversibility:** easy.
- **Sources:** reproduced and fixed in `public.ecr.aws/lambda/python:3.12-arm64` with podman, 2026-11-03: before, ImportError; after, "ok PNG bytes 270 gdal 3.12.2". `evidence/deploy-2.log` line 183 shows the smoke check passing in the real build.

## Failure modes

| Failure | Detection | Behavior | Recovery |
|---|---|---|---|
| No credentials for the requester-pays list | `NoCredentialsError` | `unverified`, "no AWS credentials for requester pays" | Configure credentials; re-evaluate |
| Worker role lacks `s3:ListBucket` on NAIP | `AccessDenied` ClientError | `unverified`, "requester-pays check failed (AccessDenied)" | Redeploy (IAM is derived from configs) |
| Sample not staged in the bucket, or the download fails | `samples.ensure` returns None; a `sample` note in the trace | `naip_tile` returns a tool error; the run fails or answers without an image; the workflow score shows the tool error | `stage_naip_tiles.py --upload <deployment>`; re-run |
| Embedder budget exhausted | `EmbedderError` | Output score null with error; overall null | Re-evaluate |
| The model ignores the image or doesn't call the tool | Workflow sequence check (`naip_tile` expected) | Low workflow score; retrieval still scores the text | n/a |
| Titan throttled or unavailable | Exception in `embed` | Output score null with error; overall null | Re-evaluate |
| `scorer.json` malformed | `EvaluateError` before any model call | `foundry evaluate` exits non-zero with file and field | Fix the file |
| Prompt has no target (e.g. added in the UI) | Scorer | Output null with error copy | Add the target to `scorer.json` |
| pystac-client raises an unmapped exception | Generic `except Exception` in each tool | `ToolError("STAC client error: <Name>")` | n/a |
| Lambda memory (rasterio + numpy) | `REPORT` Max Memory Used | n/a | AC20 measures it; raise memory if above 1,800 MB. Measured: 389 MB peak on caption runs |
| A system library rasterio needs is missing from the Lambda image | Build-time smoke check in `Dockerfile.lambda` (rasterio writes a PNG) | The image build fails, so it never deploys | Install the library with `dnf` in the Dockerfile. This happened in Verify with `libexpat.so.1`, see ADR-9 |

## Security and privacy

- **Signed requester-pays lists:**
  - They bill the sandbox account, only for allowlisted buckets, at most 25 per evaluation.
  - IAM grants `s3:ListBucket` on those buckets only. There's no `GetObject` on NAIP for the worker; runs never read NAIP.
- **The `naip` server:** reads only files under the sample directory, through a fixed filename. `tile_id` is validated by regex and looked up in `tiles.json`, so there's no path built from user input.
- **Embedder:** sends the agent's caption and the reference captions, which are public-data descriptions, to Bedrock. There's no personal data.
- **Image bytes to the model:** a public-domain NAIP tile (USDA FSA).
- **New dependencies:** rasterio 1.5.2 (bundled GDAL, PROJ and libtiff) and pystac-client 0.9.1 (+ requests). The security scan in Verify checks their advisories (OSV).
- **`docs/security/attack-surface.md`** gains rows for the signed lists, the `naip` server and the embedder.

## Performance

- **`naip_tile`:** one 256² window read plus PNG encode, under 200 ms locally. This is measured in the test.
- **Scorer:** one embed call of 9 texts. With Titan at one request per text, about 9 × ~100 ms, under 5 s.
- **Path check:** unchanged budget (30 s, 25 refs).
- **Lambda worker memory:** below 1,800 MB on a caption run (AC20, from `REPORT` lines).

## Migration and rollback

- **Rollout:** deploy with `make deploy NAME=embeddings`. The image carries the code, dependencies and task pack. On the existing deployment, seeding adds `tasks/captions` when it has no project tree (verified in Build).
- **Rollback:**
  - Revert the commits and redeploy. Runs scored meanwhile keep their additive keys.
  - Slice A alone: `requester_pays = []`.
  - Captions alone: delete the configs, or `scorer.json` (the judge would then score with the captions rubric).

## Traceability

| Acceptance criterion | Component(s) | Test(s) |
|---|---|---|
| AC1–AC4 | `paths.py` rp_lister, `ChecksCfg` | `tests/test_paths.py`: fake rp_lister for found, not found, empty bucket, NoSuchBucket, no credentials, ClientError and timeout; "no signed call for a non-allowlisted bucket"; a network-marked test against `naip-analytic` (skipped without credentials) |
| AC5 | offline configs | `test_paths.py`: offline configs have `paths = false` |
| AC6 | infra IAM, deployed worker | `tests/test_infra*.py`: synthesized template has `s3:ListBucket` on the three NAIP ARNs; live run evidence |
| AC7, AC8 | F fixture, `judge_sanity.py` | `tests/test_judge_sanity.py`: below logic, band-less case, control naming, and F differs from A only in the four strings (all offline); live `judge_sanity.py` run recorded in `evidence/` |
| AC9 | `tasks/captions/*`, configs | `tests/test_tasks.py`: pack loads; prompts, expected and targets cover cap-01…12; each prompt's tile id equals its `scorer.json` target; every target and all 64 tiles have references; `task_ids` lists captions |
| AC10 | `naip.py`, `samples.py`, runner placeholder | `tests/test_naip.py`: PNG decodes to 256×256×3, metadata bbox matches `tiles.json`, unknown id and missing sample give the error copy, and a run through the harness with a synthetic sample in `FOUNDRY_SAMPLES_DIR`; `tests/test_samples.py`: lookup order, download through a stubbed S3 client, `.part` rename, None when missing; runner note when not staged |
| AC10b | `stage_naip_tiles.py` | Unit test of the index mapping and the row-count assertion; the staging run's output in `evidence/` |
| AC11 | `hooks.py`, live run | `tests/test_hooks*.py`: `images` field, and an image kept through the cap rebuild; `evidence/live-cap-*.md`: trace step with `images: 1` and the output caption |
| AC12, AC16 | `retrieval.py`, `evaluate.py`, `report.py`, runner `reference_ids` | `tests/test_retrieval.py`: candidates, ranks and ties, null cases, scores keys, `retrieval.json` written and hashed, judge fields null, report section, embedder budget; hash embedder |
| AC13 | `caption_scorer_sanity.py` | offline test (hash: own reference = 1.0); live Titan run recorded |
| AC14 | `embedder.py`, configs | `tests/test_embedder.py`: hash determinism and unit norm; bedrock request body via a stubbed client; every config has `[embedder]` |
| AC15 | `expected.jsonl` | `test_tasks.py`; offline caption run gets `sequence_ok` |
| AC17 | `evaluate.py` | existing golden and evaluate tests unchanged. Two existing assertions change by design: `tests/test_paths.py:248` (`path_checker` `"v1"` becomes `"v2"`) and `tests/test_infra.py:60` (the laptop role's S3 actions gain the NAIP `ListBucket` statement) |
| AC18 | `stac.py` | existing `tests/test_stac*.py` assertions, including the network test |
| AC19 | `pyproject.toml`, requirements.md | `uv lock --check`; doc review |
| AC20 | Dockerfile, deployed worker | deploy log; `REPORT` line in evidence |

## Risks and spikes

- **Retired: images through MCP to the model.** The spike done before the design gate found that only the Claude models work (ADR-7).
- **pystac-client fake-server compatibility.** The fake server needs a landing page (`/`) with `conformsTo` including item-search, plus `/conformance`. *Spike:* run the STAC tests against the new client before rewriting the tools.
- **Live caption quality vs AC13.** If the scorer check fails, the window may be re-staged once. After that, the result is recorded (spec AC13).
- **Unverified claims:**
  - **Clay v1.5 has no text encoder** (claim 4). Not load-bearing: ADR-4 works either way.
  - **Titan V2 input limit.** Not checked against the docs. Captions are at most 60 words; a 6-word caption is 8 tokens (live).
  - Titan's dimensions (1024, 512 or 256) and the S3 list price ($0.000005) were checked live on 2026-11-03; see the spec.
- **Model-written reference captions** (spec assumption). Labelled in the data; the team reviews them. A `test_tasks.py` check asserts that all 64 references are distinct, since ties count against the caption.
- **AC8 drift:** the 13 existing judge checks were calibrated before the path check existed. With the check on, A–E's judge input now carries path evidence: A's `lp-prod-protected` comes back `unverified` (access denied), and D's invented bucket comes back `not_found`. The live run re-establishes AC8. If a check moves, that is recorded.
- **Caption gaming:** `caption_scorer_sanity.py` also scores a keyword-stuffed caption that lists every land-cover term in the references, for all 12 targets. The result is recorded, not thresholded.
- **Prompt and target drift:** if a caption prompt is edited in the UI to name another tile, `scorer.json` `targets` would disagree. The scorer checks that the prompt text contains its target tile id. If it doesn't, the output is unscored with error `prompt cap-NN names a different tile than scorer.json's target <id>`.

## Known limitations

- Reference captions, `scorer.json` and the sample are frozen per deployment once seeded (ADR-8).
- No OTel caption config: `bedrock-otel` isn't paired with the caption task, so image bytes in spans aren't exercised.

- Re-scoring a caption run from its own `retrieval.json`, without the task pack, isn't implemented. Re-evaluation uses the current references, as rubrics do.
- Laptop CLI caption runs need the staging script's local output: there is no laptop bucket any more.
- One sample of 64 tiles, from one place (El Centro, CA, 2020).

## Implementation plan

The slices are independent; the order runs from risk to value. Each slice ends with its own commit (tests green, lint clean, secrets scan clean).

1. **Spikes:**
   - (done before the gate) the mcp image helper and image calls across vision models; record `evidence/vision-spike.md`;
   - pystac-client against the STAC fake server.
2. **Slice A:** paths, config, IAM and tests.
3. **Slice D:** STAC on pystac-client; dependency bump.
4. **Slice C:**
   - **(a) Data:** the sample cache and runner placeholder; run the staging script; write the 64 reference captions from contact sheets; add the configs. Upload to the `embeddings` bucket only after AC13 passes locally (ADR-8).
   - **(b) Server:** the `naip` server and tests.
   - **(c) Scorer:** the embedder, retrieval, evaluate wiring, report and tests.
   - **(d) Sanity:** the scorer sanity script.
5. **Slice B:** fixture F, judge_sanity, and the live judge sanity run.
6. **Docs:** requirements library table, README, CHANGELOG, trace contract, attack surface, backlog.
7. **Check:** full suite and network tests, then deploy. Live runs:
   - two caption prompts;
   - for AC6, an ADB run with a crafted prompt that asks for the NAIP 2020 California `naip-analytic` prefix, recorded as crafted. If the plan doesn't cite it, `foundry evaluate` is run on an imported output that does.

   Then the memory check.
