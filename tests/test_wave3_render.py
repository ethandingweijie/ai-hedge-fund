"""The analyst SOTP renders on the PDF (Wave 3, 2026-09-22): the web (both
render paths) and the Excel model already showed dcf_range.sotp_breakdown; the
PDF had no block for it. Owner instruction: check the valuation can be printed
on PDF and Excel as well as the web/mobile interface."""
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate

from src.utils import pdf_report as pr

BREAKDOWN = {
    "method": "SOTP (analyst)", "reporting_currency": "USD",
    "sentence": "TP US$212 = segments US$160bn + net cash US$5bn, no holdco discount.",
    "rows": [{"name": "Commercial Airplanes", "revenue_fwd": 4.0e10, "method": "EV/Rev", "multiple": 1.8,
              "value": 7.2e10, "rationale": "ev_rev 1.5-2.0x: peers"},
             {"name": "Defense, Space & Security", "revenue_fwd": 2.6e10, "method": "EV/Rev", "multiple": 1.4,
              "value": 3.64e10, "rationale": "ev_rev 1.2-1.6x"}],
    "segment_value": 1.084e11, "associates": 0.0, "net_cash": 5.0e9, "nav": 1.134e11,
    "holdco_discount_pct": 0.0, "holdco_discount": 0.0, "final": 1.134e11,
    "per_share": 150.0, "per_share_reporting": 150.0, "shares": 7.56e8, "fx_to_reporting": 1.0,
    "sources": {"all": "gemini_accepted"},
}


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("RptLabel", parent=s["Normal"], fontSize=8))
    s.add(ParagraphStyle("RptBody", parent=s["Normal"], fontSize=8))
    return s


def test_the_analyst_sotp_block_renders_to_a_pdf_and_names_the_accepted_inputs():
    flow = pr._analyst_sotp_block_pdf({"sotp_breakdown": BREAKDOWN}, _styles(), 400.0)
    assert flow and any("owner-accepted, cited inputs" in getattr(f, "text", "") for f in flow)
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build(list(flow))
    assert buf.getvalue().startswith(b"%PDF")


def test_without_a_breakdown_the_pdf_prints_nothing_for_it():
    assert pr._analyst_sotp_block_pdf({}, _styles(), 400.0) == []
    assert pr._analyst_sotp_block_pdf({"sotp_breakdown": {"rows": [], "per_share_reporting": 1.0}}, _styles(), 400.0) == []
