import json
import textwrap
from pathlib import Path

from foundry.config import load_eval_config


def eval_config(tmp: Path, *, judge: list[dict] | None = None, summary: list[dict] | None = None,
                extra: str = "") -> Path:
    """An evaluate-only config. A list of turns makes that role 'scripted'; None keeps the offline stand-in."""
    tmp.mkdir(parents=True, exist_ok=True)

    def role(name, turns):
        if turns is None:
            return f'[{name}]\nprovider = "offline"\nid = "offline-{name}"\n'
        script = tmp / f"{name}.json"
        script.write_text(json.dumps(turns))
        return f'[{name}]\nprovider = "scripted"\nid = "scripted-{name}"\nscript = "{script}"\n'

    path = tmp / "eval.toml"
    path.write_text(role("judge", judge) + role("summarizer", summary) + textwrap.dedent(extra))
    return path


def load(path: Path):
    return load_eval_config(path)


def judge_reply(scores: dict[str, int], rationale="ok") -> dict:
    return {"text": json.dumps({"criteria": [{"id": k, "score": v, "rationale": rationale} for k, v in scores.items()]})}


ALL3 = {"data-fit": 3, "feasibility": 3, "method": 3, "limitations": 3}
