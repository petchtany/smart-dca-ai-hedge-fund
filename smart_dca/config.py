"""Configuration and dynamic path resolution (zero hardcoded user paths)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=False)


def _resolve_hedge_dir() -> Path:
    env_dir = os.environ.get("HEDGE_FUND_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    home_hedge = Path.home() / ".hedge-fund"
    if home_hedge.exists():
        return home_hedge
    return ROOT_DIR / ".hedge-fund-data"


BASE_HEDGE_DIR = _resolve_hedge_dir()
PAPER_DIR = BASE_HEDGE_DIR / "paper"

CACHE_DIR = ROOT_DIR / "cache"
DB_PATH = Path(os.environ.get("SMART_DCA_DB", str(CACHE_DIR / "smart_dca.db")))
LEGACY_CIO_CACHE_DIR = CACHE_DIR / "cio_dca"
LEGACY_TICKER_CACHE_DIR = CACHE_DIR / "ticker_dca"
LEGACY_WATCHLIST_FILE = CACHE_DIR / "watchlist.json"

# Cache TTLs (overrideable via environment variables)
PRICE_CACHE_TTL_SEC = int(os.environ.get("PRICE_CACHE_TTL_SEC", "1800"))          # 30 minutes
FUNDAMENTALS_CACHE_TTL_SEC = int(os.environ.get("FUNDAMENTALS_CACHE_TTL_SEC", "86400"))  # 24 hours
FX_CACHE_TTL_SEC = int(os.environ.get("FX_CACHE_TTL_SEC", "3600"))                # 1 hour
CIO_CACHE_TTL_SEC = int(os.environ.get("CIO_CACHE_TTL_DAYS", "7")) * 86400        # 7 days
CIO_PRICE_DRIFT_PCT = float(os.environ.get("CIO_PRICE_DRIFT_PCT", "8.0"))         # Re-analyze if price moves >= 8%

DEFAULT_TICKERS = ["TSM", "MU", "NVDA", "MSFT", "GOOGL", "META", "PLTR", "RKLB"]
DEFAULT_FX_USD_THB = 34.5

# Security & Reliability settings
ADMIN_TOKEN = os.environ.get("SMART_DCA_ADMIN_TOKEN", "").strip()
PROTECT_READ = os.environ.get("SMART_DCA_PROTECT_READ", "0").strip() == "1"
TRUST_PROXY = os.environ.get("SMART_DCA_TRUST_PROXY", "1" if os.environ.get("PORT") else "0").strip() == "1"
ALLOWED_ORIGIN = os.environ.get("SMART_DCA_ALLOWED_ORIGIN", "").strip()
BIND_HOST = os.environ.get("SMART_DCA_HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
MAX_REQUEST_BODY_BYTES = 16 * 1024  # 16 KB max POST body
MAX_TICKERS_PER_REQUEST = 10
MAX_PORTFOLIO_TICKERS = 50
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("SMART_DCA_RATE_LIMIT", "20"))
RATE_LIMIT_WINDOW_SEC = 60
SOCKET_TIMEOUT_SEC = 15.0
MAX_CONCURRENT_CONNECTIONS = 32

# Persistent Cloud Sync via Supabase (Watchlist + AI CIO Cache) — primary
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_KEY")
    or ""
).strip()

# Optional fallback: Private GitHub Gist Sync (Watchlist only)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_GIST_ID = os.environ.get("GITHUB_GIST_ID", "").strip()
GITHUB_GIST_FILENAME = os.environ.get("GITHUB_GIST_FILENAME", "watchlist.json").strip() or "watchlist.json"

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "THB": "฿",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "HKD": "HK$",
    "SGD": "S$",
    "CNY": "¥",
    "KRW": "₩",
    "TWD": "NT$",
    "AUD": "A$",
    "CAD": "C$",
}

SUFFIX_CURRENCY_MAP: dict[str, str] = {
    ".BK": "THB",
    ".T": "JPY",
    ".HK": "HKD",
    ".L": "GBP",
    ".DE": "EUR",
    ".PA": "EUR",
    ".AS": "EUR",
    ".SI": "SGD",
    ".AX": "AUD",
    ".TO": "CAD",
    ".TW": "TWD",
    ".KS": "KRW",
}


def detect_currency(ticker: str, info_currency: str | None = None) -> tuple[str, str]:
    """Return (currency_code, symbol) from yfinance info or ticker suffix."""
    code = (info_currency or "").upper().strip()
    if not code:
        upper = ticker.upper().strip()
        for suffix, cur in SUFFIX_CURRENCY_MAP.items():
            if upper.endswith(suffix):
                code = cur
                break
        else:
            code = "USD"
    symbol = CURRENCY_SYMBOLS.get(code, f"{code} ")
    return code, symbol
