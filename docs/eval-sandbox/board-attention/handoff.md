# Board: say why a run needs attention — Handoff

**Spec:** ./spec.md   **Date:** 2026-11-01   **Track:** Quick   **Build:** `85f0f20`, live in the sandbox (version 10)

## What shipped

- **Why a run failed:** a card in Needs attention says why the run failed, in a few words worked out from the run's own trace. Examples: "Hit the output-token limit; the answer was cut off", "Hit the model-call limit (12)", "Its worker stopped before finishing". The words come only from note names, limit texts and exception names, never from prompt or model text.
- **Score it →:** the link shows only when the run has no overall score. A failed run that's already scored no longer offers to be rescored for the same result.
- **The cloud:** workers store the reason with the card. The sandbox's one failed run (Haiku 4.5, `adb-005`, cut off at `max_tokens = 4096`) was re-indexed so it shows its reason.

## Verification

| AC | Proof |
|---|---|
| B1 | `test_failure_reason`: one case per row of the spec's table, plus no error text beyond the exception name. `test_runs_kpis_and_columns`: the failed fixture's card through `/api/runs` |
| B2 | Vitest: "says why a run failed, and offers no rescoring once it has a score" |
| B3 | `test_a_failed_runs_index_card_says_why` (the worker's index); the interrupted overlay in `test_cloud_api.py`; live: reindex.txt, attention.log (PASS), attention.png |
| B4 | the unreadable-card path is unchanged (`test_runs_kpis_and_columns`'s broken run) |

**Suites:** pytest 452 passed (4 network tests deselected), Vitest 38 passed, `tsc` clean, ruff clean. The new tests fail without the change (16 Python, 1 Vitest). gitleaks found no leaks.

## Notes

- **The deploy order mattered:** `Card` is a closed model, so the old API would have returned a 500 for a board whose stored card had `reason`. The run was re-indexed only after `85f0f20` was live.
- **Gate:** Quick-track self-review. Each AC is tested, and the live check ran on the real board.
- **Deferred:**
  - dismissing a run from the board (a non-goal)
  - on a laptop, the board reads each failed run's trace on every refresh; cheap while failed runs are few
