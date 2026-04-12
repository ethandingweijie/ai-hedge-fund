import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta
import argparse
import questionary
from colorama import Fore, Style

from src.utils.analysts import ANALYST_ORDER
from src.llm.models import LLM_ORDER, OLLAMA_LLM_ORDER, get_model_info, ModelProvider, find_model_by_name
from src.utils.ollama import ensure_ollama_and_model

from dataclasses import dataclass, field
from typing import Optional


def add_common_args(
    parser: argparse.ArgumentParser,
    *,
    require_tickers: bool = False,
    include_analyst_flags: bool = True,
    include_ollama: bool = True,
) -> argparse.ArgumentParser:
    parser.add_argument(
        "--tickers",
        type=str,
        required=require_tickers,
        help="Comma-separated list of stock ticker symbols (e.g., AAPL,MSFT,GOOGL)",
    )
    if include_analyst_flags:
        parser.add_argument(
            "--analysts",
            type=str,
            required=False,
            help="Comma-separated list of analysts to use (e.g., michael_burry,other_analyst)",
        )
        parser.add_argument(
            "--analysts-all",
            action="store_true",
            help="Use all available analysts (overrides --analysts)",
        )
    if include_ollama:
        parser.add_argument("--ollama", action="store_true", help="Use Ollama for local LLM inference")
    parser.add_argument("--model", type=str, required=False, help="Model name to use (e.g., gpt-4o)")
    return parser


def add_date_args(parser: argparse.ArgumentParser, *, default_months_back: int | None = None) -> argparse.ArgumentParser:
    if default_months_back is None:
        parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
        parser.add_argument("--end-date", type=str, help="End date (YYYY-MM-DD)")
    else:
        parser.add_argument(
            "--end-date",
            type=str,
            default=datetime.now().strftime("%Y-%m-%d"),
            help="End date in YYYY-MM-DD format",
        )
        parser.add_argument(
            "--start-date",
            type=str,
            default=(datetime.now() - relativedelta(months=default_months_back)).strftime("%Y-%m-%d"),
            help="Start date in YYYY-MM-DD format",
        )
    return parser


def parse_tickers(tickers_arg: str | None) -> list[str]:
    if not tickers_arg:
        return []
    return [ticker.strip() for ticker in tickers_arg.split(",") if ticker.strip()]


def select_investor_agents(analysts_all: bool = False) -> list[str]:
    """
    Interactive numbered-list agent selector for the advanced 10-phase pipeline.
    Shows the 12 INVESTOR_PERSONAS.  Always prompts unless analysts_all=True.
    """
    from src.pipeline_investors import INVESTOR_PERSONAS  # local import to avoid circular

    INVESTOR_DISPLAY: dict[str, str] = {
        "damodaran":      "Aswath Damodaran  — Dean of Valuation",
        "graham":         "Ben Graham        — Father of Value Investing",
        "ackman":         "Bill Ackman       — Activist Investor (Pershing Square)",
        "cathie_wood":    "Cathie Wood       — Queen of Disruptive Growth (ARK)",
        "munger":         "Charlie Munger    — Rational Thinker (Berkshire)",
        "burry":          "Michael Burry     — Forensic Contrarian (Scion)",
        "pabrai":         "Mohnish Pabrai    — Dhandho Investor",
        "lynch":          "Peter Lynch       — Tenbagger Hunter (Fidelity)",
        "fisher":         "Phil Fisher       — Scuttlebutt Investigator",
        "jhunjhunwala":   "Rakesh Jhunjhunwala — Big Bull of India",
        "druckenmiller":  "Stanley Druckenmiller — Macro Legend (Duquesne)",
        "buffett":        "Warren Buffett    — Oracle of Omaha (Berkshire)",
    }

    all_keys = list(INVESTOR_PERSONAS.keys())

    if analysts_all:
        print(f"\n{Fore.WHITE}{Style.BRIGHT}Investor Agents (--analysts-all: running all {len(all_keys)}){Style.RESET_ALL}")
        for i, key in enumerate(all_keys, 1):
            print(f"  {Fore.GREEN}{i:2}. {INVESTOR_DISPLAY.get(key, key)}{Style.RESET_ALL}")
        print()
        return all_keys

    # ── Interactive numbered-list prompt ─────────────────────────────────────
    print(f"\n{Fore.WHITE}{Style.BRIGHT}Select Investor Agents to run in the Advanced Pipeline{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{'─'*60}{Style.RESET_ALL}")
    for i, key in enumerate(all_keys, 1):
        print(f"  {Fore.CYAN}{i:2}.{Style.RESET_ALL} {INVESTOR_DISPLAY.get(key, key)}")
    print(f"{Fore.WHITE}{'─'*60}{Style.RESET_ALL}")

    choices = questionary.checkbox(
        "Choose agents  [Space = toggle | 'a' = all | Enter = confirm]",
        choices=[
            questionary.Choice(
                f"{i+1:2}. {INVESTOR_DISPLAY.get(key, key)}",
                value=key,
            )
            for i, key in enumerate(all_keys)
        ],
        validate=lambda x: len(x) > 0 or "Select at least one agent.",
        style=questionary.Style([
            ("checkbox-selected", "fg:green"),
            ("selected",          "fg:green noinherit"),
            ("highlighted",       "noinherit"),
            ("pointer",           "noinherit"),
        ]),
    ).ask()

    if not choices:
        print("\n\nInterrupt received. Exiting...")
        sys.exit(0)

    print(
        f"\nSelected agents: "
        + ", ".join(
            Fore.GREEN + INVESTOR_DISPLAY.get(c, c).split("—")[0].strip() + Style.RESET_ALL
            for c in choices
        )
        + "\n"
    )
    return choices


def select_analysts(flags: dict | None = None) -> list[str]:
    if flags and flags.get("analysts_all"):
        return [a[1] for a in ANALYST_ORDER]

    if flags and flags.get("analysts"):
        return [a.strip() for a in flags["analysts"].split(",") if a.strip()]

    choices = questionary.checkbox(
        "Select your AI analysts.",
        choices=[questionary.Choice(display, value=value) for display, value in ANALYST_ORDER],
        instruction="\n\nInstructions: \n1. Press Space to select/unselect analysts.\n2. Press 'a' to select/unselect all.\n3. Press Enter when done.",
        validate=lambda x: len(x) > 0 or "You must select at least one analyst.",
        style=questionary.Style(
            [
                ("checkbox-selected", "fg:green"),
                ("selected", "fg:green noinherit"),
                ("highlighted", "noinherit"),
                ("pointer", "noinherit"),
            ]
        ),
    ).ask()

    if not choices:
        print("\n\nInterrupt received. Exiting...")
        sys.exit(0)

    print(
        f"\nSelected analysts: {', '.join(Fore.GREEN + c.title().replace('_', ' ') + Style.RESET_ALL for c in choices)}\n"
    )
    return choices


def select_model(use_ollama: bool, model_flag: str | None = None) -> tuple[str, str]:
    model_name: str = ""
    model_provider: str | None = None

    if model_flag:
        model = find_model_by_name(model_flag)
        if model:
            print(
                f"\nUsing specified model: {Fore.CYAN}{model.provider.value}{Style.RESET_ALL} - {Fore.GREEN + Style.BRIGHT}{model.model_name}{Style.RESET_ALL}\n"
            )
            return model.model_name, model.provider.value
        else:
            # Non-interactive fallback: treat the flag value as a literal model name
            # and try to infer the provider from the name prefix
            import sys
            if not sys.stdin.isatty():
                provider = "Anthropic" if "claude" in model_flag.lower() else \
                           "OpenAI" if any(x in model_flag.lower() for x in ("gpt", "o1", "o3")) else \
                           "Unknown"
                print(f"\nUsing model (not in registry): {Fore.GREEN}{model_flag}{Style.RESET_ALL} [{provider}]\n")
                return model_flag, provider
            print(f"{Fore.RED}Model '{model_flag}' not found. Please select a model.{Style.RESET_ALL}")

    if use_ollama:
        print(f"{Fore.CYAN}Using Ollama for local LLM inference.{Style.RESET_ALL}")
        model_name = questionary.select(
            "Select your Ollama model:",
            choices=[questionary.Choice(display, value=value) for display, value, _ in OLLAMA_LLM_ORDER],
            style=questionary.Style(
                [
                    ("selected", "fg:green bold"),
                    ("pointer", "fg:green bold"),
                    ("highlighted", "fg:green"),
                    ("answer", "fg:green bold"),
                ]
            ),
        ).ask()

        if not model_name:
            print("\n\nInterrupt received. Exiting...")
            sys.exit(0)

        if model_name == "-":
            model_name = questionary.text("Enter the custom model name:").ask()
            if not model_name:
                print("\n\nInterrupt received. Exiting...")
                sys.exit(0)

        if not ensure_ollama_and_model(model_name):
            print(f"{Fore.RED}Cannot proceed without Ollama and the selected model.{Style.RESET_ALL}")
            sys.exit(1)

        model_provider = ModelProvider.OLLAMA.value
        print(
            f"\nSelected {Fore.CYAN}Ollama{Style.RESET_ALL} model: {Fore.GREEN + Style.BRIGHT}{model_name}{Style.RESET_ALL}\n"
        )
    else:
        model_choice = questionary.select(
            "Select your LLM model:",
            choices=[questionary.Choice(display, value=(name, provider)) for display, name, provider in LLM_ORDER],
            style=questionary.Style(
                [
                    ("selected", "fg:green bold"),
                    ("pointer", "fg:green bold"),
                    ("highlighted", "fg:green"),
                    ("answer", "fg:green bold"),
                ]
            ),
        ).ask()

        if not model_choice:
            print("\n\nInterrupt received. Exiting...")
            sys.exit(0)

        model_name, model_provider = model_choice

        model_info = get_model_info(model_name, model_provider)
        if model_info and model_info.is_custom():
            model_name = questionary.text("Enter the custom model name:").ask()
            if not model_name:
                print("\n\nInterrupt received. Exiting...")
                sys.exit(0)

        if model_info:
            print(
                f"\nSelected {Fore.CYAN}{model_provider}{Style.RESET_ALL} model: {Fore.GREEN + Style.BRIGHT}{model_name}{Style.RESET_ALL}\n"
            )
        else:
            model_provider = "Unknown"
            print(f"\nSelected model: {Fore.GREEN + Style.BRIGHT}{model_name}{Style.RESET_ALL}\n")

    return model_name, model_provider or ""


def resolve_dates(start_date: str | None, end_date: str | None, *, default_months_back: int | None = None) -> tuple[str, str]:
    if start_date:
        try:
            datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Start date must be in YYYY-MM-DD format")
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("End date must be in YYYY-MM-DD format")

    final_end = end_date or datetime.now().strftime("%Y-%m-%d")
    if start_date:
        final_start = start_date
    else:
        months = default_months_back if default_months_back is not None else 3
        end_date_obj = datetime.strptime(final_end, "%Y-%m-%d")
        final_start = (end_date_obj - relativedelta(months=months)).strftime("%Y-%m-%d")
    return final_start, final_end


def _try_float(val: str) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


_GUIDANCE_SKIP = "__skip__"   # sentinel for "skip" choice in questionary.select


def collect_management_guidance(tickers: list[str]) -> dict[str, dict]:
    """
    Interactively collect per-ticker management guidance before the advanced pipeline runs.
    All inputs are optional — pressing Enter on any numeric field or choosing "skip"
    leaves that field as None, and the DCF agent falls back to analyst consensus or
    historical averages.

    Returns: {ticker: {revenue_growth_guide, capex_guide_bn, margin_direction, source}}
    Called only when pipeline_mode == "advanced".
    """
    _qs = questionary.Style([
        ("selected",    "fg:green bold"),
        ("pointer",     "fg:green bold"),
        ("highlighted", "fg:green"),
        ("answer",      "fg:green bold"),
        ("question",    "noinherit"),
    ])

    print(f"\n{Fore.WHITE}{Style.BRIGHT}Management Guidance Input{Style.RESET_ALL}")
    print(f"{Fore.WHITE}{'─'*60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Enter figures from the most recent earnings call or analyst day.")
    print(f"  Press Enter on numeric fields to skip — DCF falls back to consensus.{Style.RESET_ALL}\n")

    guidance_map: dict[str, dict] = {}

    for ticker in tickers:
        print(f"  {Fore.YELLOW}{Style.BRIGHT}── {ticker} ──{Style.RESET_ALL}")

        # ── Revenue growth ────────────────────────────────────────────────
        rev_raw = questionary.text(
            f"  Revenue growth guidance % (e.g. 15 for 15%, Enter to skip):",
            validate=lambda v: (
                True if v.strip() == ""
                else True if _try_float(v.strip()) is not None
                else "Enter a number (e.g. 15) or leave blank to skip"
            ),
            style=_qs,
        ).ask()
        if rev_raw is None:
            print("\n\nInterrupt received. Exiting...")
            sys.exit(0)
        rev_growth = (_try_float(rev_raw.strip()) / 100.0) if rev_raw.strip() else None

        # ── CapEx ─────────────────────────────────────────────────────────
        capex_raw = questionary.text(
            f"  CapEx guidance USD billions (e.g. 65, Enter to skip):",
            validate=lambda v: (
                True if v.strip() == ""
                else True if _try_float(v.strip()) is not None
                else "Enter a number (e.g. 65) or leave blank to skip"
            ),
            style=_qs,
        ).ask()
        if capex_raw is None:
            print("\n\nInterrupt received. Exiting...")
            sys.exit(0)
        capex = _try_float(capex_raw.strip()) if capex_raw.strip() else None

        # ── Margin direction ──────────────────────────────────────────────
        margin_raw = questionary.select(
            f"  Margin direction:",
            choices=[
                questionary.Choice("stable     — margins hold",           value="stable"),
                questionary.Choice("expanding  — margins improving",      value="expanding"),
                questionary.Choice("compressing — margins under pressure", value="compressing"),
                questionary.Choice("skip — use historical trend",          value=_GUIDANCE_SKIP),
            ],
            style=_qs,
        ).ask()
        if margin_raw is None:
            print("\n\nInterrupt received. Exiting...")
            sys.exit(0)
        margin = None if margin_raw == _GUIDANCE_SKIP else margin_raw

        # ── Source ────────────────────────────────────────────────────────
        source_raw = questionary.text(
            f"  Source (e.g. Q4 2024 earnings call, Enter to skip):",
            style=_qs,
        ).ask()
        if source_raw is None:
            print("\n\nInterrupt received. Exiting...")
            sys.exit(0)
        source = source_raw.strip() if source_raw.strip() else "manual input"

        guidance_map[ticker] = {
            "revenue_growth_guide": rev_growth,
            "capex_guide_bn":       capex,
            "margin_direction":     margin,
            "source":               source,
        }
        print()

    # ── Confirmation summary ──────────────────────────────────────────────────
    print(f"  {Fore.WHITE}{Style.BRIGHT}Guidance Summary{Style.RESET_ALL}")
    print(f"  {'─'*70}")
    print(f"  {'Ticker':<8} {'Rev Growth':>12} {'CapEx $bn':>10} {'Margin':>13}  Source")
    print(f"  {'─'*70}")
    for t, g in guidance_map.items():
        rg = (f"{g['revenue_growth_guide']*100:.1f}%"
              if g["revenue_growth_guide"] is not None else "consensus")
        cx = f"{g['capex_guide_bn']:.1f}" if g["capex_guide_bn"] is not None else "n/a"
        mg = g["margin_direction"] or "historical"
        print(f"  {Fore.CYAN}{t:<8}{Style.RESET_ALL} {rg:>12} {cx:>10} {mg:>13}  {g['source']}")
    print(f"  {'─'*70}\n")

    confirmed = questionary.confirm(
        "Proceed with this guidance?",
        default=True,
        style=_qs,
    ).ask()
    if confirmed is None:
        print("\n\nInterrupt received. Exiting...")
        sys.exit(0)
    if not confirmed:
        print(f"\n  {Fore.YELLOW}Re-entering guidance...{Style.RESET_ALL}\n")
        return collect_management_guidance(tickers)   # recurse once to re-collect

    print()
    return guidance_map


@dataclass
class CLIInputs:
    tickers: list[str]
    selected_analysts: list[str]
    model_name: str
    model_provider: str
    start_date: str
    end_date: str
    initial_cash: float
    margin_requirement: float
    show_reasoning: bool = False
    show_agent_graph: bool = False
    pipeline_mode: str = "simple"          # "simple" | "advanced"
    enable_post_trade_review: bool = False
    management_guidance: dict[str, dict] = field(default_factory=dict)
    raw_args: Optional[argparse.Namespace] = None


def parse_cli_inputs(
    *,
    description: str,
    require_tickers: bool,
    default_months_back: int | None,
    include_graph_flag: bool = False,
    include_reasoning_flag: bool = False,
) -> CLIInputs:
    parser = argparse.ArgumentParser(description=description)

    # Common/interactive flags
    add_common_args(parser, require_tickers=require_tickers, include_analyst_flags=True, include_ollama=True)
    add_date_args(parser, default_months_back=default_months_back)

    # Funding flags (standardized, with alias)
    parser.add_argument(
        "--initial-cash",
        "--initial-capital",
        dest="initial_cash",
        type=float,
        default=100000.0,
        help="Initial cash position (alias: --initial-capital). Defaults to 100000.0",
    )
    parser.add_argument(
        "--margin-requirement",
        dest="margin_requirement",
        type=float,
        default=0.0,
        help="Initial margin requirement ratio for shorts (e.g., 0.5 for 50%%). Defaults to 0.0",
    )

    if include_reasoning_flag:
        parser.add_argument("--show-reasoning", action="store_true", help="Show reasoning from each agent")
    if include_graph_flag:
        parser.add_argument("--show-agent-graph", action="store_true", help="Show the agent graph")

    parser.add_argument(
        "--pipeline",
        type=str,
        choices=["simple", "advanced"],
        default="simple",
        dest="pipeline_mode",
        help="Pipeline mode: 'simple' (default LangGraph) or 'advanced' (10-phase orchestrator)",
    )
    parser.add_argument(
        "--post-trade-review",
        action="store_true",
        dest="enable_post_trade_review",
        help="Run post-trade review phase (Phase 10) — scores prior calls and updates conviction weights",
    )

    args = parser.parse_args()

    # Normalize parsed values
    tickers = parse_tickers(getattr(args, "tickers", None))
    pipeline_mode = getattr(args, "pipeline_mode", "simple")
    analysts_all_flag = getattr(args, "analysts_all", False)

    if pipeline_mode == "advanced":
        # Advanced pipeline uses INVESTOR_PERSONAS (12 agents), always shows interactive prompt
        selected_analysts = select_investor_agents(analysts_all=analysts_all_flag)
    else:
        selected_analysts = select_analysts({
            "analysts_all": analysts_all_flag,
            "analysts": getattr(args, "analysts", None),
        })
    model_name, model_provider = select_model(getattr(args, "ollama", False), getattr(args, "model", None))
    start_date, end_date = resolve_dates(getattr(args, "start_date", None), getattr(args, "end_date", None), default_months_back=default_months_back)

    # Collect management guidance upfront for the advanced pipeline only.
    # Runs after agent/model selection so the user can set up guidance in one
    # focused block before the pipeline starts crunching data.
    guidance: dict[str, dict] = {}
    if pipeline_mode == "advanced" and tickers:
        guidance = collect_management_guidance(tickers)

    return CLIInputs(
        tickers=tickers,
        selected_analysts=selected_analysts,
        model_name=model_name,
        model_provider=model_provider,
        start_date=start_date,
        end_date=end_date,
        initial_cash=getattr(args, "initial_cash", 100000.0),
        margin_requirement=getattr(args, "margin_requirement", 0.0),
        show_reasoning=getattr(args, "show_reasoning", False),
        show_agent_graph=getattr(args, "show_agent_graph", False),
        pipeline_mode=getattr(args, "pipeline_mode", "simple"),
        enable_post_trade_review=getattr(args, "enable_post_trade_review", False),
        management_guidance=guidance,
        raw_args=args,
    )


