"""What the street applies, against what the industry actually trades at.

The system already holds both halves of a comparison it never made.
`regional_comps` learns industry and sector medians per market from the
weekly screener sweep; the deposited broker notes state the multiple an
analyst applied. Nothing joined them, so every new report was read once and
forgotten — the multiple in it never made the system better at judging the
next name in that industry.

This records the join. One row per (ticker, field, report vintage):

    MU / pe / 2026-06   street 18.0x   peer 12.4x (n=14, industry)   +45%

Deliberately an observation log, not a correction. `get_industry_calibration`
aggregates the spread once several observations exist, but nothing consumes
it to move a valuation yet — with a corpus in the tens, a median spread from
one or two notes is an anecdote, and acting on it would re-introduce exactly
the dependence on deposited PDFs this is meant to reduce. It banks evidence
now and earns the right to decide later.

A row is written only when BOTH halves exist. A street multiple with no comp
set, or a comp set with no report, is not an observation — it is half of one,
and storing it with a null would quietly corrupt any later aggregate.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from src.data import db

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS multiple_calibration (
    ticker         TEXT NOT NULL,
    field          TEXT NOT NULL,
    as_of          TEXT NOT NULL,
    market         TEXT,
    industry       TEXT,
    sector         TEXT,
    street_multiple REAL NOT NULL,
    peer_median    REAL NOT NULL,
    peer_count     INTEGER,
    peer_basis     TEXT,
    spread_pct     REAL,
    house          TEXT,
    method         TEXT,
    observed_at    TEXT NOT NULL,
    PRIMARY KEY (ticker, field, as_of)
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_multiple_calibration_lookup "
    "ON multiple_calibration(market, industry, field)",
]

# A parsed `multiple_basis` maps onto the comps field it should be judged
# against. `p_nav` rides on `pb` — both are book-style denominators and the
# comps table carries no separate NAV field.
BASIS_TO_FIELD = {
    "pe": "pe",
    "ev_ebitda": "ev_ebitda",
    "p_s": "ev_revenue",
    "p_b": "pb",
    "p_nav": "pb",
}

# A street multiple this far from the peer median is far more likely to be a
# parse error than a real view — a target price mistaken for a multiple, or a
# year. Logged and skipped rather than banked.
# 3.0 = 300%. The first live backfill produced a +444% reading that was a
# mis-read cell, not a view. A genuine street-vs-peer disagreement beyond
# 3x is vanishingly rare; a parse error at that magnitude is not.
_MAX_PLAUSIBLE_SPREAD = 3.0

_ensure_lock = threading.Lock()
_ensured = False


def ensure_calibration_table() -> None:
    """Idempotent table creation (first use per process)."""
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        db.execute_script(_DDL)
        for stmt in _INDEXES:
            try:
                db.execute(stmt)
            except Exception:      # index is an optimisation, not a contract
                pass
        _ensured = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(raw: Any) -> Optional[float]:
    """A ratios-table cell as a number, or None.

    Cells arrive as printed: "36.5", "(2.3)" for negative, "NM", "—", "1.9%".
    """
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "").replace("%", "")
    if not text or text.upper() in {"NM", "NA", "N/A", "-", "—", "–"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _forward_table_multiples(ticker: str) -> Optional[dict[str, Any]]:
    """Forward-year multiples from the note's ratios table, or None.

    Nearly every note carries this table and it states several multiples per
    forward year, where the methodology sentence states at most one — so it
    is both the denser source and the better-matched one, since a peer median
    is itself forward-looking.

    The FIRST estimated column is used (a label carrying 'e' or 'E'), which
    is the nearest forward year. Historic columns are skipped: comparing a
    trailing multiple to a forward peer median is a different question.
    """
    try:
        from src.memory import assumption_store
        reports = assumption_store.get_analyst_reports(ticker, limit=1)
    except Exception:
        return None
    if not reports:
        return None
    rows = (reports[0] or {}).get("valuation_ratios") or []
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("fiscal_year_label") or "")
        if "e" not in label.lower():
            continue                      # historic column
        row = _repair_reit_pe(ticker, row)
        for basis, key in (("pe", "pe"), ("ev_ebitda", "ev_ebitda"),
                           ("p_s", "ev_sales"), ("p_b", "pb")):
            value = _num(row.get(key))
            if value and value > 0:
                return {"multiple_basis": basis, "target_multiple": value,
                        "source": f"ratios table {label}"}
    return None


# A REIT's ratios table quotes P/NAV, DPU yield and P/FFO — not P/E. When the
# extractor lands a P/NAV in the P/E slot the number is arithmetically fine
# and financially meaningless, and it reads as a 90% discount to the peer P/E.
# Both live cases, confirmed against the notes:
#
#   AJBU.SI  pe 1.32, dividend_yield 5.1%, no pb   P/NAV in the pe slot
#   CY6U.SI  pe 0.70 AND pb 0.70 — identical       one number, two labels
#
# Recover rather than discard: a 1.32x P/NAV is a real observation against the
# REIT pb median, and throwing it away loses evidence from the market with the
# thinnest coverage.
_REIT_PE_FLOOR = 5.0


def _repair_reit_pe(ticker: str, row: dict) -> dict:
    """Re-label a P/NAV that was extracted into the P/E column."""
    pe = _num(row.get("pe"))
    if not pe or pe >= _REIT_PE_FLOOR:
        return row                        # a plausible P/E — leave it alone

    pb = _num(row.get("pb"))
    duplicated = pb is not None and abs(pe - pb) < 1e-9

    is_reit = False
    try:
        from src.data.sector_profiles import get_wacc_profile_for_ticker
        sector, profile = get_wacc_profile_for_ticker(ticker)
        blob = f"{sector} {profile}".lower()
        is_reit = "reit" in blob or "trust" in blob
    except Exception:
        pass

    if not (duplicated or is_reit):
        return row

    fixed = dict(row)
    fixed["pe"] = ""                      # never a P/E
    if pb is None:
        fixed["pb"] = row.get("pe")       # it was the P/NAV all along
    logger.info(
        "multiple_calibration: %s P/E %.2f re-read as P/NAV (%s)",
        ticker, pe, "duplicate of pb" if duplicated else "REIT profile",
    )
    return fixed


def build_observation(ticker: str) -> Optional[dict[str, Any]]:
    """The join for one ticker, or None when either half is missing.

    Never raises: a cold comps table, an unclassifiable ticker or a report
    with no stated multiple are all ordinary outcomes, not errors.
    """
    try:
        from src.memory.analyst_basis import get_analyst_basis
        basis = get_analyst_basis(ticker) or {}
    except Exception:
        return None

    # The ratios table wins over the methodology sentence: it is present in
    # nearly every note (the sentence states a multiple in about a quarter of
    # them) and it is stated per forward year, which is the like-for-like
    # comparison against a forward peer median.
    source = "methodology line"
    table = _forward_table_multiples(ticker)
    if table:
        basis = {**basis, **table}
        source = table["source"]

    multiple = basis.get("target_multiple")
    field = BASIS_TO_FIELD.get(basis.get("multiple_basis") or "")
    if not multiple or not field:
        return None                     # no multiple stated, or no comparable field

    try:
        from src.data.regional_comps import (
            get_fmp_classification, get_regional_multiples, market_for_exchange,
        )
        info = get_fmp_classification(ticker) or {}
        market = market_for_exchange(info.get("exchange", ""))
        if not market:
            return None
        comps = get_regional_multiples(
            market, info.get("industry"), info.get("sector")
        ) or {}
    except Exception:
        return None

    # Plausibility band on the STREET multiple, reusing the same bands
    # regional_comps applies to peer values (`pe` 1-200, `pb` 0.05-30, ...).
    # A ratios table is a grid, and a mis-read column yields a number that is
    # arithmetically fine and financially impossible. From the first live
    # backfill: CapitaLand India Trust "P/E 0.7x" and Keppel DC REIT "1.3x" —
    # both REITs, both quoting P/NAV or DPU yield in the row the extractor
    # took for P/E. Banked, they would have dragged a REIT industry median
    # toward zero permanently.
    try:
        from src.data.regional_comps import _BANDS
        lo, hi = _BANDS.get(field, (None, None))
    except Exception:
        lo = hi = None
    if lo is not None and not (lo <= float(multiple) <= hi):
        logger.info(
            "multiple_calibration: %s %s street %.2fx outside the plausible "
            "band %.2f-%.2f — treating as a mis-read cell and skipping",
            ticker, field, multiple, lo, hi,
        )
        return None

    row = comps.get(field) or {}
    peer = row.get("value")
    if not peer:
        return None                     # half an observation is not an observation

    spread = float(multiple) / float(peer) - 1.0
    if abs(spread) > _MAX_PLAUSIBLE_SPREAD:
        logger.info(
            "multiple_calibration: %s %s street %.2fx vs peer %.2fx — spread "
            "%.0f%% implausible, treating as a parse error and skipping",
            ticker, field, multiple, peer, spread * 100,
        )
        return None

    return {
        "ticker": ticker,
        "field": field,
        "as_of": str(basis.get("as_of") or "")[:32] or "unknown",
        "market": market,
        "industry": info.get("industry"),
        "sector": info.get("sector"),
        "street_multiple": float(multiple),
        "peer_median": float(peer),
        "peer_count": row.get("peer_count"),
        "peer_basis": row.get("basis"),
        "spread_pct": spread,
        "house": basis.get("house"),
        "method": basis.get("method"),
        "source": source,
    }


def record_observation(ticker: str) -> Optional[dict[str, Any]]:
    """Build and persist one observation. Returns it, or None if not made.

    Idempotent on (ticker, field, as_of): re-ingesting the same report
    refreshes the peer side — the medians move weekly — without creating a
    second observation of the same analyst view.
    """
    obs = build_observation(ticker)
    if obs is None:
        return None
    try:
        ensure_calibration_table()
        db.execute(
            """
            INSERT INTO multiple_calibration
                (ticker, field, as_of, market, industry, sector,
                 street_multiple, peer_median, peer_count, peer_basis,
                 spread_pct, house, method, observed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker, field, as_of) DO UPDATE SET
                peer_median=excluded.peer_median,
                peer_count=excluded.peer_count,
                peer_basis=excluded.peer_basis,
                spread_pct=excluded.spread_pct,
                observed_at=excluded.observed_at
            """,
            [obs["ticker"], obs["field"], obs["as_of"], obs["market"],
             obs["industry"], obs["sector"], obs["street_multiple"],
             obs["peer_median"], obs["peer_count"], obs["peer_basis"],
             obs["spread_pct"], obs["house"], obs["method"], _now()],
        )
    except Exception as exc:
        logger.warning("multiple_calibration: write failed for %s: %s", ticker, exc)
        return None
    return obs


def record_observations(tickers) -> list[dict[str, Any]]:
    """Record what can be recorded. Never raises; skips what it cannot."""
    out: list[dict[str, Any]] = []
    for ticker in dict.fromkeys(tickers or []):      # de-dupe, keep order
        try:
            obs = record_observation(ticker)
        except Exception:
            obs = None
        if obs:
            out.append(obs)
    return out


def get_industry_calibration(market: str, industry: str,
                             field: str) -> Optional[dict[str, Any]]:
    """Median street-vs-peer spread for one industry, or None.

    Reports the observation count alongside, because that is what says
    whether the number means anything. A caller must decide its own floor —
    this does not hide thin evidence behind a plausible-looking median.
    """
    try:
        ensure_calibration_table()
        rows = db.query(
            "SELECT spread_pct FROM multiple_calibration "
            "WHERE market = ? AND industry = ? AND field = ? "
            "AND spread_pct IS NOT NULL",
            [market, industry, field],
        )
    except Exception:
        return None
    spreads = []
    for row in rows or []:
        try:
            spreads.append(float(row["spread_pct"]))
        except Exception:
            continue
    if not spreads:
        return None
    spreads.sort()
    mid = len(spreads) // 2
    median = (spreads[mid] if len(spreads) % 2
              else (spreads[mid - 1] + spreads[mid]) / 2.0)
    return {
        "market": market,
        "industry": industry,
        "field": field,
        "median_spread_pct": median,
        "observations": len(spreads),
        "min_spread_pct": spreads[0],
        "max_spread_pct": spreads[-1],
    }


def calibration_summary() -> list[dict[str, Any]]:
    """Every industry with at least one observation, richest first.

    The operational view: what the corpus actually covers, so the thin spots
    are visible rather than inferred.
    """
    try:
        ensure_calibration_table()
        rows = db.query(
            "SELECT market, industry, field, COUNT(*) AS n "
            "FROM multiple_calibration GROUP BY market, industry, field "
            "ORDER BY n DESC"
        )
    except Exception:
        return []
    out = []
    for row in rows or []:
        try:
            out.append({"market": row["market"], "industry": row["industry"],
                        "field": row["field"], "observations": int(row["n"])})
        except Exception:
            continue
    return out
