"""Every field _extract_annual_series copies off a LineItem must be REQUESTED
from the feed by run_dcf_agent's search_line_items call.

This trap has now bitten four times -- R&D, SBC, change_in_working_capital,
and (2026-09-15) short_term_investments: the row builder read the field, the
request list never asked for it, so it was None on every row and the fix it
fed silently did nothing in production while its unit tests passed.
"""
import inspect
import re

from src.agents.analysis import dcf_agent as d

#: Fields the row builder reads without requesting them, each with its reason.
#: Verified 2026-09-15 that search_line_items returns ONLY requested fields
#: (09988.HK: short_term_investments absent unless asked for), so every entry
#: below except `currency` is None on every FMP row today. Requesting them would
#: change bank and buyback inputs, so they are recorded -- not silently fixed --
#: pending an owner decision. A NEW unrequested field still fails this test.
_NOT_FROM_THE_REQUEST: dict[str, str] = {
    "currency": "base LineItem field, set on every statement row",
    "operating_income": "KNOWN GAP: unrequested, None on FMP rows (read at the EBIT/operating-margin fallback)",
    "common_stock_repurchased": "KNOWN GAP: unrequested; buyback reads fall back to share_buyback, which is requested",
    "loans_receivable": "KNOWN GAP: unrequested bank KPI (loan-to-deposit), None on FMP rows",
    "loans_held_for_investment": "KNOWN GAP: unrequested bank KPI, None on FMP rows",
    "total_deposits": "KNOWN GAP: unrequested bank KPI, None on FMP rows",
}


def _requested_fields() -> set[str]:
    src = inspect.getsource(d.run_dcf_agent)
    m = re.search(r"search_line_items\(\s*ticker,\s*\[(.*?)\],\s*end_date", src, re.S)
    assert m, "search_line_items request list not found in run_dcf_agent"
    body = re.sub(r"#[^\n]*", "", m.group(1))
    return set(re.findall(r'"([a-z0-9_]+)"', body))


def _fields_read_by_row_builder() -> set[str]:
    src = inspect.getsource(d._extract_annual_series)
    return set(re.findall(r'getattr\(li,\s*"([a-z0-9_]+)"', src))


def test_short_term_investments_is_requested():
    assert "short_term_investments" in _requested_fields()


def test_every_field_the_row_builder_reads_is_requested():
    missing = sorted(_fields_read_by_row_builder() - _requested_fields() - set(_NOT_FROM_THE_REQUEST))
    assert not missing, f"read off LineItem but never requested: {missing}"
