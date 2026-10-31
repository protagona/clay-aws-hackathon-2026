"""STAC MCP server (increment 2: S1-S5), through the real harness wiring against a local fake STAC API."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import foundry
from harness_helpers import PROMPT, load, steps, write_config

from foundry.engine.harness.runner import run

FAKE_ASSETS = {k: {"href": f"https://example.org/{k}.tif", "type": "image/tiff"} for k in
               ("visual", "red", "green", "blue", "nir", "swir16", "scl", "aot", "wvp", "thumbnail")}
FAKE_ASSETS.update({f"{k}-jp2": {"href": f"s3://x/{k}.jp2"} for k in ("red", "green", "blue")})


def _item(i):
    return {"type": "Feature", "id": f"S2X_TEST_{i}", "collection": "sentinel-2-l2a",
            "bbox": [-120.123456, 36.123456, -119.0, 37.0],
            "properties": {"datetime": f"2022-06-{i + 1:02d}T18:54:37Z", "eo:cloud_cover": i * 1.5,
                           "platform": "sentinel-2a", "proj:epsg": 32610, "padding": "x" * 15000},
            "assets": FAKE_ASSETS, "links": [{"rel": "self", "href": "https://example.org"}] * 20}


class FakeStac:
    def __init__(self, delay=0.0):
        self.requests = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.requests.append(("GET", self.path, None))
                time.sleep(delay)
                if self.path == "/collections":
                    self._send(200, {"collections": [{"id": "sentinel-2-l2a", "title": "Sentinel-2 L2A",
                                                      "extent": {"temporal": {"interval": [["2015-06-27T00:00:00Z", None]]}},
                                                      "license": "proprietary"}]})
                elif self.path.startswith("/collections/sentinel-2-l2a/items/"):
                    self._send(200, _item(0))
                else:
                    self._send(404, {"code": "NotFound"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(("POST", self.path, body))
                time.sleep(delay)
                self._send(200, {"type": "FeatureCollection", "numberMatched": 51,
                                 "features": [_item(i) for i in range(body["limit"])]})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def _stac_config(tmp_path, turns, url, timeout=None):
    env = f'{{ STAC_API_URL = "{url}"' + (f', STAC_TIMEOUT_S = "{timeout}"' if timeout else "") + " }"
    extra = ('\n[mcp.servers.stac]\ncommand = "{python}"\nargs = ["-m", "foundry.engine.mcp_servers.stac"]\n'
             f'error_detection = "mcp"\nenv = {env}\n')
    return load(write_config(tmp_path, turns, extra=extra))


@pytest.fixture
def fake():
    f = FakeStac()
    yield f
    f.close()


def test_search_through_harness_is_compact_and_traced(tmp_path, fake):  # S1, S2, S4
    turns = [{"tool": "search_items", "input": {"collection": "sentinel-2-l2a", "bbox": [-120.0, 36.6, -119.6, 36.9],
                                                "datetime": "2022-06-01/2022-06-30", "limit": 5, "max_cloud_cover": 10}},
             {"tool": "get_item", "input": {"collection": "sentinel-2-l2a", "item_id": "S2X_TEST_0"}},
             {"text": "done"}]
    run_dir, status = run(_stac_config(tmp_path, turns, fake.url), PROMPT, tmp_path / "runs")
    assert status == "succeeded"
    tool = [s for s in steps(run_dir) if s["type"] == "tool_call"]
    assert [(s["server"], s["name"], s["status"]) for s in tool] == [("stac", "search_items", "ok"),
                                                                     ("stac", "get_item", "ok")]
    servers = [m["name"] for m in json.loads((run_dir / "run.json").read_text())["mcp_servers"]]
    assert servers == ["fault", "stac"]
    search = [r for r in fake.requests if r[0] == "POST"][0][2]
    assert search["datetime"] == "2022-06-01T00:00:00Z/2022-06-30T23:59:59Z"
    assert search["query"] == {"eo:cloud_cover": {"lt": 10}} and search["limit"] == 5


def test_search_response_is_compact(tmp_path, fake):  # S2: the server's own output, not the trace preview
    import asyncio
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def call():
        p = StdioServerParameters(command=sys.executable, args=["-m", "foundry.engine.mcp_servers.stac"],
                                  env={"STAC_API_URL": fake.url, "PATH": "/usr/bin:/bin"})
        async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("search_items", {"collection": "sentinel-2-l2a", "bbox": [-121, 36, -119, 37],
                                                     "datetime": "2022-06", "limit": 5})
            assert res.is_error  # "2022-06" is not a valid date: a tool error, not a crash
            res = await s.call_tool("search_items", {"collection": "sentinel-2-l2a", "bbox": [-121, 36, -119, 37],
                                                     "datetime": "2022-06-01/2022-06-30", "limit": 5})
            return res.content[0].text

    text = asyncio.run(call())
    raw = len(json.dumps({"features": [_item(i) for i in range(5)]}))
    data = json.loads(text)
    assert len(text) <= 6000 < raw  # ~110 KB of fake STAC items in, compact summary out
    assert data["matched"] == 51 and data["returned"] == 5
    first = data["items"][0]
    assert set(first) == {"id", "collection", "datetime", "cloud_cover", "bbox", "assets", "hrefs"}
    assert first["hrefs"]["red"] == "https://example.org/red.tif" and len(first["hrefs"]) <= 5
    assert not any(a.endswith("-jp2") for a in first["assets"])


@pytest.mark.parametrize("args,needle", [
    ({"collection": "nope", "bbox": [0, 0, 1, 1], "datetime": "2022-06-01"}, "Unknown collection 'nope'"),
    ({"collection": "sentinel-2-l2a", "bbox": [1, 0, 0, 1], "datetime": "2022-06-01"}, "west<east"),
    ({"collection": "sentinel-2-l2a", "bbox": [0, 0, 1], "datetime": "2022-06-01"}, "4 numbers"),
    ({"collection": "sentinel-2-l2a", "bbox": [0, 0, 1, 1], "datetime": "June 2022"}, "datetime must be"),
    ({"collection": "sentinel-2-l2a", "bbox": [0, 0, 1, 1], "datetime": "2022-06-01", "limit": 500}, "limit must be"),
])
def test_bad_input_is_a_tool_error(tmp_path, fake, args, needle):  # S3
    run_dir, status = run(_stac_config(tmp_path, [{"tool": "search_items", "input": args}, {"text": "x"}], fake.url),
                          PROMPT, tmp_path / "runs")
    step = [s for s in steps(run_dir) if s["type"] == "tool_call"][0]
    assert step["status"] == "error" and needle in step["error"] and status == "succeeded"


def test_timeout_and_unreachable_are_tool_errors(tmp_path):  # S3
    slow = FakeStac(delay=3)
    try:
        turns = [{"tool": "list_collections", "input": {}}, {"text": "x"}]
        run_dir, _ = run(_stac_config(tmp_path / "slow", turns, slow.url, timeout=1), PROMPT, tmp_path / "slow/runs")
        step = [s for s in steps(run_dir) if s["type"] == "tool_call"][0]
        assert step["status"] == "error" and "timed out after 1s" in step["error"]
    finally:
        slow.close()
    run_dir, _ = run(_stac_config(tmp_path / "down", turns, "http://127.0.0.1:9"), PROMPT, tmp_path / "down/runs")
    step = [s for s in steps(run_dir) if s["type"] == "tool_call"][0]
    assert step["status"] == "error" and "unreachable" in step["error"]


@pytest.mark.network
def test_real_earth_search_through_cli(tmp_path):  # S4, against the public API
    runs = tmp_path / "runs"
    proc = foundry("run", "--config", "config/offline.toml", "--prompt", "adb-006", "--runs-dir", str(runs))
    assert proc.returncode == 0, proc.stderr
    (run_dir,) = [p for p in runs.iterdir() if p.is_dir()]
    stac = [s for s in steps(run_dir) if s["type"] == "tool_call" and s.get("server") == "stac"]
    assert stac and stac[0]["status"] == "ok" and '"matched"' in stac[0]["result_preview"]
