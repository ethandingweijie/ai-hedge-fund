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
#:
#: `loans_receivable`, `loans_held_for_investment` and `total_deposits` were
#: listed here until 2026-09-17, when the balance-sheet-financial gate (Phase
#: 1.2A) needed the deposit and loan book to tell a deposit-funded business
#: from a fee-based one. That was the owner decision the note was waiting for,
#: so they moved into the request; the customer-balance proxy lines
#: (`accounts_payable`, `accounts_receivable`, `other_payables`,
#: `other_current_liabilities`) went in with them.
_NOT_FROM_THE_REQUEST: dict[str, str] = {
    "currency": "base LineItem field, set on every statement row",
    "operating_income": "KNOWN GAP: unrequested, None on FMP rows (read at the EBIT/operating-margin fallback)",
    "common_stock_repurchased": "KNOWN GAP: unrequested; buyback reads fall back to share_buyback, which is requested",
}


def test_the_bank_loan_and_deposit_lines_are_now_requested():
    """Three lines were KNOWN GAPs for years: read by the row builder, absent
    from the request, so None on every row. They are inputs to the Tier 2
    customer-balance ratio now, so a silent regression here would make the
    ratio understated on every bank it was meant to catch."""
    requested = _requested_fields()
    for field in ("total_deposits", "loans_receivable",
                  "loans_held_for_investment", "accounts_payable",
                  "accounts_receivable", "other_payables",
                  "other_current_liabilities"):
        assert field in requested, f"{field} is no longer requested"
        assert field not in _NOT_FROM_THE_REQUEST, (
            f"{field} is both requested and recorded as a KNOWN GAP — the gap "
            f"list is stale and will hide the next one")


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
