"""Phase 1.4 -- HK/SG balance-sheet fallbacks and the quarterly step-change flag.

Two things landed together and are tested together, because the second only
means anything if the first is right:

1. **The fallbacks can now see short-term investments at all.** Before this, the
   HK feed had no `short_term_investments` mapping and the SG feed keyed cash as
   `cash` while the engine asks for `cash_and_equivalents` -- so
   ``_refresh_balance_sheet_from_latest_quarter`` returned early on every SG name
   ("carries both a cash and a debt figure") and every EV-based method on an HK
   name netted nothing. Both rules were fitted against live payloads on all
   eight allowlist .SI names and four HK names, at EVERY period the provider
   returns, not just the newest one.

2. **The quarterly overlay records how far it moved the answer.** Owner-set
   (2026-09-18): ``abs(q_net_cash - a_net_cash) / max(abs(a_net_cash), 1.0) >
   0.25`` files a disclosure-only record. Two points about that spec which the
   tests below pin rather than paraphrase:

   - It is **not** the 15% cross-provider parity check the original plan text
     described, and it cannot be: ``src/tools/api.py`` dispatches FMP and the
     HK/SG fallback exclusive-or, so the two are never both in hand. What is
     compared is one run's ANNUAL reading against the QUARTERLY reading it
     substituted -- which is the substitution worth auditing.
   - It is ``applied: False``. Crossing the threshold never aborts the overlay
     and never reverts the row to stale annual data. The tests assert the row
     moved *and* the record exists, in the same breath, because a telemetry flag
     that quietly became a gate is the failure mode this design exists to avoid.

The deviations this rule set is knowingly shipped with are recorded in
``TestTheHkRuleIsScoredAtEveryPeriod`` and were accepted by the owner on
2026-09-18 rather than tuned away: overfitting the baseline with ad-hoc label
exceptions for 2021-2024 historical filings would introduce regression risk
across the broader HK universe.
"""
from __future__ import annotations

import inspect
import re

import pandas as pd
import pytest

from src.agents.analysis import dcf_agent as d
from src.tools.hk import line_items as hk
from src.tools.hk.mappings import HK_BALANCE_COLS
from src.tools.sg import line_items as sg


# ─────────────────────────────────────────────────────────────────────────────
# The quarterly step-change flag
# ─────────────────────────────────────────────────────────────────────────────
class TestTheStepChangeFlagIsTelemetryAndNothingElse:
    """Disclosure only. The overlay applies whether or not the flag fires."""

    #: 09988.HK as measured: the 31-Mar-2026 year end at RMB98.6bn net cash
    #: against the 30-Jun-2026 quarter at RMB161.7bn. This is the case the
    #: overlay was written for and the case the owner quoted when setting 25%.
    ALIBABA_ANNUAL = {"period": "2026-03-31", "revenue": 1_000e9,
                      "cash_and_equivalents": 173.0e9,
                      "short_term_investments": 184.7e9,
                      "total_debt": 259.1e9, "net_debt": 86.1e9}
    ALIBABA_QUARTER = {"report_period": "2026-06-30",
                       "cash_and_equivalents": 185.5e9,
                       "short_term_investments": 242.7e9,
                       "total_debt": 266.5e9, "net_debt": 81.0e9}

    #: 02020.HK, from production run bda1622f's own forward flag: "net debt
    #: -5.6bn -> -11.1bn". Reproduced here without the short-term-investment leg
    #: so the arithmetic is checkable by hand: net cash 5.6 -> 11.1, delta 5.5,
    #: ratio 5.5/5.6 = 0.9821.
    ANTA_ANNUAL = {"period": "2025-12-31", "revenue": 70e9,
                   "cash_and_equivalents": 12.0e9, "short_term_investments": None,
                   "total_debt": 6.4e9, "net_debt": -5.6e9}
    ANTA_QUARTER = {"report_period": "2026-06-30",
                    "cash_and_equivalents": 17.0e9,
                    "short_term_investments": None,
                    "total_debt": 5.9e9, "net_debt": -11.1e9}

    class _Row:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _quarter(self, monkeypatch, **kw):
        monkeypatch.setattr(d, "search_line_items", lambda *a, **k: [self._Row(**kw)])

    def test_the_threshold_is_the_owners_25_percent(self):
        """Not the 15% from the withdrawn plan text, and not a second
        materiality floor layered on top of it.

        0.25 remains the BASE threshold after the 2026-09-19 ruling; what the
        ruling added is a second, looser one for long gaps, pinned in
        `test_the_wide_threshold_and_its_gap_are_the_owners_numbers` below."""
        assert d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD == 0.25

    def test_the_wide_threshold_and_its_gap_are_the_owners_numbers(self):
        """Owner ruling 2026-09-19, verbatim: "Adjust the step-change flag
        threshold from 0.25 to 0.50 (50%) when period_delta_days > 180".

        The conditional shipped rather than the ruling's parenthetical
        alternative (a global 0.50), so a genuine 91-day year-end-to-Q1
        comparison keeps the tighter band. Strictly greater than 180, so 180
        itself stays on 0.25."""
        assert d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE == 0.50
        assert d._BALANCE_SHEET_STEP_CHANGE_WIDE_GAP_DAYS == 180
        assert d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD < \
            d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE

    @pytest.mark.parametrize("earlier,later,expected", [
        ("2025-12-31", "2026-06-30", 181),      # the ANTA fixture's real gap
        ("2026-03-31", "2026-06-30", 91),       # the ALIBABA fixture's real gap
        ("2025-09-27", "2026-06-27", 273),      # AAPL, the widest measured
        ("2025-12-31", "2026-06-29", 180),      # the boundary itself
    ], ids=["181-anta", "91-alibaba", "273-aapl", "180-boundary"])
    def test_the_gap_helper_measures_calendar_days(self, earlier, later, expected):
        assert d._period_delta_days(earlier, later) == expected

    @pytest.mark.parametrize("earlier,later", [
        (None, "2026-06-30"),
        ("2025-12-31", None),
        ("", ""),
        ("FY2026", "2026-06-30"),
        ("not-a-date", "also-not"),
        ("2025-13-45", "2026-06-30"),          # month 13, day 45
        (20251345, "2026-06-30"),               # same, as an int
        (2025.1231, "2026-06-30"),              # a float is not a date
        ("2025-1-1", "2026-06-30"),             # unpadded month/day
    ], ids=["none-annual", "none-quarter", "both-empty", "fiscal-year-label",
            "garbage", "impossible-month", "impossible-month-int", "float",
            "unpadded"])
    def test_an_unusable_period_yields_none_rather_than_raising(self, earlier, later):
        """A telemetry path must not be able to break a valuation: every one of
        these returns None instead of raising, and the caller then applies the
        STRICTER threshold rather than the wider one."""
        assert d._period_delta_days(earlier, later) is None

    def test_a_compact_yyyymmdd_period_parses_rather_than_falling_back(
            self):
        """Measured, not assumed -- and it contradicts what I first wrote here.

        Since Python 3.11 `date.fromisoformat` accepts the basic `YYYYMMDD`
        form, so an int or compact-string period yields a real gap (181) rather
        than None. The earlier version of this module asserted None for
        `20251231` with a comment claiming `fromisoformat` rejects it; that was
        true on 3.10 and false on both interpreters this repo actually runs
        under -- the project venv is 3.11.4, measured, and the system one is
        newer still. Pinning the actual behaviour, because a test that asserts
        a stdlib's OLD strictness fails the day the interpreter is upgraded and
        reads like a regression in the engine.

        Parsing it is the better outcome anyway: a provider that emits compact
        dates gets the threshold its real gap deserves instead of silently
        being locked to the strict one."""
        assert d._period_delta_days(20251231, "2026-06-30") == 181
        assert d._period_delta_days("20251231", "20260630") == 181

    def test_a_timestamp_period_still_parses(self):
        """`[:10]` is what makes a provider that returns a full timestamp work
        rather than fall back to the strict threshold by accident."""
        assert d._period_delta_days("2025-12-31T00:00:00",
                                    "2026-06-30 00:00:00") == 181

    def test_the_mixed_format_hazard_is_in_the_staleness_guard_not_the_helper(
            self):
        """Pre-existing, recorded here because the compact-format test above is
        what surfaced it. The overlay's staleness guard compares periods
        lexicographically (`q_period <= row["period"] -> return None`), which is
        only a date ordering when both sides share one format. A compact annual
        against a dashed quarter of the SAME year sorts the wrong way:

            "2026-06-30" <= "20260101"  ->  True

        so a quarter genuinely 180 days newer is rejected and the overlay never
        runs. `_period_delta_days` handles both formats correctly; the guard
        above it does not. Not fixed here -- it needs an owner decision on
        whether to normalise provider periods at intake -- but it must not be
        rediscovered as a step-change bug, because the symptom is the ABSENCE of
        a flag rather than a wrong one."""
        assert ("2026-06-30" <= "20260101") is True
        assert d._period_delta_days("20260101", "2026-06-30") == 180

    @pytest.mark.parametrize("annual,quarter,ticker,expected", [
        (ALIBABA_ANNUAL, ALIBABA_QUARTER, "09988.HK", 0.64),
        (ANTA_ANNUAL, ANTA_QUARTER, "02020.HK", 0.9821),
    ], ids=["09988.HK-0.64", "02020.HK-0.9821"])
    def test_the_metric_is_the_owners_formula_on_measured_runs(
            self, monkeypatch, annual, quarter, ticker, expected):
        """`delta_ratio` recomputed from the two live cases, digit for digit.

        These are the two numbers the threshold was chosen against: both are
        structural, both clear 0.25 comfortably, and neither is a working-capital
        wobble. If the formula drifts -- the denominator becoming `max` of BOTH
        operands, say -- 0.9821 becomes 0.55 and the test says so.
        """
        self._quarter(monkeypatch, **quarter)
        row = dict(annual)
        d._refresh_balance_sheet_from_latest_quarter(ticker, row, "2026-09-16")
        rec = row["_balance_sheet_step_change"]
        assert rec["delta_ratio"] == pytest.approx(expected, abs=1e-4)

    def test_the_record_carries_the_owners_keys_verbatim(self, monkeypatch):
        self._quarter(monkeypatch, **self.ALIBABA_QUARTER)
        row = dict(self.ALIBABA_ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter("09988.HK", row, "2026-09-16")
        rec = row["_balance_sheet_step_change"]
        assert rec["flag"] == "BALANCE_SHEET_QUARTERLY_STEP_CHANGE"
        assert rec["source"] == "quarterly_overlay_refresh"
        assert rec["action"] == "applied_quarterly_override"
        # The six the owner specified (2026-09-18). THREE additions are mine
        # and are named as mine rather than smuggled in:
        #   * `currency_basis` -- the figures are pre-FX and a reader comparing
        #     them to a reported-currency disclosure would otherwise be off by
        #     the FX rate with nothing on the payload to say so.
        #   * `threshold_used` and `period_delta_days` -- added with the
        #     2026-09-19 conditional threshold. Without the first, a published
        #     `delta_ratio` of 0.46 does not say whether it fired at 0.25 or
        #     was dropped at 0.50; the second is the input that chose it, so the
        #     choice is auditable from the record instead of from source.
        assert {"flag", "annual_net_cash", "quarterly_net_cash", "delta_ratio",
                "source", "action"} <= set(rec)
        assert set(rec) - {"flag", "annual_net_cash", "quarterly_net_cash",
                           "delta_ratio", "source", "action"} == {
            "currency_basis", "period_delta_days", "threshold_used"}
        # The owner's six keep their exact values; the conditional changed
        # which threshold is compared against, not the shape of the disclosure.
        assert rec["threshold_used"] in (
            d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD,
            d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD_WIDE)

    def test_the_two_measured_runs_select_different_thresholds(self, monkeypatch):
        """Real data, both sides of the boundary, in one test.

        09988.HK's year end is 31-Mar and the substituted quarter is 30-Jun --
        91 days, so the STRICT 0.25 applies and its 0.6401 fires with room to
        spare. 02020.HK's year end is 31-Dec against the same 30-Jun quarter
        -- 181 days, one past the boundary, so the WIDE 0.50 applies and its
        0.9821 fires against that instead. Same overlay, same quarter, two
        different thresholds, decided by nothing but the calendar."""
        self._quarter(monkeypatch, **self.ALIBABA_QUARTER)
        row = dict(self.ALIBABA_ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter("09988.HK", row, "2026-09-16")
        a = row["_balance_sheet_step_change"]
        assert (a["period_delta_days"], a["threshold_used"]) == (91, 0.25)

        self._quarter(monkeypatch, **self.ANTA_QUARTER)
        row = dict(self.ANTA_ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter("02020.HK", row, "2026-09-16")
        b = row["_balance_sheet_step_change"]
        assert (b["period_delta_days"], b["threshold_used"]) == (181, 0.50)
        # And both fired, which is the point: the wide threshold silenced a
        # 0.46, not a 0.98.
        assert a["delta_ratio"] > a["threshold_used"]
        assert b["delta_ratio"] > b["threshold_used"]

    def test_the_ratio_that_motivated_the_ruling_is_the_one_that_drops_out(
            self, monkeypatch):
        """MELI, measured two independent ways that agree: net cash -5.093bn at
        the 2025-12-31 year end against -7.446bn at 2026-06-30 -- the position
        got WORSE by 2.353bn, not better -- a ratio of 0.4620 over a 181-day
        gap. It is the only one of the eight golden fixtures the overlay applied
        on that sits between the two thresholds, so it is the only one the
        ruling silences; the other seven run 0.6202 to 4.9521 and clear 0.50.

        The two ways: `scratchpad/probe_stepchange_golden.py` reconstructs the
        ratio by calling `_net_debt_net_of_investments` itself on the row before
        and after the field copy, and the engine independently formatted
        "-5.1bn ... -> -7.4bn ... (+46%)" into MELI's `forward_flags` in the
        golden snapshot taken before the ruling. The reconstruction is the only
        source for a QUIET overlay's ratio -- the engine writes no record when
        the flag does not fire -- so this agreement is what makes "MELI dropped
        out at 0.4620" a measurement rather than an inference.

        Pinned as a number rather than described only in a changelog, because
        "one dropout" is exactly the kind of claim that goes stale the next time
        a fixture is re-captured.

        (The first version of this test used -2.729bn, derived by multiplying
        the ratio by the annual figure and SUBTRACTING. The direction was
        invented: the real move is an increase in net debt. The ratio happened
        to land at 0.4641 instead of 0.4620, which is the tell -- a number
        derived from a ratio cannot disagree with it, but a number read from
        the capture can and did. Read the capture.)"""
        annual = {"period": "2025-12-31", "revenue": 20e9,
                  "cash_and_equivalents": 1.0e9, "short_term_investments": None,
                  "total_debt": 6.093e9, "net_debt": 5.093e9}
        # quarterly net cash -7.446bn:
        #   |(-7.446) - (-5.093)| / 5.093 = 2.353 / 5.093 = 0.46200
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=1.0e9, short_term_investments=None,
                      total_debt=8.446e9, net_debt=7.446e9)
        row = dict(annual)
        flag = d._refresh_balance_sheet_from_latest_quarter(
            "MELI", row, "2026-09-16")
        # Assert the overlay APPLIED, or the absence of the record proves
        # nothing: a row rejected by the staleness guard is equally quiet, and
        # this test would then pass against the guard instead of the threshold.
        assert flag is not None
        assert row["_balance_sheet_period"] == "2026-06-30"
        assert "_balance_sheet_step_change" not in row, (
            "MELI's 0.4620 over a 181-day gap must NOT fire at the wide 0.50 -- "
            "this is the single dropout the 2026-09-19 ruling was made for")
        # And it WOULD have fired at the old flat 0.25, so the test is not
        # passing because the ratio is small in absolute terms. Computed from
        # the fixture's own numbers, not restated as a literal.
        a_nc = -5.093e9
        q_nc = -7.446e9
        ratio = abs(q_nc - a_nc) / max(abs(a_nc), 1.0)
        assert ratio == pytest.approx(0.4620, abs=5e-5), ratio
        assert 0.25 < ratio < 0.50, ratio

    def test_the_recorded_values_are_net_cash_not_net_debt(self, monkeypatch):
        """Signs. The helper returns net DEBT; the owner's keys say net CASH.
        For 09988.HK the annual position is a net CASH of +98.6bn, and a payload
        reading -98.6e9 would be read as leverage by anyone scanning it."""
        self._quarter(monkeypatch, **self.ALIBABA_QUARTER)
        row = dict(self.ALIBABA_ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter("09988.HK", row, "2026-09-16")
        rec = row["_balance_sheet_step_change"]
        assert rec["annual_net_cash"] == pytest.approx(98.6e9, rel=1e-3)
        assert rec["quarterly_net_cash"] == pytest.approx(161.7e9, rel=1e-3)
        assert rec["annual_net_cash"] > 0 and rec["quarterly_net_cash"] > 0

    def test_delta_ratio_is_rounded_to_four_places(self, monkeypatch):
        self._quarter(monkeypatch, **self.ALIBABA_QUARTER)
        row = dict(self.ALIBABA_ANNUAL)
        d._refresh_balance_sheet_from_latest_quarter("09988.HK", row, "2026-09-16")
        r = row["_balance_sheet_step_change"]["delta_ratio"]
        assert r == round(r, 4)
        assert isinstance(r, float)

    def test_a_move_below_the_threshold_records_nothing(self, monkeypatch):
        """The point of raising 15% to 25%: ordinary quarterly drift stays out
        of the audit payload, or the flag becomes noise nobody reads."""
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=176.0e9, short_term_investments=184.7e9,
                      total_debt=259.1e9, net_debt=83.1e9)
        row = dict(self.ALIBABA_ANNUAL)      # annual net cash 98.6bn
        flag = d._refresh_balance_sheet_from_latest_quarter(
            "09988.HK", row, "2026-09-16")
        assert flag is not None                              # overlay applied
        assert row["_balance_sheet_period"] == "2026-06-30"
        # quarterly net cash 101.6bn -> 3.0/98.6 = 0.0304, well under 0.25
        assert "_balance_sheet_step_change" not in row

    @pytest.mark.parametrize("q_period,q_net_cash,fires,threshold", [
        # ── the strict band: a 90-day year-end-to-Q1 gap ──────────────────
        ("2026-03-31", 125.0e9, False, None),   # 25.0/100.0 == 0.25 exactly
        ("2026-03-31", 125.01e9, True, 0.25),   # one ten-thousandth past it
        # ── the wide band: a 273-day gap, the shape every US fixture has ──
        ("2026-09-30", 150.0e9, False, None),   # 50.0/100.0 == 0.50 exactly
        ("2026-09-30", 150.01e9, True, 0.50),   # one ten-thousandth past it
        # The case that proves the conditional does something: 0.2501 clears
        # the strict threshold and would have fired before the ruling, but not
        # the wide one. This is MELI's shape, at round numbers.
        ("2026-09-30", 125.01e9, False, None),
        # ── the gap boundary itself, one day apart, opposite outcomes ──────
        # 180 days is NOT "> 180", so the strict band still applies and 0.2501
        # fires; at 181 days the same ratio does not. Nothing else in this
        # module pins the operator on the DAY comparison rather than the ratio.
        ("2026-06-29", 125.01e9, True, 0.25),   # 2025-12-31 -> 180 days
        ("2026-06-30", 125.01e9, False, None),  # 2025-12-31 -> 181 days
    ], ids=["strict-exactly-0.25-does-not-fire", "strict-just-past-0.25-fires",
            "wide-exactly-0.50-does-not-fire", "wide-just-past-0.50-fires",
            "wide-0.2501-clears-strict-but-not-wide",
            "gap-180-days-stays-strict", "gap-181-days-goes-wide"])
    def test_the_comparison_is_strictly_greater_than(
            self, monkeypatch, q_period, q_net_cash, fires, threshold):
        """`>` not `>=`, on BOTH the ratio and the day count. Round numbers on
        purpose: an exact boundary built out of 98.6bn-scale figures lands at
        0.25000000000000006 in binary float and the test would then be
        asserting on representation error rather than on the operator. At
        100.0e9 annual net cash, 125.0e9 and 150.0e9 are both exactly
        representable, so 0.25 and 0.50 are exact too."""
        annual = {"period": "2025-12-31", "cash_and_equivalents": 100.0e9,
                  "short_term_investments": None, "total_debt": 200.0e9,
                  "net_debt": -100.0e9}            # annual net cash exactly 100bn
        self._quarter(monkeypatch, report_period=q_period,
                      cash_and_equivalents=q_net_cash, short_term_investments=None,
                      total_debt=0.0, net_debt=-q_net_cash)
        row = dict(annual)
        flag = d._refresh_balance_sheet_from_latest_quarter("X", row, "2026-09-16")
        assert flag is not None                     # overlay applied either way
        assert ("_balance_sheet_step_change" in row) is fires
        if fires:
            rec = row["_balance_sheet_step_change"]
            assert rec["delta_ratio"] > rec["threshold_used"]
            assert rec["threshold_used"] == threshold
            assert rec["period_delta_days"] == (
                d._period_delta_days("2025-12-31", q_period))
        else:
            # The overlay STILL applied. A threshold that never fired would be
            # indistinguishable from one that quietly started gating, and the
            # whole design rests on the difference.
            assert row["_balance_sheet_period"] == q_period

    def test_an_unparseable_period_falls_back_to_the_stricter_threshold(
            self, monkeypatch):
        """A provider that ever returns a fiscal label instead of an ISO date
        must not silently widen the band. The record still fires at 0.25 and
        says plainly that it could not measure the gap -- for a disclosure-only
        flag, over-reporting is the safe error.

        The label is `"2025-Q4"`, not `"FY2025"`, and that is load-bearing: the
        overlay's staleness guard is a lexicographic compare, and
        `"2026-09-30" <= "FY2025"` is True ('F' sorts after every digit), so an
        `FY`-prefixed annual never reaches the helper at all. A label starting
        with a digit does. Asserting `flag is not None` is what stops this test
        quietly becoming a no-op against the guard instead of the threshold."""
        annual = {"period": "2025-Q4", "cash_and_equivalents": 100.0e9,
                  "short_term_investments": None, "total_debt": 200.0e9,
                  "net_debt": -100.0e9}
        self._quarter(monkeypatch, report_period="2026-09-30",
                      cash_and_equivalents=125.01e9, short_term_investments=None,
                      total_debt=0.0, net_debt=-125.01e9)
        row = dict(annual)
        flag = d._refresh_balance_sheet_from_latest_quarter("X", row, "2026-09-16")
        assert flag is not None, (
            "the overlay must have applied, or this asserts nothing about the "
            "threshold -- see the docstring on why the label is '2025-Q4'")
        rec = row["_balance_sheet_step_change"]
        assert rec["period_delta_days"] is None
        assert rec["threshold_used"] == d._BALANCE_SHEET_STEP_CHANGE_THRESHOLD
        # And it fired at 0.2501, which is the point: the SAME ratio over a
        # readable 273-day gap would have taken the wide band and stayed quiet.
        assert rec["delta_ratio"] == pytest.approx(0.2501, abs=1e-4)

    def test_an_unreported_annual_base_fires_rather_than_dividing_by_zero(
            self, monkeypatch):
        """`max(abs(a_net_cash), 1.0)` is the owner's floor and it only binds
        here. A row with no `net_debt` at all makes the helper return 0.0, so
        the denominator becomes 1.0 and the ratio is the absolute quarterly
        figure -- which clears BOTH thresholds (0.25 and the wide 0.50) on any
        real balance sheet, so the conditional changed nothing for this case.
        That is correct, not a bug: moving off an UNREPORTED base is a step
        change, and `annual_net_cash: 0.0` on the payload says so plainly."""
        annual = {"period": "2025-12-31", "revenue": 1e9,
                  "cash_and_equivalents": None, "short_term_investments": None,
                  "total_debt": None, "net_debt": None}
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=5.0e9, short_term_investments=None,
                      total_debt=1.0e9, net_debt=-4.0e9)
        d._refresh_balance_sheet_from_latest_quarter("X", annual, "2026-09-16")
        rec = annual["_balance_sheet_step_change"]
        assert rec["annual_net_cash"] == 0.0
        assert rec["delta_ratio"] == pytest.approx(4.0e9)
        # 181 days -> the wide band, and it still fires by nine orders of
        # magnitude. Pinned so that "the floor case is threshold-independent"
        # stays a measured claim rather than a docstring one.
        assert (rec["period_delta_days"], rec["threshold_used"]) == (181, 0.50)
        assert rec["delta_ratio"] > rec["threshold_used"]

    def test_negative_zero_on_the_annual_base_is_normalised(self, monkeypatch):
        """`-float(0.0)` is `-0.0`, which serializes to JSON as `-0.0` and reads
        as a negative position. A net cash of zero must print as zero."""
        annual = {"period": "2025-12-31", "cash_and_equivalents": 10.0e9,
                  "short_term_investments": None, "total_debt": 10.0e9,
                  "net_debt": 0.0}
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=15.0e9, short_term_investments=None,
                      total_debt=10.0e9, net_debt=-5.0e9)
        d._refresh_balance_sheet_from_latest_quarter("X", annual, "2026-09-16")
        rec = annual.get("_balance_sheet_step_change")
        assert rec is None or str(rec["annual_net_cash"]) != "-0.0"

    def test_the_overlay_is_applied_when_the_flag_fires(self, monkeypatch):
        """THE load-bearing assertion. `applied: False` means the flag records a
        move it did not prevent: the row is on the quarterly figures either way.
        A future edit that turns this into a gate -- aborting the overlay, or
        reverting to the annual row -- fails here and nowhere else."""
        self._quarter(monkeypatch, **self.ALIBABA_QUARTER)
        row = dict(self.ALIBABA_ANNUAL)
        flag = d._refresh_balance_sheet_from_latest_quarter(
            "09988.HK", row, "2026-09-16")
        assert flag is not None
        assert row["_balance_sheet_period"] == "2026-06-30"
        assert row["cash_and_equivalents"] == 185.5e9
        assert row["total_debt"] == 266.5e9
        assert d._net_debt_net_of_investments(row, "Tech") == pytest.approx(-161.7e9)
        assert "_balance_sheet_step_change" in row          # ...and it still fired

    def test_the_returned_prose_is_unchanged_by_the_flag(self, monkeypatch):
        """The flag string is seeded into `ticker_forward_flags` verbatim and is
        pinned by shape in the 0916 module. The step-change record travels
        separately; bolting a percentage onto this string would change prose
        that other tests and the PDF report read."""
        self._quarter(monkeypatch, **self.ALIBABA_QUARTER)
        row = dict(self.ALIBABA_ANNUAL)
        flag = d._refresh_balance_sheet_from_latest_quarter(
            "09988.HK", row, "2026-09-16")
        assert "STEP_CHANGE" not in flag
        assert "step change" not in flag
        assert flag.startswith("Balance sheet from 2026-06-30")

    def test_a_stale_quarter_records_nothing_at_all(self, monkeypatch):
        """No overlay, no record. The early returns must not leave a key behind
        for the caller to pop."""
        self._quarter(monkeypatch, report_period="2025-12-31",
                      cash_and_equivalents=1e9, total_debt=2e9, net_debt=1e9)
        row = dict(self.ALIBABA_ANNUAL)
        assert d._refresh_balance_sheet_from_latest_quarter(
            "09988.HK", row, "2026-09-16") is None
        assert "_balance_sheet_step_change" not in row
        assert row == self.ALIBABA_ANNUAL

    def test_the_sector_guard_applies_to_both_operands(self, monkeypatch):
        """Healthcare: short-term investments back medical claims and are not
        netted on EITHER side, so the ratio measures the same convention twice.
        Molina's own figures -- 3.9bn of investments that must not be read as
        spare cash on the quarter any more than on the year end."""
        annual = {"period": "2026-03-31", "cash_and_equivalents": 4.0e9,
                  "short_term_investments": 3.0e9, "total_debt": 3.0e9,
                  "net_debt": -1.0e9}
        self._quarter(monkeypatch, report_period="2026-06-30",
                      cash_and_equivalents=5.0e9, short_term_investments=3.9e9,
                      total_debt=4.0e9, net_debt=-1.0e9)
        row = dict(annual)
        d._refresh_balance_sheet_from_latest_quarter(
            "MOH", row, "2026-09-16", None, "Healthcare")
        rec = row.get("_balance_sheet_step_change")
        # net cash 1.0bn -> 1.0bn, investments ignored both times
        assert rec is None


class TestTheRecordIsLiftedIntoTheAuditPayload:
    """The flag is computed ~340 lines before `gate_evaluations` exists, so it
    is stashed on the row and lifted. Both halves of that hand-off are pinned,
    because a stash nobody lifts is a record nobody sees."""

    @pytest.fixture(scope="class")
    def src(self):
        return inspect.getsource(d)

    @pytest.fixture(scope="class")
    def record_span(self, src):
        """(start, end) of the lifted gate record, sliced on REAL boundaries.

        Three tests here used to slice `src[i:i + 3_000]` from the `gate_id` line
        -- a magic character count. That is brittle in the direction that hurts:
        `applied` sits at the END of the record, so an explanatory comment added
        anywhere above it pushes the assertion's target out of the window and a
        passing test starts failing for a reason unrelated to what it checks. It
        fired on exactly that when `currency_basis` was plumbed in. Bounding on
        the closing `})` cannot be broken by prose, and it is also STRICTER --
        the window no longer spills into neighbouring code that could satisfy an
        `in` check by accident.
        """
        i = src.index('"gate_id": "GATE_BALANCE_SHEET_QUARTERLY_STEP_CHANGE"')
        start = src.rindex("gate_evaluations.append({", 0, i)
        return start, src.index("\n            })", i)

    @pytest.fixture(scope="class")
    def lift_block(self, src, record_span):
        start, end = record_span
        return src[start:end]

    @pytest.fixture(scope="class")
    def prose_block(self, src, record_span):
        """The forward-flag statement that follows the record.

        Anchored AFTER the record's close, not at its `gate_id`, so growth inside
        the record cannot consume this window. That decoupling is the whole point:
        the version anchored at `gate_id` with a 4,000-char window was measured at
        447 chars of headroom after one comment landed -- one more comment and
        this assertion goes blind, failing with a message about missing prose
        rather than about the window that stopped reaching it.
        """
        _, end = record_span
        return src[end:end + 2_000]

    def test_the_caller_pops_the_key_off_the_row(self, src):
        """`.pop`, not `.get`: `most_recent` IS `series[-1]`, it is serialized
        into the published payload downstream, and an internal hand-off key has
        no business in a run anyone reads."""
        assert 'most_recent.pop("_balance_sheet_step_change"' in src
        assert 'most_recent.get("_balance_sheet_step_change"' not in src

    def test_the_lift_happens_after_the_list_is_created(self, src):
        assert src.index("gate_evaluations: list[dict] = []") < \
            src.index('most_recent.pop("_balance_sheet_step_change"')

    def test_the_lifted_record_is_disclosure_only(self, lift_block):
        assert '"applied": False,' in lift_block
        assert '"applied": True,' not in lift_block

    def test_the_lifted_record_carries_both_halves_of_its_path_pair(self,
                                                                    lift_block):
        """MANDATORY, not stylistic. `tests/test_balance_sheet_financial_gate.py`
        and `tests/test_gate_backtest.py` both assert
        `src.count('"raw_input_path_a"') == src.count('"gated_output_path_b"')`
        over this whole module, so a gate record missing either half fails two
        unrelated modules with a message that does not name this one."""
        assert lift_block.count('"raw_input_path_a"') == 1
        assert lift_block.count('"gated_output_path_b"') == 1
        assert ('"gate_id"' in lift_block and '"metric"' in lift_block
                and '"basis"' in lift_block)

    def test_every_stashed_key_is_lifted(self, src, lift_block):
        """The invariant that was missing while `currency_basis` was dropped.

        The producer test asserts an EXACT set equality on the stashed record;
        this class asserted the presence of five DIFFERENT keys on the lifted
        one. Both looked exact, and between them the hand-off was uncovered.
        Shipped in `2f386f0` and found only by reading a production payload:
        `currency_basis` was written at the stash, never copied into the lift,
        and destroyed by the `.pop` above -- so the pre-FX disclosure the key
        exists for reached no reader, on exactly the names where it matters
        (02020.HK reports CNY against an HKD price; a reader comparing its
        published 11.07bn net cash to an HKD disclosure is off by the FX rate).

        Parsed from source on BOTH sides rather than enumerated by name, because
        an enumerated list is what failed the first time: it named the keys the
        author had just written and so could not notice the one left behind.
        """
        key_re = re.compile(r'^\s+"([a-z_0-9]+)":', re.M)

        j = src.index('row["_balance_sheet_step_change"] = {')
        stashed = set(key_re.findall(src[j:src.index("\n        }", j)]))
        lifted = set(key_re.findall(lift_block))

        # Both guards are load-bearing. Without them a regex that matches
        # nothing yields `set() - set() == set()` and this test passes while
        # asserting nothing at all -- the vacuous-pass failure mode, and the
        # reason a detector is sanity-checked against a positive it can see.
        assert stashed, "the stash block parsed to zero keys; the regex is wrong"
        assert lifted, "the lift block parsed to zero keys; the regex is wrong"
        assert stashed - lifted == set(), (
            "stashed but never lifted, so the `.pop` destroys them and they "
            f"reach no payload: {sorted(stashed - lifted)}")

    def test_the_module_wide_path_pair_invariant_still_holds(self, src):
        assert src.count('"raw_input_path_a"') == src.count('"gated_output_path_b"')

    def test_the_prose_half_reaches_the_forward_flags(self, prose_block):
        """`ticker_forward_flags` seeds every scenario's `forward_flags`, which
        IS emitted on the payload -- that is how the 02020.HK production run's
        balance-sheet prose got out. The structured record is for scoring; the
        prose is what a human reading the run sees."""
        assert "Balance-sheet step change: net cash" in prose_block
        assert "quarterly override APPLIED" in prose_block

    def test_the_prose_window_has_headroom(self, src, record_span):
        """How much can be added between the record's close and the forward-flag
        prose before `prose_block` stops reaching it. Measured, not assumed: the
        `applied` test failed at a 3,000-char window for exactly this reason, the
        prose window was then measured at 447 chars of slack, and nothing in the
        suite said how close either was. Anchoring past the record's close is
        what makes this number stable under record growth; this test is what says
        so when it stops being."""
        _, end = record_span
        k = src.index("Balance-sheet step change: net cash", end)
        headroom = 2_000 - (k - end)
        assert headroom > 1_000, (
            f"only {headroom} chars of headroom between the record's close and "
            f"the prose; widen `prose_block`")

    def test_gate_backtest_does_not_score_the_new_metric(self):
        """It keys off `gate_id` literals, so a new gate is inert there. Pinned
        because "inert" is an assumption about another module's dispatch, and if
        that module ever starts iterating `gate_evaluations` generically this
        record -- `applied: False`, with net-cash values in the path fields --
        would be scored as if it were a forecast."""
        from src.memory import gate_backtest as gb
        gsrc = inspect.getsource(gb)
        assert "BALANCE_SHEET_QUARTERLY_STEP_CHANGE" not in gsrc
        assert "gate_evaluations" not in gsrc


# ─────────────────────────────────────────────────────────────────────────────
# HK: the four-label short-term-investment rule
# ─────────────────────────────────────────────────────────────────────────────
class TestTheHkRuleSumsFourLabelsAndNothingElse:
    """Validated against live AKShare payloads on four names at every period the
    provider returns, and scored against FMP at the same periods."""

    def test_the_parts_constant_is_derived_from_the_map_not_typed(self):
        """The map is the single source of truth. If a fifth label is ever added,
        the sum picks it up without anyone remembering to edit a tuple -- and if
        someone hand-edits the tuple instead, this fails."""
        assert hk._SHORT_TERM_INVESTMENT_PARTS == tuple(sorted(
            v for v in HK_BALANCE_COLS.values()
            if v.startswith("short_term_investments_")))
        assert len(hk._SHORT_TERM_INVESTMENT_PARTS) == 4
        assert len(set(hk._SHORT_TERM_INVESTMENT_PARTS)) == 4

    def test_the_four_labels_map_to_four_distinct_staging_keys(self):
        """`_parse_statement` is first-wins PER FIELD. Mapping all four labels to
        one staging key silently keeps whichever row AKShare happens to return
        first and drops the other three -- the defect this indirection exists to
        avoid."""
        labels = [k for k, v in HK_BALANCE_COLS.items()
                  if v.startswith("short_term_investments_")]
        assert len(labels) == 4
        assert len({HK_BALANCE_COLS[l] for l in labels}) == 4

    def test_the_parentheses_are_half_width(self):
        """THE trap. Two of the four labels carry `(流动)`. AKShare publishes
        half-width U+0028/U+0029; full-width U+FF08/U+FF09 look identical in a
        terminal and match nothing. A rule typed from a rendered document rather
        than a live payload fails here."""
        for label in HK_BALANCE_COLS:
            assert "（" not in label and "）" not in label, label

    def test_the_engine_requests_the_field_from_the_provider(self):
        """Without this the field is never in `line_items`, the map is never
        consulted, and the sum below never runs -- while every unit test that
        calls `_compute_derived` directly still passes."""
        assert "short_term_investments" in hk._BALANCE_FIELDS

    @pytest.mark.parametrize("staging,expected", [
        # Measured at each name's newest period, CNY bn, from live AKShare rows.
        ({"short_term_investments_deposits": 24.275e9,
          "short_term_investments_securities": 1.920e9}, 26.195e9),      # 02020.HK
        ({"short_term_investments_securities": 185.364e9}, 185.364e9),    # 09988.HK
        ({"short_term_investments_deposits": 236.801e9,
          "short_term_investments_fv_current": 44.710e9,
          "short_term_investments_other_current": 4.201e9}, 285.712e9),   # 00700.HK
        ({"short_term_investments_deposits": 51.309e9,
          "short_term_investments_fv_current": 29.274e9,
          "short_term_investments_securities": 0.200e9}, 80.783e9),      # 01810.HK
    ], ids=["02020.HK", "09988.HK", "00700.HK", "01810.HK"])
    def test_the_sum_reproduces_the_measured_totals(self, staging, expected):
        s = dict(staging)
        hk._compute_derived(s)
        assert s["short_term_investments"] == pytest.approx(expected, rel=1e-4)

    def test_a_name_reporting_none_of_the_four_yields_no_field_not_zero(
            self):
        """All-absent must yield NO key. `_net_debt_net_of_investments` treats a
        falsy STI as "this feed has nothing to net" and leaves net debt alone; a
        0.0 would read as "netted, and there was nothing there", which is a
        different claim and would silently change the guard's behaviour."""
        s = {"total_debt": 5.0e9, "cash_and_equivalents": 2.0e9}
        hk._compute_derived(s)
        assert "short_term_investments" not in s

    def test_a_single_label_still_produces_the_field(self):
        s = {"short_term_investments_securities": 7.0e9}
        hk._compute_derived(s)
        assert s["short_term_investments"] == pytest.approx(7.0e9)

    @pytest.mark.parametrize("excluded", [
        "交易性金融资产(流动)",           # Tencent only; a trading book, not spare cash
        "其他金融资产(非流动)",           # non-current
        "指定以公允价值记账之金融资产",    # the non-current twin of the mapped label
        "中长期存款",                     # > 3 months, per the plan's own definition
    ])
    def test_the_labels_that_were_measured_and_left_out_stay_out(self, excluded):
        """Each of these was seen in a live payload and deliberately excluded.
        Pinned so a future "why is this missing?" pass does not quietly add one
        back and re-break the totals above."""
        assert excluded not in HK_BALANCE_COLS

    def test_net_debt_is_total_debt_minus_cash_so_the_sector_guard_survives(self):
        """DELIBERATELY not `total_debt - cash - STI`, whatever the plan text
        said. Pre-netting here trips the guard in `_net_debt_net_of_investments`
        and bypasses the sector exclusion that keeps a bank's or insurer's
        investment portfolio from being counted as spare cash. The provider does
        not know the sector, so it must not make the sector-dependent call."""
        s = {"short_term_investments_securities": 24.275e9,
             "short_term_debt": 11.532e9, "long_term_debt": 11.770e9,
             "cash_and_equivalents": 12.181e9}
        hk._compute_derived(s)
        assert s["total_debt"] == pytest.approx(23.302e9)
        assert s["net_debt"] == pytest.approx(23.302e9 - 12.181e9)
        # ...and the one place that DOES know the sector nets it:
        assert d._net_debt_net_of_investments(s, "Consumer") == pytest.approx(
            s["net_debt"] - 24.275e9, rel=1e-4)
        assert d._net_debt_net_of_investments(s, "Financials") == pytest.approx(
            s["net_debt"])


class TestTheHkRuleIsScoredAtEveryPeriod:
    """The deviations this rule ships with, stated as numbers rather than as a
    comment claiming accuracy it does not have.

    The earlier draft of the mapping comment quoted "worst deviation +0.34%",
    which is true ONLY at each name's newest period -- the one period the rule
    had been checked against when it was written. Re-scored across every period
    the provider returns, three names diverge further back. The owner accepted
    these on 2026-09-18: tuning labels to fit 2021-2024 historical filings
    would regress the broader HK universe. Accepted is not the same as absent,
    so the envelope is pinned here and the comment was corrected to match."""

    #: (name, period, rule sum, FMP at the same period, deviation) in CNY bn.
    #: A `None` rule sum means the probe printed the deviation but not both
    #: operands, so the operand was never transcribed -- it does NOT mean the
    #: period was unscoreable, and the deviation column is the measured one in
    #: every row. Reconstructing the missing sums as `FMP x (1 + dev)` would put
    #: derived figures in a column labelled measured.
    MEASURED = [
        ("02020.HK", "2025-12-31", 26.1950, 26.2062, -0.0428),
        ("02020.HK", "2024-12-31", 22.9060, 22.9060, 0.0),
        ("02020.HK", "2023-12-31", 22.7810, 22.7810, 0.0),
        ("02020.HK", "2022-12-31", 10.9230, 10.9230, 0.0),
        ("02020.HK", "2021-12-31", 7.7480, 7.7480, 0.0),
        ("09988.HK", "2026-03-31", 185.3640, 184.7444, +0.3354),
        ("09988.HK", "2025-03-31", 282.6060, 282.6060, 0.0),
        ("09988.HK", "2024-03-31", 322.9040, 322.9040, 0.0),
        ("09988.HK", "2023-03-31", 331.3840, 331.3840, 0.0),
        ("00700.HK", "2025-12-31", 285.7120, 285.8342, -0.0428),
        ("00700.HK", "2024-12-31", None, 205.7887, +2.3574),
        ("00700.HK", "2023-12-31", None, 200.2912, +3.2672),
        ("00700.HK", "2022-12-31", None, 130.8639, +2.4094),
        ("00700.HK", "2021-12-31", None, 94.2158, +2.0370),
        ("01810.HK", "2025-12-31", 80.7822, 80.8168, -0.0428),
        ("01810.HK", "2024-12-31", 66.8553, 66.8224, +0.0492),
        ("01810.HK", "2023-12-31", 74.0765, 73.8571, +0.2970),
        ("01810.HK", "2022-12-31", None, 39.6023, +1.4328),
        ("01810.HK", "2021-12-31", 62.6618, 62.5488, +0.1806),
    ]

    def test_the_newest_period_is_within_half_a_percent_on_every_name(self):
        worst = max(abs(r[4]) for r in self.MEASURED if r[1] >= "2025")
        assert worst < 0.34, worst

    def test_the_all_period_envelope_is_the_one_the_owner_accepted(self):
        """+3.27% is the true worst case, on Tencent's 2023 filing. Any test or
        comment claiming better than this is describing a subset."""
        worst = max(abs(r[4]) for r in self.MEASURED)
        assert worst == pytest.approx(3.2672, abs=1e-3)
        assert all(r[4] < 3.30 for r in self.MEASURED)

    def test_every_divergence_is_in_one_direction(self):
        """The rule sum sits ABOVE FMP on all five diverging periods and below on
        the three newest. No single excluded label explains the historical gap
        (an offline gap analysis over every unmapped label on Tencent's balance
        sheet found none), which is why this was accepted rather than fixed."""
        diverging = [r for r in self.MEASURED if abs(r[4]) > 0.5]
        assert len(diverging) == 5
        assert all(r[4] > 0 for r in diverging)
        assert {r[0] for r in diverging} == {"00700.HK", "01810.HK"}

    def test_the_comment_in_the_map_does_not_overclaim(self):
        """The defect this class exists to prevent recurring: a source comment
        quoting the accuracy of the one period its author looked at. The
        corrected comment must name the all-period envelope."""
        src = inspect.getsource(hk).replace("\r", "")
        from src.tools.hk import mappings
        msrc = inspect.getsource(mappings).replace("\r", "")
        assert "3.27" in msrc or "3.2672" in msrc, (
            "HK_BALANCE_COLS' comment still quotes an accuracy that holds only "
            "at the newest period")
        assert src  # the module imports; a syntax error would hide the above


# ─────────────────────────────────────────────────────────────────────────────
# SG: precedence and the coherence guard
# ─────────────────────────────────────────────────────────────────────────────
class TestTheSgPrecedenceIsTheCorrectedOne:
    """Direct label primary, `combined - cash` only as a fallback.

    This precedence is the OPPOSITE of the one first shipped, and measurement is
    why: the identity `combined == cash + Other Short Term Investments` holds
    exactly at the newest period of all six names that report the direct label --
    the only period the original rule probe looked at -- and BREAKS at a prior
    period on two of eight. Fitted-at-one-period is an anecdote."""

    def _bs(self, cells: dict) -> pd.DataFrame:
        return pd.DataFrame({"2024-12-31": cells})

    def _derive(self, cells: dict, row: dict | None = None,
                requested=("cash_and_equivalents", "short_term_investments",
                           "net_debt", "total_debt")) -> dict:
        out = dict(row or {})
        sg._derive_balance_fields(out, self._bs(cells), "2024-12-31", set(requested))
        return out

    def test_the_direct_label_wins_where_the_combined_line_carries_restricted_cash(
            self):
        """BN4.SI (Keppel) at 2024-12-31. The residual is EXACTLY Restricted
        Cash, 1.382624 to the last digit, so the combined line is
        cash + STI + restricted for this issuer/year. `combined - cash` would
        publish 1.533706 against FMP's 0.151082 -- a 10x overstatement of the
        field, which `_net_debt_net_of_investments` then subtracts from net debt
        a second time."""
        out = self._derive({
            sg._YF_CASH_AND_STI: 2.452615e9,
            sg._YF_CASH: 0.918909e9,
            sg._YF_OTHER_STI: 0.151082e9,
            "Restricted Cash": 1.382624e9,
        })
        assert out["short_term_investments"] == pytest.approx(0.151082e9)
        # and NOT the 10x figure the reversed precedence produced:
        assert out["short_term_investments"] != pytest.approx(1.533706e9)

    def test_the_coherence_guard_refuses_a_crossed_label_pair(self):
        """VC2.SI at 2024-12-31: cash 3.064681 + other 3.329674 = 6.394 against a
        combined line of 3.332245. A component cannot exceed the total it belongs
        to, so yfinance has the two labels crossed for this period alone -- FMP
        shows 3.329674 sitting in ITS cash field with STI = 0.0. Refusing the
        incoherent value hands the case to the fallback, which restores the
        right TOTAL liquidity (3.332245) even though the split does not."""
        out = self._derive({
            sg._YF_CASH_AND_STI: 3.332245e9,
            sg._YF_CASH: 3.064681e9,
            sg._YF_OTHER_STI: 3.329674e9,
        })
        assert out["short_term_investments"] == pytest.approx(0.267564e9)
        assert out["short_term_investments"] != pytest.approx(3.329674e9)

    def test_a_coherent_pair_passes_the_guard_untouched(self):
        """The guard must not swallow the normal case. S08.SI's components sum to
        well under its combined line, so the direct value stands."""
        out = self._derive({
            sg._YF_CASH_AND_STI: 0.696420e9 + 0.095465e9,
            sg._YF_CASH: 0.696420e9,
            sg._YF_OTHER_STI: 0.095465e9,
        })
        assert out["short_term_investments"] == pytest.approx(0.095465e9)

    @pytest.mark.parametrize("combined,cash", [
        (0.149489e9, 0.149489e9),   # C38U.SI 2025 -- no direct label at all
        (0.006313e9, 0.006313e9),   # P15.SI 2025
    ], ids=["C38U.SI", "P15.SI"])
    def test_a_name_without_the_direct_label_degrades_to_exactly_zero(
            self, combined, cash):
        """The fallback earns its place here: these two names lack
        `Other Short Term Investments` entirely and their combined line EQUALS
        cash, so `combined - cash` is 0.0 -- which is what FMP reports for both.
        A genuinely zero STI is not the same as an absent one, and downstream
        `_net_debt_net_of_investments` treats 0.0 as "nothing to net" anyway."""
        out = self._derive({sg._YF_CASH_AND_STI: combined, sg._YF_CASH: cash})
        assert out["short_term_investments"] == pytest.approx(0.0)

    def test_a_negative_derived_sti_is_refused_not_published(self):
        """`combined - cash < 0` means the two lines are not the pair they appear
        to be. Publishing it would ADD to net debt downstream, which is the
        opposite of what a liquidity field does."""
        out = self._derive({sg._YF_CASH_AND_STI: 1.0e9, sg._YF_CASH: 3.0e9})
        assert out.get("short_term_investments") is None

    def test_cash_is_aliased_to_the_field_name_the_engine_requests(self):
        """THE defect that made the quarterly overlay unreachable on every SG
        name: the map keys cash as `cash`, and
        `_refresh_balance_sheet_from_latest_quarter` returns early unless the
        quarter "carries both a cash and a debt figure". Aliased, not renamed, so
        a caller asking for `cash` still gets it."""
        out = self._derive({sg._YF_CASH: 0.918909e9},
                           row={"cash": 0.918909e9})
        assert out["cash_and_equivalents"] == pytest.approx(0.918909e9)
        assert out["cash"] == pytest.approx(0.918909e9)

    def test_a_reported_net_debt_is_never_overwritten(self):
        """BN4.SI reports 10.979096 where `total_debt - cash` is 11.153239: the
        reported figure is already net of short-term investments. Replacing
        reported data with derived data is a judgement call this function does
        not get to make."""
        out = self._derive({sg._YF_CASH: 0.918909e9},
                           row={"total_debt": 12.072148e9, "net_debt": 10.979096e9})
        assert out["net_debt"] == pytest.approx(10.979096e9)

    def test_net_debt_is_derived_where_yfinance_reports_nothing(self):
        """S08.SI 2025-03-31: yfinance returns NaN for `Net Debt`, which became
        None, which `_net_debt_net_of_investments` turns into 0.0 -- so a name in
        the look-through promote allowlist read as DEBT-FREE with S$0.363bn of
        debt against S$0.604bn of liquidity."""
        out = self._derive({sg._YF_CASH: 0.534353e9},
                           row={"total_debt": 0.362784e9})
        assert out["net_debt"] == pytest.approx(-0.171569e9)
        assert out["net_debt"] != 0.0

    def test_only_requested_fields_are_written(self):
        """Everything else on the row is copied verbatim into the LineItem by the
        caller, so an unrequested key changes the row's shape for every
        consumer."""
        out = self._derive({sg._YF_CASH: 1.0e9, sg._YF_OTHER_STI: 2.0e9},
                           requested=("revenue",))
        assert out == {}

    def test_a_value_the_field_loop_already_found_is_never_replaced(self):
        """`_derive_balance_fields` runs AFTER the map loop. If the map resolved
        a field, this must leave it alone -- otherwise the derived path silently
        outranks the provider's own mapping."""
        out = self._derive({sg._YF_OTHER_STI: 2.0e9, sg._YF_CASH: 1.0e9},
                           row={"short_term_investments": 5.5e9,
                                "cash_and_equivalents": 9.9e9})
        assert out["short_term_investments"] == pytest.approx(5.5e9)
        assert out["cash_and_equivalents"] == pytest.approx(9.9e9)

    def test_a_missing_balance_sheet_leaves_the_function_inert(self):
        """`_bs_value` returns None when `bs` is None or empty. The whole block
        must be inert rather than wrong in that case -- yfinance returns an empty
        frame for a delisted or newly listed name."""
        out: dict = {}
        sg._derive_balance_fields(out, None, "2024-12-31",
                                  {"cash_and_equivalents", "short_term_investments"})
        assert out == {}
        out2: dict = {}
        sg._derive_balance_fields(out2, pd.DataFrame(), "2024-12-31",
                                  {"cash_and_equivalents", "short_term_investments"})
        assert out2 == {}

    def test_the_labels_are_the_mangled_strings_yfinance_actually_publishes(self):
        """Copied verbatim from a live payload. yfinance strips the spaces out of
        multi-word labels -- `Investmentsin Associatesat Cost`, `Designatedas` --
        so tidying these into readable English silently matches nothing."""
        assert sg._YF_CASH_AND_STI == "Cash Cash Equivalents And Short Term Investments"
        assert sg._YF_CASH == "Cash And Cash Equivalents"
        assert sg._YF_OTHER_STI == "Other Short Term Investments"
        assert "CashAndCash" not in sg._YF_CASH_AND_STI

    def test_the_derivation_runs_before_the_row_is_returned(self):
        src = inspect.getsource(sg)
        assert src.index("_derive_balance_fields(row, bs, col") < \
            src.index("results.append(row)")
