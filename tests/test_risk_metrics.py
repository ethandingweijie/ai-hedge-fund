"""Balance sheet & risk card + deterministic value-trap balance-sheet check (owner, 2026-11-27)."""

from src.agents.analysis import risk_metrics as rm


def _row(period, net_debt=None, ebitda=None, **kw):
    r = {"period": period, "net_debt": net_debt, "ebitda": ebitda}
    r.update(kw)
    return r


def _data(rows, *, ticker="XYZ", profile="Mature Platform", wacc_build=None, bank=None, **extra):
    dr = {"profile": profile, "financials_used": {"currency": "USD", "rows": rows}}
    if wacc_build is not None:
        dr["wacc_build"] = wacc_build
    if bank is not None:
        dr["bank_breakdown"] = bank
    d = {"tickers": [ticker], "dcf_range": {ticker: dr}}
    d.update(extra)
    return d


def _by(block):
    return {m["key"]: m for m in block["metrics"]}


# ── level thresholds ────────────────────────────────────────────────────────

def test_steady_high_leverage_is_red_without_a_rising_trend():
    # SNAP-shaped: leverage high and flat, never "rising 3 years" -> the old check said GREEN.
    rows = [_row(f"{y}-12-31", 6.0e9, 1.0e9) for y in (2025, 2024, 2023, 2022)]
    chk = rm.assess_balance_sheet(_data(rows), "XYZ", as_of="2026-06-01")
    assert chk["status"] == "RED"
    assert "6.0x" in chk["evidence"] and "no sustained rise" in chk["evidence"]


def test_nd_ebitda_bands():
    for lev, want in ((2.9, "GREEN"), (3.5, "AMBER"), (5.1, "RED")):
        rows = [_row("2025-12-31", lev * 1e9, 1e9)]
        assert rm.assess_balance_sheet(_data(rows), "XYZ", as_of="2026-03-01")["status"] == want


def test_interest_cover_bands_from_valuation_inputs():
    rows = [_row("2025-12-31", 1e9, 1e9)]                    # 1x leverage: GREEN on level
    for cov, want in ((4.0, "GREEN"), (2.5, "AMBER"), (1.2, "RED")):
        d = _data(rows, wacc_build={"interest_coverage": cov, "rating": "BB"})
        assert rm.assess_balance_sheet(d, "XYZ", as_of="2026-03-01")["status"] == want
        assert _by(rm.assemble_risk_metrics(d, "XYZ", as_of="2026-03-01"))["interest_cover"]["display"] == f"{cov:.1f}x"


def test_net_debt_against_negative_ebitda_is_red_and_net_cash_green():
    neg = rm.assess_balance_sheet(_data([_row("2025-12-31", 3e9, -2e8)]), "XYZ", as_of="2026-03-01")
    assert neg["status"] == "RED"
    cash = rm.assess_balance_sheet(_data([_row("2025-12-31", -1e9, 5e8)]), "XYZ", as_of="2026-03-01")
    assert cash["status"] == "GREEN" and "net cash" in cash["evidence"]


# ── trend + NOT ASSESSED ────────────────────────────────────────────────────

def test_rising_trend_red_on_three_years_amber_on_two():
    four = [_row("2025-12-31", 2.8e9, 1e9), _row("2024-12-31", 2.2e9, 1e9),
            _row("2023-12-31", 1.8e9, 1e9), _row("2022-12-31", 1.2e9, 1e9)]
    assert rm.assess_balance_sheet(_data(four), "XYZ", as_of="2026-03-01")["status"] == "RED"
    three = four[:3]
    assert rm.assess_balance_sheet(_data(three), "XYZ", as_of="2026-03-01")["status"] == "AMBER"


def test_trend_with_fewer_than_three_periods_is_not_assessed_but_level_still_runs():
    rows = [_row("2025-12-31", 1.5e9, 1e9), _row("2024-12-31", 1e9, 1e9)]
    chk = rm.assess_balance_sheet(_data(rows), "XYZ", as_of="2026-03-01")
    assert chk["status"] == "GREEN"
    assert "trend not assessed" in chk["evidence"]


def test_stale_statements_are_not_assessed():
    rows = [_row("2023-12-31", 9e9, 1e9)]                    # 9x but 26 months old
    chk = rm.assess_balance_sheet(_data(rows), "XYZ", as_of="2026-03-01")
    assert chk["status"] == rm.NOT_ASSESSED
    assert "18 months" in chk["evidence"]


def test_no_statements_not_assessed():
    assert rm.assess_balance_sheet(_data([]), "XYZ", as_of="2026-03-01")["status"] == rm.NOT_ASSESSED


def test_raw_financials_fallback_with_fiscal_year_keys():
    d = {"tickers": ["ABC"], "raw_financials": {
        "FY2025": {"revenue": 1, "net_debt": 4e9, "operating_income": 8e8, "depreciation_and_amortization": 2e8},
        "FY2024": {"revenue": 1, "net_debt": 4e9, "operating_income": 8e8, "depreciation_and_amortization": 2e8},
    }}
    chk = rm.assess_balance_sheet(d, "ABC", as_of="2026-05-01")
    assert chk["status"] == "AMBER"                          # 4.0x on EBIT + D&A, FY2025 read as Dec year end


# ── banks ───────────────────────────────────────────────────────────────────

def test_banks_routed_to_capital_checks():
    rows = [_row("2025-12-31", 9e10, 1e9)]                   # 90x "leverage" is meaningless for a bank
    ok = _data(rows, profile="Money Center Bank (SG)", bank={"cet1_ratio": 0.146, "loan_to_deposit_ratio": 0.78})
    chk = rm.assess_balance_sheet(ok, "XYZ", as_of="2026-03-01")
    assert chk["status"] == "GREEN" and chk["kind"] == "bank_capital"
    thin = _data(rows, profile="Money Center Bank (SG)", bank={"cet1_ratio": 0.10, "loan_to_deposit_ratio": 1.15})
    assert rm.assess_balance_sheet(thin, "XYZ", as_of="2026-03-01")["status"] == "RED"
    none = _data(rows, profile="Bank / Lending Institution")
    assert rm.assess_balance_sheet(none, "XYZ", as_of="2026-03-01")["status"] == rm.NOT_ASSESSED
    block = rm.assemble_risk_metrics(ok, "XYZ", as_of="2026-03-01")
    keys = _by(block)
    assert "cet1_ratio" in keys and "loan_to_deposit" in keys and "net_debt_to_ebitda" not in keys
    assert keys["interest_cover"]["display"] is None and "bank" in keys["interest_cover"]["reason"]
    assert keys["credit_rating"]["display"] is None


# ── metric assembly ─────────────────────────────────────────────────────────

def test_assembly_reads_existing_payload_values():
    rows = [_row(f"{y}-12-31", 2e9, 1e9, stock_based_compensation=3e8, operating_cash_flow=1e9,
                 shares_outstanding=s) for y, s in ((2025, 110.0), (2024, 105.0), (2023, 102.0), (2022, 100.0))]
    closes = [{"date": f"d{i}", "close": 100 * (1.01 if i % 2 else 0.99) ** (i % 3)} for i in range(260)]
    d = _data(rows, wacc_build={"interest_coverage": 8.0, "rating": "A", "rating_source": "agency"},
              price_history={"XYZ": closes},
              analyst_signals={"advanced_risk_manager": {"XYZ": {"liquidity_days_to_exit": 0.4,
                                                                  "liquidity_adv_dollars": 5e7}}},
              short_interest={"XYZ": {"short_float_pct": 12.3, "days_to_cover": 4.5}})
    b = rm.assemble_risk_metrics(d, "XYZ", as_of="2026-03-01")
    m = _by(b)
    assert m["net_debt_to_ebitda"]["display"] == "2.0x" and m["net_debt_to_ebitda"]["status"] == "GREEN"
    assert m["interest_cover"]["display"] == "8.0x"
    assert m["credit_rating"]["display"] == "A (agency)"
    assert m["volatility_1y"]["value"] > 0
    assert m["liquidity"]["display"].startswith("0.4 days")
    assert m["short_interest"]["display"] == "12.3% of float | 4.5 days to cover"
    assert m["sbc_pct_ocf"]["display"] == "30%" and m["sbc_pct_ocf"]["status"] == "AMBER"
    assert m["share_count_change_3y"]["display"] == "+10.0%" and m["share_count_change_3y"]["status"] == "AMBER"
    assert all(x.get("explain") for x in b["metrics"])
    line = b["risk_line"]
    assert line.startswith("Balance sheet and dilution:")
    assert "30% of operating cash flow" in line and "10.0% over three years" in line
    assert "EBITDA" not in line                              # leverage is within threshold


def test_missing_values_carry_a_reason():
    b = rm.assemble_risk_metrics({"tickers": ["XYZ"]}, "XYZ", as_of="2026-03-01")
    for m in b["metrics"]:
        assert m["display"] is None and m["reason"], m["key"]
    assert b["risk_line"] is None


def test_volatility_falls_back_to_risk_manager_window():
    d = {"tickers": ["XYZ"], "analyst_signals": {"risk_management_agent": {
        "XYZ": {"volatility_metrics": {"annualized_volatility": 0.31}}}}}
    m = _by(rm.assemble_risk_metrics(d, "XYZ"))["volatility_1y"]
    assert m["display"] == "31%" and "60-day" in m["basis"]


# ── verdict arithmetic ──────────────────────────────────────────────────────

def _trap(bs, verdict, others=("GREEN", "GREEN", "AMBER", "GREEN")):
    keys = ("dividend_sustainability", "structural_decline", "earnings_cashflow_mismatch", "insider_behaviour")
    t = {k: {"status": s, "evidence": "x"} for k, s in zip(keys, others)}
    t["balance_sheet_deterioration"] = {"status": bs, "evidence": "model"}
    t["overall_verdict"] = verdict
    return t


def test_apply_check_moves_verdict_by_the_rule():
    t = _trap("GREEN", "TRAP RISK LOW")
    new = rm.apply_balance_sheet_check(t, {"status": "RED", "evidence": "6x"})
    assert new["overall_verdict"] == "TRAP RISK MEDIUM"
    assert new["balance_sheet_deterioration"]["model_status"] == "GREEN"
    assert new["verdict_before_balance_check"] == "TRAP RISK LOW"
    assert t["balance_sheet_deterioration"]["status"] == "GREEN"     # input untouched


def test_apply_check_reaches_high_with_a_second_red():
    t = _trap("GREEN", "TRAP RISK MEDIUM", others=("RED", "GREEN", "GREEN", "GREEN"))
    assert rm.apply_balance_sheet_check(t, {"status": "RED", "evidence": "6x"})["overall_verdict"] == "TRAP RISK HIGH"


def test_apply_check_keeps_the_models_extra_judgement():
    # The model said HIGH on items outside the five checks (asset impairment); a GREEN balance
    # sheet replacing a GREEN one changes nothing.
    t = _trap("GREEN", "TRAP RISK HIGH")
    assert rm.apply_balance_sheet_check(t, {"status": "GREEN", "evidence": "1x"})["overall_verdict"] == "TRAP RISK HIGH"
    # ...and a RED model call downgraded to NOT ASSESSED moves it down one notch only.
    t2 = _trap("RED", "TRAP RISK HIGH", others=("RED", "GREEN", "GREEN", "GREEN"))
    assert rm.apply_balance_sheet_check(t2, {"status": rm.NOT_ASSESSED, "evidence": "stale"})["overall_verdict"] \
        == "TRAP RISK MEDIUM"


def test_apply_check_is_idempotent():
    t = _trap("GREEN", "TRAP RISK LOW")
    chk = {"status": "RED", "evidence": "6x"}
    once = rm.apply_balance_sheet_check(t, chk)
    assert rm.apply_balance_sheet_check(once, chk) == once


# ── earnings quality: one observation ───────────────────────────────────────

def test_single_accrual_observation_is_not_assessed():
    from src.agents.intelligence.earnings_quality_agent import _compute_earnings_quality
    one = [{"net_income": 100.0, "operating_cash_flow": 150.0, "total_assets": 1000.0}]
    assert _compute_earnings_quality("X", one).accrual_flag == "NOT ASSESSED"
    two = one + [{"net_income": 90.0, "operating_cash_flow": 140.0, "total_assets": 900.0}]
    assert _compute_earnings_quality("X", two).accrual_flag == "GREEN"


# ── risks to rating ─────────────────────────────────────────────────────────

def test_risks_to_rating_carries_the_balance_sheet_line():
    from src.agents.pm.reader_text import build_risks_to_rating
    trap = {"balance_sheet_deterioration": {"status": "RED", "evidence": "Model text."},
            "insider_behaviour": {"status": "AMBER", "evidence": "CFO sold."}}
    line = "Balance sheet and dilution: net debt is 6.0x EBITDA (above 3x)."
    out = build_risks_to_rating(rating_label=None, spot=None, target=None, view=None, pt_bridge=None,
                                scenario=None, trap=trap, balance_sheet_line=line)
    assert out[0] == line
    assert not any(s.startswith("Balance sheet (RED)") for s in out)
    assert any(s.startswith("Insider selling") for s in out)
    out2 = build_risks_to_rating(rating_label=None, spot=None, target=None, view=None, pt_bridge=None,
                                 scenario=None, trap=trap)
    assert any(s.startswith("Balance sheet (RED)") for s in out2)


# ── on read + PDF ───────────────────────────────────────────────────────────

def test_attach_on_read_fills_missing_block_only():
    from app.backend.services.analysis_service import _attach_risk_metrics
    rows = [_row("2025-12-31", 6e9, 1e9)]
    payload = {"ticker": "XYZ", "run_at": "2026-03-01T00:00:00", "data": _data(rows)}
    _attach_risk_metrics(payload)
    blk = payload["data"]["risk_metrics"]["XYZ"]
    assert blk["assembled_on_read"] and blk["balance_sheet_check"]["status"] == "RED"
    saved = {"ticker": "XYZ", "data": {**_data(rows), "risk_metrics": {"XYZ": {"metrics": [], "kept": 1}}}}
    _attach_risk_metrics(saved)
    assert saved["data"]["risk_metrics"]["XYZ"] == {"metrics": [], "kept": 1}


def test_pdf_block_renders():
    from src.utils.pdf_report import _balance_sheet_risk_block, _build_styles
    blk = rm.assemble_risk_metrics(_data([_row("2025-12-31", 6e9, 1e9)]), "XYZ", as_of="2026-03-01")
    flow = _balance_sheet_risk_block(blk, _build_styles(), 400.0)
    assert len(flow) >= 3
    assert _balance_sheet_risk_block({}, _build_styles(), 400.0) == []
