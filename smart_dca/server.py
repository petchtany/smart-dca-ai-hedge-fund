"""Hardened HTTP API Server + Portfolio Sync Orchestrator (Auth, Rate Limit, Safe CORS, Validation, Sync Lock)."""

from __future__ import annotations

import base64
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import yfinance as yf

from .backtest import run_portfolio_backtest
from .cio import populate_cio_advices
from .config import (
    ADMIN_TOKEN,
    ALLOWED_ORIGIN,
    BIND_HOST,
    MAX_CONCURRENT_CONNECTIONS,
    MAX_PORTFOLIO_TICKERS,
    MAX_REQUEST_BODY_BYTES,
    MAX_TICKERS_PER_REQUEST,
    PAPER_DIR,
    PROTECT_READ,
    RATE_LIMIT_MAX_REQUESTS,
    RATE_LIMIT_WINDOW_SEC,
    ROOT_DIR,
    SOCKET_TIMEOUT_SEC,
    TRUST_PROXY,
)
from .market_data import (
    _fetch_chart_direct,
    _read_text_safe,
    fetch_all_tickers_batch,
    get_usd_thb_rate,
    load_latest_fund_signals,
)
from .report import generate_html_report
from .storage import get_store

_SYNC_LOCK = threading.RLock()
_TICKER_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")


class SlidingWindowRateLimiter:
    """Thread-safe per-IP sliding-window rate limiter with automatic stale-IP pruning."""

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_sec: int = RATE_LIMIT_WINDOW_SEC,
        max_tracked_ips: int = 10_000,
    ) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        self.max_tracked_ips = max_tracked_ips
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._ops = 0

    def allow(self, client_ip: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_sec
        with self._lock:
            self._ops += 1
            if self._ops % 200 == 0 or len(self._hits) >= self.max_tracked_ips:
                stale = [ip for ip, dq in self._hits.items() if not dq or dq[-1] < cutoff]
                for ip in stale:
                    del self._hits[ip]
                # Hard cap against extreme random-IP floods
                while len(self._hits) >= self.max_tracked_ips and client_ip not in self._hits:
                    oldest_ip = next(iter(self._hits))
                    del self._hits[oldest_ip]

            q = self._hits[client_ip]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                return False
            q.append(now)
            return True


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer bounded by a semaphore to prevent Slowloris/thread exhaustion."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], RequestHandlerClass: type, max_workers: int = MAX_CONCURRENT_CONNECTIONS) -> None:
        self._conn_sem = threading.Semaphore(max_workers)
        super().__init__(server_address, RequestHandlerClass)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._conn_sem.release()

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._conn_sem.acquire(timeout=2.0):
            try:
                request.close()
            except Exception:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._conn_sem.release()
            raise


def validate_ticker(tkr: str) -> str | None:
    cleaned = tkr.upper().strip()
    if not cleaned or not _TICKER_RE.match(cleaned):
        return None
    return cleaned


def is_authorized_request(headers: Any, expected_token: str | None = None) -> bool:
    """Constant-time check for X-API-Key, Bearer token, or HTTP Basic Auth password when ADMIN_TOKEN is set."""
    token = expected_token if expected_token is not None else ADMIN_TOKEN
    if not token:
        return True
    provided = (headers.get("X-API-Key") or "").strip()
    if not provided:
        auth_hdr = (headers.get("Authorization") or "").strip()
        if auth_hdr.lower().startswith("bearer "):
            provided = auth_hdr[7:].strip()
        elif auth_hdr.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth_hdr[6:].strip()).decode("utf-8", errors="ignore")
                if ":" in decoded:
                    provided = decoded.split(":", 1)[1]
            except Exception:
                provided = ""
    if not provided:
        return False
    return hmac.compare_digest(provided, token)


def build_portfolio_snapshot(
    force_refresh: bool = False,
    force_fundamentals: bool = False,
    force_cio: bool = False,
    cli_tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch batch prices, split-cached fundamentals, FX rate, and smart-TTL CIO analyses."""
    fund_name, default_tickers, ai_weights, signals_by_ticker = load_latest_fund_signals()
    tickers = cli_tickers if cli_tickers else default_tickers
    fx_usd_thb = get_usd_thb_rate(force_refresh=force_refresh)
    results = fetch_all_tickers_batch(
        tickers,
        force_refresh=force_refresh,
        force_fundamentals=force_fundamentals,
        signals_by_ticker=signals_by_ticker,
    )
    if results:
        populate_cio_advices(results, signals_by_ticker, force_refresh=force_cio)
    return {
        "fund_name": fund_name,
        "tickers": tickers,
        "fx_usd_thb": fx_usd_thb,
        "ai_weights": ai_weights,
        "signals_by_ticker": signals_by_ticker,
        "results": results,
        "updated_at": datetime.now(timezone(timedelta(hours=7))).isoformat(timespec="seconds"),
    }


def sync_report_quietly(force_refresh: bool = False, force_cio: bool = False) -> Path | None:
    with _SYNC_LOCK:
        try:
            snap = build_portfolio_snapshot(force_refresh=force_refresh, force_cio=force_cio)
            if snap["results"]:
                return generate_html_report(
                    snap["results"],
                    snap["ai_weights"],
                    snap["signals_by_ticker"],
                    snap["fund_name"],
                    fx_usd_thb=snap["fx_usd_thb"],
                )
        except Exception:
            pass
    return None


def modify_portfolio_tickers(add: list[str] | None = None, remove: list[str] | None = None) -> tuple[bool, str]:
    """Validate and add/remove tickers from SQLite watchlist (and paper fund.yaml if present), then refresh report."""
    import yaml

    with _SYNC_LOCK:
        store = get_store()
        wl = store.load_watchlist()
        added_set = list(wl["added"])
        removed_set = set(wl["removed"])

        valid_added: list[str] = []
        if add:
            parsed_add: list[str] = []
            for raw_t in add:
                for tok in raw_t.replace(",", " ").split():
                    tkr = validate_ticker(tok)
                    if not tkr:
                        return False, f"รูปแบบชื่อหุ้น '{tok[:20]}' ไม่ถูกต้อง (ใช้ได้เฉพาะตัวอักษร ตัวเลข . - ^ =)"
                    parsed_add.append(tkr)
            if len(parsed_add) > MAX_TICKERS_PER_REQUEST:
                return False, f"เพิ่มหุ้นได้สูงสุดครั้งละ {MAX_TICKERS_PER_REQUEST} ตัว"

            _, current_tickers, _, _ = load_latest_fund_signals(store=store)
            if len(set(current_tickers) | set(parsed_add)) > MAX_PORTFOLIO_TICKERS:
                return False, f"พอร์ตมีหุ้นเกินขีดจำกัดสูงสุด ({MAX_PORTFOLIO_TICKERS} ตัว)"

            for tkr in parsed_add:
                df_check = yf.Ticker(tkr).history(period="1mo", interval="1d")
                if df_check is None or df_check.empty:
                    df_check = _fetch_chart_direct(tkr, period="1mo")
                if df_check is None or df_check.empty:
                    return False, f"ไม่พบข้อมูลหุ้น '{tkr}' บน Yahoo Finance (หากเป็นหุ้นไทยให้เติม .BK เช่น PTT.BK)"
                if tkr not in added_set:
                    added_set.append(tkr)
                removed_set.discard(tkr)
                valid_added.append(tkr)

        if remove:
            parsed_rem: list[str] = []
            for raw_t in remove:
                for tok in raw_t.replace(",", " ").split():
                    tkr = validate_ticker(tok)
                    if not tkr:
                        return False, f"รูปแบบชื่อหุ้น '{tok[:20]}' ไม่ถูกต้อง"
                    parsed_rem.append(tkr)
            for tkr in parsed_rem:
                if tkr in added_set:
                    added_set.remove(tkr)
                removed_set.add(tkr)

        wl["added"] = added_set
        wl["removed"] = sorted(removed_set)
        store.save_watchlist(wl)

        if PAPER_DIR.is_dir():
            for fund_dir in PAPER_DIR.iterdir():
                fy = fund_dir / "fund.yaml"
                if fy.is_file():
                    try:
                        ydata = yaml.safe_load(_read_text_safe(fy)) or {}
                        u = list(ydata.get("universe") or [])
                        for tkr in valid_added:
                            if tkr not in u:
                                u.append(tkr)
                        if remove:
                            for raw_t in remove:
                                for tok in raw_t.replace(",", " ").split():
                                    tkr = validate_ticker(tok)
                                    if tkr and tkr in u and len(u) > 1:
                                        u.remove(tkr)
                        ydata["universe"] = u
                        fy.write_text(yaml.safe_dump(ydata, sort_keys=False), encoding="utf-8")
                    except Exception:
                        pass

        sync_report_quietly(force_refresh=False)
        return True, "อัปเดตรายชื่อหุ้นและวิเคราะห์ด้วย AI เรียบร้อยแล้ว"


def serve_api(port: int = 8765) -> None:
    limiter = SlidingWindowRateLimiter()

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            try:
                self.request.settimeout(SOCKET_TIMEOUT_SEC)
            except Exception:
                pass

        def log_message(self, format: str, *args) -> None:
            pass

        def _client_ip(self) -> str:
            if TRUST_PROXY:
                forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                if forwarded:
                    return forwarded
            return self.client_address[0] if self.client_address else "unknown"

        def _send_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self' http://127.0.0.1:* http://localhost:*; "
                "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
            )

            origin = (self.headers.get("Origin") or "").strip()
            if ALLOWED_ORIGIN and origin == ALLOWED_ORIGIN:
                self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
            elif origin in (f"http://127.0.0.1:{actual_port}", f"http://localhost:{actual_port}"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")

        def _send_json(self, obj: Any, status: int = 200) -> None:
            resp_bytes = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(resp_bytes)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_security_headers()
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)

            if parsed.path == "/api/health":
                st = get_store()
                self._send_json({
                    "ok": True,
                    "version": "4.0",
                    "auth_required": bool(ADMIN_TOKEN),
                    "protect_read": PROTECT_READ,
                    "supabase_sync": st.supabase_sync_enabled,
                    "gist_sync": st.gist_sync_enabled,
                })
                return

            if PROTECT_READ and ADMIN_TOKEN and not is_authorized_request(self.headers):
                self.send_response(401)
                self._send_security_headers()
                self.send_header("WWW-Authenticate", 'Basic realm="Smart DCA Dashboard"')
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return

            if parsed.path == "/api/portfolio":
                include_candles = (qs.get("candles", ["0"])[0] == "1")
                force = (qs.get("refresh", ["0"])[0] == "1")
                if force:
                    if not is_authorized_request(self.headers):
                        self._send_json({"ok": False, "message": "Unauthorized"}, status=401)
                        return
                    if not limiter.allow(self._client_ip()):
                        self._send_json({"ok": False, "message": "Too many requests, please wait."}, status=429)
                        return
                snap = build_portfolio_snapshot(force_refresh=force)
                items = []
                for r in snap["results"]:
                    item = {k: v for k, v in r.items() if include_candles or k not in ("candles", "ema50_series", "ema200_series", "markers")}
                    items.append(item)
                self._send_json({
                    "ok": True,
                    "updated_at": snap["updated_at"],
                    "fx_usd_thb": snap["fx_usd_thb"],
                    "count": len(items),
                    "stocks": sorted(items, key=lambda x: x["dip_score"], reverse=True),
                })
                return

            if parsed.path == "/api/backtest":
                if not limiter.allow(self._client_ip()):
                    self._send_json({"ok": False, "message": "Too many requests, please wait."}, status=429)
                    return
                period = qs.get("period", ["3y"])[0]
                if period not in ("1y", "2y", "3y", "5y"):
                    period = "3y"
                _, tickers, _, _ = load_latest_fund_signals()
                bt = run_portfolio_backtest(tickers, period=period)
                self._send_json(bt)
                return

            html_file = ROOT_DIR / "trade_plan.html"
            if not html_file.exists():
                sync_report_quietly(force_refresh=True)
            else:
                file_day = datetime.fromtimestamp(html_file.stat().st_mtime).strftime("%Y-%m-%d")
                today_day = datetime.now().strftime("%Y-%m-%d")
                if file_day != today_day:
                    sync_report_quietly(force_refresh=True)

            data = html_file.read_bytes() if html_file.exists() else b"<h1>Generating...</h1>"
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if not is_authorized_request(self.headers):
                self._send_json({"ok": False, "message": "Unauthorized: กรุณาระบุ Admin Token ที่ถูกต้อง"}, status=401)
                return

            if not limiter.allow(self._client_ip()):
                self._send_json({"ok": False, "message": "ส่งคำขอถี่เกินไป กรุณารอสักครู่แล้วลองใหม่"}, status=429)
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._send_json({"ok": False, "message": "Invalid Content-Length"}, status=400)
                return

            if length < 0 or length > MAX_REQUEST_BODY_BYTES:
                self._send_json({"ok": False, "message": "Payload too large"}, status=413)
                return

            body = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else "{}"
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                self._send_json({"ok": False, "message": "Invalid JSON body"}, status=400)
                return

            raw_tickers = str(payload.get("tickers") or "")
            if self.path.startswith("/api/add"):
                ok, msg = modify_portfolio_tickers(add=[raw_tickers])
            elif self.path.startswith("/api/remove"):
                ok, msg = modify_portfolio_tickers(remove=[raw_tickers])
            elif self.path.startswith("/api/refresh"):
                sync_report_quietly(force_refresh=True)
                ok, msg = True, "อัปเดตราคาล่าสุดเรียบร้อยแล้ว"
            else:
                self._send_json({"ok": False, "message": "Unknown endpoint"}, status=404)
                return

            self._send_json({"ok": ok, "message": msg}, status=200 if ok else 400)

    actual_port = int(os.environ.get("PORT", port))
    server = BoundedThreadingHTTPServer((BIND_HOST, actual_port), Handler)
    print(f"Smart DCA Web API Server v4.0 (Hardened) running on {BIND_HOST}:{actual_port} (Press Ctrl+C to stop)")
    server.serve_forever()


def ensure_background_server(script_path: Path, port: int = 8765) -> bool:
    """Ensure the local API server on port 8765 is running in a detached background process."""
    import socket
    import subprocess

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return True

    try:
        py_exe = Path(sys.executable)
        pyw_exe = py_exe.with_name("pythonw.exe")
        exe_to_use = str(pyw_exe if pyw_exe.exists() else py_exe)
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [exe_to_use, str(script_path.resolve()), "--serve-only"],
            cwd=str(script_path.resolve().parent),
            creationflags=flags,
            close_fds=True,
        )
        time.sleep(0.6)
        return True
    except Exception:
        return False
