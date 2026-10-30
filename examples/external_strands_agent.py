"""A standalone Strands agent that knows nothing about foundry, used to show `foundry receive` / `foundry import`.

It only emits standard OpenTelemetry GenAI spans (Strands' built-in telemetry). Any agent that does the same,
in any framework, can be evaluated the same way:

    uv run foundry receive --task adb --prompt-id adb-001 --config config/bedrock.toml &
    AWS_PROFILE=... uv run python examples/external_strands_agent.py --endpoint http://127.0.0.1:4318/v1/traces
    uv run foundry evaluate runs/<printed run dir> --config config/bedrock.toml
"""

import argparse
import shutil

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands import Agent
from strands.models import BedrockModel
from strands.telemetry import StrandsTelemetry
from strands.tools.mcp import MCPClient

PROMPT = ("We want to map cropland change in California's Central Valley between 2020 and 2022 using free, open "
          "imagery. Find suitable open datasets and write a short dataset-build plan (under 400 words): which data, "
          "how to access it, the processing steps, and the limitations.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--endpoint", default="http://127.0.0.1:4318/v1/traces", help="OTLP/HTTP traces endpoint")
    p.add_argument("--model", default="us.moonshotai.kimi-k3")
    p.add_argument("--region", default="us-west-2")
    args = p.parse_args()

    telemetry = StrandsTelemetry().setup_otlp_exporter(endpoint=args.endpoint)
    roda = MCPClient(lambda: stdio_client(StdioServerParameters(
        command=shutil.which("uvx") or "uvx", args=["awslabs.roda-mcp-server@0.1.7"])))
    with roda:
        agent = Agent(model=BedrockModel(model_id=args.model, region_name=args.region, max_tokens=4096),
                      tools=roda.list_tools_sync(), name="external-planner", callback_handler=None,
                      system_prompt="You are a geospatial data engineer. Use the tools to confirm datasets.")
        result = agent(PROMPT)
    telemetry.tracer_provider.force_flush()
    print(str(result)[:300])


if __name__ == "__main__":
    main()
