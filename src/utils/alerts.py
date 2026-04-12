"""
Push Alert System — Sprint A, task #6.

Evaluates five trigger conditions against the Phase 10 result dict and
dispatches formatted alerts to any combination of:
  - Slack  (SLACK_WEBHOOK_URL env var)
  - Email  (ALERT_EMAIL + SMTP_* env vars)

Silent no-op when no channels are configured.

Trigger conditions (checked per ticker):
  1. Strong signal    — BUY/SHORT with position_size_pct >= ALERT_MIN_POSITION
  2. Value Trap RED   — overall_verdict == "TRAP RISK HIGH" (fires on any action)
  3. Insider cluster  — cluster_buy flag from InsiderActivityAgent
  4. Squeeze fuel     — squeeze_risk=True from ShortInterestAgent on a BUY
  5. Liquidity RED    — position halved by liquidity check (Level 3 risk)
  6. High EV upside   — scenario EV upside >= ALERT_EV_THRESHOLD

Environment variables
─────────────────────
SLACK_WEBHOOK_URL    Slack Incoming Webhook URL (activates Slack channel)
ALERT_EMAIL          Recipient email address     (activates email channel)
SMTP_HOST            SMTP server host  (default: smtp.gmail.com)
SMTP_PORT            SMTP server port  (default: 587)
SMTP_USER            SMTP login / From address
SMTP_PASS            SMTP password or app-password

ALERT_MIN_POSITION   Minimum position size to trigger alert (default: 0.03 = 3%)
ALERT_EV_THRESHOLD   EV upside % to trigger alert          (default: 25.0)
"""

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

try:
    import requests as _requests  # already in requirements via FMP calls
except ImportError:
    _requests = None  # type: ignore[assignment]

# ── Channel configuration ──────────────────────────────────────────────────────
_SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL", "").strip()
_ALERT_EMAIL   = os.getenv("ALERT_EMAIL", "").strip()
_SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
_SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER     = os.getenv("SMTP_USER", "").strip()
_SMTP_PASS     = os.getenv("SMTP_PASS", "").strip()

# ── Trigger thresholds ─────────────────────────────────────────────────────────
_MIN_POSITION  = float(os.getenv("ALERT_MIN_POSITION", "0.03"))   # 3 %
_EV_THRESHOLD  = float(os.getenv("ALERT_EV_THRESHOLD", "25.0"))   # 25 % EV upside


# ── Public entry point ─────────────────────────────────────────────────────────

def check_and_send_alerts(result: dict) -> None:
    """
    Evaluate alert conditions against the pipeline result dict and
    dispatch to all configured channels.  Called from main.py after
    generate_pdf_report().

    Silent no-op when neither SLACK_WEBHOOK_URL nor ALERT_EMAIL is set.
    Never raises — all errors are printed as warnings.
    """
    if not _SLACK_WEBHOOK and not _ALERT_EMAIL:
        return  # no channels configured

    decisions         = result.get("decisions", {})
    analyst_signals   = result.get("analyst_signals", {})
    scenario_analysis = result.get("scenario_analysis", {})
    power_law         = result.get("power_law_analysis", {})
    value_trap        = result.get("value_trap_analysis", {})
    macro_regime      = result.get("macro_regime", {})
    insider_activity  = result.get("insider_activity", {})
    short_interest    = result.get("short_interest", {})
    sector            = result.get("sector", "")

    alerted = 0
    for ticker, decision in decisions.items():
        triggers = _evaluate_triggers(
            ticker, decision, analyst_signals,
            scenario_analysis, value_trap,
            insider_activity, short_interest,
        )
        if not triggers:
            continue

        message = _format_message(
            ticker, decision, triggers,
            analyst_signals, scenario_analysis,
            power_law, value_trap, macro_regime, sector,
        )
        _dispatch(ticker, decision.get("action", ""), message)
        alerted += 1

    if alerted == 0:
        print("  [alerts] No alert thresholds crossed — no messages sent")


# ── Trigger evaluation ─────────────────────────────────────────────────────────

def _evaluate_triggers(
    ticker: str,
    decision: dict,
    analyst_signals: dict,
    scenario_analysis: dict,
    value_trap: dict,
    insider_activity: dict,
    short_interest: dict,
) -> list[str]:
    triggers: list[str] = []
    action   = decision.get("action", "HOLD")
    size_pct = decision.get("position_size_pct", 0.0) or 0.0

    # 1. Meaningful directional call
    if action in ("BUY", "SHORT") and size_pct >= _MIN_POSITION:
        triggers.append(f"Strong {action} ({size_pct:.1%} position)")

    # 2. Value Trap RED — always alert regardless of action
    trap = value_trap.get(ticker, {})
    if isinstance(trap, dict) and trap.get("overall_verdict", "") == "TRAP RISK HIGH":
        triggers.append("Value Trap RED")

    # 3. Insider cluster buy
    ia = insider_activity.get(ticker, {})
    if isinstance(ia, dict) and ia.get("cluster_buy"):
        triggers.append("Insider Cluster Buy (>=2 insiders / 30d)")

    # 4. Short squeeze fuel on a long
    si = short_interest.get(ticker, {})
    if isinstance(si, dict) and si.get("squeeze_risk") and action == "BUY":
        sf = si.get("short_float_pct")
        sf_str = f" ({sf:.1f}% float)" if isinstance(sf, (int, float)) else ""
        triggers.append(f"Short Squeeze Fuel{sf_str} on BUY")

    # 5. Liquidity RED — position was halved by Level 3 risk check
    risk = analyst_signals.get("advanced_risk_manager", {}).get(ticker, {})
    if isinstance(risk, dict) and risk.get("liquidity_flag") == "RED":
        days = risk.get("liquidity_days_to_exit")
        days_str = f" ({days:.0f}d to exit)" if isinstance(days, (int, float)) else ""
        triggers.append(f"Liquidity RED{days_str} — position size halved")

    # 6. High expected value upside (or downside for shorts)
    scenario = scenario_analysis.get(ticker, {})
    ev_upside = scenario.get("upside_pct", 0.0) or 0.0
    if abs(ev_upside) >= _EV_THRESHOLD and action in ("BUY", "SHORT"):
        triggers.append(f"High EV {ev_upside:+.1f}%")

    return triggers


# ── Message formatting ─────────────────────────────────────────────────────────

def _format_message(
    ticker: str,
    decision: dict,
    triggers: list[str],
    analyst_signals: dict,
    scenario_analysis: dict,
    power_law: dict,
    value_trap: dict,
    macro_regime: dict,
    sector: str,
) -> str:
    action   = decision.get("action", "-")
    size_pct = decision.get("position_size_pct", 0.0) or 0.0
    target   = decision.get("price_target")
    stop     = decision.get("stop_loss")
    horizon  = decision.get("time_horizon", "-")

    risk     = analyst_signals.get("advanced_risk_manager", {}).get(ticker, {}) or {}
    cap_pct  = risk.get("approved_size_pct", 0.0) or 0.0
    liq_flag = risk.get("liquidity_flag", "N/A")
    liq_days = risk.get("liquidity_days_to_exit")

    scenario  = scenario_analysis.get(ticker, {}) or {}
    ev_upside = scenario.get("upside_pct", 0.0) or 0.0
    cur_price = scenario.get("current_price", 0.0) or 0.0

    pl_score = (power_law.get(ticker, {}) or {}).get("total_score", "-")
    trap_v   = (value_trap.get(ticker, {}) or {}).get("overall_verdict", "-")

    ra  = macro_regime.get("risk_appetite", "-")
    rd  = macro_regime.get("rate_direction", "-")
    vol = macro_regime.get("volatility_regime", "-")

    # Agent vote tally
    _skip = {"risk_management_agent", "advanced_risk_manager"}
    buy_c = sell_c = hold_c = 0
    for ak, sigs in analyst_signals.items():
        if ak in _skip or not isinstance(sigs, dict):
            continue
        s = (sigs.get(ticker) or {}).get("signal", "")
        if s == "BUY":
            buy_c += 1
        elif s in ("SELL", "SHORT"):
            sell_c += 1
        elif s == "HOLD":
            hold_c += 1

    # Decision line
    decision_parts = [f"{action} | {size_pct:.1%} (cap {cap_pct:.0%})"]
    if isinstance(target, (int, float)):
        decision_parts.append(f"Target ${target:.2f}")
    if isinstance(stop, (int, float)):
        decision_parts.append(f"Stop ${stop:.2f}")
    decision_line = " | ".join(decision_parts)

    liq_str = liq_flag
    if isinstance(liq_days, (int, float)):
        liq_str += f" ({liq_days:.1f}d)"

    sep = "=" * 52
    lines = [
        f"AI Hedge Fund Alert  |  {ticker}  |  {action}",
        sep,
    ]
    if cur_price:
        lines.append(f"Sector: {sector}   Price: ${cur_price:.2f}")
    else:
        lines.append(f"Sector: {sector}")
    lines += [
        "",
        f"Decision:  {decision_line}",
        f"Horizon:   {horizon}",
        "",
        "Triggers:",
    ]
    for t in triggers:
        lines.append(f"  * {t}")
    lines += [
        "",
        f"Agents:    {buy_c} BUY  {hold_c} HOLD  {sell_c} SELL/SHORT",
        f"EV Upside: {ev_upside:+.1f}%",
        f"Power Law: {pl_score}/10",
        f"Trap Risk: {trap_v}",
        f"Liquidity: {liq_str}",
        "",
        f"Regime:    {ra} | {rd} rates | {vol} vol",
        f"Time:      {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    return "\n".join(lines)


# ── Dispatch ───────────────────────────────────────────────────────────────────

def _dispatch(ticker: str, action: str, message: str) -> None:
    subject = f"[AI Hedge Fund] {ticker} — {action}"
    _send_slack(message)
    _send_email(subject, message)


def _send_slack(message: str) -> None:
    if not _SLACK_WEBHOOK:
        return
    if _requests is None:
        print("  [alerts] Slack: 'requests' not installed — skipping")
        return
    try:
        resp = _requests.post(
            _SLACK_WEBHOOK,
            json={"text": f"```\n{message}\n```"},
            timeout=10,
        )
        if resp.status_code == 200:
            print("  [alerts] Slack alert sent")
        else:
            print(f"  [alerts] Slack HTTP {resp.status_code}: {resp.text[:120]}")
    except Exception as exc:
        print(f"  [alerts] Slack error: {exc}")


def send_rotation_alert(rotation_result: dict) -> None:
    """
    Send a macro rotation alert when a SIGNIFICANT regime shift is detected.
    Called from src/rotation/engine.py after shift detection.
    Silent no-op when no channels configured. Never raises.
    """
    if not _SLACK_WEBHOOK and not _ALERT_EMAIL:
        return

    shift_label  = rotation_result.get("shift_label", "NONE")
    shift_score  = rotation_result.get("shift_score", 0)
    changed_dims = rotation_result.get("changed_dims", [])
    recs         = rotation_result.get("recommendations", [])
    sector       = rotation_result.get("sector_signal", {})
    new_regime   = rotation_result.get("new_regime", {})

    dim_lines = "\n".join(
        f"  {d['dimension']}: {d['old']} -> {d['new']} (+{d['weight']}pts)"
        for d in changed_dims
    ) or "  (no changes)"

    rec_lines = "\n".join(
        f"  {r['ticker']}: {r['current_pct']:.1f}% -> {r['new_pct']:.1f}%"
        f"  [{r['rec_action']}]  {r['reason']}"
        for r in recs
    ) or "  (no open positions)"

    reduce_str = " | ".join(sector.get("reduce",     [])) or "None"
    ow_str     = " | ".join(sector.get("overweight", [])) or "None"

    sep = "=" * 52
    lines = [
        "AI Hedge Fund -- MACRO ROTATION ALERT",
        sep,
        f"Regime Shift: {shift_label} (score {shift_score}/10)",
        "",
        "Regime Change:",
        dim_lines,
        "",
        "Rebalance Recommendations:",
        rec_lines,
        "",
        "Sector Rotation:",
        f"  REDUCE:     {reduce_str}",
        f"  OVERWEIGHT: {ow_str}",
        "",
        (
            f"New Regime: {new_regime.get('risk_appetite', '-')} | "
            f"{new_regime.get('rate_direction', '-')} rates | "
            f"{new_regime.get('volatility_regime', '-')} vol"
        ),
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    message = "\n".join(lines)
    subject = f"[AI Hedge Fund] Macro Rotation -- {shift_label} (score {shift_score}/10)"
    _send_slack(message)
    _send_email(subject, message)


def _send_email(subject: str, body: str) -> None:
    if not _ALERT_EMAIL:
        return
    if not _SMTP_USER or not _SMTP_PASS:
        print("  [alerts] Email: SMTP_USER/SMTP_PASS not set — skipping")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = _SMTP_USER
        msg["To"]      = _ALERT_EMAIL
        msg.set_content(body)
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(_SMTP_USER, _SMTP_PASS)
            smtp.send_message(msg)
        print(f"  [alerts] Email sent -> {_ALERT_EMAIL}")
    except Exception as exc:
        print(f"  [alerts] Email error: {exc}")
