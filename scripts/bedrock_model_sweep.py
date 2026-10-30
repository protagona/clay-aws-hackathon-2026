"""Which Bedrock models can actually serve as the agent, judge or summarizer in this account?

Quotas and model listings are not enough: a model can have a quota and still be AccessDenied, or accept
Converse but reject tool use in streaming mode (which is how Strands calls Bedrock). This script calls every
ACTIVE text model, plus inference-profile-only models, with the exact features the harness uses:

  stream_system   ConverseStream with a system prompt      (agent path)
  temperature     inferenceConfig.temperature accepted       (config/bedrock*.toml set it)
  tool_use        streaming tool call with toolConfig        (agent path)
  tool_roundtrip  toolResult sent back, model replies        (agent path)
  converse        non-streaming Converse                     (judge / summarizer path)

Usage: AWS_PROFILE=<profile> uv run python scripts/bedrock_model_sweep.py results.json [region]
Costs a few cents (tiny prompts, maxTokens <= 600).
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
import boto3
from botocore.config import Config

REGION = sys.argv[2] if len(sys.argv) > 2 else "us-west-2"
cfg = Config(read_timeout=90, connect_timeout=10, retries={"max_attempts": 2, "mode": "standard"})
br = boto3.client("bedrock", region_name=REGION)
rt = boto3.client("bedrock-runtime", region_name=REGION, config=cfg)

models = br.list_foundation_models()["modelSummaries"]
profiles = []
kw = {"typeEquals": "SYSTEM_DEFINED", "maxResults": 1000}
while True:
    r = br.list_inference_profiles(**kw)
    profiles += r["inferenceProfileSummaries"]
    if not r.get("nextToken"):
        break
    kw["nextToken"] = r["nextToken"]

def invoke_id(m):
    if "ON_DEMAND" in m.get("inferenceTypesSupported", []):
        return m["modelId"], "on-demand"
    arn_tail = m["modelId"]
    cands = [p["inferenceProfileId"] for p in profiles
             if any(x["modelArn"].endswith("/" + arn_tail) for x in p.get("models", []))]
    for pref in ("us.", "global."):
        for c in cands:
            if c.startswith(pref):
                return c, pref.rstrip(".") + " profile"
    return (cands[0], "profile") if cands else (None, "no on-demand or profile")

TOOLS = {"tools": [{"toolSpec": {"name": "search_datasets", "description": "Search the Registry of Open Data on AWS for datasets.",
         "inputSchema": {"json": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}}]}
SYS = [{"text": "You are a geospatial data assistant. Use tools when they help."}]

def err(e):
    s = str(e)
    for k in ("ValidationException", "AccessDeniedException", "ThrottlingException", "ResourceNotFoundException",
              "ModelErrorException", "ServiceUnavailableException", "ModelNotReadyException", "ReadTimeout"):
        if k in s:
            return f"{k}: {s.split(':', 1)[-1].strip()[:140]}"
    return s[:160]

def stream(mid, **kw):
    r = rt.converse_stream(modelId=mid, **kw)
    text, tool, stop, usage = "", None, None, None
    cur = None
    for ev in r["stream"]:
        if "contentBlockStart" in ev and "toolUse" in ev["contentBlockStart"]["start"]:
            cur = {**ev["contentBlockStart"]["start"]["toolUse"], "input": ""}
        elif "contentBlockDelta" in ev:
            d = ev["contentBlockDelta"]["delta"]
            if "text" in d:
                text += d["text"]
            if "toolUse" in d and cur is not None:
                cur["input"] += d["toolUse"].get("input", "")
        elif "contentBlockStop" in ev and cur is not None:
            tool, cur = cur, None
        elif "messageStop" in ev:
            stop = ev["messageStop"]["stopReason"]
        elif "metadata" in ev:
            usage = ev["metadata"].get("usage")
    return text, tool, stop, usage

def probe(m):
    mid, how = invoke_id(m)
    row = {"model": m["modelId"], "provider": m["providerName"], "invoke": mid, "how": how}
    if mid is None:
        row["result"] = "not invocable (no on-demand, no inference profile)"
        return row
    user = [{"role": "user", "content": [{"text": "Reply with the single word: ok"}]}]
    # 1 basic streaming + system prompt (agent path)
    try:
        t, _, stop, u = stream(mid, messages=user, system=SYS, inferenceConfig={"maxTokens": 300})
        row["stream_system"] = "ok"
    except Exception as e:
        row["stream_system"] = err(e)
        # fall back: streaming without system prompt, to separate failures
        try:
            stream(mid, messages=user, inferenceConfig={"maxTokens": 300})
            row["stream_nosystem"] = "ok"
        except Exception as e2:
            row["stream_nosystem"] = err(e2)
    # 2 temperature
    try:
        stream(mid, messages=user, inferenceConfig={"maxTokens": 300, "temperature": 0.2})
        row["temperature"] = "ok"
    except Exception as e:
        row["temperature"] = err(e)
    # 3 streaming tool use
    ask = [{"role": "user", "content": [{"text": "Find datasets about NAIP aerial imagery. Use the search_datasets tool."}]}]
    tool = None
    try:
        t, tool, stop, u = stream(mid, messages=ask, system=SYS, toolConfig=TOOLS, inferenceConfig={"maxTokens": 600})
        row["tool_use"] = f"ok ({tool['name']} {tool['input'][:40]})" if tool else f"no tool call (stop={stop})"
    except Exception as e:
        row["tool_use"] = err(e)
    # 4 tool round-trip
    if tool:
        try:
            try:
                tin = json.loads(tool["input"] or "{}")
            except json.JSONDecodeError:
                tin = {}
            msgs = ask + [{"role": "assistant", "content": [{"toolUse": {"toolUseId": tool["toolUseId"], "name": tool["name"], "input": tin}}]},
                          {"role": "user", "content": [{"toolResult": {"toolUseId": tool["toolUseId"], "status": "success",
                            "content": [{"text": '{"status":"ok","datasets":[{"slug":"naip","name":"NAIP on AWS"}]}'}]}}]}]
            t, tool2, stop, u = stream(mid, messages=msgs, system=SYS, toolConfig=TOOLS, inferenceConfig={"maxTokens": 600})
            row["tool_roundtrip"] = "ok" if (t.strip() or tool2) else f"empty reply (stop={stop})"
        except Exception as e:
            row["tool_roundtrip"] = err(e)
    # 5 non-streaming Converse (judge/summarizer path)
    try:
        rt.converse(modelId=mid, messages=user, inferenceConfig={"maxTokens": 300})
        row["converse"] = "ok"
    except Exception as e:
        row["converse"] = err(e)
    return row

cands = [m for m in models if m.get("modelLifecycle", {}).get("status") == "ACTIVE"
         and "TEXT" in m.get("outputModalities", []) and "TEXT" in m.get("inputModalities", [])]
print(f"{len(models)} foundation models; {len(cands)} active text-in/text-out candidates", file=sys.stderr)
with ThreadPoolExecutor(max_workers=6) as ex:
    rows = list(ex.map(probe, cands))

# Models reachable only through an inference profile (no foundation-model entry in this region).
probed = {r["model"] for r in rows} | {r["invoke"] for r in rows if r["invoke"]}
SKIP = ("embed", "sonic", "rerank", "pegasus", "stable", "titan-image", "ray")
extra, seen = [], set()
for p in profiles:
    pid = p["inferenceProfileId"]
    base = pid.split(".", 1)[1]
    if not pid.startswith(("us.", "global.")) or base in seen or any(x.endswith(base) for x in probed) or any(k in base for k in SKIP):
        continue
    seen.add(base)
    extra.append({"modelId": pid, "providerName": base.split(".")[0], "inferenceTypesSupported": ["ON_DEMAND"]})
with ThreadPoolExecutor(max_workers=6) as ex:
    rows += list(ex.map(probe, extra))


def ok(v):
    return isinstance(v, str) and v.startswith("ok")


for r in sorted(rows, key=lambda r: (r["provider"], r["model"])):
    agent = all(ok(r.get(k)) for k in ("stream_system", "tool_use", "tool_roundtrip"))
    role = "agent+judge" if agent and ok(r.get("converse")) else ("judge only" if ok(r.get("converse")) else "unusable")
    print(f"{role:<12} {r['invoke'] or r['model']:<52} temperature={'ok' if ok(r.get('temperature')) else 'rejected/na'}")
json.dump(rows, open(sys.argv[1], "w"), indent=1)
print("done", file=sys.stderr)
