# One code path for the laptop and AWS — Launch

**Status:** Launched in the sandbox, 2026-11-01. Build `14e8c38`, API and worker on alias `live`.

## What changes for whom

- **Contributors:**
  - Write a feature once, following "Writing a feature once" in `docs/architecture.md`.
  - API tests take the `backend` fixture and run on both setups.
  - `tests/test_architecture.py` refuses cloud code in `services/` and `api/`.
- **Users of the sandbox:**
  - Nothing to do.
  - A warm instance no longer shows prompts created on another instance as unknown.
  - Cloud batches also write `summary.md`.
  - A laptop run whose process died shows as interrupted.
- **Operator:** a new command, `scripts/cloud.sh reindex`, to run after a deploy that adds a board-card field (`docs/deploy.md`, "Re-indexing").

## Shipping it elsewhere (another account, or the hackathon account)

1. `scripts/cloud.sh deploy`. Nothing changed in the infrastructure: same stacks, same event shapes, and no data migration.
2. Optional: `scripts/cloud.sh reindex`. It's safe beside live work and safe to re-run; 151 runs took 38 s cold.

## Watching it

- **Health:** the existing alarms (API errors, the probe, invocations) and `scripts/cloud.sh logs api|worker`.
- **Signs of trouble specific to this change:**
  - a 409 "Changed by someone else; reload" on a page that wasn't open twice. That's the version check: reload the page.
  - a board card stuck in "running" after its run ended. Run `reindex`.

## Rolling back

Check out the previous commit (`cdbd960` or later) and run `scripts/cloud.sh deploy`.
- **Stored data:** the run index's card and summary shape is unchanged, so old code reads what new code wrote, and the reverse.
- **New files:** cloud batch trees gain `summary.md`, and `batch.json` gains `runs_dir`; old code ignores both.
- **Commands:** `reindex` disappears with the old `cloud.sh`.
