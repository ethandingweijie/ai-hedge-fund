"""
src/research_ideas/alerts/iv15_monitor.py
==========================================
IV15 "price reached fair value" sweep for the SW46 + HK50 cohorts.

The check, per name:

    live_p_iv15 = live_price / stored_iv15_per_share
    FIRE if live_p_iv15 <= IV15_ALERT_BAND   (default 1.00 — price <= fair value)

`stored_iv15_per_share` is the fundamental anchor from the latest cohort run
(it does NOT move intraday). `live_price` is a FRESH quote — the price stored
on the cohort row is stale, so we must re-quote. We quote the SAME symbol the
cohort row's price/IV15 were computed against so the currency matches (SW46:
US ticker; HK50: the HK line or the ADR depending on the row's route).

Hysteresis (anti-spam): we keep a per-(cohort, ticker) state in
iv15_alert_store. A name fires ONCE when it crosses from `armed` (above IV15)
to `triggered` (at/below IV15). It re-arms only after the live price recovers
above IV15_REARM_BAND (default 1.05 = 5% above fair value). So a name sitting
cheap for weeks alerts once, not every morning.

Names FMP can't return a fresh quote for are skipped (no alert) — never a
false positive. Single-name failures never abort the sweep.

Env knobs:
  IV15_ALERT_BAND     — trigger band on live P/IV15 (default 1.00)
  IV15_REARM_BAND     — re-arm band on live P/IV15 (default 1.05)
  SLACK_WEBHOOK_URL   — required for delivery (no-op without it)
  APP_BASE_URL        — used for the "Open in app" deep-links
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from app.backend.services import iv15_alert_store as store

logger = logging.getLogger(__name__)


def _band(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("iv15_monitor: bad %s; using default %s", name, default)
        return default


TRIGGER_BAND = _band("IV15_ALERT_BAND", 1.00)
REARM_BAND = _band("IV15_REARM_BAND", 1.05)


# ─── Candidate + fired-alert shapes ────────────────────────────────────────


@dataclass(frozen=True)
class AlertCandidate:
    """One name to monitor, normalised across cohorts."""
    cohort: str                 # 'sw46' | 'hk50'
    quote_symbol: str           # symbol to fetch a fresh quote for (FMP)
    display_ticker: str         # what to show the user
    name: str
    iv15: float                 # stored per-share fair value (quote ccy)
    stored_price: Optional[float] = None
    currency: str = ""
    aict: str = ""
    conviction: str = ""        # HK50 only
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FiredAlert:
    cohort: str
    display_ticker: str
    name: str
    live_price: float
    iv15: float
    live_p_iv15: float
    currency: str = ""
    aict: str = ""
    conviction: str = ""


@dataclass
class SweepResult:
    checked: int = 0
    fired: list = field(default_factory=list)         # list[FiredAlert]
    rearmed: list = field(default_factory=list)       # list[str] "cohort:ticker"
    skipped_no_quote: list = field(default_factory=list)  # list[str]
    errors: list = field(default_factory=list)        # list[str]
    posted_to_slack: bool = False


# ─── Cohort loaders ────────────────────────────────────────────────────────


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def load_sw46_candidates() -> list[AlertCandidate]:
    """Latest SW46 cohort → monitor candidates. SW46 names are US tickers, so
    the quote symbol is just the ticker."""
    from app.backend.services import sw46_storage

    run = sw46_storage.get_latest_sw46_run()
    if not run:
        return []
    out: list[AlertCandidate] = []
    for r in run.get("results", []):
        if r.get("error"):
            continue
        ticker = (r.get("ticker") or "").strip()
        iv15 = _safe_float((r.get("iv15") or {}).get("iv15_per_share"))
        if not ticker or not iv15 or iv15 <= 0:
            continue
        out.append(AlertCandidate(
            cohort="sw46",
            quote_symbol=ticker.upper(),
            display_ticker=ticker.upper(),
            name=r.get("name") or ticker,
            iv15=iv15,
            stored_price=_safe_float(r.get("price")),
            currency="USD",
            aict=(r.get("aict") or {}).get("tier", ""),
        ))
    return out


def _hk50_quote_symbol(r: dict) -> str:
    """Pick the symbol whose price/IV15 the row was computed against, so the
    fresh quote is in the same currency. ADR-routed rows quote the reported
    ticker (USD ADR); HK-routed rows quote the canonical HK line."""
    route = (r.get("route_label") or "").upper()
    reported = (r.get("ticker") or "").strip()
    hk = (r.get("hk_ticker") or "").strip()
    if route.startswith("ADR") and reported:
        return reported.upper()
    return (hk or reported).upper()


def load_hk50_candidates() -> list[AlertCandidate]:
    """Latest HK50 cohort → monitor candidates."""
    from app.backend.services import hk50_storage

    run = hk50_storage.get_latest_hk50_run()
    if not run:
        return []
    out: list[AlertCandidate] = []
    for r in run.get("results", []):
        if r.get("error"):
            continue
        iv15 = _safe_float(r.get("iv15"))
        sym = _hk50_quote_symbol(r)
        if not sym or not iv15 or iv15 <= 0:
            continue
        qual = r.get("qualitative") or {}
        out.append(AlertCandidate(
            cohort="hk50",
            quote_symbol=sym,
            display_ticker=(r.get("ticker") or sym).upper(),
            name=r.get("name") or sym,
            iv15=iv15,
            stored_price=_safe_float(r.get("price")),
            currency=r.get("currency") or "",
            aict=r.get("aict_tier") or "",
            conviction=qual.get("conviction") or "",
        ))
    return out


# ─── The sweep ─────────────────────────────────────────────────────────────


def run_iv15_sweep(*, dry_run: bool = False) -> SweepResult:
    """Run one IV15 cross sweep across SW46 + HK50.

    dry_run=True computes crossings and returns them but does NOT persist any
    state change and does NOT post to Slack — safe to run repeatedly for
    testing. dry_run=False persists the hysteresis state and posts a single
    consolidated Slack message for the names that newly crossed below IV15.
    """
    from src.agents.dd.batch_quote import fetch_batch_quotes

    result = SweepResult()

    candidates = load_sw46_candidates() + load_hk50_candidates()
    if not candidates:
        logger.info("iv15_monitor: no candidates (no cohort runs yet)")
        return result

    # One batch quote across the union of symbols.
    symbols = sorted({c.quote_symbol for c in candidates})
    quotes = fetch_batch_quotes(symbols)
    logger.info(
        "iv15_monitor: %d candidates, %d symbols, %d quotes returned",
        len(candidates), len(symbols), len(quotes),
    )

    for c in candidates:
        try:
            q = quotes.get(c.quote_symbol)
            if q is None or q.price is None or q.price <= 0:
                result.skipped_no_quote.append(f"{c.cohort}:{c.quote_symbol}")
                continue

            live_price = float(q.price)
            live_p_iv15 = live_price / c.iv15
            result.checked += 1

            prev = store.get_all_states(c.cohort).get(c.quote_symbol)
            prev_state = (prev or {}).get("state") or "armed"

            if live_p_iv15 <= TRIGGER_BAND and prev_state == "armed":
                # Downward cross — FIRE.
                result.fired.append(FiredAlert(
                    cohort=c.cohort,
                    display_ticker=c.display_ticker,
                    name=c.name,
                    live_price=live_price,
                    iv15=c.iv15,
                    live_p_iv15=live_p_iv15,
                    currency=c.currency,
                    aict=c.aict,
                    conviction=c.conviction,
                ))
                if not dry_run:
                    store.upsert_state(
                        cohort=c.cohort, ticker=c.quote_symbol, state="triggered",
                        p_iv15=live_p_iv15, price=live_price, iv15=c.iv15, alerted=True,
                    )
            elif live_p_iv15 >= REARM_BAND and prev_state == "triggered":
                # Recovered above the re-arm band — re-arm (no alert).
                result.rearmed.append(f"{c.cohort}:{c.quote_symbol}")
                if not dry_run:
                    store.upsert_state(
                        cohort=c.cohort, ticker=c.quote_symbol, state="armed",
                        p_iv15=live_p_iv15, price=live_price, iv15=c.iv15, alerted=False,
                    )
            else:
                # No state change — just refresh the last-seen telemetry.
                if not dry_run:
                    store.upsert_state(
                        cohort=c.cohort, ticker=c.quote_symbol, state=prev_state,
                        p_iv15=live_p_iv15, price=live_price, iv15=c.iv15, alerted=False,
                    )
        except Exception as exc:  # never let one name abort the sweep
            logger.warning("iv15_monitor: %s failed: %s", c.quote_symbol, exc)
            result.errors.append(f"{c.cohort}:{c.quote_symbol}: {exc}")

    # Consolidated Slack push for the names that newly crossed this sweep.
    if result.fired and not dry_run:
        try:
            from src.research_ideas.alerts.iv15_slack import post_iv15_alerts
            result.posted_to_slack = post_iv15_alerts(
                result.fired, app_base_url=os.environ.get("APP_BASE_URL"),
            )
        except Exception as exc:
            logger.warning("iv15_monitor: Slack post failed: %s", exc)

    if not dry_run:
        try:
            store.record_sweep(
                checked=result.checked,
                fired=len(result.fired),
                rearmed=len(result.rearmed),
                skipped=len(result.skipped_no_quote),
                detail={
                    "fired": [f"{a.cohort}:{a.display_ticker}" for a in result.fired],
                    "errors": result.errors[:20],
                },
            )
        except Exception as exc:
            logger.warning("iv15_monitor: record_sweep failed: %s", exc)

    logger.info(
        "iv15_monitor: sweep done — checked=%d fired=%d rearmed=%d skipped=%d errors=%d slack=%s",
        result.checked, len(result.fired), len(result.rearmed),
        len(result.skipped_no_quote), len(result.errors), result.posted_to_slack,
    )
    return result
