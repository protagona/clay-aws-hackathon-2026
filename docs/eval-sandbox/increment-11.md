# Increment 11: the Runs board's key numbers follow its filters

**Track:** Quick   **Status:** Built (verified 2026-10-31)   **Date:** 2026-10-31

## Problem

On the Runs board, the model, prompt and search filters change which cards show, but the four key-number cards (Runs, Mean overall, Live now, Spent) always describe every run. With "kimi-k3" picked, the board shows 19 scored cards while the cards still say 73 runs and the spend of all 73.

## Acceptance criteria

- **K1:** With any filter set, every key-number card describes only the runs the board shows:
  - **Runs:** how many, with "of N" (all runs) and how many of them started today
  - **Mean overall:** their mean, still over the active rubric only, with how many are scored
  - **Live now:** how many are running or evaluating
  - **Spent:** their runs plus evaluations
- **K2:** With no filter set, the numbers equal today's (the server's totals).
- **K3:** The numbers come from the same rules as the server's totals: each card carries its own share (`kpi: {spent, overall}`, where `overall` is set only when the run counts toward the active-rubric mean). The board adds up the shares of the cards it shows. Unreadable runs count as runs and add no spend.
- **K4:** Tests:
  - API: the card shares add up to the server's totals
  - Vitest: picking a model changes the Runs, Mean and Spent cards to the filtered runs' numbers
  - e2e: still passes

## Non-goals

- The Dashboard tab's KPIs; it has its own rubric filter.
- Filters in the URL.

## Verification (2026-10-31)

**Suites:** Python 322 passed; Vitest 10 passed; ruff and gitleaks clean; the e2e (`scripts/ui_e2e.mjs`) passes.

| AC | How it was proven |
|---|---|
| K1 | Vitest `key numbers › follow the model filter`: picking a model changes Runs (with "of 3"), today, Mean and its scored count, Live and Spent to the filtered runs' numbers. **Live, on the real 74 runs:** with kimi-k3 picked, the cards read 19 runs "of 74 · +17 today", mean 0.97 over 19 scored, $3.33. Adding the search "adb-005" gives 2 runs, 0.98 and $0.30. Both match totals computed independently from `/api/runs`. Screenshot: [board-filtered-kimi-k3](increment-11-screenshots/board-filtered-kimi-k3.png). |
| K2 | Unfiltered, the board shows the server's `kpis` unchanged: 74 runs, 0.94, $13.48 live; `$1.15` and `0.70` in Vitest. |
| K3 | `test_api.py::test_card_kpi_shares_add_up_to_the_totals`: the per-card `kpi.spent` adds up to `kpis.spent` (one fixture given a real $0.25 cost so the sum isn't trivially 0). The cards with `kpi.overall` set equal `kpis.scored` and average to `kpis.mean`. An unreadable run carries `{spent: 0, overall: null}`. |
| K4 | All of the above; the e2e still passes. |

**Self-review (Quick):**
- The shares come from the same code as the totals: `dashboard.costs()` is now used by both the dashboard and the board, and the mean rule is the existing "active rubric only".
- "Today" is the UTC date on both sides.
- There are no new write paths, and the endpoint is unchanged apart from one extra field per card.
- A run that appears between the server reading the cards and computing the totals gets a $0 share until the next poll, 2 to 5 s later.

## Handoff

- **Shipped:** the four key-number cards follow the model, prompt and search filters. The Runs card shows "of N" while filtered.
- **Ruling:** this was decided without a mockup round, since it reuses the existing cards and adds only the "of N" sub-line.
- **Still open from increment 9:** whether "Spent" should cover runs plus evaluations (the current default) or only runs scored with the active rubric.
- **Follow-ups (not done):** keep the filters in the URL; the Dashboard tab's KPIs have their own rubric filter and are unchanged.
