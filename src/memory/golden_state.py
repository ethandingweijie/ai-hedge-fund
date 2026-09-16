"""Reconstruct ``run_dcf_agent`` input state from an archived ``web_runs`` row.

Phase 0's golden suite replays the engine offline. Replay needs the same
``state["data"]`` the live run saw. The archived row carries most of it, but
three engine inputs are **not** persisted anywhere:

  ``end_date``            — recovered by joining ``runs.analysis_date`` on
                            ``web_runs.archive_run_id``.
  ``management_guidance`` — never archived. Replay runs without it, so the
                            guided-growth path is not covered by the golden
                            suite. Recorded here rather than papered over:
                            a guided name's year-1 growth will differ between
                            production and replay.
  ``sotp_assumptions``    — never archived (only the rendered
                            ``sotp_breakdown`` is). Same consequence for the
                            SOTP-led methods.

Frozen state is limited to LLM-only inputs, per the plan: research-derived
blocks, ``extractor_only`` KPI vectors, and profile/sector routing. The
deterministic KPIs are deliberately NOT read from the frozen row — replay
recomputes them from the recorded raw series so Phase 1.3's precedence flip
is exercised offline instead of being masked by stale metrics.

``framework_metrics_all`` is the archived extractor output. It mixes
LLM-extracted KPIs with ones Phase 1.3 makes deterministic. Filtering the
deterministic ones out here would pre-empt that phase and hide the very drift
it fixes, so the vector is passed through whole and the precedence flip is
what changes the answer.
"""
from __future__ import annotations

from typing import Any, Optional

#: state.data keys the engine reads that the archived row carries under the
#: SAME name.
_DIRECT_KEYS = (
    "sector",
    "sectors",
    "profile_names",
    "macro_regime",
    "segment_scenarios",
    "pipeline_assets",
    "reit_metrics",
    "bank_metrics",
    "saas_metrics",
    "insider_activity",
    "deep_research",
    "industry_brief",
    "sector_confidence",
    "sector_warning",
)

#: Archived key -> engine state key. The pipeline persists the per-ticker
#: maps under ``*_all``; the engine reads the un-suffixed name and falls back
#: to ``*_all`` only for a few. Pass both so either lookup resolves.
_ALIASES = {
    "framework_metrics_all": "framework_metrics",
    "pipeline_assets_all": "pipeline_assets",
    "reit_metrics_all": "reit_metrics",
    "bank_metrics_all": "bank_metrics",
    "saas_metrics_all": "saas_metrics",
}


def build_state(ticker: str,
                archived: dict,
                end_date: str,
                api_key: Optional[str] = None) -> dict:
    """Build a ``run_dcf_agent`` state from an archived run.

    ``archived`` is the parsed ``full_result_json`` ``data`` block (what
    ``web_run.json`` stores). Returns a fresh state dict; the engine mutates
    it in place, so never share one across runs.
    """
    data: dict[str, Any] = {}

    for key in _DIRECT_KEYS:
        if key in archived and archived[key] is not None:
            data[key] = archived[key]

    for src, dst in _ALIASES.items():
        val = archived.get(src)
        if val:
            # Prefer the specific map; keep the *_all form too because some
            # engine paths read it directly.
            data.setdefault(dst, val)
            data[src] = val

    # The dcf_calibration payload key is vestigial in the archive (the engine
    # reads dcf_calibration_signals). Carry it under both names so a replay
    # resolves whichever the engine asks for.
    cal = archived.get("dcf_calibration_signals") or archived.get("dcf_calibration")
    if cal:
        data["dcf_calibration_signals"] = cal

    data["tickers"] = [ticker]
    data["end_date"] = end_date
    data.setdefault("sector", archived.get("sector") or "Tech")
    data.setdefault("tickers", [ticker])

    # Profile routing is an LLM/classifier output — frozen, not recomputed.
    profile = archived.get("profile_name")
    if profile:
        data.setdefault("profile_names", {})[ticker] = profile

    state = {
        "data": data,
        "metadata": {},
    }
    if api_key:
        state["metadata"]["api_key"] = api_key
    return state


def extract_dcf_entry(archived: dict, ticker: str) -> Optional[dict]:
    """The production ``dcf_range[ticker]`` for comparison, or None."""
    return (archived.get("dcf_range") or {}).get(ticker) or None


#: Neutral regime, matching tests/test_dcf_fixtures.py so C_macro == 0.0 and
#: the recorded baseline is not a function of the day's macro print.
NEUTRAL_REGIME = {
    "risk_appetite": "neutral",
    "rate_direction": "neutral",
    "volatility_regime": "medium",
}


def build_state_from_lookups(ticker: str, end_date: str,
                             api_key: Optional[str] = None) -> dict:
    """Build state for a ticker with no archived production run.

    Five basket tickers (AAPL, V, SCHW, C38U.SI, FCX) have never been run in
    production, so there is no ``web_runs`` row to freeze LLM inputs from.
    They are recorded from their curated routing instead: sector and profile
    come from ``get_wacc_profile_for_ticker``, which is the same Guardrail-4
    lookup the engine applies over any pre-classified profile, so seeding it
    changes which layer wins the trace but not the resolved pair.

    These fixtures therefore carry **no** LLM-derived inputs — no SOTP
    assumptions, no framework KPI vector, no guidance. That is a real coverage
    limit, recorded rather than hidden: a golden move on these five is
    attributable to the deterministic engine only.
    """
    from src.data.sector_profiles import get_wacc_profile_for_ticker

    sector, profile = get_wacc_profile_for_ticker(ticker)
    data: dict[str, Any] = {
        "tickers": [ticker],
        "end_date": end_date,
        "sector": sector,
        "sectors": {ticker: sector},
        "macro_regime": dict(NEUTRAL_REGIME),
    }
    if profile:
        data["profile_names"] = {ticker: profile}

    state = {"data": data, "metadata": {}}
    if api_key:
        state["metadata"]["api_key"] = api_key
    return state
