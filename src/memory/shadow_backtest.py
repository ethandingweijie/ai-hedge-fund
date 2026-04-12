"""
src/memory/shadow_backtest.py
==============================
Rules-Based Shadow Backtest — Quarterly

Applies deterministic agent rules to archived scored signals and compares
the rules-based hit rate against the actual LLM-generated hit rate from the
archive.  A significant divergence (Sharpe deviation > threshold) indicates
the LLM agents are adding value over naive rules, or alternatively that a
regime shift has made the rules stale.

Rules use financial proxies already stored in run_archive.db:
  dcf_iv_vs_price_pct   → margin-of-safety proxy
  ev_upside_pct         → expected-value upside proxy
  power_law_score       → moat/category-leadership proxy
  value_trap_verdict    → forensic risk proxy

Agent rule definitions:
  graham       : BUY if dcf_iv_vs_price_pct >= 33
  buffett      : BUY if dcf_iv_vs_price_pct >= 25 AND power_law_score >= 7
  damodaran    : BUY if ev_upside_pct >= 20
  pabrai       : BUY if ev_upside_pct >= 30 AND value_trap != HIGH
  burry        : SHORT if value_trap == HIGH
  druckenmiller: BUY in risk-on, SHORT in risk-off (regime-only rule)

Sharpe computation:
  Uses pct_change from ticker_signals as the return series.
  Sharpe = (mean_return - 0) / std_return × sqrt(12)   [monthly, annualised]

CLI:
    python -m src.memory.shadow_backtest
    python -m src.memory.shadow_backtest --dry-run
    python -m src.memory.shadow_backtest --min-scored 5 --threshold 0.30
"""

import argparse
import math
import os
import sqlite3
from datetime import datetime
from typing import Callable

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "run_archive.db")

SHARPE_DEVIATION_THRESHOLD = float(os.getenv("SHADOW_SHARPE_THRESHOLD", "0.30"))
MIN_SCORED_DEFAULT = 10


# ── Rule definitions ──────────────────────────────────────────────────────────

def _rule_graham(row: dict) -> str | None:
    dcf = row.get("dcf_iv_vs_price_pct")
    if dcf is None:
        return None
    return "BUY" if dcf >= 33.0 else "HOLD"


def _rule_buffett(row: dict) -> str | None:
    dcf = row.get("dcf_iv_vs_price_pct")
    pl  = row.get("power_law_score")
    if dcf is None or pl is None:
        return None
    return "BUY" if (dcf >= 25.0 and pl >= 7.0) else "HOLD"


def _rule_damodaran(row: dict) -> str | None:
    ev = row.get("ev_upside_pct")
    if ev is None:
        return None
    return "BUY" if ev >= 20.0 else "HOLD"


def _rule_pabrai(row: dict) -> str | None:
    ev   = row.get("ev_upside_pct")
    trap = row.get("value_trap_verdict") or ""
    if ev is None:
        return None
    return "BUY" if (ev >= 30.0 and "HIGH" not in trap.upper()) else "HOLD"


def _rule_burry(row: dict) -> str | None:
    trap = row.get("value_trap_verdict") or ""
    return "SHORT" if "HIGH" in trap.upper() else "HOLD"


def _rule_druckenmiller(row: dict) -> str | None:
    regime = row.get("regime_risk_appetite") or ""
    if regime == "risk-on":
        return "BUY"
    if regime == "risk-off":
        return "SHORT"
    return "HOLD"


AGENT_RULES: dict[str, Callable[[dict], str | None]] = {
    "graham":        _rule_graham,
    "buffett":       _rule_buffett,
    "damodaran":     _rule_damodaran,
    "pabrai":        _rule_pabrai,
    "burry":         _rule_burry,
    "druckenmiller": _rule_druckenmiller,
}


# ── Sharpe helper ─────────────────────────────────────────────────────────────

def _sharpe(returns: list[float]) -> float:
    """Annualised Sharpe (monthly sampling, rf=0)."""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    return round((mean / std) * math.sqrt(12), 3) if std else 0.0


# ── Core computation ──────────────────────────────────────────────────────────

def run_shadow_backtest(min_scored: int = MIN_SCORED_DEFAULT) -> dict:
    """
    Load all scored ticker signals, apply deterministic rules, compute
    rule-based Sharpe vs LLM Sharpe, and flag significant deviation.

    Returns
    -------
    {
      "llm_sharpe":          float,
      "rule_sharpe":         {agent: float},
      "llm_hit_rate":        float,
      "rule_hit_rate":       {agent: float},
      "deviations":          {agent: float},   # llm_sharpe - rule_sharpe
      "alerts":              [str],            # agents where deviation > threshold
      "total_scored":        int,
      "threshold":           float,
      "computed_at":         str,
      "insufficient_data":   bool,
    }
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                ts.ticker, ts.final_action, ts.pct_change, ts.outcome,
                ts.dcf_iv_vs_price_pct, ts.ev_upside_pct,
                ts.power_law_score, ts.value_trap_verdict,
                r.regime_risk_appetite
            FROM ticker_signals ts
            JOIN runs r ON r.run_id = ts.run_id
            WHERE ts.outcome NOT IN ('PENDING', 'NEUTRAL')
              AND ts.pct_change IS NOT NULL
            """
        ).fetchall()
        conn.close()
    except Exception as exc:
        return {
            "llm_sharpe": 0.0, "rule_sharpe": {}, "llm_hit_rate": 0.0,
            "rule_hit_rate": {}, "deviations": {}, "alerts": [],
            "total_scored": 0, "threshold": SHARPE_DEVIATION_THRESHOLD,
            "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "insufficient_data": True, "error": str(exc),
        }

    data = [dict(r) for r in rows]
    total_scored = len(data)

    if total_scored < min_scored:
        return {
            "llm_sharpe": 0.0, "rule_sharpe": {}, "llm_hit_rate": 0.0,
            "rule_hit_rate": {}, "deviations": {}, "alerts": [],
            "total_scored": total_scored, "threshold": SHARPE_DEVIATION_THRESHOLD,
            "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "insufficient_data": True,
            "reason": f"Only {total_scored} scored signals (need {min_scored}+)",
        }

    # ── LLM (archive) performance ─────────────────────────────────────────────
    llm_returns    = [r["pct_change"] for r in data]
    llm_correct    = sum(1 for r in data if r["outcome"] == "CORRECT")
    llm_sharpe     = _sharpe(llm_returns)
    llm_hit_rate   = round(llm_correct / total_scored, 3)

    # ── Rules-based performance per agent ─────────────────────────────────────
    # For each row, apply the rule.  If rule signals BUY and outcome was for
    # a BUY-direction trade, attribute pct_change; if SHORT, negate pct_change.
    # A HOLD rule signal = no position → return 0.
    rule_returns:  dict[str, list[float]] = {a: [] for a in AGENT_RULES}
    rule_correct:  dict[str, int]         = {a: 0  for a in AGENT_RULES}
    rule_coverage: dict[str, int]         = {a: 0  for a in AGENT_RULES}

    for row in data:
        actual_action = (row.get("final_action") or "HOLD").upper()
        pct = row["pct_change"]

        for agent, rule_fn in AGENT_RULES.items():
            rule_signal = rule_fn(row)
            if rule_signal is None:
                continue   # rule could not evaluate (missing data)

            rule_coverage[agent] += 1

            if rule_signal == "HOLD":
                rule_returns[agent].append(0.0)
                continue

            # Directional return: positive if rule agrees with price movement
            if rule_signal == "BUY":
                rule_ret = pct
            else:   # SHORT
                rule_ret = -pct

            rule_returns[agent].append(rule_ret)

            # Correct if rule direction matched outcome
            if rule_signal in ("BUY", "COVER") and actual_action in ("BUY", "COVER"):
                if pct > 5:
                    rule_correct[agent] += 1
            elif rule_signal in ("SHORT", "SELL") and actual_action in ("SHORT", "SELL"):
                if pct < -5:
                    rule_correct[agent] += 1

    rule_sharpe:    dict[str, float] = {}
    rule_hit_rate:  dict[str, float] = {}
    for agent in AGENT_RULES:
        rets = rule_returns[agent]
        cov  = rule_coverage[agent]
        rule_sharpe[agent]   = _sharpe(rets) if rets else 0.0
        rule_hit_rate[agent] = round(rule_correct[agent] / cov, 3) if cov else 0.0

    # ── Deviation = LLM Sharpe - Rule Sharpe ─────────────────────────────────
    deviations = {a: round(llm_sharpe - rule_sharpe[a], 3) for a in AGENT_RULES}
    alerts = [
        f"{a}: LLM Sharpe {llm_sharpe:.2f} vs rule Sharpe {rule_sharpe[a]:.2f} "
        f"(deviation {deviations[a]:+.2f})"
        for a in AGENT_RULES
        if abs(deviations[a]) > SHARPE_DEVIATION_THRESHOLD
        and rule_coverage[a] >= min_scored
    ]

    return {
        "llm_sharpe":        llm_sharpe,
        "rule_sharpe":       rule_sharpe,
        "llm_hit_rate":      llm_hit_rate,
        "rule_hit_rate":     rule_hit_rate,
        "deviations":        deviations,
        "rule_coverage":     rule_coverage,
        "alerts":            alerts,
        "total_scored":      total_scored,
        "threshold":         SHARPE_DEVIATION_THRESHOLD,
        "computed_at":       datetime.now().strftime("%Y-%m-%d %H:%M"),
        "insufficient_data": False,
    }


# ── Report ────────────────────────────────────────────────────────────────────

def print_shadow_report(result: dict) -> None:
    W = 72
    print(f"\n{'='*W}")
    print(f"  Rules-Based Shadow Backtest")
    print(f"  Computed : {result.get('computed_at', 'now')}")
    print(f"  Scored signals : {result.get('total_scored', 0)}")
    print("=" * W)

    if result.get("insufficient_data"):
        print(f"  STATUS : INSUFFICIENT DATA")
        if result.get("reason"):
            print(f"           {result['reason']}")
        if result.get("error"):
            print(f"           Error: {result['error']}")
        print("=" * W)
        return

    print(f"\n  LLM (archive) performance:")
    print(f"    Sharpe   : {result['llm_sharpe']:+.3f}")
    print(f"    Hit rate : {result['llm_hit_rate']:.1%}")

    print(f"\n  {'Agent':<18}  {'Rule Sharpe':>12}  {'LLM-Rule Dev':>13}  "
          f"{'Rule Hit%':>9}  {'Coverage':>8}")
    print(f"  {'-'*66}")

    for agent in sorted(AGENT_RULES):
        rs  = result["rule_sharpe"].get(agent, 0.0)
        dev = result["deviations"].get(agent, 0.0)
        rhr = result["rule_hit_rate"].get(agent, 0.0)
        cov = result["rule_coverage"].get(agent, 0)
        flag = " *** ALERT" if abs(dev) > result["threshold"] and cov >= MIN_SCORED_DEFAULT else ""
        print(f"  {agent:<18}  {rs:>+12.3f}  {dev:>+13.3f}  "
              f"{rhr:>9.1%}  {cov:>8}{flag}")

    if result["alerts"]:
        print(f"\n  DEVIATION ALERTS (threshold ±{result['threshold']:.2f}):")
        for a in result["alerts"]:
            print(f"    · {a}")
        print()
        print("  Interpretation:")
        print("  · Positive deviation (LLM > rule): LLM adds value — agents are")
        print("    reasoning beyond the mechanical rule.  This is healthy.")
        print("  · Negative deviation (LLM < rule): agents underperforming a naive")
        print("    rule — review prompts for that agent and check for regime drift.")
    else:
        print(f"\n  STATUS : OK — all agents within ±{result['threshold']:.2f} Sharpe of rules baseline")

    print("=" * W)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli() -> None:
    global SHARPE_DEVIATION_THRESHOLD  # noqa: PLW0603
    parser = argparse.ArgumentParser(
        description="Rules-based shadow backtest against the run archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.memory.shadow_backtest
  python -m src.memory.shadow_backtest --min-scored 5
  python -m src.memory.shadow_backtest --threshold 0.20
        """,
    )
    parser.add_argument(
        "--min-scored", type=int, default=MIN_SCORED_DEFAULT,
        help=f"Minimum scored ticker signals required (default: {MIN_SCORED_DEFAULT}).",
    )
    parser.add_argument(
        "--threshold", type=float, default=SHARPE_DEVIATION_THRESHOLD,
        help=f"Sharpe deviation to trigger alert (default: {SHARPE_DEVIATION_THRESHOLD}).",
    )
    args = parser.parse_args()

    SHARPE_DEVIATION_THRESHOLD = args.threshold
    result = run_shadow_backtest(min_scored=args.min_scored)
    print_shadow_report(result)


if __name__ == "__main__":
    _cli()
