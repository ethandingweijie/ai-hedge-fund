"""Segment footnote for an SGX listing -- NOT YET IMPLEMENTED.

This module exists so the market registry in `segment_providers` has a real
shape for SGX rather than an implicit gap, and so the reason it returns nothing
is written down instead of being rediscovered.

What is already ruled out
-------------------------
* FMP -- `revenue-product-segmentation` and `revenue-geographic-segmentation`
  both return [] for SGX, verified live (D05.SI). Same as HK.
* SEC -- no route for an SGX primary listing.

What the route will have to be
------------------------------
The SGX annual report PDF, the same shape of problem HKEX solved: locate the
filing, find the IFRS 8 segment note, recover columns from geometry. The
PARSER in `hkex_segments` is already market-agnostic once it has a PDF -- what
is missing is the SGX equivalent of `src/tools/hkex_api.py`, i.e. resolving a
ticker to its annual-report document URL. SGX publishes filings through its
own announcements API rather than anything resembling HKEXnews.

Two SGX-specific things will need care when it is built:

* Singapore banks (D05, O39, U11) report by BUSINESS SEGMENT and by
  GEOGRAPHY, and the geographic cut is usually the more prominent one. The
  shared `_is_geographic` check in `segment_normalize` already discriminates,
  but a bank's segment profit is not comparable to an industrial's -- the
  engine values SG banks by GGM, not by a multiple on segment EBIT.
* SGX REITs and trusts report by property, which is a valuation split but not
  an operating one; those belong on a NAV basis, not an earnings multiple.
"""
from __future__ import annotations

from typing import Optional

NOT_IMPLEMENTED_REASON = (
    "SGX annual-report resolution is not built: FMP segmentation returns [] "
    "for SGX and there is no SEC route, so a filing-document resolver "
    "equivalent to src/tools/hkex_api.py is required first"
)


def get_segment_footnote(ticker: str, end_date: str) -> Optional[dict]:
    """Always None for now. See NOT_IMPLEMENTED_REASON."""
    return None
