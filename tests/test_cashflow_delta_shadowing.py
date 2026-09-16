"""A cash-flow DELTA must never be published as a balance-sheet STOCK.

`search_line_items` fetches four FMP statements and merges them into ONE flat
dict per date before translating camelCase -> snake_case::

    merged = {**cf_by_date.get(date, {}), **bal_by_date.get(date, {}),
              **rt_by_date.get(date, {}), **inc}

so a balance-sheet key displaces a cash-flow key only when the two share a
spelling. FMP's balance sheet spells the payable stock `accountPayables` and its
cash flow spells the payable delta `accountsPayables` -- one letter apart, and
`_safe_float(0)` is not None, so a zero delta displaces a non-zero stock too.
`_BALANCE_MAP` carried BOTH spellings and the translation loop is first-wins
over `_ALL_MAPS`, which put `accountsPayables` first.

The delta therefore won on **36 of 36 names** measured live on 2026-09-17:

    AAPL   0.902bn delta published as 69.86bn stock   (77x understated)
    COST   0.404bn                "    19.78bn         (49x)
    BABA   0.000bn                "   358.55bn         (total loss)
    HOOD, ICE, CME, BN4.SI, U96.SI, C38U.SI went NEGATIVE -- which a payable
    stock never is and a payable delta routinely is.

Four consumers read that flow as a level: DPO / the cash-conversion cycle,
`earnings_quality_agent._BALANCE_FIELDS`, the financial-statements table, and
`dcf_agent._tier2_customer_balance_ratio` (the balance-sheet-financial gate),
whose Tier-2 denominator decision sat on a number the feed never supplied. On
S68.SI the true ratio is 0.4738; the shadowed one read 0.3135, only 0.0135 above
the 0.30 threshold the Market Infrastructure exemption was justified against.

`accounts_receivable` and `inventory` have the same two-candidate shape but are
NOT exposed: FMP's balance sheet carries those exact spellings, so the merge
order already protects them. The data-driven test below is what makes that a
measured fact rather than an assumption, and what catches the next key.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import src.tools.api as api
from src.tools.api import (
    _BALANCE_MAP,
    _CASHFLOW_MAP,
    _INCOME_MAP,
    _RATIOS_MAP,
    _safe_float,
)

#: Reconstructed here because it is a LOCAL inside `search_line_items`.
#: `test_the_map_order_the_invariant_is_stated_against_is_the_real_one` pins
#: this reconstruction to the source, so the two cannot drift apart.
_ALL_MAPS = {**_INCOME_MAP, **_BALANCE_MAP, **_CASHFLOW_MAP, **_RATIOS_MAP}

_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN = _ROOT / "tests" / "fixtures" / "golden"


# ── The frozen payloads ─────────────────────────────────────────────────────
# Live FMP /stable readings, 2026-09-17. Values are UNROUNDED as returned; do
# not tidy them into round numbers -- the point of the fixture is that the
# delta and the stock are different magnitudes of the same quantity, and
# rounding is how that stops being visible.

_SCHW_DATE = "2025-12-31"
_SCHW_INCOME = {"date": _SCHW_DATE, "period": "FY", "reportedCurrency": "USD",
                "revenue": 21870000000, "netIncome": 7172000000}
_SCHW_BALANCE = {
    "date": _SCHW_DATE, "period": "FY", "reportedCurrency": "USD",
    "totalAssets": 490995000000,
    "accountPayables": 142030000000,       # the STOCK
    "otherPayables": 0,
    "taxPayables": 0,
    "totalPayables": 142030000000,
    "accountsReceivables": 107547000000,
    "netReceivables": 107547000000,
    "otherCurrentLiabilities": 255747000000,
}
_SCHW_CASHFLOW = {
    "date": _SCHW_DATE, "period": "FY", "reportedCurrency": "USD",
    "accountsPayables": 14782000000,       # the DELTA, one letter apart
    "accountsReceivables": -24140000000,
    "operatingCashFlow": 9000000000,
    "capitalExpenditure": -1000000000,
}
_SCHW_RATIOS = {"date": _SCHW_DATE, "period": "FY"}

#: BABA FY2025: the delta is exactly 0 and the stock is RMB 358.5bn. Because
#: `_safe_float(0)` returns 0.0 and the translation only skips None, a zero
#: delta erased the largest payable in the basket.
_BABA_DATE = "2025-03-31"
_BABA_INCOME = {"date": _BABA_DATE, "period": "FY", "reportedCurrency": "CNY",
                "revenue": 996347000000, "netIncome": 130109000000}
_BABA_BALANCE = {"date": _BABA_DATE, "period": "FY", "reportedCurrency": "CNY",
                 "totalAssets": 1825878000000,
                 "accountPayables": 358548910000}
_BABA_CASHFLOW = {"date": _BABA_DATE, "period": "FY", "reportedCurrency": "CNY",
                  "accountsPayables": 0,
                  "operatingCashFlow": 158819000000}
_BABA_RATIOS = {"date": _BABA_DATE, "period": "FY"}

#: S68.SI (SGX) FY2026: the delta is POSITIVE here, which is the case that
#: hides -- nothing looks broken, the stock is merely 4.2x too small.
_SGX_DATE = "2026-06-30"
_SGX_INCOME = {"date": _SGX_DATE, "period": "FY", "reportedCurrency": "SGD",
               "revenue": 1700000000, "netIncome": 500000000}
_SGX_BALANCE = {"date": _SGX_DATE, "period": "FY", "reportedCurrency": "SGD",
                "totalAssets": 4563092000,
                "accountPayables": 961143000,
                "otherPayables": 192232000,
                "otherCurrentLiabilities": -1153375000,
                "accountsReceivables": 1008476000,
                "netReceivables": 1139570000}
_SGX_CASHFLOW = {"date": _SGX_DATE, "period": "FY", "reportedCurrency": "SGD",
                 "accountsPayables": 229822000,
                 "accountsReceivables": -234134000,
                 "operatingCashFlow": 600000000}
_SGX_RATIOS = {"date": _SGX_DATE, "period": "FY"}


def _serve(monkeypatch, *, income, balance, cashflow, ratios):
    """Route `search_line_items`' four `_fmp_get` calls to frozen payloads."""
    def fake_get(path, params, api_key=None, uncap=False):
        if path.endswith("/income-statement"):
            return [income]
        if path.endswith("/balance-sheet-statement"):
            return [balance]
        if path.endswith("/cash-flow-statement"):
            return [cashflow]
        if path.endswith("/ratios"):
            return [ratios]
        return []

    monkeypatch.setattr(api, "_fmp_get", fake_get)
    monkeypatch.setattr(api, "_statement_cache_get", lambda key: None)
    monkeypatch.setattr(api, "_statement_cache_put", lambda key, value: None)
    monkeypatch.setattr(api.time, "sleep", lambda *_a, **_k: None)


_FIELDS = ["accounts_payable", "accounts_receivable", "other_payables",
           "other_current_liabilities", "total_assets", "revenue"]


# ── The bug, replayed through the real code path ────────────────────────────

def test_accounts_payable_is_the_stock_not_the_cashflow_delta(monkeypatch):
    _serve(monkeypatch, income=_SCHW_INCOME, balance=_SCHW_BALANCE,
           cashflow=_SCHW_CASHFLOW, ratios=_SCHW_RATIOS)
    rows = api.search_line_items("SCHW", _FIELDS, end_date="2026-09-17",
                                 period="annual", limit=3, api_key="x")
    assert len(rows) == 1
    assert rows[0].accounts_payable == pytest.approx(142_030_000_000)
    assert rows[0].accounts_payable != pytest.approx(14_782_000_000), (
        "the cash-flow delta is being published as the balance-sheet stock")


def test_a_zero_delta_does_not_erase_the_stock(monkeypatch):
    """`_safe_float(0)` is 0.0, not None, so a zero delta is a value and the
    first-wins translation took it. BABA's RMB 358.5bn payable read 0."""
    _serve(monkeypatch, income=_BABA_INCOME, balance=_BABA_BALANCE,
           cashflow=_BABA_CASHFLOW, ratios=_BABA_RATIOS)
    rows = api.search_line_items("BABA", _FIELDS, end_date="2026-09-17",
                                 period="annual", limit=3, api_key="x")
    assert rows[0].accounts_payable == pytest.approx(358_548_910_000)


def test_a_small_positive_delta_does_not_quietly_scale_the_stock(monkeypatch):
    """The case that hides: nothing is negative and nothing is zero, the stock
    is simply 4.2x too small. S68.SI read 0.2298bn against a 0.9611bn truth."""
    _serve(monkeypatch, income=_SGX_INCOME, balance=_SGX_BALANCE,
           cashflow=_SGX_CASHFLOW, ratios=_SGX_RATIOS)
    rows = api.search_line_items("S68.SI", _FIELDS, end_date="2026-09-17",
                                 period="annual", limit=3, api_key="x")
    assert rows[0].accounts_payable == pytest.approx(961_143_000)


def test_the_sibling_lines_were_never_exposed(monkeypatch):
    """`accountsReceivables` and `otherCurrentLiabilities` exist on BOTH
    statements under the same spelling, so the balance sheet -- merged after the
    cash flow -- already displaced the delta. Asserted so that a future edit to
    the merge order is caught here rather than in a valuation."""
    _serve(monkeypatch, income=_SCHW_INCOME, balance=_SCHW_BALANCE,
           cashflow=_SCHW_CASHFLOW, ratios=_SCHW_RATIOS)
    row = api.search_line_items("SCHW", _FIELDS, end_date="2026-09-17",
                                period="annual", limit=3,
                                api_key="x")[0]
    assert row.accounts_receivable == pytest.approx(107_547_000_000)
    assert row.other_current_liabilities == pytest.approx(255_747_000_000)
    assert row.total_assets == pytest.approx(490_995_000_000)


# ── Structural regression ───────────────────────────────────────────────────

#: camelCase keys FMP emits ONLY on the cash-flow statement, where they are a
#: period delta rather than a level. Not exhaustive of the cash-flow schema --
#: exhaustive of the ones that collide with a `_BALANCE_MAP` target, which is
#: the only way this can bite. `test_no_recorded_cashflow_key_shadows_a_balance_stock`
#: is what keeps the list honest against real payloads.
_CASHFLOW_DELTA_ONLY_KEYS = frozenset({"accountsPayables"})


def test_no_cashflow_only_delta_key_is_a_balance_map_key():
    leaked = sorted(_CASHFLOW_DELTA_ONLY_KEYS & set(_BALANCE_MAP))
    assert not leaked, (
        f"{leaked} is a cash-flow DELTA key mapped as a balance-sheet STOCK; it "
        f"will win the first-wins translation because FMP's balance sheet spells "
        f"the stock differently and cannot displace it in the merge")


def test_accounts_payable_has_exactly_one_balance_spelling():
    cands = [k for k, v in _BALANCE_MAP.items() if v == "accounts_payable"]
    assert cands == ["accountPayables"], cands


def test_the_delta_spelling_is_not_mapped_to_anything():
    """Deliberate. Nothing consumes the payable delta today, and mapping it to
    `accounts_payable` is the bug. If a working-capital consumer wants it, it
    gets its own snake name (`change_in_accounts_payable`) -- a delta and a
    stock must not share a field."""
    assert "accountsPayables" not in _ALL_MAPS


def test_the_map_order_the_invariant_is_stated_against_is_the_real_one():
    src = inspect.getsource(api.search_line_items)
    assert ("_ALL_MAPS = {**_INCOME_MAP, **_BALANCE_MAP, **_CASHFLOW_MAP, "
            "**_RATIOS_MAP}") in src, (
        "the translation order changed; every first-wins assertion in this file "
        "is stated against the old one")
    assert "if val is not None and our_key not in snake:" in src, (
        "the translation is no longer first-wins")
    # Balance merged AFTER cash flow: the only reason receivables/inventory are safe.
    merge = src.index("merged = {")
    assert src.index("**cf_by_date", merge) < src.index("**bal_by_date", merge), (
        "the cash-flow statement is merged after the balance sheet; delta keys "
        "now displace stocks of the SAME spelling too")


# ── Data-driven: every recorded golden payload ──────────────────────────────

def _golden_calls() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for path in sorted(_GOLDEN.glob("*/raw/calls.json")):
        calls = json.loads(path.read_text(encoding="utf-8"))
        out[path.parent.parent.name] = [
            c for c in calls if c.get("target") == "src.tools.api._fmp_get"]
    return out


def _by_endpoint(calls: list[dict]) -> dict[str, dict[str, dict]]:
    """endpoint -> date -> row, for the four statements."""
    buckets = {"income-statement": {}, "balance-sheet-statement": {},
               "cash-flow-statement": {}, "ratios": {}}
    for call in calls:
        ep = next((b for b in buckets if f"/stable/{b}" in call["key"]), None)
        if ep is None:
            continue
        result = call.get("result")
        if not isinstance(result, list):
            continue
        for row in result:
            if isinstance(row, dict) and row.get("date"):
                buckets[ep].setdefault(row["date"], row)
    return buckets


def _all_dates(buckets) -> list[str]:
    return sorted({d for b in buckets.values() for d in b})


#: Every snake target with two or more candidate camel spellings -- the shape
#: that can be shadowed. Computed, not listed, so a new alias is covered
#: automatically.
_MULTI_CANDIDATE_TARGETS = {
    v for v in _ALL_MAPS.values()
    if sum(1 for x in _ALL_MAPS.values() if x == v) > 1
}


def test_the_recorded_fixtures_exercise_the_colliding_shape():
    """Guard the guard. If no recorded payload has a multi-candidate target
    supplied by both statements, the invariant below is vacuous."""
    calls = _golden_calls()
    assert calls, f"no golden fixtures under {_GOLDEN}"
    seen = 0
    for ticker, tcalls in calls.items():
        buckets = _by_endpoint(tcalls)
        for date in _all_dates(buckets):
            cf = buckets["cash-flow-statement"].get(date, {})
            bs = buckets["balance-sheet-statement"].get(date, {})
            for k in cf:
                if _ALL_MAPS.get(k) in _MULTI_CANDIDATE_TARGETS and bs:
                    seen += 1
                    break
    assert seen >= 10, (
        f"only {seen} recorded periods put a multi-candidate cash-flow key "
        f"beside a balance sheet; the shadowing invariant is not being tested")


def test_no_recorded_cashflow_key_shadows_a_balance_stock():
    """The general invariant, over real payloads rather than my three.

    For every date where the balance sheet supplies a target under SOME
    spelling, the winner of the first-wins translation must be a spelling the
    balance sheet actually carries. A cash-flow-only spelling winning is the
    bug, and this is the test that would have caught it on day one instead of
    in a Tier-2 ratio 0.0135 above its threshold.
    """
    offenders: list[str] = []
    calls = _golden_calls()
    for ticker, tcalls in calls.items():
        buckets = _by_endpoint(tcalls)
        for date in _all_dates(buckets):
            merged = {**buckets["cash-flow-statement"].get(date, {}),
                      **buckets["balance-sheet-statement"].get(date, {}),
                      **buckets["ratios"].get(date, {}),
                      **buckets["income-statement"].get(date, {})}
            bs = buckets["balance-sheet-statement"].get(date, {})
            if not bs:
                continue
            for target in _MULTI_CANDIDATE_TARGETS:
                cands = [k for k, v in _ALL_MAPS.items() if v == target]
                winner = next((k for k in cands
                               if _safe_float(merged.get(k)) is not None), None)
                if winner is None:
                    continue
                from_cf_only = (winner in buckets["cash-flow-statement"].get(date, {})
                                and winner not in bs)
                bs_supplies = [k for k in cands
                               if _safe_float(bs.get(k)) is not None]
                if from_cf_only and bs_supplies:
                    offenders.append(
                        f"{ticker} {date} {target}: cash-flow `{winner}`="
                        f"{merged[winner]} won over balance `{bs_supplies[0]}`="
                        f"{bs[bs_supplies[0]]}")
    assert not offenders, (
        "a cash-flow delta is displacing a balance-sheet stock:\n  "
        + "\n  ".join(offenders))
