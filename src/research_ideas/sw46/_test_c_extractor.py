"""
Unit test for the C-line extractor. Feeds a synthetic
/stable/financial-reports-json shape (modelled on the AAPL FY2022 sample
structure the user supplied) and confirms _extract_c_per_year() pulls the
right number with the right unit scaling and the right fiscal-year mapping.

Invoke:  .\.venv\Scripts\python.exe -m src.research_ideas.sw46._test_c_extractor
"""
from src.research_ideas.sw46.data_fetch import _extract_c_per_year, _parse_units, _parse_fiscal_year


# Minimum-viable synthetic 10-K response. Top-level keys mimic FMP's section
# truncation behaviour ("CONSOLIDATED STATEMENTS OF CASH" instead of the full
# "...CASH FLOWS"). The cash-flow section contains:
#   - section header carrying the unit annotation
#   - the "items" row listing the three reported fiscal years (newest first)
#   - several line items, one of which is the C line we care about
#   - other lines we should NOT match
SAMPLE_APPLE_FY2022 = {
    "symbol": "AAPL",
    "period": "FY",
    "year": "2022",
    "Cover Page": [
        {"Cover Page - USD ($) shares in Thousands, $ in Millions": ["12 Months Ended"]},
        {"items": ["Sep. 24, 2022"]},
    ],
    "CONSOLIDATED STATEMENTS OF CASH": [
        {"CONSOLIDATED STATEMENTS OF CASH FLOWS - USD ($) $ in Millions": ["12 Months Ended"]},
        {"items": ["Sep. 24, 2022", "Sep. 25, 2021", "Sep. 26, 2020"]},
        {"Statement of Cash Flows [Abstract]": [" ", " ", " "]},
        {"Net income": [99803, 94680, 57411]},
        {"Share-based compensation expense": [9038, 7906, 6829]},
        # The C line — three real AAPL numbers from their FY2022 10-K filing.
        {"Payments for taxes related to net share settlement of equity awards": [6223, 6556, 3634]},
        {"Repurchases of common stock": [-89402, -85971, -72358]},
        {"Other": [-1255, -129, -126]},
    ],
}

# A second issuer with different wording for the same line (e.g. some
# companies use "Taxes paid for net share settlement..." or include "Cash"
# explicitly). Confirms the regex alternates work.
SAMPLE_OTHER_FY2024 = {
    "symbol": "TEST",
    "period": "FY",
    "year": "2024",
    "Consolidated Statements of Cash Flows": [
        {"Consolidated Statements of Cash Flows - USD ($) $ in Thousands": ["12 Months Ended"]},
        {"items": ["December 31, 2024", "December 31, 2023"]},
        # Variant wording, "Thousands" scaling.
        {"Taxes paid for net share settlement of equity awards": [-1_200_000, -900_000]},
    ],
}

# Negative case — issuer doesn't break it out at all.
SAMPLE_NO_C_LINE = {
    "symbol": "NOTHING",
    "period": "FY",
    "year": "2024",
    "Consolidated Statements of Cash Flows": [
        {"Consolidated Statements of Cash Flows - USD ($) $ in Millions": ["12 Months Ended"]},
        {"items": ["December 31, 2024"]},
        {"Net income": [1000]},
        {"Other financing activities, net": [-500]},
    ],
}


def _expect(label: str, actual, expected) -> bool:
    ok = actual == expected
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {label}: got {actual!r}, expected {expected!r}")
    return ok


def main() -> None:
    print("\n=== unit helpers ===")
    all_ok = True
    all_ok &= _expect("_parse_units('$ in Millions')",  _parse_units("$ in Millions"), 1e6)
    all_ok &= _expect("_parse_units('$ in Billions')",  _parse_units("$ in Billions"), 1e9)
    all_ok &= _expect("_parse_units('$ in Thousands')", _parse_units("$ in Thousands"), 1e3)
    all_ok &= _expect("_parse_units('')",               _parse_units(""), 1.0)
    all_ok &= _expect("_parse_fiscal_year('Sep. 24, 2022')",     _parse_fiscal_year("Sep. 24, 2022"), 2022)
    all_ok &= _expect("_parse_fiscal_year('December 31, 2024')", _parse_fiscal_year("December 31, 2024"), 2024)
    all_ok &= _expect("_parse_fiscal_year('2024-12-31')",        _parse_fiscal_year("2024-12-31"), 2024)
    all_ok &= _expect("_parse_fiscal_year(None)",                _parse_fiscal_year(None), None)

    print("\n=== _extract_c_per_year(AAPL FY2022 sample, Millions) ===")
    out = _extract_c_per_year(SAMPLE_APPLE_FY2022)
    print(f"  result: {out}")
    all_ok &= _expect("FY2022 in result", 2022 in out, True)
    all_ok &= _expect("FY2022 value (M -> raw USD)", out.get(2022), 6_223_000_000)
    all_ok &= _expect("FY2021 value",                out.get(2021), 6_556_000_000)
    all_ok &= _expect("FY2020 value",                out.get(2020), 3_634_000_000)

    print("\n=== _extract_c_per_year(other-wording sample, Thousands) ===")
    out2 = _extract_c_per_year(SAMPLE_OTHER_FY2024)
    print(f"  result: {out2}")
    all_ok &= _expect("FY2024 value (k -> raw USD, abs)", out2.get(2024), 1_200_000 * 1e3)
    all_ok &= _expect("FY2023 value",                     out2.get(2023),   900_000 * 1e3)

    print("\n=== _extract_c_per_year(no-C-line sample) ===")
    out3 = _extract_c_per_year(SAMPLE_NO_C_LINE)
    all_ok &= _expect("empty result when issuer doesn't break it out", out3, {})

    print("\n" + ("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED"))


if __name__ == "__main__":
    main()
