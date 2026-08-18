from __future__ import annotations

"""Phase 7g — SOTP Assumption Extractor (GS-style SOTP input assembler)

Assumptions are sourced BY NATURE, cheapest-and-most-reliable first:

  1. Reported balance-sheet facts (net cash, shares, FX, and — when
     disclosed — associates/investments) come straight from FMP line items.
     No LLM in the loop: these are facts, not judgments.
  2. Segment revenue mix: FMP product segmentation when available
     (deterministic anchor, US-listed filers); otherwise a targeted research
     pass reconstructs the segment map.
  3. Segment economics (EBIT margins / unit economics such as daily orders x
     profit-per-order): targeted LLM research pass over deep-research
     sections, press releases and — when the user attached licensed
     documents — research-PDF evidence. Evidence citation mandatory.
  4. Multiples + rationale (P/E or EV/Rev per segment, justified by growth
     outlook and peer reference, sell-side style) and the judgment-side
     balance-sheet items (fair value of associates, holdco discount): a
     second, comparative LLM pass.
  5. Policy parameters (China-internet holdco discount 15%, default tax)
     come from a constant family table, overridable per run.

PDF ingestion (research/manifest.json, ai_input_allowed per document) is ONE
corroborating evidence source — not the pipeline. The output feeds the
"SOTP (analyst)" shadow method in dcf_agent via state["data"]["sotp_assumptions"].
"""

import json
import os
import re
import statistics

from src.graph.state import AgentState
from src.tools.api import (
    get_analyst_estimates,
    get_fx_rate,
    get_revenue_product_segmentation,
    search_line_items,
)
from src.agents.analysis.sotp_multiple_basis import (
    _fwd_multiples,
    apply_margin_basis,
    apply_multiple_basis,
    normalize_key,
)
from src.utils.llm import call_llm_vision
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state
from src.utils.research_pdf import load_pdf_evidence_for_ticker
from src.data.models import SOTPEconomicsOutput, SOTPMultiplesOutput

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# China internet megacaps: GS applies a 15% holdco discount "in-line with our
# holdco discount for China Internet". Same family default here.
_CHINA_INTERNET_TICKERS = {
    "BABA", "JD", "PDD", "TCEHY", "MPNGY", "BIDU", "NTES",
    "0700.HK", "9988.HK", "3690.HK", "9888.HK", "9618.HK",
}

_ECONOMICS_SYSTEM = """
You are a buy-side equity research analyst retrieving SEGMENT-LEVEL
ECONOMICS for a sum-of-the-parts valuation. For each major segment produce
forward (next fiscal year) revenue in USD and profitability as ONE of:
  - ebit (USD, when segment EBIT is directly reported/estimated),
  - ebit_margin (decimal, when only margins are substantiated),
  - unit_economics {volume_annual, profit_per_unit, fx_to_usd} when the
    business is best described by volume x profit-per-unit (food delivery
    orders, cloud vCPU-hours, marketplace GMV x take-rate style).
Rules:
  * If a segment revenue ANCHOR is provided (FMP reported segmentation),
    honor it: revenue_fwd = anchor grown by the growth rate you can
    substantiate; do not invent divergent bases.
  * Every segment needs an evidence sentence (management guidance, broker
    estimate, disclosed segment result). Omit segments you cannot
    substantiate.
  * default_tax_rate: statutory/effective rate for the group (0.15 typical
    for China internet).
Respond ONLY with valid JSON, no commentary, using exactly these keys:
{
  "segments": [
    {
      "name": "<segment name>",
      "revenue_fwd": <forward revenue in RAW USD DOLLARS, e.g. 23251000000 for $23.25bn — never millions>,
      "ebit": <forward EBIT in RAW USD DOLLARS, number or null>,
      "ebit_margin": <decimal or null>,
      "unit_economics": {"volume_annual": <absolute units per year, e.g. 24455000000 for 67mn orders/day x 365>, "profit_per_unit": <profit per unit in REPORTING currency, e.g. 1.1 RMB>, "fx_to_usd": <reporting-to-USD rate, e.g. 0.139 for RMB>} ,
      "tax_rate": <decimal or null>,
      "evidence": "<one-sentence source of these figures>"
    }
  ],
  "default_tax_rate": <decimal>,
  "confidence": "<low|medium|high>",
  "data_limitations": "<what could not be substantiated>"
}
ALL dollar amounts are RAW USD dollars (10 decimal-digit scale for
megacaps), never $-millions. Omit unit_economics entirely when not
applicable; use null (not 0) for unknown figures.
""".strip()

_MULTIPLES_SYSTEM = """
You are a sell-side valuation analyst assigning sum-of-the-parts MULTIPLES.
If the attached research evidence contains a published SOTP table for this
company, anchor on its per-segment multiples (cite them in rationale);
deviate only with explicit justification. Otherwise assign
pe_multiple (on NOPAT) and/or ev_rev_multiple
justified sell-side style: reference the segment's growth outlook and the
peer group / sector the multiple benchmarks against (e.g. "12x P/E
referencing single-digit revenue growth outlook", "25x P/E referencing 20%+
revenue growth"). Loss-making or pre-profit segments get ev_rev_multiple
only. Also provide, ONLY when substantiated:
  - associates_investments (USD fair value of listed/unlisted equity
    holdings the group does not consolidate). China-internet megacaps
    almost always carry material associate stakes that belong on the SOTP
    as an "Associates/investments" line — read that line from any attached
    research-PDF SOTP table, or estimate from disclosed stakes x market
    caps. Use null only when truly nothing is disclosed.
  - holdco_discount_pct (conglomerate/holding-company discount; China
    internet convention is 0.15).
Respond ONLY with valid JSON, no commentary, using exactly these keys:
{
  "segments": [
    {"name": "<segment name, matching pass 1>",
     "pe_multiple": <number or null>,
     "ev_rev_multiple": <number or null>,
     "rationale": "<growth outlook + peer reference>"}
  ],
  "associates_investments": <RAW USD DOLLARS number or null, e.g. 12339000000 for $12.3bn — never millions>,
  "associates_rationale": "<how the fair value was derived>",
  "holdco_discount_pct": <decimal, e.g. 0.15>,
  "holdco_rationale": "<why this discount>",
  "confidence": "<low|medium|high>",
  "data_limitations": "<what could not be substantiated>"
}
Use null (not 0) for unknown figures.
""".strip()


def _is_china_internet(ticker: str) -> bool:
    return (ticker or "").upper() in _CHINA_INTERNET_TICKERS


def _deterministic_skeleton(ticker: str, end_date: str, api_key) -> dict:
    """Reported facts from FMP line items — no LLM (nature: facts).

    Also returns total ``revenue`` (raw USD) as the scale anchor the units
    guard uses to detect when the LLM emitted $-millions instead of dollars.
    """
    skeleton: dict = {"net_cash": None, "associates": None, "shares": None,
                      "revenue": None}
    try:
        li = search_line_items(
            ticker,
            ["cash_and_equivalents", "total_debt", "short_term_investments",
             "long_term_investments", "equity_method_investments",
             "shares_outstanding", "revenue"],
            end_date, period="ttm", limit=1, api_key=api_key,
        )
    except Exception:
        li = []
    if li:
        row = li[0]
        # Reported currency → USD. FMP line items for HK/China names come back
        # in CNY (or local ccy); the SOTP engine assumes USD throughout.
        _ccy = (getattr(row, "currency", None) or "USD").upper()
        try:
            _ccy_fx = get_fx_rate(_ccy, "USD", api_key) if _ccy != "USD" else 1.0
        except Exception:
            _ccy_fx = 1.0
        if not _ccy_fx or _ccy_fx <= 0:
            _ccy_fx = 1.0
        cash = getattr(row, "cash_and_equivalents", None)
        sti = getattr(row, "short_term_investments", None) or 0.0
        debt = getattr(row, "total_debt", None)
        if cash is not None and debt is not None:
            skeleton["net_cash"] = (float(cash) + float(sti or 0) - float(debt)) * _ccy_fx
        assoc = getattr(row, "equity_method_investments", None) \
            or getattr(row, "long_term_investments", None)
        if assoc:
            skeleton["associates"] = float(assoc) * _ccy_fx
        shares = getattr(row, "shares_outstanding", None)
        if shares:
            skeleton["shares"] = float(shares)
        rev = getattr(row, "revenue", None)
        if rev:
            skeleton["revenue"] = float(rev) * _ccy_fx
        skeleton["reporting_currency"] = _ccy
    return skeleton


def _reconcile_research_segments(merged: list, rs_block: dict | None,
                                 group_revenue: float | None = None) -> list:
    """Reconcile the LLM merge against the researched 2A.5 block.

    The deep-research 2A.5 block is the PRIMARY researched source (raw USD,
    cited). Two failure modes it protects against:

    1. DROPPED researched segments (observed on degraded runs: 3 of 4
       Meituan segments survived the merge) — the block stands alone, so
       missing segments are appended verbatim.
    2. CANONICAL-MAP conflicts: when the researched block covers >=70% of
       the group's reported revenue, it IS the segment map — the merge is
       replaced wholesale by the researched segments. Non-matching merged
       segments (typically FMP product-line anchors under a different
       taxonomy than the researched map, e.g. GS geographic buckets vs FMP
       product lines for AMZN; a group-level single segment for MSFT) are
       double-count candidates; matching by normalized key cannot resolve
       abbreviation-vs-full-name pairs ("AWS" vs "Amazon Web Services"),
       so the researched map simply wins — it is complete (>=70% coverage)
       and already carries raw-USD revenues, economics and multiples.
       Partial research blocks (no group revenue, or covering <70%) keep
       the additive behavior.
    """
    if not rs_block or not rs_block.get("segments"):
        return merged
    rs_sum = sum(s.get("revenue_fwd") or 0 for s in rs_block["segments"])
    canonical = bool(group_revenue and group_revenue > 0
                     and rs_sum >= 0.7 * group_revenue)
    if canonical:
        return [dict(s) for s in rs_block["segments"]]

    def _matches(rs_key: str, keys: list) -> bool:
        return any(rs_key == h or rs_key in h or h in rs_key
                   for h in keys if h)

    have = [normalize_key(s.get("name", "")) for s in merged]
    for rs_seg in rs_block["segments"]:
        rs_key = normalize_key(rs_seg.get("name", ""))
        if not rs_key:
            continue
        if _matches(rs_key, have):
            continue
        merged.append(dict(rs_seg))
        have.append(rs_key)
    return merged


def _drop_aggregate_segments(segs: list) -> list:
    """Drop double-counted parent segments.

    LLMs sometimes emit an aggregate line AND its children (e.g. "Core
    local commerce" alongside Food Delivery / Instashopping / IHT), which
    double-counts NAV. Deterministic signature of a parent: its revenue is
    ≈ (±5%) the sum of SOME subset (≥2) of the other segments — tight on
    purpose: reported parent/child sets reconcile within ~4%, and looser
    bands false-positive on coincidental segment sums. Segment counts are
    small (≤8) so the subset sweep is cheap. Drop the parent, keep leaves.
    """
    if len(segs) <= 2:
        return segs
    from itertools import combinations
    revs = [(s.get("revenue_fwd") or 0) for s in segs]
    drop: set = set()
    for i, s in enumerate(segs):
        rev = revs[i]
        if rev <= 0:
            continue
        others = [r for j, r in enumerate(revs) if j != i and r > 0]
        is_parent = False
        for size in range(2, len(others) + 1):
            for combo in combinations(others, size):
                sub = sum(combo)
                if sub > 0 and abs(rev - sub) / sub <= 0.05:
                    is_parent = True
                    break
            if is_parent:
                break
        if is_parent:
            drop.add(i)
    # Never drop everything (all-equal segments would all match).
    kept = [s for i, s in enumerate(segs) if i not in drop]
    return kept or segs


def _units_scale_factor(segment_revs: list, anchor_revenue) -> float:
    """Detect whether the LLM emitted $-millions/$-billions instead of raw USD.

    Segment revenues should sum to roughly the group's total revenue (0.3x–3x
    once rounding / partial segment maps are allowed). Compare the segment sum
    against the raw-USD FMP revenue anchor and pick the 10^k factor that lands
    the ratio in that band. Returns 1.0 when no rescaling is warranted or no
    anchor exists.
    """
    segs = [r for r in segment_revs if r and r > 0]
    if not segs or not anchor_revenue or anchor_revenue <= 0:
        return 1.0
    seg_sum = sum(segs)
    if seg_sum <= 0:
        return 1.0
    ratio = anchor_revenue / seg_sum
    # Raw-USD output → ratio near 1. $-millions → ratio ~1e6. $-billions → 1e9.
    # Check the larger factor first — a billions ratio also clears the 3e5 bar.
    if ratio >= 3e8:      # segments ~1e9 too small
        return 1e9
    if ratio >= 3e5:      # segments ~1e6 too small
        return 1e6
    return 1.0


def _fmp_segment_anchor(ticker: str, end_date: str, api_key) -> list[dict]:
    """FMP product segmentation as deterministic revenue anchor (US filers)."""
    try:
        rows = get_revenue_product_segmentation(
            ticker, end_date, period="annual", api_key=api_key)
    except Exception:
        rows = []
    if not rows:
        return []
    latest = rows[-1]
    return [
        {"name": name, "revenue": rev, "period_end": latest["period_end"]}
        for name, rev in sorted(latest["segments"].items(), key=lambda kv: -kv[1])
    ]


def _fmp_estimates_anchor(ticker: str, end_date: str, api_key) -> dict | None:
    """Consensus forward estimates (revenue/EBIT/net income) — deterministic
    group-level anchor for pass 1. US-listed names only (FMP has no HK data);
    returns None when unavailable so the research pass carries the load."""
    try:
        # limit>=5: FMP truncates newest-first, so a small limit can exclude
        # the true Y+1 year (e.g. the current fiscal year still running).
        # limit=1 returns the FURTHEST year.
        ests = get_analyst_estimates(
            ticker, end_date, period="annual", limit=5, api_key=api_key)
    except Exception:
        return None
    if not ests:
        return None
    ests = sorted(ests, key=lambda e: e.period_end)
    # Y+1 = the first annual period ending strictly after the run date —
    # the forward-year anchor segment revenues should reconcile to.
    fwd = [e for e in ests if e.period_end > end_date]
    e = fwd[0] if fwd else ests[-1]
    return {
        "period_end": e.period_end,
        "revenue_avg": e.revenue_avg,
        "ebit_avg": e.ebit_avg,
        "ebitda_avg": e.ebitda_avg,
        "net_income_avg": e.net_income_avg,
    }


def _classify_multiple_ref(ref: str) -> tuple[float | None, float | None]:
    """Classify a ``multiple_ref`` string ("12x P/E …", "1.25x EV/Sales …")
    into ``(pe_multiple, ev_rev_multiple)`` — one-metric classification,
    shared by SEGMENT and SCENARIO parsing."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*x", ref or "")
    if not m:
        return None, None
    mult_val = float(m.group(1))
    ref_lower = ref.lower()
    if "ev/" in ref_lower or "ev-" in ref_lower or "sales" in ref_lower:
        return None, mult_val
    return mult_val, None


def _parse_research_sotp_block(text: str) -> dict | None:
    """Parse the deep-research SOTP_BLOCK (prompt section 2A.5).

    Nature-driven sourcing in action: the research layer DISCOVERS the SOTP
    variables (web search, citations) and the extractor ASSEMBLES them
    deterministically — no re-derivation, no PDF dependency. Returns the
    parsed dict or None when no block is present.
    """
    if not text:
        return None
    start = text.find("SOTP_BLOCK_START")
    end = text.find("SOTP_BLOCK_END")
    if start < 0 or end <= start:
        return None
    block = text[start + len("SOTP_BLOCK_START"):end]
    out: dict = {"segments": [], "associates_fair": None, "net_cash": None,
                 "holdco_pct": None, "tax_rate": None}

    def _num(kv: dict, key: str) -> float | None:
        v = str(kv.get(key, "")).split("(")[0]
        v = re.sub(r"(?i)usd|bn|b\b|\$|,", "", v).strip()
        if v.upper() in ("UNKNOWN", "NA", "N/A", ""):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    for raw in block.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        head = line.split("|", 1)[0].strip().upper()
        kv = {}
        for part in line.split("|")[1:]:
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        if head == "SEGMENT":
            seg: dict = {
                "name": kv.get("name", "").split("(Source")[0].strip(),
                "revenue_fwd": None, "ebit_margin": None,
                "pe_multiple": None, "ev_rev_multiple": None,
                "rationale": kv.get("multiple_ref", ""),
                "evidence": kv.get("unit_economics", ""),
                "source": "deep_research_2a5",
            }
            rev_bn = _num(kv, "rev_fwd_usd_bn")
            if rev_bn:
                seg["revenue_fwd"] = rev_bn * 1e9
            margin = _num(kv, "ebit_margin_pct")
            if margin is not None:
                seg["ebit_margin"] = margin / 100.0
            pe_m, evrev_m = _classify_multiple_ref(kv.get("multiple_ref", ""))
            seg["pe_multiple"] = pe_m
            seg["ev_rev_multiple"] = evrev_m
            if seg["name"] and seg["revenue_fwd"]:
                out["segments"].append(seg)
        elif head == "ASSOCIATES":
            v = _num(kv, "fair_value_usd_bn")
            if v:
                out["associates_fair"] = v * 1e9
        elif head == "NET_CASH":
            v = _num(kv, "usd_bn")
            if v is not None:
                out["net_cash"] = v * 1e9
        elif head == "HOLDCO_DISCOUNT":
            v = _num(kv, "pct")
            if v is not None:
                out["holdco_pct"] = v / 100.0
        elif head == "TAX_RATE":
            v = _num(kv, "pct")
            if v is not None:
                out["tax_rate"] = v / 100.0
        elif head == "SCENARIO":
            # Tier 3.8 — optional bear/bull per-segment multiples. Two forms:
            #   SCENARIO | case=bear | multiples=A:12x P/E; B:1.0x EV/Sales
            #   SCENARIO | case=bull | segment=A | multiple_ref=14x P/E ...
            case = (kv.get("case") or "").strip().lower()
            if case in ("bear", "bull"):
                scen = out.setdefault("scenarios", {}).setdefault(case, [])
                if kv.get("multiples"):
                    for pair in kv["multiples"].split(";"):
                        if ":" not in pair:
                            continue
                        sname, sref = pair.split(":", 1)
                        sname = sname.split("(Source")[0].strip()
                        pe_m, evrev_m = _classify_multiple_ref(sref)
                        if not sname or (pe_m is None and evrev_m is None):
                            continue
                        scen.append({"name": sname, "pe_multiple": pe_m,
                                     "ev_rev_multiple": evrev_m,
                                     "rationale": sref.strip()})
                elif kv.get("segment"):
                    sname = kv["segment"].split("(Source")[0].strip()
                    pe_m, evrev_m = _classify_multiple_ref(
                        kv.get("multiple_ref", ""))
                    if sname and (pe_m is not None or evrev_m is not None):
                        scen.append({"name": sname, "pe_multiple": pe_m,
                                     "ev_rev_multiple": evrev_m,
                                     "rationale": kv.get("multiple_ref", "")})
    if not any([out["segments"], out["associates_fair"], out["net_cash"],
                out["holdco_pct"], out["tax_rate"], out.get("scenarios")]):
        return None
    return out


def run_sotp_extractor(state: AgentState) -> AgentState:
    """Assemble GS-style SOTP assumptions per ticker, by assumption nature."""
    agent_id = "sotp_extractor"
    data = state["data"]
    tickers = data["tickers"]
    end_date = data["end_date"]
    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")

    enabled = bool(data.get("sotp_enabled"))
    results: dict = {}

    for ticker in tickers:
        pdf_evidence = load_pdf_evidence_for_ticker(_REPO_ROOT, ticker)
        if not enabled and not pdf_evidence:
            continue  # zero-cost on ordinary runs unless opted in

        progress.update_status(agent_id, ticker, "Building deterministic skeleton")
        skeleton = _deterministic_skeleton(ticker, end_date, api_key)
        anchors = _fmp_segment_anchor(ticker, end_date, api_key)
        fwd_est = _fmp_estimates_anchor(ticker, end_date, api_key)

        dr_sections = data.get("deep_research_sections", {}) or {}
        research_ctx = "\n\n".join(filter(None, [
            dr_sections.get("2a", ""), dr_sections.get("2b", ""),
            dr_sections.get("2f", ""), (data.get("deep_research", "") or "")[:3000],
        ]))[:8000]
        industry_brief = (data.get("industry_brief", "") or "")[:6000]
        pdf_text = (pdf_evidence or {}).get("text", "")[:24000]
        pdf_images = (pdf_evidence or {}).get("table_images", [])

        # Research-layer SOTP variable block (deep research 2A.5): the
        # PRIMARY researched source — PDF evidence only corroborates.
        rs_block = _parse_research_sotp_block("\n".join(filter(None, [
            dr_sections.get("2a", ""),
            data.get("deep_research", ""), research_ctx])))
        rs_block_txt = ""
        if rs_block:
            _seg_lines = "; ".join(
                f"{s['name']}: rev ${s['revenue_fwd']/1e9:.1f}B"
                + (f", EBIT margin {s['ebit_margin']:.0%}" if s.get("ebit_margin") else "")
                + (f", P/E {s['pe_multiple']}x" if s.get("pe_multiple") else "")
                + (f", EV/Rev {s['ev_rev_multiple']}x" if s.get("ev_rev_multiple") else "")
                for s in rs_block["segments"])
            rs_block_txt = (
                "=== RESEARCH-SOURCED SOTP VARIABLES (PRIMARY — already "
                "researched and cited by the deep-research pass; anchor on "
                "these rather than re-deriving different figures) ===\n"
                f"Segments: {_seg_lines}\n"
                + (f"Associates/investments fair value: ${rs_block['associates_fair']/1e9:.1f}B\n"
                   if rs_block.get("associates_fair") else "")
                + (f"Net cash: ${rs_block['net_cash']/1e9:.1f}B\n"
                   if rs_block.get("net_cash") is not None else "")
                + (f"Holdco discount: {rs_block['holdco_pct']:.0%}\n"
                   if rs_block.get("holdco_pct") is not None else "")
                + (f"Group tax rate: {rs_block['tax_rate']:.0%}\n"
                   if rs_block.get("tax_rate") is not None else "")
            )

        anchor_txt = "\n".join(
            f"- {a['name']}: ${a['revenue']/1e9:.2f}B (FY {a['period_end']})"
            for a in anchors
        ) or "Not available (no FMP segmentation for this listing) — reconstruct the segment map from research."

        if fwd_est:
            _fwd_txt = (
                f"FMP analyst consensus for FY ending {fwd_est['period_end']} "
                f"(deterministic anchor — segment revenues must sum to within "
                f"~10% of consensus revenue):\n"
                f"- Revenue: ${fwd_est['revenue_avg']/1e9:,.1f}B\n"
                + (f"- EBIT: ${fwd_est['ebit_avg']/1e9:,.1f}B\n"
                   if fwd_est.get("ebit_avg") else "")
                + (f"- EBITDA: ${fwd_est['ebitda_avg']/1e9:,.1f}B\n"
                   if fwd_est.get("ebitda_avg") else "")
                + (f"- Net income: ${fwd_est['net_income_avg']/1e9:,.1f}B\n"
                   if fwd_est.get("net_income_avg") else "")
            )
        else:
            _fwd_txt = ("Not available for this listing (HK names have no FMP "
                        "consensus data) — substantiate group forward revenue "
                        "from research and state it explicitly.")

        # M1 self-improvement: sotp_extractor lessons distilled from past
        # extraction misses (src/memory/agent_lessons). Pure prompt-append;
        # the deterministic cross-checks below are unaffected. [] when the
        # kill switch is off or nothing is stored.
        try:
            from src.memory.agent_lessons import get_active_lessons
            _sotp_lessons = get_active_lessons("sotp_extractor")
        except Exception:
            _sotp_lessons = []
        _lessons_txt = ""
        if _sotp_lessons:
            _lessons_txt = (
                "\n\nPast misses to avoid (post-mortems of prior extraction errors):\n"
                + "\n".join(f"- {l}" for l in _sotp_lessons)
            )

        progress.update_status(agent_id, ticker, "Pass 1: segment economics")
        econ: SOTPEconomicsOutput = call_llm_vision(
            system_text=_ECONOMICS_SYSTEM + _lessons_txt,
            human_text=(
                f"Ticker: {ticker}\nSector: {data.get('sector', '')}\n\n"
                f"{rs_block_txt}\n"
                f"=== Reported segment revenue anchors (FMP, latest FY) ===\n{anchor_txt}\n\n"
                f"=== Consensus forward estimates ===\n{_fwd_txt}\n\n"
                f"=== Deep research excerpts ===\n{research_ctx or 'N/A'}\n\n"
                f"=== Industry brief ===\n{industry_brief or 'N/A'}\n\n"
                f"=== Attached research PDF text (licensed evidence) ===\n{pdf_text or 'N/A'}\n"
            ),
            images=pdf_images,
            pydantic_model=SOTPEconomicsOutput,
            agent_name=agent_id,
            state=state,
            default_factory=SOTPEconomicsOutput,
            max_tokens=8000,
            temperature=0.1,
        )

        _seg_names = [s.name for s in econ.segments if s.name]
        progress.update_status(agent_id, ticker, "Pass 2: multiples & add-ons")
        mult: SOTPMultiplesOutput = call_llm_vision(
            system_text=_MULTIPLES_SYSTEM + _lessons_txt,
            human_text=(
                f"Ticker: {ticker}\nSector: {data.get('sector', '')}\n\n"
                f"{rs_block_txt}\n"
                f"SEGMENT LIST (you MUST output exactly one entry per name "
                f"below, using these EXACT names, no extras, no omissions):\n"
                f"{json.dumps([{'name': s.name, 'revenue_fwd': s.revenue_fwd} for s in econ.segments])}\n\n"
                f"=== Deep research excerpts ===\n{research_ctx or 'N/A'}\n\n"
                f"=== Attached research PDF text (licensed evidence) ===\n{pdf_text or 'N/A'}\n"
            ),
            images=pdf_images,
            pydantic_model=SOTPMultiplesOutput,
            agent_name=agent_id,
            state=state,
            default_factory=SOTPMultiplesOutput,
            max_tokens=6000,
            temperature=0.1,
        )

        # ── Merge by nature: economics (pass 1) + multiples (pass 2) ─────
        mult_by_name: dict = {}
        for m in mult.segments:
            _k = "".join(c for c in (m.name or "").lower() if c.isalnum())
            if _k:
                mult_by_name[_k] = m
        _used: set = set()
        _unmatched_idx: list = []
        merged_segments = []
        for _idx, seg in enumerate(econ.segments):
            key = "".join(c for c in (seg.name or "").lower() if c.isalnum())
            m = mult_by_name.get(key)
            if m is not None:
                _used.add(key)
            else:  # normalized substring fallback
                for k, v in mult_by_name.items():
                    if k not in _used and (k in key or key in k):
                        m = v
                        _used.add(k)
                        break
            entry = seg.model_dump()
            if m:
                entry["pe_multiple"] = m.pe_multiple if m.pe_multiple is not None else entry.get("pe_multiple")
                entry["ev_rev_multiple"] = m.ev_rev_multiple if m.ev_rev_multiple is not None else entry.get("ev_rev_multiple")
                entry["rationale"] = m.rationale or entry.get("rationale", "")
                entry["source"] = "fmp_anchor+research_llm" if anchors else "research_llm"
            else:
                _unmatched_idx.append(_idx)
            merged_segments.append(entry)
        # Order-based last resort: pass-2 names drifted despite the lock.
        # Pair the leftover pass-2 entries with the unmatched pass-1 segments
        # in order — keeps LLM multiples from silently collapsing to the
        # keyword fallback (observed across GOOGL/AMZN test runs).
        if _unmatched_idx:
            _leftover = [m for k, m in mult_by_name.items() if k not in _used]
            if len(_leftover) == len(_unmatched_idx):
                for _idx, m in zip(_unmatched_idx, _leftover):
                    entry = merged_segments[_idx]
                    entry["pe_multiple"] = m.pe_multiple if m.pe_multiple is not None else entry.get("pe_multiple")
                    entry["ev_rev_multiple"] = m.ev_rev_multiple if m.ev_rev_multiple is not None else entry.get("ev_rev_multiple")
                    entry["rationale"] = m.rationale or entry.get("rationale", "")
                    entry["source"] = "fmp_anchor+research_llm" if anchors else "research_llm"

        # Research block as wholesale fallback: when the LLM passes degrade
        # to synthetic defaults, the researched variables still stand alone
        # (already raw USD, cited) — no re-derivation needed.
        if not merged_segments and rs_block and rs_block["segments"]:
            merged_segments = [dict(s) for s in rs_block["segments"]]

        # Research-block reconciliation: when the LLM passes DROP a segment
        # the researched 2A.5 block states (already raw USD, cited), add it
        # back — the block is the primary researched source. When the block
        # covers >=70% of reported group revenue it is the canonical map and
        # replaces the conflicting merge wholesale (double-count protection).
        # Runs before the aggregate guard so parent/child detection still
        # sees the reconciled set.
        merged_segments = _reconcile_research_segments(
            merged_segments, rs_block, group_revenue=skeleton["revenue"])

        # Deterministic double-count guard (parent + children emitted together).
        merged_segments = _drop_aggregate_segments(merged_segments)

        # ── Basis layer (independent of any sell-side report), margin FIRST
        #    so profitability classification sees model-filled margins:
        #    1. margin model fills ebit_margin where no researched
        #       margin/EBIT/unit economics exists (margin_model_v1);
        #    2. multiples: research-note (2A.5) override with flagged
        #       rationale; then the learned characteristic model
        #       (segment_calibration_v1.json) when it covers the archetype
        #       (multiple_model_v1); else comp-derived multiples when >=3
        #       pure-play comps carry the metric (one metric per segment —
        #       the engine's max() rule arbitrages if both are given);
        #       LLM/report multiples become cross-checks only (z-score vs
        #       the model CI or divergence >25% vs comp basis flags). ─────
        _grp_fwd = _fwd_multiples(ticker, end_date)
        _group_g_pct = (_grp_fwd or {}).get("g_pct")
        merged_segments, margin_detail = apply_margin_basis(
            merged_segments, (rs_block or {}).get("segments") or [],
            china=_is_china_internet(ticker),
            group_g_pct=_group_g_pct)
        merged_segments, basis_detail = apply_multiple_basis(
            merged_segments,
            (rs_block or {}).get("segments") or [],
            ticker=ticker, end_date=end_date,
            china=_is_china_internet(ticker),
            group_g_pct=_group_g_pct,
            group_revenue=skeleton["revenue"],
        )

        # ── Units guard: LLM may emit $-millions/$-billions; engine wants raw
        #    USD. Detect via the raw-USD FMP revenue anchor and rescale. ────
        _scale = _units_scale_factor(
            [s.get("revenue_fwd") for s in merged_segments],
            skeleton["revenue"])
        if _scale != 1.0:
            for s in merged_segments:
                if s.get("revenue_fwd"):
                    s["revenue_fwd"] = s["revenue_fwd"] * _scale
                if s.get("ebit"):
                    s["ebit"] = s["ebit"] * _scale
            # LLM-sourced associates share the same output's unit convention.
            if mult.associates_investments:
                mult.associates_investments = mult.associates_investments * _scale

        # ── Associates/investments: sell-side marks these at FAIR value, not
        #    balance-sheet book (Tencent's stakes carry at a fraction of
        #    market value). Take the max over all sourced candidates — book
        #    value is a floor, never the estimate. ──────────────────────────
        _assoc_src_map = [(skeleton["associates"], "fmp_book"),
                          (mult.associates_investments, "llm_fair"),
                          ((rs_block or {}).get("associates_fair"), "research_2a5")]
        _assoc_cands = [(v, lbl) for v, lbl in _assoc_src_map if v]
        if _assoc_cands:
            associates_val = max(_assoc_cands, key=lambda t: t[0])[0]
            assoc_source = f"max({'+'.join(lbl for _, lbl in _assoc_cands)})"
        else:
            associates_val, assoc_source = None, "none"

        # Holdco / tax / net cash: research block pins when present (it is the
        # researched variable), then LLM judgment, then policy defaults.
        # None checks, not truthiness: 0.0 is a valid researched value
        # (AMZN EV/EBIT replication carries tax 0; US names carry holdco 0).
        holdco = (rs_block or {}).get("holdco_pct")
        if holdco is None:
            holdco = (mult.holdco_discount_pct
                      if mult.holdco_discount_pct is not None
                      else (0.15 if _is_china_internet(ticker) else 0.0))
        _net_cash = (skeleton["net_cash"] if skeleton["net_cash"] is not None
                     else (rs_block or {}).get("net_cash"))
        _tax = (rs_block or {}).get("tax_rate")
        if _tax is None:
            _tax = (econ.default_tax_rate
                    if econ.default_tax_rate is not None else 0.15)
        # Segment-map consistency flag vs the reported revenue anchor.
        _consistency = ""
        _seg_sum = sum(s.get("revenue_fwd") or 0 for s in merged_segments)
        if skeleton["revenue"] and _seg_sum:
            _ratio_cs = _seg_sum / skeleton["revenue"]
            if not (0.5 <= _ratio_cs <= 1.6):
                _consistency = (
                    f"segment revenue sum ${_seg_sum/1e9:.1f}B is "
                    f"{_ratio_cs:.2f}x group TTM revenue "
                    f"${skeleton['revenue']/1e9:.1f}B — check segment map")
        assumptions = {
            "segments": merged_segments,
            "default_tax_rate": _tax,
            "holdco_discount_pct": holdco,
            "associates_investments": associates_val,
            "net_cash": _net_cash,
            "_multiple_basis": basis_detail,
            "_margin_basis": margin_detail,
            "_shares": skeleton["shares"],
            "_fwd_estimates": fwd_est,
            # Tier 3.8: optional bear/bull per-segment multiple overrides
            # from the 2A.5 SCENARIO lines; None when the research block
            # carried none. Consumed by sotp_report_extras.sotp_scenario_tps.
            "_scenarios": (rs_block or {}).get("scenarios"),
            "_sources": {
                "net_cash": "fmp_line_items" if skeleton["net_cash"] is not None else (
                    "deep_research_2a5" if (rs_block or {}).get("net_cash") is not None else "none"),
                "associates": assoc_source,
                "segment_revenues": "fmp_anchor" if anchors else (
                    "deep_research_2a5" if (rs_block and rs_block["segments"]) else "research_llm"),
                "forward_estimates": "fmp_consensus" if fwd_est else "none",
                "research_block": "deep_research_2a5" if rs_block else "none",
                "economics": "research_llm" + ("+pdf_evidence" if pdf_evidence else ""),
                "multiples": "research_llm" + ("+pdf_evidence" if pdf_evidence else ""),
                "holdco": "research_llm" if mult.holdco_discount_pct else "policy_default",
                "multiple_basis": basis_detail.get("summary", "none"),
                "margin_basis": margin_detail.get("summary", "none"),
                "units_scale_applied": _scale,
            },
            "_confidence": {"economics": econ.confidence, "multiples": mult.confidence},
            "_data_limitations": " | ".join(filter(None, [
                econ.data_limitations, mult.data_limitations, _consistency])),
        }
        results[ticker] = assumptions
        progress.update_status(
            agent_id, ticker, "Done",
            analysis=json.dumps({
                "segments": len(merged_segments),
                "holdco": holdco,
                "sources": assumptions["_sources"],
            }))

    data["sotp_assumptions"] = results
    progress.update_status(agent_id, None, "Done")
    return state
