# The command-line tools

Everything the UI does can also be done from a terminal, a script or CI. There are two tools:

| | `foundry` | `foundry-remote` |
|---|---|---|
| What it talks to | This checkout: it runs agents and scorers itself | A Foundry server over HTTPS: a deployment, or a laptop's `foundry ui` |
| Install | `uv sync` in a clone; then `uv run foundry …` | `curl -O <url>/cli/foundry-remote.pyz`; then `python3 foundry-remote.pyz …`. From a clone it's also `uv run foundry remote …` |
| Needs | Python 3.12 (uv installs it). AWS credentials only for Bedrock configs | Python 3.9 or later, nothing else (macOS's built-in `python3` works). An access token from the deployment's operator |
| Commands | run, evaluate, validate, batch, import, receive, external, rubric, tools, ui | login, logout, whoami, runs, run show/start, evaluate, batch start/show, batches, compare, import, jobs |

Each command's `--help` gives a short version of what's below.

**Conventions for both tools:**

- **stdout and stderr:** stdout carries only results, which are paths, tables or JSON, so it's safe to capture. Progress, warnings and errors go to stderr.
- **Exit codes:**
  - `0`: success
  - `1`: the thing ran but didn't succeed, such as a failed run or a validation error
  - `2`: a usage or configuration error, where nothing ran

  The exceptions are noted under each command.
- **Paths:** `foundry` resolves relative paths in configs, and the `config/` and `tasks/` folders, against the project root. That's the current directory, or `FOUNDRY_HOME` if it's set. Run it from the repo root.

**Contents:**

- [Use it in your workflow](#use-it-in-your-workflow): recipes for scripts and CI
- [`foundry` reference](#foundry-reference)
- [`foundry-remote` reference](#foundry-remote-reference)
- [Environment variables](#environment-variables)
- [Troubleshooting](#troubleshooting)

## Use it in your workflow

Each recipe below was run as written. The output it printed is in [eval-sandbox/cli/evidence/](eval-sandbox/cli/evidence/). The recipes use `jq` for JSON.

### 1. Fail CI when a run scores too low (laptop or CI runner with the repo)

```bash
set -u
CONFIG=config/bedrock.toml        # config/offline.toml needs no AWS
out=$(uv run foundry run --config "$CONFIG" --prompt adb-001 --evaluate); status=$?
run_dir=$(head -n 1 <<<"$out")
[ "$status" -eq 2 ] && exit 2                            # config or usage error: no run happened
overall=$(jq -r '.overall // 0' "$run_dir/scores.json")  # null when the judge's reply was unusable
echo "overall $overall (run $run_dir, exit $status)"
jq -e --argjson min 0.7 '.overall != null and .overall >= $min' "$run_dir/scores.json" >/dev/null
```

`foundry run --evaluate` exits 1 when the run failed, but it still scores the failed run, so the gate reads the score either way. `jq -e` exits 1 when the condition is false.

### 2. The same gate against a deployment (no clone, no AWS account)

```bash
export FOUNDRY_URL=https://<id>.lambda-url.us-west-2.on.aws FOUNDRY_TOKEN=$FOUNDRY_TOKEN   # a CI secret
curl -fsS -o foundry-remote.pyz "$FOUNDRY_URL/cli/foundry-remote.pyz"
run=$(python3 foundry-remote.pyz --json run start --prompt adb-001 --config bedrock --wait | jq -r .run.run_id)
python3 foundry-remote.pyz --json evaluate "$run" --wait | jq -e '.scores.overall >= 0.7'
```

- **Login:** `FOUNDRY_URL` and `FOUNDRY_TOKEN` replace `login`, so nothing is saved on the runner.
- **Output:** `--json` keeps stdout pure JSON. In text mode, progress goes to stderr.
- **Timeouts:** `--wait` has no timeout of its own, so give the CI step one. A deployment ends a lost job as `interrupted` within 32 minutes.

### 3. Compare two models

```bash
uv run foundry batch --config config/bedrock.toml \
    --models us.moonshotai.kimi-k3,us.anthropic.claude-sonnet-4-6 \
    --prompts adb-001 --repeats 2 --evaluate --max-cost 2 --out batches/kimi-vs-sonnet
cat batches/kimi-vs-sonnet/summary.md          # per model: mean, min, max and stdev of the overall score
```

- **Resuming:** run the same command again; finished runs are kept.
- **Comparing two runs:** `foundry-remote compare <run A> <run B>` against a deployment, or the UI's Compare page.

### 4. Score an agent we didn't build

```bash
AWS_PROFILE=<profile> uv run foundry external --task adb --prompt adb-001 --config config/bedrock.toml --evaluate \
    -- uv run python examples/external_strands_agent.py
```

The command can be any agent that exports OpenTelemetry GenAI spans. It gets the prompt in `FOUNDRY_PROMPT` and the endpoint in `OTEL_EXPORTER_OTLP_ENDPOINT`. Its own output goes to stderr. On stdout, `foundry external` prints only one run directory per agent run, then the score lines.

### 5. Re-score saved runs against another rubric

```bash
for run in batches/kimi-vs-sonnet/runs/*/; do        # the runs of recipe 3's batch
  uv run foundry evaluate "$run" --config config/bedrock.toml --rubric path/to/clay-rubric.json | tail -n 1
done
```

- **What it changes:** `evaluate` never re-runs the agent. It replaces `rubric.json`, `scores.json` and `report.md` together.
- **Rubric files:** to make a rubric from a PDF, a spreadsheet or text first, see [`foundry rubric draft`](#foundry-rubric-draft).
- **Tracking:** each run records the rubric that scored it.

## `foundry` reference

### `foundry run`

Runs the agent on one prompt and records the run.

```bash
uv run foundry run --config config/offline.toml --prompt adb-001 [--runs-dir runs] [--run-id my-run] [--evaluate]
```

| Option | Default | Meaning |
|---|---|---|
| `--config` | required | A config file. `config/offline.toml` needs no AWS; `config/bedrock*.toml` use Bedrock |
| `--prompt` | required | A prompt id from `tasks/<task>/prompts.jsonl`, e.g. `adb-001` |
| `--runs-dir` | `runs` | Where the run directory goes |
| `--run-id` | `<time>_<prompt>_<random>` | Name the run directory yourself. It must match `[A-Za-z0-9._-]` and not exist yet |
| `--evaluate` | off | Then score it with the same config, which is `run` and `evaluate` in one command |

- **stdout:** the run directory. With `--evaluate`, two more lines follow: the report's path and `overall 0.83 | workflow 0.90 | output 0.75`.
- **stderr:** `Run failed: see <run dir>/trace.jsonl` when the run didn't succeed.
- **Exit:**
  - `0`: the run succeeded
  - `1`: it failed or timed out. The run directory is still complete, and with `--evaluate` it's still scored.
  - `2`: a bad config, unknown prompt, invalid or existing `--run-id`, or a runs directory that can't be written. No run directory is created. Also `2` when `--evaluate`'s scoring failed.
- **Writes:**
  - `run.json`: status `running` until the run ends
  - `trace.jsonl`
  - `output.json`
  - `mcp-<server>.log`

  With `--evaluate`, also `rubric.json`, `scores.json` and `report.md`. The format is the [trace contract](trace-contract.md).

### `foundry evaluate`

Summarizes and scores a recorded run. It never runs the agent, so it also works on old runs and on runs imported from other agents.

```bash
uv run foundry evaluate runs/<run id> [--config config/bedrock.toml] [--rubric path/to/rubric.json] [--force]
```

| Option | Default | Meaning |
|---|---|---|
| `<run_dir>` | required | The run directory |
| `--config` | `config/offline.toml` | Its `[judge]` and `[summarizer]` score the run. The default needs no AWS but uses deterministic stand-ins, so use a Bedrock config for real output scores |
| `--rubric` | the task's active rubric | Score against this `rubric.json` instead |
| `--force` | off | Score a run stuck at `running` (a crashed run) as failed |

- **stdout:** the report's path, then `overall … | workflow … | output …`. `overall incomplete` means the judge's reply was unusable even after one retry.
- **Exit:**
  - `0`: it wrote the scores and the report
  - `2`: a bad config or rubric, a run that isn't finished (without `--force`), a run already being scored, or files that can't be written
- **Writes:** it replaces `rubric.json`, `scores.json` and `report.md` together. It never touches `run.json`, `trace.jsonl` or `output.json`.

### `foundry validate`

Checks a run directory against the [trace contract](trace-contract.md).

```bash
uv run foundry validate runs/<run id>
```

| Option | Default | Meaning |
|---|---|---|
| `<run_dir>` | required | The run directory |

- **stdout:** one line per problem, as `<file>:<line>: <field>: <message>`. Warnings say `warning:`. For example, a crash mid-write leaves a half-written last line on a `running` run.
- **Exit:**
  - `0`: no errors (warnings allowed)
  - `1`: one or more errors
  - `2`: not a directory

### `foundry batch`

Runs prompts × variants × repeats, then summarizes them.

```bash
uv run foundry batch --config config/bedrock.toml --models <id>,<id> --prompts adb-001,adb-006 \
    [--repeats 1] [--concurrency 2] [--evaluate] [--eval-config <file>] [--max-cost 2] --out batches/demo \
    [--runs-dir <dir>] [--run-prefix <prefix>]
```

| Option | Default | Meaning |
|---|---|---|
| `--config` | required | A variant's config. Repeat it to compare configs (e.g. `bedrock` and `bedrock-pipeline`) |
| `--models` | — | Comma-separated model ids. Each becomes a variant derived from the single `--config` by changing only `[model] id` |
| `--prompts` | required | Comma-separated prompt ids, or `all` (archived prompts included) |
| `--repeats` | `1` | Runs per variant and prompt |
| `--concurrency` | `2` | Runs at once |
| `--evaluate` | off | Score each run too |
| `--eval-config` | the first variant's config | The config that scores the runs |
| `--max-cost` | none | Stop starting runs once the recorded spend in USD reaches this |
| `--out` | required | The batch directory. Running the same command again resumes it and retries crashed runs |
| `--runs-dir` | `<out>/runs` | Where run directories go |
| `--run-prefix` | none | A prefix for run ids, so batches can share a runs directory |

- **stdout:**
  - a header line
  - one line per finished job: `[3/8] <variant> adb-001 r1: succeeded overall 0.92 $0.152`
  - a closing line: `summary: <out>/summary.md (spent $… of $…)`
- **Exit:**
  - `0`: every job ran. A run that failed still counts as ran: it's scored as a failure.
  - `1`: a job couldn't run, or `--max-cost` stopped one from starting
  - `2`: a usage or config error
- **Writes:**
  - `<out>/batch.json`, the status and spend
  - `summary.md` and `summary.csv`, the mean, min, max and stdev of each variant's scores
  - `configs/`, the derived configs
  - `logs/`
  - the runs

### `foundry import`

Turns OpenTelemetry GenAI traces from any agent into run directories that `evaluate` can score.

```bash
uv run foundry import traces.jsonl --task adb [--prompt-id adb-001] [--runs-dir runs] [--config config/bedrock.toml]
```

| Option | Default | Meaning |
|---|---|---|
| `<file>` | required | OTLP/JSON, for example from the OpenTelemetry Collector's file exporter. It can be one `ExportTraceServiceRequest`, or JSON Lines of them. At most 50 MB |
| `--task` | required | The task the runs belong to, which picks the rubric `evaluate` uses |
| `--prompt-id` | `imported` | The prompt id recorded on the runs |
| `--runs-dir` | `runs` | Where the run directories go |
| `--config` | none | A config whose `[prices]` give each call's cost. Without it, cost is unknown |

- **stdout:** one run directory per `invoke_agent` span.
- **Exit:**
  - `0`: imported
  - `2`: an unreadable file, no agent runs in it, a bad config, or an invalid `--prompt-id`

### `foundry receive`

An OTLP/HTTP receiver on 127.0.0.1. Point an agent's OpenTelemetry exporter at it, and each agent run it sends becomes a run directory.

```bash
uv run foundry receive --task adb [--prompt-id adb-001] [--runs-dir runs] [--port 4318] [--config config/bedrock.toml]
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 <your agent>
```

| Option | Default | Meaning |
|---|---|---|
| `--task` | required | The task the runs belong to |
| `--prompt-id` | `imported` | The prompt id recorded on the runs |
| `--runs-dir` | `runs` | Where the run directories go |
| `--port` | `4318` | The port on 127.0.0.1, which is OTLP/HTTP's standard port |
| `--config` | none | A config whose `[prices]` give each call's cost |

- **Formats:** it accepts JSON and protobuf on `/v1/traces`.
- **stdout:** `Receiving OTLP/HTTP on http://127.0.0.1:4318/v1/traces -> runs …` once it's listening, then `run written: <run dir>` for each trace, a few seconds after its last span.
- **Exit:**
  - `0` after Ctrl-C. Traces still in that few-second wait are written first.
  - `2`: the port is taken, or a bad config.

### `foundry external`

Runs any OpenTelemetry-instrumented agent command and scores its runs. It's `receive` plus your command in one step.

```bash
uv run foundry external --task adb --prompt adb-001 [--config config/bedrock.toml] [--runs-dir runs] [--timeout 600] [--evaluate] \
    -- <command> [args…]
```

| Option | Default | Meaning |
|---|---|---|
| `--task` | required | The task: its prompts and rubric |
| `--prompt` | required | A prompt id. Its text goes to the command as `FOUNDRY_PROMPT` |
| `--config` | `config/offline.toml` | Scores the runs (with `--evaluate`) and prices them |
| `--runs-dir` | `runs` | Where the run directories go |
| `--timeout` | `600` | Kill the command after this many seconds |
| `--evaluate` | off | Score each run it receives |
| `<command>` | required | After `--`. Run as an argument list with no shell, as you, unsandboxed |

- **The command's environment:**
  - `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, pointing at a private receiver
  - `FOUNDRY_TASK_ID`, `FOUNDRY_PROMPT_ID` and `FOUNDRY_PROMPT`
- **stdout:** run directories and score lines only. The command's own output goes to stderr.
- **Exit:**
  - `0`: the command succeeded and every run succeeded
  - `1`: a run failed, or the command failed or timed out
  - `2`: no runs were received, a usage error, or a scoring error

### `foundry rubric draft`

Drafts a rubric from almost any source, using the config's judge model. The draft is for you to review before activating it.

```bash
uv run foundry rubric draft --file "Clay rubric.xlsx" [--task adb] [--config config/bedrock.toml]
uv run foundry rubric draft --text "Accuracy counts double. …"
uv run foundry rubric draft --url https://example.org/rubric.html
uv run foundry rubric draft --mcp roda/<tool> --args '{"…": "…"}'
```

| Option | Default | Meaning |
|---|---|---|
| `--file` / `--text` / `--url` / `--mcp` | one is required | The source: a pdf, docx, xlsx, csv, md, txt, html or json file; a few sentences; an https URL; or `SERVER/TOOL` from the config's `[mcp.servers]` |
| `--args` | none | JSON arguments for `--mcp` |
| `--task` | `adb` | The task under `tasks/` |
| `--config` | `config/bedrock.toml` | Its `[judge]` drafts the rubric. The offline judge can't draft: it refuses rather than inventing a rubric |

- **stdout:**
  - `draft <id>: valid`, or `INVALID: <why>`
  - any `note:` lines, one for each interpretation the model made
  - the path to review, and the `activate` command to run next
- **Exit:**
  - `0`: a valid draft
  - `1`: an invalid draft, saved so you can fix it in the UI
  - `2`: an unreadable source, an unknown task, or a judge that can't draft
- **Writes:** `tasks/<task>/rubrics/drafts/<id>/`, which is git-ignored.

### `foundry rubric list`

Shows the active rubric, its history and the pending drafts.

```bash
uv run foundry rubric list [--task adb] [--runs-dir runs]
```

| Option | Default | Meaning |
|---|---|---|
| `--task` | `adb` | The task under `tasks/` |
| `--runs-dir` | `runs` | Used to count how many runs each version scored |

- **stdout:** one line per version: `*` for the active one, then its file name, when it was activated, and how many runs it scored. Then one line per draft.
- **Exit:**
  - `0`
  - `2`: an unknown task

### `foundry rubric activate`

Activates a reviewed draft, or rolls back to a version from the history.

```bash
uv run foundry rubric activate <draft id> [--task adb]
uv run foundry rubric activate adb-rubric-v0@0.1.json      # a history file name, as `list` shows it
```

| Option | Default | Meaning |
|---|---|---|
| `<target>` | required | A draft id, or a history file name (it ends in `.json`) |
| `--task` | `adb` | The task under `tasks/` |

- **stdout:** `active: <rubric id> v<version> (<file>)`.
- **Exit:**
  - `0`
  - `2`: an unknown draft or version, or an invalid draft
- **Writes:** the old `rubric.json` goes into `tasks/<task>/rubrics/history/`, and the new one replaces it. Commit both.

### `foundry rubric discard`

Deletes a draft.

```bash
uv run foundry rubric discard <draft id> [--task adb]
```

| Option | Default | Meaning |
|---|---|---|
| `<draft_id>` | required | A draft id, as `list` shows it |
| `--task` | `adb` | The task under `tasks/` |

- **stdout:** `discarded <draft id>`.
- **Exit:**
  - `0`
  - `2`: an unknown draft

### `foundry tools`

Lists every tool of every MCP server in `config/*.toml`, with descriptions. The UI's New run panel uses this list.

```bash
uv run foundry tools [--out tools.json]
```

| Option | Default | Meaning |
|---|---|---|
| `--out` | print to stdout | Write the JSON to this file instead, replacing it in one step. If no server could be listed, the file isn't touched |

- **stdout:** JSON, as `{listed_at, servers: [{name, package, version, error}], tools: [{server, name, description, …}], skipped_configs}`.
- **What it does:** it starts each server, and gives up after 10 minutes.
- **Exit:**
  - `0`: at least one server was listed. Failed servers are named on stderr.
  - `1`: no server could be listed

### `foundry ui`

Serves the web UI and its API on 127.0.0.1 only.

```bash
uv run foundry ui [--port 8765] [--runs-dir runs]
```

| Option | Default | Meaning |
|---|---|---|
| `--port` | `8765` | The port on 127.0.0.1 |
| `--runs-dir` | `runs` | The runs it shows and writes |

- **stdout:** `Foundry UI on http://127.0.0.1:8765`.
- **Stopping:** it runs until Ctrl-C.
- **Scripting it:** use `foundry-remote` (`login http://127.0.0.1:8765 any`), or the API at `/api/*`. Writes need the `X-CSRF-Token` header from `GET /api/session`.

### `foundry remote`

The same program as `foundry-remote.pyz`, run from a clone: `uv run foundry remote <command> …`. Its errors start with `foundry remote:`.

## `foundry-remote` reference

```bash
curl -O https://<id>.lambda-url.us-west-2.on.aws/cli/foundry-remote.pyz    # no token needed: it holds no secrets
python3 foundry-remote.pyz login https://<id>.lambda-url.us-west-2.on.aws/ <token>
python3 foundry-remote.pyz runs
```

- **Runs on:** Python 3.9 or later, standard library only. It's tested on macOS's built-in Python 3.9, on CPython 3.10 and on 3.12.
- **`--json`:** before or after the command, it prints the API's JSON instead of text, for example `python3 foundry-remote.pyz runs --json | jq`.
- **Exit codes:**
  - `0`: success
  - `1`: an API or network error, with the server's message on stderr, or a waited-for run, scoring or batch that didn't succeed
  - `2`: a usage error, or not logged in
  - `130`: Ctrl-C
- **Limits a server enforces:** it prints these as errors with exit 1.
  - One run and one batch at a time: `A run is already in progress: <id>`.
  - A run can't be scored twice at once: `Run <id> is already being scored.`
  - On a deployment, the monthly spend cap: `This month's spend is $… of the $… cap; new runs are paused until …`.
- **Against a laptop's server:** `login http://127.0.0.1:8765 <anything>` works too. A laptop's server has no sign-in.

### `foundry-remote login`

Checks a URL and token against the server, then saves both.

| Option | Default | Meaning |
|---|---|---|
| `<url>` | required | The deployment's URL (`https://`), or `http://127.0.0.1:<port>` / `http://localhost:<port>` for a laptop |
| `<token>` | required | Your access token (`fdy_…`) |

- **stdout:** `Logged in to <url> as <name> (saved to <path>).`
- **Writes:** `~/.config/foundry/remote.json` (or under `$XDG_CONFIG_HOME`), readable only by you (mode 0600). Nothing is saved if the check fails.
- **Exit:**
  - `0`
  - `1`: the server can't be reached, or the token was refused
  - `2`: not an https URL

### `foundry-remote logout`

Deletes the saved login. If nothing is saved, it still exits 0.

- **stdout:** `Forgot the saved login (<path>).`

### `foundry-remote whoami`

Who the token belongs to.

- **stdout:** `<name> at <url>`. On a laptop's server, it's `(no sign-in on this server) at <url>`.

### `foundry-remote runs`

Lists runs, newest first.

| Option | Default | Meaning |
|---|---|---|
| `--limit` | all | Show only the newest N |

- **stdout:** a table with RUN, PROMPT, MODEL, STATUS, OVERALL and COST, then a line `<n> runs · <n> scored · mean … · spent $… · <n> live`.
- **`--json`:** `{runs: [cards…], kpis: {…}}`.

### `foundry-remote run show`

One run's status, cost and scores.

| Option | Default | Meaning |
|---|---|---|
| `<run_id>` | required | A run id, as `runs` lists it |

- **stdout:** the run id, then indented `prompt`, `model`, `status`, `steps` and `scores` lines.
- **Exit:**
  - `0`
  - `1`: `Run not found`

### `foundry-remote run start`

Starts a run of one prompt.

| Option | Default | Meaning |
|---|---|---|
| `--prompt` | required | A prompt id, like `adb-001` |
| `--config` | required | A config name, like `bedrock` (the file name without `.toml`) |
| `--wait` | off | Wait for it to end, then show it |

- **stdout:** `Started <run id>`. With `--wait`, the `run show` text, and a progress line on stderr meanwhile.
- **Exit:**
  - `0`
  - `1`: an unknown prompt or config, another run in progress, the spend cap, or with `--wait` a run that didn't succeed. The run's text is still printed.

### `foundry-remote evaluate`

Scores a run with the server's judge.

| Option | Default | Meaning |
|---|---|---|
| `<run_id>` | required | A run id |
| `--wait` | off | Wait for the scores, then show the run |

- **stdout:** `Scoring <run id>`. With `--wait`, the `run show` text.
- **Exit:**
  - `0`
  - `1`: an unknown run, the run is already being scored, or with `--wait` the scoring failed

### `foundry-remote batch start`

Runs prompts × models × repeats on the server.

| Option | Default | Meaning |
|---|---|---|
| `--config` | required | A config name, like `bedrock` |
| `--prompts` | required | Comma-separated prompt ids |
| `--models` | the config's model | Comma-separated model ids, as the UI's New batch lists them |
| `--repeats` | `1` | 1 to 5 |
| `--concurrency` | `2` | Runs at once, 1 to 4 |
| `--evaluate` | off | Score each run |
| `--max-cost` | `2` | Stop starting runs past this many USD. Above 0 and at most 50 |
| `--wait` | off | Wait for it to finish, then show it |

- **stdout:** `Started batch <batch id>`. With `--wait`, the `batch show` text, and a progress line on stderr meanwhile.
- **Exit:**
  - `0`
  - `1`: a limit is out of range, another batch is running, or with `--wait` the batch didn't finish cleanly (a run couldn't start, or the cost cap stopped it)

### `foundry-remote batch show`

One batch's progress and rows.

| Option | Default | Meaning |
|---|---|---|
| `<batch_id>` | required | A batch id, as `batches` lists it |

- **stdout:** `<batch id>: <status> · <done>/<jobs> done · spent $… of $…`, then a table with VARIANT, PROMPT, REPEAT, STATUS, OVERALL and COST.
- **Exit:**
  - `0`
  - `1`: `Batch not found`. In the first seconds after `batch start`, the batch has no record yet, so wait a moment, or use `batch start --wait`.

### `foundry-remote batches`

Lists batches.

- **stdout:** a table with BATCH, STATUS, DONE, SPENT and MODELS, or `No batches yet.`

### `foundry-remote compare`

Two runs side by side.

| Option | Default | Meaning |
|---|---|---|
| `<a>` | required | Run id A |
| `<b>` | required | Run id B |

- **stdout:** `A = …` and `B = …`, then a table of the scores, components, cost and limits used, with `B − A` for each.
- **Exit:**
  - `0`
  - `1`: either run is unknown

### `foundry-remote import`

Uploads an OTLP/JSON trace file. Each agent run in it becomes a run on the server.

| Option | Default | Meaning |
|---|---|---|
| `<file>` | required | OTLP/JSON, as for [`foundry import`](#foundry-import). At most 50 MB |
| `--task` | `adb` | The task the runs belong to |
| `--prompt-id` | `imported` | The prompt id recorded on the runs |
| `--config` | none | A config name whose prices give the cost |

- **stdout:** `Imported <n> run(s):`, then the run ids.
- **Exit:**
  - `0`
  - `1`: the server rejected the file (not a trace, or too large)
  - `2`: no such local file

### `foundry-remote jobs`

Lists the server's jobs: runs, scorings, batches and imports.

- **stdout:** a table with JOB, KIND, RUN, STATUS (running, done or failed) and MESSAGE, or `No jobs.`

## Environment variables

| Variable | Read by | Meaning |
|---|---|---|
| `AWS_PROFILE`, `AWS_REGION` | `foundry` | Credentials for Bedrock configs. The region is `us-west-2` |
| `FOUNDRY_HOME` | `foundry` | The project root: `config/`, `tasks/` and the base for relative paths in configs. The default is the current directory |
| `STAC_API_URL` | the STAC MCP server | Another STAC API instead of Earth Search |
| `FOUNDRY_URL`, `FOUNDRY_TOKEN` | `foundry-remote` | Override the saved login, each one on its own |
| `XDG_CONFIG_HOME` | `foundry-remote` | Where the login is saved. The default is `~/.config` |
| `FOUNDRY_TASK_ID`, `FOUNDRY_PROMPT_ID`, `FOUNDRY_PROMPT` | the command `foundry external` runs | Set for it, together with the `OTEL_EXPORTER_OTLP_*` variables |

## Troubleshooting

| Message | What to do |
|---|---|
| `Unknown prompt id 'x' for task 'adb'. Available: …` | Use one of the listed ids, or add the prompt in the UI's Prompts page or in `tasks/<task>/prompts.jsonl`. |
| `foundry-remote: Not logged in` | Run `login <url> <token>`, or set `FOUNDRY_URL` and `FOUNDRY_TOKEN`. |
| `foundry-remote: Can't reach <url>: …` | Check the URL, as `scripts/cloud.sh status` prints it. After a `destroy` and `deploy` the URL changes. A deployment that's been stopped (`cloud.sh stop`) serves nothing. |
| `A run is already in progress: <id>` | One run at a time per server. Wait for it with `run show <id>`, or start a batch instead. |
| `This month's spend is $… of the $… cap` | The deployment's operator can raise the cap (`cloud.sh deploy --cap <usd>`). Offline configs still run. |
| `--wait` never returns | A lost job ends as `interrupted` within 32 minutes on a deployment. Give CI steps a timeout. |
| A Bedrock run fails at once with an access error | Run `aws sso login`, and check that the model is enabled in us-west-2 ([bedrock-models.md](eval-sandbox/bedrock-models.md)). |
