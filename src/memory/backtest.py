"""
src/memory/backtest.py
======================
Regime-tagged backtesting engine for the AI Hedge Fund run archive.

Reads scored outcomes from the SQLite archive and produces performance
metrics grouped by:

  Portfolio-level (from backtest_query):
    - Overall summary
    - Macro regime dimensions (risk appetite, volatility, rate direction,
      dollar trend, recession risk)
    - Sector
    - Action type (BUY / SELL / SHORT / HOLD)
    - Power law tier (category king / compounder / commodity)
    - Value trap verdict

  Agent-level (from agent_backtest_query):
    - Per-agent hit rate, avg return, conviction calibration
    - Conviction tier (1-3 / 4-6 / 7-10) vs hit rate
    - Agent × regime cross-tab (which agent performs best in which regime)

Usage:
    from src.memory.backtest import run_backtest, print_backtest_report
    report = run_backtest()
    print_backtest_report(report)

    # With filters
    report = run_backtest(regime="risk-off", min_conviction=6)
    print_backtest_report(report)

    # Export to CSV
    export_csv(report, "backtest_export.csv")

CLI:
    python -m src.memory.backtest
    python -m src.memory.backtest --regime risk-on
    python -m src.memory.backtest --agent buffett --min-conviction 7
    python -m src.memory.backtest --ticker NVDA --output both --out-path nvda.csv
    python -m src.memory.backtest --regime risk-off --sector Tech --min-scored 3
"""

import argparse
import csv
import os
from dataclasses import dataclass, field
from typing import Optional

from src.memory.run_archive import backtest_query, agent_backtest_query

# ── Data Structures ───────────────────────────────────────────────────────────


@dataclass
class PerformanceSlice:
    """Aggregated performance stats for a cohort of signals."""

    label: str
    total: int = 0
    correct: int = 0
    neutral: int = 0
    incorrect: int = 0
    _sum_pct_change: float = 0.0
    _sum_conviction: float = 0.0
    _n_conviction: int = 0
    _sum_position_size: float = 0.0
    _n_position_size: int = 0
    _sum_dcf_vs_price: float = 0.0
    _n_dcf: int = 0
    buy_count: int = 0
    sell_short_count: int = 0
    hold_count: int = 0

    # ── Derived properties ──────────────────────────────────────────────────

    @property
    def scored(self) -> int:
        return self.correct + self.incorrect

    @property
    def hit_rate(self) -> float:
        return round(self.correct / self.scored, 3) if self.scored else 0.0

    @property
    def win_loss_ratio(self) -> float:
        return round(self.correct / self.incorrect, 2) if self.incorrect else float("inf")

    @property
    def avg_pct_change(self) -> float:
        return round(self._sum_pct_change / self.total, 2) if self.total else 0.0

    @property
    def avg_conviction(self) -> float:
        return round(self._sum_conviction / self._n_conviction, 2) if self._n_conviction else 0.0

    @property
    def avg_position_size(self) -> float:
        return round(self._sum_position_size / self._n_position_size, 2) if self._n_position_size else 0.0

    @property
    def avg_dcf_vs_price(self) -> float:
        return round(self._sum_dcf_vs_price / self._n_dcf, 2) if self._n_dcf else 0.0

    def to_dict(self) -> dict:
        return {
            "label":            self.label,
            "total":            self.total,
            "correct":          self.correct,
            "neutral":          self.neutral,
            "incorrect":        self.incorrect,
            "scored":           self.scored,
            "hit_rate":         self.hit_rate,
            "win_loss":         self.win_loss_ratio,
            "avg_pct_change":   self.avg_pct_change,
            "avg_conviction":   self.avg_conviction,
            "avg_position_size": self.avg_position_size,
            "avg_dcf_vs_price": self.avg_dcf_vs_price,
            "buy_count":        self.buy_count,
            "sell_short_count": self.sell_short_count,
            "hold_count":       self.hold_count,
        }


@dataclass
class AgentSlice:
    """Per-agent performance across all signals."""

    agent_key: str
    total: int = 0
    correct: int = 0
    neutral: int = 0
    incorrect: int = 0
    _sum_pct_change: float = 0.0
    _sum_conviction: float = 0.0
    _n_conviction: int = 0
    # Regime breakdown: {regime_value: [outcome, ...]}
    by_regime: dict = field(default_factory=dict)

    @property
    def scored(self) -> int:
        return self.correct + self.incorrect

    @property
    def hit_rate(self) -> float:
        return round(self.correct / self.scored, 3) if self.scored else 0.0

    @property
    def avg_pct_change(self) -> float:
        return round(self._sum_pct_change / self.total, 2) if self.total else 0.0

    @property
    def avg_conviction(self) -> float:
        return round(self._sum_conviction / self._n_conviction, 2) if self._n_conviction else 0.0

    def to_dict(self) -> dict:
        return {
            "agent_key":      self.agent_key,
            "total":          self.total,
            "correct":        self.correct,
            "neutral":        self.neutral,
            "incorrect":      self.incorrect,
            "scored":         self.scored,
            "hit_rate":       self.hit_rate,
            "avg_pct_change": self.avg_pct_change,
            "avg_conviction": self.avg_conviction,
        }


@dataclass
class BacktestReport:
    """Full backtesting results — portfolio-level and agent-level."""

    # Applied filters
    filters: dict = field(default_factory=dict)

    # Portfolio-level slices
    overall: PerformanceSlice = field(default_factory=lambda: PerformanceSlice("Overall"))
    by_risk_appetite:  dict[str, PerformanceSlice] = field(default_factory=dict)
    by_volatility:     dict[str, PerformanceSlice] = field(default_factory=dict)
    by_rate_direction: dict[str, PerformanceSlice] = field(default_factory=dict)
    by_dollar:         dict[str, PerformanceSlice] = field(default_factory=dict)
    by_recession_risk: dict[str, PerformanceSlice] = field(default_factory=dict)
    by_sector:         dict[str, PerformanceSlice] = field(default_factory=dict)
    by_action:         dict[str, PerformanceSlice] = field(default_factory=dict)
    by_power_law_tier: dict[str, PerformanceSlice] = field(default_factory=dict)
    by_value_trap:     dict[str, PerformanceSlice] = field(default_factory=dict)

    # Agent-level slices
    by_agent:           dict[str, AgentSlice] = field(default_factory=dict)
    by_conviction_tier: dict[str, PerformanceSlice] = field(default_factory=dict)

    # Signal log (full rows for CSV export)
    signal_log:       list[dict] = field(default_factory=list)
    agent_signal_log: list[dict] = field(default_factory=list)

    # Metadata
    total_runs:    int = 0
    total_tickers: int = 0
    date_range: tuple[str, str] = ("", "")


# ── Internal helpers ───────────────────────────────────────────────────────────

def _power_law_tier(score) -> str:
    if score is None:
        return "unknown"
    s = float(score)
    if s >= 8:
        return "8-10 category_king"
    if s >= 5:
        return "5-7 compounder"
    return "<5  commodity"


def _conviction_tier(conviction) -> str:
    if conviction is None:
        return "unknown"
    c = int(conviction)
    if c >= 7:
        return "7-10 high"
    if c >= 4:
        return "4-6  medium"
    return "1-3  low"


def _accumulate_slice(s: PerformanceSlice, row: dict, conviction_key: str = "conviction") -> None:
    """Accumulate one signal row into a PerformanceSlice in place."""
    s.total += 1
    outcome = row.get("outcome", "NEUTRAL")
    if outcome == "CORRECT":
        s.correct += 1
    elif outcome == "INCORRECT":
        s.incorrect += 1
    else:
        s.neutral += 1

    pct = row.get("pct_change")
    if pct is not None:
        s._sum_pct_change += float(pct)

    conv = row.get(conviction_key)
    if conv is not None:
        s._sum_conviction += float(conv)
        s._n_conviction += 1

    pos = row.get("position_size_pct")
    if pos is not None:
        s._sum_position_size += float(pos)
        s._n_position_size += 1

    dcf = row.get("dcf_iv_vs_price_pct")
    if dcf is not None:
        s._sum_dcf_vs_price += float(dcf)
        s._n_dcf += 1

    action = (row.get("final_action") or "HOLD").upper()
    if action in ("BUY", "COVER"):
        s.buy_count += 1
    elif action in ("SELL", "SHORT"):
        s.sell_short_count += 1
    else:
        s.hold_count += 1


def _get_or_create_slice(group: dict, key, label_prefix: str = "") -> PerformanceSlice:
    k = str(key) if key is not None else "unknown"
    if k not in group:
        group[k] = PerformanceSlice(label=f"{label_prefix}{k}")
    return group[k]


def _drop_thin_slices(group: dict, min_scored: int) -> None:
    """Remove groups with fewer than min_scored scored rows."""
    for k in list(group.keys()):
        if group[k].scored < min_scored:
            del group[k]


# ── Public API ────────────────────────────────────────────────────────────────

def run_backtest(
    ticker:         Optional[str] = None,
    regime:         Optional[str] = None,
    sector:         Optional[str] = None,
    agent:          Optional[str] = None,
    min_conviction: Optional[int] = None,
    min_scored:     int = 1,
) -> BacktestReport:
    """
    Run a full regime-tagged backtest over the archive.

    Parameters
    ----------
    ticker         : filter to a specific ticker symbol
    regime         : filter by risk_appetite ("risk-on" | "risk-off")
    sector         : filter by sector ("Tech" | "Energy" | ...)
    agent          : filter to runs where this agent participated
    min_conviction : only include agent signals with at least this conviction
    min_scored     : minimum scored rows required per slice to include it

    Returns
    -------
    BacktestReport with all slices populated
    """
    report = BacktestReport(filters={
        "ticker":         ticker,
        "regime":         regime,
        "sector":         sector,
        "agent":          agent,
        "min_conviction": min_conviction,
    })

    # ── Portfolio-level signals ─────────────────────────────────────────────
    rows = backtest_query(
        ticker=ticker, regime=regime, sector=sector,
        agent=agent, min_conviction=min_conviction,
    )

    run_ids: set = set()
    ticker_keys: set = set()
    dates: list = []

    for row in rows:
        run_ids.add(row.get("run_id"))
        ticker_keys.add((row.get("run_id"), row.get("ticker")))
        if row.get("run_at"):
            dates.append(row["run_at"])

        # Overall
        _accumulate_slice(report.overall, row)

        # Regime dimensions
        _accumulate_slice(
            _get_or_create_slice(report.by_risk_appetite, row.get("regime_risk_appetite")),
            row,
        )
        _accumulate_slice(
            _get_or_create_slice(report.by_volatility, row.get("regime_volatility")),
            row,
        )
        _accumulate_slice(
            _get_or_create_slice(report.by_rate_direction, row.get("regime_rate_direction")),
            row,
        )
        _accumulate_slice(
            _get_or_create_slice(report.by_dollar, row.get("regime_dollar")),
            row,
        )
        _accumulate_slice(
            _get_or_create_slice(report.by_recession_risk, row.get("regime_recession_risk")),
            row,
        )

        # Sector
        _accumulate_slice(_get_or_create_slice(report.by_sector, row.get("sector")), row)

        # Action type
        action = (row.get("final_action") or "HOLD").upper()
        _accumulate_slice(_get_or_create_slice(report.by_action, action), row)

        # Power law tier
        tier = _power_law_tier(row.get("power_law_score"))
        _accumulate_slice(_get_or_create_slice(report.by_power_law_tier, tier), row)

        # Value trap
        trap = row.get("value_trap_verdict") or "unknown"
        _accumulate_slice(_get_or_create_slice(report.by_value_trap, trap), row)

        report.signal_log.append(row)

    report.total_runs    = len(run_ids)
    report.total_tickers = len(ticker_keys)
    if dates:
        report.date_range = (min(dates)[:10], max(dates)[:10])

    # ── Agent-level signals ─────────────────────────────────────────────────
    ag_rows = agent_backtest_query(
        ticker=ticker, regime=regime, sector=sector,
        agent=agent, min_conviction=min_conviction,
    )

    for row in ag_rows:
        agent_key = row.get("agent_key") or "unknown"

        # Per-agent AgentSlice
        if agent_key not in report.by_agent:
            report.by_agent[agent_key] = AgentSlice(agent_key=agent_key)
        a = report.by_agent[agent_key]
        a.total += 1
        outcome = row.get("outcome", "NEUTRAL")
        if outcome == "CORRECT":
            a.correct += 1
        elif outcome == "INCORRECT":
            a.incorrect += 1
        else:
            a.neutral += 1
        pct = row.get("pct_change")
        if pct is not None:
            a._sum_pct_change += float(pct)
        conv = row.get("conviction")
        if conv is not None:
            a._sum_conviction += float(conv)
            a._n_conviction += 1

        # Per-conviction-tier PerformanceSlice
        ctier = _conviction_tier(row.get("conviction"))
        agent_row_for_slice = dict(row)
        agent_row_for_slice["final_action"] = row.get("pm_action")  # use PM action for buy/sell counts
        _accumulate_slice(
            _get_or_create_slice(report.by_conviction_tier, ctier),
            agent_row_for_slice,
            conviction_key="conviction",
        )

        report.agent_signal_log.append(row)

    # ── Thin slice pruning ──────────────────────────────────────────────────
    for group in [
        report.by_risk_appetite, report.by_volatility, report.by_rate_direction,
        report.by_dollar, report.by_recession_risk, report.by_sector,
        report.by_action, report.by_power_law_tier, report.by_value_trap,
        report.by_conviction_tier,
    ]:
        _drop_thin_slices(group, min_scored)

    # Drop agent slices with insufficient scored rows
    for k in list(report.by_agent.keys()):
        if report.by_agent[k].scored < min_scored:
            del report.by_agent[k]

    return report


# ── Printing ──────────────────────────────────────────────────────────────────

def print_backtest_report(report: BacktestReport) -> None:
    """Pretty-print the full backtest report to stdout."""
    W = 68
    SEP  = "=" * W
    DASH = "-" * W

    def _bar(rate: float, width: int = 18) -> str:
        filled = round(rate * width)
        return "[" + "#" * filled + "." * (width - filled) + f"] {rate:>5.1%}"

    def _header(title: str) -> None:
        print(f"\n{DASH}")
        print(f"  {title}")
        print(f"  {'Group':<26}  {'Scored':>6}  {'Hit Rate':^26}  {'AvgRet':>7}  {'W/L':>6}")
        print(f"  {'-'*64}")

    def _row(label: str, s: PerformanceSlice) -> None:
        wl = f"{s.correct}/{s.incorrect}"
        print(
            f"  {label:<26}  {s.scored:>6}  {_bar(s.hit_rate)}  "
            f"{s.avg_pct_change:>+7.1f}%  {wl:>6}"
        )

    def _agent_row(a: AgentSlice) -> None:
        wl = f"{a.correct}/{a.incorrect}"
        print(
            f"  {a.agent_key:<18}  {a.scored:>6}  {_bar(a.hit_rate)}  "
            f"{a.avg_pct_change:>+7.1f}%  {wl:>6}  conv={a.avg_conviction:.1f}"
        )

    # ── Title block ────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print(f"  AI HEDGE FUND  -  REGIME-TAGGED BACKTEST REPORT")
    active = {k: v for k, v in report.filters.items() if v is not None}
    if active:
        print(f"  Filters: {active}")
    print(SEP)

    o = report.overall
    print(f"  Runs analysed    : {report.total_runs}")
    print(f"  Ticker-signals   : {report.total_tickers}")
    print(f"  Date range       : {report.date_range[0]}  to  {report.date_range[1]}")
    print(f"  Scored signals   : {o.scored}  (C={o.correct}  N={o.neutral}  I={o.incorrect})")
    print(f"  Overall hit rate : {_bar(o.hit_rate)}")
    print(f"  Avg return       : {o.avg_pct_change:+.2f}%")
    print(f"  Avg position sz  : {o.avg_position_size:.1f}%")
    if o._n_dcf:
        print(f"  Avg DCF margin   : {o.avg_dcf_vs_price:+.1f}%")

    # ── Regime sections ────────────────────────────────────────────────────
    def _section(title: str, group: dict) -> None:
        if not group:
            return
        _header(title)
        for k, s in sorted(group.items(), key=lambda x: -x[1].hit_rate):
            _row(k, s)

    _section("RISK APPETITE",    report.by_risk_appetite)
    _section("VOLATILITY REGIME", report.by_volatility)
    _section("RATE DIRECTION",   report.by_rate_direction)
    _section("DOLLAR TREND",     report.by_dollar)
    _section("RECESSION RISK",   report.by_recession_risk)
    _section("SECTOR",           report.by_sector)
    _section("ACTION TYPE",      report.by_action)
    _section("POWER LAW TIER",   report.by_power_law_tier)
    _section("VALUE TRAP VERDICT", report.by_value_trap)

    # ── Agent section ──────────────────────────────────────────────────────
    if report.by_agent:
        print(f"\n{DASH}")
        print(f"  AGENT PERFORMANCE")
        print(f"  {'Agent':<18}  {'Scored':>6}  {'Hit Rate':^26}  {'AvgRet':>7}  {'W/L':>6}  Conv")
        print(f"  {'-'*64}")
        for k, a in sorted(report.by_agent.items(), key=lambda x: -x[1].hit_rate):
            _agent_row(a)

    # ── Conviction calibration ─────────────────────────────────────────────
    if report.by_conviction_tier:
        _section("CONVICTION CALIBRATION (agent-level)", report.by_conviction_tier)

    print(f"\n{SEP}\n")


# ── CSV Export ────────────────────────────────────────────────────────────────

def export_csv(report: BacktestReport, path: str) -> None:
    """
    Export full portfolio-level signal log to CSV.
    Every row is one scored ticker-signal with all regime tags attached.
    """
    if not report.signal_log:
        print("  [backtest] No scored signals to export.")
        return

    fieldnames = [
        "run_at", "analysis_date", "ticker", "sector",
        "regime_risk_appetite", "regime_volatility", "regime_rate_direction",
        "regime_dollar", "regime_recession_risk", "research_tier",
        "final_action", "position_size_pct", "price_at_run",
        "price_target", "stop_loss", "entry_range_low", "entry_range_high",
        "time_horizon", "pm_rationale",
        "dcf_base_iv", "dcf_wacc", "dcf_iv_vs_price_pct",
        "debate_triggered", "debate_adjudicated_signal",
        "power_law_score", "value_trap_verdict", "ev_upside_pct",
        "si_signal", "si_short_float_pct", "si_squeeze_risk",
        "insider_signal", "revision_direction", "news_signal",
        "eq_quality_verdict", "eq_quality_score",
        "review_date", "price_at_review", "pct_change", "outcome",
    ]

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report.signal_log)

    print(f"  [backtest] Portfolio signals: {len(report.signal_log)} rows -> {path}")


def export_agent_csv(report: BacktestReport, path: str) -> None:
    """
    Export per-agent signal log to CSV.
    Every row is one scored agent-signal with regime tags and PM outcome.
    """
    if not report.agent_signal_log:
        print("  [backtest] No scored agent signals to export.")
        return

    fieldnames = [
        "run_at", "analysis_date", "ticker", "sector", "agent_key",
        "agent_signal", "conviction", "agent_price_target", "agent_time_horizon",
        "thesis_summary", "outcome",
        "regime_risk_appetite", "regime_volatility", "regime_rate_direction",
        "regime_dollar", "regime_recession_risk", "research_tier",
        "pm_action", "price_at_run", "pct_change",
        "power_law_score", "value_trap_verdict",
        "dcf_iv_vs_price_pct", "ev_upside_pct",
    ]

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report.agent_signal_log)

    print(f"  [backtest] Agent signals: {len(report.agent_signal_log)} rows -> {path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Regime-tagged backtesting over the AI Hedge Fund run archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.memory.backtest
  python -m src.memory.backtest --ticker NVDA
  python -m src.memory.backtest --regime risk-off --sector Tech
  python -m src.memory.backtest --agent buffett --min-conviction 7
  python -m src.memory.backtest --output both --out-path results/backtest.csv
  python -m src.memory.backtest --min-scored 3
        """,
    )
    parser.add_argument("--ticker",         help="Filter to a specific ticker (e.g. NVDA)")
    parser.add_argument("--regime",         help="Filter by risk appetite: risk-on | risk-off")
    parser.add_argument("--sector",         help="Filter by sector: Tech | Energy | Financials | ...")
    parser.add_argument("--agent",          help="Filter to a specific investor agent (e.g. buffett)")
    parser.add_argument("--min-conviction", type=int, metavar="N",
                        help="Only include agent signals with conviction >= N (1-10)")
    parser.add_argument("--min-scored",     type=int, default=1, metavar="N",
                        help="Min scored signals per slice to display it (default: 1)")
    parser.add_argument(
        "--output", choices=["report", "csv", "both"], default="report",
        help="Output format: report (default), csv, or both",
    )
    parser.add_argument("--out-path",  default="backtest_export.csv",
                        help="CSV output path for portfolio signals (default: backtest_export.csv)")
    parser.add_argument("--agent-out", default=None,
                        help="CSV output path for agent-level signals (optional; omit to skip)")
    args = parser.parse_args()

    report = run_backtest(
        ticker=args.ticker,
        regime=args.regime,
        sector=args.sector,
        agent=args.agent,
        min_conviction=args.min_conviction,
        min_scored=args.min_scored,
    )

    if args.output in ("report", "both"):
        print_backtest_report(report)

    if args.output in ("csv", "both"):
        export_csv(report, args.out_path)
        if args.agent_out:
            export_agent_csv(report, args.agent_out)


if __name__ == "__main__":
    _cli()
