"""
cron_dispatcher.py — Phase 2B entry point: auto-fire DD agent on ±10% breaches.

Run as a Railway cron service (every 5 min default). Each tick:

  1. Check market-hours gate (scheduler.should_run)              — instant
  2. Build dispatcher universe (universe.build_dispatcher_universe) — instant
  3. Batch-quote everything (batch_quote.fetch_batch_quotes)      — ~50ms
  4. Detect ±10% breaches (batch_quote.detect_breaches)           — instant
  5. For each breach: HTTP POST → /admin/dd-trigger              — ~100ms
  6. Web service handles cooldown + dd_agent + Slack             — async, ~60s

Architecture: dispatcher is a stateless HTTP client. All persistence + LLM
work happens server-side in the existing FastAPI service. This avoids
sharing the SQLite volume between two Railway services.

Required env vars:
  PORTFOLIO_TICKERS         — Tier 1 universe (e.g. "AAPL,MSFT,NVDA")
  DD_DISPATCHER_BASE_URL    — Web service URL (e.g. https://...railway.app)
  DB_UPLOAD_SECRET          — Shared admin secret (same as web service uses)
  FINANCIAL_DATASETS_API_KEY — FMP key (already wired for the rest of the app)

Optional env vars:
  DD_DISPATCH_THRESHOLD_PCT — Default 0.10 (10%)
  DD_MAX_ALERTS_PER_TICK    — Safety valve, default 10. If we detect more
                              breaches than this in one tick (e.g. flash
                              crash), we only dispatch the top N to prevent
                              runaway Qwen costs.
  DD_DISPATCH_TIER          — "tier1_dispatch" tag written to dd_alerts.tier
  DD_DRY_RUN                — If truthy, log breaches but skip the POST.
                              Useful for local smoke testing.
  DD_INCLUDE_ANALYZED       — Expand universe to include analyzed tickers
  DD_INCLUDE_SP500          — Expand universe to include S&P 500
  DD_FORCE_DISPATCH         — Bypass the market-hours gate (testing only)

Exit codes:
  0 — tick completed (whether or not any alerts fired)
  1 — fatal config error (missing env, bad URL, etc.)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Final

import requests

from src.agents.dd import batch_quote, scheduler, sector_clustering, universe


logger = logging.getLogger(__name__)


# Env var names
ENV_BASE_URL:        Final[str] = "DD_DISPATCHER_BASE_URL"
ENV_ADMIN_SECRET:    Final[str] = "DB_UPLOAD_SECRET"
ENV_THRESHOLD_PCT:   Final[str] = "DD_DISPATCH_THRESHOLD_PCT"
ENV_MAX_ALERTS_TICK: Final[str] = "DD_MAX_ALERTS_PER_TICK"
ENV_DISPATCH_TIER:   Final[str] = "DD_DISPATCH_TIER"
ENV_DRY_RUN:         Final[str] = "DD_DRY_RUN"

# Defaults
DEFAULT_THRESHOLD_PCT     = 0.10
DEFAULT_MAX_ALERTS_TICK   = 10
DEFAULT_DISPATCH_TIER     = "tier1_dispatch"

# HTTP timeout for /admin/dd-trigger. The route returns in ~50ms (real-mode
# dispatches a background thread), so 15s is comfortable headroom.
HTTP_TIMEOUT_SEC = 15


@dataclass
class DispatchSummary:
    """One-tick summary written to stdout as JSON for easy parsing in
    Railway logs / structured monitoring."""
    timestamp_utc:    str
    decision:         str            # gate decision (skipped/ran)
    universe_size:    int
    quotes_returned:  int
    breaches_found:   int
    alerts_dispatched: int
    alerts_skipped:    int           # breaches above MAX_ALERTS_TICK cap
    failures:          int           # POSTs that errored
    breaches:          list[dict]    # [{"ticker":..., "pct":..., "price":...}]
    clusters_dispatched: int = 0     # Phase 2C: sector cluster posts
    cluster_summary:    list[dict] = None  # [{"sector":..., "direction":..., "n":..., "members":[...]}]


def main() -> int:
    """Cron entry point. Returns process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    base_url = os.environ.get(ENV_BASE_URL, "").rstrip("/")
    secret   = os.environ.get(ENV_ADMIN_SECRET, "")
    if not base_url:
        logger.error("Missing required env: %s", ENV_BASE_URL)
        return 1
    if not secret:
        logger.error("Missing required env: %s", ENV_ADMIN_SECRET)
        return 1

    threshold = _read_float_env(ENV_THRESHOLD_PCT, DEFAULT_THRESHOLD_PCT)
    max_alerts = _read_int_env(ENV_MAX_ALERTS_TICK, DEFAULT_MAX_ALERTS_TICK)
    tier_label = os.environ.get(ENV_DISPATCH_TIER, DEFAULT_DISPATCH_TIER).strip() or DEFAULT_DISPATCH_TIER
    dry_run    = _truthy(os.environ.get(ENV_DRY_RUN, ""))

    summary = DispatchSummary(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        decision="(unset)",
        universe_size=0,
        quotes_returned=0,
        breaches_found=0,
        alerts_dispatched=0,
        alerts_skipped=0,
        failures=0,
        breaches=[],
        clusters_dispatched=0,
        cluster_summary=[],
    )

    # Step 1: market-hours gate
    decision = scheduler.should_run()
    summary.decision = decision.reason
    if not decision.should_run:
        logger.info("dispatcher: skipping (%s)", decision.reason)
        _emit_summary(summary)
        return 0
    logger.info("dispatcher: running (%s)", decision.reason)

    # Step 2: build universe.
    #
    # Tier 1 is fetched via HTTP from the web service (so the cron service
    # doesn't need the SQLite volume). Tier 2 / 3 (if enabled) still come
    # from the in-process universe builders — Tier 2 needs DB access and
    # Tier 3 hits FMP directly, neither of which has the shared-volume
    # constraint of the watchlist.
    tickers: set[str] = _fetch_tier1_via_http(base_url=base_url) | _fetch_tier_2_3_inprocess()
    summary.universe_size = len(tickers)
    if not tickers:
        logger.warning(
            "dispatcher: empty universe — add tickers via the Watchlist UI tab, "
            "or set %s env var as a fallback",
            universe.ENV_PORTFOLIO_TICKERS,
        )
        _emit_summary(summary)
        return 0

    # Step 3: batch quote
    quotes = batch_quote.fetch_batch_quotes(tickers)
    summary.quotes_returned = len(quotes)
    if not quotes:
        logger.warning("dispatcher: no quotes returned (FMP unavailable?)")
        _emit_summary(summary)
        return 0

    # Step 4: detect breaches
    breaches = batch_quote.detect_breaches(quotes, threshold_pct=threshold)
    summary.breaches_found = len(breaches)

    if not breaches:
        logger.info(
            "dispatcher: no breaches in %d quotes at ±%.0f%% threshold",
            len(quotes), threshold * 100,
        )
        _emit_summary(summary)
        return 0

    # Step 5: cluster breaches into (sector × direction) groups (Phase 2C)
    # Clusters of ≥3 same-sector same-direction breaches fire ONE sector
    # alert; the remaining "singletons" fire as individual ticker alerts.
    cluster_result = sector_clustering.cluster_breaches(breaches)
    summary.cluster_summary = [
        {"sector": c.sector, "direction": c.direction,
         "n": c.n, "members": [m.ticker for m in c.members],
         "median_pct": round(c.median_pct, 4),
         "cluster_id": c.cluster_id}
        for c in cluster_result.clusters
    ]

    # Sector clusters first, then singletons. Cap applies to TOTAL events.
    cluster_events = list(cluster_result.clusters)
    singleton_events = list(cluster_result.singletons)
    total_events = len(cluster_events) + len(singleton_events)
    if total_events > max_alerts:
        # Trim singletons first (clusters are higher-signal)
        keep_clusters = cluster_events[:max_alerts]
        keep_singletons = singleton_events[:max_alerts - len(keep_clusters)]
        summary.alerts_skipped = total_events - (len(keep_clusters) + len(keep_singletons))
        cluster_events = keep_clusters
        singleton_events = keep_singletons
        logger.warning(
            "dispatcher: %d total events but capping to %d "
            "(safety valve DD_MAX_ALERTS_PER_TICK)",
            total_events, max_alerts,
        )

    # Dispatch clusters first
    for cluster in cluster_events:
        if dry_run:
            logger.info(
                "dispatcher: [DRY-RUN] would POST cluster %s/%s (%d members: %s)",
                cluster.sector, cluster.direction, cluster.n,
                ",".join(m.ticker for m in cluster.members),
            )
            summary.clusters_dispatched += 1
            continue

        ok = _post_cluster_trigger(
            base_url=base_url, secret=secret,
            sector=cluster.sector, direction=cluster.direction,
            members=cluster.members, cluster_id=cluster.cluster_id,
        )
        if ok:
            summary.clusters_dispatched += 1
        else:
            summary.failures += 1

    # Then dispatch the singletons (existing path)
    to_dispatch = singleton_events

    # Opportunistic retention cleanup: run once per UTC date, on the first
    # tick after midnight ET / first tick of the day. Every other tick is a
    # no-op. Bounded by `DD_RETENTION_DAYS` (default 7). Runs server-side
    # via the admin /admin/dd-cleanup endpoint so we don't need DB access
    # here in the cron service.
    _maybe_run_daily_cleanup(base_url=base_url, secret=secret)

    for q in to_dispatch:
        summary.breaches.append({
            "ticker": q.ticker,
            "pct":    round(q.changes_percentage, 4),
            "price":  q.price,
        })
        if dry_run:
            logger.info(
                "dispatcher: [DRY-RUN] would POST trigger for %s pct=%+.2f%%",
                q.ticker, q.changes_percentage * 100,
            )
            summary.alerts_dispatched += 1
            continue

        ok = _post_trigger(
            base_url=base_url,
            secret=secret,
            ticker=q.ticker,
            pct=q.changes_percentage,
            price=q.price,
            tier=tier_label,
        )
        if ok:
            summary.alerts_dispatched += 1
        else:
            summary.failures += 1

    _emit_summary(summary)
    return 0


# ── Internals ───────────────────────────────────────────────────────────────


def _post_trigger(
    *, base_url: str, secret: str, ticker: str, pct: float, price: float, tier: str,
) -> bool:
    """POST to the web service's /admin/dd-trigger.

    Always uses agent_mode=real (the whole point of auto-dispatch is that
    the user gets a real LLM brief). Returns True on 200 + fired:true OR
    fired:false (cooldown is a healthy outcome — the alert engine
    correctly rejected a duplicate).
    """
    url = f"{base_url}/admin/dd-trigger"
    params = {
        "secret":     secret,
        "ticker":     ticker,
        "pct":        pct,
        "price":      price,
        "tier":       tier,
        "agent_mode": "real",
    }
    try:
        r = requests.post(url, params=params, timeout=HTTP_TIMEOUT_SEC)
    except requests.RequestException as exc:
        logger.error("dispatcher: POST failed for %s: %s", ticker, exc)
        return False

    if r.status_code != 200:
        logger.error("dispatcher: %s → HTTP %d: %s", ticker, r.status_code, r.text[:200])
        return False

    try:
        body = r.json()
    except Exception:
        logger.error("dispatcher: %s → non-JSON response: %s", ticker, r.text[:200])
        return False

    if body.get("fired"):
        logger.info(
            "dispatcher: %s FIRED  pct=%+.2f%%  reason=%s  run_id=%s",
            ticker, pct * 100, body.get("eligibility_reason"), body.get("dd_run_id", "?")[:8],
        )
    else:
        logger.info(
            "dispatcher: %s skipped (%s)",
            ticker, body.get("eligibility_reason", "unknown"),
        )
    return True


def _fetch_tier1_via_http(*, base_url: str) -> set[str]:
    """Pull Tier 1 (watchlist) tickers from the web service over HTTP.

    Designed to keep the cron service stateless — it never touches the
    SQLite DB directly. The web service exposes the watchlist via
    GET /api/dd-universe/tier1.

    Falls back to env-var (PORTFOLIO_TICKERS) if the HTTP call fails so a
    network blip doesn't blank the universe and silently disable monitoring.

    Returns set() if both HTTP and env are empty.
    """
    url = f"{base_url}/api/dd-universe/tier1"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT_SEC)
        r.raise_for_status()
        body = r.json()
        tickers = {t.strip().upper() for t in body.get("tickers", []) if t and t.strip()}
        logger.info("dispatcher: Tier 1 (watchlist) → %d tickers via HTTP", len(tickers))
        # Also union the env-var override (matches universe.get_watchlist_tickers's
        # behaviour for the in-process path).
        env_raw = os.environ.get(universe.ENV_PORTFOLIO_TICKERS, "")
        if env_raw.strip():
            extras = {p.strip().upper() for p in env_raw.replace(",", " ").split() if p.strip()}
            tickers |= extras
            logger.info("dispatcher: + %d env-var tickers", len(extras))
        return tickers
    except Exception as exc:
        logger.warning(
            "dispatcher: Tier 1 HTTP fetch failed (%s) — falling back to env-only", exc,
        )
        # Fallback: use env var only. This keeps monitoring alive if the
        # web service is briefly unreachable.
        env_raw = os.environ.get(universe.ENV_PORTFOLIO_TICKERS, "")
        if not env_raw.strip():
            return set()
        return {p.strip().upper() for p in env_raw.replace(",", " ").split() if p.strip()}


def _fetch_tier_2_3_inprocess() -> set[str]:
    """Build Tier 2 (analyzed) + Tier 3 (S&P 500) via the in-process
    universe helpers. Both are gated by env vars and default off.

    Tier 2 needs DB access (web_runs query); if the cron service can't
    reach the DB it returns empty (graceful degradation). Tier 3 hits
    FMP directly so it works regardless of DB sharing.
    """
    extra: set[str] = set()
    include_analyzed = _truthy(os.environ.get(universe.ENV_INCLUDE_ANALYZED, ""))
    include_sp500    = _truthy(os.environ.get(universe.ENV_INCLUDE_SP500, ""))

    if include_analyzed:
        analyzed = universe.get_analyzed_universe()
        logger.info("dispatcher: Tier 2 (analyzed last 90d) → %d tickers", len(analyzed))
        extra |= analyzed
    if include_sp500:
        sp500 = universe.get_sp500_universe()
        logger.info("dispatcher: Tier 3 (S&P 500) → %d tickers", len(sp500))
        extra |= sp500
    return extra


def _maybe_run_daily_cleanup(*, base_url: str, secret: str) -> None:
    """Hit /admin/dd-cleanup at most once per UTC day.

    Tracking is best-effort via a file in /tmp (Railway gives each service
    instance a writable /tmp). If the file is missing or stale, fire a
    cleanup. If it's today, no-op.

    Failure to write the marker file or contact the admin endpoint is
    logged but never raises — cleanup is "nice to have," not critical to
    the dispatch itself.
    """
    import tempfile
    from pathlib import Path

    marker = Path(tempfile.gettempdir()) / "dd_cleanup_last_utc_date.txt"
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        if marker.exists() and marker.read_text().strip() == today:
            return
    except OSError:
        pass   # fall through and try cleanup anyway

    url = f"{base_url}/admin/dd-cleanup"
    try:
        r = requests.post(url, params={"secret": secret}, timeout=HTTP_TIMEOUT_SEC)
        if r.status_code == 200:
            try:
                logger.info("dispatcher: daily cleanup → %s", r.json())
            except Exception:
                logger.info("dispatcher: daily cleanup HTTP 200 (non-JSON body)")
            try:
                marker.write_text(today)
            except OSError as exc:
                logger.warning("dispatcher: cleanup marker write failed: %s", exc)
        else:
            logger.warning("dispatcher: cleanup HTTP %d: %s", r.status_code, r.text[:200])
    except requests.RequestException as exc:
        logger.warning("dispatcher: cleanup POST failed: %s", exc)


def _post_cluster_trigger(
    *, base_url: str, secret: str, sector: str, direction: str,
    members: tuple, cluster_id: str,
) -> bool:
    """POST a sector cluster to /admin/dd-trigger-cluster on the web service.

    Always uses agent_mode=real. Members is the tuple from
    sector_clustering.Cluster.members (BatchQuote objects).
    Returns True on 200+fired:true; False on any failure.
    """
    url = f"{base_url}/admin/dd-trigger-cluster"
    member_tickers = ",".join(m.ticker for m in members)
    pcts_str       = ",".join(f"{m.changes_percentage:.6f}" for m in members)
    prices_str     = ",".join(f"{m.price:.6f}" for m in members)
    params = {
        "secret":     secret,
        "sector":     sector,
        "direction":  direction,
        "members":    member_tickers,
        "pcts":       pcts_str,
        "prices":     prices_str,
        "cluster_id": cluster_id,
        "agent_mode": "real",
    }
    try:
        r = requests.post(url, params=params, timeout=HTTP_TIMEOUT_SEC)
    except requests.RequestException as exc:
        logger.error("dispatcher: cluster POST failed for %s/%s: %s", sector, direction, exc)
        return False

    if r.status_code != 200:
        logger.error(
            "dispatcher: cluster %s/%s → HTTP %d: %s",
            sector, direction, r.status_code, r.text[:200],
        )
        return False

    try:
        body = r.json()
    except Exception:
        logger.error("dispatcher: cluster %s/%s → non-JSON response", sector, direction)
        return False

    if body.get("fired"):
        logger.info(
            "dispatcher: CLUSTER FIRED  %s/%s  (n=%d)  cluster_id=%s",
            sector, direction, len(members), body.get("cluster_id", "?")[:24],
        )
    else:
        logger.info(
            "dispatcher: cluster %s/%s skipped (%s)",
            sector, direction, body.get("note", "unknown"),
        )
    return True


def _emit_summary(summary: DispatchSummary) -> None:
    """Write the tick summary to stdout as JSON. Railway log aggregation
    can grep for this prefix to build dispatch-rate dashboards."""
    print(f"[dd_dispatcher_summary] {json.dumps(asdict(summary))}")


def _read_float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("Bad %s; using default %s", name, default)
        return default


def _read_int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning("Bad %s; using default %d", name, default)
        return default


def _truthy(s: str) -> bool:
    return s.strip().lower() in {"true", "1", "yes", "on", "y", "t"}


# Allow `python -m src.agents.dd.cron_dispatcher` invocation
if __name__ == "__main__":
    sys.exit(main())
