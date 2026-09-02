"""Bridge the SEC segment footnote into SOTP assumptions.

`src/tools/sec_segments.py` returns reported segment revenue and profit in the
filing's reporting currency for the last completed fiscal year. The SOTP engine
wants FORWARD revenue in USD. This module does that conversion and nothing
else -- it is kept separate from the parser so the parser stays offline-testable
and free of FMP/FX concerns, mirroring how `sotp_multiple_basis` sits beside
`sotp_extractor`.

Two decisions worth stating, because both were forced by the data:

**FX comes from the filing itself where possible.** A 20-F carries a
convenience translation (BABA: 1,023,670 CNY <-> 148,401 USD => 0.144971) which
is the rate the issuer actually used. Preferring it over a live spot quote means
converted segments reconcile to the filing's own stated USD totals. Note
`_fmp_segment_anchor` drops `reported_currency` entirely while
`_deterministic_skeleton` converts -- that asymmetry is a live trap, so the
currency is carried explicitly all the way to the conversion here.

**Trailing revenue is re-based to consensus by MIX, not by uniform growth.**
Segment revenue sums to more than consolidated revenue because of inter-segment
eliminations (BABA 1.085x, JD 1.064x). Scaling every segment by one growth
factor carries that 6-8% inflation straight into NAV. Normalising the mix onto
the consensus forward group revenue removes it by construction, pins the sum to
consensus so the downstream consistency check is trivially clean, and matches
what the research-note fixtures already do -- which is what makes a note-vs-
filing comparison honest.

The filing's real contribution is the MIX and the MARGIN. The absolute level is
better sourced from consensus, which is forward-looking.
"""
from __future__ import annotations

from typing import Any, Optional

from src.agents.analysis.sotp_multiple_basis import normalize_key

# Tokens that carry no identity when matching segment names across sources.
_STOPWORDS = {
    "group", "segment", "segments", "business", "businesses", "and", "the",
    "inc", "ltd", "limited", "total", "other", "others", "co", "corporation",
}
# Below this, two names are different businesses rather than two spellings.
_JACCARD_FLOOR = 0.5
# A filing group revenue this far from the FMP TTM group revenue means a units
# misread, a wrong FX direction, or a fiscal-period mix-up -- not a real gap.
_SCALE_TOLERANCE = 0.35
# Outside this, the "consensus" is not the year after the filing.
_GROWTH_BAND = (-0.15, 0.60)


def _tokens(name: str) -> set[str]:
    raw = (name or "").lower().replace("&", " and ")
    words = "".join(c if c.isalnum() else " " for c in raw).split()
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _resolve_fx(filing: dict, api_key: Any) -> tuple[float, str]:
    """USD per unit of reporting currency, and where the rate came from."""
    ccy = (filing.get("reporting_currency") or "USD").upper()
    if ccy == "USD":
        return 1.0, "identity"
    implied = filing.get("usd_convenience_rate")
    if implied and implied > 0:
        return float(implied), "filing_convenience_translation"
    try:
        from src.tools.api import get_fx_rate
        rate = get_fx_rate(ccy, "USD", api_key)
        if rate and rate > 0:
            return float(rate), "fx_rate_api"
    except Exception:                             # noqa: BLE001
        pass
    return 1.0, "unconverted"


# Margin drifts a few points a year; anything steeper is a restatement or a
# perimeter change, not a trend to extrapolate. BABA's China E-commerce margin
# reads 38.0% -> 19.4% because the segment was redefined, and projecting that
# slope two years forward would take it negative.
_MARGIN_TREND_CAP = 0.03          # pp per year
_MARGIN_DRIFT_CAP = 0.15          # total move away from the reported level
_MARGIN_FLOOR, _MARGIN_CEIL = -1.0, 0.80


def _margin_trend(seg: dict) -> Optional[float]:
    """Annual change in a segment's reported margin, from the filing history."""
    rbp = seg.get("revenue_by_period") or {}
    pbp = seg.get("profit_by_period") or {}
    periods = sorted((p for p in set(rbp) & set(pbp) if rbp.get(p)), reverse=True)
    if len(periods) < 2:
        return None
    m_cur = pbp[periods[0]] / rbp[periods[0]]
    m_prev = pbp[periods[1]] / rbp[periods[1]]
    return min(max(m_cur - m_prev, -_MARGIN_TREND_CAP), _MARGIN_TREND_CAP)


def _project_margin(margin: Optional[float], trend: Optional[float],
                    years: int) -> Optional[float]:
    """Reported margin carried forward along its own trend, bounded."""
    if margin is None:
        return None
    if not trend or years <= 0:
        return margin
    moved = margin + trend * years
    moved = min(max(moved, margin - _MARGIN_DRIFT_CAP),
                margin + _MARGIN_DRIFT_CAP)
    return min(max(moved, _MARGIN_FLOOR), _MARGIN_CEIL)


def _reconcile_ebit(segs: list[dict], target_ebit: Optional[float]) -> Optional[float]:
    """Shift every segment's margin by the same amount to hit consensus EBIT.

    Same discipline as revenue: the filing supplies the SPLIT, the market
    supplies the LEVEL. Holding realised margins flat was the largest remaining
    error -- consensus has AMZN at a 16.0% group margin against ~11.1% trailing,
    so a flat assumption discards five points of expected expansion.

    The gap is spread pro-rata to REVENUE, i.e. a uniform percentage-point
    adjustment. Scaling EBIT multiplicatively instead would push loss-making
    segments further negative while lifting profitable ones -- the wrong
    direction for exactly the segments the engine values on revenue.

    Returns the applied adjustment in percentage points, or None when no
    consensus EBIT is available.
    """
    if target_ebit is None:
        return None
    total_rev = sum(s["revenue_fwd"] for s in segs)
    if total_rev <= 0:
        return None
    current = sum(s["ebit_fwd"] or 0.0 for s in segs)
    delta_pp = (target_ebit - current) / total_rev
    moved = 0
    for s in segs:
        if s.get("margin") is None:
            continue
        moved += 1
        shifted = (s.get("margin_fwd") or s["margin"]) + delta_pp
        # A segment cannot earn more than it bills. Without this an aggressive
        # consensus EBIT drives the shift past 100% -- NVDA reconciled to an
        # EBIT of $370B on $311B of segment revenue. The clamp means the group
        # may miss the consensus total slightly, which is the honest outcome:
        # the target is unreachable from this revenue base.
        s["margin_fwd"] = min(max(shifted, _MARGIN_FLOOR), _MARGIN_CEIL)
        s["ebit_fwd"] = s["revenue_fwd"] * s["margin_fwd"]
    # A filer that discloses segment revenue but no segment profit (AAPL) has
    # nothing to shift. Reporting a shift anyway claims a reconciliation that
    # never happened -- the margins come from `apply_margin_basis` downstream.
    return delta_pp if moved else None


def _years_between(filing_period: Optional[str],
                   target_period: Optional[str]) -> int:
    """Whole years from the filing's fiscal year end to the consensus period.

    Both are ISO dates from the same fiscal calendar, so the year difference is
    the projection horizon. Returns 0 when either is missing, which makes the
    caller fall back to a constant-mix allocation rather than guess a horizon.
    """
    try:
        fy = int((filing_period or "")[:4])
        ty = int((target_period or "")[:4])
    except (TypeError, ValueError):
        return 0
    return max(0, min(ty - fy, 5))


def forward_estimate(ticker: str, end_date: str, api_key: Any = None, *,
                     years_ahead: int = 2) -> Optional[dict]:
    """Consensus for the Nth annual period after `end_date`.

    `_fmp_estimates_anchor` returns the FIRST forward year (NTM). The research
    notes value NTM+1 -- GS's AMZN note is explicit: "NTM+1 SOTP ... GS CY27E
    segment estimates". Anchoring the filing bridge on NTM while the note
    anchors on NTM+1 compares two different years and reads as a data problem
    when it is a period problem: on AMZN the note's group revenue is ~$977B
    against ~$828B for NTM, a full year of growth apart.

    Falls back to the furthest available year when the requested one is not
    published, and reports which period it actually used.
    """
    try:
        from src.tools.api import get_analyst_estimates
        ests = get_analyst_estimates(ticker, end_date, period="annual",
                                     limit=6, api_key=api_key)
    except Exception:                             # noqa: BLE001
        return None
    if not ests:
        return None
    fwd = sorted((e for e in ests if e.period_end > end_date),
                 key=lambda e: e.period_end)
    if not fwd:
        return None
    idx = min(max(years_ahead, 1), len(fwd)) - 1
    e = fwd[idx]
    return {"period_end": e.period_end, "revenue_avg": e.revenue_avg,
            "ebit_avg": getattr(e, "ebit_avg", None),
            "years_ahead": idx + 1, "years_requested": years_ahead,
            "n_forward_periods": len(fwd)}


def _segment_growth(seg: dict) -> Optional[float]:
    """Latest reported YoY revenue growth for one segment.

    Segments do not grow together: AWS +19.7% against North America +10.0%,
    Intelligent Cloud +29.7% against More Personal Computing -1.1%, BABA Cloud
    +34.0% against All others -24.8%. Holding the mix constant is biased rather
    than merely imprecise, because in every case the fastest-growing segment is
    also the highest-multiple one -- so a constant mix starves exactly the unit
    that drives the valuation.
    """
    by_period = seg.get("revenue_by_period") or {}
    periods = sorted((p for p, v in by_period.items() if v), reverse=True)
    if len(periods) < 2:
        return None
    cur, prev = by_period[periods[0]], by_period[periods[1]]
    if not prev or prev <= 0 or cur is None:
        return None
    g = cur / prev - 1.0
    # A segment compounding outside this band is a restatement, a disposal or a
    # standing start (JD's New Businesses printed +157%); projecting it forward
    # would dominate the group. Clamp and let the caller record it.
    return min(max(g, -0.30), 0.60)


def filing_segment_anchor(ticker: str, end_date: str, api_key: Any = None, *,
                          fwd_est: Optional[dict] = None,
                          group_revenue_usd: Optional[float] = None,
                          state: Any = None,
                          use_transcript: bool = True
                          ) -> Optional[dict]:
    """Forward, USD segment revenue and EBIT from the latest 10-K / 20-F.

    Returns None whenever the filing route yields nothing usable, so the caller
    can fall through to its existing sources untouched.
    """
    try:
        from src.tools.sec_segments import get_segment_footnote
        filing = get_segment_footnote(ticker, end_date)
    except Exception as exc:                      # noqa: BLE001
        print(f"  [sotp-filing] {ticker}: lookup failed {type(exc).__name__}")
        return None
    if not filing or not filing.get("segments"):
        return None

    fx, fx_source = _resolve_fx(filing, api_key)
    if fx_source == "unconverted" and (filing.get("reporting_currency") or "USD") != "USD":
        print(f"  [sotp-filing] {ticker}: no FX for "
              f"{filing['reporting_currency']} -- anchor dropped")
        return None

    segs_usd = []
    for s in filing["segments"]:
        rev = (s.get("revenue") or 0.0) * fx
        if rev <= 0:
            continue
        profit = s.get("profit")
        segs_usd.append({
            "name": s["name"],
            "revenue_ttm": rev,
            "profit_ttm": (profit * fx) if profit is not None else None,
            # Margin is a ratio and therefore FX-invariant -- it is the part of
            # the filing that survives every conversion decision intact.
            "margin": s.get("margin"),
            # Prior years, carried through so the bridge can allocate
            # growth per segment instead of holding the mix constant.
            # Growth is a ratio, so no FX conversion is needed here.
            "revenue_by_period": s.get("revenue_by_period") or {},
            # Needed alongside revenue to derive the margin trend.
            "profit_by_period": s.get("profit_by_period") or {},
        })
    if len(segs_usd) < 2:
        return None

    group_usd = (filing.get("consolidated_revenue") or 0.0) * fx
    warnings: list[str] = list(filing.get("warnings") or [])

    # One check catches a units misread, an inverted FX rate and a fiscal
    # mix-up: the filing's group revenue must be near FMP's TTM group revenue.
    if group_usd and group_revenue_usd:
        drift = abs(group_usd / group_revenue_usd - 1.0)
        if drift > _SCALE_TOLERANCE:
            print(f"  [sotp-filing] {ticker}: group revenue {group_usd/1e9:.1f}B "
                  f"vs FMP {group_revenue_usd/1e9:.1f}B ({drift:.0%}) -- dropped")
            return None

    seg_sum = sum(s["revenue_ttm"] for s in segs_usd)
    # FMP consensus arrives in the filer's REPORTING currency, not USD --
    # verified live: JD's forward revenue_avg is ~1.37tn, which is CNY, while
    # `_deterministic_skeleton` reports group revenue already converted to USD.
    # Comparing them unconverted implies +629% growth for JD and +665% for
    # BABA. Same asymmetry as `_fmp_segment_anchor` dropping reported_currency.
    consensus = (fwd_est or {}).get("revenue_avg")
    if consensus and fx != 1.0:
        consensus = consensus * fx
    _period_years = _years_between(filing.get("period_end"),
                                   (fwd_est or {}).get("period_end")) or 1
    basis_group = group_usd or group_revenue_usd or seg_sum
    implied_growth = None
    clamped = False

    if consensus and consensus > 0 and basis_group:
        implied_growth = consensus / basis_group - 1.0
        # The band is an ANNUAL rate, but the consensus period can be two or
        # more years out. Comparing a multi-year total against an annual band
        # caps a growth filer absurdly hard -- NVDA's consensus sits +216% above
        # the last filing over two years (a 78% CAGR, which is what the market
        # actually expects of it) and was being cut to +60% total, i.e. 26% a
        # year. Compound the band over the horizon before testing.
        _yrs = max(_period_years, 1.0)
        lo = (1.0 + _GROWTH_BAND[0]) ** _yrs - 1.0
        hi = (1.0 + _GROWTH_BAND[1]) ** _yrs - 1.0
        if not (lo <= implied_growth <= hi):
            clamped_growth = min(max(implied_growth, lo), hi)
            warnings.append(
                f"implied group growth {implied_growth:+.0%} over {_yrs:.0f}y "
                f"outside [{lo:+.0%},{hi:+.0%}] -- clamped to "
                f"{clamped_growth:+.0%}")
            consensus = basis_group * (1.0 + clamped_growth)
            clamped = True
        target, mode = consensus, "mix_to_consensus"
    else:
        target, mode = seg_sum, "trailing_as_forward"
        warnings.append("no forward consensus -- trailing revenue used as forward")

    # ── allocate the group target across segments ──────────────────────────
    # Each segment compounds at its OWN reported growth for the number of years
    # between the filing and the consensus period, then the whole set is
    # rescaled so the group still lands exactly on consensus. Rescaling is what
    # keeps this an allocation rather than a forecast: the group total is still
    # the market's number, only the split is the filing's.
    years = _years_between(filing.get("period_end"),
                           (fwd_est or {}).get("period_end"))
    # Growth per segment, by precedence: what management said on the call,
    # then a deposited analyst report, then the filing's own history, then
    # the group rate. The filing history is only the THIRD tier -- it is
    # backward-looking, and a transcript carries both a fresher read and
    # occasionally real forward guidance (AAPL guides iPhone explicitly).
    from src.agents.analysis.segment_growth import resolve_segment_growth
    _filing_hist = {s["name"]: _segment_growth(s) for s in segs_usd}
    _resolved = resolve_segment_growth(
        ticker, segs_usd, filing_growth=_filing_hist,
        group_growth=(implied_growth / max(1, 1) if implied_growth is not None
                      else None),
        state=state, use_transcript=use_transcript and state is not None)
    growths = {n: (v or {}).get("growth") for n, v in _resolved.items()}
    growth_sources = {n: (v or {}).get("source") for n, v in _resolved.items()}
    # The group-consensus tier hands every segment the SAME rate, so a set made
    # only of those is constant mix by another name -- after rescaling to the
    # group target the shares are identical to trailing. Counting it as
    # per-segment growth would label a uniform assumption as differentiated.
    n_growth = sum(1 for n, g in growths.items()
                   if g is not None
                   and growth_sources.get(n) != "group_consensus")
    use_growth = n_growth >= 2 and years >= 1

    if use_growth:
        projected = {}
        for s in segs_usd:
            g = growths.get(s["name"])
            # A segment with no history rides the group rate rather than
            # standing still, which would silently shrink its share.
            rate = g if g is not None else (implied_growth or 0.0) / max(years, 1)
            projected[s["name"]] = s["revenue_ttm"] * (1.0 + rate) ** years
        proj_sum = sum(projected.values()) or 1.0
        for s in segs_usd:
            s["revenue_fwd"] = target * projected[s["name"]] / proj_sum
            s["growth_used"] = growths.get(s["name"])
            s["growth_source"] = growth_sources.get(s["name"])
    else:
        for s in segs_usd:
            s["revenue_fwd"] = target * (s["revenue_ttm"] / seg_sum
                                         if seg_sum else 0.0)
            s["growth_used"] = None
        if years >= 1:
            warnings.append(
                "constant-mix allocation: fewer than two segments carry a "
                "prior-year revenue, so per-segment growth is unavailable")

    # ── margin path ────────────────────────────────────────────────────────
    # Each segment's reported margin is carried along its own trend, then the
    # whole set is shifted so the group lands on consensus EBIT. Holding
    # margins flat was the dominant remaining error.
    trends = {s["name"]: _margin_trend(s) for s in segs_usd}
    for s in segs_usd:
        s["margin_trend"] = trends.get(s["name"])
        s["margin_fwd"] = _project_margin(s["margin"], s["margin_trend"], years)
        s["ebit_fwd"] = (s["revenue_fwd"] * s["margin_fwd"]
                         if s["margin_fwd"] is not None else None)

    # Consensus EBIT is a GROUP number and arrives in the reporting currency,
    # like revenue. Corporate overhead is not allocated to segments, so the
    # segment EBIT target is consensus less that unallocated cost -- which is
    # the first real use of the corporate line the parser has been capturing.
    consensus_ebit = (fwd_est or {}).get("ebit_avg")
    if consensus_ebit and fx != 1.0:
        consensus_ebit = consensus_ebit * fx
    corp = (filing.get("corporate_unallocated") or {}).get("profit")
    target_ebit = None
    consensus_margin = None
    if consensus_ebit:
        segment_ebit_consensus = consensus_ebit - ((corp * fx) if corp else 0.0)
        # The revenue level and the EBIT level must share a base. They do NOT
        # when the growth guard above clamps revenue: NVDA's revenue was cut to
        # $345B while consensus EBIT stayed at $406B, implying a 118% segment
        # margin. Carry the consensus MARGIN across instead of the absolute --
        # identical when nothing is clamped, and self-consistent when it is.
        _cons_rev = (fwd_est or {}).get("revenue_avg")
        if _cons_rev and fx != 1.0:
            _cons_rev = _cons_rev * fx
        if _cons_rev and _cons_rev > 0:
            consensus_margin = segment_ebit_consensus / _cons_rev
            target_ebit = consensus_margin * sum(s["revenue_fwd"]
                                                 for s in segs_usd)
        else:
            target_ebit = segment_ebit_consensus
    margin_shift_pp = _reconcile_ebit(segs_usd, target_ebit)
    if margin_shift_pp is None:
        warnings.append(
            "no consensus EBIT -- segment margins carry their own trend but "
            "are not reconciled to a group level")

    return {
        "ticker": ticker,
        "filer_ticker": filing.get("filer_ticker"),
        "form": filing.get("form"), "accession": filing.get("accession"),
        "filed": filing.get("filed"), "report_file": filing.get("report_file"),
        "source_url": filing.get("source_url"),
        "period_end": filing.get("period_end"),
        "reporting_currency": filing.get("reporting_currency"),
        "fx": fx, "fx_source": fx_source,
        "profit_metric": filing.get("profit_metric"),
        "profit_is_gaap_operating_income":
            filing.get("profit_is_gaap_operating_income"),
        "segments": segs_usd,
        "group_revenue_usd": group_usd or None,
        "segment_sum_usd": seg_sum,
        "sum_vs_consolidated": filing.get("sum_vs_consolidated"),
        "corporate_unallocated": filing.get("corporate_unallocated"),
        "eliminations": filing.get("eliminations"),
        "bridge": {"mode": mode, "target_revenue_usd": target,
                   "consensus_period_end": (fwd_est or {}).get("period_end"),
                   "filing_period_end": filing.get("period_end"),
                   "years_projected": years,
                   "years_ahead": (fwd_est or {}).get("years_ahead"),
                   "allocation": ("segment_growth" if use_growth
                                  else "constant_mix"),
                   "segment_growth": growths,
                   "segment_growth_source": growth_sources,
                   "segment_growth_detail": _resolved,
                   "margin_basis": ("trend_then_reconciled_to_consensus"
                                    if margin_shift_pp is not None
                                    else "reported_trailing_with_trend"),
                   "margin_shift_pp": margin_shift_pp,
                   "consensus_ebit_usd": consensus_ebit,
                   "segment_ebit_target_usd": target_ebit,
                   "consensus_ebit_margin": consensus_margin,
                   "corporate_unallocated_usd": ((corp * fx) if corp else None),
                   "margin_trend": trends,
                   "implied_group_growth": implied_growth,
                   "clamped": clamped},
        "warnings": warnings,
    }


def _match(filing_name: str, candidates: list[dict],
           taken: set[int]) -> tuple[Optional[int], Optional[str]]:
    """Find the merged segment a filing segment refers to.

    Deliberately conservative: an unmatched segment is reported as such rather
    than paired on a guess, because a wrong pairing silently swaps one
    business's economics onto another.
    """
    fk = normalize_key(filing_name)
    for i, c in enumerate(candidates):
        if i in taken:
            continue
        if normalize_key(c.get("name", "")) == fk:
            return i, "exact"
    for i, c in enumerate(candidates):
        if i in taken:
            continue
        ck = normalize_key(c.get("name", ""))
        if ck and fk and (ck in fk or fk in ck):
            return i, "containment"
    best, best_score = None, 0.0
    for i, c in enumerate(candidates):
        if i in taken:
            continue
        score = _jaccard(filing_name, c.get("name", ""))
        if score > best_score:
            best, best_score = i, score
    if best is not None and best_score >= _JACCARD_FLOOR:
        return best, f"jaccard:{best_score:.2f}"
    return None, None


def apply_filing_overlay(segments: list[dict], anchor: Optional[dict],
                         *, allow_canonical: bool = True
                         ) -> tuple[list[dict], dict]:
    """Overwrite segment revenue and EBIT with reported figures.

    Names and multiples are deliberately LEFT ALONE. Filing names largely fail
    `classify_archetype` -- "Alibaba China E-commerce Group", "All others" and
    "New Businesses" all miss -- so swapping them in would silently strip the
    multiple basis from the largest segments and turn a single-variable
    experiment into a two-variable one. Only the numbers change.

    Runs BEFORE `apply_margin_basis`, which skips any entry that already has an
    `ebit` (status ``researched_kept``). Setting `ebit` here is therefore what
    preempts the learned margin model, with no edit to `sotp_multiple_basis`.
    """
    if not anchor or not anchor.get("segments"):
        return segments, {"status": "no_filing"}

    out = [dict(s) for s in segments]
    displaced: list = []
    taken: set[int] = set()
    matches: list[dict] = []
    unmatched: list[str] = []

    for fs in sorted(anchor["segments"], key=lambda s: -s["revenue_fwd"]):
        idx, rule = _match(fs["name"], out, taken)
        if idx is None:
            unmatched.append(fs["name"])
            continue
        taken.add(idx)
        entry = out[idx]
        prev_rev, prev_margin = entry.get("revenue_fwd"), entry.get("ebit_margin")
        entry["revenue_fwd"] = fs["revenue_fwd"]
        if fs["ebit_fwd"] is not None:
            entry["ebit"] = fs["ebit_fwd"]
            # Drop the stale margin: it belonged to the previous revenue and
            # would now disagree with the EBIT just written.
            entry["ebit_margin"] = None
        entry["source"] = "sec_segment_footnote"
        entry["evidence"] = (
            f"{anchor['form']} {anchor['accession']} {anchor['report_file']}, "
            f"FY ending {anchor['period_end']}: {fs['name']} "
            f"revenue {fs['revenue_ttm']/1e9:.1f}B, "
            f"{anchor.get('profit_metric') or 'profit'} "
            f"{(fs['profit_ttm'] or 0)/1e9:.1f}B "
            f"({(fs['margin'] or 0):.1%} margin); re-based to "
            f"{anchor['bridge']['mode']}")
        matches.append({
            "filing_name": fs["name"], "matched_name": entry.get("name"),
            "rule": rule, "revenue_fwd": fs["revenue_fwd"],
            "ebit": fs["ebit_fwd"], "margin": fs["margin"],
            "prev_revenue_fwd": prev_rev, "prev_ebit_margin": prev_margin,
        })

    filing_rev = sum(s["revenue_fwd"] for s in anchor["segments"]) or 1.0
    matched_rev = sum(m["revenue_fwd"] for m in matches)
    coverage = matched_rev / filing_rev
    status = ("applied" if coverage >= 0.6
              else "partial_taxonomy_mismatch" if matches else "no_match")

    if coverage < 0.6 and allow_canonical:
        # Below this, the two maps describe different businesses and a partial
        # overlay values neither properly: on BABA the LLM produced "Alibaba
        # Quick Commerce (Food Delivery)" and no China-commerce unit, so the
        # filing's largest segment ($81.9B at 19.4%) had nowhere to land and
        # simply vanished from the SOTP.
        #
        # Rebuilding from the filing map is the honest fallback -- it values
        # every reported segment -- but it costs the multiple basis, because
        # filing names largely miss `classify_archetype` ("Alibaba China
        # E-commerce Group", "All others"). That degradation is reported, not
        # hidden, so a result produced this way is never mistaken for a clean
        # overlay.
        out = [{
            "name": s["name"],
            "revenue_fwd": s["revenue_fwd"],
            "ebit": s["ebit_fwd"],
            "source": "sec_segment_footnote",
            "evidence": (f"{anchor['form']} {anchor['accession']} "
                         f"{anchor['report_file']}, FY ending "
                         f"{anchor['period_end']}: {s['name']} "
                         f"{(s['margin'] or 0):.1%} reported margin"),
        } for s in anchor["segments"]]
        status = "filing_canonical"
        coverage = 1.0
        # `out` has been rebuilt, so the pre-replacement `taken` indices no
        # longer address it. Record which of the ORIGINAL segments were
        # displaced -- echoing the new list back would report filing names as
        # "unmatched note segments", which reads as a parser fault.
        displaced = [s.get("name") for s in segments]
        matches = [{"filing_name": s["name"], "matched_name": s["name"],
                    "rule": "canonical_replacement",
                    "revenue_fwd": s["revenue_fwd"], "ebit": s["ebit_fwd"],
                    "margin": s["margin"], "prev_revenue_fwd": None,
                    "prev_ebit_margin": None} for s in anchor["segments"]]
        unmatched = []

    detail = {
        "status": status, "coverage": coverage,
        "names_from_filing": status == "filing_canonical",
        "matches": matches, "unmatched_filing": unmatched,
        "unmatched_merged": (displaced if status == "filing_canonical"
                             else [s.get("name") for i, s in enumerate(out)
                                   if i not in taken]),
        "form": anchor.get("form"), "accession": anchor.get("accession"),
        "report_file": anchor.get("report_file"),
        "source_url": anchor.get("source_url"),
        "period_end": anchor.get("period_end"),
        "profit_metric": anchor.get("profit_metric"),
        "profit_is_gaap_operating_income":
            anchor.get("profit_is_gaap_operating_income"),
        "fx": anchor.get("fx"), "fx_source": anchor.get("fx_source"),
        "bridge": anchor.get("bridge"),
        "sum_vs_consolidated": anchor.get("sum_vs_consolidated"),
        "corporate_unallocated": anchor.get("corporate_unallocated"),
        "eliminations": anchor.get("eliminations"),
        "warnings": anchor.get("warnings") or [],
    }
    return out, detail
