from langchain_core.messages import HumanMessage
from src.graph.state import AgentState, show_agent_reasoning
from src.utils.progress import progress
from src.tools.api import get_prices, prices_to_df, get_adv
import json
import numpy as np
import pandas as pd
from src.utils.api_key import get_api_key_from_state
from src.agents.industry.sector_prompts import (
    is_biopharma_sector, is_tech_sector, is_bank_sector, is_reit_sector,
)

##### Risk Management Agent #####
def risk_management_agent(state: AgentState, agent_id: str = "risk_management_agent"):
    """Controls position sizing based on volatility-adjusted risk factors for multiple tickers."""
    portfolio = state["data"]["portfolio"]
    data = state["data"]
    tickers = data["tickers"]
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    
    # Initialize risk analysis for each ticker
    risk_analysis = {}
    current_prices = {}  # Store prices here to avoid redundant API calls
    volatility_data = {}  # Store volatility metrics
    returns_by_ticker: dict[str, pd.Series] = {}  # For correlation analysis

    # First, fetch prices and calculate volatility for all relevant tickers
    all_tickers = set(tickers) | set(portfolio.get("positions", {}).keys())
    
    for ticker in all_tickers:
        progress.update_status(agent_id, ticker, "Fetching price data and calculating volatility")
        
        prices = get_prices(
            ticker=ticker,
            start_date=data["start_date"],
            end_date=data["end_date"],
            api_key=api_key,
        )

        if not prices:
            progress.update_status(agent_id, ticker, "Warning: No price data found")
            volatility_data[ticker] = {
                "daily_volatility": 0.05,  # Default fallback volatility (5% daily)
                "annualized_volatility": 0.05 * np.sqrt(252),
                "volatility_percentile": 100,  # Assume high risk if no data
                "data_points": 0
            }
            continue

        prices_df = prices_to_df(prices)
        
        if not prices_df.empty and len(prices_df) > 1:
            current_price = prices_df["close"].iloc[-1]
            current_prices[ticker] = current_price
            
            # Calculate volatility metrics
            volatility_metrics = calculate_volatility_metrics(prices_df)
            volatility_data[ticker] = volatility_metrics

            # Store returns for correlation analysis (use close-to-close returns)
            daily_returns = prices_df["close"].pct_change().dropna()
            if len(daily_returns) > 0:
                returns_by_ticker[ticker] = daily_returns
            
            progress.update_status(
                agent_id, 
                ticker, 
                f"Price: {current_price:.2f}, Ann. Vol: {volatility_metrics['annualized_volatility']:.1%}"
            )
        else:
            progress.update_status(agent_id, ticker, "Warning: Insufficient price data")
            current_prices[ticker] = 0
            volatility_data[ticker] = {
                "daily_volatility": 0.05,
                "annualized_volatility": 0.05 * np.sqrt(252),
                "volatility_percentile": 100,
                "data_points": len(prices_df) if not prices_df.empty else 0
            }

    # Build returns DataFrame aligned across tickers for correlation analysis
    correlation_matrix = None
    if len(returns_by_ticker) >= 2:
        try:
            returns_df = pd.DataFrame(returns_by_ticker).dropna(how="any")
            if returns_df.shape[1] >= 2 and returns_df.shape[0] >= 5:
                correlation_matrix = returns_df.corr()
        except Exception:
            correlation_matrix = None

    # Determine which tickers currently have exposure (non-zero absolute position)
    active_positions = {
        t for t, pos in portfolio.get("positions", {}).items()
        if abs(pos.get("long", 0) - pos.get("short", 0)) > 0
    }

    # Calculate total portfolio value based on current market prices (Net Liquidation Value)
    total_portfolio_value = portfolio.get("cash", 0.0)
    
    for ticker, position in portfolio.get("positions", {}).items():
        if ticker in current_prices:
            # Add market value of long positions
            total_portfolio_value += position.get("long", 0) * current_prices[ticker]
            # Subtract market value of short positions
            total_portfolio_value -= position.get("short", 0) * current_prices[ticker]
    
    progress.update_status(agent_id, None, f"Total portfolio value: {total_portfolio_value:.2f}")

    # Calculate volatility- and correlation-adjusted risk limits for each ticker
    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Calculating volatility- and correlation-adjusted limits")
        
        if ticker not in current_prices or current_prices[ticker] <= 0:
            progress.update_status(agent_id, ticker, "Failed: No valid price data")
            risk_analysis[ticker] = {
                "remaining_position_limit": 0.0,
                "current_price": 0.0,
                "reasoning": {
                    "error": "Missing price data for risk calculation"
                }
            }
            continue
            
        current_price = current_prices[ticker]
        vol_data = volatility_data.get(ticker, {})
        
        # Calculate current market value of this position
        position = portfolio.get("positions", {}).get(ticker, {})
        long_value = position.get("long", 0) * current_price
        short_value = position.get("short", 0) * current_price
        current_position_value = abs(long_value - short_value)  # Use absolute exposure
        
        # Volatility-adjusted limit pct
        vol_adjusted_limit_pct = calculate_volatility_adjusted_limit(
            vol_data.get("annualized_volatility", 0.25)
        )

        # Correlation adjustment
        corr_metrics = {
            "avg_correlation_with_active": None,
            "max_correlation_with_active": None,
            "top_correlated_tickers": [],
        }
        corr_multiplier = 1.0
        if correlation_matrix is not None and ticker in correlation_matrix.columns:
            # Compute correlations with active positions (exclude self)
            comparable = [t for t in active_positions if t in correlation_matrix.columns and t != ticker]
            if not comparable:
                # If no active positions, compare with all other available tickers
                comparable = [t for t in correlation_matrix.columns if t != ticker]
            if comparable:
                series = correlation_matrix.loc[ticker, comparable]
                # Drop NaNs just in case
                series = series.dropna()
                if len(series) > 0:
                    avg_corr = float(series.mean())
                    max_corr = float(series.max())
                    corr_metrics["avg_correlation_with_active"] = avg_corr
                    corr_metrics["max_correlation_with_active"] = max_corr
                    # Top 3 most correlated tickers
                    top_corr = series.sort_values(ascending=False).head(3)
                    corr_metrics["top_correlated_tickers"] = [
                        {"ticker": idx, "correlation": float(val)} for idx, val in top_corr.items()
                    ]
                    corr_multiplier = calculate_correlation_multiplier(avg_corr)
        
        # Combine volatility and correlation adjustments
        combined_limit_pct = vol_adjusted_limit_pct * corr_multiplier
        # Convert to dollar position limit
        position_limit = total_portfolio_value * combined_limit_pct
        
        # Calculate remaining limit for this position
        remaining_position_limit = position_limit - current_position_value
        
        # Ensure we don't exceed available cash
        max_position_size = min(remaining_position_limit, portfolio.get("cash", 0))
        
        risk_analysis[ticker] = {
            "remaining_position_limit": float(max_position_size),
            "current_price": float(current_price),
            "volatility_metrics": {
                "daily_volatility": float(vol_data.get("daily_volatility", 0.05)),
                "annualized_volatility": float(vol_data.get("annualized_volatility", 0.25)),
                "volatility_percentile": float(vol_data.get("volatility_percentile", 100)),
                "data_points": int(vol_data.get("data_points", 0))
            },
            "correlation_metrics": corr_metrics,
            "reasoning": {
                "portfolio_value": float(total_portfolio_value),
                "current_position_value": float(current_position_value),
                "base_position_limit_pct": float(vol_adjusted_limit_pct),
                "correlation_multiplier": float(corr_multiplier),
                "combined_position_limit_pct": float(combined_limit_pct),
                "position_limit": float(position_limit),
                "remaining_limit": float(remaining_position_limit),
                "available_cash": float(portfolio.get("cash", 0)),
                "risk_adjustment": f"Volatility x Correlation adjusted: {combined_limit_pct:.1%} (base {vol_adjusted_limit_pct:.1%})"
            },
        }
        
        progress.update_status(
            agent_id, 
            ticker, 
            f"Adj. limit: {combined_limit_pct:.1%}, Available: ${max_position_size:.0f}"
        )

    progress.update_status(agent_id, None, "Done")

    message = HumanMessage(
        content=json.dumps(risk_analysis),
        name=agent_id,
    )

    if state["metadata"]["show_reasoning"]:
        show_agent_reasoning(risk_analysis, "Volatility-Adjusted Risk Management Agent")

    # Add the signal to the analyst_signals list
    state["data"]["analyst_signals"][agent_id] = risk_analysis

    return {
        "messages": state["messages"] + [message],
        "data": data,
    }


def calculate_volatility_metrics(prices_df: pd.DataFrame, lookback_days: int = 60) -> dict:
    """Calculate comprehensive volatility metrics from price data."""
    if len(prices_df) < 2:
        return {
            "daily_volatility": 0.05,
            "annualized_volatility": 0.05 * np.sqrt(252),
            "volatility_percentile": 100,
            "data_points": len(prices_df)
        }
    
    # Calculate daily returns
    daily_returns = prices_df["close"].pct_change().dropna()
    
    if len(daily_returns) < 2:
        return {
            "daily_volatility": 0.05,
            "annualized_volatility": 0.05 * np.sqrt(252),
            "volatility_percentile": 100,
            "data_points": len(daily_returns)
        }
    
    # Use the most recent lookback_days for volatility calculation
    recent_returns = daily_returns.tail(min(lookback_days, len(daily_returns)))
    
    # Calculate volatility metrics
    daily_vol = recent_returns.std()
    annualized_vol = daily_vol * np.sqrt(252)  # Annualize assuming 252 trading days
    
    # Calculate percentile rank of recent volatility vs historical volatility
    if len(daily_returns) >= 30:  # Need sufficient history for percentile calculation
        # Calculate 30-day rolling volatility for the full history
        rolling_vol = daily_returns.rolling(window=30).std().dropna()
        if len(rolling_vol) > 0:
            # Compare current volatility against historical rolling volatilities
            current_vol_percentile = (rolling_vol <= daily_vol).mean() * 100
        else:
            current_vol_percentile = 50  # Default to median
    else:
        current_vol_percentile = 50  # Default to median if insufficient data
    
    return {
        "daily_volatility": float(daily_vol) if not np.isnan(daily_vol) else 0.025,
        "annualized_volatility": float(annualized_vol) if not np.isnan(annualized_vol) else 0.25,
        "volatility_percentile": float(current_vol_percentile) if not np.isnan(current_vol_percentile) else 50.0,
        "data_points": len(recent_returns)
    }


def calculate_volatility_adjusted_limit(annualized_volatility: float) -> float:
    """
    Calculate position limit as percentage of portfolio based on volatility.
    
    Logic:
    - Low volatility (<15%): Up to 25% allocation
    - Medium volatility (15-30%): 15-20% allocation  
    - High volatility (>30%): 10-15% allocation
    - Very high volatility (>50%): Max 10% allocation
    """
    base_limit = 0.20  # 20% baseline
    
    if annualized_volatility < 0.15:  # Low volatility
        # Allow higher allocation for stable stocks
        vol_multiplier = 1.25  # Up to 25%
    elif annualized_volatility < 0.30:  # Medium volatility  
        # Standard allocation with slight adjustment based on volatility
        vol_multiplier = 1.0 - (annualized_volatility - 0.15) * 0.5  # 20% -> 12.5%
    elif annualized_volatility < 0.50:  # High volatility
        # Reduce allocation significantly
        vol_multiplier = 0.75 - (annualized_volatility - 0.30) * 0.5  # 15% -> 5%
    else:  # Very high volatility (>50%)
        # Minimum allocation for very risky stocks
        vol_multiplier = 0.50  # Max 10%
    
    # Apply bounds to ensure reasonable limits
    vol_multiplier = max(0.25, min(1.25, vol_multiplier))  # 5% to 25% range
    
    return base_limit * vol_multiplier


def calculate_correlation_multiplier(avg_correlation: float) -> float:
    """Map average correlation to an adjustment multiplier.
    - Very high correlation (>= 0.8): reduce limit sharply (0.7x)
    - High correlation (0.6-0.8): reduce (0.85x)
    - Moderate correlation (0.4-0.6): neutral (1.0x)
    - Low correlation (0.2-0.4): slight increase (1.05x)
    - Very low correlation (< 0.2): increase (1.10x)
    """
    if avg_correlation >= 0.80:
        return 0.70
    if avg_correlation >= 0.60:
        return 0.85
    if avg_correlation >= 0.40:
        return 1.00
    if avg_correlation >= 0.20:
        return 1.05
    return 1.10


# ---------------------------------------------------------------------------
# Phase 8 — Advanced Dual-Layer Risk Manager
# ---------------------------------------------------------------------------

def run_advanced_risk_manager(state: AgentState) -> AgentState:
    """
    Phase 8: dual-layer risk filtering.

    Level 1 — Agent-level validation:
      Each investor's signal carries a quality check. If the agent's own cot_log
      doesn't demonstrate the required analysis, conviction is penalised.
      - Graham: must show margin of safety ≥ 33% language
      - Druckenmiller: must align with current macro regime
      - Wood: must include a TAM or 5-year model reference
      - Burry: must reference forensic / accounting analysis

    Level 2 — Portfolio-level constraints (from CLAUDE.md §8):
      - Max single position: 15% of portfolio (10% in high-volatility regime)
      - Max sector concentration: 35%
      - Sector overlays: Biopharma FDA cap, Energy VIX cap, Crypto jurisdiction, Financials late-cycle

    Output stored in state["data"]["analyst_signals"]["advanced_risk_manager"][ticker].
    """
    agent_id = "advanced_risk_manager"
    tickers = state["data"]["tickers"]
    analyst_signals = state["data"].get("analyst_signals", {})
    macro_regime = state["data"].get("macro_regime", {})
    sector = state["data"].get("sector", "Tech")
    portfolio = state["data"].get("portfolio", {})
    volatility_regime = macro_regime.get("volatility_regime", "medium")
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")

    # Base position cap from macro regime.
    # macro_regime already folds high-volatility into position_size_cap (0.5 → 7.5%).
    # Keeping a separate 0.10 floor for high-vol caused a double-penalty (5% instead
    # of the intended 7.5%).  Use 0.15 as the single baseline; position_cap does the scaling.
    position_cap = state["data"].get("position_size_cap", 1.0)
    max_single_position = 0.15 * position_cap

    # Phase 2.5 earnings quality — read once, consumed as remarks per ticker
    eq_all = state["data"].get("earnings_quality", {})

    risk_output: dict[str, dict] = {}

    for ticker in tickers:
        flags: list[str] = []
        conviction_adjustments: dict[str, float] = {}

        # ── Earnings Quality remark (Pathway 3) ──────────────────────────────
        # NOTE: This is informational only. No weight changes are applied here.
        # The EarningsQualityAgent output is surfaced so the portfolio manager
        # and any downstream audit trail can see the quality signal alongside
        # risk flags, without it mechanically altering approved_size_pct.
        # Position sizing impact is handled exclusively via the Value Trap agent
        # verdict (TRAP RISK HIGH → 50% cap in the Portfolio Manager formula).
        eq_remarks: list[str] = []
        eq = eq_all.get(ticker, {})
        if eq and eq.get("data_quality") in ("FULL", "PARTIAL"):
            verdict  = eq.get("quality_verdict", "UNKNOWN")
            score    = eq.get("overall_quality_score", 0.0)
            pe_risk  = eq.get("pre_earnings_risk", "UNKNOWN")
            eq_flags = eq.get("flags", [])
            eq_remarks.append(
                f"[EQ-REMARK] Earnings Quality verdict={verdict} ({score:.1f}/10) | "
                f"pre_earnings_risk={pe_risk} — no weight change applied; "
                f"sizing impact flows through Value Trap verdict only."
            )
            # Surface individual metric states as informational remarks
            for metric, key, flag_key in [
                ("Accrual",        "accrual_ratio_avg",       "accrual_flag"),
                ("CashConversion", "cash_conversion_ratio",   "cash_conversion_flag"),
                ("FCF/NI",         "fcf_ni_divergence",       "fcf_ni_divergence"),
                ("AR/RevDiv",      "ar_revenue_divergence",   "ar_revenue_divergence"),
            ]:
                flag_val = eq.get(flag_key, "UNKNOWN")
                val      = eq.get(key)
                if flag_val in ("RED", "AMBER") and val is not None:
                    eq_remarks.append(
                        f"  [EQ-REMARK] {metric}={flag_val} (value={val}) — "
                        "informational; consult Value Trap audit for position impact."
                    )
            # First computed flag for context
            if eq_flags:
                eq_remarks.append(f"  [EQ-REMARK] Top flag: {eq_flags[0]}")

        # --- Level 1: agent-level quality checks ---
        for agent_key, signals in analyst_signals.items():
            if not isinstance(signals, dict) or ticker not in signals:
                continue
            sig = signals[ticker]
            if not isinstance(sig, dict):
                continue
            cot = str(sig.get("cot_log") or sig.get("reasoning") or "").lower()

            if agent_key in ("graham", "ben_graham"):
                if "margin of safety" not in cot and "33%" not in cot:
                    conviction_adjustments[agent_key] = 0.5
                    flags.append(f"{agent_key}: margin of safety not confirmed — conviction halved")

            elif agent_key in ("druckenmiller", "stanley_druckenmiller"):
                risk_appetite = macro_regime.get("risk_appetite", "risk-on")
                signal_val = sig.get("signal", "HOLD")
                if risk_appetite == "risk-off" and signal_val in ("BUY",):
                    conviction_adjustments[agent_key] = 0.5
                    flags.append(f"{agent_key}: BUY in risk-off regime — conviction halved")

            elif agent_key in ("cathie_wood",):
                if "tam" not in cot and "5-year" not in cot and "5 year" not in cot:
                    conviction_adjustments[agent_key] = 0.7
                    flags.append(f"{agent_key}: no TAM/5-year model found — conviction reduced")

            elif agent_key in ("burry", "michael_burry"):
                if "forensic" not in cot and "accounting" not in cot and "footnote" not in cot:
                    conviction_adjustments[agent_key] = 0.7
                    flags.append(f"{agent_key}: no forensic accounting check found — conviction reduced")

        # Apply conviction adjustments back into analyst_signals (in-place)
        for agent_key, multiplier in conviction_adjustments.items():
            if agent_key in analyst_signals and ticker in analyst_signals[agent_key]:
                orig = analyst_signals[agent_key][ticker].get("conviction", 5)
                analyst_signals[agent_key][ticker]["conviction"] = max(1, round(orig * multiplier))

        # --- Level 2: portfolio-level constraints ---
        total_portfolio_value = portfolio.get("cash", 100000.0)
        positions = portfolio.get("positions", {})
        for t, pos in positions.items():
            total_portfolio_value += pos.get("long", 0) * 100  # approximate if no price

        # Sector overlay caps
        approved_size_pct = max_single_position
        sector_flags: list[str] = []

        if is_biopharma_sector(sector):
            approved_size_pct = min(approved_size_pct, 0.05)
            sector_flags.append("Biopharma: capped at 5% (FDA binary risk)")

        elif is_bank_sector(sector):
            rate_dir = macro_regime.get("rate_direction", "neutral")
            if rate_dir == "tightening":
                approved_size_pct *= 0.7
                sector_flags.append("Financials: late-cycle tightening → 30% size reduction")

        elif sector == "Crypto":
            approved_size_pct = min(approved_size_pct, 0.08)
            sector_flags.append("Crypto: jurisdiction risk cap at 8%")

        # Concentration check — sum existing positions in same sector
        # (simplified: use total positions count as a proxy)
        existing_position_count = sum(
            1 for t, pos in positions.items()
            if pos.get("long", 0) > 0 or pos.get("short", 0) > 0
        )
        if existing_position_count > 5:
            # Reduce new position size when already heavily invested
            approved_size_pct = min(approved_size_pct, 0.35 / (existing_position_count + 1))
            flags.append(f"Concentration: {existing_position_count} existing positions — size reduced")

        # --- Level 3: Liquidity risk check ---
        # At 20% ADV participation rate, how many days does it take to exit
        # the proposed position?  Thresholds: <3d GREEN, 3–7d AMBER, >7d RED.
        # RED: cap approved_size_pct at 50% of current value.
        liquidity_flag        = "GREEN"
        liquidity_days        = None
        liquidity_adv_dollars = None
        liquidity_remarks: list[str] = []

        adv_data = get_adv(ticker, days=30, api_key=api_key)
        if adv_data and adv_data.get("adv_dollars", 0) > 0:
            adv_dollars           = adv_data["adv_dollars"]
            liquidity_adv_dollars = adv_dollars
            position_dollars      = total_portfolio_value * approved_size_pct
            liquidity_days        = position_dollars / (adv_dollars * 0.20)

            if liquidity_days > 7:
                liquidity_flag    = "RED"
                approved_size_pct = approved_size_pct * 0.50
                liquidity_remarks.append(
                    f"LIQUIDITY RED: {liquidity_days:.1f}d to exit at 20% ADV "
                    f"(ADV ${adv_dollars:,.0f}/day) — position capped at 50%"
                )
            elif liquidity_days > 3:
                liquidity_flag = "AMBER"
                liquidity_remarks.append(
                    f"LIQUIDITY AMBER: {liquidity_days:.1f}d to exit at 20% ADV "
                    f"(ADV ${adv_dollars:,.0f}/day)"
                )
            else:
                liquidity_remarks.append(
                    f"LIQUIDITY GREEN: {liquidity_days:.1f}d to exit at 20% ADV "
                    f"(ADV ${adv_dollars:,.0f}/day)"
                )
        else:
            liquidity_remarks.append("LIQUIDITY: ADV data unavailable — check skipped")

        approved_dollar = total_portfolio_value * approved_size_pct

        risk_output[ticker] = {
            "approved_size_pct":              approved_size_pct,
            "approved_dollar":                approved_dollar,
            "max_single_position_pct":        max_single_position,
            "level1_flags":                   flags,
            "sector_flags":                   sector_flags,
            "conviction_adjustments_applied": conviction_adjustments,
            # Pathway 3: informational remarks from EarningsQualityAgent.
            # These do NOT change approved_size_pct — they are audit-trail
            # annotations surfaced to the Portfolio Manager's rationale.
            "earnings_quality_remarks":       eq_remarks,
            # Liquidity layer (Level 3)
            "liquidity_flag":                 liquidity_flag,
            "liquidity_days_to_exit":         liquidity_days,
            "liquidity_adv_dollars":          liquidity_adv_dollars,
            "liquidity_remarks":              liquidity_remarks,
        }

    state["data"]["analyst_signals"][agent_id] = risk_output
    return state
