from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from research_lab.dashboard import READ_ONLY_LABEL, render_artifact_preview, render_dashboard_html, render_dashboard_json


class DashboardHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], request_handler_class, root: Path):
        super().__init__(server_address, request_handler_class)
        self.root = root.resolve()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "research-lab-dashboard/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"", "/", "/index.html"}:
            self._send_text(render_dashboard_html(self.server.root), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/refresh":
            payload = render_dashboard_json(self.server.root)
            self._send_text(json.dumps(payload, indent=2, ensure_ascii=False), "application/json; charset=utf-8")
            return
        if parsed.path == "/preview":
            params = parse_qs(parsed.query)
            rel_path = params.get("path", [""])[0]
            body, content_type, status = render_artifact_preview(self.server.root, rel_path)
            self._send_text(body, content_type, status=status)
            return
        if parsed.path == "/healthz":
            payload = {
                "ok": True,
                "read_only_mode": True,
                "label": READ_ONLY_LABEL,
            }
            self._send_text(json.dumps(payload, indent=2), "application/json; charset=utf-8")
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"", "/", "/index.html", "/api/refresh", "/preview", "/healthz"}:
            self._send_text("", self._content_type_for_path(parsed.path), send_body=False)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802
        self._send_error(HTTPStatus.FORBIDDEN, "write endpoints are disabled in read-only mode")

    def do_PUT(self) -> None:  # noqa: N802
        self._send_error(HTTPStatus.FORBIDDEN, "write endpoints are disabled in read-only mode")

    def do_PATCH(self) -> None:  # noqa: N802
        self._send_error(HTTPStatus.FORBIDDEN, "write endpoints are disabled in read-only mode")

    def do_DELETE(self) -> None:  # noqa: N802
        self._send_error(HTTPStatus.FORBIDDEN, "write endpoints are disabled in read-only mode")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self._send_common_headers("text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        message = format % args
        self.server  # keep attribute access explicit for linting clarity
        print(f"{self.address_string()} - - [{self.log_date_time_string()}] {message}")

    def _send_text(self, body: str, content_type: str, status: int = 200, send_body: bool = True) -> None:
        encoded = body.encode("utf-8") if send_body else b""
        self.send_response(status)
        self._send_common_headers(content_type, len(encoded))
        self.end_headers()
        if send_body:
            self.wfile.write(encoded)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = message
        self.send_response(status)
        encoded = body.encode("utf-8")
        self._send_common_headers("text/plain; charset=utf-8", len(encoded))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_common_headers(self, content_type: str, content_length: int | None = None) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'")
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))

    def _content_type_for_path(self, path: str) -> str:
        if path == "/api/refresh" or path == "/healthz":
            return "application/json; charset=utf-8"
        return "text/html; charset=utf-8"


def create_dashboard_server(root: Path, host: str = "127.0.0.1", port: int = 8787) -> DashboardHTTPServer:
    return DashboardHTTPServer((host, port), DashboardRequestHandler, root=root)


def run_dashboard_server(root: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    server = create_dashboard_server(root, host=host, port=port)
    address = f"http://{host}:{server.server_port}"
    print(f"research-lab dashboard listening on {address}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the research-lab read-only observability dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    run_dashboard_server(Path(args.root), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

