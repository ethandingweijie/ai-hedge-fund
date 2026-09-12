"""Market-agnostic segment normalisation.

Where a segment map comes from differs per market -- SEC rendered XBRL for
US filers, an HKEXnews annual-report PDF for Hong Kong, and SGX filings after
that. What has to happen to it afterwards does not:

  * decide whether the split is by BUSINESS LINE or by GEOGRAPHY, because a
    SOTP of places is not a SOTP of businesses;
  * collapse hierarchy parents that would otherwise be counted twice with
    their own children;
  * reject a map that does not reconcile to group revenue.

Keeping that here means a new market implements only a PARSER -- the judgement
about what makes a valid segment map is written once and shared, so SGX cannot
quietly acquire different rules from HKEX.

The provider contract
---------------------
A market provider exposes `get_segment_footnote(ticker, end_date) -> dict|None`
returning:

    {"segments": [{"name", "revenue", "profit", "margin", "assets", "member"}],
     "consolidated_revenue": float|None,
     "profit_label": str|None,      # the filer's OWN measure -- see below
     "reported_currency": str|None,
     "period_end": str, "segment_axis": "business_line"|"geographic",
     "profit_disclosed": bool, "assets_disclosed": bool,
     "source_url": str, "warnings": [str]}

`profit_label` is load-bearing and must never be normalised by a parser. IFRS 8
and ASC 280 report whatever the CODM reviews: Tencent's segment measure is
GROSS PROFIT, AIA's is operating profit, Alibaba's is adjusted EBITA. An engine
that computes `ebit x (1-tax) x multiple` on gross profit overstates
enormously, so the label travels with the number and the consumer decides.
"""
from __future__ import annotations

import itertools
import re
from typing import Optional


def normalize_key(name: str) -> str:
    """Loose name key for matching one segment across two tables or sources."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_GEO_AXIS_RE = re.compile(r"GeographicalAxis|GeographicAreas", re.I)
_GEO_NAME_RE = re.compile(
    r"^(americas|europe|emea|apjc|apac|asia|japan|china|greater china|united states|u\.s\.|us|canada|international|other international|rest of |north america|latin america|emerging markets|domestic|foreign)", re.I)

# Tables that disaggregate revenue by product or business line. ASC 606
# requires the split but NOT segment profit, so these are revenue-only --
# which is exactly why the reportable-segment table gets picked first.
_BUSINESS_LINE_RE = re.compile(
    r"disaggregat|by product|product category|item category|net revenue for group|revenue from contract", re.I)


def _is_geographic(segments: list[dict]) -> bool:
    """Whether a segment set describes places rather than businesses.

    A sum-of-the-parts values lines of business: you cannot apply a different
    multiple to "Americas" than to "Europe" for the same company, so a SOTP
    built on geography produces a number that is not a valuation. Apple,
    Cisco and Costco all report GEOGRAPHIC operating segments -- that is
    genuinely their ASC 280 disclosure -- so the geography has to be detected
    rather than assumed away.
    """
    if not segments:
        return False
    geo = 0
    for seg in segments:
        member = seg.get("member") or ""
        name = (seg.get("name") or "").strip()
        if _GEO_AXIS_RE.search(member) or _GEO_NAME_RE.match(name):
            geo += 1
    # Unanimity, not a majority. AMZN reports North America / International /
    # AWS -- two geographies and one business -- and the street values it
    # exactly that way, because AWS is a genuinely separable business with
    # its own multiple. A majority rule would reject that split. Only a set
    # where EVERY member is a place is unusable for a SOTP.
    return geo == len(segments)


def _drop_hierarchy_parents(segments: list[dict],
                           consolidated: Optional[float]) -> list[dict]:
    """Reduce overlapping revenue cuts to the finest set that adds up.

    A disaggregation table often carries several views of the SAME revenue.
    CSCO's prints three at once:

        Product 41.6 + Services 15.1                       = 56.7  (2 rows)
        Networking 28.3 + Security 8.1 + Collab 4.2
            + Observability 1.1                            = Product
        Subscription 31.5 = Subscription,Product 17.8
            + Subscription,Service 13.7                    (an orthogonal cut)

    Counting them together triples the revenue. This is not a tree, so removing
    "parents" cannot express it -- the Subscription rows are a cross-cut, not
    children. The general statement is simpler: pick the subset of rows that
    sums to consolidated revenue, preferring the one with the MOST members
    because that is the finest genuine split.

    The reconciliation requirement is also the safety net. An earlier
    value-matching version deleted AMD's Datacenter -- its largest segment --
    on a coincidental subset sum. Here AMD's rows reach only 0.42x group
    revenue, so no subset qualifies and the whole set is returned untouched.
    """
    if not consolidated or consolidated <= 0 or len(segments) < 3:
        return segments
    revs = [s.get("revenue") or 0.0 for s in segments]
    tol = 0.02 * consolidated
    if abs(sum(revs) - consolidated) <= tol:
        return segments                       # already a clean split
    if len(segments) > 16:                    # keep the search bounded
        return segments

    import itertools

    def _redundant(value: float, pool: list[float]) -> bool:
        """Is `value` just a sum of rows we are keeping?"""
        return any(abs(sum(c) - value) <= max(0.01 * value, 1.0)
                   for k in range(2, min(len(pool), 8) + 1)
                   for c in itertools.combinations(pool, k))

    # Reconciling on value alone over-fits: it will happily drop Intel Foundry
    # or Berkshire's BNSF to force a fit, and those are real businesses. Every
    # dropped row must ALSO be provably redundant -- equal to a sum of the rows
    # being kept -- which is what distinguishes an aggregate ("Net Sales",
    # "Product") from a segment that merely happens to make the arithmetic work.
    best: Optional[tuple] = None
    for mask in range(1, 1 << len(segments)):
        idx = [i for i in range(len(segments)) if mask & (1 << i)]
        if len(idx) < 2:
            continue
        if abs(sum(revs[i] for i in idx) - consolidated) > tol:
            continue
        kept_revs = [revs[i] for i in idx]
        if not all(_redundant(revs[j], kept_revs)
                   for j in range(len(segments)) if j not in idx):
            continue
        if best is None or len(idx) > len(best):
            best = tuple(idx)
    if best is None:
        return segments

    dropped = [segments[i].get("name") for i in range(len(segments))
               if i not in best]
    if dropped:
        print(f"  [sec_segments] overlapping revenue cuts: keeping the "
              f"{len(best)}-way split, dropping {dropped}")
    return [segments[i] for i in best]


def _filter_segments(segments: list[dict],
                     consolidated: Optional[float] = None) -> list[dict]:
    """Keep one reporting axis and drop subtotals.

    Two contaminations show up in real filings and both inflate the revenue
    sum, which then propagates into every downstream mix and margin:

    * MIXED AXES -- AVGO's table carries two business segments on
      `StatementBusinessSegmentsAxis` AND five geographies on
      `srt_StatementGeographicalAxis`. Both describe the same revenue, so the
      sum lands at exactly 2.000x consolidated. Business segments win: a SOTP
      values lines of business, not countries.
    * SUBTOTALS -- INTC reports "Total Intel Products" ($49.1B) alongside its
      components CCG ($32.2B) and DCAI ($16.9B). It sits on the same axis as a
      real segment and is only identifiable by being the sum of its siblings.
    """
    if len(segments) < 2:
        return segments

    def axis(seg: dict) -> str:
        return (seg.get("member") or "").split("=")[0]

    by_axis: dict[str, list[dict]] = {}
    for seg in segments:
        by_axis.setdefault(axis(seg), []).append(seg)
    if len(by_axis) > 1:
        for preferred in ("us-gaap_StatementBusinessSegmentsAxis",
                          "srt_ConsolidationItemsAxis"):
            if len(by_axis.get(preferred, [])) >= 2:
                segments = by_axis[preferred]
                break
        else:
            segments = max(by_axis.values(), key=len)

    # A subtotal must BOTH be named like one and add up like one. Value alone
    # is not enough: across five or more segments some subset almost always
    # sums to another within tolerance, and on AMD that false positive deleted
    # Datacenter -- the largest segment -- leaving a map covering 42% of
    # revenue. Requiring the name keeps INTC's "Total Intel Products" while
    # leaving genuine segments alone.
    import itertools
    kept: list[dict] = []
    for i, seg in enumerate(segments):
        rev = seg.get("revenue") or 0.0
        name = (seg.get("name") or "").lower()
        looks_like_subtotal = bool(re.search(r"\btotal\b|\bsubtotal\b", name))
        if not looks_like_subtotal or rev <= 0:
            kept.append(seg)
            continue
        others = [s.get("revenue") or 0.0 for j, s in enumerate(segments) if j != i]
        adds_up = any(
            abs(sum(c) - rev) <= 0.01 * rev
            for k in range(2, min(len(others), 6) + 1)
            for c in itertools.combinations(others, k))
        if adds_up:
            print(f"  [sec_segments] dropping subtotal segment "
                  f"{seg.get('name')!r} (= sum of siblings)")
            continue
        kept.append(seg)
    kept = kept if len(kept) >= 2 else segments
    return _drop_hierarchy_parents(kept, consolidated)


# ── Reported segment profit -> operating profit ─────────────────────────────

def normalize_segment_profit(segments: list[dict],
                             group_operating_profit: Optional[float],
                             *, weights: Optional[dict] = None,
                             reported_label: Optional[str] = None
                             ) -> Optional[dict]:
    """Convert whatever the filer reports per segment into operating profit.

    ASC 280 and IFRS 8 report whatever the CODM reviews, and it is usually not
    EBIT: Tencent's segment measure is GROSS PROFIT, Alibaba's ADJUSTED EBITA,
    Kingboard's "segment results", Cathay's "segment profit before
    non-recurring items". The engine computes `ebit x (1-tax) x multiple` on
    whatever it is handed, so passing any of those through unconverted
    overstates -- Tencent's segment gross margins run near 60% against a group
    operating margin around 32%.

    The conversion needs exactly ONE outside number, the group's reported
    operating profit, because the gap is whatever stands between the sum of
    the segment measure and that total:

        central cost = sum(segment reported profit) - group operating profit

    Stating it that way means the rule does not have to KNOW which measure the
    filer used. It is the same arithmetic for gross profit, adjusted EBITA and
    a bespoke "segment result", and it reconciles exactly by construction.

    The split is pro-rata to revenue by default. That is an assumption and it
    is labelled as one: filers that report a pre-opex measure say explicitly
    that the costs are managed centrally and NOT allocated -- Tencent's note
    reads "selling and marketing and administrative expenses ... are managed
    centrally ... therefore, they are not included in the measure of segment
    performance" -- so there is nothing to extract and any split is ours.
    `weights` overrides it where a better one exists (a sell-side model's
    segment margins); partial weights fall back rather than half-apply.

    Reported figures are never overwritten. A derived number that cannot be
    traced back to what the filer actually said is worse than no number.
    """
    if group_operating_profit is None:
        return None
    priced = [s for s in segments if s.get("profit") is not None]
    if len(priced) < 2:
        return None
    seg_rev = sum(s["revenue"] for s in segments if s.get("revenue"))
    if seg_rev <= 0:
        return None

    reported_total = sum(s["profit"] for s in priced)
    central = reported_total - group_operating_profit

    basis = "pro_rata_revenue"
    shares = None
    if weights:
        picked = {s["name"]: float(weights[s["name"]]) for s in segments
                  if s.get("name") in weights
                  and weights.get(s["name"]) is not None}
        if len(picked) == len(segments) and sum(picked.values()) > 0:
            total_w = sum(picked.values())
            shares = {k: v / total_w for k, v in picked.items()}
            basis = "supplied_weights"

    for s in segments:
        s["reported_profit"] = s.get("profit")
        s["reported_margin"] = s.get("margin")
        if s.get("profit") is None:
            s["operating_profit"] = None
            continue
        share = (shares[s["name"]] if shares
                 else (s["revenue"] / seg_rev) if s.get("revenue") else 0.0)
        s["operating_profit"] = s["profit"] - central * share
        s["profit"] = s["operating_profit"]
        s["margin"] = (s["operating_profit"] / s["revenue"]
                       if s.get("revenue") else None)
    return {
        "central_cost": central,
        "reported_segment_total": reported_total,
        "group_operating_profit": group_operating_profit,
        "reported_measure": reported_label,
        "basis": basis,
    }
