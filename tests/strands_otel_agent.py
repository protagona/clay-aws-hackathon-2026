"""Run a real Strands agent with Strands' own OpenTelemetry instrumentation (test helper; one process per run,
because StrandsTelemetry installs a global tracer provider).

usage: python tests/strands_otel_agent.py <otlp-endpoint> [--json-out FILE]
Exports to the endpoint; with --json-out, the endpoint is a local recorder and the spans are also written as
OTLP/JSON. Set OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental for the newer attribute style.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp import StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from strands import Agent  # noqa: E402
from strands.telemetry import StrandsTelemetry  # noqa: E402
from strands.tools.mcp import MCPClient  # noqa: E402

from foundry.models.scripted import ScriptedModel  # noqa: E402

TURNS = [{"tool": "echo", "input": {"text": "hello"}, "usage": {"in": 500, "out": 20}},
         {"tool": "fail", "input": {"message": "boom"}, "usage": {"in": 700, "out": 20}},
         {"text": "Final plan: use NAIP.", "usage": {"in": 900, "out": 30}}]


def main(endpoint: str, json_out: str | None) -> None:
    rec = None
    if json_out:
        from otlp_recorder import Recorder

        rec = Recorder()
        endpoint = rec.endpoint
    script = Path(tempfile.mkdtemp()) / "script.json"
    script.write_text(json.dumps(TURNS))
    tel = StrandsTelemetry().setup_otlp_exporter(endpoint=endpoint)
    server = Path(__file__).with_name("mcp_fault_server.py")
    mcp = MCPClient(lambda: stdio_client(StdioServerParameters(command=sys.executable, args=[str(server)])))
    with mcp:
        agent = Agent(model=ScriptedModel("scripted-x", script), tools=mcp.list_tools_sync(),
                      system_prompt="You plan datasets.", callback_handler=None, name="dataset-planner")
        agent("Plan a NAIP cropland dataset.")
    tel.tracer_provider.force_flush()
    if rec is not None:
        time.sleep(0.5)
        from google.protobuf.json_format import MessageToDict
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

        docs = [MessageToDict(ExportTraceServiceRequest.FromString(b)) for b in rec.bodies]
        Path(json_out).write_text("\n".join(json.dumps(d) for d in docs) + "\n")  # JSON Lines, like the Collector
        rec.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    main(args[0], args[args.index("--json-out") + 1] if "--json-out" in args else None)
