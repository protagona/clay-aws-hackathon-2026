"""AC21: credentials in the environment never reach run files, CLI output or exported spans (UI: test_ui.py)."""

import os

from conftest import foundry
from harness_helpers import write_config
from otlp_recorder import Recorder

SENTINEL = "SENTINEL-c0ffee-do-not-leak"


def sentinel_env() -> dict:
    env = dict(os.environ)
    env.update(AWS_ACCESS_KEY_ID=f"AKIA{SENTINEL}", AWS_SECRET_ACCESS_KEY=SENTINEL, AWS_SESSION_TOKEN=SENTINEL,
               FOUNDRY_TEST_OTLP_HEADERS=f"Authorization=Bearer {SENTINEL}")
    return env


def test_sentinel_absent_everywhere(tmp_path):
    rec = Recorder()
    try:
        extra = (f'\n[tracing]\ntracers = ["jsonl", "otel"]\n[tracing.otel]\nendpoint = "{rec.endpoint}"\n'
                 'headers_env = "FOUNDRY_TEST_OTLP_HEADERS"\n')
        turns = [{"tool": "echo", "input": {"text": "x"}}, {"tool": "fail", "input": {"message": "nope"}},
                 {"text": "done"}]
        cfg = write_config(tmp_path, turns, extra=extra)
        runs = tmp_path / "runs"
        env = sentinel_env()
        p1 = foundry("run", "--config", str(cfg), "--prompt", "adb-001", "--runs-dir", str(runs), env=env)
        assert p1.returncode == 0, p1.stderr
        (run_dir,) = [p for p in runs.iterdir() if p.is_dir()]
        p2 = foundry("evaluate", str(run_dir), "--config", "config/offline.toml", env=env)
        assert p2.returncode == 0, p2.stderr

        assert rec.bodies, "spans were exported"
        haystacks = {"stdout/stderr": p1.stdout + p1.stderr + p2.stdout + p2.stderr}
        for f in run_dir.iterdir():
            if f.is_file():
                haystacks[f.name] = f.read_text(errors="replace")
        for i, body in enumerate(rec.bodies):
            haystacks[f"otlp-body-{i}"] = body.decode("latin-1")
        leaks = [name for name, text in haystacks.items() if SENTINEL in text]
        assert leaks == []
    finally:
        rec.close()
