"""
test_pipeline_e2e.py — End-to-end pipeline test per ticker.

NOTE: Forces UTF-8 stdout/stderr at the top because Windows defaults to cp1252
which crashes the moment Qwen returns a `≤`/`→`/`✓` character. Without this,
Tier 1 fails with UnicodeEncodeError mid-stream and falls through to Tier 2
Tavily (slower + burns Tavily quota).

5-step scenario:
  1. Ticker → sector router → (sector, profile_name)
  2. FMP API → raw_financials + market data
  3. Deep research → 4 web searches → Section 2F (1500-word preview)
  4. Extractor → sector-specific KPI metrics dict
  5. Parse FMP + metrics → DCF computation → IV + upside

Output:
  - Per-ticker bundle:  pipeline_<TICKER>_<ts>/  (section_2f.txt, metrics.json, dcf.json)
  - Aggregate table:    pipeline_e2e.md  (one row per ticker, all 5 steps)
  - Aggregate JSON:     pipeline_e2e.json

USAGE (PowerShell):
    poetry run python test_pipeline_e2e.py PGR JPM NEM NVDA LMT MCD
    poetry run python test_pipeline_e2e.py PGR --searches 4 --words 1500
"""
from __future__ import annotations

# ── FORCE UTF-8 STDOUT/STDERR FIRST (before any other import) ────────────────
# Windows cp1252 (the default Python IO codec on Windows) cannot encode Unicode
# characters that Qwen returns (≤, →, ✓, etc.). Without this, Tier 1 crashes
# mid-print with UnicodeEncodeError and the run wastes time falling through.
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any


# ── Step 0: Load env vars from .env / .env.local ─────────────────────────────
def _load_env():
    here = Path(__file__).resolve().parent
    main_repo = Path(r"C:\Users\ethan\Documents\Projects\AI Hedge Fund")
    for parent in [here, main_repo]:
        for fname in (".env.local", ".env"):
            p = parent / fname
            if p.exists():
                for line in p.read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if v and not os.environ.get(k):
                        os.environ[k] = v
_load_env()


# ── Suppress citation_registry + all post-synthesis extractor LLM calls ──────
# We call them ourselves later in Step 4. Skipping them inside _research_one_ticker
# avoids the noisy DashScope 401s and ~5-15s of redundant LLM calls per ticker.
def _patch_isolate_section_2f():
    import src.agents.industry.deep_research as _dr
    _dr._extract_citation_registry = lambda *a, **kw: []
    _dr._extract_dcf_calibration   = lambda *a, **kw: {
        "growth_rate_adj": None, "margin_direction": "stable", "risk_flag": ""}
    _dr._extract_segment_scenarios = lambda *a, **kw: {}
    _dr._extract_pipeline_assets   = lambda *a, **kw: []
    _dr._extract_reit_metrics      = lambda *a, **kw: {}
    _dr._extract_bank_metrics      = lambda *a, **kw: {}
    _dr._extract_saas_metrics      = lambda *a, **kw: {}
    _dr._extract_insurance_metrics = lambda *a, **kw: {}
    if hasattr(_dr, "_build_news_supplement"):
        _dr._build_news_supplement = lambda *a, **kw: ""


# ── FMP wrapper ──────────────────────────────────────────────────────────────
_FMP_BASE = "https://financialmodelingprep.com/stable"


def _fmp(path: str, ticker: str, timeout: int = 8) -> Any:
    key = os.environ.get("FMP_API_KEY") or "UFPUuQjTht66l2GmJhQbUZzij7IfJbsx"
    url = f"{_FMP_BASE}/{path}?symbol={urllib.parse.quote(ticker)}&apikey={key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "test_pipeline_e2e/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def step_2_fmp(ticker: str) -> dict:
    """Step 2: FMP API → raw_financials + market data."""
    profile = (_fmp("profile", ticker) or [{}])[0]
    ratios  = (_fmp("ratios-ttm", ticker) or [{}])[0]
    keymet  = (_fmp("key-metrics-ttm", ticker) or [{}])[0]
    income  = (_fmp("income-statement", ticker) or [{}])[0]
    # Field map (verified against FMP /stable/ on Apr 2026):
    #   bvps / tbvps  → ratios-ttm  (NOT key-metrics-ttm)
    #   marketCap     → key-metrics-ttm or profile
    #   eps           → key-metrics-ttm.netIncomePerShareTTM
    return {
        "company_name":  profile.get("companyName"),
        "current_price": profile.get("price"),
        "market_cap":    keymet.get("marketCap") or profile.get("mktCap"),
        "shares_out":    keymet.get("commonSharesOutstanding") or profile.get("shareNumber"),
        "bvps":          ratios.get("bookValuePerShareTTM"),
        "tbvps":         ratios.get("tangibleBookValuePerShareTTM"),
        "eps":           keymet.get("netIncomePerShareTTM") or income.get("eps"),
        "ebitda":        income.get("ebitda"),
        "fcf_per_share": ratios.get("freeCashFlowPerShareTTM") or keymet.get("freeCashFlowPerShareTTM"),
        "rev_per_share": ratios.get("revenuePerShareTTM") or keymet.get("revenuePerShareTTM"),
        "pe_ttm":        ratios.get("priceToEarningsRatioTTM"),
        "pb_ttm":        ratios.get("priceToBookRatioTTM"),
        "ev_ebitda_ttm": ratios.get("enterpriseValueMultipleTTM"),
    }


# ── Step 1: Sector router (TICKER_SECTOR_LOOKUP) ─────────────────────────────
def step_1_router(ticker: str) -> tuple[str, str] | None:
    from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
    lookup = TICKER_SECTOR_LOOKUP.get(ticker)
    if not lookup:
        return None
    return (lookup[0], lookup[1])


# ── Step 3: Deep research (web run only, Section 2F focus) ────────────────────
def step_3_research(ticker: str, sector: str, profile_name: str,
                    raw_financials: dict, n_searches: int) -> dict:
    """Step 3: deep research with N web searches. Returns the raw result dict
    from _research_one_ticker (deep_research_sections has Section 2F)."""
    import src.agents.industry.deep_research as _dr
    _dr.MAX_SEARCHES = n_searches  # cap the search budget

    # Pick the right routing: Qwen if DEEP_RESEARCH_API_KEY set, else Anthropic
    qwen_key = os.environ.get("DEEP_RESEARCH_API_KEY")
    anth_key = os.environ.get("ANTHROPIC_API_KEY")
    if qwen_key:
        api_key = qwen_key
        model_name = os.environ.get("DEEP_RESEARCH_MODEL", "qwen3.6-plus")
        base_url = os.environ.get("DEEP_RESEARCH_BASE_URL")
        synthesis_model = os.environ.get("DEEP_RESEARCH_SYNTHESIS_MODEL") or model_name
    elif anth_key:
        api_key = anth_key
        model_name = "claude-sonnet-4-6"
        base_url = None
        synthesis_model = None
    else:
        return {"error": "no DEEP_RESEARCH_API_KEY or ANTHROPIC_API_KEY"}

    from src.agents.industry.deep_research import _research_one_ticker
    return _research_one_ticker(
        ticker=ticker,
        sector=sector,
        end_date=date.today().isoformat(),
        anthropic_key=api_key,
        model_name=model_name,
        raw_financials=raw_financials or {},
        insider_summary="",
        base_url=base_url,
        synthesis_model=synthesis_model,
        profile_name=profile_name,
    )


# ── Step 4: Extractor — sector-specific KPI metrics ──────────────────────────
def step_4_extractor(ticker: str, profile_name: str, sections: dict, final_report: str) -> dict:
    """Step 4: run the framework extractor on the deep research output to
    pull out the sector-specific KPIs (NRR, combined_ratio, AISC, etc.)."""
    qwen_key = os.environ.get("DEEP_RESEARCH_API_KEY")
    anth_key = os.environ.get("ANTHROPIC_API_KEY")
    api_key = qwen_key or anth_key
    if not api_key:
        return {"error": "no API key for extractor"}
    base_url = os.environ.get("DEEP_RESEARCH_BASE_URL") if qwen_key else None
    model_name = (os.environ.get("DEEP_RESEARCH_SYNTHESIS_MODEL")
                  or os.environ.get("DEEP_RESEARCH_MODEL")
                  or "claude-sonnet-4-6")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=120.0, max_retries=1)

    from src.data.sector_kpi_framework import (
        SECTOR_KPI_FRAMEWORK, extract_via_framework, is_legacy_profile,
    )
    if profile_name not in SECTOR_KPI_FRAMEWORK:
        return {"error": f"profile {profile_name!r} not in framework"}
    if is_legacy_profile(profile_name):
        # Use the legacy hand-written extractor for these
        from src.agents.industry import deep_research as _dr
        if profile_name in ("Growth SaaS", "Mature SaaS", "Hyperscaler",
                            "Cybersecurity / Mission-Critical SaaS"):
            return _dr._extract_saas_metrics(client, model_name, sections, final_report, ticker)
        if profile_name == "REIT":
            return _dr._extract_reit_metrics(client, model_name, sections, final_report, ticker)
        return {}
    return extract_via_framework(client, model_name, sections, final_report, ticker, profile_name=profile_name)


# ── Step 5: DCF / anchor-method valuation ────────────────────────────────────
def step_5_dcf(ticker: str, sector: str, profile_name: str,
               fmp: dict, metrics: dict) -> dict:
    """Step 5: per-anchor-method valuation via the V3 valuate_ticker entry
    point so quality/risk/commodity multipliers from SECTOR_KPI_FRAMEWORK fire.

    BUG FIX (audit): old impl iterated value_per_share() directly without ever
    calling composite_adjustment(), so the V3 audit_bridge was lost on every
    test_pipeline_e2e.py run. valuate_ticker() is the canonical V3 entry that
    1) iterates anchor_methods, 2) takes median, 3) applies composite kicker,
    4) returns the audit_bridge dict.
    """
    from valuation_check import valuate_ticker
    r = valuate_ticker(ticker, sector_metrics=metrics)
    return {
        "method_results": r.get("method_results", []),
        "pre_kicker_iv": r.get("pre_kicker_iv"),
        "primary_iv":    r.get("primary_iv"),
        "bear_iv":       r.get("bear_iv"),
        "bull_iv":       r.get("bull_iv"),
        "upside_pct":    r.get("upside_pct"),
        "audit_bridge":  r.get("audit_bridge"),  # V3: Quality x Risk x Commodity
    }


# ── Per-ticker driver ─────────────────────────────────────────────────────────
def run_one(ticker: str, n_searches: int, n_words: int) -> dict:
    print(f"\n{'='*72}\nTICKER: {ticker}\n{'='*72}")
    t0 = time.time()
    out: dict = {"ticker": ticker}

    # Step 1
    print(f"  [1/5] Router  ...")
    rt = step_1_router(ticker)
    if not rt:
        out["error"] = "ticker not in TICKER_SECTOR_LOOKUP"
        return out
    sector, profile_name = rt
    out.update(sector=sector, profile_name=profile_name)
    print(f"        sector={sector!r}, profile={profile_name!r}")

    # Step 2
    print(f"  [2/5] FMP     ...")
    fmp = step_2_fmp(ticker)
    out["fmp"] = fmp
    def _f(v, fmt=""):
        if v is None: return "—"
        try: return f"{v:{fmt}}" if fmt else str(v)
        except Exception: return str(v)
    print(f"        ${_f(fmp.get('current_price'))} cap=${_f(fmp.get('market_cap'),',')} "
          f"bvps={_f(fmp.get('bvps'))} eps={_f(fmp.get('eps'))} ebitda={_f(fmp.get('ebitda'),',')}")

    # Step 3 (the expensive one)
    print(f"  [3/5] Deep research ({n_searches} searches)...")
    t3 = time.time()
    research = step_3_research(ticker, sector, profile_name, {ticker: fmp}, n_searches)
    out["research_elapsed_s"] = round(time.time() - t3, 1)
    if "error" in research:
        out["research_error"] = research["error"]
        return out
    sections = research.get("deep_research_sections") or {}
    final_report = research.get("deep_research") or ""
    section_2f = sections.get("2f") or sections.get("2F") or ""
    out["research_tier"] = research.get("research_tier")
    out["section_2f_chars"] = len(section_2f)
    out["section_2f_words"] = len(section_2f.split())
    print(f"        tier={out['research_tier']}, 2F chars={out['section_2f_chars']:,}, "
          f"words={out['section_2f_words']:,}, elapsed={out['research_elapsed_s']}s")

    # Step 4
    print(f"  [4/5] Extractor ...")
    t4 = time.time()
    metrics = step_4_extractor(ticker, profile_name, sections, final_report)
    out["extractor_elapsed_s"] = round(time.time() - t4, 1)
    out["metrics"] = metrics
    n_kpis = sum(1 for k in metrics.keys() if not str(k).startswith("_"))
    print(f"        {n_kpis} KPIs extracted, elapsed={out['extractor_elapsed_s']}s")
    for k, v in list(metrics.items())[:8]:
        if not str(k).startswith("_"):
            print(f"          {k} = {v}")

    # Step 5
    print(f"  [5/5] DCF / Anchor valuation ...")
    dcf = step_5_dcf(ticker, sector, profile_name, fmp, metrics)
    out["dcf"] = dcf
    print(f"        primary_IV=${dcf['primary_iv']}, upside={dcf['upside_pct']}%")
    for mr in dcf["method_results"]:
        iv_str = f"${mr['iv']}" if mr.get("iv") is not None else "—"
        print(f"          {mr['method']:25s}: {iv_str}")

    # Bundle output
    bundle = Path(f"pipeline_{ticker}_{int(time.time())}")
    bundle.mkdir(exist_ok=True)
    if section_2f:
        (bundle / "section_2f.txt").write_text(section_2f, encoding="utf-8")
    (bundle / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    (bundle / "dcf.json").write_text(json.dumps(dcf, indent=2, default=str), encoding="utf-8")
    (bundle / "fmp.json").write_text(json.dumps(fmp, indent=2, default=str), encoding="utf-8")
    out["bundle"] = str(bundle)

    # Section 2F preview (first n_words)
    if section_2f:
        words = section_2f.split()
        preview = " ".join(words[:n_words])
        print(f"\n  --- Section 2F preview ({min(n_words, len(words))} of {len(words)} words) ---")
        print(preview[:3000])  # cap console output too
        if len(words) > n_words:
            print(f"  [... {len(words)-n_words} more words in {bundle}/section_2f.txt]")

    out["total_elapsed_s"] = round(time.time() - t0, 1)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*", default=["PGR"])
    parser.add_argument("--searches", type=int, default=4)
    parser.add_argument("--words", type=int, default=1500)
    parser.add_argument("--out-md", default="")
    parser.add_argument("--out-json", default="")
    args = parser.parse_args()

    _patch_isolate_section_2f()
    print(f"Patched: skipping citation_registry + extractors INSIDE deep_research "
          f"(we run our own extractor in Step 4)")

    t0 = time.time()
    rows = []
    for t in args.tickers:
        try:
            rows.append(run_one(t.upper(), args.searches, args.words))
        except Exception as e:
            print(f"  FATAL {t}: {type(e).__name__}: {e}")
            rows.append({"ticker": t.upper(), "error": str(e)})

    elapsed = time.time() - t0
    # Default output filename: derived from tickers when run in parallel
    out_md   = args.out_md   or f"pipeline_{'_'.join(args.tickers).upper()}.md"
    out_json = args.out_json or f"pipeline_{'_'.join(args.tickers).upper()}.json"
    json.dump({"rows": rows, "elapsed_sec": elapsed},
              open(out_json, "w"), indent=2, default=str)

    # Markdown table
    md = ["# Pipeline E2E Test — Section 2F web run + Extractor + DCF", "",
          f"_Elapsed: {elapsed:.0f}s · {len(args.tickers)} tickers · "
          f"searches={args.searches} · words={args.words}_", "",
          "| # | Ticker | Sector / Profile | Tier | 2F words | KPIs | Current | Primary IV | Upside | Bundle |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        if "error" in r:
            md.append(f"| {i} | {r['ticker']} | — | — | — | — | — | — | — | {r['error']} |")
            continue
        n_kpis = sum(1 for k in (r.get("metrics") or {}).keys() if not str(k).startswith("_"))
        cur = r.get("fmp", {}).get("current_price")
        dcf = r.get("dcf", {})
        md.append(
            f"| {i} | {r['ticker']} | {r.get('sector','?')} / {r.get('profile_name','?')} | "
            f"{r.get('research_tier','?')} | {r.get('section_2f_words',0):,} | {n_kpis} | "
            f"${cur} | ${dcf.get('primary_iv','—')} | "
            f"{dcf.get('upside_pct','—')}% | {r.get('bundle','—')} |"
        )

    open(out_md, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"\n  Markdown -> {out_md}")
    print(f"  JSON     -> {out_json}")
    print(f"  Total elapsed: {elapsed:.0f}s")


if __name__ == "__main__":
    main()
