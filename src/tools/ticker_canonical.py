"""
Cross-market ticker canonicalisation.

The pipeline stores every ticker in its market's canonical form — HK as
"NNNNN.HK", SG as "XXX.SI", everything else uppercased as typed. Anything
that READS that stored state (DB rows, progress-bus keys, per-ticker result
dicts) has to canonicalise its input the same way, or it silently looks up a
key that does not exist and returns "nothing found" rather than an error.

That failure mode has now bitten twice:

  * ReportPage.tsx grew a file-local `_hkCanonical` to fix per-ticker dict
    lookups, but the fix never left that component — the run-recovery paths
    kept sending the raw ticker.
  * `/analysis/status/{ticker}` and `get_history`'s ticker filter never
    canonicalised at all, so a run started as "2888" (stored "02888.HK") was
    invisible to both: the progress bar froze mid-run and History showed the
    run as permanently ongoing alongside its own completed report.

Both are the same bug, and both came from the canonicalisation living inline
at each call site instead of in one place. `canonical_ticker` is that place —
route handlers and services should call it on any user-supplied ticker before
it reaches storage or a lookup.

Order matters: HK is checked before SG because HK codes are purely numeric
and unambiguous, while the SG registry matches short alphanumeric codes.

Examples
--------
>>> canonical_ticker("2888")
'02888.HK'
>>> canonical_ticker("2888.HK")
'02888.HK'
>>> canonical_ticker("02888.HK")
'02888.HK'
>>> canonical_ticker("80700")        # genuine 5-digit RMB counter, not padded
'80700.HK'
>>> canonical_ticker("d05")
'D05.SI'
>>> canonical_ticker("aapl")
'AAPL'
>>> canonical_ticker("")
''
"""
from __future__ import annotations

__all__ = ["canonical_ticker", "ticker_match_forms"]


def canonical_ticker(ticker: str | None) -> str:
    """Return the canonical stored form of `ticker`.

    Non-HK/SG tickers are returned stripped and uppercased. Empty or None
    input returns "" so callers can keep their existing falsy checks.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    # Imported lazily: these modules pull in market registries that the
    # backend does not otherwise need at import time.
    from src.tools.hk.ticker import is_hk_ticker, to_canonical as _hk_canonical
    from src.tools.sg.ticker import is_sg_ticker, to_canonical as _sg_canonical

    # HK first — HK codes are purely numeric and unambiguous, while the SG
    # registry matches short alphanumeric codes.
    if is_hk_ticker(t):
        return _hk_canonical(t)
    if is_sg_ticker(t):
        return _sg_canonical(t)
    return t


def ticker_match_forms(ticker: str | None) -> list[str]:
    """Return the distinct forms a stored row might use for `ticker`.

    Rows written before the pipeline canonicalised on ingest can still hold
    the raw form ("2888.HK"), so a query filtering on the canonical form
    alone would miss them. Callers should match against every form returned
    here — normally one entry, two when the raw and canonical forms differ.
    """
    raw = (ticker or "").strip().upper()
    if not raw:
        return []
    canon = canonical_ticker(raw)
    return [raw] if canon == raw else [raw, canon]
