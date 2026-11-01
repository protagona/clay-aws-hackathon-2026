"""The monthly spend cap (aws-deploy spec A5, design ADR-8): what a job may cost at worst, reserved before it starts
and settled with what it actually cost when it finishes.

A run is bounded by tokens and calls, not dollars, so its worst case comes from its config: `max_input_tokens` ×
the agent model's input price + `max_model_calls` × `max_tokens` × its output price. A scoring's worst case is the
judge's and the summarizer's output budget plus an input bound (the evaluation view is capped at about
EVAL_INPUT_TOKENS). Every reservation carries a +10% margin (a projected input can overshoot by one call; retries
after a client timeout aren't recorded: stated gaps). The cap applies only when one is set (`Workspace.cap_usd`);
a model with no price can't start while it is.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from foundry.services.errors import ServiceError

MARGIN = 1.10
EVAL_INPUT_TOKENS = 50_000  # the view: a 20,000-character output excerpt plus step details, well under this


def month(at: float | None = None) -> str:
    return datetime.fromtimestamp(at if at is not None else time.time(), timezone.utc).strftime("%Y-%m")


def next_month_start(at: float | None = None) -> str:
    d = datetime.fromtimestamp(at if at is not None else time.time(), timezone.utc)
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return f"{y:04d}-{m:02d}-01"


def _price(prices: dict, model_id: str, config_name: str):
    p = prices.get(model_id)
    if p is None:
        raise ServiceError(409, f"No price for {model_id} in {config_name}; add it to [prices] (the spend cap needs it).")
    return p


def run_reservation(cfg, config_name: str, model_id: str | None = None) -> float:
    model = model_id or cfg.model.id
    if cfg.model.provider != "bedrock":
        return 0.0  # offline (scripted) runs make no paid calls
    p = _price(cfg.prices, model, config_name)
    lim = cfg.limits
    usd = (lim.max_input_tokens * p.input + lim.max_model_calls * lim.max_tokens * p.output) / 1e6
    return round(usd * MARGIN, 4)


def eval_reservation(cfg, config_name: str) -> float:
    total = 0.0
    for part in (cfg.judge, cfg.summarizer):
        if part is None or part.provider != "bedrock":
            continue
        p = _price(cfg.prices, part.id, config_name)
        total += (EVAL_INPUT_TOKENS * p.input + part.max_tokens * p.output) / 1e6
    return round(total * MARGIN, 4)


def batch_reservation(max_cost: float, concurrency: int, run_wc: float, eval_wc: float) -> float:
    """The batch's own cap, plus the jobs that can still be in flight once it reaches it (it checks only before
    each job): max_cost + concurrency × (run_wc + eval_wc)."""
    return round(max_cost + concurrency * (run_wc + eval_wc), 4)


def refusal(committed: float, cap: float, at: float | None = None) -> ServiceError:
    return ServiceError(409, f"This month's spend is ${committed:.2f} of the ${cap:.2f} cap; new runs are paused "
                             f"until {next_month_start(at)}.")


DRAFT_INPUT_TOKENS = 200_000  # a rubric source can be a long document


def draft_reservation(cfg, config_name: str) -> float:
    from foundry.engine.rubrics.draft import MAX_TOKENS as DRAFT_MAX_TOKENS

    if cfg.judge.provider != "bedrock":
        return 0.0
    p = _price(cfg.prices, cfg.judge.id, config_name)
    return round((DRAFT_INPUT_TOKENS * p.input + max(cfg.judge.max_tokens, DRAFT_MAX_TOKENS) * p.output) / 1e6 * MARGIN, 4)
