import json
import sys
import textwrap
from pathlib import Path

from foundry.config import load_config
from foundry.tasks import Prompt

FAULT_SERVER = Path(__file__).with_name("mcp_fault_server.py")


def write_config(tmp: Path, turns: list[dict], *, limits: str = "", server_args: list[str] | None = None,
                 command: str | None = None, extra: str = "", error_detection: str = "mcp") -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    script = tmp / "script.json"
    script.write_text(json.dumps(turns))
    args = [str(FAULT_SERVER), *(server_args or [])]
    cfg = tmp / "test.toml"
    cfg.write_text(textwrap.dedent(f"""
        [task]
        id = "adb"
        [model]
        provider = "scripted"
        id = "scripted-test"
        script = "{script}"
        [judge]
        provider = "offline"
        id = "offline-judge"
        [summarizer]
        provider = "offline"
        id = "offline-summarizer"
        [limits]
        mcp_start_timeout_s = 30
        {limits}
        [mcp.servers.fault]
        command = "{command or sys.executable}"
        args = {json.dumps(args)}
        error_detection = "{error_detection}"
        """) + extra)
    return cfg


def load(cfg_path: Path):
    return load_config(cfg_path)


PROMPT = Prompt("adb-001", "test prompt")


def steps(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]


def run_json(run_dir: Path) -> dict:
    return json.loads((run_dir / "run.json").read_text())


def output_json(run_dir: Path) -> dict:
    return json.loads((run_dir / "output.json").read_text())
