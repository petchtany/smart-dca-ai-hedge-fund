"""Yahoo Finance (yfinance) free fallback implementation for DataClient."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from hedge_fund.data.models import (
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsData,
    EarningsRecord,
    FinancialMetrics,
    InsiderTrade,
    Price,
    PriceSnapshot,
)

logger = logging.getLogger(__name__)


def _clean_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _safe_div(num: Any, den: Any) -> float | None:
    n = _clean_float(num)
    d = _clean_float(den)
    if n is None or d is None or d == 0:
        return None
    return _clean_float(n / d)


def _get_row_val(df: pd.DataFrame | None, col: Any, keys: list[str]) -> float | None:
    if df is None or df.empty or col not in df.columns:
        return None
    series = df[col]
    for k in keys:
        if k in series.index:
            v = _clean_float(series[k])
            if v is not None:
                return v
    return None


def yf_get_prices(
    ticker: str,
    start_date: str,
    end_date: str,
    interval: str = "day",
    interval_multiplier: int = 1,
) -> list[Price]:
    yf_interval = "1d"
    if interval == "minute":
        if interval_multiplier in (1, 2, 5, 15, 30, 60, 90):
            yf_interval = f"{interval_multiplier}m"
        else:
            yf_interval = "5m"
    elif interval == "week":
        yf_interval = "1wk"
    elif interval == "month":
        yf_interval = "1mo"

    start_d = start_date[:10]
    end_d = end_date[:10]
    try:
        end_exclusive = (date.fromisoformat(end_d) + timedelta(days=2)).isoformat()
    except ValueError:
        end_exclusive = end_d

    t = yf.Ticker(ticker)
    df = t.history(start=start_d, end=end_exclusive, interval=yf_interval, auto_adjust=False)
    if df is None or df.empty:
        return []

    prices: list[Price] = []
    for idx, row in df.iterrows():
        o = _clean_float(row.get("Open"))
        c = _clean_float(row.get("Close"))
        h = _clean_float(row.get("High"))
        low = _clean_float(row.get("Low"))
        vol = _clean_float(row.get("Volume")) or 0.0
        if o is None or c is None or h is None or low is None or c <= 0:
            continue
        if yf_interval == "1d":
            t_str = idx.strftime("%Y-%m-%d")
            if not (start_d <= t_str <= end_d):
                continue
        else:
            t_str = idx.strftime("%Y-%m-%dT%H:%M:%S")
            if not (start_d <= t_str[:10] <= end_d):
                continue
        prices.append(
            Price(
                open=o,
                close=c,
                high=h,
                low=low,
                volume=int(vol),
                time=t_str,
            )
        )
    return prices


def yf_get_snapshot(ticker: str) -> PriceSnapshot | None:
    t = yf.Ticker(ticker)
    df = t.history(period="5d", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None
    last_close = _clean_float(df["Close"].iloc[-1])
    if last_close is None:
        return None
    day_change = None
    day_change_pct = None
    if len(df) >= 2:
        prev_close = _clean_float(df["Close"].iloc[-2])
        if prev_close and prev_close > 0:
            day_change = last_close - prev_close
            day_change_pct = (day_change / prev_close) * 100.0
    return PriceSnapshot(
        ticker=ticker,
        price=last_close,
        time=datetime.now(timezone.utc).isoformat(),
        day_change=day_change,
        day_change_percent=day_change_pct,
    )


def yf_get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    if period in ("quarterly", "ttm"):
        inc = getattr(t, "quarterly_income_stmt", None)
        bal = getattr(t, "quarterly_balance_sheet", None)
        cf = getattr(t, "quarterly_cashflow", None)
    else:
        inc = getattr(t, "income_stmt", None)
        bal = getattr(t, "balance_sheet", None)
        cf = getattr(t, "cashflow", None)

    currency = info.get("currency", "USD")
    mcap_info = _clean_float(info.get("marketCap"))
    ev_info = _clean_float(info.get("enterpriseValue"))
    pe_info = _clean_float(info.get("trailingPE") or info.get("forwardPE"))
    pb_info = _clean_float(info.get("priceToBook"))
    ps_info = _clean_float(info.get("priceToSalesTrailing12Months"))
    ev_ebitda_info = _clean_float(info.get("enterpriseToEbitda"))
    ev_rev_info = _clean_float(info.get("enterpriseToRevenue"))
    peg_info = _clean_float(info.get("pegRatio"))

    cols = []
    if inc is not None and not inc.empty:
        cols = [c for c in inc.columns if hasattr(c, "strftime") and c.strftime("%Y-%m-%d") <= end_date[:10]]

    results: list[FinancialMetrics] = []
    for i, col in enumerate(cols[:limit]):
        rep_date = col.strftime("%Y-%m-%d")
        rev = _get_row_val(inc, col, ["Total Revenue", "Operating Revenue"])
        gp = _get_row_val(inc, col, ["Gross Profit"])
        op_inc = _get_row_val(inc, col, ["Operating Income", "EBIT"])
        net_inc = _get_row_val(inc, col, ["Net Income", "Net Income Common Stockholders"])
        ebitda = _get_row_val(inc, col, ["EBITDA", "Normalized EBITDA"])
        eps = _get_row_val(inc, col, ["Diluted EPS", "Basic EPS"])
        shares = _get_row_val(inc, col, ["Diluted Average Shares", "Basic Average Shares"])

        tot_assets = _get_row_val(bal, col, ["Total Assets"])
        tot_liab = _get_row_val(bal, col, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
        eq = _get_row_val(bal, col, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"])
        tot_debt = _get_row_val(bal, col, ["Total Debt"])
        curr_assets = _get_row_val(bal, col, ["Current Assets"])
        curr_liab = _get_row_val(bal, col, ["Current Liabilities"])
        cash = _get_row_val(bal, col, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])

        fcf = _get_row_val(cf, col, ["Free Cash Flow"])
        ocf = _get_row_val(cf, col, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])

        prev_rev = None
        prev_net = None
        if i + 1 < len(cols):
            prev_col = cols[i + 1]
            prev_rev = _get_row_val(inc, prev_col, ["Total Revenue", "Operating Revenue"])
            prev_net = _get_row_val(inc, prev_col, ["Net Income", "Net Income Common Stockholders"])

        rev_growth = _safe_div(rev - prev_rev, abs(prev_rev)) if (rev is not None and prev_rev) else _clean_float(info.get("revenueGrowth"))
        earn_growth = _safe_div(net_inc - prev_net, abs(prev_net)) if (net_inc is not None and prev_net) else _clean_float(info.get("earningsGrowth"))

        results.append(
            FinancialMetrics(
                ticker=ticker,
                report_period=rep_date,
                period=period,
                currency=currency,
                filing_date=rep_date,
                market_cap=mcap_info,
                enterprise_value=ev_info,
                price_to_earnings_ratio=pe_info,
                price_to_book_ratio=pb_info,
                price_to_sales_ratio=ps_info,
                enterprise_value_to_ebitda_ratio=ev_ebitda_info,
                enterprise_value_to_revenue_ratio=ev_rev_info,
                free_cash_flow_yield=_safe_div(fcf, mcap_info),
                peg_ratio=peg_info,
                gross_margin=_safe_div(gp, rev) or _clean_float(info.get("grossMargins")),
                operating_margin=_safe_div(op_inc, rev) or _clean_float(info.get("operatingMargins")),
                net_margin=_safe_div(net_inc, rev) or _clean_float(info.get("profitMargins")),
                return_on_equity=_safe_div(net_inc, eq) or _clean_float(info.get("returnOnEquity")),
                return_on_assets=_safe_div(net_inc, tot_assets) or _clean_float(info.get("returnOnAssets")),
                return_on_invested_capital=_safe_div(op_inc, (eq or 0) + (tot_debt or 0)) if (eq or tot_debt) else None,
                asset_turnover=_safe_div(rev, tot_assets),
                current_ratio=_safe_div(curr_assets, curr_liab) or _clean_float(info.get("currentRatio")),
                quick_ratio=_clean_float(info.get("quickRatio")),
                cash_ratio=_safe_div(cash, curr_liab),
                operating_cash_flow_ratio=_safe_div(ocf, curr_liab),
                debt_to_equity=_safe_div(tot_debt, eq) or _clean_float(info.get("debtToEquity")),
                debt_to_assets=_safe_div(tot_debt, tot_assets),
                revenue_growth=rev_growth,
                earnings_growth=earn_growth,
                earnings_per_share_growth=earn_growth,
                payout_ratio=_clean_float(info.get("payoutRatio")),
                earnings_per_share=eps or _clean_float(info.get("trailingEps")),
                book_value_per_share=_safe_div(eq, shares) or _clean_float(info.get("bookValue")),
                free_cash_flow_per_share=_safe_div(fcf, shares),
            )
        )

    if not results and info:
        results.append(
            FinancialMetrics(
                ticker=ticker,
                report_period=end_date[:10],
                period=period,
                currency=currency,
                filing_date=end_date[:10],
                market_cap=mcap_info,
                enterprise_value=ev_info,
                price_to_earnings_ratio=pe_info,
                price_to_book_ratio=pb_info,
                price_to_sales_ratio=ps_info,
                enterprise_value_to_ebitda_ratio=ev_ebitda_info,
                enterprise_value_to_revenue_ratio=ev_rev_info,
                peg_ratio=peg_info,
                gross_margin=_clean_float(info.get("grossMargins")),
                operating_margin=_clean_float(info.get("operatingMargins")),
                net_margin=_clean_float(info.get("profitMargins")),
                return_on_equity=_clean_float(info.get("returnOnEquity")),
                return_on_assets=_clean_float(info.get("returnOnAssets")),
                current_ratio=_clean_float(info.get("currentRatio")),
                quick_ratio=_clean_float(info.get("quickRatio")),
                debt_to_equity=_clean_float(info.get("debtToEquity")),
                revenue_growth=_clean_float(info.get("revenueGrowth")),
                earnings_growth=_clean_float(info.get("earningsGrowth")),
                payout_ratio=_clean_float(info.get("payoutRatio")),
                earnings_per_share=_clean_float(info.get("trailingEps")),
                book_value_per_share=_clean_float(info.get("bookValue")),
            )
        )
    return results[:limit]


def yf_get_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[CompanyNews]:
    t = yf.Ticker(ticker)
    try:
        raw_news = t.news or []
    except Exception:
        raw_news = []

    items: list[CompanyNews] = []
    for n in raw_news:
        if not isinstance(n, dict):
            continue
        content = n.get("content") if isinstance(n.get("content"), dict) else n
        title = content.get("title") or n.get("title")
        if not title:
            continue
        provider = content.get("provider")
        if isinstance(provider, dict):
            source = provider.get("displayName") or "Yahoo Finance"
        else:
            source = n.get("publisher") or "Yahoo Finance"
        pub_date = content.get("pubDate") or n.get("providerPublishTime")
        if isinstance(pub_date, (int, float)):
            date_str = datetime.fromtimestamp(pub_date, tz=timezone.utc).strftime("%Y-%m-%d")
        elif isinstance(pub_date, str):
            date_str = pub_date[:10]
        else:
            date_str = end_date[:10]

        canonical = content.get("canonicalUrl")
        url = canonical.get("url") if isinstance(canonical, dict) else n.get("link")
        items.append(
            CompanyNews(
                ticker=ticker,
                title=str(title),
                source=str(source),
                date=date_str,
                url=url,
            )
        )
        if len(items) >= limit:
            break
    return items


def yf_get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    t = yf.Ticker(ticker)
    try:
        df = t.insider_transactions
    except Exception:
        df = None
    if df is None or df.empty:
        return []

    trades: list[InsiderTrade] = []
    for _, row in df.iterrows():
        start_dt = row.get("Start Date")
        if hasattr(start_dt, "strftime"):
            f_date = start_dt.strftime("%Y-%m-%d")
        else:
            f_date = str(start_dt)[:10] if start_dt is not None else end_date[:10]
        if f_date > end_date[:10]:
            continue
        if start_date and f_date < start_date[:10]:
            continue
        name = str(row.get("Insider") or "Insider")
        title = str(row.get("Position") or "") or None
        shares = _clean_float(row.get("Shares"))
        val = _clean_float(row.get("Value"))
        price = _safe_div(val, shares) if (val is not None and shares) else None
        trades.append(
            InsiderTrade(
                ticker=ticker,
                name=name,
                filing_date=f_date,
                title=title,
                transaction_date=f_date,
                transaction_type=str(row.get("Transaction") or "Trade"),
                transaction_shares=shares,
                transaction_price_per_share=price,
                transaction_value=val,
            )
        )
        if len(trades) >= limit:
            break
    return trades


def yf_get_company_facts(ticker: str) -> CompanyFacts | None:
    t = yf.Ticker(ticker)
    try:
        info = t.info or {}
    except Exception:
        info = {}
    if not info:
        return CompanyFacts(ticker=ticker, is_active=True, name=ticker)
    return CompanyFacts(
        ticker=ticker,
        is_active=True,
        name=info.get("longName") or info.get("shortName") or ticker,
        sector=info.get("sector"),
        industry=info.get("industry"),
        exchange=info.get("exchange"),
    )


def _build_earnings_data(
    inc: pd.DataFrame | None,
    bal: pd.DataFrame | None,
    cf: pd.DataFrame | None,
    col: Any,
    prev_col: Any | None = None,
) -> EarningsData:
    rev = _get_row_val(inc, col, ["Total Revenue", "Operating Revenue"])
    net_inc = _get_row_val(inc, col, ["Net Income", "Net Income Common Stockholders"])
    gp = _get_row_val(inc, col, ["Gross Profit"])
    op_inc = _get_row_val(inc, col, ["Operating Income", "EBIT"])
    eps = _get_row_val(inc, col, ["Diluted EPS", "Basic EPS"])
    shares = _get_row_val(inc, col, ["Basic Average Shares"])
    shares_dil = _get_row_val(inc, col, ["Diluted Average Shares"])

    cash = _get_row_val(bal, col, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])
    debt = _get_row_val(bal, col, ["Total Debt"])
    assets = _get_row_val(bal, col, ["Total Assets"])
    liab = _get_row_val(bal, col, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
    eq = _get_row_val(bal, col, ["Stockholders Equity", "Common Stock Equity"])

    ocf = _get_row_val(cf, col, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    capex = _get_row_val(cf, col, ["Capital Expenditure"])
    icf = _get_row_val(cf, col, ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"])
    fcf_val = _get_row_val(cf, col, ["Free Cash Flow"])
    fin_cf = _get_row_val(cf, col, ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"])

    rev_chg = None
    net_chg = None
    op_chg = None
    gp_chg = None
    fcf_chg = None
    if prev_col is not None:
        p_rev = _get_row_val(inc, prev_col, ["Total Revenue", "Operating Revenue"])
        p_net = _get_row_val(inc, prev_col, ["Net Income", "Net Income Common Stockholders"])
        p_op = _get_row_val(inc, prev_col, ["Operating Income", "EBIT"])
        p_gp = _get_row_val(inc, prev_col, ["Gross Profit"])
        p_fcf = _get_row_val(cf, prev_col, ["Free Cash Flow"])
        rev_chg = _safe_div((rev or 0) - p_rev, abs(p_rev)) if (rev is not None and p_rev) else None
        net_chg = _safe_div((net_inc or 0) - p_net, abs(p_net)) if (net_inc is not None and p_net) else None
        op_chg = _safe_div((op_inc or 0) - p_op, abs(p_op)) if (op_inc is not None and p_op) else None
        gp_chg = _safe_div((gp or 0) - p_gp, abs(p_gp)) if (gp is not None and p_gp) else None
        fcf_chg = _safe_div((fcf_val or 0) - p_fcf, abs(p_fcf)) if (fcf_val is not None and p_fcf) else None

    return EarningsData(
        revenue=rev,
        earnings_per_share=eps,
        net_income=net_inc,
        gross_profit=gp,
        operating_income=op_inc,
        weighted_average_shares=shares,
        weighted_average_shares_diluted=shares_dil,
        free_cash_flow=fcf_val,
        cash_and_equivalents=cash,
        total_debt=debt,
        total_assets=assets,
        total_liabilities=liab,
        shareholders_equity=eq,
        net_cash_flow_from_operations=ocf,
        capital_expenditure=capex,
        net_cash_flow_from_investing=icf,
        net_cash_flow_from_financing=fin_cf,
        revenue_chg=rev_chg,
        net_income_chg=net_chg,
        operating_income_chg=op_chg,
        gross_profit_chg=gp_chg,
        free_cash_flow_chg=fcf_chg,
    )


def yf_get_earnings(ticker: str) -> Earnings | None:
    recs = yf_get_earnings_history(ticker, limit=1)
    if not recs:
        return None
    r = recs[0]
    return Earnings(
        ticker=ticker,
        report_period=r.report_period,
        fiscal_period=r.fiscal_period,
        currency=r.currency,
        quarterly=r.quarterly,
        annual=r.annual,
    )


def yf_get_earnings_history(ticker: str, limit: int = 12) -> list[EarningsRecord]:
    t = yf.Ticker(ticker)
    q_inc = getattr(t, "quarterly_income_stmt", None)
    q_bal = getattr(t, "quarterly_balance_sheet", None)
    q_cf = getattr(t, "quarterly_cashflow", None)
    a_inc = getattr(t, "income_stmt", None)
    a_bal = getattr(t, "balance_sheet", None)
    a_cf = getattr(t, "cashflow", None)

    records: list[EarningsRecord] = []
    if q_inc is not None and not q_inc.empty:
        cols = [c for c in q_inc.columns if hasattr(c, "strftime")]
        for i, col in enumerate(cols[:limit]):
            rep = col.strftime("%Y-%m-%d")
            prev_col = cols[i + 1] if i + 1 < len(cols) else None
            q_data = _build_earnings_data(q_inc, q_bal, q_cf, col, prev_col)
            a_data = None
            if a_inc is not None and not a_inc.empty and col in a_inc.columns:
                a_cols = list(a_inc.columns)
                a_idx = a_cols.index(col)
                a_prev = a_cols[a_idx + 1] if a_idx + 1 < len(a_cols) else None
                a_data = _build_earnings_data(a_inc, a_bal, a_cf, col, a_prev)
            records.append(
                EarningsRecord(
                    ticker=ticker,
                    report_period=rep,
                    source_type="10-Q",
                    filing_date=rep,
                    filing_datetime=f"{rep}T16:30:00",
                    filing_window="after_close",
                    currency="USD",
                    quarterly=q_data,
                    annual=a_data,
                )
            )
    return records[:limit]
