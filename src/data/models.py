from pydantic import BaseModel, Field, field_validator, model_validator


class Price(BaseModel):
    open: float
    close: float
    high: float
    low: float
    volume: int
    time: str


class PriceResponse(BaseModel):
    ticker: str
    prices: list[Price]


class FinancialMetrics(BaseModel):
    ticker: str
    report_period: str
    period: str
    currency: str
    market_cap: float | None
    enterprise_value: float | None
    price_to_earnings_ratio: float | None
    price_to_book_ratio: float | None
    price_to_sales_ratio: float | None
    enterprise_value_to_ebitda_ratio: float | None
    enterprise_value_to_revenue_ratio: float | None
    free_cash_flow_yield: float | None
    peg_ratio: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    return_on_equity: float | None
    return_on_assets: float | None
    return_on_invested_capital: float | None
    asset_turnover: float | None
    inventory_turnover: float | None
    receivables_turnover: float | None
    days_sales_outstanding: float | None
    operating_cycle: float | None
    working_capital_turnover: float | None
    current_ratio: float | None
    quick_ratio: float | None
    cash_ratio: float | None
    operating_cash_flow_ratio: float | None
    debt_to_equity: float | None
    debt_to_assets: float | None
    interest_coverage: float | None
    revenue_growth: float | None
    earnings_growth: float | None
    book_value_growth: float | None
    earnings_per_share_growth: float | None
    free_cash_flow_growth: float | None
    operating_income_growth: float | None
    ebitda_growth: float | None
    payout_ratio: float | None
    earnings_per_share: float | None
    book_value_per_share: float | None
    free_cash_flow_per_share: float | None
    # ── REIT / Business Trust specific metrics (optional, default None) ────────
    # Populated for REIT sector tickers; None for non-REIT equities.
    ffo: float | None = None                    # Funds From Operations
    affo: float | None = None                   # Adjusted FFO
    noi: float | None = None                    # Net Operating Income
    price_to_ffo: float | None = None           # P/FFO (like P/E for REITs)
    price_to_nav: float | None = None           # P/NAV (price to net asset value)
    nav_per_unit: float | None = None           # Book value per unit (proxy for NAV)
    cap_rate: float | None = None               # NOI / Enterprise Value
    ltv: float | None = None                    # Loan-to-Value (Total Debt / EV)
    net_debt_to_ebitda: float | None = None     # Leverage ratio
    dividend_yield: float | None = None         # Distribution yield


class FinancialMetricsResponse(BaseModel):
    financial_metrics: list[FinancialMetrics]


class LineItem(BaseModel):
    ticker: str
    report_period: str
    period: str
    currency: str

    # Allow additional fields dynamically
    model_config = {"extra": "allow"}


class LineItemResponse(BaseModel):
    search_results: list[LineItem]


class InsiderTrade(BaseModel):
    ticker: str
    issuer: str | None
    name: str | None
    title: str | None
    is_board_director: bool | None
    transaction_date: str | None
    transaction_shares: float | None
    transaction_price_per_share: float | None
    transaction_value: float | None
    shares_owned_before_transaction: float | None
    shares_owned_after_transaction: float | None
    security_title: str | None
    filing_date: str


class InsiderTradeResponse(BaseModel):
    insider_trades: list[InsiderTrade]


class CompanyNews(BaseModel):
    ticker: str
    title: str
    author: str
    source: str
    date: str
    url: str
    sentiment: str | None = None


class CompanyNewsResponse(BaseModel):
    news: list[CompanyNews]


class CompanyFacts(BaseModel):
    ticker: str
    name: str
    cik: str | None = None
    industry: str | None = None
    sector: str | None = None
    category: str | None = None
    exchange: str | None = None
    is_active: bool | None = None
    listing_date: str | None = None
    location: str | None = None
    market_cap: float | None = None
    number_of_employees: int | None = None
    sec_filings_url: str | None = None
    sic_code: str | None = None
    sic_industry: str | None = None
    sic_sector: str | None = None
    website_url: str | None = None
    weighted_average_shares: int | None = None


class CompanyFactsResponse(BaseModel):
    company_facts: CompanyFacts


class Position(BaseModel):
    cash: float = 0.0
    shares: int = 0
    ticker: str


class Portfolio(BaseModel):
    positions: dict[str, Position]  # ticker -> Position mapping
    total_cash: float = 0.0


class AnalystSignal(BaseModel):
    signal: str | None = None
    confidence: float | None = None
    reasoning: dict | str | None = None
    max_position_size: float | None = None  # For risk management signals


class TickerAnalysis(BaseModel):
    ticker: str
    analyst_signals: dict[str, AnalystSignal]  # agent_name -> signal mapping


class AgentStateData(BaseModel):
    tickers: list[str]
    portfolio: Portfolio
    start_date: str
    end_date: str
    ticker_analyses: dict[str, TickerAnalysis]  # ticker -> analysis mapping


class AgentStateMetadata(BaseModel):
    show_reasoning: bool = False
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Advanced 10-Phase Pipeline Models
# ---------------------------------------------------------------------------

from typing import Literal
# Field, field_validator, model_validator already imported at top of file


class MacroRegime(BaseModel):
    risk_appetite: Literal["risk-on", "risk-off"]
    rate_direction: Literal["tightening", "easing", "neutral"]
    dollar_trend: Literal["strengthening", "weakening", "neutral"]
    volatility_regime: Literal["low", "medium", "high"]
    recession_risk: Literal["low", "elevated", "high"] = "low"   # default for backward compat
    regime_notes: str


class MacroRegimeOutput(BaseModel):
    regime: MacroRegime
    agent_weights: dict[str, float]   # LLM-suggested multipliers (0.5–2.0); overridden by rule table
    position_size_cap: float
    regime_notes: str


class StrategicRouterOutput(BaseModel):
    sector: Literal["Consumer", "Tech", "Biopharma", "Telco",
                    "Crypto", "Energy", "Financials", "Industrials",
                    "RealEstate", "Transportation", "Materials",
                    "Resources", "ProfessionalServices", "HealthcareServices",
                    "Semiconductor"]
    raw_financials: dict[str, object]   # {FY2020: {revenue: x, ...}, ...}
    insider_summary: str
    routing_decision: dict[str, object]  # {specialist_block: str, data_feeds: [...]}


class IndustryBriefOutput(BaseModel):
    brief_text: str = ""
    key_kpis: dict[str, object] = Field(default_factory=dict)
    footnotes: list[dict] = Field(default_factory=list)
    # Each footnote: {ref_id: int, source_name: str, source_type: str,
    #                 date: str, speaker: str, claim: str, quote: str, url: str}


class AdvancedInvestorSignal(BaseModel):
    signal: Literal["BUY", "SELL", "SHORT", "HOLD"]
    conviction: int = Field(ge=1, le=10)
    time_horizon: Literal["short", "medium", "long"]
    price_target: float
    thesis_summary: str
    key_risks: list[str] = Field(default_factory=list)
    cot_log: str = ""          # optional — may be truncated if max_tokens hit


class DebateResult(BaseModel):
    disagreement_core: str
    agent_a: str
    agent_b: str
    agent_a_rebuttal: str
    agent_b_rebuttal: str
    adjudication: str
    adjudicated_signal: Literal["BUY", "SELL", "HOLD"]
    adjudicated_conviction: int = Field(ge=1, le=10)


class ScenarioCase(BaseModel):
    assumptions: str
    fair_value: float
    probability: float   # 0.0–1.0; bull+base+bear must sum to 1.0


class ScenarioOutput(BaseModel):
    bull: ScenarioCase
    base: ScenarioCase
    bear: ScenarioCase
    expected_value: float
    current_price: float
    upside_pct: float

    @field_validator("current_price", "expected_value", "upside_pct", mode="before")
    @classmethod
    def _coerce_float(cls, v: object) -> float:
        """Coerce None / 'unknown' / non-numeric strings to 0.0."""
        if v is None:
            return 0.0
        if isinstance(v, str) and v.strip().lower() in ("unknown", "n/a", "null", "none", ""):
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0


class PowerLawOutput(BaseModel):
    # total_score is derived in the backend (weighted mean of dimensions); the
    # LLM no longer produces it directly. Kept as a field for storage + UI.
    total_score: int = Field(ge=0, le=10, default=5)
    scale_economies: int = Field(ge=0, le=10)
    scale_economies_note: str = ""       # positive evidence / what checks off
    scale_economies_concern: str = ""    # risk, caveat, or gap
    network_effects: int = Field(ge=0, le=10)
    network_effects_note: str = ""
    network_effects_concern: str = ""
    winner_take_most: int = Field(ge=0, le=10)
    winner_take_most_note: str = ""
    winner_take_most_concern: str = ""
    switching_costs: int = Field(ge=0, le=10)
    switching_costs_note: str = ""
    switching_costs_concern: str = ""
    data_ip_moat: int = Field(ge=0, le=10)
    data_ip_moat_note: str = ""
    data_ip_moat_concern: str = ""
    # interpretation is derived from total_score server-side, but we keep the
    # field for backwards compat with stored rows (old rows may still have
    # "solid compounder" / "commodity risk").
    interpretation: str = "average"
    multiple_implication: str = ""

    @model_validator(mode="before")
    @classmethod
    def _clamp_scores(cls, data: object) -> object:
        """Clamp all score fields into valid range before Pydantic validates ge/le.

        Handles:
        - LLM returning floats (6.5) → rounded to nearest integer
        - LLM returning out-of-range values (11, -1) → clamped
        - Legacy cached rows on the 0-2 scale → auto-rescaled to 0-10 (×5).
          Heuristic: if every dimension is in [0, 2] and total_score is 1-10,
          the row is legacy — multiply dimensions by 5 so the radar + pentagon
          render consistently with the new rubric.
        """
        if not isinstance(data, dict):
            return data
        def _ci(v, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(round(float(v)))))
            except (TypeError, ValueError):
                return lo

        dim_keys = ("scale_economies", "network_effects", "winner_take_most",
                    "switching_costs", "data_ip_moat")
        raw_dims = [data.get(k) for k in dim_keys]

        # Legacy-rescale: old schema had dims in 0-2. If every provided dim is
        # a number ≤ 2, multiply by 5 to bring into the new 0-10 scale.
        def _is_num(x) -> bool:
            try: float(x); return True
            except (TypeError, ValueError): return False
        numeric_dims = [float(x) for x in raw_dims if _is_num(x)]
        is_legacy = (
            len(numeric_dims) == 5
            and all(0 <= x <= 2 for x in numeric_dims)
            and max(numeric_dims) <= 2
        )
        if is_legacy:
            for k in dim_keys:
                if _is_num(data.get(k)):
                    data[k] = float(data[k]) * 5.0

        data["total_score"]      = _ci(data.get("total_score",      5), 0, 10)
        for k in dim_keys:
            data[k] = _ci(data.get(k, 5), 0, 10)
        return data


class ValueTrapCheck(BaseModel):
    status: Literal["RED", "AMBER", "GREEN"]
    evidence: str

    @field_validator("status", mode="before")
    @classmethod
    def _normalise_status(cls, v: object) -> object:
        """Map common LLM deviations to canonical RED/AMBER/GREEN."""
        if not isinstance(v, str):
            return v
        _MAP = {
            "YELLOW": "AMBER", "ORANGE": "AMBER", "WARNING": "AMBER",
            "CAUTION": "AMBER", "MODERATE": "AMBER",
            "RED FLAG": "RED", "HIGH": "RED", "FLAG": "RED",
            "CLEAR": "GREEN", "OK": "GREEN", "PASS": "GREEN",
            "LOW": "GREEN", "NONE": "GREEN",
            "N/A": "GREEN", "NA": "GREEN", "UNKNOWN": "GREEN",
        }
        return _MAP.get(v.strip().upper(), v.strip().upper())


class ValueTrapOutput(BaseModel):
    dividend_sustainability: ValueTrapCheck
    structural_decline: ValueTrapCheck
    earnings_cashflow_mismatch: ValueTrapCheck
    insider_behaviour: ValueTrapCheck
    balance_sheet_deterioration: ValueTrapCheck
    overall_verdict: Literal["TRAP RISK HIGH", "TRAP RISK MEDIUM", "TRAP RISK LOW"]

    @field_validator("overall_verdict", mode="before")
    @classmethod
    def _normalise_verdict(cls, v: object) -> object:
        """Map LLM shorthand to the canonical three-word verdict strings."""
        if not isinstance(v, str):
            return v
        u = v.strip().upper()
        if "HIGH" in u:
            return "TRAP RISK HIGH"
        if "MED" in u:
            return "TRAP RISK MEDIUM"
        if "LOW" in u:
            return "TRAP RISK LOW"
        return v


class AdvancedPortfolioDecision(BaseModel):
    action: Literal["BUY", "SELL", "SHORT", "COVER", "HOLD"]
    position_size_pct: float
    entry_range: list[float]   # [low, high]
    stop_loss: float
    price_target: float
    time_horizon: str
    rationale: str


# ---------------------------------------------------------------------------
# Phase 2.5 Intelligence Agent Models
# ---------------------------------------------------------------------------

class InsiderTransaction(BaseModel):
    """Single open-market insider buy or sell transaction."""
    name: str
    title: str | None = None
    transaction_type: Literal["BUY", "SELL"]
    shares: float
    price_per_share: float | None = None
    value_usd: float | None = None
    date: str
    role_weight: float = 1.0   # CEO/CFO = 3.0, Director = 1.5, Other officer = 1.0


class InsiderActivityOutput(BaseModel):
    """Summary output of the Insider Activity Agent (Phase 2.5)."""
    ticker: str
    signal: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    cluster_buy: bool = False            # >= 2 insiders buying within 30 days
    net_buying_30d_usd: float = 0.0
    net_buying_90d_usd: float = 0.0
    net_buying_12m_usd: float = 0.0
    gross_buy_value_12m: float = 0.0   # total insider buy value (absolute) over 12 months
    gross_sell_value_12m: float = 0.0  # total insider sell value (absolute) over 12 months
    buy_sell_ratio_12m: float = 0.0     # total buy value / total sell value; >1 = net buying
    conviction_sell_flag: bool = False   # CEO or CFO sold > $5M in a single transaction
    key_transactions: list[InsiderTransaction] = Field(default_factory=list)
    data_source: Literal["FMP", "EDGAR", "NONE"] = "NONE"
    analysis_note: str = ""


class EarningsSurprise(BaseModel):
    """Single quarter earnings beat or miss."""
    date: str
    eps_actual: float
    eps_estimated: float
    surprise_pct: float
    beat: bool


class AnalystRevisionOutput(BaseModel):
    """Summary output of the Analyst Revision Agent (Phase 2.5)."""
    ticker: str
    revision_direction: Literal[
        "ACCELERATING_UP", "STABLE", "DECELERATING", "ACCELERATING_DOWN", "UNKNOWN"
    ] = "UNKNOWN"
    eps_dispersion_pct: float | None = None    # (eps_high - eps_low) / abs(eps_avg) * 100
    revenue_dispersion_pct: float | None = None
    analyst_count: int = 0
    surprise_streak: int = 0            # positive = consecutive beats, negative = misses
    surprise_direction: Literal["BEAT", "MISS", "MIXED", "UNKNOWN"] = "UNKNOWN"
    estimate_dispersion: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    recent_surprises: list[EarningsSurprise] = Field(default_factory=list)
    analysis_note: str = ""


class ScoredArticle(BaseModel):
    """Single news article or press release with deterministic sentiment score."""
    ticker: str
    title: str
    text: str = ""
    date: str
    source: str
    url: str = ""
    is_press_release: bool = False
    score: float = 0.0          # -1.0 (bearish) to +1.0 (bullish); 0.0 = neutral
    label: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"


class NewsSentimentOutput(BaseModel):
    """Summary output of the News Sentiment Agent (Phase 2.5)."""
    ticker: str
    signal: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "NEUTRAL"
    composite_score: float = 0.0      # recency-weighted mean score, -1.0 to +1.0
    article_count: int = 0            # total news articles scored
    press_release_count: int = 0      # press releases scored separately
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    press_release_signal: Literal["POSITIVE", "NEGATIVE", "NEUTRAL", "NONE"] = "NONE"
    volume_spike: bool = False        # True if article count > 2× trailing 30-day average
    top_headlines: list[str] = Field(default_factory=list)   # top 5 most-signal headlines
    scored_articles: list[ScoredArticle] = Field(default_factory=list)
    analysis_note: str = ""


class ShortInterestOutput(BaseModel):
    """
    Summary output of the Short Interest Agent (Phase 2.5).

    All metrics are computed deterministically from FMP /stable/short-interest.
    No LLM is involved.  Consumed by:
      - Pathway 1: all 12 investor agent prompts (intel_section injection)
        with persona-specific framing for Burry (forensic/variant perception)
        and Druckenmiller (positioning/crowded trade/squeeze risk)
    """
    ticker: str

    # ── Latest snapshot ───────────────────────────────────────────────────────
    report_date: str | None = None          # most recent settlement date
    short_interest_shares: float | None = None   # number of shares sold short
    short_float_pct: float | None = None    # short interest as % of float
    shares_float: float | None = None       # total float shares
    days_to_cover: float | None = None      # short interest / avg daily volume
    borrow_rate_pct: float | None = None    # annualised cost to borrow (%)

    # ── Flag classifications ───────────────────────────────────────────────────
    short_float_flag: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    # HIGH: > 20% | MEDIUM: 10–20% | LOW: < 10%
    days_to_cover_flag: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    # HIGH: > 10d | MEDIUM: 5–10d | LOW: < 5d
    borrow_rate_flag: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    # HIGH: > 50% p.a. | MEDIUM: 20–50% | LOW: < 20%

    # ── Trend (change vs prior period) ───────────────────────────────────────
    short_interest_trend: Literal["INCREASING", "STABLE", "DECREASING", "UNKNOWN"] = "UNKNOWN"
    short_float_pct_prior: float | None = None   # previous period for delta context

    # ── Composite risk flags ───────────────────────────────────────────────────
    squeeze_risk: bool = False        # short float > 20% AND days_to_cover > 7
    crowded_trade: bool = False       # short float > 15% — dangerous for short sellers
    signal: Literal["HEAVILY_SHORTED", "MODERATELY_SHORTED", "LOW_SHORT_INTEREST", "UNKNOWN"] = "UNKNOWN"

    # ── Persona-specific interpretations ──────────────────────────────────────
    burry_note: str = ""        # forensic/variant-perception framing
    druckenmiller_note: str = ""   # positioning/crowded-trade/squeeze framing

    data_source: Literal["FMP", "yfinance", "NONE"] = "NONE"
    analysis_note: str = ""


class EarningsQualityOutput(BaseModel):
    """
    Summary output of the Earnings Quality Scorer (Phase 2.5).

    All metrics are computed deterministically from FMP financial statement data.
    No LLM is involved.  Consumed by:
      - Pathway 1: all 12 investor agent prompts (intel_section injection)
      - Pathway 2: Value Trap agent Check 3 (earnings vs cash flow mismatch)
      - Pathway 3: Risk Manager — remark only, no weight change
    """
    ticker: str

    # ── Accruals (Sloan 1996 — high accruals predict negative future returns) ──
    accrual_ratio_avg: float | None = None   # 3-year avg of (NI - OCF) / Total Assets
    accrual_ratios: list[float] = Field(default_factory=list)   # per-year, newest first
    accrual_trend: Literal["DETERIORATING", "STABLE", "IMPROVING", "UNKNOWN"] = "UNKNOWN"
    accrual_flag: Literal["RED", "AMBER", "GREEN", "UNKNOWN"] = "UNKNOWN"
    # RED: avg > 0.10 | AMBER: 0.05–0.10 | GREEN: < 0.05

    # ── Cash conversion (OCF / Net Income) ───────────────────────────────────
    cash_conversion_ratio: float | None = None   # OCF / NI (most recent full year)
    cash_conversion_flag: Literal["RED", "AMBER", "GREEN", "UNKNOWN"] = "UNKNOWN"
    # RED: < 0.75 | AMBER: 0.75–0.85 | GREEN: ≥ 0.85

    # ── Accounts receivable vs revenue growth divergence ─────────────────────
    ar_cagr_3y: float | None = None          # AR 3-year CAGR
    revenue_cagr_3y: float | None = None     # Revenue 3-year CAGR
    ar_revenue_divergence: Literal["RED", "AMBER", "GREEN", "UNKNOWN"] = "UNKNOWN"
    # RED: AR CAGR > Revenue CAGR × 1.5 | AMBER: × 1.2 | GREEN: ≤ × 1.2

    # ── Days Sales Outstanding trend ─────────────────────────────────────────
    dso_values: list[float] = Field(default_factory=list)   # per-year, newest first
    dso_trend: Literal["RISING", "STABLE", "FALLING", "UNKNOWN"] = "UNKNOWN"

    # ── Stock-based compensation drag ─────────────────────────────────────────
    sbc_drag_pct: float | None = None        # SBC / OCF × 100 (most recent year)
    sbc_drag_flag: Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"] = "UNKNOWN"
    # HIGH: > 25% | MEDIUM: 15–25% | LOW: < 15%

    # ── FCF vs Net Income divergence ─────────────────────────────────────────
    fcf_ni_ratios: list[float] = Field(default_factory=list)   # per-year, newest first
    fcf_ni_divergence: Literal["RED", "AMBER", "GREEN", "UNKNOWN"] = "UNKNOWN"
    # RED: ratio declining 2+ consecutive years | AMBER: 1 year | GREEN: stable/improving

    # ── Aggregate ─────────────────────────────────────────────────────────────
    overall_quality_score: float = 5.0      # 0 (worst) – 10 (best)
    quality_verdict: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    pre_earnings_risk: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    flags: list[str] = Field(default_factory=list)
    data_quality: Literal["FULL", "PARTIAL", "INSUFFICIENT"] = "INSUFFICIENT"
    analysis_note: str = ""


class AnalystEstimates(BaseModel):
    """
    Forward analyst consensus estimates from FMP /stable/analyst-estimates.
    All financial fields in the same currency/scale as the FMP financials endpoints
    (i.e., raw dollars, not millions). Fields absent from the API response are None.

    Includes low/avg/high bands for revenue, EBITDA, EBIT, net income, EPS so the
    DCF engine can drive bear/base/bull scenarios directly from analyst dispersion.
    """
    ticker: str
    period_end: str             # fiscal year end date, e.g. "2025-12-31"
    # Revenue band
    revenue_avg: float | None = None
    revenue_low: float | None = None
    revenue_high: float | None = None
    # EBITDA band
    ebitda_avg: float | None = None
    ebitda_low: float | None = None
    ebitda_high: float | None = None
    # EBIT band
    ebit_avg: float | None = None
    ebit_low: float | None = None
    ebit_high: float | None = None
    # Net income band
    net_income_avg: float | None = None
    net_income_low: float | None = None
    net_income_high: float | None = None
    # EPS band
    eps_avg: float | None = None
    eps_low: float | None = None
    eps_high: float | None = None
    # Coverage quality
    analyst_count_revenue: int | None = None   # number of analysts covering revenue
    analyst_count_eps: int | None = None       # number of analysts covering EPS


# ---------------------------------------------------------------------------
# Phase 7d — BU-Level Analyst Output
# ---------------------------------------------------------------------------

class BUAnalysisOutput(BaseModel):
    """Output of the BU-Level Analyst agent (Phase 7d)."""
    kpi_extraction: dict = Field(default_factory=dict)
    # {unit_economics: str, backlog_rpo: str, segment_nrr: str}

    margin_attribution: str = ""

    capex_breakdown: dict = Field(default_factory=dict)
    # {growth_capex_pct: float, maintenance_capex_pct: float,
    #  capex_as_pct_revenue: float, commentary: str}

    product_resilience: str = ""

    segment_forecast: dict = Field(default_factory=dict)
    # {bear: {yr1_rev_growth, yr2_rev_growth, yr3_rev_growth, ebitda_margin_yr3, assumption},
    #  base: {...}, bull: {...}}

    data_limitations: str = ""


# ---------------------------------------------------------------------------
# Phase 7e — Senior Financial Editor Output
# ---------------------------------------------------------------------------

class FinancialEditorOutput(BaseModel):
    """Output of the Senior Financial Editor agent (Phase 7e)."""
    polished_summary: str = ""
    # Corrected, de-duplicated, professionally worded executive summary

    logic_audit_flags: list[str] = Field(default_factory=list)
    # List of internal contradictions found: e.g. ["Bull case assumes 40% rev growth
    # but base WACC implies capital-scarce environment"]

    formatting_notes: list[str] = Field(default_factory=list)
    # Consolidated table suggestions, nomenclature fixes

    report_quality_score: int = Field(default=5, ge=1, le=10)
    # 1-10: 10 = publication-ready, 1 = requires major revision

    key_corrections: list[str] = Field(default_factory=list)
    # Specific factual or logical corrections made


# ---------------------------------------------------------------------------
# Phase 7f — Governance & Citation Auditor Output
# ---------------------------------------------------------------------------

class CitationAuditOutput(BaseModel):
    """Output of the Governance & Citation Auditor agent (Phase 7f)."""
    hallucination_flags: list[str] = Field(default_factory=list)
    # Claims that cannot be verified or are demonstrably incorrect

    primary_source_gaps: list[str] = Field(default_factory=list)
    # Claims citing "estimates" or "web search" that should cite SEC filings

    audit_score: int = Field(default=5, ge=1, le=10)
    # Data provenance score: 10 = 100% primary sources, 1 = mostly AI-generated
