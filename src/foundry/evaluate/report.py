"""report.md: everything a domain expert needs, with every run-derived string escaped (spec AC10c)."""

from __future__ import annotations

import re

from foundry.contract import Output, RunRecord, Step

_MD_SPECIAL = re.compile(r"([\\`*_\[\]!#])")
OUTPUT_EXCERPT_CHARS = 4000


def esc(text) -> str:
    """Inert Markdown: no raw HTML, links, images, emphasis or headings; one line."""
    s = "" if text is None else str(text)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = _MD_SPECIAL.sub(r"\\\1", s)
    return " ".join(s.split())


def cell(text) -> str:
    return esc(text).replace("|", "\\|")


def _fmt(score) -> str:
    return "n/a" if score is None else f"{score:.2f}"


def build(run: RunRecord, steps: list[Step], output: Output, scores: dict) -> str:
    w, o = scores["workflow"], scores["output"]
    lines = [f"# Evaluation report: {esc(run.prompt_id)}", ""]
    if scores.get("forced"):
        lines += ['> Evaluated with --force: run.json still says "running" (the run probably crashed).', ""]
    if scores.get("truncated_trace_lines"):
        lines += [f"> {scores['truncated_trace_lines']} truncated trace line ignored.", ""]
    overall = "Incomplete: output not scored" if scores["overall"] is None else _fmt(scores["overall"])
    lines += [f"**Overall:** {overall} · **Workflow:** {_fmt(w['score'])} · **Output:** {_fmt(o['score'])}",
              "", f"Run status: **{esc(run.status)}** · Model: {esc(run.model.id)} · Task: {esc(run.task_id)}", ""]

    lines += ["## Summary", ""]
    if scores.get("narrative"):
        lines.append(esc(scores["narrative"]))
    else:
        lines.append(f"Narrative unavailable: {esc(scores.get('narrative_error'))}")
    lines += ["", "## Struggles", ""]
    lines += [f"- {esc(s)}" for s in scores["struggles"]]

    lines += ["", f"## Workflow score: {_fmt(w['score'])}", "", esc(w["rationale"]), "",
              "| Component | Weight | Value |", "|---|---|---|"]
    lines += [f"| {k} | {w['weights'][k]:.2f} | {v:.2f} |" for k, v in w["components"].items()]
    cost = "n/a" if w["cost_usd"] is None else f"${w['cost_usd']:.4f}"
    lines += ["", f"Steps {w['steps']} · model calls {w['model_calls']}/{w['max_model_calls']} · tool calls "
                  f"{w['tool_calls']} · tool errors {w['tool_errors']} · retries {w['retries']} · invalid "
                  f"{w['invalid_tool_calls']} · recovered {({True: 'yes', False: 'no'}).get(w['recovered'], 'n/a')}"
                  f" · duration {w['duration_s']} s · cost {cost}"]

    lines += ["", f"## Output score: {_fmt(o['score'])}", ""]
    if o["score"] is None:
        lines.append(f"Output not scored: {esc(o.get('error'))}")
    else:
        lines += ["| Criterion | Score | Rationale |", "|---|---|---|"]
        lines += [f"| {cell(c['id'])} | {c['score']}/{c['max']} | {cell(c['rationale'])} |" for c in o["criteria"]]

    lines += ["", "## Timeline", "", "| Step | Type | Name | Status | Duration (ms) | Detail |", "|---|---|---|---|---|---|"]
    for s in steps:
        tags = []
        if s.retry_of is not None:
            tags.append(f"retry of {s.retry_of}")
        if s.invalid:
            tags.append("invalid")
        detail = s.error or s.text or s.result_preview or ""
        detail = (" ".join(detail.split()))[:160]
        name = f"{s.server}/{s.name}" if s.server else s.name
        lines.append(f"| {s.step} | {s.type} | {cell(name)} | {s.status}{' (' + ', '.join(tags) + ')' if tags else ''}"
                     f" | {s.duration_ms:g} | {cell(detail)} |")

    lines += ["", "## Agent output", ""]
    if output.kind == "none":
        lines.append("No final output was produced.")
    else:
        text = output.text[:OUTPUT_EXCERPT_CHARS]
        more = len(output.text) - len(text)
        lines.append(esc(text) + (f" (… {more} more characters in output.json)" if more > 0 else ""))

    ev = scores["evaluation"]
    servers = ", ".join(f"{m.name} {m.version or ''}".strip() for m in run.mcp_servers) or "none recorded"
    lines += ["", "## Provenance", "",
              f"- Run id: {esc(run.run_id)}",
              f"- Task: {esc(run.task_id)}, prompt {esc(run.prompt_id)}",
              f"- Model: {esc(run.model.provider)} {esc(run.model.id)}",
              f"- Harness: {esc(run.harness.name if run.harness else 'unknown')} {esc(run.harness.version if run.harness else '')}",
              f"- Tracer(s): {esc(run.tracer or 'unknown')}",
              f"- MCP servers: {esc(servers)}",
              f"- Inputs: {esc(', '.join(i.uri for i in run.inputs) or 'none')}",
              f"- Rubric: {esc(scores['rubric_id_loaded'])} v{esc(scores['rubric_version'])}"
              + (f" (run.json says {esc(scores['rubric_id'])})" if scores.get("rubric_id_mismatch") else ""),
              f"- Judge: {esc(ev['judge']['provider'])} {esc(ev['judge']['id'])}",
              f"- Summarizer: {esc(ev['summarizer']['provider'])} {esc(ev['summarizer']['id'])}",
              f"- Prompt templates: {esc(scores['templates']['judge'])}, {esc(scores['templates']['summarizer'])}",
              f"- Workflow scorer: {esc(scores['workflow_scorer'])}",
              f"- Evaluation config: {esc(scores['config'])}",
              f"- Evaluated at: {esc(scores['evaluated_at'])}",
              f"- Evaluation id: {esc(scores['evaluation_id'])}", ""]
    return "\n".join(lines)
