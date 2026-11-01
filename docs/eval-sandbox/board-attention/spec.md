# Board: say why a run needs attention — Spec

**Track:** Quick   **Date:** 2026-11-01   **Status:** asked for by the user, 2026-11-01

## Problem

A run in **Needs attention** shows only "failed" and a **Score it →** link. The user couldn't tell what had gone wrong or what to do about it. Seen live: a batch run (Haiku 4.5, `adb-005`) that had hit the per-call output-token limit was already scored (0.33), yet the card still offered **Score it →**. Scoring it again would cost money and give the same result.

## Acceptance criteria

- **B1.** A card in **Needs attention** for a run that failed, timed out or was interrupted shows one short line saying why. The line comes from the run's own trace and record, in this order of precedence:

  | Recorded in the run | The card says |
  |---|---|
  | status `interrupted` | Its worker stopped before finishing |
  | a `limit` note `max_model_calls (N) reached` | Hit the model-call limit (N) |
  | a `limit` note `max_input_tokens (N) reached` | Hit the input-token limit (N) |
  | a `limit` note `max_seconds (N) reached`, or status `timeout` | Hit the time limit (N s) |
  | a `limit` note `hard timeout…` | Timed out, and the agent didn't stop |
  | an `mcp_start` note | A tool server didn't start |
  | an error naming `MaxTokensReachedException` | Hit the output-token limit; the answer was cut off |
  | another `agent_error` note, or an `llm_call` with status `error` | Model call failed: `<exception name>` / Agent error: `<exception name>` |
  | none of these | No final answer |

  The first matching row wins. Exception names are capped at 60 characters, and nothing else from the error text (prompts, model output) appears.
- **B2.** **Score it →** shows only on attention cards with no overall score: "not evaluated", or an "incomplete" scoring. A failed run that already has a score has no link.
- **B3.** It works the same on a laptop and in the cloud. In the cloud the reason is stored with the card when the worker indexes the run. The one failed run already in the sandbox is re-indexed so it shows its reason.
- **B4.** A run whose trace can't be read still gets a card. The reason is then absent, or "No final answer".

## Non-goals

- Dismissing or deleting runs from the board.
- Changing what counts as "Needs attention".
- Changing limits such as `max_tokens`.

## Assumptions and open questions

- Reading a failed run's trace each time the board loads on a laptop is cheap. Failed runs are few, and the trace is already read for unscored runs' cost.

## Plan

1. `services/runs.py`: add a `failure_reason(status, steps)` that implements B1's table. `card_extras` returns `reason` for failed, timed-out and interrupted runs. The cloud's interrupted cards get the B1 text where the board marks them.
2. `api/schemas.py`: add `Card.reason: str | None`, then regenerate `web/src/types.ts`.
3. `Board.tsx`: on attention cards, show the reason line and apply B2's condition to **Score it →**.
4. Tests: each row of the B1 table, the cloud index carrying `reason`, and the board UI for B1 and B2.
5. Deploy, re-index the sandbox's failed run, and check the live board.
