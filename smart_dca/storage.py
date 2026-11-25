"""SQLite-backed persistent storage for watchlist, split caches, FX rates, and CIO analyses."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from .config import (
    CACHE_DIR,
    CIO_CACHE_TTL_SEC,
    CIO_PRICE_DRIFT_PCT,
    DB_PATH,
    FUNDAMENTALS_CACHE_TTL_SEC,
    FX_CACHE_TTL_SEC,
    GITHUB_GIST_FILENAME,
    GITHUB_GIST_ID,
    GITHUB_TOKEN,
    LEGACY_CIO_CACHE_DIR,
    LEGACY_WATCHLIST_FILE,
    PRICE_CACHE_TTL_SEC,
    SUPABASE_KEY,
    SUPABASE_URL,
)

_DB_LOCK = threading.RLock()


class SmartDCAStore:
    """Thread-safe SQLite store with automatic migration from legacy JSON cache files."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
        github_token: str | None = None,
        gist_id: str | None = None,
        gist_filename: str = GITHUB_GIST_FILENAME,
    ) -> None:
        self.db_path = db_path
        self.supabase_url = (SUPABASE_URL if supabase_url is None else supabase_url).strip().rstrip("/")
        self.supabase_key = (SUPABASE_KEY if supabase_key is None else supabase_key).strip()
        self.github_token = GITHUB_TOKEN if github_token is None else github_token.strip()
        self.gist_id = GITHUB_GIST_ID if gist_id is None else gist_id.strip()
        self.gist_filename = gist_filename
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_legacy_files()
        if self.supabase_sync_enabled:
            self._pull_watchlist_from_supabase()
            self._pull_cio_cache_from_supabase()
        elif self.gist_sync_enabled:
            self._pull_watchlist_from_gist()

    @property
    def supabase_sync_enabled(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def gist_sync_enabled(self) -> bool:
        return bool(self.github_token and self.gist_id)

    def _supabase_headers(self, prefer: str | None = None) -> dict[str, str]:
        hdrs = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            hdrs["Prefer"] = prefer
        return hdrs

    @staticmethod
    def _iso_to_epoch(val: Any) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str) and val:
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.timestamp()
            except Exception:
                pass
        return time.time()

    def _pull_watchlist_from_supabase(self) -> bool:
        """Pull persistent watchlist from Supabase into local SQLite on startup."""
        if not self.supabase_sync_enabled:
            return False
        try:
            import requests

            resp = requests.get(
                f"{self.supabase_url}/rest/v1/smart_dca_watchlist",
                params={"select": "ticker,status,updated_at", "order": "updated_at.asc"},
                headers=self._supabase_headers(),
                timeout=8,
            )
            if resp.status_code != 200:
                return False
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                return False
            added = [str(r["ticker"]).upper().strip() for r in rows if r.get("status") == "added" and r.get("ticker")]
            removed = sorted(
                {str(r["ticker"]).upper().strip() for r in rows if r.get("status") == "removed" and r.get("ticker")}
            )
            self._write_watchlist_local({"added": added, "removed": removed})
            return True
        except Exception:
            return False

    def _push_watchlist_to_supabase(self, wl: dict[str, list[str]]) -> bool:
        """Sync watchlist changes to Supabase (upsert current rows, delete removed keys)."""
        if not self.supabase_sync_enabled:
            return False
        try:
            import requests

            now_iso = datetime.now(timezone.utc).isoformat()
            rows: list[dict[str, Any]] = []
            for tkr in wl.get("added") or []:
                t = str(tkr).upper().strip()
                if t:
                    rows.append({"ticker": t, "status": "added", "updated_at": now_iso})
            for tkr in wl.get("removed") or []:
                t = str(tkr).upper().strip()
                if t:
                    rows.append({"ticker": t, "status": "removed", "updated_at": now_iso})

            url = f"{self.supabase_url}/rest/v1/smart_dca_watchlist"
            # Replace table contents cleanly (delete all then upsert current state)
            requests.delete(url, params={"ticker": "not.is.null"}, headers=self._supabase_headers(), timeout=8)
            if rows:
                resp = requests.post(
                    url,
                    headers=self._supabase_headers(prefer="resolution=merge-duplicates"),
                    json=rows,
                    timeout=8,
                )
                return resp.status_code in (200, 201, 204)
            return True
        except Exception:
            return False

    def _pull_cio_cache_from_supabase(self) -> bool:
        """Pull AI CIO analysis cache from Supabase into local SQLite so Render restarts stay warm."""
        if not self.supabase_sync_enabled:
            return False
        try:
            import requests

            resp = requests.get(
                f"{self.supabase_url}/rest/v1/smart_dca_cio_cache",
                params={"select": "ticker,payload,price_at_analysis,updated_at"},
                headers=self._supabase_headers(),
                timeout=8,
            )
            if resp.status_code != 200:
                return False
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                return False
            with _DB_LOCK, self._connect() as conn:
                for r in rows:
                    tkr = str(r.get("ticker") or "").upper().strip()
                    payload = r.get("payload")
                    if not tkr or not isinstance(payload, dict) or not payload.get("verdict_headline"):
                        continue
                    price = r.get("price_at_analysis")
                    ts = self._iso_to_epoch(r.get("updated_at"))
                    conn.execute(
                        "INSERT OR REPLACE INTO cio_cache (ticker, payload, price_at_analysis, updated_at) VALUES (?, ?, ?, ?)",
                        (tkr, json.dumps(payload, ensure_ascii=False), price, ts),
                    )
            return True
        except Exception:
            return False

    def _push_cio_to_supabase(self, ticker: str, clean_advice: dict[str, Any], current_price: float | None) -> bool:
        """Upsert a single ticker's AI CIO analysis into Supabase."""
        if not self.supabase_sync_enabled:
            return False
        try:
            import requests

            row = {
                "ticker": ticker.upper().strip(),
                "payload": clean_advice,
                "price_at_analysis": current_price,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            resp = requests.post(
                f"{self.supabase_url}/rest/v1/smart_dca_cio_cache",
                headers=self._supabase_headers(prefer="resolution=merge-duplicates"),
                json=row,
                timeout=8,
            )
            return resp.status_code in (200, 201, 204)
        except Exception:
            return False

    def _gist_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _pull_watchlist_from_gist(self) -> bool:
        """Load persistent watchlist.json from Private GitHub Gist into SQLite on startup."""
        if not self.gist_sync_enabled:
            return False
        try:
            import requests

            resp = requests.get(
                f"https://api.github.com/gists/{self.gist_id}",
                headers=self._gist_headers(),
                timeout=8,
            )
            if resp.status_code != 200:
                return False
            files = resp.json().get("files") or {}
            file_obj = files.get(self.gist_filename)
            if not file_obj or not file_obj.get("content"):
                return False
            data = json.loads(file_obj["content"])
            wl = {
                "added": [str(x).upper().strip() for x in (data.get("added") or []) if x],
                "removed": sorted({str(x).upper().strip() for x in (data.get("removed") or []) if x}),
            }
            self._write_watchlist_local(wl)
            return True
        except Exception:
            return False

    def _push_watchlist_to_gist(self, wl: dict[str, list[str]]) -> bool:
        """Persist watchlist.json to Private GitHub Gist so changes survive Render restarts."""
        if not self.gist_sync_enabled:
            return False
        try:
            import requests

            body = {
                "files": {
                    self.gist_filename: {
                        "content": json.dumps(wl, indent=2, ensure_ascii=False),
                    }
                }
            }
            resp = requests.patch(
                f"https://api.github.com/gists/{self.gist_id}",
                headers=self._gist_headers(),
                json=body,
                timeout=8,
            )
            return resp.status_code == 200
        except Exception:
            return False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with _DB_LOCK, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    ticker TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('added', 'removed')),
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fundamentals_cache (
                    ticker TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS price_cache (
                    ticker TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cio_cache (
                    ticker TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    price_at_analysis REAL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fx_cache (
                    pair TEXT PRIMARY KEY,
                    rate REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _migrate_legacy_files(self) -> None:
        with _DB_LOCK, self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'migrated_v1'").fetchone()
            if row:
                return

            now = time.time()
            # 1. Migrate legacy watchlist.json
            if LEGACY_WATCHLIST_FILE.exists():
                try:
                    data = json.loads(LEGACY_WATCHLIST_FILE.read_text(encoding="utf-8"))
                    for tkr in data.get("added") or []:
                        t = str(tkr).upper().strip()
                        if t:
                            conn.execute(
                                "INSERT OR REPLACE INTO watchlist (ticker, status, updated_at) VALUES (?, 'added', ?)",
                                (t, now),
                            )
                    for tkr in data.get("removed") or []:
                        t = str(tkr).upper().strip()
                        if t:
                            conn.execute(
                                "INSERT OR REPLACE INTO watchlist (ticker, status, updated_at) VALUES (?, 'removed', ?)",
                                (t, now),
                            )
                except Exception:
                    pass

            # 2. Migrate legacy CIO JSON files
            if LEGACY_CIO_CACHE_DIR.is_dir():
                for f in LEGACY_CIO_CACHE_DIR.glob("*_cio_v3.json"):
                    try:
                        tkr = f.name.replace("_cio_v3.json", "").upper().strip()
                        payload = json.loads(f.read_text(encoding="utf-8"))
                        if isinstance(payload, dict) and payload.get("verdict_headline"):
                            mtime = f.stat().st_mtime
                            conn.execute(
                                "INSERT OR IGNORE INTO cio_cache (ticker, payload, price_at_analysis, updated_at) VALUES (?, ?, NULL, ?)",
                                (tkr, json.dumps(payload, ensure_ascii=False), mtime),
                            )
                    except Exception:
                        continue

            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('migrated_v1', ?)", (str(now),))

    # --- Watchlist ---
    def load_watchlist(self) -> dict[str, list[str]]:
        with _DB_LOCK, self._connect() as conn:
            rows = conn.execute("SELECT ticker, status FROM watchlist ORDER BY updated_at ASC").fetchall()
        added = [r[0] for r in rows if r[1] == "added"]
        removed = sorted(r[0] for r in rows if r[1] == "removed")
        return {"added": added, "removed": removed}

    def _write_watchlist_local(self, wl: dict[str, list[str]]) -> None:
        now = time.time()
        with _DB_LOCK, self._connect() as conn:
            conn.execute("DELETE FROM watchlist")
            for idx, tkr in enumerate(wl.get("added") or []):
                t = str(tkr).upper().strip()
                if t:
                    conn.execute(
                        "INSERT OR REPLACE INTO watchlist (ticker, status, updated_at) VALUES (?, 'added', ?)",
                        (t, now + idx * 0.001),
                    )
            for tkr in wl.get("removed") or []:
                t = str(tkr).upper().strip()
                if t:
                    conn.execute(
                        "INSERT OR REPLACE INTO watchlist (ticker, status, updated_at) VALUES (?, 'removed', ?)",
                        (t, now),
                    )
        # Keep legacy JSON mirror in sync for easy inspection / git diffing
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            LEGACY_WATCHLIST_FILE.write_text(json.dumps(wl, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def save_watchlist(self, wl: dict[str, list[str]]) -> None:
        self._write_watchlist_local(wl)
        if self.supabase_sync_enabled:
            self._push_watchlist_to_supabase(wl)
        elif self.gist_sync_enabled:
            self._push_watchlist_to_gist(wl)

    # --- Fundamentals Cache (24h TTL) ---
    def get_fundamentals(self, ticker: str, max_age_sec: int = FUNDAMENTALS_CACHE_TTL_SEC) -> dict[str, Any] | None:
        t = ticker.upper().strip()
        with _DB_LOCK, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM fundamentals_cache WHERE ticker = ?", (t,)
            ).fetchone()
        if not row:
            return None
        if (time.time() - float(row[1])) > max_age_sec:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def set_fundamentals(self, ticker: str, data: dict[str, Any]) -> None:
        t = ticker.upper().strip()
        with _DB_LOCK, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fundamentals_cache (ticker, payload, updated_at) VALUES (?, ?, ?)",
                (t, json.dumps(data, ensure_ascii=False), time.time()),
            )

    # --- Price / Technical Result Cache (30m TTL) ---
    def get_price_analysis(self, ticker: str, max_age_sec: int = PRICE_CACHE_TTL_SEC) -> dict[str, Any] | None:
        t = ticker.upper().strip()
        with _DB_LOCK, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM price_cache WHERE ticker = ?", (t,)
            ).fetchone()
        if not row:
            return None
        if (time.time() - float(row[1])) > max_age_sec:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def set_price_analysis(self, ticker: str, data: dict[str, Any]) -> None:
        t = ticker.upper().strip()
        with _DB_LOCK, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO price_cache (ticker, payload, updated_at) VALUES (?, ?, ?)",
                (t, json.dumps(data, ensure_ascii=False), time.time()),
            )

    # --- CIO Analysis Cache (TTL + Price Drift Invalidation) ---
    def get_cio_analysis(
        self,
        ticker: str,
        current_price: float | None = None,
        max_age_sec: int = CIO_CACHE_TTL_SEC,
        max_price_drift_pct: float = CIO_PRICE_DRIFT_PCT,
    ) -> tuple[dict[str, Any] | None, str]:
        """Return (cached_advice_or_None, reason_tag)."""
        t = ticker.upper().strip()
        with _DB_LOCK, self._connect() as conn:
            row = conn.execute(
                "SELECT payload, price_at_analysis, updated_at FROM cio_cache WHERE ticker = ?", (t,)
            ).fetchone()
        if not row:
            return None, "miss"

        payload_raw, price_at_analysis, updated_at = row
        age_sec = time.time() - float(updated_at)
        if age_sec > max_age_sec:
            return None, "expired_ttl"

        if current_price and price_at_analysis and float(price_at_analysis) > 0:
            drift_pct = abs((current_price - float(price_at_analysis)) / float(price_at_analysis)) * 100.0
            if drift_pct >= max_price_drift_pct:
                return None, f"price_drift_{drift_pct:.1f}%"

        try:
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict) and parsed.get("verdict_headline"):
                parsed.setdefault("_meta", {})
                parsed["_meta"].update(
                    {
                        "cached": True,
                        "updated_at": float(updated_at),
                        "price_at_analysis": price_at_analysis,
                        "age_hours": round(age_sec / 3600.0, 1),
                    }
                )
                return parsed, "hit"
        except Exception:
            pass
        return None, "corrupt"

    def set_cio_analysis(self, ticker: str, advice: dict[str, Any], current_price: float | None) -> None:
        t = ticker.upper().strip()
        now = time.time()
        clean = {k: v for k, v in advice.items() if k != "_meta"}
        with _DB_LOCK, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cio_cache (ticker, payload, price_at_analysis, updated_at) VALUES (?, ?, ?, ?)",
                (t, json.dumps(clean, ensure_ascii=False), current_price, now),
            )
        if self.supabase_sync_enabled:
            self._push_cio_to_supabase(t, clean, current_price)

    # --- FX Rate Cache (1h TTL) ---
    def get_fx_rate(self, pair: str = "USDTHB", max_age_sec: int = FX_CACHE_TTL_SEC) -> float | None:
        with _DB_LOCK, self._connect() as conn:
            row = conn.execute(
                "SELECT rate, updated_at FROM fx_cache WHERE pair = ?", (pair.upper(),)
            ).fetchone()
        if not row:
            return None
        if (time.time() - float(row[1])) > max_age_sec:
            return None
        return float(row[0])

    def set_fx_rate(self, pair: str, rate: float) -> None:
        with _DB_LOCK, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fx_cache (pair, rate, updated_at) VALUES (?, ?, ?)",
                (pair.upper(), float(rate), time.time()),
            )


_DEFAULT_STORE: SmartDCAStore | None = None


def get_store() -> SmartDCAStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = SmartDCAStore()
    return _DEFAULT_STORE
