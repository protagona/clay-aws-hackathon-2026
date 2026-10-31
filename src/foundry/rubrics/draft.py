"""Draft a Rubric from a Source with the config's judge model (increment 8, D1-D4).

Binary sources (pdf/docx/xlsx/csv) go to Bedrock Converse as document blocks; text goes inside <source> tags. The reply
must be one JSON object that validates as a Rubric; an invalid reply gets one repair attempt, then is returned as
invalid for the person reviewing it to fix or discard. Nothing here activates anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from foundry.contract import Rubric
from foundry.models.base import CompletionRequest, Provider
from foundry.rubrics.sources import BINARY, Source

PROMPT_ID = "draft-v2"  # v2: refuses sources that are not rubrics instead of inventing one (live finding)
_PROMPT = Path(__file__).with_name("prompts") / f"{PROMPT_ID}.txt"
MAX_TOKENS = 8192
INSTRUCTION = "Convert the rubric in the attached source document into the JSON rubric described above."


@dataclass
class DraftResult:
    rubric: dict | None  # the validated rubric (as JSON-ready dict), or None when the reply never validated
    raw: str = ""  # the last model reply (or the source itself for the JSON shortcut)
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    model: str | None = None
    prompt_id: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    partial: dict | None = None  # whatever JSON parsed from an invalid reply, to prefill the review form


def validation_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"]) or "(rubric)"
    return f"{where}: {err['msg'].removeprefix('Value error, ')}"


def _first_object(text: str) -> dict | None:
    """The first top-level JSON object in a reply (tolerates prose or code fences around it)."""
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj if isinstance(obj, dict) else None
        except ValueError:
            start = text.find("{", start + 1)
    return None


MAX_WEIGHT = 100  # finite and bounded: Infinity or 1e308 would break the weighted score
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def canonical(obj: dict, task_id: str) -> dict:
    """Only the fields the review form shows (so nothing unreviewed reaches the judge), levels 1..max only, and the
    target task. Raises ValidationError/ValueError when the result is not a valid rubric."""
    crits = obj.get("criteria") if isinstance(obj.get("criteria"), list) else []
    out = {"rubric_id": obj.get("rubric_id"), "task_id": task_id, "version": obj.get("version"),
           "description": obj.get("description") or "", "criteria": []}
    for c in crits:
        if not isinstance(c, dict):
            continue
        levels = c.get("levels") if isinstance(c.get("levels"), dict) else {}
        mx = c.get("max")
        keep = {str(i) for i in range(1, mx + 1)} if isinstance(mx, int) and not isinstance(mx, bool) and mx < 100 else set()
        weight = c.get("weight", 1)
        if isinstance(weight, (int, float)) and not isinstance(weight, bool) and not (0 < weight <= MAX_WEIGHT):
            raise ValueError(f"criteria.{len(out['criteria'])}.weight: must be above 0 and at most {MAX_WEIGHT}")
        out["criteria"].append({"id": c.get("id"), "name": c.get("name"), "description": c.get("description"),
                                "max": mx, "weight": weight,
                                "levels": {k: v for k, v in levels.items() if k in keep}})
    rubric = Rubric.model_validate(out)
    if not NAME_RE.match(rubric.rubric_id):
        raise ValueError("rubric_id: use letters, digits, '.', '_' or '-' only (max 100)")
    if not VERSION_RE.match(rubric.version):
        raise ValueError("version: use letters, digits, '.', '_' or '-' only (max 40)")
    return rubric.model_dump(include={"rubric_id", "task_id", "version", "description", "criteria"})


def check(obj: dict, task_id: str) -> tuple[dict | None, str | None]:
    """(canonical rubric, None) or (None, error message)."""
    try:
        return canonical(obj, task_id), None
    except ValidationError as exc:
        return None, validation_error(exc)
    except ValueError as exc:
        return None, str(exc)


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
    except ValueError:
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
        obj = _first_object(done.text)
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
