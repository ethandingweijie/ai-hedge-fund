"""PDF report: section order, page-1 content, and what must never come back.

Owner spec (2026-09-19): page 1 = investment decision (as bullet points, like
the web report) + scenario analysis, with the price chart, key financials and
intelligence signals in a narrow right-hand column; then the industry brief;
then valuation analysis, valuation summary, risk assessment, power law; last,
the decision. Removed as stale: both sensitivity grids and their "diverges"
banners, the old executive summary, and the risk manager's pre-decision
position size, which contradicted the decision's.
"""
import pytest

fitz = pytest.importorskip("fitz")

from src.utils import pdf_report

T = "02020.HK"


def _result():
    rationale = ("First theme: the catalyst cleared.\n\n"
                 "Second theme: the multi-brand mix is under-priced.\n\n"
                 "Third theme: valuation is anchored by the DCF.")
    return {
        "decisions": {T: {
            "action": "BUY", "position_size_pct": 0.052, "price_target": 88.5,
            "entry_range": [71.7, 74.5], "stop_loss": 64.53, "time_horizon": "medium",
            "rationale": rationale,
            "research_view": {"rating_label": "Overweight", "price": 71.7, "price_as_of": "2026-09-19",
                              "intrinsic_value": 105.31, "tsr_12m": 0.2714,
                              "benchmark": {"name": "Hang Seng Index", "expected_return": 0.0991},
                              "callout": "Rating is based on 12-month total shareholder return."},
        }},
        "scenario_analysis": {T: {
            "current_price": 71.7, "expected_value": 104.35, "upside_pct": 45.5,
            "12m_price_target": 88.5,
            "12m_targets_by_scenario": {"bear": 76.11, "base": 86.89, "bull": 99.46},
            "reconciliation": {"blended_iv": 105.31},
            "bear": {"probability": 0.2, "fair_value": 80.52, "assumptions": "Bear case text."},
            "base": {"probability": 0.5, "fair_value": 102.09, "assumptions": "Base case text."},
            "bull": {"probability": 0.3, "fair_value": 127.22, "assumptions": "Bull case text."},
        }},
        "dcf_range": {T: {
            "reported_currency": "HKD",
            "pt_bridge": {"spot": 71.7, "capture": 0.5, "rule": "target = spot + capture x (IV - spot)",
                          "scenarios": {"bear": {"intrinsic_value": 80.52, "target": 76.11},
                                        "base": {"intrinsic_value": 102.09, "target": 86.89},
                                        "bull": {"intrinsic_value": 127.22, "target": 99.46}}},
        }},
        "analyst_signals": {"advanced_risk_manager": {T: {"approved_size_pct": 0.1275,
                                                          "level1_flags": [], "sector_flags": []}}},
        "power_law_analysis": {T: {"total_score": 6, "scale_economies": 7,
                                   "scale_economies_note": "Scale note.", "interpretation": "solid"}},
        "value_trap_analysis": {T: {"overall_verdict": "TRAP RISK LOW"}},
        "raw_financials": {"currency": "HKD", "company": "ANTA Sports Products Limited",
                           "FY2023": {"revenue": 62.4e9, "net_income": 10.2e9, "free_cash_flow": 18.3e9},
                           "FY2024": {"revenue": 70.8e9, "net_income": 15.6e9, "free_cash_flow": 13.3e9},
                           "FY2025": {"revenue": 80.2e9, "net_income": 13.6e9, "free_cash_flow": 17.9e9}},
        "price_history": {T: [{"date": f"2026-0{m}-01", "close": 70 + m} for m in range(1, 10)]},
        "industry_brief": "## SECTION 7 — INDUSTRY INTELLIGENCE BRIEF\n━━━━━━━━━━\nBrief body text.",
        "sector": "Consumer",
        "financial_statements": {"layout": "standard", "currency": "CNY", "periods": ["FY2024", "FY2025"],
                                 "statements": {
            "income": {"title": "Income Statement", "rows": [
                {"key": "revenue", "label": "Revenue", "values": {"FY2024": 70.826e9, "FY2025": 80.219e9},
                 "emphasis": True, "indent": 0},
                {"key": "net_income", "label": "Net income", "values": {"FY2024": 15.596e9, "FY2025": 13.588e9},
                 "emphasis": True, "indent": 0},
                {"key": "eps_diluted", "label": "Diluted EPS", "values": {"FY2024": 5.41, "FY2025": 4.80}}]},
            "balance": {"title": "Balance Sheet", "rows": [
                {"key": "shareholders_equity", "label": "Shareholders' equity",
                 "values": {"FY2024": 61.729e9, "FY2025": 64.152e9}, "emphasis": True}]},
            "cashflow": {"title": "Cash Flow Statement", "rows": [
                {"key": "capital_expenditure", "label": "Capital expenditure",
                 "values": {"FY2024": -3.46e9, "FY2025": -2.5045e9}}]}}},
    }


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    orig = pdf_report._fetch_company_name
    pdf_report._fetch_company_name = lambda t: t          # no network; forces the stored name
    try:
        path = str(tmp_path_factory.mktemp("pdf") / "r.pdf")
        pdf_report.generate_pdf_report(_result(), path, open_after=False)
    finally:
        pdf_report._fetch_company_name = orig
    return [p.get_text() for p in fitz.open(path)]


def test_page_one_is_the_decision_and_the_scenarios(pdf):
    p1 = pdf[0]
    assert "ANTA Sports Products Limited (02020.HK)" in p1       # stored name when lookup fails
    assert p1.index("INVESTMENT DECISION") < p1.index("SCENARIO ANALYSIS")
    for theme in ("First theme", "Second theme", "Third theme"):   # one bullet per theme
        assert theme in p1
    assert "12-month price" in p1 and "Key financials" in p1


def test_sections_follow_the_owner_order(pdf):
    text = "\n".join(pdf)
    order = ["INVESTMENT DECISION", "SCENARIO ANALYSIS", "SECTION 2 — INDUSTRY INTELLIGENCE BRIEF",
             "SECTION 3 — VALUATION AND RISK", "Valuation Analysis", "FINANCIAL STATEMENTS",
             "Valuation Summary",
             "Risk Assessment", "Power Law Analysis", "SECTION 4 — DECISION"]
    pos = [text.index(x) for x in order]
    assert pos == sorted(pos)


def test_stale_content_stays_out(pdf):
    text = "\n".join(pdf)
    for gone in ("Sensitivity", "diverges", "Executive Summary", "Approved Position",
                 "12.8%", "SECTION 7", "CUDA", "■"):
        assert gone not in text, gone


def test_per_share_figures_are_in_the_listing_currency(pdf):
    text = "\n".join(pdf)
    assert "HK$88.50" in text and "HK$105.31" in text
    assert " $88.50" not in text


def test_statements_page_is_in_millions_with_parenthesised_negatives(pdf):
    page = next(p for p in pdf if "FINANCIAL STATEMENTS" in p)
    for s in ("Income Statement (CNY mn)", "Balance Sheet (CNY mn)", "Cash Flow (CNY mn)",
              "Growth & Margins (%)", "80,219.0", "(3,460.0)", "4.80", "(12.9)"):
        assert s in page, s


def test_running_header_is_the_brand(pdf):
    assert "Equitable Research" in pdf[0]
    assert "AI Hedge Fund Research" not in "\n".join(pdf)


def test_target_bridge_is_the_capture_rule(pdf):
    text = "\n".join(pdf)
    assert "capture 50% of the gap to fair value" in text
    assert "forward sector multiples" not in text
