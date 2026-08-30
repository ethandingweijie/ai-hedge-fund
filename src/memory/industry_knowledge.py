"""What a sector note knows, keyed the way an equity is actually routed.

A sector report matches no ticker, so `match_tickers` returns [] and the
document is downloaded, registered as `unmatched`, and never read. Meanwhile
`sector_prompts._SECTOR_PROFILE_PROMPTS` — the block injected into the
deep-research prompt as "2F. INDUSTRY-SPECIFIC KPI FRAMEWORK" — is keyed by
exactly `(sector, profile_name)` and is hand-written eleven times over. Of the
95 (sector, sub-profile) routes in the engine, 26 get a tailored block and 54
fall through to a generic "every industry has 3-5 metrics that matter".

This store closes that gap: sector notes populate the 2F block instead of it
being authored by hand.

**Market is part of the key.** The profile taxonomy is inconsistent about it —
17 of 95 profiles name their market, and 10 are shared across markets, 9 of
those HK+US. `Money Center Bank` is held by JPM, BAC, C, WFC *and* 02888.HK,
so filing a US money-centre note and a Hong Kong banks note under one key
would blend a CCAR/US-rate-cycle industry with a HIBOR/mainland-credit one.
Lookup falls back to market-agnostic, because most industries are not
market-specific and one good note should stay useful everywhere.

Three kinds of content, because sector notes carry three:

  quantitative   TAM, penetration, category CAGR, take rate, and a
                 comparative multiple table across named peers
  structural     the anchor KPI, what companies here disclose, who competes
                 on what axis
  forward        named trends and who the analyst positions to win

The forward half is stored as **the analyst's view with house and vintage
attached**, never as fact — the same discipline the PM prompt already applies
to a deposited equity thesis.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from src.data import db

logger = logging.getLogger(__name__)

# "" is the market-agnostic bucket — a note that is true of an industry
# wherever it trades.
ANY_MARKET = ""

_DDL = """
CREATE TABLE IF NOT EXISTS industry_knowledge (
    market            TEXT NOT NULL,
    sector            TEXT NOT NULL,
    profile           TEXT NOT NULL,
    as_of             TEXT NOT NULL,
    house             TEXT NOT NULL,
    anchor_kpi        TEXT,
    disclosed_metrics_json  TEXT,
    economics_json    TEXT,
    competitive_json  TEXT,
    quantitative_json TEXT,
    peer_multiples_json     TEXT,
    trends_json       TEXT,
    positioning_json  TEXT,
    doc_path          TEXT,
    extracted_at      TEXT NOT NULL,
    PRIMARY KEY (market, sector, profile, as_of, house)
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_industry_knowledge_lookup "
    "ON industry_knowledge(sector, profile, market)",
]

_ensure_lock = threading.Lock()
_ensured = False


def ensure_industry_table() -> None:
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
            except Exception:       # an index is an optimisation, not a contract
                pass
        _ensured = True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps(obj) -> Optional[str]:
    if obj in (None, [], {}):
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def upsert_industry_knowledge(
    sector: str,
    profile: str,
    house: str,
    as_of: str,
    market: str = ANY_MARKET,
    anchor_kpi: str | None = None,
    disclosed_metrics=None,
    economics=None,
    competitive=None,
    quantitative=None,
    peer_multiples=None,
    trends=None,
    positioning=None,
    doc_path: str | None = None,
) -> None:
    """Record one sector note's view of one (market, sector, profile).

    Keyed on house and vintage as well, so two banks' views of the same
    industry coexist rather than overwrite — disagreement between houses is
    signal, not a conflict to resolve.
    """
    ensure_industry_table()
    db.execute(
        """
        INSERT INTO industry_knowledge
            (market, sector, profile, as_of, house, anchor_kpi,
             disclosed_metrics_json, economics_json, competitive_json,
             quantitative_json, peer_multiples_json, trends_json,
             positioning_json, doc_path, extracted_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(market, sector, profile, as_of, house) DO UPDATE SET
            anchor_kpi=excluded.anchor_kpi,
            disclosed_metrics_json=excluded.disclosed_metrics_json,
            economics_json=excluded.economics_json,
            competitive_json=excluded.competitive_json,
            quantitative_json=excluded.quantitative_json,
            peer_multiples_json=excluded.peer_multiples_json,
            trends_json=excluded.trends_json,
            positioning_json=excluded.positioning_json,
            doc_path=excluded.doc_path,
            extracted_at=excluded.extracted_at
        """,
        [market or ANY_MARKET, sector, profile, as_of or "unknown",
         house or "sell-side", anchor_kpi,
         _dumps(disclosed_metrics), _dumps(economics), _dumps(competitive),
         _dumps(quantitative), _dumps(peer_multiples), _dumps(trends),
         _dumps(positioning), doc_path, _now()],
    )


def _row(r) -> dict[str, Any]:
    def col(name):
        try:
            return r[name]
        except Exception:
            return None
    return {
        "market": col("market"), "sector": col("sector"),
        "profile": col("profile"), "as_of": col("as_of"),
        "house": col("house"), "anchor_kpi": col("anchor_kpi"),
        "disclosed_metrics": _loads(col("disclosed_metrics_json")) or [],
        "economics": _loads(col("economics_json")) or [],
        "competitive": _loads(col("competitive_json")) or [],
        "quantitative": _loads(col("quantitative_json")) or {},
        "peer_multiples": _loads(col("peer_multiples_json")) or [],
        "trends": _loads(col("trends_json")) or [],
        "positioning": _loads(col("positioning_json")) or [],
        "doc_path": col("doc_path"),
    }


def get_industry_knowledge(sector: str, profile: str,
                           market: str = ANY_MARKET) -> list[dict[str, Any]]:
    """Notes for one route, newest first, market-specific ahead of agnostic.

    Extends the tiering `get_kpi_prompt` already uses rather than inventing
    one: a market-scoped note wins where the dynamics genuinely diverge, and
    a market-agnostic note still serves every market that has nothing more
    specific. Returns [] on any store error — a missing 2F block falls back
    to the hand-written one, never to an exception.
    """
    try:
        ensure_industry_table()
        rows = db.query(
            "SELECT * FROM industry_knowledge "
            "WHERE sector = ? AND profile = ? AND (market = ? OR market = ?) "
            "ORDER BY as_of DESC",
            [sector, profile, market or ANY_MARKET, ANY_MARKET],
        )
    except Exception as exc:
        logger.warning("industry_knowledge read failed: %s", exc)
        return []

    out = [_row(r) for r in rows or []]
    # Market-specific first, then market-agnostic; newest within each.
    out.sort(key=lambda d: (0 if (d.get("market") or "") == (market or ANY_MARKET)
                            and market else 1))
    return out


def industry_coverage() -> list[dict[str, Any]]:
    """Which routes have knowledge, richest first — the operational view of
    where the 54 generic routes have been filled and where they have not."""
    try:
        ensure_industry_table()
        rows = db.query(
            "SELECT market, sector, profile, COUNT(*) AS n "
            "FROM industry_knowledge GROUP BY market, sector, profile "
            "ORDER BY n DESC"
        )
    except Exception:
        return []
    out = []
    for r in rows or []:
        try:
            out.append({"market": r["market"], "sector": r["sector"],
                        "profile": r["profile"], "notes": int(r["n"])})
        except Exception:
            continue
    return out
