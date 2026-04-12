"""
Event-Driven Pipeline Monitor — Sprint B #1
============================================

Checks every ticker in the SQLite run archive for three trigger conditions
and automatically re-runs the full advanced pipeline when one fires.
Push alerts (Slack/email) fire at the end of each triggered pipeline run.

Trigger conditions (checked per ticker each run):
  1. Price shock        — single-day move ≥ TRIGGER_PRICE_PCT (default 5%)
  2. Earnings ≤ N days  — pre-emptive refresh before binary event (default 7d)
  3. Insider cluster buy— ≥2 insiders bought via Form 4 in last 30d, fresh filing ≤2d

Watchlist source:
  Primary  — all distinct tickers in src/data/run_archive.db (ticker_signals table)
  Override — --watchlist NVDA,AAPL,MSFT  (merges with archive; seeds new tickers)

State / cooldown:
  src/data/trigger_state.json tracks last-fired date per ticker per trigger.
  Prevents duplicate pipeline runs for the same event.

Scheduling (SGT — local machine time):
  Run at 21:00 SGT daily = 09:00 AM ET pre-market (covers prior session's full data).

  Windows Task Scheduler (run once to register):
    schtasks /create /tn "HedgeFundMonitor" ^
      /tr "poetry run python -m src.triggers.monitor" ^
      /sc DAILY /st 21:00

  During US EST (Nov–second Sunday Mar): 21:00 SGT = 08:00 AM EST — still pre-market.

Usage:
  python -m src.triggers.monitor                       # all archive tickers
  python -m src.triggers.monitor --watchlist NVDA,TSLA # add / override tickers
  python -m src.triggers.monitor --dry-run             # print triggers, no pipeline
  python -m src.triggers.monitor --threshold 3.0       # lower price shock bar
  python -m src.triggers.monitor --earnings-days 14    # wider earnings window
"""

import argparse
import os
import sqlite3
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from dotenv import load_dotenv

# Load env vars (.env first, .env.local overrides)
load_dotenv(override=True)
load_dotenv(".env.local", override=True)

from src.triggers.state import load_state, save_state, already_fired, mark_fired
from src.triggers.detectors import price_shock, earnings_soon, fresh_form4

_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "run_archive.db")

# Default pipeline configuration for triggered runs
_DEFAULT_MODEL    = os.environ.get("TRIGGER_MODEL",    "claude-sonnet-4-6")
_DEFAULT_PROVIDER = os.environ.get("TRIGGER_PROVIDER", "Anthropic")
_DEFAULT_CASH     = float(os.environ.get("TRIGGER_CASH", "100000"))


# ── Watchlist ─────────────────────────────────────────────────────────────────

def _get_archive_tickers() -> list[str]:
    """Return all distinct tickers from the run archive (ticker_signals table)."""
    if not os.path.exists(_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(_DB_PATH)
        cur  = conn.execute(
            "SELECT DISTINCT ticker FROM ticker_signals ORDER BY ticker"
        )
        tickers = [row[0] for row in cur.fetchall()]
        conn.close()
        return tickers
    except Exception as exc:
        print(f"  [monitor] Archive read error: {exc}")
        return []


def _build_watchlist(cli_watchlist: str | None) -> list[str]:
    """Merge archive tickers with any CLI-supplied tickers, deduplicated."""
    seen: set[str] = set()
    result: list[str] = []

    for ticker in _get_archive_tickers():
        t = ticker.upper().strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    if cli_watchlist:
        for ticker in cli_watchlist.split(","):
            t = ticker.upper().strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)

    return result


# ── Portfolio builder ─────────────────────────────────────────────────────────

def _build_portfolio(tickers: list[str], cash: float = _DEFAULT_CASH) -> dict:
    """
    Build a fresh flat portfolio for a triggered pipeline run (Design Choice A).
    Every trigger run is independent — no stale position state.
    """
    return {
        "cash": cash,
        "margin_requirement": 0.0,
        "margin_used": 0.0,
        "positions": {
            ticker: {
                "long":             0,
                "short":            0,
                "long_cost_basis":  0.0,
                "short_cost_basis": 0.0,
                "short_margin_used": 0.0,
            }
            for ticker in tickers
        },
        "realized_gains": {
            ticker: {"long": 0.0, "short": 0.0}
            for ticker in tickers
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AI Hedge Fund — event-driven pipeline monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--watchlist",
        metavar="TICKER,TICKER,...",
        help="Comma-separated tickers to add/override archive watchlist",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=float(os.environ.get("TRIGGER_PRICE_PCT", "5.0")),
        metavar="PCT",
        help="Price shock threshold %% (default 5.0)",
    )
    p.add_argument(
        "--earnings-days",
        type=int,
        default=int(os.environ.get("TRIGGER_EARNINGS_DAYS", "7")),
        metavar="DAYS",
        help="Days ahead to check for upcoming earnings (default 7)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which triggers would fire — do not run pipeline",
    )
    p.add_argument(
        "--model",
        default=_DEFAULT_MODEL,
        help=f"LLM model for triggered runs (default: {_DEFAULT_MODEL})",
    )
    p.add_argument(
        "--provider",
        default=_DEFAULT_PROVIDER,
        help=f"LLM provider for triggered runs (default: {_DEFAULT_PROVIDER})",
    )
    return p.parse_args()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    args     = _parse_args()
    today    = date.today().strftime("%Y-%m-%d")
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S SGT")

    print(f"\n{'='*60}")
    print(f"  AI Hedge Fund — Event Monitor  [{run_time}]")
    print(f"{'='*60}")

    # Build watchlist
    watchlist = _build_watchlist(args.watchlist)
    if not watchlist:
        print(
            "\n  [monitor] No tickers in archive.\n"
            "  Pass --watchlist TICKER1,TICKER2 to seed the monitor.\n"
        )
        return

    print(f"  Watchlist ({len(watchlist)}): {', '.join(watchlist)}")
    print(f"  Price threshold: {args.threshold:.1f}%   Earnings window: {args.earnings_days}d")
    if args.dry_run:
        print("  Mode: DRY RUN — pipeline will NOT be executed\n")
    else:
        print()

    # Load trigger state
    state = load_state()

    # Collect all tickers that need a pipeline run
    # List of (ticker, list_of_(trigger_type, reason, state_key))
    runs_needed: list[tuple[str, list[tuple[str, str, str]]]] = []

    for ticker in watchlist:
        print(f"  Checking {ticker}...")
        ticker_triggers: list[tuple[str, str, str]] = []

        # ── 1. Price shock ────────────────────────────────────────────────
        if not already_fired(state, ticker, "price_shock", today):
            fired, reason, key = price_shock(ticker, args.threshold)
            if fired:
                print(f"    TRIGGER  price_shock  {reason}")
                ticker_triggers.append(("price_shock", reason, key))
            else:
                print(f"    clear    price_shock")
        else:
            print(f"    skip     price_shock  (already fired today)")

        # ── 2. Earnings soon (pre-emptive) ────────────────────────────────
        fired, reason, earnings_date = earnings_soon(ticker, args.earnings_days)
        if fired and earnings_date:
            if not already_fired(state, ticker, "earnings", earnings_date):
                print(f"    TRIGGER  earnings     {reason}")
                ticker_triggers.append(("earnings", reason, earnings_date))
            else:
                print(f"    skip     earnings     (already fired for {earnings_date})")
        else:
            print(f"    clear    earnings")

        # ── 3. Fresh Form 4 cluster buy ───────────────────────────────────
        if not already_fired(state, ticker, "form4", today):
            fired, reason, key = fresh_form4(ticker)
            if fired:
                print(f"    TRIGGER  form4        {reason}")
                ticker_triggers.append(("form4", reason, key))
            else:
                print(f"    clear    form4")
        else:
            print(f"    skip     form4        (already fired today)")

        if ticker_triggers:
            runs_needed.append((ticker, ticker_triggers))

    print()

    # ── Summary ───────────────────────────────────────────────────────────
    if not runs_needed:
        print("  [monitor] No triggers fired — all clear.\n")
        return

    fired_count = sum(len(t) for _, t in runs_needed)
    print(f"  [monitor] {fired_count} trigger(s) across {len(runs_needed)} ticker(s):")
    for ticker, triggers in runs_needed:
        for ttype, reason, _ in triggers:
            print(f"    {ticker}  [{ttype}]  {reason}")
    print()

    if args.dry_run:
        print("  [monitor] Dry run — exiting without running pipeline.\n")
        return

    # ── Run pipeline for each triggered ticker ────────────────────────────
    from src.pipeline import run_advanced_pipeline
    from src.utils.alerts import check_and_send_alerts
    from src.utils.pdf_report import generate_pdf_reports_per_ticker

    end_date   = date.today().strftime("%Y-%m-%d")
    start_date = (date.today() - relativedelta(months=3)).strftime("%Y-%m-%d")

    for ticker, ticker_triggers in runs_needed:
        trigger_labels = ", ".join(t[0] for t in ticker_triggers)
        print(f"  {'─'*56}")
        print(f"  Running pipeline for {ticker}  [{trigger_labels}]")
        print(f"  {'─'*56}")

        portfolio = _build_portfolio([ticker], cash=_DEFAULT_CASH)

        try:
            result = run_advanced_pipeline(
                tickers=[ticker],
                start_date=start_date,
                end_date=end_date,
                portfolio=portfolio,
                model_name=args.model,
                model_provider=args.provider,
                show_reasoning=False,
                enable_post_trade_review=False,
            )

            # Attach trigger context to result for alerts formatting
            result["trigger_source"] = trigger_labels

            # PDF report
            try:
                for pdf_path in generate_pdf_reports_per_ticker(result):
                    print(f"  PDF → {pdf_path}")
            except Exception as exc:
                print(f"  [monitor] PDF skipped: {exc}")

            # Push alerts
            check_and_send_alerts(result)

            # Mark all triggers as fired only after successful run
            for ttype, _, key_date in ticker_triggers:
                mark_fired(state, ticker, ttype, key_date)

        except Exception as exc:
            print(f"  [monitor] Pipeline error for {ticker}: {exc}")
            # Don't mark as fired — allow retry next run

    # Persist updated state
    save_state(state)
    print(f"\n  [monitor] Done. State saved → src/data/trigger_state.json\n")


if __name__ == "__main__":
    main()
