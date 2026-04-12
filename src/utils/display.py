from colorama import Fore, Style
from tabulate import tabulate
from .analysts import ANALYST_ORDER
import os
import json


def sort_agent_signals(signals):
    """Sort agent signals in a consistent order."""
    # Create order mapping from ANALYST_ORDER
    analyst_order = {display: idx for idx, (display, _) in enumerate(ANALYST_ORDER)}
    analyst_order["Risk Management"] = len(ANALYST_ORDER)  # Add Risk Management at the end

    return sorted(signals, key=lambda x: analyst_order.get(x[0], 999))


def print_trading_output(result: dict) -> None:
    """
    Print formatted trading results with colored tables for multiple tickers.

    Args:
        result (dict): Dictionary containing decisions and analyst signals for multiple tickers
    """
    decisions = result.get("decisions")
    if not decisions:
        print(f"{Fore.RED}No trading decisions available{Style.RESET_ALL}")
        return

    # Print decisions for each ticker
    for ticker, decision in decisions.items():
        print(f"\n{Fore.WHITE}{Style.BRIGHT}Analysis for {Fore.CYAN}{ticker}{Style.RESET_ALL}")
        print(f"{Fore.WHITE}{Style.BRIGHT}{'=' * 50}{Style.RESET_ALL}")

        # Prepare analyst signals table for this ticker
        table_data = []
        for agent, signals in result.get("analyst_signals", {}).items():
            if ticker not in signals:
                continue
                
            # Skip Risk Management agent in the signals section
            if agent == "risk_management_agent":
                continue

            signal = signals[ticker]
            agent_name = agent.replace("_agent", "").replace("_", " ").title()
            signal_type = signal.get("signal", "").upper()
            confidence = signal.get("confidence", 0)

            signal_color = {
                "BULLISH": Fore.GREEN,
                "BEARISH": Fore.RED,
                "NEUTRAL": Fore.YELLOW,
            }.get(signal_type, Fore.WHITE)
            
            # Get reasoning if available
            reasoning_str = ""
            if "reasoning" in signal and signal["reasoning"]:
                reasoning = signal["reasoning"]
                
                # Handle different types of reasoning (string, dict, etc.)
                if isinstance(reasoning, str):
                    reasoning_str = reasoning
                elif isinstance(reasoning, dict):
                    # Convert dict to string representation
                    reasoning_str = json.dumps(reasoning, indent=2)
                else:
                    # Convert any other type to string
                    reasoning_str = str(reasoning)
                
                # Wrap long reasoning text to make it more readable
                wrapped_reasoning = ""
                current_line = ""
                # Use a fixed width of 60 characters to match the table column width
                max_line_length = 60
                for word in reasoning_str.split():
                    if len(current_line) + len(word) + 1 > max_line_length:
                        wrapped_reasoning += current_line + "\n"
                        current_line = word
                    else:
                        if current_line:
                            current_line += " " + word
                        else:
                            current_line = word
                if current_line:
                    wrapped_reasoning += current_line
                
                reasoning_str = wrapped_reasoning

            table_data.append(
                [
                    f"{Fore.CYAN}{agent_name}{Style.RESET_ALL}",
                    f"{signal_color}{signal_type}{Style.RESET_ALL}",
                    f"{Fore.WHITE}{confidence}%{Style.RESET_ALL}",
                    f"{Fore.WHITE}{reasoning_str}{Style.RESET_ALL}",
                ]
            )

        # Sort the signals according to the predefined order
        table_data = sort_agent_signals(table_data)

        print(f"\n{Fore.WHITE}{Style.BRIGHT}AGENT ANALYSIS:{Style.RESET_ALL} [{Fore.CYAN}{ticker}{Style.RESET_ALL}]")
        print(
            tabulate(
                table_data,
                headers=[f"{Fore.WHITE}Agent", "Signal", "Confidence", "Reasoning"],
                tablefmt="grid",
                colalign=("left", "center", "right", "left"),
            )
        )

        # Print Trading Decision Table
        action = decision.get("action", "").upper()
        action_color = {
            "BUY": Fore.GREEN,
            "SELL": Fore.RED,
            "HOLD": Fore.YELLOW,
            "COVER": Fore.GREEN,
            "SHORT": Fore.RED,
        }.get(action, Fore.WHITE)

        # Get reasoning and format it
        reasoning = decision.get("reasoning", "")
        # Wrap long reasoning text to make it more readable
        wrapped_reasoning = ""
        if reasoning:
            current_line = ""
            # Use a fixed width of 60 characters to match the table column width
            max_line_length = 60
            for word in reasoning.split():
                if len(current_line) + len(word) + 1 > max_line_length:
                    wrapped_reasoning += current_line + "\n"
                    current_line = word
                else:
                    if current_line:
                        current_line += " " + word
                    else:
                        current_line = word
            if current_line:
                wrapped_reasoning += current_line

        decision_data = [
            ["Action", f"{action_color}{action}{Style.RESET_ALL}"],
            ["Quantity", f"{action_color}{decision.get('quantity')}{Style.RESET_ALL}"],
            [
                "Confidence",
                f"{Fore.WHITE}{decision.get('confidence'):.1f}%{Style.RESET_ALL}",
            ],
            ["Reasoning", f"{Fore.WHITE}{wrapped_reasoning}{Style.RESET_ALL}"],
        ]
        
        print(f"\n{Fore.WHITE}{Style.BRIGHT}TRADING DECISION:{Style.RESET_ALL} [{Fore.CYAN}{ticker}{Style.RESET_ALL}]")
        print(tabulate(decision_data, tablefmt="grid", colalign=("left", "left")))

    # Print Portfolio Summary
    print(f"\n{Fore.WHITE}{Style.BRIGHT}PORTFOLIO SUMMARY:{Style.RESET_ALL}")
    portfolio_data = []
    
    # Extract portfolio manager reasoning (common for all tickers)
    portfolio_manager_reasoning = None
    for ticker, decision in decisions.items():
        if decision.get("reasoning"):
            portfolio_manager_reasoning = decision.get("reasoning")
            break
            
    analyst_signals = result.get("analyst_signals", {})
    for ticker, decision in decisions.items():
        action = decision.get("action", "").upper()
        action_color = {
            "BUY": Fore.GREEN,
            "SELL": Fore.RED,
            "HOLD": Fore.YELLOW,
            "COVER": Fore.GREEN,
            "SHORT": Fore.RED,
        }.get(action, Fore.WHITE)

        # Calculate analyst signal counts
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        if analyst_signals:
            for agent, signals in analyst_signals.items():
                if ticker in signals:
                    signal = signals[ticker].get("signal", "").upper()
                    if signal == "BULLISH":
                        bullish_count += 1
                    elif signal == "BEARISH":
                        bearish_count += 1
                    elif signal == "NEUTRAL":
                        neutral_count += 1

        portfolio_data.append(
            [
                f"{Fore.CYAN}{ticker}{Style.RESET_ALL}",
                f"{action_color}{action}{Style.RESET_ALL}",
                f"{action_color}{decision.get('quantity')}{Style.RESET_ALL}",
                f"{Fore.WHITE}{decision.get('confidence'):.1f}%{Style.RESET_ALL}",
                f"{Fore.GREEN}{bullish_count}{Style.RESET_ALL}",
                f"{Fore.RED}{bearish_count}{Style.RESET_ALL}",
                f"{Fore.YELLOW}{neutral_count}{Style.RESET_ALL}",
            ]
        )

    headers = [
        f"{Fore.WHITE}Ticker",
        f"{Fore.WHITE}Action",
        f"{Fore.WHITE}Quantity",
        f"{Fore.WHITE}Confidence",
        f"{Fore.WHITE}Bullish",
        f"{Fore.WHITE}Bearish",
        f"{Fore.WHITE}Neutral",
    ]
    
    # Print the portfolio summary table
    print(
        tabulate(
            portfolio_data,
            headers=headers,
            tablefmt="grid",
            colalign=("left", "center", "right", "right", "center", "center", "center"),
        )
    )
    
    # Print Portfolio Manager's reasoning if available
    if portfolio_manager_reasoning:
        # Handle different types of reasoning (string, dict, etc.)
        reasoning_str = ""
        if isinstance(portfolio_manager_reasoning, str):
            reasoning_str = portfolio_manager_reasoning
        elif isinstance(portfolio_manager_reasoning, dict):
            # Convert dict to string representation
            reasoning_str = json.dumps(portfolio_manager_reasoning, indent=2)
        else:
            # Convert any other type to string
            reasoning_str = str(portfolio_manager_reasoning)
            
        # Wrap long reasoning text to make it more readable
        wrapped_reasoning = ""
        current_line = ""
        # Use a fixed width of 60 characters to match the table column width
        max_line_length = 60
        for word in reasoning_str.split():
            if len(current_line) + len(word) + 1 > max_line_length:
                wrapped_reasoning += current_line + "\n"
                current_line = word
            else:
                if current_line:
                    current_line += " " + word
                else:
                    current_line = word
        if current_line:
            wrapped_reasoning += current_line
            
        print(f"\n{Fore.WHITE}{Style.BRIGHT}Portfolio Strategy:{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{wrapped_reasoning}{Style.RESET_ALL}")


def print_advanced_output(result: dict, show_reasoning: bool = False) -> None:
    """
    Rich output for the advanced 10-phase pipeline.

    Sections (per ticker):
      1. Macro Regime banner
      2. Sector + Industry Brief excerpt
      3. Agent Signals table  (signal | conviction/10 | horizon | target | thesis)
         [+ cot_log block per agent when show_reasoning=True]
      4. Debate Round result  (only if debate occurred)
      5. Phase 7 Analytics    (scenario EV upside | power law score | value trap)
      6. Risk Manager flags
      7. Final Portfolio Decision
    """
    W = Style.BRIGHT + Fore.WHITE
    R = Style.RESET_ALL

    def _wrap(text: str, width: int = 58) -> str:
        """Hard-wrap text at word boundaries."""
        if not text:
            return ""
        words = str(text).split()
        lines, cur = [], ""
        for word in words:
            if len(cur) + len(word) + 1 > width:
                lines.append(cur)
                cur = word
            else:
                cur = (cur + " " + word).strip()
        if cur:
            lines.append(cur)
        return "\n".join(lines)

    SIGNAL_COLOR = {
        "BUY":   Fore.GREEN,
        "SELL":  Fore.RED,
        "SHORT": Fore.RED,
        "HOLD":  Fore.YELLOW,
        "COVER": Fore.GREEN,
    }

    AGENT_DISPLAY = {
        "buffett":        "Warren Buffett",
        "munger":         "Charlie Munger",
        "graham":         "Ben Graham",
        "damodaran":      "Aswath Damodaran",
        "lynch":          "Peter Lynch",
        "fisher":         "Phil Fisher",
        "ackman":         "Bill Ackman",
        "cathie_wood":    "Cathie Wood",
        "burry":          "Michael Burry",
        "pabrai":         "Mohnish Pabrai",
        "druckenmiller":  "Stanley Druckenmiller",
        "jhunjhunwala":   "Rakesh Jhunjhunwala",
        "fundamentals_analyst":      "Fundamentals Analyst",
        "growth_analyst":            "Growth Analyst",
        "news_sentiment_analyst":    "News Sentiment Analyst",
        "sentiment_analyst":         "Sentiment Analyst",
        "technical_analyst":         "Technical Analyst",
        "valuation_analyst":         "Valuation Analyst",
    }

    decisions       = result.get("decisions", {})
    analyst_signals = result.get("analyst_signals", {})
    debate_result   = result.get("debate_result") or {}
    scenario        = result.get("scenario_analysis") or {}
    power_law       = result.get("power_law_analysis") or {}
    value_trap      = result.get("value_trap_analysis") or {}
    macro           = result.get("macro_regime") or {}
    sector          = result.get("sector", "—")
    industry_brief  = result.get("industry_brief", "")

    if not decisions:
        print(f"{Fore.RED}No trading decisions available{R}")
        return

    # ── 1. Macro Regime Banner ─────────────────────────────────────────────
    regime_line = (
        f"  Risk Appetite: {Fore.CYAN}{macro.get('risk_appetite', '—')}{R}  |  "
        f"Rates: {Fore.CYAN}{macro.get('rate_direction', '—')}{R}  |  "
        f"Dollar: {Fore.CYAN}{macro.get('dollar_trend', '—')}{R}  |  "
        f"Vol Regime: {Fore.CYAN}{macro.get('volatility_regime', '—')}{R}"
    )
    print(f"\n{W}{'='*70}{R}")
    print(f"{W}  MACRO REGIME{R}")
    print(regime_line)
    if macro.get("regime_notes"):
        print(f"  {Fore.WHITE}{macro['regime_notes']}{R}")

    # ── 2. Sector + Industry Brief ─────────────────────────────────────────
    print(f"\n{W}  SECTOR: {Fore.CYAN}{sector}{R}")
    if industry_brief:
        for line in industry_brief.splitlines()[:60]:
            print(f"  {Fore.WHITE}{line}{R}")

    # ── Per-Ticker sections ────────────────────────────────────────────────
    for ticker, decision in decisions.items():
        print(f"\n{W}{'='*70}{R}")
        print(f"{W}  ANALYSIS: {Fore.CYAN}{ticker}{R}")
        print(f"{W}{'='*70}{R}")

        # ── 3. Agent Signals Table ─────────────────────────────────────────
        agent_rows = []
        skip_agents = {"risk_management_agent", "advanced_risk_manager"}
        for agent_key, sig_map in analyst_signals.items():
            if agent_key in skip_agents:
                continue
            if not isinstance(sig_map, dict) or ticker not in sig_map:
                continue
            sig = sig_map[ticker]
            if not isinstance(sig, dict):
                continue

            raw_signal  = sig.get("signal", "—").upper()
            conviction  = sig.get("conviction", "—")
            horizon     = sig.get("time_horizon", "—")
            pt          = sig.get("price_target")
            thesis      = sig.get("thesis_summary", "")
            key_risks   = sig.get("key_risks", [])

            sc = SIGNAL_COLOR.get(raw_signal, Fore.WHITE)
            display_name = AGENT_DISPLAY.get(agent_key, agent_key.replace("_", " ").title())

            # Include first key risk in the thesis column if present
            thesis_text = thesis
            if key_risks:
                thesis_text += f"  [!] {key_risks[0]}"

            agent_rows.append([
                f"{Fore.CYAN}{display_name}{R}",
                f"{sc}{raw_signal}{R}",
                f"{Fore.WHITE}{conviction}/10{R}",
                f"{Fore.WHITE}{horizon}{R}",
                f"{Fore.WHITE}${pt:.2f}{R}" if isinstance(pt, (int, float)) else f"{Fore.WHITE}—{R}",
                f"{Fore.WHITE}{_wrap(thesis_text, 55)}{R}",
            ])

        # Sort: BUY first, then HOLD, then SELL/SHORT; within each group by conviction desc
        def _sort_key(row):
            raw = row[1].replace(Fore.GREEN, "").replace(Fore.RED, "").replace(Fore.YELLOW, "").replace(R, "").strip()
            order = {"BUY": 0, "HOLD": 1, "SELL": 2, "SHORT": 2, "COVER": 0}.get(raw, 9)
            conv_str = row[2].replace(Fore.WHITE, "").replace(R, "").replace("/10", "").strip()
            conv = int(conv_str) if conv_str.isdigit() else 0
            return (order, -conv)
        agent_rows.sort(key=_sort_key)

        print(f"\n{W}AGENT SIGNALS{R}")
        print(tabulate(
            agent_rows,
            headers=[f"{W}Agent", "Signal", "Conviction", "Horizon", "Target", f"Thesis / Key Risk{R}"],
            tablefmt="grid",
            colalign=("left", "center", "center", "center", "right", "left"),
        ))

        # ── 3b. Chain-of-Thought Reasoning (--show-reasoning) ─────────────
        if show_reasoning:
            print(f"\n{W}AGENT CHAIN-OF-THOUGHT REASONING{R}")
            skip_agents = {"risk_management_agent", "advanced_risk_manager"}
            for agent_key, sig_map in analyst_signals.items():
                if agent_key in skip_agents:
                    continue
                if not isinstance(sig_map, dict) or ticker not in sig_map:
                    continue
                sig = sig_map[ticker]
                if not isinstance(sig, dict):
                    continue
                cot = sig.get("cot_log", "").strip()
                if not cot:
                    continue
                display_name = AGENT_DISPLAY.get(agent_key, agent_key.replace("_", " ").title())
                raw_signal = sig.get("signal", "—").upper()
                sc = SIGNAL_COLOR.get(raw_signal, Fore.WHITE)
                print(f"\n  {Fore.CYAN}{display_name}{R} [{sc}{raw_signal}{R}]")
                print(f"  {Fore.WHITE}{'-'*66}{R}")
                for line in cot.splitlines():
                    print(f"  {Fore.WHITE}{line}{R}")

        # ── 4. Debate Round ────────────────────────────────────────────────
        dr = debate_result.get(ticker)
        if dr:
            adj_signal = dr.get("adjudicated_signal", "—").upper()
            adj_conv   = dr.get("adjudicated_conviction", "—")
            adj_color  = SIGNAL_COLOR.get(adj_signal, Fore.WHITE)
            # DebateResult stores agent_a (bull) and agent_b (bear)
            bull_key = dr.get("agent_a", "")
            bear_key = dr.get("agent_b", "")
            bull_name = AGENT_DISPLAY.get(bull_key, bull_key.replace("_", " ").title() if bull_key else "—")
            bear_name = AGENT_DISPLAY.get(bear_key, bear_key.replace("_", " ").title() if bear_key else "—")
            adjudication  = dr.get("adjudication", "")
            disagreement  = dr.get("disagreement_core", "")
            debate_rows = [
                ["Bull advocate",      f"{Fore.GREEN}{bull_name}{R}"],
                [f"  Rebuttal",        f"{Fore.WHITE}{_wrap(dr.get('agent_a_rebuttal', ''), 62)}{R}"],
                ["Bear advocate",      f"{Fore.RED}{bear_name}{R}"],
                [f"  Rebuttal",        f"{Fore.WHITE}{_wrap(dr.get('agent_b_rebuttal', ''), 62)}{R}"],
                ["Core disagreement",  f"{Fore.WHITE}{_wrap(disagreement, 62)}{R}"],
                ["Adjudicated signal", f"{adj_color}{Style.BRIGHT}{adj_signal}  conviction {adj_conv}/10{R}"],
                ["Moderator ruling",   f"{Fore.WHITE}{_wrap(adjudication, 62)}{R}"],
            ]
            print(f"\n{W}DEBATE ROUND — TRIGGERED (≥3 BUY vs ≥3 SELL){R}")
            print(tabulate(debate_rows, tablefmt="grid", colalign=("left", "left")))
        else:
            print(f"\n{Fore.YELLOW}  Debate: SKIPPED — no strong conflict (< 3 BUY and 3 SELL on same ticker){R}")

        # ── 5. Phase 7 Analytics ───────────────────────────────────────────
        scen = scenario.get(ticker, {})
        pl   = power_law.get(ticker, {})
        trap = value_trap.get(ticker, {})

        trap_verdict = trap.get("overall_verdict", "—")
        trap_color = (Fore.RED if "HIGH" in str(trap_verdict).upper()
                      else Fore.YELLOW if "MEDIUM" in str(trap_verdict).upper()
                      else Fore.GREEN)

        # Scenario: ScenarioCase nested dicts with fair_value + probability
        bull_fv = scen.get("bull", {}).get("fair_value")
        base_fv = scen.get("base", {}).get("fair_value")
        bear_fv = scen.get("bear", {}).get("fair_value")
        bull_p  = scen.get("bull", {}).get("probability", 0)
        base_p  = scen.get("base", {}).get("probability", 0)
        bear_p  = scen.get("bear", {}).get("probability", 0)
        scenario_detail = (
            f"Bull ${bull_fv:.0f} ({bull_p*100:.0f}%)  "
            f"Base ${base_fv:.0f} ({base_p*100:.0f}%)  "
            f"Bear ${bear_fv:.0f} ({bear_p*100:.0f}%)"
            if isinstance(bull_fv, (int, float)) else "—"
        )

        # Value Trap: one line per RED/AMBER check, each individually wrapped
        trap_checks = ["dividend_sustainability", "structural_decline",
                       "earnings_cashflow_mismatch", "insider_behaviour", "balance_sheet_deterioration"]
        trap_flags = [
            f"{c.replace('_', ' ').title()}: {trap[c]['status']} — {trap[c].get('evidence','')[:80]}"
            for c in trap_checks
            if isinstance(trap.get(c), dict) and trap[c].get("status") in ("RED", "AMBER")
        ]
        trap_detail = (
            "\n".join(_wrap(flag, 62) for flag in trap_flags)
            if trap_flags else "All checks GREEN"
        )

        # Power Law: wrap interpretation to fixed width; append dim scores on final line
        _DETAIL_W = 62
        pl_interp  = _wrap(pl.get("interpretation", "—"), _DETAIL_W)
        pl_dims    = (
            f"Scale:{pl.get('scale_economies','?')} "
            f"Net:{pl.get('network_effects','?')} "
            f"Win:{pl.get('winner_take_most','?')} "
            f"Sw:{pl.get('switching_costs','?')} "
            f"IP:{pl.get('data_ip_moat','?')}"
        )
        pl_detail = f"{pl_interp}\n[{pl_dims}]"

        analytics_rows = [
            [f"{W}Scenario EV Upside{R}",
             f"{Fore.CYAN}{scen.get('upside_pct', 0):.1f}%{R}" if isinstance(scen.get("upside_pct"), (int, float)) else "—",
             scenario_detail],
            [f"{W}Power Law Score{R}",
             f"{Fore.CYAN}{pl.get('total_score', '—')}/10{R}",
             pl_detail],
            [f"{W}Value Trap Risk{R}",
             f"{trap_color}{trap_verdict}{R}",
             trap_detail],
        ]
        print(f"\n{W}PHASE 7 — ANALYTICAL OVERLAYS{R}")
        print(tabulate(analytics_rows, tablefmt="grid", colalign=("left", "center", "left")))

        # ── 6. Risk Manager Flags ──────────────────────────────────────────
        risk     = analyst_signals.get("advanced_risk_manager", {}).get(ticker, {})
        approved = risk.get("approved_size_pct", 0) if risk else 0
        if risk:
            flags = risk.get("level1_flags", []) + risk.get("sector_flags", [])
            risk_rows = []
            for flag in flags:
                risk_rows.append([f"{Fore.YELLOW}  Flag{R}", f"{Fore.YELLOW}{_wrap(flag, 62)}{R}"])
            if not flags:
                risk_rows.append(["  Flags", f"{Fore.GREEN}None — all checks passed{R}"])
            print(f"\n{W}PHASE 8 — RISK MANAGER{R}")
            print(tabulate(risk_rows, tablefmt="grid", colalign=("left", "left")))

        # ── 7. Final Portfolio Decision ────────────────────────────────────
        action     = decision.get("action", "—").upper()
        size_pct   = decision.get("position_size_pct", 0)
        entry      = decision.get("entry_range", [])
        stop       = decision.get("stop_loss")
        target     = decision.get("price_target")
        horizon    = decision.get("time_horizon", "—")
        rationale  = decision.get("rationale", decision.get("reasoning", ""))

        ac = SIGNAL_COLOR.get(action, Fore.WHITE)
        entry_str = (f"${entry[0]:.2f} – ${entry[1]:.2f}"
                     if isinstance(entry, list) and len(entry) == 2
                     else "—")
        # Show allocated size alongside the risk manager ceiling for context
        size_str = f"{Fore.CYAN}{size_pct:.2%}{R} (cap {Fore.CYAN}{approved:.0%}{R})"

        decision_rows = [
            ["Action",        f"{ac}{Style.BRIGHT}{action}{R}"],
            ["Position Size", size_str],
            ["Entry Range",   f"{Fore.WHITE}{entry_str}{R}"],
            ["Stop Loss",     f"{Fore.RED}${stop:.2f}{R}" if isinstance(stop, (int, float)) else "—"],
            ["Price Target",  f"{Fore.GREEN}${target:.2f}{R}" if isinstance(target, (int, float)) else "—"],
            ["Time Horizon",  f"{Fore.WHITE}{horizon}{R}"],
            ["Rationale",     f"{Fore.WHITE}{_wrap(rationale, 60)}{R}"],
        ]
        print(f"\n{W}PHASE 9 — PORTFOLIO DECISION{R}")
        print(tabulate(decision_rows, tablefmt="grid", colalign=("left", "left")))

    print(f"\n{W}{'='*70}{R}\n")

    # ── Result Summary (one table per ticker) ──────────────────────────────
    for ticker, decision in decisions.items():
        _print_result_summary(ticker, result, decision, W, R, SIGNAL_COLOR, AGENT_DISPLAY)


def _print_result_summary(
    ticker: str,
    result: dict,
    decision: dict,
    W: str,
    R: str,
    SIGNAL_COLOR: dict,
    AGENT_DISPLAY: dict,
) -> None:
    """Print a compact 2-column Result Summary table for one ticker."""

    def _wrap_narrow(text: str, width: int = 60) -> str:
        if not text:
            return ""
        words = str(text).split()
        lines, cur = [], ""
        for word in words:
            if len(cur) + len(word) + 1 > width:
                lines.append(cur)
                cur = word
            else:
                cur = (cur + " " + word).strip()
        if cur:
            lines.append(cur)
        return "\n".join(lines)

    macro        = result.get("macro_regime") or {}
    sector       = result.get("sector", "-")
    analyst_sigs = result.get("analyst_signals", {})
    debate_res   = (result.get("debate_result") or {}).get(ticker)
    scenario     = (result.get("scenario_analysis") or {}).get(ticker, {})
    power_law    = (result.get("power_law_analysis") or {}).get(ticker, {})
    trap         = (result.get("value_trap_analysis") or {}).get(ticker, {})
    risk         = analyst_sigs.get("advanced_risk_manager", {}).get(ticker, {})

    # ── Macro row ──────────────────────────────────────────────────────────
    macro_str = (
        f"{macro.get('risk_appetite', '-')} | "
        f"{macro.get('rate_direction', '-')} rates | "
        f"{macro.get('volatility_regime', '-')} vol"
    )

    # ── Agent vote counts ──────────────────────────────────────────────────
    skip = {"risk_management_agent", "advanced_risk_manager"}
    buy_c = sell_c = hold_c = 0
    for ak, smap in analyst_sigs.items():
        if ak in skip or not isinstance(smap, dict) or ticker not in smap:
            continue
        s = smap[ticker].get("signal", "").upper()
        if s == "BUY":
            buy_c += 1
        elif s in ("SELL", "SHORT"):
            sell_c += 1
        else:
            hold_c += 1
    vote_str = (
        f"{Fore.GREEN}{buy_c} BUY{R} / "
        f"{Fore.RED}{sell_c} SELL{R} / "
        f"{Fore.YELLOW}{hold_c} HOLD{R}"
    )

    # ── Agent assessment lines (one per agent) ────────────────────────────
    agent_lines = []
    for ak, smap in analyst_sigs.items():
        if ak in skip or not isinstance(smap, dict) or ticker not in smap:
            continue
        sig = smap[ticker]
        if not isinstance(sig, dict):
            continue
        raw_signal = sig.get("signal", "-").upper()
        sc = SIGNAL_COLOR.get(raw_signal, Fore.WHITE)
        name = AGENT_DISPLAY.get(ak, ak.replace("_", " ").title())
        thesis = sig.get("thesis_summary", "") or sig.get("reasoning", "")
        thesis_words = str(thesis).split()
        thesis_short = " ".join(thesis_words[:120])
        if len(thesis_words) > 120:
            thesis_short += "..."
        # Wrap long thesis lines to fit the table column
        wrapped = _wrap_narrow(thesis_short, 56)
        agent_lines.append(
            f"  {Fore.CYAN}{name:<24}{R} {sc}{raw_signal:<5}{R}  {Fore.WHITE}{wrapped}{R}"
        )
    agent_assessment = "\n".join(agent_lines) if agent_lines else "-"

    # ── Debate row ─────────────────────────────────────────────────────────
    if debate_res:
        adj_sig  = debate_res.get("adjudicated_signal", "-").upper()
        adj_conv = debate_res.get("adjudicated_conviction", "-")
        sc = SIGNAL_COLOR.get(adj_sig, Fore.WHITE)
        debate_str = f"TRIGGERED — {sc}{adj_sig}{R} conviction {adj_conv}/10"
    else:
        debate_str = f"{Fore.YELLOW}Skipped (no strong conflict){R}"

    # ── Phase 7 rows ───────────────────────────────────────────────────────
    upside = scenario.get("upside_pct")
    ev_str = (
        f"{Fore.GREEN if upside and upside > 0 else Fore.RED}"
        f"{upside:.1f}%{R}"
        if isinstance(upside, (int, float)) else "-"
    )
    # Bull / base / bear breakdown
    bull_fv = scenario.get("bull", {}).get("fair_value")
    base_fv = scenario.get("base", {}).get("fair_value")
    bear_fv = scenario.get("bear", {}).get("fair_value")
    bull_p  = scenario.get("bull", {}).get("probability", 0)
    base_p  = scenario.get("base", {}).get("probability", 0)
    bear_p  = scenario.get("bear", {}).get("probability", 0)
    if isinstance(bull_fv, (int, float)):
        ev_str += (
            f"  (Bull ${bull_fv:.0f} {bull_p*100:.0f}% / "
            f"Base ${base_fv:.0f} {base_p*100:.0f}% / "
            f"Bear ${bear_fv:.0f} {bear_p*100:.0f}%)"
        )

    pl_score = power_law.get("total_score", "-")
    pl_interp = power_law.get("interpretation", "")
    pl_str = f"{Fore.CYAN}{pl_score}/10{R}  {_wrap_narrow(pl_interp, 72)}"

    trap_verdict = trap.get("overall_verdict", "-")
    trap_color = (Fore.RED if "HIGH" in str(trap_verdict).upper()
                  else Fore.YELLOW if "MEDIUM" in str(trap_verdict).upper()
                  else Fore.GREEN)
    trap_flags = [
        _wrap_narrow(c.replace("_", " ").title() + ": " + trap[c].get("evidence", "")[:120], 72)
        for c in ["dividend_sustainability", "structural_decline",
                  "earnings_cashflow_mismatch", "insider_behaviour", "balance_sheet_deterioration"]
        if isinstance(trap.get(c), dict) and trap[c].get("status") in ("RED", "AMBER")
    ]
    trap_str = f"{trap_color}{trap_verdict}{R}"
    if trap_flags:
        trap_str += "\n  " + "\n  ".join(trap_flags)

    # ── Decision row ───────────────────────────────────────────────────────
    cap_pct   = risk.get("approved_size_pct", 0)
    risk_flags = risk.get("level1_flags", []) + risk.get("sector_flags", [])

    # ── Liquidity row ───────────────────────────────────────────────────────
    liq_flag  = risk.get("liquidity_flag", "")
    liq_days  = risk.get("liquidity_days_to_exit")
    liq_adv   = risk.get("liquidity_adv_dollars")
    LIQ_COLOR = {"RED": Fore.RED, "AMBER": Fore.YELLOW, "GREEN": Fore.GREEN}
    lc = LIQ_COLOR.get(liq_flag, Fore.WHITE)
    if liq_days is not None and liq_adv is not None:
        liq_str = (
            f"{lc}{liq_flag}{R} — "
            f"{liq_days:.1f}d to exit at 20% ADV "
            f"(ADV ${liq_adv/1e6:.1f}M/day)"
        )
    elif liq_flag:
        liq_str = f"{lc}{liq_flag}{R}"
    else:
        liq_str = f"{Fore.WHITE}N/A{R}"

    action   = decision.get("action", "-").upper()
    size_pct = decision.get("position_size_pct", 0)
    stop     = decision.get("stop_loss")
    target   = decision.get("price_target")
    horizon  = decision.get("time_horizon", "-")
    rationale = decision.get("rationale", decision.get("reasoning", ""))

    ac = SIGNAL_COLOR.get(action, Fore.WHITE)
    # Allocated size shown alongside the risk-manager ceiling for context
    size_with_cap = (
        f"{Fore.CYAN}{size_pct:.2%}{R} "
        f"(cap {Fore.CYAN}{cap_pct:.0%}{R})"
    )
    if isinstance(target, (int, float)):
        decision_str = (
            f"{ac}{Style.BRIGHT}{action}{R} | "
            f"{size_with_cap} | "
            f"Target {Fore.GREEN}${target:.2f}{R}"
        )
    else:
        decision_str = f"{ac}{Style.BRIGHT}{action}{R} | {size_with_cap}"
    if isinstance(stop, (int, float)):
        decision_str += f" | Stop {Fore.RED}${stop:.2f}{R}"
    decision_str += f" | Horizon {Fore.WHITE}{horizon}{R}"
    if risk_flags:
        decision_str += "\n  " + "\n  ".join(f"{Fore.YELLOW}{f}{R}" for f in risk_flags)

    rationale_str = _wrap_narrow(rationale, 60)

    # ── Build table ────────────────────────────────────────────────────────
    rows = [
        [f"{W}Macro Regime{R}",       macro_str],
        [f"{W}Sector{R}",             f"{Fore.CYAN}{sector}{R}"],
        [f"{W}Agent Vote{R}",         vote_str],
        [f"{W}Agent Assessment{R}",   agent_assessment],
        [f"{W}Debate{R}",             debate_str],
        [f"{W}Scenario EV Upside{R}", ev_str],
        [f"{W}Power Law{R}",          pl_str],
        [f"{W}Value Trap{R}",         trap_str],
        [f"{W}Liquidity{R}",          liq_str],
        [f"{W}Decision{R}",           decision_str],
        [f"{W}Rationale{R}",          f"{Fore.WHITE}{rationale_str}{R}"],
    ]

    print(f"\n{W}{'='*70}{R}")
    print(f"{W}  RESULT SUMMARY: {Fore.CYAN}{ticker}{R}")
    print(f"{W}{'='*70}{R}")
    print(tabulate(rows, tablefmt="grid", colalign=("left", "left")))
    print()


def print_backtest_results(table_rows: list) -> None:
    """Print the backtest results in a nicely formatted table"""
    # Clear the screen
    os.system("cls" if os.name == "nt" else "clear")

    # Split rows into ticker rows and summary rows
    ticker_rows = []
    summary_rows = []

    for row in table_rows:
        if isinstance(row[1], str) and "PORTFOLIO SUMMARY" in row[1]:
            summary_rows.append(row)
        else:
            ticker_rows.append(row)

    # Display latest portfolio summary
    if summary_rows:
        # Pick the most recent summary by date (YYYY-MM-DD)
        latest_summary = max(summary_rows, key=lambda r: r[0])
        print(f"\n{Fore.WHITE}{Style.BRIGHT}PORTFOLIO SUMMARY:{Style.RESET_ALL}")

        # Adjusted indexes after adding Long/Short Shares
        position_str = latest_summary[7].split("$")[1].split(Style.RESET_ALL)[0].replace(",", "")
        cash_str     = latest_summary[8].split("$")[1].split(Style.RESET_ALL)[0].replace(",", "")
        total_str    = latest_summary[9].split("$")[1].split(Style.RESET_ALL)[0].replace(",", "")

        print(f"Cash Balance: {Fore.CYAN}${float(cash_str):,.2f}{Style.RESET_ALL}")
        print(f"Total Position Value: {Fore.YELLOW}${float(position_str):,.2f}{Style.RESET_ALL}")
        print(f"Total Value: {Fore.WHITE}${float(total_str):,.2f}{Style.RESET_ALL}")
        print(f"Portfolio Return: {latest_summary[10]}")
        if len(latest_summary) > 14 and latest_summary[14]:
            print(f"Benchmark Return: {latest_summary[14]}")

        # Display performance metrics if available
        if latest_summary[11]:  # Sharpe ratio
            print(f"Sharpe Ratio: {latest_summary[11]}")
        if latest_summary[12]:  # Sortino ratio
            print(f"Sortino Ratio: {latest_summary[12]}")
        if latest_summary[13]:  # Max drawdown
            print(f"Max Drawdown: {latest_summary[13]}")

    # Add vertical spacing
    print("\n" * 2)

    # Print the table with just ticker rows
    print(
        tabulate(
            ticker_rows,
            headers=[
                "Date",
                "Ticker",
                "Action",
                "Quantity",
                "Price",
                "Long Shares",
                "Short Shares",
                "Position Value",
            ],
            tablefmt="grid",
            colalign=(
                "left",    # Date
                "left",    # Ticker
                "center",  # Action
                "right",   # Quantity
                "right",   # Price
                "right",   # Long Shares
                "right",   # Short Shares
                "right",   # Position Value
            ),
        )
    )

    # Add vertical spacing
    print("\n" * 4)


def format_backtest_row(
    date: str,
    ticker: str,
    action: str,
    quantity: float,
    price: float,
    long_shares: float = 0,
    short_shares: float = 0,
    position_value: float = 0,
    is_summary: bool = False,
    total_value: float = None,
    return_pct: float = None,
    cash_balance: float = None,
    total_position_value: float = None,
    sharpe_ratio: float = None,
    sortino_ratio: float = None,
    max_drawdown: float = None,
    benchmark_return_pct: float | None = None,
) -> list[any]:
    """Format a row for the backtest results table"""
    # Color the action
    action_color = {
        "BUY": Fore.GREEN,
        "COVER": Fore.GREEN,
        "SELL": Fore.RED,
        "SHORT": Fore.RED,
        "HOLD": Fore.WHITE,
    }.get(action.upper(), Fore.WHITE)

    if is_summary:
        return_color = Fore.GREEN if return_pct >= 0 else Fore.RED
        benchmark_str = ""
        if benchmark_return_pct is not None:
            bench_color = Fore.GREEN if benchmark_return_pct >= 0 else Fore.RED
            benchmark_str = f"{bench_color}{benchmark_return_pct:+.2f}%{Style.RESET_ALL}"
        return [
            date,
            f"{Fore.WHITE}{Style.BRIGHT}PORTFOLIO SUMMARY{Style.RESET_ALL}",
            "",  # Action
            "",  # Quantity
            "",  # Price
            "",  # Long Shares
            "",  # Short Shares
            f"{Fore.YELLOW}${total_position_value:,.2f}{Style.RESET_ALL}",  # Total Position Value
            f"{Fore.CYAN}${cash_balance:,.2f}{Style.RESET_ALL}",  # Cash Balance
            f"{Fore.WHITE}${total_value:,.2f}{Style.RESET_ALL}",  # Total Value
            f"{return_color}{return_pct:+.2f}%{Style.RESET_ALL}",  # Return
            f"{Fore.YELLOW}{sharpe_ratio:.2f}{Style.RESET_ALL}" if sharpe_ratio is not None else "",  # Sharpe Ratio
            f"{Fore.YELLOW}{sortino_ratio:.2f}{Style.RESET_ALL}" if sortino_ratio is not None else "",  # Sortino Ratio
            f"{Fore.RED}{max_drawdown:.2f}%{Style.RESET_ALL}" if max_drawdown is not None else "",  # Max Drawdown (signed)
            benchmark_str,  # Benchmark (S&P 500)
        ]
    else:
        return [
            date,
            f"{Fore.CYAN}{ticker}{Style.RESET_ALL}",
            f"{action_color}{action.upper()}{Style.RESET_ALL}",
            f"{action_color}{quantity:,.0f}{Style.RESET_ALL}",
            f"{Fore.WHITE}{price:,.2f}{Style.RESET_ALL}",
            f"{Fore.GREEN}{long_shares:,.0f}{Style.RESET_ALL}",   # Long Shares
            f"{Fore.RED}{short_shares:,.0f}{Style.RESET_ALL}",    # Short Shares
            f"{Fore.YELLOW}{position_value:,.2f}{Style.RESET_ALL}",
        ]
