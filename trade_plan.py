"""Smart DCA & Buy-the-Dip AI Chief Investment Officer (CIO) Command Center (v4.0)
Modular entrypoint delegating to `smart_dca/`:
- SQLite persistent storage (watchlist + split 24h fundamentals / 30m price / smart-TTL CIO caches)
- Single batched OHLCV download via yfinance + live USD/THB FX rate & multi-currency support
- AI CIO Thai analysis with 7-day TTL & >=8% price-drift auto-refresh
- Historical Smart DCA vs Fixed DCA Backtester (`--backtest [1y|2y|3y|5y]` & `/api/backtest`)
- JSON API (`/api/portfolio`, `/api/backtest`, `/api/health`) + Interactive Dashboard v4.0
"""

from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import time
import webbrowser

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from smart_dca.backtest import run_portfolio_backtest
from smart_dca.market_data import load_latest_fund_signals
from smart_dca.report import generate_html_report
from smart_dca.server import (
    build_portfolio_snapshot,
    ensure_background_server,
    modify_portfolio_tickers,
    serve_api,
    sync_report_quietly,
)


def enable_windows_utf8() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _print_backtest_table(console: Console, bt: dict) -> None:
    rows = bt.get("tickers") or []
    summary = bt.get("summary") or {}
    if not rows:
        console.print("[yellow]No backtest results available.[/]")
        return

    table = Table(title=f"Smart DCA vs Fixed DCA Backtest ({bt.get('period', '3y')})")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Period")
    table.add_column("Fixed Return", justify="right")
    table.add_column("Smart Return", justify="right", style="green")
    table.add_column("Alpha", justify="right")
    table.add_column("Max DD (Fix->Smart)", justify="right")
    table.add_column("Dip Buys", justify="right")

    for r in rows:
        alpha_style = "bold green" if r["alpha_pct"] >= 0 else "bold red"
        table.add_row(
            r["ticker"],
            f"{r['start_date']}..{r['end_date']}",
            f"{r['fixed_return_pct']:+.2f}%",
            f"{r['smart_return_pct']:+.2f}%",
            f"[{alpha_style}]{r['alpha_pct']:+.2f}%[/]",
            f"{r['fixed_max_dd_pct']:.1f}% -> {r['smart_max_dd_pct']:.1f}%",
            str(r["dip_deployments"]),
        )
    console.print(table)
    console.print(
        Panel(
            f"Portfolio Fixed DCA: {summary.get('fixed_return_pct', 0):+.2f}%  |  "
            f"Smart DCA: {summary.get('smart_return_pct', 0):+.2f}%  |  "
            f"Alpha: {summary.get('portfolio_alpha_pct', 0):+.2f}%  |  "
            f"Avg MaxDD: {summary.get('avg_fixed_max_dd_pct', 0):.1f}% -> {summary.get('avg_smart_max_dd_pct', 0):.1f}%  |  "
            f"Smart Wins: {summary.get('smart_wins', 0)}/{summary.get('count', 0)}",
            style="bold cyan",
        )
    )


def main() -> None:
    enable_windows_utf8()
    console = Console(legacy_windows=False)

    args = sys.argv[1:]
    if "--add" in args:
        idx = args.index("--add")
        to_add = [a for a in args[idx + 1:] if not a.startswith("-")]
        console.print(f"[cyan]Adding tickers and running AI CIO analysis: {', '.join(to_add)}...[/]")
        ok, msg = modify_portfolio_tickers(add=to_add)
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return

    if "--remove" in args:
        idx = args.index("--remove")
        to_rem = [a for a in args[idx + 1:] if not a.startswith("-")]
        ok, msg = modify_portfolio_tickers(remove=to_rem)
        console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return

    if "--serve-only" in args:
        serve_api(8765)
        return

    if "--backtest" in args:
        idx = args.index("--backtest")
        period = "3y"
        if idx + 1 < len(args) and args[idx + 1] in ("1y", "2y", "3y", "5y"):
            period = args[idx + 1]
        _, tickers, _, _ = load_latest_fund_signals()
        console.print(f"[cyan]Running Smart DCA vs Fixed DCA backtest ({period}) for {len(tickers)} tickers...[/]")
        t0 = time.time()
        bt = run_portfolio_backtest(tickers, period=period)
        console.print(f"[green]Backtest finished in {time.time() - t0:.2f}s[/]")
        _print_backtest_table(console, bt)
        return

    cli_tickers = [arg.upper().strip() for arg in args if not arg.startswith("-")]
    no_open = "--no-open" in args
    serve_flag = "--serve" in args
    force_refresh = "--refresh" in args
    force_cio = "--force-cio" in args

    t0 = time.time()
    console.print("[cyan]Fetching batch market data, SQLite split-cache & Smart-TTL AI CIO analysis...[/]")
    snap = build_portfolio_snapshot(
        force_refresh=force_refresh,
        force_cio=force_cio,
        cli_tickers=cli_tickers or None,
    )
    results = snap["results"]
    elapsed = time.time() - t0
    console.print(
        f"[green]Completed {len(results)} stocks in {elapsed:.2f}s! (Live FX: {snap['fx_usd_thb']:.2f} THB/USD)[/]"
    )

    if results:
        html_path = generate_html_report(
            results,
            snap["ai_weights"],
            snap["signals_by_ticker"],
            snap["fund_name"],
            fx_usd_thb=snap["fx_usd_thb"],
            macro=snap.get("macro"),
        )
        ensure_background_server(Path(__file__), 8765)
        console.print(
            Panel(
                f"AI CIO Smart DCA Dashboard v4.0: http://127.0.0.1:8765 (and {html_path})",
                style="bold green",
            )
        )
        if not no_open:
            try:
                webbrowser.open("http://127.0.0.1:8765")
            except Exception:
                pass
        if serve_flag:
            serve_api(8765)


if __name__ == "__main__":
    main()
