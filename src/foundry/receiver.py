"""`foundry receive`: an OTLP/HTTP endpoint on 127.0.0.1 that turns incoming agent traces into run directories.

Point any OpenTelemetry-instrumented agent at it (OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318). Spans are
buffered per trace; when a top-level invoke_agent span arrives, the run is written after a short grace period so
spans exported in a later batch still make it in.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from foundry.importer import ImportError_, Span, agent_roots, spans_from_protobuf, spans_from_request, write_run

MAX_BODY = 10 * 1024 * 1024
GRACE_S = 2.0


class Receiver:
    def __init__(self, runs_dir: Path, task_id: str, prompt_id: str, prices: dict | None = None,
                 port: int = 4318, grace_s: float = GRACE_S, log=lambda msg: print(msg, flush=True)):
        self.runs_dir, self.task_id, self.prompt_id, self.prices = Path(runs_dir), task_id, prompt_id, prices
        self.grace_s, self.log = grace_s, log
        self.lock = threading.Lock()
        self.spans: dict[str, list[Span]] = {}
        self.done: set[tuple[str, str]] = set()
        self.written: list[Path] = []
        rec = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path.rstrip("/") != "/v1/traces":
                    return self._reply(404, b"not found", "text/plain")
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._reply(413, b"payload too large", "text/plain")
                body = self.rfile.read(length)
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                try:
                    spans = spans_from_request(json.loads(body)) if ctype == "application/json" else spans_from_protobuf(body)
                except (ImportError_, ValueError) as exc:
                    return self._reply(400, str(exc).encode()[:300], "text/plain")
                rec.accept(spans)
                if ctype == "application/json":
                    return self._reply(200, b"{}", "application/json")
                from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

                return self._reply(200, ExportTraceServiceResponse().SerializeToString(), "application/x-protobuf")

            def _reply(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]

    def accept(self, spans: list[Span]) -> None:
        with self.lock:
            for s in spans:
                self.spans.setdefault(s.trace_id, []).append(s)
            traces = {s.trace_id for s in spans if s.op == "invoke_agent"}
        for trace_id in traces:
            threading.Timer(self.grace_s, self._flush, args=(trace_id,)).start()

    def _flush(self, trace_id: str) -> None:
        with self.lock:
            spans = [Span(**{**s.__dict__, "children": []}) for s in self.spans.get(trace_id, [])]
            roots = [r for r in agent_roots(spans) if (r.trace_id, r.span_id) not in self.done]
            for r in roots:
                self.done.add((r.trace_id, r.span_id))
        for r in roots:
            try:
                path = write_run(r, self.runs_dir, self.task_id, self.prompt_id, "receiver", self.prices)
            except FileExistsError:
                continue
            self.written.append(path)
            self.log(f"run written: {path}")

    def serve_forever(self) -> None:
        self.server.serve_forever()

    def start(self) -> Receiver:
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def main(args) -> int:
    from foundry.config import ConfigError, load_eval_config

    try:
        prices = load_eval_config(args.config).prices if args.config else None
        rec = Receiver(Path(args.runs_dir), args.task, args.prompt_id, prices, port=args.port)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Cannot listen on 127.0.0.1:{args.port}: {exc.strerror}", file=sys.stderr)
        return 2
    print(f"Receiving OTLP/HTTP on http://127.0.0.1:{rec.port}/v1/traces -> {args.runs_dir} "
          f"(set OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:{rec.port})", flush=True)
    try:
        rec.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
