"""
Phase 7a — Scenario Agent

What it does:
- Collects all investor price targets from Phase 5
- Builds bull / base / bear scenarios with explicit probability weights
- Computes expected value = Σ(target × probability)
- Compares EV to current price → upside_pct
- This upside_pct feeds directly into the Portfolio Manager's position sizing formula:
    position_size = approved_size × (ev_upside / 100) × (power_law_score / 10)

Why probabilities here instead of inside individual investors:
- Individual investors are biased (Graham will always lean bear; Wood will always lean bull)
- The Scenario Agent sees ALL signals and synthesises a calibrated probability distribution
- Forcing probabilities to sum to 1.0 prevents the LLM from hedging with three "high" scenarios
"""

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import ScenarioOutput, ScenarioCase
from src.graph.state import AgentState
from datetime import datetime, timedelta
from src.tools.api import get_market_cap, get_prices
from src.utils.llm import call_llm
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state


def _case_fv(case) -> float:
    """Extract fair_value from a ScenarioCase (Pydantic) or legacy dict."""
    if isinstance(case, ScenarioCase):
        return float(case.fair_value or 0.0)
    if isinstance(case, dict):
        return float(case.get("fair_value", 0.0) or 0.0)
    return 0.0


def _case_prob(case, default: float) -> float:
    """Extract probability from a ScenarioCase (Pydantic) or legacy dict."""
    if isinstance(case, ScenarioCase):
        return float(case.probability or default)
    if isinstance(case, dict):
        return float(case.get("probability", default) or default)
    return default

SYSTEM_PROMPT = """
You are the Scenario Agent executing Phase 3 analysis.

Using all investor agent outputs and the Industry Intelligence Brief,
define three mutually exclusive scenarios. Probabilities must sum to exactly 1.0.

Bull Case: optimistic assumptions, fair value target, probability
Base Case: central assumptions, fair value target, probability
Bear Case: pessimistic assumptions, fair value target, probability

DCF Engine anchors (when provided): These are deterministic, forward-looking per-share
intrinsic values computed before you ran. Use them as numeric constraints:
- Your bear fair_value should be near the DCF bear IV (deviate only with strong evidence)
- Your base fair_value should be near the DCF base IV (investor consensus may shift ±15%)
- Your bull fair_value should be near the DCF bull IV (TAM or catalyst premium acceptable)
If no DCF anchors are provided, derive fair values from investor targets and industry data.

M&A OVERRIDE: If the research identifies a SIGNED acquisition, merger, or tender offer at a
specific per-share price, the deal price REPLACES DCF anchors for fair_value:
- Base fair_value = offer price (the deal IS the valuation)
- Bear fair_value = standalone trough value if deal fails (ignore DCF bear IV)
- Bull fair_value = offer price + small premium (competing bid, regulatory approval timing)
- Probability weights should reflect deal-close likelihood (typically 60-80% for signed deals)
The offer price is a HARD CEILING — do not set any fair_value above the offer price unless
a competing bid or sweetened offer is explicitly identified in the research.

Expected Value = (Bull target × Bull prob) + (Base target × Base prob) + (Bear target × Bear prob)
Compare EV to current price. Output upside_pct = (EV - current_price) / current_price × 100.

Be specific about assumptions (revenue growth rate, margin, exit multiple).
Output JSON only.
""".strip()


def run_scenario_agent(state: AgentState) -> AgentState:
    """Phase 7a: bull/base/bear scenarios + expected value."""
    agent_id = "scenario_agent"
    tickers = state["data"]["tickers"]
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    end_date = state["data"]["end_date"]
    industry_brief = state["data"].get("industry_brief", "")
    analyst_signals = state["data"].get("analyst_signals", {})
    dr_sections = state["data"].get("deep_research_sections", {})
    # 2B feeds scenario assumptions (competitive dynamics shape bull/base cases)
    # 2E feeds bear case (disruption vectors are the primary bear case driver)
    competitive_section = dr_sections.get("2b", "")
    disruption_section  = dr_sections.get("2e", "")

    scenario_results: dict[str, object] = {}

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Building scenarios")

        market_cap = get_market_cap(ticker, end_date, api_key=api_key)

        # Waterfall price resolution — must always resolve to a float
        # 1. Risk manager output (live prices fed in by the risk manager)
        risk_data = analyst_signals.get("risk_management_agent", {}).get(ticker, {})
        current_price_val = risk_data.get("current_price") or (
            state["data"].get("current_prices", {}).get(ticker)
        )
        # 2. Latest EOD price from FMP price history (last 5 calendar days)
        if not current_price_val:
            try:
                _price_start = (
                    datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=5)
                ).strftime("%Y-%m-%d")
                _prices = get_prices(ticker, _price_start, end_date, api_key=api_key)
                if _prices:
                    current_price_val = float(_prices[-1].close)
            except Exception:
                pass
        # 3. Unconditional sentinel — LLM will override with its own estimate
        if not current_price_val:
            current_price_val = 100.0

        # DCF Engine anchors (Phase 4.5) — per-share intrinsic values
        dcf_ticker = state["data"].get("dcf_range", {}).get(ticker, {})
        if dcf_ticker and dcf_ticker.get("base"):
            dcf_anchors_str = (
                f"Bear IV: ${dcf_ticker['bear']['intrinsic_value']:.2f}  "
                f"Base IV: ${dcf_ticker['base']['intrinsic_value']:.2f}  "
                f"Bull IV: ${dcf_ticker['bull']['intrinsic_value']:.2f}  "
                f"| WACC: {dcf_ticker['wacc']:.1%}  "
                f"Growth source: {dcf_ticker['data_source']}  "
                f"FCF margin base: {dcf_ticker['fcf_margin_base']:.1%}"
            )
        else:
            dcf_anchors_str = "Not available — derive fair values from investor targets."

        # Collect all investor signals for this ticker
        investor_targets = {}
        for agent_key, signals in analyst_signals.items():
            if not isinstance(signals, dict) or ticker not in signals:
                continue
            sig = signals[ticker]
            if isinstance(sig, dict) and "price_target" in sig:
                investor_targets[agent_key] = {
                    "signal": sig.get("signal"),
                    "price_target": sig.get("price_target"),
                    "conviction": sig.get("conviction"),
                    "thesis_summary": sig.get("thesis_summary", "")[:200],
                }

        template = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", (
                "Ticker: {ticker}\n"
                "Current price: {current_price}\n\n"
                "DCF Engine anchors (deterministic, use as constraints):\n{dcf_anchors}\n\n"
                "Investor signals and price targets:\n{targets}\n\n"
                "Industry Intelligence Brief:\n{brief}\n\n"
                "2B — Competitive Landscape (informs bull/base scenario assumptions):\n{competitive}\n\n"
                "2E — Disruption Vectors (informs bear case scenario):\n{disruption}\n\n"
                "Debate adjudication (if any):\n{debate}\n\n"
                "Output format:\n"
                '{{\n'
                '  "bull": {{"assumptions": "...", "fair_value": float, "probability": float}},\n'
                '  "base": {{"assumptions": "...", "fair_value": float, "probability": float}},\n'
                '  "bear": {{"assumptions": "...", "fair_value": float, "probability": float}},\n'
                '  "expected_value": float,\n'
                '  "current_price": float,\n'
                '  "upside_pct": float\n'
                "}}"
            )),
        ])

        debate_text = str(
            state["data"].get("debate_result", {}).get(ticker, "No debate triggered")
        )

        prompt = template.invoke({
            "ticker": ticker,
            "current_price": current_price_val or "unknown",
            "dcf_anchors": dcf_anchors_str,
            "targets": str(investor_targets)[:2000],
            "brief": industry_brief[:25000],
            "competitive": competitive_section[:1500] if competitive_section else "Not available.",
            "disruption": disruption_section[:1500] if disruption_section else "Not available.",
            "debate": debate_text[:500],
        })

        result: ScenarioOutput = call_llm(
            prompt=prompt,
            pydantic_model=ScenarioOutput,
            agent_name=agent_id,
            state=state,
            max_tokens=2000,
            default_factory=lambda: ScenarioOutput(
                bull={"assumptions": "N/A", "fair_value": 0.0, "probability": 0.25},
                base={"assumptions": "N/A", "fair_value": 0.0, "probability": 0.5},
                bear={"assumptions": "N/A", "fair_value": 0.0, "probability": 0.25},
                expected_value=0.0,
                current_price=current_price_val or 0.0,
                upside_pct=0.0,
            ),
        )

        scenario_dict = result.model_dump()

        # ── CHECK 4: Independent EV arithmetic verification ───────────────
        # The LLM computes expected_value itself — verify it matches the
        # probability-weighted sum of fair values to catch rounding / formula errors.
        _fv_bull = _case_fv(result.bull)
        _fv_base = _case_fv(result.base)
        _fv_bear = _case_fv(result.bear)
        _p_bull  = _case_prob(result.bull, 0.25)
        _p_base  = _case_prob(result.base, 0.50)
        _p_bear  = _case_prob(result.bear, 0.25)
        _ev_computed = _fv_bull * _p_bull + _fv_base * _p_base + _fv_bear * _p_bear
        _ev_llm      = result.expected_value or 0.0
        _ev_tol      = max(_ev_computed * 0.01, 0.5)  # 1% tolerance or $0.50
        if abs(_ev_computed - _ev_llm) > _ev_tol and _ev_computed > 0:
            scenario_dict["ev_arithmetic_flag"] = (
                f"⚠ LLM EV ${_ev_llm:.2f} ≠ computed ${_ev_computed:.2f} "
                f"({abs(_ev_computed - _ev_llm) / _ev_computed:.1%} drift) — overriding with computed."
            )
            scenario_dict["expected_value"] = round(_ev_computed, 2)
            # Also recalculate upside_pct with corrected EV
            _cp_now = float(current_price_val or 1.0)
            if _cp_now > 0:
                scenario_dict["upside_pct"] = round((_ev_computed - _cp_now) / _cp_now * 100, 1)
        else:
            scenario_dict["ev_arithmetic_flag"] = None

        # ── §7 Framework: probability-weighted 12m Price Target ──────────
        # Uses DCF-engine's forward-multiple targets (bear/base/bull) combined with
        # the LLM-assigned scenario probabilities to produce a market-based 12m PT.
        # This is SEPARATE from intrinsic value (EV) — market pricing vs. fair value.
        _12m_raw = dcf_ticker.get("12m_targets", {}) if dcf_ticker else {}
        _bull_p  = _case_prob(result.bull, 0.25)
        _base_p  = _case_prob(result.base, 0.50)
        _bear_p  = _case_prob(result.bear, 0.25)
        _12m_bull = _12m_raw.get("bull")
        _12m_base = _12m_raw.get("base")
        _12m_bear = _12m_raw.get("bear")
        if _12m_bull and _12m_base and _12m_bear:
            _12m_pt = (_12m_bull * _bull_p + _12m_base * _base_p + _12m_bear * _bear_p)
            scenario_dict["12m_price_target"]       = round(_12m_pt, 2)
            scenario_dict["12m_targets_by_scenario"] = {
                "bear": _12m_bear, "base": _12m_base, "bull": _12m_bull,
            }
            scenario_dict["12m_pt_method"] = "EV/EBITDA or EV/Revenue forward multiple"
        else:
            # Fallback: use scenario fair values (same as IV — note in report)
            _fv_bull = _case_fv(result.bull)
            _fv_base = _case_fv(result.base)
            _fv_bear = _case_fv(result.bear)
            _12m_pt  = _fv_bull * _bull_p + _fv_base * _base_p + _fv_bear * _bear_p
            scenario_dict["12m_price_target"]       = round(_12m_pt, 2) if _12m_pt else None
            scenario_dict["12m_targets_by_scenario"] = {
                "bear": _fv_bear, "base": _fv_base, "bull": _fv_bull,
            }
            scenario_dict["12m_pt_method"] = "scenario fair values (forward multiple unavailable)"

        # ── §8 Reconciliation: IV vs 12m PT vs current price ─────────────
        _cp = float(current_price_val or 0.0)
        _ev = result.expected_value
        _pt = scenario_dict.get("12m_price_target") or 0.0
        # Blended IV = true probability-weighted average of all three DCF scenario IVs.
        # (Previously incorrectly assigned base case IV only.)
        _bear_iv_raw = float((dcf_ticker.get("bear") or {}).get("intrinsic_value") or 0) if dcf_ticker else 0
        _base_iv_raw = float((dcf_ticker.get("base") or {}).get("intrinsic_value") or 0) if dcf_ticker else 0
        _bull_iv_raw = float((dcf_ticker.get("bull") or {}).get("intrinsic_value") or 0) if dcf_ticker else 0
        _prob_sum = _p_bear + _p_base + _p_bull
        if _bear_iv_raw > 0 and _base_iv_raw > 0 and _bull_iv_raw > 0 and _prob_sum > 0:
            _blended_iv = round(
                _bear_iv_raw * (_p_bear / _prob_sum)
                + _base_iv_raw * (_p_base / _prob_sum)
                + _bull_iv_raw * (_p_bull / _prob_sum),
                2,
            )
        else:
            _blended_iv = _base_iv_raw or None  # fallback to base if any scenario missing
        if _cp > 0:
            scenario_dict["reconciliation"] = {
                "current_price":   _cp,
                "blended_iv":      _blended_iv,
                "expected_value":  _ev,
                "12m_price_target": _pt,
                "upside_to_pt_pct":   round((_pt - _cp) / _cp * 100, 1) if _pt and _cp else None,
                "upside_to_iv_pct":   round((_blended_iv - _cp) / _cp * 100, 1) if _blended_iv and _cp else None,
                "bear_iv":         dcf_ticker.get("bear", {}).get("intrinsic_value") if dcf_ticker else None,
                "downside_to_bear_pct": round(
                    (dcf_ticker.get("bear", {}).get("intrinsic_value", _cp) - _cp) / _cp * 100, 1
                ) if dcf_ticker and _cp else None,
            }
            _down = scenario_dict["reconciliation"].get("downside_to_bear_pct") or -1
            _up   = scenario_dict["reconciliation"].get("upside_to_pt_pct") or 0
            if _down < 0 and _up > 0:
                scenario_dict["reconciliation"]["skew_ratio"] = round(_up / abs(_down), 2)
            else:
                scenario_dict["reconciliation"]["skew_ratio"] = None

            # C8: flag when 12m PT diverges >5% from probability-weighted blended IV
            if _blended_iv and _pt and _blended_iv > 0:
                _pt_iv_gap = abs(_pt - _blended_iv) / _blended_iv
                if _pt_iv_gap > 0.02:
                    scenario_dict["reconciliation"]["pt_iv_gap_flag"] = (
                        f"⚠ 12m PT ${_pt:.2f} diverges {_pt_iv_gap:.1%} from blended IV "
                        f"${_blended_iv:.2f} — explicit methodology bridging required."
                    )

        # Expose PT upside at top level for downstream agents (distinct from EV upside)
        _recon = scenario_dict.get("reconciliation") or {}
        if _recon.get("upside_to_pt_pct") is not None:
            scenario_dict["pt_upside_pct"] = _recon["upside_to_pt_pct"]

        scenario_results[ticker] = scenario_dict
        progress.update_status(
            agent_id, ticker,
            f"EV upside: {(result.upside_pct or 0):.1f}% | 12m PT: ${scenario_dict.get('12m_price_target') or 0:.2f}"
        )

    state["data"]["scenario_analysis"] = scenario_results
    return state
