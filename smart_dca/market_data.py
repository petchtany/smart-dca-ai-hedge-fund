"""Batch market data fetcher, split fundamentals/price cache, multi-currency & live FX, and technical scoring."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from .config import (
    DEFAULT_FX_USD_THB,
    DEFAULT_TICKERS,
    PAPER_DIR,
    detect_currency,
)
from .storage import SmartDCAStore, get_store


def _read_text_safe(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "cp1252", "cp874"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clean_num(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (ValueError, TypeError):
        return None


def get_usd_thb_rate(force_refresh: bool = False, store: SmartDCAStore | None = None) -> float:
    """Fetch live USD/THB exchange rate via Yahoo Finance (THB=X) with 1h SQLite cache."""
    st = store or get_store()
    if not force_refresh:
        cached = st.get_fx_rate("USDTHB")
        if cached and 20.0 <= cached <= 50.0:
            return round(cached, 2)

    try:
        df = yf.Ticker("THB=X").history(period="5d", interval="1d")
        if df is None or df.empty:
            df = _fetch_chart_direct("THB=X", period="5d")
        if df is not None and not df.empty:
            val = _clean_num(df["Close"].iloc[-1])
            if val and 20.0 <= val <= 50.0:
                st.set_fx_rate("USDTHB", val)
                return round(val, 2)
    except Exception:
        pass
    return DEFAULT_FX_USD_THB


def fetch_macro_radar(force_refresh: bool = False, store: SmartDCAStore | None = None) -> dict[str, Any]:
    """Fetch global macro indicators (^VIX, ^TNX 10Y yield, ^GSPC vs EMA200) via crumb-free chart API."""
    st = store or get_store()
    if not force_refresh:
        cached = st.get_price_analysis("__MACRO_RADAR__")
        if cached is not None:
            return cached

    frames = _batch_download_ohlcv(["^VIX", "^TNX", "^GSPC"], period="1y")

    vix = None
    vix_chg = None
    if "^VIX" in frames and not frames["^VIX"].empty:
        vdf = frames["^VIX"]
        vix = round(float(vdf["Close"].iloc[-1]), 2)
        if len(vdf) >= 2:
            prev = float(vdf["Close"].iloc[-2])
            if prev > 0:
                vix_chg = round(((vix - prev) / prev) * 100.0, 1)

    us10y = None
    if "^TNX" in frames and not frames["^TNX"].empty:
        us10y = round(float(frames["^TNX"]["Close"].iloc[-1]), 2)

    sp500 = None
    sp500_ema200 = None
    sp500_dist_ema200 = None
    sp500_dd = None
    if "^GSPC" in frames and len(frames["^GSPC"]) >= 30:
        sdf = frames["^GSPC"].copy()
        sdf["EMA200"] = sdf["Close"].ewm(span=200, adjust=False).mean()
        sp500 = round(float(sdf["Close"].iloc[-1]), 2)
        sp500_ema200 = round(float(sdf["EMA200"].iloc[-1]), 2)
        if sp500_ema200 > 0:
            sp500_dist_ema200 = round(((sp500 - sp500_ema200) / sp500_ema200) * 100.0, 1)
        high_52w = float(sdf["High"].max())
        if high_52w > 0:
            sp500_dd = round(((sp500 - high_52w) / high_52w) * 100.0, 1)

    # Classify Market Regime for Smart DCA
    if (vix is not None and vix >= 25.0) or (sp500_dist_ema200 is not None and sp500_dist_ema200 <= -3.0):
        regime = "EXTREME_FEAR"
        regime_label = "🔥 ตลาดกลัวหนัก (Fear / Panic)"
        regime_advice = "จังหวะทองของสาย Smart DCA! เพิ่มงบช้อนหุ้นคุณภาพดีที่ลงมาชนแนวรับ"
        regime_color = "#22c55e"
    elif (vix is not None and vix >= 18.0) or (sp500_dist_ema200 is not None and sp500_dist_ema200 <= 1.5):
        regime = "CAUTIOUS_DIP"
        regime_label = "🎯 ตลาดเริ่มย่อ (Cautious Dip)"
        regime_advice = "ทยอยสะสมตามบันได ไม้ 1–2 เน้นหุ้นที่ Dip Score สูงและงบโตแกร่ง"
        regime_color = "#38bdf8"
    elif (vix is not None and vix < 14.0) and (sp500_dist_ema200 is not None and sp500_dist_ema200 >= 10.0):
        regime = "GREED_OVERHEATED"
        regime_label = "⚠️ ตลาดร้อนแรง (Greed / Overheated)"
        regime_advice = "อย่ารีบไล่ราคา ออมตามวินัยขั้นต่ำและเก็บเงินสดสำรองไว้รอจังหวะย่อ"
        regime_color = "#f59e0b"
    else:
        regime = "NEUTRAL"
        regime_label = "⚖️ ตลาดปกติ (Neutral)"
        regime_advice = "ออม DCA ตามแผนปกติ และตั้ง Limit Order ดักรับตามแนวรับแต่ละตัว"
        regime_color = "#60a5fa"

    macro = {
        "vix": vix,
        "vix_chg_pct": vix_chg,
        "us10y": us10y,
        "sp500": sp500,
        "sp500_ema200": sp500_ema200,
        "sp500_dist_ema200_pct": sp500_dist_ema200,
        "sp500_drawdown_pct": sp500_dd,
        "regime": regime,
        "regime_label": regime_label,
        "regime_advice": regime_advice,
        "regime_color": regime_color,
    }
    st.set_price_analysis("__MACRO_RADAR__", macro)
    return macro


def load_latest_fund_signals(
    store: SmartDCAStore | None = None,
) -> tuple[str | None, list[str], dict[str, float], dict[str, list[dict]]]:
    """Load latest AI Hedge Fund signals & universe merged with SQLite watchlist."""
    import yaml

    st = store or get_store()
    merged_weights: dict[str, float] = {}
    signals_by_ticker: dict[str, list[dict]] = {}
    all_tickers: list[str] = []
    latest_fund_name: str | None = None

    if PAPER_DIR.is_dir():
        files: list[tuple[float, str, Path]] = []
        fund_yamls: list[tuple[float, str, Path]] = []
        for fund_dir in PAPER_DIR.iterdir():
            if not fund_dir.is_dir():
                continue
            fy = fund_dir / "fund.yaml"
            if fy.is_file():
                fund_yamls.append((fy.stat().st_mtime, fund_dir.name, fy))
            ledger_dir = fund_dir / "ledger"
            if ledger_dir.is_dir():
                for f in ledger_dir.glob("*.json"):
                    files.append((f.stat().st_mtime, fund_dir.name, f))

        files.sort(key=lambda x: x[0])
        fund_yamls.sort(key=lambda x: x[0])

        for _, f_name, fy_path in fund_yamls:
            try:
                ydata = yaml.safe_load(_read_text_safe(fy_path)) or {}
                for tkr in ydata.get("universe") or []:
                    if tkr not in all_tickers:
                        all_tickers.append(tkr)
                latest_fund_name = f_name
            except Exception:
                continue

        for _, f_name, f_path in files:
            try:
                data = json.loads(_read_text_safe(f_path))
                for tkr in data.get("universe") or []:
                    if tkr not in all_tickers:
                        all_tickers.append(tkr)
                decision = data.get("decision") or {}
                w = decision.get("final_weights") or {}
                if w:
                    merged_weights.update(w)
                    latest_fund_name = f_name
                for strat in decision.get("strategies") or []:
                    for sig in strat.get("signals") or []:
                        tkr = sig.get("ticker")
                        if tkr and sig.get("reasoning"):
                            signals_by_ticker.setdefault(tkr, [])
                            existing = [
                                s for s in signals_by_ticker[tkr] if s.get("model_name") != sig.get("model_name")
                            ]
                            existing.append(sig)
                            signals_by_ticker[tkr] = existing
            except Exception:
                continue

    if not all_tickers:
        all_tickers = list(DEFAULT_TICKERS)

    wl = st.load_watchlist()
    for tkr in wl["added"]:
        if tkr not in all_tickers:
            all_tickers.append(tkr)
    removed_set = set(wl["removed"])
    all_tickers = [t for t in all_tickers if t not in removed_set]

    return latest_fund_name, (all_tickers or list(DEFAULT_TICKERS)), merged_weights, signals_by_ticker


def compute_volume_profile_supports(df: pd.DataFrame, close: float, bins: int = 24) -> list[float]:
    low_min = float(df["Low"].min())
    high_max = float(df["High"].max())
    if high_max <= low_min:
        return []
    edges = np.linspace(low_min, high_max, bins + 1)
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].fillna(0).values
    tp = typical_price.values

    bin_vols = np.zeros(bins)
    for i in range(bins):
        mask = (tp >= edges[i]) & (tp < edges[i + 1])
        bin_vols[i] = vol[mask].sum()

    centers = (edges[:-1] + edges[1:]) / 2.0
    below_indices = [i for i in range(bins) if centers[i] < close * 0.99]
    below_indices.sort(key=lambda idx: bin_vols[idx], reverse=True)
    return [float(centers[i]) for i in below_indices[:3]]


def _extract_next_earnings_date(t: yf.Ticker, info: dict[str, Any]) -> tuple[str | None, int | None]:
    """Extract upcoming earnings date (YYYY-MM-DD) and days remaining from yfinance."""
    today = date.today()
    candidates: list[date] = []

    try:
        cal = t.calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)):
                for item in ed:
                    if hasattr(item, "date"):
                        candidates.append(item.date())
                    elif isinstance(item, date):
                        candidates.append(item)
            elif hasattr(ed, "date"):
                candidates.append(ed.date())
            elif isinstance(ed, date):
                candidates.append(ed)
    except Exception:
        pass

    if not candidates:
        ts = info.get("earningsTimestamp") or info.get("earningsTimestampStart")
        if ts:
            try:
                candidates.append(datetime.fromtimestamp(float(ts)).date())
            except Exception:
                pass

    future = sorted(d for d in candidates if d >= today)
    if future:
        nxt = future[0]
        return nxt.isoformat(), (nxt - today).days
    return None, None


def _extract_quarterly_trend(t: yf.Ticker) -> list[dict[str, Any]]:
    """Extract last 4 quarters Revenue, Net Income, and Net Margin (%) from quarterly income statement."""
    rows: list[dict[str, Any]] = []
    try:
        stmt = t.quarterly_income_stmt
        if stmt is None or stmt.empty:
            return rows

        rev_row = None
        for key in ("Total Revenue", "Operating Revenue", "Revenue"):
            if key in stmt.index:
                rev_row = stmt.loc[key]
                break

        ni_row = None
        for key in ("Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"):
            if key in stmt.index:
                ni_row = stmt.loc[key]
                break

        cols = list(stmt.columns)[:4]
        for col in reversed(cols):  # oldest -> newest
            q_label = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
            rev = _clean_num(rev_row[col]) if rev_row is not None and col in rev_row.index else None
            ni = _clean_num(ni_row[col]) if ni_row is not None and col in ni_row.index else None
            margin = round((ni / rev) * 100.0, 1) if (rev and rev > 0 and ni is not None) else None
            if rev is not None or ni is not None:
                rows.append({"date": q_label, "revenue": rev, "net_income": ni, "net_margin_pct": margin})
    except Exception:
        pass
    return rows


def _extract_eps_surprises(t: yf.Ticker) -> list[dict[str, Any]]:
    """Extract last 4 quarters EPS Actual vs Estimate and Surprise %."""
    items: list[dict[str, Any]] = []
    try:
        eh = t.earnings_history
        if eh is None or eh.empty:
            return items
        for idx, row in eh.tail(4).iterrows():
            q_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            est = _clean_num(row.get("epsEstimate"))
            act = _clean_num(row.get("epsActual"))
            surp = _clean_num(row.get("surprisePercent"))
            if surp is not None and abs(surp) <= 5.0:
                surp = round(surp * 100.0, 1)
            elif surp is not None:
                surp = round(surp, 1)
            if est is not None or act is not None:
                beat = (act >= est) if (act is not None and est is not None) else None
                items.append({"date": q_str, "estimate": est, "actual": act, "surprise_pct": surp, "beat": beat})
    except Exception:
        pass
    return items


_CRUMB_BLOCKED_UNTIL: float = 0.0


def _fetch_fundamentals_for_ticker(
    ticker: str,
    force_refresh: bool = False,
    store: SmartDCAStore | None = None,
) -> dict[str, Any]:
    """Fetch slow Yahoo Finance `.info`, `.news`, 4Q income trend, EPS surprises, and next earnings date (24h SQLite cache)."""
    global _CRUMB_BLOCKED_UNTIL
    import time

    st = store or get_store()
    if not force_refresh:
        cached = st.get_fundamentals(ticker)
        if cached is not None and "quarterly_trend" in cached:
            return cached

    # Circuit breaker: if Yahoo crumb recently returned 429/401 on cloud IP, skip hammering crumb endpoints
    if time.monotonic() < _CRUMB_BLOCKED_UNTIL:
        stale = st.get_fundamentals(ticker, max_age_sec=-1)
        if stale is not None:
            return stale
        cur_code, cur_sym = detect_currency(ticker, None)
        return {
            "company_name": ticker,
            "sector": "Technology",
            "currency": cur_code,
            "currency_symbol": cur_sym,
            "headlines": [],
            "quarterly_trend": [],
            "eps_surprises": [],
        }

    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    info_has_data = bool(
        info.get("longName")
        or info.get("shortName")
        or info.get("forwardPE")
        or info.get("trailingPE")
        or info.get("targetMeanPrice")
    )

    if not info_has_data:
        # Trip circuit breaker for 10 minutes on cloud IP 429/401 — don't fire 3 more failing crumb calls!
        _CRUMB_BLOCKED_UNTIL = time.monotonic() + 600.0
        stale = st.get_fundamentals(ticker, max_age_sec=-1)
        if stale is not None:
            return stale
        cur_code, cur_sym = detect_currency(ticker, None)
        return {
            "company_name": ticker,
            "sector": "Technology",
            "currency": cur_code,
            "currency_symbol": cur_sym,
            "headlines": [],
            "quarterly_trend": [],
            "eps_surprises": [],
        }

    try:
        raw_news = t.news or []
    except Exception:
        raw_news = []

    headlines: list[str] = []
    for n in raw_news[:5]:
        if isinstance(n, dict):
            content = n.get("content") if isinstance(n.get("content"), dict) else n
            title = content.get("title") or n.get("title")
            if title:
                headlines.append(str(title))

    next_earnings_date, days_to_earnings = _extract_next_earnings_date(t, info)
    quarterly_trend = _extract_quarterly_trend(t)
    eps_surprises = _extract_eps_surprises(t)

    has_real_info = info_has_data or bool(quarterly_trend)
    if not has_real_info:
        # Yahoo crumb 429/401 on shared cloud IP: keep last known good fundamentals instead of wiping!
        stale = st.get_fundamentals(ticker, max_age_sec=-1)
        if stale is not None:
            return stale

    cur_code, cur_sym = detect_currency(ticker, info.get("currency"))
    bundle = {
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "Technology",
        "currency": cur_code,
        "currency_symbol": cur_sym,
        "trailing_pe": _clean_num(info.get("trailingPE")),
        "forward_pe": _clean_num(info.get("forwardPE")),
        "peg_ratio": _clean_num(info.get("pegRatio")),
        "roe": _clean_num(info.get("returnOnEquity")),
        "gross_margin": _clean_num(info.get("grossMargins")),
        "op_margin": _clean_num(info.get("operatingMargins")),
        "profit_margin": _clean_num(info.get("profitMargins")),
        "rev_growth": _clean_num(info.get("revenueGrowth")),
        "earn_growth": _clean_num(info.get("earningsGrowth")),
        "debt_to_eq": _clean_num(info.get("debtToEquity")),
        "fcf": _clean_num(info.get("freeCashflow")),
        "beta": _clean_num(info.get("beta")),
        "recommendation": str(info.get("recommendationKey") or "").upper(),
        "target_mean": _clean_num(info.get("targetMeanPrice")),
        "target_low": _clean_num(info.get("targetLowPrice")),
        "target_high": _clean_num(info.get("targetHighPrice")),
        "num_analysts": int(info.get("numberOfAnalystOpinions") or 0),
        "headlines": headlines,
        "next_earnings_date": next_earnings_date,
        "days_to_earnings": days_to_earnings,
        "quarterly_trend": quarterly_trend,
        "eps_surprises": eps_surprises,
    }
    if has_real_info:
        st.set_fundamentals(ticker, bundle)
    return bundle


def _fetch_chart_direct(ticker: str, period: str = "1y", auto_adjust: bool = False) -> pd.DataFrame | None:
    """Crumb-free fallback using Yahoo Finance v8 chart API (works even when crumb endpoint returns 429 on Render)."""
    try:
        import requests

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        resp = requests.get(
            url,
            params={"range": period, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        res_list = (resp.json().get("chart") or {}).get("result") or []
        if not res_list:
            return None
        r0 = res_list[0]
        timestamps = r0.get("timestamp") or []
        quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        adj_list = (r0.get("indicators") or {}).get("adjclose") or []
        adjclose = adj_list[0].get("adjclose") if adj_list else None
        if not timestamps or not quote.get("close"):
            return None

        dates = pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(None).normalize()
        closes = quote.get("close")
        opens = quote.get("open") or closes
        highs = quote.get("high") or closes
        lows = quote.get("low") or closes
        vols = quote.get("volume") or [0] * len(timestamps)

        df = pd.DataFrame(
            {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vols},
            index=dates,
        ).dropna(subset=["Close"])

        if auto_adjust and adjclose and len(adjclose) == len(timestamps):
            adj_s = pd.Series(adjclose, index=dates).reindex(df.index)
            ratio = (adj_s / df["Close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
            for col in ("Open", "High", "Low", "Close"):
                df[col] = df[col] * ratio

        return df if not df.empty else None
    except Exception:
        return None


def _batch_download_ohlcv(tickers: list[str], period: str = "1y", auto_adjust: bool = False) -> dict[str, pd.DataFrame]:
    """Download OHLCV for multiple tickers in a single batched yfinance call."""
    if not tickers:
        return {}

    frames: dict[str, pd.DataFrame] = {}
    if len(tickers) == 1:
        tkr = tickers[0]
        try:
            df = yf.Ticker(tkr).history(period=period, interval="1d", auto_adjust=auto_adjust)
            if df is not None and not df.empty:
                frames[tkr] = df.dropna(subset=["Close"])
        except Exception:
            pass
        return frames

    try:
        raw = yf.download(
            tickers=tickers,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=auto_adjust,
            threads=True,
            progress=False,
        )
        if raw is not None and not raw.empty:
            if isinstance(raw.columns, pd.MultiIndex):
                top_level = set(raw.columns.get_level_values(0))
                for tkr in tickers:
                    if tkr in top_level:
                        sub = raw[tkr].dropna(subset=["Close"])
                        if not sub.empty:
                            frames[tkr] = sub
            else:
                sub = raw.dropna(subset=["Close"])
                if not sub.empty:
                    frames[tickers[0]] = sub
    except Exception:
        pass

    # Fallback for any ticker missing from the batch call (including crumb-free direct chart API)
    missing = [t for t in tickers if t not in frames]
    if missing:
        def _single(tkr: str) -> tuple[str, pd.DataFrame | None]:
            try:
                df = yf.Ticker(tkr).history(period=period, interval="1d", auto_adjust=auto_adjust)
                if df is not None and not df.empty:
                    return tkr, df.dropna(subset=["Close"])
            except Exception:
                pass
            df_direct = _fetch_chart_direct(tkr, period=period, auto_adjust=auto_adjust)
            return tkr, df_direct

        with ThreadPoolExecutor(max_workers=min(3, len(missing))) as pool:
            for tkr, df in pool.map(_single, missing):
                if df is not None:
                    frames[tkr] = df
    return frames


def compute_dip_metrics(
    ticker: str,
    df: pd.DataFrame,
    fundamentals: dict[str, Any],
    signals: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Compute technicals, 3-tier ladder, Dip Score (with optional AI committee boost), and chart series."""
    if df is None or df.empty or len(df) < 30:
        return None

    df = df.copy()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["EMA100"] = df["Close"].ewm(span=100, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

    sma20 = df["Close"].rolling(20).mean()
    std20 = df["Close"].rolling(20).std()
    bb_lower = float((sma20 - 2.0 * std20).iloc[-1])

    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100.0 - (100.0 / (1.0 + rs))

    close = float(df["Close"].iloc[-1])
    ema50 = float(df["EMA50"].iloc[-1])
    ema100 = float(df["EMA100"].iloc[-1])
    ema200 = float(df["EMA200"].iloc[-1])
    rsi_raw = float(df["RSI"].iloc[-1])
    rsi = rsi_raw if not math.isnan(rsi_raw) else 50.0

    high_52w = float(df["High"].max())
    low_52w = float(df["Low"].min())
    low_60d = float(df["Low"].tail(60).min())
    drawdown_pct = ((close - high_52w) / high_52w) * 100.0
    dist_ema200_pct = ((close - ema200) / ema200) * 100.0

    swing_range = max(high_52w - low_52w, close * 0.05)
    pos_52w_pct = max(2.0, min(98.0, ((close - low_52w) / swing_range) * 100.0))

    fib_382 = high_52w - 0.382 * swing_range
    fib_500 = high_52w - 0.500 * swing_range
    fib_618 = high_52w - 0.618 * swing_range

    vp_supports = compute_volume_profile_supports(df, close)
    vp_node_1 = vp_supports[0] if vp_supports else fib_382

    t1_candidates = [p for p in [bb_lower, fib_382, ema50, vp_node_1, close * 0.96] if p <= close * 1.005]
    tier1_price = max(t1_candidates) if t1_candidates else close * 0.95

    t2_candidates = [p for p in [ema200, fib_500, low_60d, ema100] if p < tier1_price * 0.96]
    tier2_price = max(t2_candidates) if t2_candidates else tier1_price * 0.91

    t3_candidates = [p for p in [fib_618, low_52w * 1.04, tier2_price * 0.88] if p < tier2_price * 0.95]
    tier3_price = max(t3_candidates) if t3_candidates else tier2_price * 0.89

    dist_to_t1_pct = ((close - tier1_price) / close) * 100.0
    if close <= tier2_price * 1.02:
        hit_status = "hit_t2"
        hit_label = "🔥 แตะโซนไม้ 2 แล้ว!"
    elif close <= tier1_price * 1.02 or rsi <= 36.0:
        hit_status = "hit_t1"
        hit_label = "🎯 แตะโซนช้อนไม้ 1 แล้ว!"
    else:
        hit_status = "waiting"
        hit_label = f"⏳ รอย่ออีก -{dist_to_t1_pct:.1f}% ถึงไม้ 1"

    target_mean = fundamentals.get("target_mean")
    wall_st_upside = ((target_mean - close) / close * 100.0) if (target_mean and close > 0) else None

    peg_ratio = fundamentals.get("peg_ratio")
    forward_pe = fundamentals.get("forward_pe")
    earn_growth = fundamentals.get("earn_growth")
    roe = fundamentals.get("roe")
    fcf = fundamentals.get("fcf")

    score = 45.0
    if drawdown_pct <= -25:
        score += 15
    elif drawdown_pct <= -15:
        score += 10
    elif drawdown_pct <= -8:
        score += 5
    elif drawdown_pct >= -3:
        score -= 10

    if rsi <= 30:
        score += 15
    elif rsi <= 40:
        score += 10
    elif rsi >= 70:
        score -= 15

    if close <= ema200:
        score += 8

    if peg_ratio is not None:
        if 0 < peg_ratio <= 0.8:
            score += 12
        elif 0.8 < peg_ratio <= 1.5:
            score += 6
        elif peg_ratio > 2.8:
            score -= 8
    if forward_pe is not None:
        if 0 < forward_pe <= 18:
            score += 8
        elif forward_pe > 45:
            score -= 6
    if wall_st_upside is not None:
        if wall_st_upside >= 35:
            score += 10
        elif wall_st_upside >= 15:
            score += 5
        elif wall_st_upside < 0:
            score -= 10

    if earn_growth is not None and earn_growth >= 0.20:
        score += 8
    elif earn_growth is not None and earn_growth < -0.15:
        score -= 10
    if roe is not None and roe >= 0.20:
        score += 6
    if fcf is not None and fcf > 0:
        score += 4

    # Blend AI Hedge Fund committee conviction [-1, +1] up to ±6 points when available
    if signals:
        vals = [float(s.get("value", 0.0)) for s in signals if s.get("value") is not None]
        if vals:
            avg_conv = sum(vals) / len(vals)
            score += max(-6.0, min(6.0, avg_conv * 6.0))

    dip_score = max(10, min(99, int(round(score))))

    if dip_score >= 82:
        dca_mult = "2.0x (ออมเพิ่ม 2 เท่า!)"
        dca_mult_en = "2.0x (CONVICTION BUY THE DIP - Double DCA)"
        dca_mult_num = 2.0
        zone_color = "#22c55e"
        zone_style = "bold green"
    elif dip_score >= 68:
        dca_mult = "1.5x (ออมเพิ่ม 1.5 เท่า)"
        dca_mult_en = "1.5x (ACCUMULATE DIP - Add +50% DCA)"
        dca_mult_num = 1.5
        zone_color = "#4ade80"
        zone_style = "bold green"
    elif dip_score >= 48:
        dca_mult = "1.0x (ออมปกติ)"
        dca_mult_en = "1.0x (STANDARD DCA - Wait for Limit Tiers)"
        dca_mult_num = 1.0
        zone_color = "#38bdf8"
        zone_style = "bold cyan"
    else:
        dca_mult = "0.5x (เก็บเงินสดรอย่อ)"
        dca_mult_en = "0.5x (HOLD CASH FOR DIP - Valuation Rich)"
        dca_mult_num = 0.5
        zone_color = "#f59e0b"
        zone_style = "bold yellow"

    candles = []
    ema50_series = []
    ema200_series = []
    markers = []
    last_marker_idx = -10

    for i, (idx, row) in enumerate(df.iterrows()):
        d_str = idx.strftime("%Y-%m-%d")
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        e50, e200 = float(row["EMA50"]), float(row["EMA200"])
        r_val = float(row["RSI"]) if not math.isnan(float(row["RSI"])) else 50.0

        candles.append({"time": d_str, "open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(c, 2)})
        ema50_series.append({"time": d_str, "value": round(e50, 2)})
        ema200_series.append({"time": d_str, "value": round(e200, 2)})

        if i - last_marker_idx >= 12:
            if r_val <= 32.0:
                markers.append(
                    {
                        "time": d_str,
                        "position": "belowBar",
                        "color": "#22c55e",
                        "shape": "arrowUp",
                        "text": f"Buy Dip (RSI {r_val:.0f})",
                    }
                )
                last_marker_idx = i
            elif l <= e200 * 1.01 and c >= e200 and i > 30:
                markers.append(
                    {
                        "time": d_str,
                        "position": "belowBar",
                        "color": "#38bdf8",
                        "shape": "arrowUp",
                        "text": "EMA200 Dip",
                    }
                )
                last_marker_idx = i

    cur_code = fundamentals.get("currency") or "USD"
    cur_sym = fundamentals.get("currency_symbol") or "$"

    return {
        "ticker": ticker,
        "company_name": fundamentals.get("company_name") or ticker,
        "sector": fundamentals.get("sector") or "Technology",
        "currency": cur_code,
        "currency_symbol": cur_sym,
        "close": close,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pos_52w_pct": round(pos_52w_pct, 1),
        "drawdown_pct": drawdown_pct,
        "ema50": ema50,
        "ema100": ema100,
        "ema200": ema200,
        "dist_ema200_pct": dist_ema200_pct,
        "fib_382": round(fib_382, 2),
        "fib_500": round(fib_500, 2),
        "fib_618": round(fib_618, 2),
        "vp_node_1": round(vp_node_1, 2),
        "rsi": rsi,
        "trailing_pe": fundamentals.get("trailing_pe"),
        "forward_pe": forward_pe,
        "peg_ratio": peg_ratio,
        "roe": roe,
        "gross_margin": fundamentals.get("gross_margin"),
        "op_margin": fundamentals.get("op_margin"),
        "profit_margin": fundamentals.get("profit_margin"),
        "rev_growth": fundamentals.get("rev_growth"),
        "earn_growth": earn_growth,
        "debt_to_eq": fundamentals.get("debt_to_eq"),
        "fcf": fcf,
        "beta": fundamentals.get("beta"),
        "recommendation": fundamentals.get("recommendation") or "",
        "next_earnings_date": fundamentals.get("next_earnings_date"),
        "days_to_earnings": fundamentals.get("days_to_earnings"),
        "quarterly_trend": fundamentals.get("quarterly_trend") or [],
        "eps_surprises": fundamentals.get("eps_surprises") or [],
        "target_mean": target_mean,
        "target_low": fundamentals.get("target_low"),
        "target_high": fundamentals.get("target_high"),
        "num_analysts": fundamentals.get("num_analysts") or 0,
        "wall_st_upside": wall_st_upside,
        "headlines": fundamentals.get("headlines") or [],
        "dip_score": dip_score,
        "dca_mult": dca_mult,
        "dca_mult_en": dca_mult_en,
        "dca_mult_num": dca_mult_num,
        "zone_color": zone_color,
        "zone_style": zone_style,
        "hit_status": hit_status,
        "hit_label": hit_label,
        "tier1_price": round(tier1_price, 2),
        "tier2_price": round(tier2_price, 2),
        "tier3_price": round(tier3_price, 2),
        "tier1_disc": ((tier1_price - high_52w) / high_52w) * 100.0,
        "tier2_disc": ((tier2_price - high_52w) / high_52w) * 100.0,
        "tier3_disc": ((tier3_price - high_52w) / high_52w) * 100.0,
        "candles": candles,
        "ema50_series": ema50_series,
        "ema200_series": ema200_series,
        "markers": markers,
    }


def fetch_all_tickers_batch(
    tickers: list[str],
    force_refresh: bool = False,
    force_fundamentals: bool = False,
    signals_by_ticker: dict[str, list[dict]] | None = None,
    store: SmartDCAStore | None = None,
) -> list[dict[str, Any]]:
    """Fetch all tickers using split SQLite caches + single batched OHLCV download."""
    st = store or get_store()
    sigs_map = signals_by_ticker or {}

    results_map: dict[str, dict[str, Any]] = {}
    stale_tickers: list[str] = []

    for tkr in tickers:
        if not force_refresh:
            cached = st.get_price_analysis(tkr)
            if cached is not None:
                # Ensure currency metadata exists on older cached entries
                if "currency_symbol" not in cached:
                    cur_code, cur_sym = detect_currency(tkr, cached.get("currency"))
                    cached["currency"] = cur_code
                    cached["currency_symbol"] = cur_sym
                results_map[tkr] = cached
                continue
        stale_tickers.append(tkr)

    if stale_tickers:
        # 1. Batch download OHLCV for all stale tickers in one call
        ohlcv_map = _batch_download_ohlcv(stale_tickers, period="1y")

        # 2. Fetch fundamentals (cached 24h in SQLite, only hits Yahoo on cache miss!)
        def _get_f(tkr: str) -> tuple[str, dict[str, Any]]:
            return tkr, _fetch_fundamentals_for_ticker(tkr, force_refresh=force_fundamentals, store=st)

        fundamentals_map: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(2, max(1, len(stale_tickers)))) as pool:
            for tkr, fund_data in pool.map(_get_f, stale_tickers):
                fundamentals_map[tkr] = fund_data

        # 3. Compute technicals + dip score and save to 30m price cache
        for tkr in stale_tickers:
            df = ohlcv_map.get(tkr)
            if df is None:
                continue
            res = compute_dip_metrics(tkr, df, fundamentals_map.get(tkr, {}), signals=sigs_map.get(tkr))
            if res is not None:
                st.set_price_analysis(tkr, res)
                results_map[tkr] = res

    return [results_map[t] for t in tickers if t in results_map]
