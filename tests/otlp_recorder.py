"""A local OTLP/HTTP endpoint that records what it receives (or never answers, to test timeouts)."""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)


class Recorder:
    def __init__(self):
        self.bodies: list[bytes] = []
        rec = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                rec.bodies.append(body)
                out = ExportTraceServiceResponse().SerializeToString()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.endpoint = f"http://127.0.0.1:{self.server.server_address[1]}/v1/traces"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def spans(self) -> list:
        out = []
        for body in self.bodies:
            req = ExportTraceServiceRequest.FromString(body)
            for rs in req.resource_spans:
                for ss in rs.scope_spans:
                    out.extend(ss.spans)
        return out

    def close(self):
        self.server.shutdown()


def attrs(span) -> dict:
    out = {}
    for kv in span.attributes:
        v = kv.value
        out[kv.key] = v.string_value or v.int_value or v.double_value or v.bool_value
    return out


class BlackHole:
    """Accepts connections and never answers."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.endpoint = f"http://127.0.0.1:{self.sock.getsockname()[1]}/v1/traces"
        self.conns = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
                self.conns.append(c)
            except OSError:
                return

    def close(self):
        self.sock.close()
        time.sleep(0)
