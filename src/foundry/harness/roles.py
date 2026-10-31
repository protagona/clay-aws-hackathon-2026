"""Multi-agent pipeline roles (increment 7): versioned role prompts, the messages between agents, the checker verdict."""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_VERSION = "roles-v1"
_DIR = Path(__file__).with_name("roles") / "v1"
ROLES = ("planner", "executor", "checker")
PREVIEW_CHARS = 200  # agent_step text: a preview of the agent's input
_VERDICT_RE = re.compile(r"^VERDICT:\s*(approve|revise)\b", re.IGNORECASE)
_MARKS = str.maketrans("", "", "*_`")  # Markdown emphasis anywhere on the line: **VERDICT:** revise
_TAGS = ("plan", "candidate_answer", "checker_feedback")


def role_prompt(role: str) -> str:
    return (_DIR / f"{role}.txt").read_text()


def system_prompt(task_system: str, role: str) -> str:
    """The task's own system prompt first (what the job is), then the role's (how this agent contributes)."""
    return f"{task_system.rstrip()}\n\n{role_prompt(role)}" if task_system.strip() else role_prompt(role)


def user_message(task_prompt: str, *, plan: str | None = None, candidate: str | None = None,
                 feedback: str | None = None) -> str:
    """The task, plus earlier agents' text in tags the role prompts declare to be data."""
    parts = [f"Task:\n{task_prompt}"]
    for tag, text in (("plan", plan), ("candidate_answer", candidate), ("checker_feedback", feedback)):
        if text is not None:
            parts.append(f"<{tag}>\n{_inert(text)}\n</{tag}>")
    return "\n\n".join(parts)


def _inert(text: str) -> str:
    """Neutralise our wrapper tags inside agent text, so it cannot close its own tag early."""
    for tag in _TAGS:
        text = re.sub(rf"<\s*(/?)\s*{tag}\s*>", rf"<\1{tag.upper()}-TAG>", text, flags=re.IGNORECASE)
    return text


def parse_verdict(text: str) -> str | None:
    """'approve' | 'revise' from the last line that starts with "VERDICT:" (Markdown emphasis/heading marks ignored);
    None if there is none. Asked to lead with it, Kimi K3 put its reasoning first (live run, 2026-10-31)."""
    found = None
    for line in (text or "").splitlines():
        m = _VERDICT_RE.match(line.translate(_MARKS).strip().lstrip("#> \t"))
        if m:
            found = m.group(1).lower()
    return found
