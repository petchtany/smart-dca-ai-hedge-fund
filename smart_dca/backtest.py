"""Historical Smart DCA vs Fixed DCA backtester (strictly point-in-time, zero lookahead bias)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .market_data import _batch_download_ohlcv


def _compute_max_drawdown(equity_series: list[float]) -> float:
    if not equity_series:
        return 0.0
    arr = np.asarray(equity_series, dtype=float)
    peaks = np.maximum.accumulate(arr)
    valid = peaks > 0
    if not np.any(valid):
        return 0.0
    dd = ((arr[valid] - peaks[valid]) / peaks[valid]) * 100.0
    return float(np.min(dd))


def backtest_ticker_dca(
    ticker: str,
    df: pd.DataFrame,
    monthly_budget: float = 1000.0,
) -> dict[str, Any] | None:
    """Simulate Fixed DCA vs Smart DCA (Dip-Score scaled + dry-powder dip deployment) on daily OHLCV."""
    if df is None or df.empty or len(df) < 120:
        return None

    df = df.copy().sort_index()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    df["High252"] = df["High"].rolling(252, min_periods=60).max()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = 100.0 - (100.0 / (1.0 + rs))

    # Start simulation after 60 trading days warm-up
    sim_df = df.iloc[60:].copy()
    if len(sim_df) < 40:
        return None

    fixed_shares = 0.0
    fixed_invested = 0.0
    fixed_equity: list[float] = []

    smart_shares = 0.0
    smart_cash = 0.0
    smart_contributed = 0.0
    smart_equity: list[float] = []

    last_month: tuple[int, int] | None = None
    dip_deployments = 0

    for idx, row in sim_df.iterrows():
        close = float(row["Close"])
        ema200 = float(row["EMA200"])
        high252 = float(row["High252"]) if not math.isnan(float(row["High252"])) else close
        rsi = float(row["RSI"]) if not math.isnan(float(row["RSI"])) else 50.0
        drawdown_pct = ((close - high252) / high252) * 100.0 if high252 > 0 else 0.0

        ym = (idx.year, idx.month)
        if ym != last_month:
            last_month = ym
            # Fixed DCA: invest 100% of monthly budget on first trading day of each month
            fixed_shares += monthly_budget / close
            fixed_invested += monthly_budget

            # Smart DCA: add monthly budget to cash pool, then deploy according to technical dip score
            smart_cash += monthly_budget
            smart_contributed += monthly_budget

            tech_score = 50
            if drawdown_pct <= -25:
                tech_score += 20
            elif drawdown_pct <= -15:
                tech_score += 12
            elif drawdown_pct <= -8:
                tech_score += 6
            elif drawdown_pct >= -3:
                tech_score -= 12

            if rsi <= 32:
                tech_score += 18
            elif rsi <= 42:
                tech_score += 10
            elif rsi >= 70:
                tech_score -= 15

            if close <= ema200:
                tech_score += 10

            if tech_score >= 78:
                mult = 2.0
            elif tech_score >= 64:
                mult = 1.5
            elif tech_score >= 46:
                mult = 1.0
            else:
                mult = 0.5

            deploy = min(smart_cash, monthly_budget * mult)
            if deploy > 0:
                smart_shares += deploy / close
                smart_cash -= deploy
        else:
            # Intra-month buy-the-dip trigger: deploy dry powder when deeply oversold (RSI <= 30 or <= -18% dip)
            if smart_cash >= monthly_budget * 0.5 and (rsi <= 30.0 or (drawdown_pct <= -18.0 and close <= ema200)):
                deploy = smart_cash * 0.5
                smart_shares += deploy / close
                smart_cash -= deploy
                dip_deployments += 1

        fixed_equity.append(fixed_shares * close)
        smart_equity.append(smart_shares * close + smart_cash)

    final_close = float(sim_df["Close"].iloc[-1])
    fixed_final = fixed_shares * final_close
    smart_final = smart_shares * final_close + smart_cash

    fixed_ret = ((fixed_final - fixed_invested) / fixed_invested * 100.0) if fixed_invested > 0 else 0.0
    smart_ret = ((smart_final - smart_contributed) / smart_contributed * 100.0) if smart_contributed > 0 else 0.0
    fixed_avg_cost = (fixed_invested / fixed_shares) if fixed_shares > 0 else 0.0
    smart_spent = smart_contributed - smart_cash
    smart_avg_cost = (smart_spent / smart_shares) if smart_shares > 0 else 0.0

    return {
        "ticker": ticker,
        "start_date": sim_df.index[0].strftime("%Y-%m-%d"),
        "end_date": sim_df.index[-1].strftime("%Y-%m-%d"),
        "months": int(round(fixed_invested / monthly_budget)),
        "total_contributed": round(fixed_invested, 2),
        "fixed_final_value": round(fixed_final, 2),
        "fixed_return_pct": round(fixed_ret, 2),
        "fixed_avg_cost": round(fixed_avg_cost, 2),
        "fixed_max_dd_pct": round(_compute_max_drawdown(fixed_equity), 2),
        "smart_final_value": round(smart_final, 2),
        "smart_return_pct": round(smart_ret, 2),
        "smart_avg_cost": round(smart_avg_cost, 2),
        "smart_cash_remaining": round(smart_cash, 2),
        "smart_max_dd_pct": round(_compute_max_drawdown(smart_equity), 2),
        "alpha_pct": round(smart_ret - fixed_ret, 2),
        "dip_deployments": dip_deployments,
    }


def run_portfolio_backtest(
    tickers: list[str],
    period: str = "3y",
    monthly_budget_per_ticker: float = 1000.0,
) -> dict[str, Any]:
    """Run Smart DCA vs Fixed DCA backtest across all tickers using split/dividend-adjusted prices."""
    frames = _batch_download_ohlcv(tickers, period=period, auto_adjust=True)
    rows: list[dict[str, Any]] = []
    for tkr in tickers:
        df = frames.get(tkr)
        if df is None:
            continue
        res = backtest_ticker_dca(tkr, df, monthly_budget=monthly_budget_per_ticker)
        if res is not None:
            rows.append(res)

    if not rows:
        return {"period": period, "tickers": [], "summary": {}}

    tot_contrib = sum(r["total_contributed"] for r in rows)
    tot_fixed = sum(r["fixed_final_value"] for r in rows)
    tot_smart = sum(r["smart_final_value"] for r in rows)
    fixed_ret = ((tot_fixed - tot_contrib) / tot_contrib * 100.0) if tot_contrib > 0 else 0.0
    smart_ret = ((tot_smart - tot_contrib) / tot_contrib * 100.0) if tot_contrib > 0 else 0.0
    avg_fixed_dd = sum(r["fixed_max_dd_pct"] for r in rows) / len(rows)
    avg_smart_dd = sum(r["smart_max_dd_pct"] for r in rows) / len(rows)
    win_count = sum(1 for r in rows if r["alpha_pct"] > 0)

    return {
        "period": period,
        "monthly_budget_per_ticker": monthly_budget_per_ticker,
        "tickers": sorted(rows, key=lambda x: x["alpha_pct"], reverse=True),
        "summary": {
            "count": len(rows),
            "total_contributed": round(tot_contrib, 2),
            "fixed_final_value": round(tot_fixed, 2),
            "fixed_return_pct": round(fixed_ret, 2),
            "smart_final_value": round(tot_smart, 2),
            "smart_return_pct": round(smart_ret, 2),
            "portfolio_alpha_pct": round(smart_ret - fixed_ret, 2),
            "avg_fixed_max_dd_pct": round(avg_fixed_dd, 2),
            "avg_smart_max_dd_pct": round(avg_smart_dd, 2),
            "smart_wins": win_count,
        },
    }
