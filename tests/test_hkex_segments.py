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


class TestUnitTokenisation:
    """Units arrive as one token or two, and both occur in real filings."""

    def test_split_currency_and_scale_tokens(self):
        """PyMuPDF splits Cathay's "HK$M" into "HK$" and "M".

        A combined currency-plus-scale pattern matched neither half, so the
        units multiplier stayed 1.0 -- every revenue read as ~0 -- and the
        leftovers glued onto names as "Total HK$M HK$M".
        """
        assert hk._is_unit_token("HK$")
        assert hk._is_unit_token("M")
        assert hk._is_unit_token("HK$M")
        assert hk._units_and_ccy("HK$M") == (1e6, "HKD")

    def test_a_scale_letter_inside_a_word_is_not_a_unit(self):
        """A bare `m` alternative without a word boundary matches the m in
        "Marketing", which would silently rescale a whole table."""
        for name in ("Marketing", "Total", "VAS", "Smartphones", "Others"):
            assert not hk._is_unit_token(name), name

    def test_no_backspace_bytes_survive_in_the_source(self):
        r"""`\b` written through a shell heredoc becomes 0x08, a literal
        backspace, which as a regex matches a backspace character and never a
        word boundary. It is invisible in a diff and in most editors."""
        import io
        import pathlib
        root = pathlib.Path(hk.__file__).resolve().parents[2]
        for path in list((root / "src").rglob("*.py")):
            text = io.open(path, encoding="utf-8").read()
            assert "\x08" not in text, f"backspace byte in {path}"


class TestPeriodWords:
    def test_date_fragments_never_become_segment_names(self):
        """"Year ended 31 December 2025" spans the table and lands in the same
        x-bands as the headers, producing "Year ended Smartphone x AIoT"."""
        for w in ("Year", "ended", "December", "2025", "For", "the", "(i)"):
            assert hk._PERIOD_WORD.match(w), w
        for w in ("Smartphones", "Services", "Marketing", "Mar-a-Lago"):
            assert not hk._PERIOD_WORD.match(w), w


class TestIncomeStatementTitles:
    def test_ifrs_titles_are_recognised(self):
        """IFRS filers title it "Statement of Profit or Loss"; only US-style
        filers say "Income Statement". Requiring the latter found Tencent's
        and missed Cathay's and Xiaomi's, leaving both carrying gross profit
        as though it were EBIT."""
        import re
        rx = re.compile(r"income statement|profit or loss|"
                        r"statement of comprehensive income", re.I)
        for title in ("Consolidated Income Statement",
                      "Consolidated Statement of Profit or Loss",
                      "Consolidated Statement of Comprehensive Income"):
            assert rx.search(title), title

    def test_operating_profit_row_variants(self):
        for label in ("Operating profit", "Operating profit/(loss)",
                      "Profit from operations"):
            assert hk._IS_ROWS["operating_profit"].match(label), label
        assert not hk._IS_ROWS["operating_profit"].match("Operating profit margin")


class TestBilingualLabels:
    """HK filings are bilingual and PRC filers use different terminology."""

    def test_chinese_suffix_is_stripped_before_matching(self):
        assert hk._clean_label(
            "Operating income from external transactions \u5c0d\u5916\u4ea4\u6613\u6536\u5165"
        ) == "Operating income from external transactions"
        assert hk._clean_label("Overseas \u5883\u5916") == "Overseas"

    def test_prc_operating_income_is_revenue_not_profit(self):
        """"Operating income" means REVENUE to a PRC filer.

        The profit patterns used to prefix-match it, so YOFC's revenue row was
        classified as profit and the table came back with no revenue at all.
        """
        lab = hk._clean_label("Operating income from external transactions")
        assert hk._EXTERNAL_REV_ROW.match(lab)
        assert not hk._PROFIT_ROW.match(lab)

    def test_inter_segment_revenue_is_not_external_revenue(self):
        assert not hk._EXTERNAL_REV_ROW.match("Inter-segment revenue")
        assert not hk._REVENUE_ROW.match("Inter-segment revenue")

    def test_segment_balance_sheet_rows_are_not_metrics(self):
        for lab in ("Segment liabilities", "Segment equity",
                    "Capital expenditures", "Accounts payable"):
            assert not hk._PROFIT_ROW.match(lab), lab
            assert not hk._REVENUE_ROW.match(lab), lab


class TestNilDashes:
    """A nil entry prints as a dash, not a zero."""

    def test_dash_forms_are_read_as_zero(self):
        cells = hk._numeric_cells([(0, 1, "1,000"), (2, 3, "\u2013"),
                                   (4, 5, "\u2014"), (6, 7, "-")])
        assert [c[2] for c in cells] == [1000.0, 0.0, 0.0, 0.0]

    def test_row_lengths_stay_aligned(self):
        """Skipping nils makes a row carry FEWER cells than its neighbours,
        the modal column count goes ambiguous and the block is discarded --
        YOFC's table has rows of 4, 5 and 6 cells for exactly this reason."""
        full = hk._numeric_cells([(0, 1, "1"), (2, 3, "2"), (4, 5, "3")])
        nils = hk._numeric_cells([(0, 1, "1"), (2, 3, "\u2013"), (4, 5, "3")])
        assert len(full) == len(nils) == 3

    def test_hyphenated_words_do_not_truncate_a_label(self):
        assert hk._label_of([(0, 1, "Non-operating"), (2, 3, "income"),
                             (4, 5, "\u2013")]) == "Non-operating income"


class TestSingleSegmentFilers:
    """One reportable segment is a VERDICT, not a parse failure.

    MiniMax: "the Group has only one single operating segment and no further
    analysis of the single segment". CATL: "the management believes that the
    Company has only one operating segment and does not need to prepare a
    segment report". Reporting those as "no segment map" sends someone hunting
    a parser bug that does not exist, and hides that SOTP is the wrong method
    for the company rather than merely unavailable.
    """

    class _Page:
        def __init__(self, text):
            self._t = text

        def get_text(self):
            return self._t

    class _Doc:
        def __init__(self, pages):
            self._p = [TestSingleSegmentFilers._Page(t) for t in pages]
            self.page_count = len(self._p)

        def __getitem__(self, i):
            return self._p[i]

    def test_real_declarations_are_detected(self):
        for text in (
            "Accordingly, the Group has only one single operating segment and "
            "no further analysis of the single segment is presented.",
            "the management believes that the Company has only one operating "
            "segment and does not need to prepare a segment report",
            "The Group has one reportable segment.",
        ):
            assert hk.declares_single_segment(self._Doc([text])), text

    def test_a_multi_segment_filing_is_not_flagged(self):
        text = ("The Group has the following reportable segments: VAS; "
                "Marketing Services; FinTech and Business Services; Others.")
        assert not hk.declares_single_segment(self._Doc([text]))

    def test_pages_without_the_word_segment_are_skipped(self):
        assert not hk.declares_single_segment(
            self._Doc(["Directors' report", "Auditor's opinion"]))


class TestPageSelection:
    """Structure decides which page to try FIRST, never which to skip."""

    class _P:
        def __init__(self, text, words=()):
            self._t, self._w = text, words

        def get_text(self, kind=None):
            return self._w if kind == "words" else self._t

    class _D:
        def __init__(self, pages):
            self._p = pages
            self.page_count = len(pages)

        def __getitem__(self, i):
            return self._p[i]

    def _rows(self, y0):
        """A page whose text has enough numbers, with no parseable rows."""
        return [(10.0, y0, 40.0, y0 + 8, "Segment", 0, 0, 0)]

    def test_a_page_with_unrecognised_rows_is_still_a_candidate(self):
        """Requiring two recognised metric rows to qualify dropped Lenovo and
        SMIC from three candidate pages to none. A page whose rows are not
        recognised YET is exactly the page still worth trying."""
        text = "Segment information " + " ".join(["1,000"] * 8)
        doc = self._D([self._P(text, self._rows(10.0))])
        assert hk._candidate_pages(doc) == [0]

    def test_pages_without_the_word_segment_are_excluded(self):
        doc = self._D([self._P("Directors report " + " ".join(["1,000"] * 9))])
        assert hk._candidate_pages(doc) == []

    def test_sparse_pages_are_excluded(self):
        doc = self._D([self._P("Segment information 1,000 2,000")])
        assert hk._candidate_pages(doc) == []

    def test_a_structured_page_outranks_a_merely_dense_one(self):
        """Keyword-plus-density ranked Ping An's segment BALANCE SHEET above
        its income table, and BYD's ASC 606 timing table above its segment
        note. Both are dense and both say "segment"."""
        def row(y, label, n):
            out = [(10.0, y, 60.0, y + 8, w, 0, 0, i)
                   for i, w in enumerate(label.split())]
            out += [(100.0 + 60 * k, y, 140.0 + 60 * k, y + 8, "1,000", 0, 0, 9 + k)
                    for k in range(n)]
            return out

        dense = self._P("segment " + " ".join(["1,000"] * 40),
                        row(10.0, "Accounts payable", 4) + row(30.0, "Segment liabilities", 4))
        good = self._P("segment " + " ".join(["1,000"] * 10),
                       row(10.0, "Segment revenues", 4) + row(30.0, "Gross profit", 4))
        assert hk._candidate_pages(self._D([dense, good]))[0] == 1
