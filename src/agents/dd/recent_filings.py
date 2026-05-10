"""
recent_filings.py — Pull recent SEC filings (8-K, 10-Q, 10-K, Form 4) for DD context.

Wraps the existing `src/data/sec_edgar.py` infrastructure. The DD agent feeds
these into the LLM prompt as factual grounding before the agent does any web
searches — material 8-Ks (acquisitions, executive changes, restatements) are
exactly the kind of catalyst that explains a ±10% move and that the model
might miss with web search alone.

Designed to fail soft: every public function returns `[]` (empty list) on
error so the DD agent can proceed without filings if SEC is down or the
ticker isn't US-listed.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

# Reuse the throttle / UA / CIK lookup already battle-tested in sec_edgar.py
from src.data.sec_edgar import _http_get_json, resolve_cik


# Forms we care about for DD catalyst discovery.
# 8-K  → material events (M&A, exec changes, restatements, guidance updates)
# 10-Q → quarterly results
# 10-K → annual results
# Form 4 → insider transactions (also covered by get_insider_trades_edgar but
#          surfaced here as a brief pointer to recent filings)
# 6-K  → foreign issuer interim reports (ADRs)
# DEF 14A → proxy statements (rarely catalyst-level but included for completeness)
_RELEVANT_FORMS = {"8-K", "10-Q", "10-K", "6-K", "Form 4", "4", "DEF 14A"}


@dataclass(frozen=True)
class RecentFiling:
    """One row from the SEC submissions feed, normalized for DD prompt use."""
    form:        str
    filing_date: str           # YYYY-MM-DD
    url:         str           # canonical primary-document URL
    accession:   str           # accession-number with dashes (e.g. 0000320193-26-000123)

    def to_dict(self) -> dict:
        return asdict(self)


def get_recent_filings(
    ticker: str,
    *,
    lookback_days: int = 30,
    max_filings: int = 10,
    forms: set[str] | None = None,
) -> list[RecentFiling]:
    """Return up to `max_filings` SEC filings from the last `lookback_days`.

    Args:
      ticker:        Trading ticker (case-insensitive).
      lookback_days: How far back to look. Default 30 — covers the typical
                     DD catalyst window without dragging in stale 10-Qs.
      max_filings:   Hard cap on returned rows (most recent first).
      forms:         Whitelist of form types. Defaults to _RELEVANT_FORMS.

    Returns:
      List of RecentFiling, most recent first. Empty list on any failure
      (ticker not found, SEC API error, etc.) — never raises to the caller.

    Performance: 2 SEC API calls (CIK lookup + submissions). Both throttled
    via the shared 100ms gate in sec_edgar.py.
    """
    forms_filter = forms if forms is not None else _RELEVANT_FORMS

    cik = resolve_cik(ticker)
    if not cik:
        return []

    try:
        sub = _http_get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    except Exception:
        return []

    rec = (sub.get("filings") or {}).get("recent") or {}
    f_forms = rec.get("form",            []) or []
    f_dates = rec.get("filingDate",      []) or []
    f_accs  = rec.get("accessionNumber", []) or []
    f_docs  = rec.get("primaryDocument", []) or []

    # cik can come back as either str or int from the submissions feed
    try:
        cik_int = int(sub.get("cik", cik))
    except (TypeError, ValueError):
        cik_int = int(cik)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()

    out: list[RecentFiling] = []
    for i, form in enumerate(f_forms):
        if form not in forms_filter:
            continue
        date_str = f_dates[i] if i < len(f_dates) else ""
        try:
            filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if filing_date < cutoff:
            continue

        acc_raw   = f_accs[i] if i < len(f_accs) else ""
        primary   = f_docs[i] if i < len(f_docs) else ""
        if not acc_raw or not primary:
            continue
        acc_clean = acc_raw.replace("-", "")
        url       = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{primary}"

        out.append(RecentFiling(
            form=form,
            filing_date=date_str,
            url=url,
            accession=acc_raw,
        ))
        if len(out) >= max_filings:
            break

    return out


def format_filings_for_prompt(filings: list[RecentFiling]) -> str:
    """Render the filings list as a markdown bullet block for the DD user prompt.

    Output style matches the DD prompt's "## Recent filings" section so the
    LLM can reference these directly when assembling its `filings` JSON
    output. Returns the literal string "(none in last 30 days)" if empty so
    the model knows it actually checked, vs. having no field at all.
    """
    if not filings:
        return "(none in last 30 days)"
    lines = []
    for f in filings:
        lines.append(f"- **{f.form}** ({f.filing_date}) — {f.url}")
    return "\n".join(lines)
