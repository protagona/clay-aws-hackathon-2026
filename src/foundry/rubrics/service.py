"""One entry point for the CLI and the UI: acquire a source, draft it with the config's judge, save the draft."""

from __future__ import annotations

import json
from pathlib import Path

from foundry.rubrics import sources
from foundry.rubrics.draft import MAX_TOKENS as DRAFT_MAX_TOKENS
from foundry.rubrics.draft import draft
from foundry.rubrics.sources import SourceError
from foundry.data.rubrics import Store


def acquire(kind: str, cfg, *, file_name: str = "", file_data: bytes = b"", text: str = "", url: str = "",
            mcp: str = "", args: str = "") -> sources.Source:
    if kind == "file":
        return sources.from_file(file_name, file_data)
    if kind == "text":
        return sources.from_text(text)
    if kind == "url":
        return sources.from_url(url)
    if kind == "mcp":
        server, _, tool = (mcp or "").partition("/")
        if not server or not tool:
            raise SourceError("Name the tool as SERVER/TOOL, e.g. roda/get_dataset_details")
        try:
            parsed = json.loads(args) if (args or "").strip() else {}
        except ValueError as exc:
            raise SourceError(f"Tool arguments are not valid JSON: {exc}") from None
        return sources.from_mcp(cfg, server, tool, parsed)
    raise SourceError(f"Unknown source kind '{kind}'")


def create_draft(task_dir: Path, source: sources.Source, cfg) -> tuple[str, dict]:
    """Draft with cfg.judge and save; returns (draft_id, meta). Provider errors propagate (ProviderError)."""
    from foundry.models.registry import build_provider

    # Its own output budget: a big rubric (criteria x levels + notes) must not be cut at the judge's max_tokens.
    role = cfg.judge.model_copy(update={"max_tokens": max(cfg.judge.max_tokens, DRAFT_MAX_TOKENS)})
    result = draft(source, Path(task_dir).name, build_provider(role))
    store = Store(task_dir)
    draft_id = store.save_draft(source, result)
    price = cfg.prices.get(result.model) if result.model else None
    meta, _, _ = store.load_draft(draft_id)
    if price is not None:
        meta["usage"]["cost_usd"] = round(price.cost(result.tokens_in, result.tokens_out), 6)
        (Path(task_dir) / "rubrics" / "drafts" / draft_id / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return draft_id, meta
