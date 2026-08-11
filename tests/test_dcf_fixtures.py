"""Stage 5 / D6 — deterministic DCF fixture tests, one profile per family.

The DCF engine (``run_dcf_agent``) makes no LLM calls — everything it needs
arrives through six data seams. This module monkeypatches those seams with
hand-built annual line items for one representative profile per valuation
family (SaaS/cyber, money-center bank, REIT, pre-approval biotech, insurance,
BTC miner, BTC treasury, crypto exchange) and asserts the durable contract of
the multi-method engine:

  * ``dcf_range[ticker]["profile"]`` == the pre-classified profile
  * base scenario ``methods_used`` non-empty + profile anchor fired
  * base ``intrinsic_value`` > 0
  * ``profile_fallback_used`` is False (D3 loud-degradation flag)
  * ``methods_unavailable`` matches the per-case expectation (D3)
  * ``calibration_note`` is a string — the T-1 backward gate ran or skipped
    cleanly (never crashed)

Discovered while building these fixtures (fixed in dcf_agent.py):
``research_and_development`` was requested in ``search_line_items`` and listed
in ``_FX_MONETARY`` but never copied into the annual-series rows, so the
EV/R&D method (25% weight in Pre-approval Biotech) could never fire.
``test_rd_field_copied_into_series`` guards that regression.

Seams patched (``src.agents.analysis.dcf_agent`` namespace unless noted):
  search_line_items, get_prices, get_analyst_estimates, get_fx_rate,
  get_revenue_product_segmentation, get_price_target_consensus, and
  ``src.tools.api._fmp_get`` (resolved at call time by the inline quote import).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.analysis import dcf_agent
from src.agents.analysis.dcf_agent import _extract_annual_series, run_dcf_agent
from src.tools import api as _api

END_DATE = "2026-06-30"


# ── Line-item builder ─────────────────────────────────────────────────────────
# _extract_annual_series reads every field via getattr — SimpleNamespace rows
# with a complete attribute set reproduce FMP LineItem objects faithfully.

_LI_FIELDS = (
    "report_period", "currency", "revenue", "free_cash_flow",
    "shares_outstanding", "debt_to_equity", "net_debt", "total_debt",
    "ebitda", "net_income", "total_assets", "total_equity",
    "dividends_per_share", "book_value_per_share", "capital_expenditure",
    "ebit", "interest_expense", "invested_capital",
    "research_and_development", "stock_based_compensation",
    "depreciation_and_amortization", "operating_cash_flow",
    "cash_and_equivalents", "interest_income", "provision_for_loan_losses",
    "goodwill", "intangible_assets", "tangible_book_value_per_share",
    "total_liabilities", "operating_expense", "operating_income",
    "loans_receivable", "loans_held_for_investment", "total_deposits",
    "share_buyback", "common_stock_repurchased", "gross_profit",
    "cost_of_revenue",
)


def _li(**kw) -> SimpleNamespace:
    vals = {f: kw.get(f) for f in _LI_FIELDS}
    vals.update(kw)
    return SimpleNamespace(**vals)


def _mk_rows(revs: list[float], **fields) -> list[SimpleNamespace]:
    """Build annual rows oldest→newest over FY2022–FY2025 (reversed to the
    newest-first order FMP returns). Each field value may be a scalar
    (constant across years) or a list aligned with ``revs``."""
    n = len(revs)
    rows = []
    for i in range(n):
        kw = {
            "report_period": f"{2022 + i}-12-31",
            "currency": "USD",
            "revenue": revs[i],
        }
        for f, v in fields.items():
            kw[f] = v[i] if isinstance(v, (list, tuple)) else v
        rows.append(_li(**kw))
    rows.reverse()
    return rows


# ── Seam-patching fixture ─────────────────────────────────────────────────────

@pytest.fixture
def seams(monkeypatch):
    """Patch every external data seam the DCF engine touches. Tests fill the
    returned registry per ticker; nothing reaches the network."""
    reg: dict = {"rows": {}, "price": {}, "mcap": {}, "estimates": {}}

    def _search_line_items(ticker, fields, end_date, period=None, limit=None,
                           api_key=None):
        return reg["rows"].get(ticker, [])

    def _get_prices(ticker, start_date, end_date, api_key=None):
        return [SimpleNamespace(close=reg["price"].get(ticker, 100.0))]

    def _get_estimates(ticker, end_date, period=None, limit=None, api_key=None):
        return reg["estimates"].get(ticker, [])

    def _fmp_get(path, params, api_key):
        # Inline quote import in run_dcf_agent resolves src.tools.api._fmp_get
        # at call time, so patching here covers the /stable/quote seam.
        symbol = (params or {}).get("symbol")
        price = reg["price"].get(symbol)
        if price is None:
            return None
        return [{
            "symbol": symbol,
            "price": price,
            "yearHigh": price * 1.2,
            "yearLow": price * 0.8,
            "priceAvg50": price,
            "priceAvg200": price * 0.9,
            "marketCap": reg["mcap"].get(symbol, price * 1e8),
        }]

    monkeypatch.setattr(dcf_agent, "search_line_items", _search_line_items)
    monkeypatch.setattr(dcf_agent, "get_prices", _get_prices)
    monkeypatch.setattr(dcf_agent, "get_analyst_estimates", _get_estimates)
    monkeypatch.setattr(dcf_agent, "get_fx_rate", lambda *a, **k: 1.0)
    monkeypatch.setattr(
        dcf_agent, "get_revenue_product_segmentation", lambda *a, **k: [])
    monkeypatch.setattr(
        dcf_agent, "get_price_target_consensus", lambda *a, **k: None)
    monkeypatch.setattr(_api, "_fmp_get", _fmp_get)
    return reg


def _state(ticker: str, sector: str, profile: str, extra: dict | None = None) -> dict:
    data = {
        "tickers": [ticker],
        "end_date": END_DATE,
        "sector": sector,
        # Pre-classified profile (strategic_router path) — the fixture controls
        # classification so each case exercises exactly one profile's methods.
        "profile_names": {ticker: profile},
        "macro_regime": {
            "risk_appetite": "neutral",
            "rate_direction": "neutral",
            "volatility_regime": "medium",
        },  # → C_macro = 0.0
    }
    if extra:
        data.update(extra)
    return {"data": data, "metadata": {}}


# ── Per-family case builders ──────────────────────────────────────────────────

def _case_cyber_saas() -> dict:
    """Tech / Cybersecurity — DCF-family + EV/Revenue + P/E + EV/EBITDA all
    fire from positive FCF, NI and EBITDA."""
    revs = [2.2e9, 2.6e9, 3.0e9, 3.4e9]
    rows = _mk_rows(
        revs,
        free_cash_flow=[r * 0.16 for r in revs],
        net_income=[r * 0.10 for r in revs],
        ebitda=[r * 0.20 for r in revs],
        ebit=[r * 0.16 for r in revs],
        stock_based_compensation=[r * 0.08 for r in revs],
        capital_expenditure=[r * 0.05 for r in revs],
        gross_profit=[r * 0.78 for r in revs],
        shares_outstanding=2.5e8,
        net_debt=2.0e8, total_debt=6.0e8, cash_and_equivalents=4.0e8,
        total_equity=1.6e9, total_assets=3.0e9, invested_capital=1.5e9,
        book_value_per_share=6.4, debt_to_equity=0.4,
        interest_expense=2.0e7, dividends_per_share=0.0,
    )
    return dict(rows=rows, price=350.0, mcap=350.0 * 2.5e8,
                anchor="DCF (FCF+)", unavailable=[])


def _case_money_center_bank() -> dict:
    """Financials / Money Center Bank — Residual Income, P/TBV, P/E (norm),
    Excess Capital all fire from NI/equity/BVPS/TBV/asset data."""
    revs = [1.50e11, 1.56e11, 1.63e11, 1.70e11]
    rows = _mk_rows(
        revs,
        net_income=[5.0e10] * 4,
        interest_income=[1.75e11] * 4,
        interest_expense=[8.8e10] * 4,
        operating_expense=[7.0e10] * 4,
        provision_for_loan_losses=[6.0e9] * 4,
        total_assets=[3.9e12] * 4,
        total_equity=[3.2e11] * 4,
        total_liabilities=[3.58e12] * 4,
        goodwill=[5.2e10] * 4,
        intangible_assets=[1.0e10] * 4,
        tangible_book_value_per_share=[110.0] * 4,   # FMP /stable/ratios direct
        book_value_per_share=[118.0] * 4,
        loans_receivable=[1.3e12] * 4,
        total_deposits=[2.4e12] * 4,
        share_buyback=[3.0e10] * 4,
        shares_outstanding=2.9e9,
        dividends_per_share=4.6,
        net_debt=None, total_debt=3.0e11,
        debt_to_equity=0.9,
        ebitda=None, ebit=None, free_cash_flow=None,   # banks: n/a
    )
    return dict(rows=rows, price=280.0, mcap=280.0 * 2.9e9,
                anchor="Residual Income", unavailable=[])


def _case_reit() -> dict:
    """RealEstate / REIT — NAV (Cap Rates), P/FFO, P/AFFO, DDM all fire from
    D&A-reconstructed FFO, EBITDA-proxied NOI and a covered dividend."""
    revs = [4.2e9, 4.4e9, 4.6e9, 4.8e9]
    rows = _mk_rows(
        revs,
        net_income=[1.1e9] * 4,
        depreciation_and_amortization=[-1.5e9] * 4,
        operating_cash_flow=[2.5e9] * 4,
        capital_expenditure=[-8.0e8] * 4,
        ebitda=[2.9e9] * 4,               # NOI proxy
        ebit=[1.4e9] * 4,
        free_cash_flow=[1.7e9] * 4,
        total_debt=[1.0e10] * 4,
        cash_and_equivalents=[3.0e8] * 4,
        net_debt=9.7e9,
        total_equity=[8.0e9] * 4,
        total_assets=[2.5e10] * 4,
        shares_outstanding=6.0e8,
        book_value_per_share=13.3,
        dividends_per_share=3.1,          # < AFFO/sh (3.17) → DDM not capped
        debt_to_equity=1.25,
    )
    return dict(rows=rows, price=55.0, mcap=55.0 * 6.0e8,
                anchor="NAV (Cap Rates)", unavailable=[])


def _case_preapproval_biotech() -> dict:
    """Biopharma / Pre-approval Biotech — rNPV (pipeline assets in state),
    EV/R&D (R&D spend), Pipeline NAV via P/BV proxy, Cash Runway (net cash)."""
    revs = [3.0e7, 4.0e7, 5.0e7, 6.0e7]   # early commercial revenue (>0, <$10M cap n/a)
    rows = _mk_rows(
        revs,
        free_cash_flow=[-3.0e7] * 4,
        net_income=[-1.2e8] * 4,
        ebitda=[-1.0e8] * 4,
        ebit=[-1.1e8] * 4,
        research_and_development=[1.5e8] * 4,
        shares_outstanding=1.0e8,
        net_debt=-5.0e8,                  # net cash → Cash Runway fires
        cash_and_equivalents=[5.0e8] * 4,
        total_debt=0.0,
        total_equity=[5.5e8] * 4,
        total_assets=[7.0e8] * 4,
        book_value_per_share=5.5,
        debt_to_equity=0.0,
    )
    pipeline = [
        {"name": "fixelabart", "phase": "Phase 3", "indication": "oncology",
         "peak_sales_usd": 2.0e9, "launch_year": 2028},
        {"name": "fixelimab", "phase": "Phase 2", "indication": "immunology",
         "peak_sales_usd": 8.0e8},
    ]
    return dict(rows=rows, price=12.0, mcap=12.0 * 1.0e8,
                anchor="rNPV", unavailable=[],
                extra={"pipeline_assets": {"FIXBT": pipeline}})


def _case_insurance() -> dict:
    """Financials / Insurance — Embedded Value + Combined Ratio Gate fire via
    framework-metrics attach; P/BV, P/E (ops), DDM from line items."""
    revs = [2.8e10, 2.93e10, 3.06e10, 3.2e10]
    rows = _mk_rows(
        revs,
        net_income=[3.0e9] * 4,
        total_equity=[2.5e10] * 4,
        total_assets=[1.1e11] * 4,
        shares_outstanding=3.0e8,
        book_value_per_share=83.3,
        dividends_per_share=2.5,
        net_debt=None, total_debt=2.0e10,
        debt_to_equity=0.8,
        ebitda=None, ebit=None, free_cash_flow=None,
    )
    framework = {
        "combined_ratio": 0.94,               # solid underwriter → 1.10× P/BV
        "embedded_value_per_share": 95.0,     # Life insurer IR disclosure
        "vnb_margin": 0.22,
        "solvency_ratio_scr": 2.1,
    }
    return dict(rows=rows, price=90.0, mcap=90.0 * 3.0e8,
                anchor="Embedded Value", unavailable=[],
                extra={"framework_metrics": {"FIXIN": framework}})


def _case_btc_miner() -> dict:
    """Crypto / Digital Asset Mining — P/BV anchors; hashprice-cyclical EBITDA
    exercises the normalised EV/EBITDA; EV/Revenue + DCF complete the set."""
    revs = [5.0e8, 6.5e8, 7.5e8, 8.5e8]
    rows = _mk_rows(
        revs,
        ebitda=[1.0e8, 2.8e8, 1.5e8, 3.2e8],        # cyclical → norm matters
        net_income=[5.0e7, 2.0e8, -1.0e8, 2.2e8],
        ebit=[6.0e7, 2.2e8, 9.0e7, 2.6e8],
        free_cash_flow=[r * 0.12 for r in revs],
        stock_based_compensation=[r * 0.04 for r in revs],
        shares_outstanding=2.0e8,
        book_value_per_share=8.0,
        total_equity=[1.6e9] * 4,
        total_assets=[2.6e9] * 4,
        invested_capital=2.0e9,
        net_debt=-8.0e8,                            # BTC treasury + fleet = net cash
        cash_and_equivalents=[9.0e8] * 4,
        total_debt=1.0e8,
        debt_to_equity=0.1,
    )
    return dict(rows=rows, price=18.0, mcap=18.0 * 2.0e8,
                anchor="P/BV", unavailable=[])


def _case_btc_treasury() -> dict:
    """Crypto / BTC Treasury / Proxy — NAV Discount (mNAV proxy via book ×
    peer premium) + EV/EBITDA, DCF, EV/Revenue for the operating business."""
    revs = [4.6e8, 4.8e8, 5.0e8, 5.2e8]             # flat software business
    rows = _mk_rows(
        revs,
        ebitda=[1.5e8] * 4,
        net_income=[8.0e7] * 4,
        ebit=[1.1e8] * 4,
        free_cash_flow=[1.0e8] * 4,
        shares_outstanding=3.0e7,
        book_value_per_share=133.0,                 # GAAP book (BTC at cost)
        total_equity=[4.0e9] * 4,
        total_assets=[7.0e9] * 4,
        invested_capital=5.0e9,
        net_debt=5.0e8,                             # converts outstanding
        total_debt=1.5e9,
        cash_and_equivalents=1.0e9,
        debt_to_equity=0.4,
    )
    return dict(rows=rows, price=400.0, mcap=400.0 * 3.0e7,
                anchor="NAV Discount", unavailable=[])


def _case_crypto_exchange() -> dict:
    """Crypto / Crypto Exchange — EV/EBITDA, EV/Revenue, Forward P/E (analyst
    consensus fixture), P/E (norm). Estimates kept below the 3-analyst band
    threshold so growth stays on the deterministic consensus point estimate."""
    revs = [4.0e9, 5.0e9, 5.5e9, 6.2e9]
    rows = _mk_rows(
        revs,
        ebitda=[r * 0.38 for r in revs],
        net_income=[r * 0.24 for r in revs],
        ebit=[r * 0.30 for r in revs],
        free_cash_flow=[r * 0.29 for r in revs],
        stock_based_compensation=[r * 0.05 for r in revs],
        shares_outstanding=2.6e8,
        book_value_per_share=34.6,
        total_equity=[9.0e9] * 4,
        total_assets=[1.6e10] * 4,
        invested_capital=1.0e10,
        net_debt=-3.0e9,
        cash_and_equivalents=[4.5e9] * 4,
        total_debt=1.5e9,
        debt_to_equity=0.2,
    )
    estimates = [SimpleNamespace(
        period_end="2027-12-31",
        revenue_avg=7.0e9, revenue_low=6.5e9, revenue_high=7.5e9,
        eps_avg=12.0, eps_low=9.0, eps_high=15.0,
        ebitda_avg=2.8e9, ebitda_low=2.2e9, ebitda_high=3.4e9,
        ebit_avg=None, ebit_low=None, ebit_high=None,
        analyst_count_eps=10,
        analyst_count_revenue=2,   # < 3 → dispersion bands off, point estimate on
    )]
    return dict(rows=rows, price=290.0, mcap=290.0 * 2.6e8,
                anchor="EV/EBITDA", unavailable=[],
                estimates=estimates)


_CASES = [
    ("FIXCR", "Tech",          "Cybersecurity / Mission-Critical SaaS", _case_cyber_saas),
    ("FIXBK", "Financials",    "Money Center Bank",                     _case_money_center_bank),
    ("FIXRE", "RealEstate",    "REIT",                                  _case_reit),
    ("FIXBT", "Biopharma",     "Pre-approval Biotech",                  _case_preapproval_biotech),
    ("FIXIN", "Financials",    "Insurance",                             _case_insurance),
    ("FIXMN", "Crypto",        "Digital Asset Mining",                  _case_btc_miner),
    ("FIXTC", "Crypto",        "BTC Treasury / Proxy",                  _case_btc_treasury),
    ("FIXEX", "Crypto",        "Crypto Exchange",                       _case_crypto_exchange),
]


@pytest.mark.parametrize("ticker,sector,profile,builder", _CASES,
                         ids=[c[0] for c in _CASES])
def test_dcf_fixture_profile(ticker, sector, profile, builder, seams):
    spec = builder()
    seams["rows"][ticker] = spec["rows"]
    seams["price"][ticker] = spec["price"]
    seams["mcap"][ticker] = spec["mcap"]
    if spec.get("estimates"):
        seams["estimates"][ticker] = spec["estimates"]

    state = _state(ticker, sector, profile, extra=spec.get("extra"))
    out = run_dcf_agent(state)

    entry = out["data"]["dcf_range"].get(ticker)
    skip = out["data"].get("dcf_skip_reasons", {}).get(ticker)
    assert entry, f"{ticker}: engine skipped the ticker ({skip})"

    # Profile resolution — the pre-classified profile must resolve in-situ
    # (D1 taxonomy + D3 loud degradation).
    assert entry["profile"] == profile
    assert entry["profile_fallback_used"] is False, (
        f"{ticker}: profile fallback fired for resolved profile {profile!r}"
    )

    # Forward gate — the base scenario values the company through the
    # profile's declared methods, not the pure-DCF fallback.
    base = entry["base"]
    assert base["methods_used"], f"{ticker}: no methods fired in base scenario"
    assert base["intrinsic_value"] > 0, (
        f"{ticker}: base IV non-positive ({base['intrinsic_value']}) — "
        f"methods={base['methods_used']}"
    )
    assert spec["anchor"] in base["methods_used"], (
        f"{ticker}: anchor method {spec['anchor']!r} missing from "
        f"{base['methods_used']}"
    )

    # Loud degradation metadata (D3) — declared methods that produced no
    # value must be named, and for these complete fixtures none are missing.
    assert entry["methods_unavailable"] == spec["unavailable"], (
        f"{ticker}: methods_unavailable={entry['methods_unavailable']!r}, "
        f"expected {spec['unavailable']!r}"
    )

    # Backward gate path sane — the T-1 calibration test ran against the
    # patched price seam or skipped with a note; it never crashed.
    assert isinstance(entry.get("calibration_note"), str)
    assert entry["calibration_note"]


def test_rd_field_copied_into_series():
    """Regression for the D6 discovery: EV/R&D and the rNPV R&D-burn read
    most_recent['research_and_development'], so _extract_annual_series must
    copy it (it is requested in search_line_items and in _FX_MONETARY)."""
    rows = _mk_rows([1.0e8, 1.2e8],
                    research_and_development=[2.0e7, 2.4e7],
                    net_income=[1.0e7, 1.2e7])
    series, ccy = _extract_annual_series(rows)
    assert ccy == "USD"
    assert series[-1]["research_and_development"] == 2.4e7
    assert series[0]["research_and_development"] == 2.0e7


def test_shares_cross_check_repairs_bad_line_items(seams):
    """D6 discovery on CRWD: FMP line items reported 1.0325e9 shares vs
    ~2.55e8 implied by quote marketCap/price — every per-share value came
    out ~4x low. When quote-implied shares diverge >25% from line items,
    the engine must trust the market-observed quote count."""
    spec = _case_cyber_saas()
    rows = spec["rows"]
    true_shares = 2.5e8
    for li in rows:
        li.shares_outstanding = true_shares * 4.13   # FMP-style gross error
    seams["rows"]["FIXSH"] = rows
    seams["price"]["FIXSH"] = 350.0
    # Quote marketCap reflects REALITY (price × true shares), not the bad line items
    seams["mcap"]["FIXSH"] = 350.0 * true_shares

    state = _state("FIXSH", "Tech", "Cybersecurity / Mission-Critical SaaS")
    out = run_dcf_agent(state)

    entry = out["data"]["dcf_range"].get("FIXSH")
    assert entry, f"engine skipped FIXSH: {out['data'].get('dcf_skip_reasons')}"
    assert entry["shares_source"] == "quote_cross_check"
    got = entry["shares_outstanding"]
    assert abs(got - true_shares) / true_shares < 0.01, (
        f"cross-check failed: shares={got:,.0f}, expected ~{true_shares:,.0f}"
    )
    assert entry["base"]["intrinsic_value"] > 0


def test_scenario_method_set_structurally_consistent(seams):
    """COIN 2026-08-09 (Stage 5 forward gate): consensus EPS crossed zero
    between eps_avg (−) and eps_high (+), so Forward P/E fired ONLY in the
    bull scenario — _blend_methods renormalized weights around the extra
    method and a low outlier dragged bull IV below bear IV (78/103/65).
    Scenario analysis varies INPUTS, not model structure: the base scenario
    fixes the method set, so a method unavailable in base must be absent
    from every scenario (named in methods_unavailable), and all scenarios
    must blend the same methods."""
    spec = _case_crypto_exchange()
    # Same fixture, but consensus EPS is negative on average and only the
    # bull dispersion point is positive — exactly the COIN shape.
    spec["estimates"] = [SimpleNamespace(
        period_end="2027-12-31",
        revenue_avg=7.0e9, revenue_low=6.5e9, revenue_high=7.5e9,
        eps_avg=-0.5, eps_low=-2.0, eps_high=1.5,
        ebitda_avg=2.8e9, ebitda_low=2.2e9, ebitda_high=3.4e9,
        ebit_avg=None, ebit_low=None, ebit_high=None,
        analyst_count_eps=10,
        analyst_count_revenue=2,   # < 3 → dispersion bands off
    )]
    seams["rows"]["FIXCO"] = spec["rows"]
    seams["price"]["FIXCO"] = spec["price"]
    seams["mcap"]["FIXCO"] = spec["mcap"]
    seams["estimates"]["FIXCO"] = spec["estimates"]

    state = _state("FIXCO", "Crypto", "Crypto Exchange")
    out = run_dcf_agent(state)

    entry = out["data"]["dcf_range"].get("FIXCO")
    assert entry, f"engine skipped FIXCO: {out['data'].get('dcf_skip_reasons')}"

    # Forward P/E could not produce a base-case value → named as unavailable
    # (loud degradation) and excluded from EVERY scenario's blend.
    assert "Forward P/E" in entry["methods_unavailable"]
    base_used = set(entry["base"]["methods_used"])
    assert "Forward P/E" not in base_used
    for scen in ("bear", "bull"):
        used = set(entry[scen]["methods_used"])
        assert used == base_used, (
            f"FIXCO: {scen} scenario method set {sorted(used)} diverges from "
            f"base {sorted(base_used)} — scenario analysis must vary inputs, "
            f"not model structure"
        )
        assert entry[scen]["methods_count"] == entry["base"]["methods_count"], (
            f"FIXCO: methods_count {scen}={entry[scen]['methods_count']} vs "
            f"base={entry['base']['methods_count']}"
        )

    # The pre-fix pathology: the bull-only method was a low outlier that
    # inverted the scenario ordering (COIN: bull $64.56 < bear $78.25).
    assert entry["bull"]["intrinsic_value"] >= entry["bear"]["intrinsic_value"], (
        f"FIXCO: scenario inversion — bull {entry['bull']['intrinsic_value']} "
        f"< bear {entry['bear']['intrinsic_value']}"
    )


# ── Task #18: SW50 owner-earnings≤0 cascade ─────────────────────────────────
# Pre-fix pathology (CRWD 2026-08): a ≤0 trailing owner-earnings margin was
# floored per-year by FCF_MARGIN_FLOOR inside _project_dcf, producing a tiny
# positive "IV" that was really only discounted net cash — blended at full
# profile weight it dragged IV toward zero ($13.69 vs multiples ~$26). The
# cascade: median of the positive years of the chosen basis field when any
# exist; otherwise every DCF-projection method returns None and the blend
# renormalizes onto the multiples bucket (reference: sw46/iv15.py
# ::_resolve_base_oe).

def _cyber_rows_sbc(sbc_pcts: list[float], fcf_pct: float = 0.16):
    """Cyber-SaaS line items with per-year SBC as a fraction of revenue and
    no buybacks — owner earnings = FCF − unfunded SBC − 37% RSU withholding."""
    revs = [2.2e9, 2.6e9, 3.0e9, 3.4e9]
    return _mk_rows(
        revs,
        free_cash_flow=[r * fcf_pct for r in revs],
        net_income=[r * 0.10 for r in revs],
        ebitda=[r * 0.20 for r in revs],
        ebit=[r * 0.16 for r in revs],
        stock_based_compensation=[r * p for r, p in zip(revs, sbc_pcts)],
        capital_expenditure=[r * 0.05 for r in revs],
        gross_profit=[r * 0.78 for r in revs],
        shares_outstanding=2.5e8,
        net_debt=2.0e8, total_debt=6.0e8, cash_and_equivalents=4.0e8,
        total_equity=1.6e9, total_assets=3.0e9, invested_capital=1.5e9,
        book_value_per_share=6.4, debt_to_equity=0.4,
        interest_expense=2.0e7, dividends_per_share=0.0,
    )


def test_oe_cascade_median_of_positive_years(seams):
    """Mean owner-earnings margin ≤ 0 but one positive year → DCF basis
    becomes the median of the positive years and the DCF family fires."""
    # Owner-earnings margins: FY22-24 at 16% − 20% − 37%×20% = −11.4%,
    # FY25 at 16% − 8% − 37%×8% = +5.04% → mean ≈ −7.4% ≤ 0 → cascade.
    seams["rows"]["FIXOE1"] = _cyber_rows_sbc([0.20, 0.20, 0.20, 0.08])
    seams["price"]["FIXOE1"] = 350.0
    seams["mcap"]["FIXOE1"] = 350.0 * 2.5e8

    state = _state("FIXOE1", "Tech", "Cybersecurity / Mission-Critical SaaS")
    out = run_dcf_agent(state)
    entry = out["data"]["dcf_range"].get("FIXOE1")
    assert entry, f"engine skipped FIXOE1: {out['data'].get('dcf_skip_reasons')}"

    base = entry["base"]
    expected_median = 0.16 - 0.08 - 0.37 * 0.08          # the single + year
    assert base["fcf_margin_start"] == pytest.approx(expected_median, abs=1e-3), (
        f"cascade basis {base['fcf_margin_start']} != median of positive "
        f"years {expected_median:.4f}"
    )
    assert any("OE≤0 cascade" in f for f in base["forward_flags"]), (
        f"cascade flag missing: {base['forward_flags']}"
    )
    # The DCF anchor now projects off a real (if conservative) basis.
    assert "DCF (FCF+)" in base["methods_used"]
    assert base["intrinsic_value"] > 0
    assert entry["methods_unavailable"] == []
    # Trailing reality still classified the profile (pre-cascade margin).
    assert entry["profile"] == "Cybersecurity / Mission-Critical SaaS"


def test_oe_all_negative_disables_dcf_family(seams):
    """No positive owner-earnings year in the window → every DCF-projection
    method returns None, the blend renormalizes onto multiples only, and the
    excluded methods are named in methods_unavailable."""
    # Owner-earnings margin = 16% − 25% − 37%×25% = −18.25% every year.
    seams["rows"]["FIXOE2"] = _cyber_rows_sbc([0.25] * 4)
    seams["price"]["FIXOE2"] = 350.0
    seams["mcap"]["FIXOE2"] = 350.0 * 2.5e8

    state = _state("FIXOE2", "Tech", "Cybersecurity / Mission-Critical SaaS")
    out = run_dcf_agent(state)
    entry = out["data"]["dcf_range"].get("FIXOE2")
    assert entry, f"engine skipped FIXOE2: {out['data'].get('dcf_skip_reasons')}"

    base = entry["base"]
    assert any("OE≤0" in f and "disabled" in f for f in base["forward_flags"]), (
        f"disable flag missing: {base['forward_flags']}"
    )
    base_used = set(base["methods_used"])
    assert base_used, "multiples methods must still fire when DCF is disabled"
    assert "DCF (FCF+)" not in base_used
    assert "NRR-adj DCF" not in base_used
    assert set(entry["methods_unavailable"]) == {"DCF (FCF+)", "NRR-adj DCF"}
    # Blend is multiples-only: the DCF bucket is empty.
    assert base["weight_dcf"] == 0.0
    assert base["weight_multi"] == pytest.approx(1.0)
    assert base["iv_dcf"] is None
    assert base["intrinsic_value"] > 0
    # Structural gate: identical method set in every scenario.
    for scen in ("bear", "bull"):
        assert set(entry[scen]["methods_used"]) == base_used, (
            f"FIXOE2: {scen} method set diverges from base after DCF disable"
        )


def test_oe_cascade_reported_path_when_sbc_untrusted(seams):
    """SBC disclosed in <3 of 5 years → owner path not trusted → the cascade
    runs on the reported-FCF field instead (mean ≤ 0, two positive years)."""
    revs = [2.2e9, 2.6e9, 3.0e9, 3.4e9]
    seams["rows"]["FIXOE3"] = _mk_rows(
        revs,
        free_cash_flow=[r * m for r, m in
                        zip(revs, [-0.08, -0.06, 0.04, 0.02])],
        net_income=[r * 0.10 for r in revs],
        ebitda=[r * 0.20 for r in revs],
        ebit=[r * 0.16 for r in revs],
        capital_expenditure=[r * 0.05 for r in revs],
        gross_profit=[r * 0.78 for r in revs],
        shares_outstanding=2.5e8,
        net_debt=2.0e8, total_debt=6.0e8, cash_and_equivalents=4.0e8,
        total_equity=1.6e9, total_assets=3.0e9, invested_capital=1.5e9,
        book_value_per_share=6.4, debt_to_equity=0.4,
        interest_expense=2.0e7, dividends_per_share=0.0,
    )
    seams["price"]["FIXOE3"] = 350.0
    seams["mcap"]["FIXOE3"] = 350.0 * 2.5e8

    state = _state("FIXOE3", "Tech", "Cybersecurity / Mission-Critical SaaS")
    out = run_dcf_agent(state)
    entry = out["data"]["dcf_range"].get("FIXOE3")
    assert entry, f"engine skipped FIXOE3: {out['data'].get('dcf_skip_reasons')}"

    base = entry["base"]
    # Reported margins mean = −2% ≤ 0 → median of [+4%, +2%] = 3%.
    assert base["fcf_margin_start"] == pytest.approx(0.03, abs=1e-3)
    assert any("OE≤0 cascade" in f and "reported-FCF" in f
               for f in base["forward_flags"]), (
        f"reported-path cascade flag missing: {base['forward_flags']}"
    )
    assert "DCF (FCF+)" in base["methods_used"]
    assert base["intrinsic_value"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
