"""
Live spend server: real-time usage stats over HTTP.

Serves two endpoints via the stdlib ``http.server``:

``GET /stats``
    JSON snapshot of running totals, the summary, and per-provider
    spending.
``GET /stream``
    Server-sent events: an ``event: record`` message per recorded usage,
    plus a ``: ping`` heartbeat every 15 seconds to keep proxies alive.

CORS is enabled on both endpoints so browser dashboards can consume them
directly. Start with ``ledger.serve(host, port)`` or instantiate
:class:`LiveServer` and call :meth:`LiveServer.start` (or use it as a
context manager).
"""

import contextlib
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from ..core.ledger import TokenLedger

_HEARTBEAT_SECONDS = 15.0


class _ThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class LiveServer:
    """A tiny daemon HTTP server streaming ledger activity."""

    def __init__(self, ledger: TokenLedger, host: str = "127.0.0.1", port: int = 8765, api_key: Optional[str] = None):
        self.ledger = ledger
        self.host = host
        self.port = port
        self.api_key = api_key
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._httpd: Optional[_ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._original_on_record: Optional[Callable[[dict[str, Any]], None]] = ledger.interceptor.on_record

        def _on_record(record: dict[str, Any]) -> None:
            self._publish(record)
            if self._original_on_record:
                self._original_on_record(record)

        self._on_record = _on_record

    # -- lifecycle -----------------------------------------------------

    def start(self) -> "LiveServer":
        if self._httpd is not None:
            return self
        self.ledger.interceptor.on_record = self._on_record
        try:
            self._httpd = _ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        except OSError:
            self.ledger.interceptor.on_record = self._original_on_record
            raise
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="tokenledger-live")
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            for sub in self._subscribers:
                sub.put(None)
            self._subscribers.clear()
        if self.ledger.interceptor.on_record is self._on_record:
            self.ledger.interceptor.on_record = self._original_on_record

    def __enter__(self) -> "LiveServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # -- internals -----------------------------------------------------

    def _publish(self, record: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            with contextlib.suppress(queue.Full):
                sub.put_nowait(dict(record))

    def _subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def _unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(sub)

    def _stats_payload(self) -> dict[str, Any]:
        summary = self.ledger.get_summary()
        return {
            "record_count": summary.get("requests", 0),
            "total_tokens": summary.get("total_tokens", 0),
            "cost_usd": summary.get("cost_usd", 0.0),
            "providers": self.ledger.get_spending_by_provider(),
            "running_totals": {
                key: totals
                for key, totals in self.ledger.store.running_totals.items()
                if key in ("global:all",)
            },
            "generated_at": summary.get("generated_at", ""),
        }


def _make_handler(server: LiveServer) -> type:
    handler = server  # close over; bound in __init__ below

    class _Handler(BaseHTTPRequestHandler):
        _server: LiveServer = handler  # type: ignore[misc]

        def log_message(self, *args: Any) -> None:
            pass

        def _send_headers(self, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self) -> None:
            if self._server.api_key:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or auth[7:] != self._server.api_key:
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error": "unauthorized"}')
                    return
            path = self.path.split("?", 1)[0]
            try:
                if path == "/stats":
                    self._send_headers("application/json")
                    self.wfile.write(json.dumps(self._server._stats_payload()).encode("utf-8"))
                elif path == "/stream":
                    self._send_headers("text/event-stream")
                    self._serve_stream()
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error": "not found"}')
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _serve_stream(self) -> None:
            sub = self._server._subscribe()
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        record = sub.get(timeout=_HEARTBEAT_SECONDS)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if record is None:
                        break
                    body = json.dumps(record, default=str).encode("utf-8")
                    self.wfile.write(b"event: record\ndata: " + body + b"\n\n")
                    self.wfile.flush()
            finally:
                self._server._unsubscribe(sub)

    return _Handler
