"""HKEX annual-report segment parsing, and gross profit -> operating profit.

No test here touches the network or a PDF: the geometry helpers are pure, and
the allocation is exercised on constructed inputs.
"""
import pytest

from src.tools import hkex_segments as hk


class TestUnits:
    def test_typographic_apostrophe(self):
        """The apostrophe in "RMB’Million" is U+2019, not ASCII.

        Matching only the ASCII form left the units token glued onto every
        column name, which stopped "Total" being recognised and lost both the
        consolidated revenue and the reporting currency.
        """
        assert hk._units_and_ccy("RMB\u2019Million") == (1e6, "CNY")
        assert hk._units_and_ccy("RMB'Million") == (1e6, "CNY")
        assert hk._units_and_ccy("US$ million") == (1e6, "USD")
        assert hk._units_and_ccy("HK$\u2019000") == (1e3, "HKD")
        assert hk._units_and_ccy("S$ million") == (1e6, "SGD")

    def test_negatives_in_parentheses(self):
        assert hk._to_float("(2,051)") == -2051.0
        assert hk._to_float("1,234") == 1234.0
        assert hk._to_float("n/a") is None


class TestHeaderGeometry:
    def _cols(self):
        # three right-aligned numeric columns
        return [[(274.0, 306.0, 1.0), (330.0, 363.0, 2.0), (387.0, 420.0, 3.0)]]

    def test_centres_come_from_the_numbers(self):
        c = hk._column_centers(self._cols())
        assert c == pytest.approx([290.0, 346.5, 403.5])

    def test_nearest_centre_beats_range_containment(self):
        """Tencent's "FinTech" sits at x=389, two points inside column 1's
        range, but belongs to column 2. Containment yields
        "FinTech and Marketing Services"."""
        centers = hk._column_centers(self._cols())
        lines = [
            {"y": 243.0, "words": [(380.0, 398.0, "FinTech"),
                                   (405.0, 421.0, "and")]},
            {"y": 258.0, "words": [(335.0, 353.0, "Marketing")]},
            {"y": 273.0, "words": [(282.0, 298.0, "VAS"),
                                   (338.0, 356.0, "Services"),
                                   (395.0, 413.0, "Services")]},
        ]
        assert hk._header_names(lines, centers) == [
            "VAS", "Marketing Services", "FinTech and Services"]

    def test_units_row_never_becomes_part_of_a_name(self):
        centers = hk._column_centers(self._cols())
        lines = [{"y": 273.0, "words": [(282.0, 298.0, "VAS")]},
                 {"y": 288.0, "words": [(276.0, 304.0, "RMB\u2019Million")]}]
        assert hk._header_names(lines, centers)[0] == "VAS"


class TestOpexAllocation:
    """Tencent's filing says opex is managed centrally and NOT allocated.

    So the total is disclosed and only the split is assumed. The invariant
    that matters is that the derived segment profits reconcile exactly to the
    group operating profit the filer reported.
    """

    def _segs(self):
        return [{"name": "A", "revenue": 600.0, "profit": 300.0, "margin": 0.5},
                {"name": "B", "revenue": 400.0, "profit": 100.0, "margin": 0.25}]

    def _income(self):
        return {"gross_profit": 400.0, "operating_profit": 150.0, "page": 129}

    def test_derived_profits_reconcile_to_group_operating_profit(self):
        segs = self._segs()
        d = hk._allocate_opex(segs, self._income())
        assert d["central_opex"] == pytest.approx(250.0)
        assert sum(s["operating_profit"] for s in segs) == pytest.approx(150.0)

    def test_reported_gross_profit_is_preserved(self):
        """A derived number that cannot be traced to what the filer said is
        worse than no number."""
        segs = self._segs()
        hk._allocate_opex(segs, self._income())
        assert segs[0]["gross_profit"] == 300.0
        assert segs[0]["gross_margin"] == 0.5
        assert segs[0]["profit"] != 300.0        # profit now operating

    def test_split_is_pro_rata_revenue_by_default(self):
        segs = self._segs()
        d = hk._allocate_opex(segs, self._income())
        assert d["basis"] == "pro_rata_revenue"
        # A carries 60% of revenue, so 60% of the 250 central cost
        assert segs[0]["operating_profit"] == pytest.approx(300.0 - 150.0)
        assert segs[1]["operating_profit"] == pytest.approx(100.0 - 100.0)

    def test_supplied_weights_override_and_are_labelled(self):
        segs = self._segs()
        d = hk._allocate_opex(segs, self._income(), weights={"A": 3, "B": 1})
        assert d["basis"] == "supplied_weights"
        assert segs[0]["operating_profit"] == pytest.approx(300.0 - 187.5)
        assert sum(s["operating_profit"] for s in segs) == pytest.approx(150.0)

    def test_partial_weights_fall_back_rather_than_half_apply(self):
        segs = self._segs()
        d = hk._allocate_opex(segs, self._income(), weights={"A": 3})
        assert d["basis"] == "pro_rata_revenue"

    def test_no_income_statement_means_no_derivation(self):
        assert hk._allocate_opex(self._segs(), {"gross_profit": None,
                                                "operating_profit": None}) is None


class TestIncomeStatementRows:
    def test_note_reference_column_is_not_read_as_money(self):
        """The statement carries a small "Note" integer beside each line."""
        assert hk._IS_ROWS["gross_profit"].match("Gross profit")
        assert hk._IS_ROWS["operating_profit"].match("Operating profit")
        assert not hk._IS_ROWS["gross_profit"].match("Gross profit margin")
