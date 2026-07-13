#!/usr/bin/env python3
"""Minimal BPM callback mock: accepts ai_service's onTranscriptionComplete POSTs
and always answers 200, so the durable queue reaches `done`.

Logs one line per callback and keeps counters. Stdlib only.

    python3 tools/loadtest/bpm_mock.py --port 9099

Point ai_service at it with BPM_CALLBACK_URL=http://<host>:9099/callback
(the path is ignored; every POST returns 200).
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lock = threading.Lock()
_count = 0
_bytes = 0
_first_ts: float | None = None
_last_ts: float | None = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence default access log
        pass

    def do_POST(self):
        global _count, _bytes, _first_ts, _last_ts
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        crid, summ_len, ft_len = "?", 0, 0
        try:
            data = json.loads(body or b"{}")
            crid = data.get("CallRecordId", "?")
            summ_len = len(data.get("Summary", "") or "")
            ft_len = len(data.get("FullText", "") or "")
        except (ValueError, AttributeError):
            pass
        now = time.monotonic()
        with _lock:
            _count += 1
            _bytes += length
            if _first_ts is None:
                _first_ts = now
            _last_ts = now
            n = _count
        print(
            f"[{n:4d}] callback CallRecordId={crid} "
            f"Summary={summ_len}c FullText={ft_len}c ({length}B)",
            flush=True,
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_GET(self):
        # /stats for quick inspection
        with _lock:
            span = (_last_ts - _first_ts) if (_first_ts and _last_ts) else 0.0
            payload = {"received": _count, "bytes": _bytes, "span_seconds": round(span, 2)}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9099)
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"BPM mock listening on http://{args.host}:{args.port}  (all POSTs -> 200)")
    print("GET /stats for counters. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        with _lock:
            print(f"\nreceived {_count} callbacks, {_bytes} bytes total")


if __name__ == "__main__":
    main()
