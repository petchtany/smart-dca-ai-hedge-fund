"""Financial Datasets API client (with automatic Yahoo Finance free fallback)."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests

from hedge_fund.data.models import (
    CompanyFacts,
    CompanyNews,
    Earnings,
    EarningsRecord,
    FinancialMetrics,
    InsiderTrade,
    Price,
    PriceSnapshot,
)
from hedge_fund.data.screener import OrderBy, SCREEN_LIMIT, ScreenerFields, ScreenFilter, ScreenRow
from hedge_fund.data.wallet import payment_session, WALLET_KEY_ENV
from hedge_fund.data.yf_provider import (
    yf_get_company_facts,
    yf_get_earnings,
    yf_get_earnings_history,
    yf_get_financial_metrics,
    yf_get_insider_trades,
    yf_get_news,
    yf_get_prices,
    yf_get_snapshot,
)

logger = logging.getLogger(__name__)

NEWS_PAGE = 10  # the most /news/ returns per request
API_KEY_ENV = "FINANCIAL_DATASETS_API_KEY"
# x402 answers only this far back; older dates get a 403 with the window start.
X402_HISTORY_DAYS = 365

_DUMMY_KEYS = {"", "your-financial-datasets-api-key", "yfinance", "free", "0x-your-wallet-private-key"}


def payment_error(resp: requests.Response) -> str:
    """Why a paid retry still came back 402."""
    header = resp.headers.get("PAYMENT-REQUIRED", "")
    try:
        terms = json.loads(base64.b64decode(header + "=" * (-len(header) % 4)))
    except (ValueError, UnicodeDecodeError):
        return "no payment terms in the response"
    return str(terms.get("error") or "no reason given")[:300]


def has_data_credential() -> bool:
    """Always True since Yahoo Finance (yfinance) provides free market data."""
    return True


@dataclass
class CallMeter:
    """How many requests actually went to the API, by path."""

    calls: int = 0
    by_path: Counter[str] = field(default_factory=Counter)

    def record(self, url: str) -> None:
        self.calls += 1
        self.by_path[urlsplit(url).path] += 1


class FDClientError(Exception):
    """An API request failed for infrastructure reasons."""

    def __init__(self, message: str, *, status_code: int | None = None, path: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.path = path


class FDClient:
    """Financial Datasets API client with automatic Yahoo Finance fallback."""

    BASE_URL = "https://api.financialdatasets.ai"
    _RETRY_DELAYS = (5, 15, 30)

    def __init__(
        self,
        api_key: str | None = None,
        wallet_key: str | None = None,
        timeout: float = 30.0,
        meter: CallMeter | None = None,
    ) -> None:
        raw_api = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        raw_wallet = wallet_key if wallet_key is not None else os.environ.get(WALLET_KEY_ENV, "")
        self._api_key = "" if raw_api.strip() in _DUMMY_KEYS else raw_api.strip()
        self._wallet_key = "" if raw_wallet.strip() in _DUMMY_KEYS else raw_wallet.strip()
        self._timeout = timeout
        self.meter = meter if meter is not None else CallMeter()
        if self.pays_per_call():
            try:
                self._session = payment_session(self._wallet_key)
            except ImportError as exc:
                raise FDClientError(str(exc)) from exc
        else:
            self._session = requests.Session()
            if self._api_key:
                self._session.headers["X-API-Key"] = self._api_key

    def _use_yfinance(self) -> bool:
        return not self._api_key and not self._wallet_key

    def pays_per_call(self) -> bool:
        """Whether requests go out keyless and pay with x402."""
        return not self._api_key and bool(self._wallet_key)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> FDClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()

    # ------------------------------------------------------------------
    # Prices
    # ------------------------------------------------------------------

    def get_prices(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        interval: str = "day",
        interval_multiplier: int = 1,
    ) -> list[Price]:
        """Fetch OHLC price bars."""
        if self._use_yfinance():
            return yf_get_prices(ticker, start_date, end_date, interval, interval_multiplier)
        try:
            data = self._get("/prices/", {
                "ticker": ticker,
                "interval": interval,
                "interval_multiplier": interval_multiplier,
                "start_date": start_date,
                "end_date": end_date,
            }, response_key="prices")
            return [Price(**row) for row in data] if data else []
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_prices(ticker, start_date, end_date, interval, interval_multiplier)
            raise

    def get_snapshot(self, ticker: str) -> PriceSnapshot | None:
        """Fetch the real-time quote. None if the ticker is unknown."""
        if self._use_yfinance():
            return yf_get_snapshot(ticker)
        try:
            resp = self._request("GET", "/prices/snapshot/", params={"ticker": ticker})
            if resp is None:
                return None
            row = resp.json().get("snapshot")
            return PriceSnapshot(**row) if row else None
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_snapshot(ticker)
            raise

    # ------------------------------------------------------------------
    # Financial Metrics
    # ------------------------------------------------------------------

    def get_financial_metrics(
        self,
        ticker: str,
        end_date: str,
        period: str = "ttm",
        limit: int = 10,
    ) -> list[FinancialMetrics]:
        """Fetch financial metrics that were PUBLIC as of *end_date*."""
        if self._use_yfinance():
            return yf_get_financial_metrics(ticker, end_date, period, limit)
        try:
            data = self._get("/financial-metrics/", {
                "ticker": ticker,
                "filing_date_lte": end_date,
                "period": period,
                "limit": limit,
            }, response_key="financial_metrics")
            return [FinancialMetrics(**row) for row in data] if data else []
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_financial_metrics(ticker, end_date, period, limit)
            raise

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def get_news(
        self,
        ticker: str,
        end_date: str,
        start_date: str | None = None,
        limit: int = 1000,
    ) -> list[CompanyNews]:
        """Fetch company news: the newest items first, at most *limit*."""
        if self._use_yfinance():
            return yf_get_news(ticker, end_date, start_date, limit)
        try:
            params: dict = {"ticker": ticker, "end_date": end_date, "limit": min(limit, NEWS_PAGE)}
            if start_date is not None:
                params["start_date"] = start_date
            data = self._get("/news/", params, response_key="news")
            return [CompanyNews(**row) for row in data] if data else []
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_news(ticker, end_date, start_date, limit)
            raise

    # ------------------------------------------------------------------
    # Insider Trades
    # ------------------------------------------------------------------

    def get_insider_trades(
        self,
        ticker: str,
        end_date: str,
        start_date: str | None = None,
        limit: int = 1000,
    ) -> list[InsiderTrade]:
        """Fetch insider trades."""
        if self._use_yfinance():
            return yf_get_insider_trades(ticker, end_date, start_date, limit)
        try:
            params: dict = {"ticker": ticker, "filing_date_lte": end_date, "limit": limit}
            if start_date is not None:
                params["filing_date_gte"] = start_date
            data = self._get("/insider-trades/", params, response_key="insider_trades")
            return [InsiderTrade(**row) for row in data] if data else []
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_insider_trades(ticker, end_date, start_date, limit)
            raise

    # ------------------------------------------------------------------
    # Company Facts
    # ------------------------------------------------------------------

    def get_company_facts(self, ticker: str) -> CompanyFacts | None:
        """Fetch company metadata (single record)."""
        if self._use_yfinance():
            return yf_get_company_facts(ticker)
        try:
            resp = self._request("GET", "/company/facts/", params={"ticker": ticker})
            if resp is None:
                return None
            facts_data = resp.json().get("company_facts")
            return CompanyFacts(**facts_data) if facts_data else None
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_company_facts(ticker)
            raise

    # ------------------------------------------------------------------
    # Earnings
    # ------------------------------------------------------------------

    def get_earnings(self, ticker: str) -> Earnings | None:
        """Fetch latest earnings for a single ticker."""
        if self._use_yfinance():
            return yf_get_earnings(ticker)
        try:
            data = self._get("/earnings/", {"ticker": ticker, "limit": 1}, response_key="earnings")
            if not data:
                return None
            row = data[0] if isinstance(data, list) else data
            return Earnings(**row)
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_earnings(ticker)
            raise

    def get_earnings_history(
        self,
        ticker: str,
        limit: int = 12,
    ) -> list[EarningsRecord]:
        """Fetch historical earnings filings as a flat list."""
        if self._use_yfinance():
            return yf_get_earnings_history(ticker, limit)
        try:
            data = self._get("/earnings/", {
                "ticker": ticker,
                "limit": limit,
            }, response_key="earnings")
            return [EarningsRecord(**row) for row in data] if data else []
        except FDClientError as exc:
            if exc.status_code in (401, 402, 403):
                return yf_get_earnings_history(ticker, limit)
            raise

    # ------------------------------------------------------------------
    # Screener
    # ------------------------------------------------------------------

    def screen(
        self,
        as_of: str,
        filters: list[ScreenFilter],
        *,
        order_by: OrderBy | None = None,
        limit: int = SCREEN_LIMIT,
    ) -> list[ScreenRow]:
        if not as_of:
            raise ValueError("screen needs an as_of date: a screen without one reads today's numbers")
        if not filters:
            raise ValueError("screen needs at least one filter")
        if self._use_yfinance():
            return []
        path = "/financials/search/screener"
        body: dict = {"as_of": as_of, "filters": [item.model_dump() for item in filters], "limit": limit}
        if order_by is not None:
            body["order_by"] = order_by.model_dump()
        resp = self._request("POST", path, json=body)
        if resp is None:
            raise FDClientError(f"POST {path} returned 404", status_code=404, path=path)
        return [ScreenRow.from_api(row) for row in resp.json().get("results") or []]

    def screener_fields(self) -> ScreenerFields:
        if self._use_yfinance():
            return ScreenerFields(fields=[], operators=[], sectors=[], industries=[])
        path = "/financials/search/screener/filters"
        resp = self._request("GET", path)
        if resp is None:
            raise FDClientError(f"GET {path} returned 404", status_code=404, path=path)
        return ScreenerFields.from_api(resp.json())

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_market_cap(self, ticker: str, end_date: str) -> float | None:
        """Return market cap from company facts or financial metrics."""
        facts = self.get_company_facts(ticker)
        if facts is not None and getattr(facts, "market_cap", None) is not None:
            return getattr(facts, "market_cap")
        metrics = self.get_financial_metrics(ticker, end_date, limit=1)
        if metrics and metrics[0].market_cap is not None:
            return metrics[0].market_cap
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get(
        self,
        path: str,
        params: dict,
        response_key: str,
    ) -> list[dict] | None:
        resp = self._request("GET", path, params=params)
        if resp is None:
            return None
        body = resp.json()
        rows = body.get(response_key)
        next_page_url = body.get("next_page_url")
        while next_page_url and isinstance(rows, list):
            resp = self._request("GET", next_page_url)
            if resp is None:
                break
            body = resp.json()
            rows.extend(body.get(response_key) or [])
            next_page_url = body.get("next_page_url")
        return rows

    def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> requests.Response | None:
        url = path if path.startswith("http") else self.BASE_URL + path
        for attempt, delay in enumerate((*self._RETRY_DELAYS, None)):
            self.meter.record(url)
            try:
                resp = self._session.request(
                    method, url, timeout=self._timeout, **kwargs,
                )
            except requests.RequestException as exc:
                raise FDClientError(
                    f"{method} {path} failed: {exc}", path=path,
                ) from exc

            if resp.status_code in (429, 503) and delay is not None:
                logger.info(
                    "Got %d, retrying in %ds (attempt %d/%d)",
                    resp.status_code, delay, attempt + 1, len(self._RETRY_DELAYS),
                )
                time.sleep(delay)
                continue

            if resp.status_code == 404:
                return None

            if resp.status_code >= 400:
                message = f"{method} {path} returned {resp.status_code}: {resp.text[:200]}"
                if resp.status_code == 402 and self.pays_per_call():
                    message += f" (x402 payment from the wallet failed: {payment_error(resp)})"
                if resp.status_code == 403 and self.pays_per_call():
                    message += (
                        f" (x402 serves the last {X402_HISTORY_DAYS} days; move the start "
                        f"date later or set {API_KEY_ENV} for older data)"
                    )
                raise FDClientError(message, status_code=resp.status_code, path=path)

            return resp

        raise FDClientError(
            f"{method} {path} returned {resp.status_code} after {len(self._RETRY_DELAYS)} retries",
            status_code=resp.status_code, path=path,
        )
