"""Draft a Rubric from a Source with the config's judge model (increment 8, D1-D4).

Binary sources (pdf/docx/xlsx/csv) go to Bedrock Converse as document blocks; text goes inside <source> tags. The reply
must be one JSON object that validates as a Rubric; an invalid reply gets one repair attempt, then is returned as
invalid for the person reviewing it to fix or discard. Nothing here activates anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


from foundry.contract import Rubric
from foundry.models.base import CompletionRequest, Provider
from foundry.data.rubrics import DraftResult, Source, check  # noqa: F401 (DraftResult: re-exported for callers)
from foundry.rubrics.sources import BINARY

PROMPT_ID = "draft-v3"  # v2: refuses non-rubrics (live finding); v3: max 2-20, wider scales banded (verify finding)
_PROMPT = Path(__file__).with_name("prompts") / f"{PROMPT_ID}.txt"
MAX_TOKENS = 8192
INSTRUCTION = "Convert the rubric in the attached source document into the JSON rubric described above."


def _first_object(text: str) -> dict | None:
    """The first top-level JSON object in a reply (tolerates prose or code fences around it)."""
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])  # RecursionError is handled by the caller
            return obj if isinstance(obj, dict) else None
        except ValueError:
            start = text.find("{", start + 1)
    return None


def _check(obj: dict | None, task_id: str) -> tuple[dict | None, list[str], str | None]:
    if obj is None:
        return None, [], "the reply contained no JSON object"
    if isinstance(obj.get("error"), str) and not obj.get("criteria"):
        return None, [], f"This doesn't look like a rubric ({obj['error'][:300]}). Try another source."
    raw_notes = obj.pop("notes", None)
    notes = [str(n)[:500] for n in (raw_notes if isinstance(raw_notes, list) else [raw_notes] if isinstance(raw_notes, str)
                                    else []) if isinstance(n, (str, int, float)) and not isinstance(n, bool)][:20]
    rubric, error = check(obj, task_id)
    return rubric, notes, error


def as_rubric_json(source: Source, task_id: str) -> dict | None:
    """I5: a source that already is a valid Rubric needs no model."""
    if source.fmt not in ("json", "txt", "md"):
        return None
    try:
        obj = json.loads(source.data)
    except (ValueError, RecursionError):  # RecursionError: "[" * 40000
        return None
    return check(obj, task_id)[0] if isinstance(obj, dict) else None


def draft(source: Source, task_id: str, provider: Provider) -> DraftResult:
    direct = as_rubric_json(source, task_id)
    if direct is not None:
        return DraftResult(direct, raw=source.data.decode("utf-8", "replace"), notes=["Source was already a valid rubric."])
    schema = json.dumps(Rubric.model_json_schema(), separators=(",", ":"))
    system = _PROMPT.read_text().replace("{{SCHEMA}}", schema).replace("{{TASK_ID}}", task_id)
    if source.fmt in BINARY:
        user, docs = INSTRUCTION, ((source.fmt, source.data),)
    else:
        text = re.sub(r"<\s*/?\s*source\s*>", "<SOURCE-TAG>", source.data.decode("utf-8", "replace"), flags=re.IGNORECASE)
        user, docs = f"<source>\n{text}\n</source>\n\n{INSTRUCTION.replace('attached source document', 'source above')}", ()
    result = DraftResult(None, model=provider.model_id, prompt_id=PROMPT_ID)
    for attempt in range(2):
        req = CompletionRequest(system=system, user=user, max_tokens=MAX_TOKENS, purpose="rubric", documents=docs)
        done = provider.complete(req)
        result.tokens_in += done.tokens_in
        result.tokens_out += done.tokens_out
        result.raw = done.text
        try:
            obj = _first_object(done.text)
        except RecursionError:
            obj = None
        result.partial = dict(obj) if obj else None
        rubric, notes, error = _check(obj, task_id)
        result.notes, result.error = notes, error
        if rubric is not None:
            result.rubric, result.partial = rubric, None
            return result
        if (obj or {}).get("error") and not (obj or {}).get("criteria"):  # a refusal is final: no repair attempt
            return result
        if attempt == 0:
            user = (f"{user}\n\nYour previous reply was not a valid rubric: {error}\n"
                    f"Previous reply:\n{done.text[:6000]}\n\nReply again with only the corrected JSON object.")
    return result
