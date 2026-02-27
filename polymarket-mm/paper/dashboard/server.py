"""Dashboard HTTP server for PMM Paper Trading.

Lightweight server using Python stdlib http.server.
Serves static files + API endpoints on port 8501.

Endpoints:
    GET /              → serves index.html
    GET /api/state     → returns live_state.json
    GET /api/trades    → last N trades from trades.jsonl (?limit=50&market=<id>)
    GET /api/runs      → returns history.json
    GET /api/report/<run_id> → report markdown (placeholder)
"""

from __future__ import annotations

import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Resolve paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = PROJECT_ROOT / "paper" / "data"
RUNS_DIR = PROJECT_ROOT / "paper" / "runs"
DASHBOARD_DIR = SCRIPT_DIR

PORT = int(os.environ.get("DASHBOARD_PORT", "8501"))


def load_trades(limit: int = 50, market: str | None = None) -> list[dict]:
    """Load last N trades from JSONL, optionally filtered by market."""
    trades_path = DATA_DIR / "trades.jsonl"
    if not trades_path.exists():
        return []

    all_trades = []
    with open(trades_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trade = json.loads(line)
                if market and trade.get("market_id") != market:
                    continue
                all_trades.append(trade)
            except json.JSONDecodeError:
                continue

    # Return last N trades, newest first
    return list(reversed(all_trades[-limit:]))


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for dashboard."""

    def __init__(self, *args, **kwargs):
        # Serve from dashboard directory
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # CORS headers
        if path.startswith("/api"):
            self._handle_api(path, parsed.query)
            return

        # Serve index.html for root
        if path == "" or path == "/":
            self.path = "/index.html"

        return super().do_GET()

    def _handle_api(self, path: str, query_string: str):
        """Handle API endpoints."""
        params = parse_qs(query_string)

        if path == "/api/state":
            self._serve_json_file(DATA_DIR / "live_state.json")

        elif path == "/api/trades":
            limit = int(params.get("limit", ["50"])[0])
            market = params.get("market", [None])[0]
            trades = load_trades(limit=limit, market=market)
            self._serve_json(trades)

        elif path == "/api/runs":
            self._serve_json_file(RUNS_DIR / "history.json")

        elif path.startswith("/api/report/"):
            run_id = path.split("/api/report/")[1]
            report_path = DATA_DIR / "paper_trading_report.md"
            if report_path.exists():
                self._serve_text(report_path.read_text())
            else:
                self._serve_json({"error": f"Report not found for {run_id}"}, status=404)

        else:
            self._serve_json({"error": "Not found"}, status=404)

    def _serve_json(self, data, status: int = 200):
        """Serve JSON response with CORS."""
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _serve_json_file(self, path: Path):
        """Serve a JSON file with CORS."""
        if not path.exists():
            self._serve_json({}, status=200)
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._serve_json(data)
        except Exception as e:
            self._serve_json({"error": str(e)}, status=500)

    def _serve_text(self, text: str, status: int = 200):
        """Serve text response."""
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Suppress default logging to reduce noise."""
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Dashboard server starting on http://0.0.0.0:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard server...")
        server.shutdown()


if __name__ == "__main__":
    main()
