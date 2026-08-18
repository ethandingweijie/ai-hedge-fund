from __future__ import annotations

"""Phase 7h — Independent multiple basis for GS-style SOTP segments.

Segment multiples must NOT rest on a sell-side report or LLM judgment alone.
This module derives them from observable market data (all inputs live FMP,
no research-report content):

  segment multiple = median(forward multiple of pure-play listed comps)
                     x jurisdiction haircut (China-tech vs US-tech reported
                       TTM P/E ratio)
                     x growth adjustment (elasticity 0.5 on g_seg / g_comp),
                     capped at the comp p75. IQR = confidence band.

Tiers:
  Tier 1  ``comp_basis``    — >= MIN_COMPS pure-play comps carry the metric.
  Tier 2  ``thin_comps``    — fewer usable comps: keep the LLM/policy
                              multiple, flag for review.
  Tier 3  engine policy     — ``_classify_segment`` keyword table (floor).

Rules (established 2026-08-16, validated vs GS Exhibit 17 with no GS input:
Food Delivery 13.0 vs 12, Instashopping 19.1 vs 25, IHT 8.7 vs 10,
New Initiatives 1.3 EV/Rev vs 1.3):

  * ONE metric per segment — profitable segments take forward P/E only,
    loss-making segments take forward EV/Rev only. The engine's
    max(P/E-path, EV/Rev-path) rule arbitrages if both are given.
  * Jurisdiction haircut uses REPORTED TTM P/E on both sides. FMP consensus
    P/E for China ADRs is mis-scaled garbage and is never used here.
  * Research-note (deep research 2A.5) multiples override the basis with
    flagged rationale; LLM/report multiples are cross-checks only —
    divergence > MAX_DIVERGENCE vs the derived basis raises a review flag.
"""

import math
import statistics

from src.agents.analysis.multiple_learner import (
    load_artifact as _load_artifact,
    predict as _model_predict,
)
from src.tools.api import (
    get_analyst_estimates,
    get_market_cap,
    search_line_items,
)

# ── Policy constants ──────────────────────────────────────────────────────────
MIN_COMPS = 3                   # Tier 1 needs >=3 comps on the chosen metric
GROWTH_ELASTICITY = 0.5         # growth-adj elasticity on (g_seg / g_comp)
GROWTH_RATIO_CLAMP = (0.5, 2.0)
EVREV_HAIRCUT_FLOOR = 0.8       # EV/Rev is less jurisdiction-sensitive
MAX_DIVERGENCE = 0.25           # applied-vs-derived cross-check flag level
DEFAULT_CN_HAIRCUT = 0.60       # measured 2026-08-16: CN 16.8x vs US 28.1x
_TTM_PE_CAP = 200.0             # reject garbage TTM P/E values

# Jurisdiction haircut inputs: China-tech ADRs vs US mega-cap tech, same
# metric (reported TTM P/E) on both sides. Never FMP consensus P/E.
CN_PEERS = ["BABA", "JD", "PDD", "BIDU"]
US_PEERS = ["MSFT", "GOOGL", "AMZN", "META"]

# Segment archetypes -> pure-play listed comps (FMP-covered; China ADRs
# excluded from comp sets — their FMP consensus data is mis-scaled).
# ``metric`` (optional) forces EV/Rev for structurally loss-making/thin-margin
# archetypes; otherwise profitability at extraction time picks the metric.
# ``g_pct`` is the archetype's note-consistent forward revenue growth used in
# the growth adjustment when comp consensus growth is unavailable.
ARCHETYPES: dict[str, dict] = {
    "food_delivery": {
        "label": "Food delivery / local commerce",
        "keywords": ["fooddelivery", "delivery", "localcommerce", "ondemand"],
        "peers": ["DASH", "UBER", "LYFT", "GRAB"], "g_pct": 17.0,
    },
    "growth_commerce": {
        "label": "High-growth instant commerce",
        "keywords": ["instashopping", "instantretail", "quickcommerce"],
        "peers": ["SE", "CPNG", "MELI"], "g_pct": 25.0,
    },
    "ota_travel": {
        "label": "In-store / hotel / travel (OTA)",
        "keywords": ["instore", "hotel", "travel", "booking"],
        "peers": ["BKNG", "EXPE", "ABNB"], "g_pct": 8.0,
    },
    "low_margin_logistics": {
        "label": "Loss-making new commerce initiatives",
        "keywords": ["newinitiatives", "communitygroup", "keeta"],
        "peers": ["CPNG", "CHWY", "AMZN", "DASH"], "g_pct": 15.0,
        "metric": "ev_rev",
    },
    "cloud": {
        "label": "Cloud / AI infrastructure",
        "keywords": ["cloud", "intelligentcloud", "aws",
                     "amazonwebservices", "azure"],
        "peers": ["MSFT", "GOOGL", "AMZN", "ORCL"], "g_pct": 15.0,
    },
    "ecommerce_core": {
        "label": "Mature core commerce / retail",
        "keywords": ["taobao", "tmall", "jdretail", "corecommerce",
                     "domesticcore", "retail", "marketplace",
                     "northamerica", "onlinestores"],
        "peers": ["EBAY", "WMT", "TGT", "COST"], "g_pct": 8.0,
    },
    "logistics": {
        "label": "Integrated logistics / supply chain",
        "keywords": ["logistics", "cainiao", "supplychain", "fulfillment"],
        "peers": ["FDX", "UPS", "XPO", "JBHT"], "g_pct": 6.0,
    },
    "international_commerce": {
        "label": "International commerce (cross-border)",
        "keywords": ["aidc", "international", "aliexpress", "lazada",
                     "trendyol", "temu"],
        "peers": ["MELI", "SE", "CPNG"], "g_pct": 20.0,
        "metric": "ev_rev",
    },
    "games_media": {
        "label": "Games / interactive media",
        "keywords": ["games", "gaming", "valueaddedservices",
                     "interactiveentertainment"],
        "peers": ["NTES", "EA", "TTWO", "RBLX"], "g_pct": 6.0,
    },
    "ads": {
        "label": "Advertising / marketing services",
        "keywords": ["marketing", "advertising", "ads"],
        "peers": ["GOOGL", "META", "SNAP", "TTD"], "g_pct": 10.0,
    },
    "fintech_payments": {
        "label": "FinTech / payments / business services",
        "keywords": ["fintech", "payments", "businessservices"],
        "peers": ["PYPL", "GPN", "FIS", "FISV"], "g_pct": 8.0,
    },
}

_COMP_CACHE: dict = {}      # (ticker, end_date) -> fwd multiples row
_HAIRCUT_CACHE: dict = {}   # end_date -> jurisdiction dict
_CALIB_CACHE: dict = {}     # path key -> calibration artifact (or None)
_UNSET = object()           # "load default artifact" sentinel (None = disable)
_ZSCORE_FLAG = 1.5          # applied-vs-model flag level (residual stds)


def _load_calibration(path=None):
    """Cached load of the learned-basis artifact; None when missing/corrupt
    (callers then keep the pre-learning behavior bit-identically)."""
    key = str(path) if path else "default"
    if key not in _CALIB_CACHE:
        _CALIB_CACHE[key] = _load_artifact(path)
    return _CALIB_CACHE[key]


# ── Pure helpers (unit-tested) ────────────────────────────────────────────────

def normalize_key(name: str) -> str:
    # "&" reads as "and" ("Hotel & Travel" == "Hotel and Travel") — the
    # LLM flips between the two across runs.
    s = (name or "").lower().replace("&", " and ")
    return "".join(c for c in s if c.isalnum())


def classify_archetype(name: str) -> str | None:
    """Map a segment name to an archetype via keyword containment.

    Parenthetical gloss is stripped first ("New Businesses (food delivery,
    ...)" must classify by its primary name, not the gloss).
    """
    key = normalize_key((name or "").split("(")[0])
    if not key:
        return None
    for arch, spec in ARCHETYPES.items():
        if any(kw in key for kw in spec["keywords"]):
            return arch
    return None


def med_iqr(vals: list[float]) -> tuple[float, float, float]:
    """Median + confidence band (quartiles when >=4 obs, range otherwise)."""
    vals = sorted(vals)
    med = statistics.median(vals)
    if len(vals) >= 4:
        q = statistics.quantiles(vals, n=4)
        return med, q[0], q[2]
    return med, vals[0], vals[-1]


def adjusted_multiple(comp_median: float, comp_p75: float, *, haircut: float,
                      metric: str, seg_growth_pct: float | None,
                      comp_growth_pct: float | None) -> float:
    """median x jurisdiction haircut x growth adjustment, capped at comp p75.

    A faster-growing segment phases the jurisdiction haircut toward 1.0
    (elasticity 0.5 on the growth ratio, clamped to [0.5, 2.0]). EV/Rev is
    less jurisdiction-sensitive and floored at EVREV_HAIRCUT_FLOOR.
    """
    if seg_growth_pct and comp_growth_pct and comp_growth_pct > 0:
        ratio = max(GROWTH_RATIO_CLAMP[0],
                    min(GROWTH_RATIO_CLAMP[1], seg_growth_pct / comp_growth_pct))
    else:
        ratio = 1.0
    if metric == "ev_rev":
        xhc = max(haircut, EVREV_HAIRCUT_FLOOR)
    else:
        xhc = min(1.0, haircut * ratio ** GROWTH_ELASTICITY)
    return min(comp_median * xhc, comp_p75)


def derive_segment_basis(name: str, *, profitable: bool, loss: bool,
                         end_date: str, haircut: float, fetch) -> dict:
    """Tier-1 comp basis for one segment (pure once ``fetch`` is given).

    Returns a status dict: ``ok`` carries the derived multiple + IQR band;
    ``thin_comps`` / ``unknown_profitability`` / ``no_archetype`` mean the
    caller keeps the LLM/policy multiple (tiers 2/3).
    """
    arch_key = classify_archetype(name)
    if not arch_key:
        return {"status": "no_archetype"}
    spec = ARCHETYPES[arch_key]
    metric = (spec.get("metric")
              or ("pe" if profitable else "ev_rev" if loss else None))
    if metric is None:
        return {"status": "unknown_profitability", "archetype": arch_key}
    rows = fetch(spec["peers"], end_date)
    vals = [r[metric] for r in rows if r.get(metric)]
    if len(vals) < MIN_COMPS:
        return {"status": "thin_comps", "archetype": arch_key,
                "metric": metric, "n_comps": len(vals)}
    med, lo, hi = med_iqr(vals)
    g_vals = [r["g_pct"] for r in rows if r.get("g_pct") is not None]
    g_comp = statistics.median(g_vals) if g_vals else spec["g_pct"]
    derived = adjusted_multiple(med, hi, haircut=haircut, metric=metric,
                                seg_growth_pct=spec["g_pct"],
                                comp_growth_pct=g_comp)
    return {"status": "ok", "archetype": arch_key, "metric": metric,
            "peers": spec["peers"], "n_comps": len(vals),
            "comp_median": round(med, 2),
            "iqr": [round(lo, 2), round(hi, 2)],
            "comp_growth_pct": round(g_comp, 1),
            "seg_growth_pct": spec["g_pct"],
            "haircut": round(haircut, 3),
            "derived": round(derived, 2)}


# ── FMP-backed fetchers ───────────────────────────────────────────────────────

def _ttm_pe(t: str, end_date: str) -> float | None:
    """Reported TTM P/E (never consensus — mis-scaled for China ADRs)."""
    try:
        li = search_line_items(t, ["price_to_earnings_ratio"], end_date,
                               period="ttm", limit=1)
        v = getattr(li[0], "price_to_earnings_ratio", None) if li else None
        return float(v) if v and 0 < float(v) < _TTM_PE_CAP else None
    except Exception:
        return None


def _fwd_multiples(t: str, end_date: str) -> dict:
    """Forward EV/Rev and P/E for one comp from consensus Y+1 (cached)."""
    ck = (t, end_date)
    if ck in _COMP_CACHE:
        return _COMP_CACHE[ck]
    out: dict = {}
    try:
        mcap = get_market_cap(t, end_date)
    except Exception:
        mcap = None
    if mcap:
        nd = 0.0
        try:
            li = search_line_items(t, ["netDebt"], end_date,
                                   period="annual", limit=1)
            if li:
                nd = float(getattr(li[0], "net_debt", None)
                           or getattr(li[0], "netDebt", None) or 0.0)
        except Exception:
            nd = 0.0
        ev = mcap + nd
        try:
            ests = sorted(get_analyst_estimates(t, end_date, period="annual",
                                                limit=3),
                          key=lambda e: e.period_end)
        except Exception:
            ests = []
        if ests:
            y1 = ests[0]
            rev, ebitda, ni = y1.revenue_avg, y1.ebitda_avg, y1.net_income_avg
            out = {"ticker": t,
                   "ev_rev": ev / rev if rev else None,
                   "ev_ebitda": ev / ebitda if ebitda else None,
                   "pe": mcap / ni if ni and ni > 0 else None}
            if len(ests) >= 2 and ests[0].revenue_avg and ests[1].revenue_avg:
                out["g_pct"] = 100.0 * (
                    ests[1].revenue_avg / ests[0].revenue_avg - 1)
    _COMP_CACHE[ck] = out
    return out


def fetch_comp_multiples(peers: list[str], end_date: str) -> list[dict]:
    return [row for row in (_fwd_multiples(t, end_date) for t in peers) if row]


def get_jurisdiction_haircut(china: bool, end_date: str) -> dict:
    """China-tech vs US-tech reported-TTM-P/E ratio (1.0 outside China)."""
    if not china:
        return {"value": 1.0, "basis": "not_applicable (non-China)"}
    if end_date in _HAIRCUT_CACHE:
        return _HAIRCUT_CACHE[end_date]
    cn = [v for v in (_ttm_pe(t, end_date) for t in CN_PEERS) if v]
    us = [v for v in (_ttm_pe(t, end_date) for t in US_PEERS) if v]
    if cn and us:
        cn_med, us_med = statistics.median(cn), statistics.median(us)
        out = {"value": round(cn_med / us_med, 3),
               "basis": "reported_ttm_pe",
               "cn_pe": round(cn_med, 1), "us_pe": round(us_med, 1)}
    else:
        out = {"value": DEFAULT_CN_HAIRCUT,
               "basis": "policy_default_2026-08-16"}
    _HAIRCUT_CACHE[end_date] = out
    return out


# ── Extractor entry point ─────────────────────────────────────────────────────

def _basis_rationale(existing: str, basis: dict) -> str:
    note = (f"comp basis: {basis['comp_median']:.1f}x median "
            f"({'P/E' if basis['metric'] == 'pe' else 'EV/Rev'}) of "
            f"{', '.join(basis['peers'])} x {basis['haircut']:.2f} "
            f"jurisdiction haircut, growth-adjusted, capped at comp p75")
    return f"{existing}; {note}" if existing else note


def apply_multiple_basis(segments: list[dict], rs_segments: list[dict], *,
                         ticker: str, end_date: str, china: bool,
                         group_g_pct: float | None = None,
                         group_revenue: float | None = None,
                         _fetch=None, _haircut: dict | None = None,
                         _artifact=_UNSET,
                         ) -> tuple[list[dict], dict]:
    """Apply the independent multiple basis to merged extractor segments.

    Precedence per segment:
      1. deep-research 2A.5 block (``rs_segments``) — the researched
         variable overrides LLM multiples; source ``deep_research_2a5``.
      2. Learned characteristic model (multiple_learner artifact) when it
         covers the segment's archetype — source ``multiple_model_v1``.
      3. Tier-1 comp basis when >= MIN_COMPS pure-play comps carry the
         metric — source ``comp_basis``. Exactly ONE metric is set (the
         engine's max() rule arbitrages if both are given).
      4. Otherwise the LLM/policy multiple stands, flagged thin_comps.

    Wherever a basis is derivable, the applied multiple is cross-checked
    against it: a z-score > _ZSCORE_FLAG vs the model (residual stds) or
    divergence > MAX_DIVERGENCE vs the comp basis raises a review flag
    (GS/LLM multiples are cross-checks, never the anchor).

    ``_fetch`` / ``_haircut`` / ``_artifact`` are test seams (FMP fetchers
    and the default artifact by default; ``_artifact=None`` disables the
    model tier and restores the pre-learning behavior bit-identically).
    Returns ``(segments, basis_detail)``; basis_detail goes to
    assumptions["_multiple_basis"].
    """
    fetch = _fetch or fetch_comp_multiples
    haircut_info = _haircut or get_jurisdiction_haircut(china, end_date)
    hc = float(haircut_info.get("value", 1.0))
    artifact = (_load_calibration() if _artifact is _UNSET else _artifact) or None

    rs_by_key: dict = {}
    for s in rs_segments or []:
        k = normalize_key(s.get("name", ""))
        if k:
            rs_by_key[k] = s

    detail: dict = {}
    flags: list = []
    counts: dict = {}
    used_rs: set = set()

    for entry in segments:
        name = entry.get("name") or ""
        key = normalize_key(name)

        # Research-block match: exact normalized key, then substring.
        rs = rs_by_key.get(key)
        if rs is None:
            for k, s in rs_by_key.items():
                if k not in used_rs and (k in key or key in k):
                    rs = s
                    break
        if rs is not None:
            used_rs.add(normalize_key(rs.get("name", "")))

        _ue = entry.get("unit_economics") or {}
        profitable = ((entry.get("ebit") or 0) > 0
                      or (entry.get("ebit_margin") or 0) > 0
                      or (_ue.get("profit_per_unit") or 0) > 0)
        loss = ((entry.get("ebit") or 0) < 0
                or (entry.get("ebit_margin") or 0) < 0
                or (_ue.get("profit_per_unit") or 0) < 0)

        basis = derive_segment_basis(name, profitable=profitable, loss=loss,
                                     end_date=end_date, haircut=hc,
                                     fetch=fetch)

        # Learned-model prediction when the artifact covers this archetype
        # (containment gate lives in predict: thin/unseen archetypes return
        # None). Characteristics: archetype + jurisdiction + growth/scale
        # proxies (group consensus growth with archetype-g fallback; segment
        # forward revenue with group-revenue fallback).
        arch = basis.get("archetype") or classify_archetype(name)
        spec = ARCHETYPES.get(arch) if arch else None
        model_metric = ((spec or {}).get("metric")
                        or ("pe" if profitable else "ev_rev" if loss else None))
        model_pred = None
        if artifact and arch and model_metric:
            _g = (group_g_pct if group_g_pct is not None
                  else (spec or {}).get("g_pct"))
            _rev = entry.get("revenue_fwd") or group_revenue
            model_pred = _model_predict(artifact, model_metric, {
                "archetype": arch, "china": china,
                "g_fwd_pct": _g if _g is not None else 0.0,
                "log_revenue_scale": (math.log(_rev)
                                      if _rev and _rev > 0 else 0.0)})

        llm_pe, llm_evrev = entry.get("pe_multiple"), entry.get("ev_rev_multiple")

        if rs is not None and (rs.get("pe_multiple") or rs.get("ev_rev_multiple")):
            # Research override — enforce the one-metric rule if the block
            # somehow carried both.
            rs_pe, rs_ev = rs.get("pe_multiple"), rs.get("ev_rev_multiple")
            if rs_pe and rs_ev:
                if loss:
                    rs_pe = None
                else:
                    rs_ev = None
            entry["pe_multiple"] = rs_pe
            entry["ev_rev_multiple"] = rs_ev
            entry["source"] = "deep_research_2a5"
            if rs.get("rationale"):
                entry["rationale"] = rs["rationale"]
            status = "research_override"
            applied = rs_pe if rs_pe else rs_ev
            applied_metric = "pe" if rs_pe else "ev_rev"
        elif model_pred is not None:
            derived = round(model_pred["point"], 2)
            if model_metric == "pe":
                entry["pe_multiple"], entry["ev_rev_multiple"] = derived, None
            else:
                entry["ev_rev_multiple"], entry["pe_multiple"] = derived, None
            entry["source"] = "multiple_model_v1"
            note = (f"model basis: {derived}x "
                    f"{'P/E' if model_metric == 'pe' else 'EV/Rev'} "
                    f"(fit CI {model_pred['ci'][0]}-{model_pred['ci'][1]}, "
                    f"n={model_pred['archetype_n']} fit obs)")
            existing = entry.get("rationale") or ""
            entry["rationale"] = f"{existing}; {note}" if existing else note
            status = "model_applied"
            applied, applied_metric = derived, model_metric
        elif basis.get("status") == "ok":
            derived, metric = basis["derived"], basis["metric"]
            if metric == "pe":
                entry["pe_multiple"] = derived
                entry["ev_rev_multiple"] = None
            else:
                entry["ev_rev_multiple"] = derived
                entry["pe_multiple"] = None
            entry["source"] = "comp_basis"
            entry["rationale"] = _basis_rationale(entry.get("rationale") or "",
                                                  basis)
            status = "comp_basis_applied"
            applied, applied_metric = derived, metric
        else:
            status = basis.get("status", "no_archetype")
            applied = llm_pe if llm_pe else llm_evrev
            applied_metric = ("pe" if llm_pe else "ev_rev") if applied else None

        # Cross-check the applied multiple against the active derived basis:
        # z-score vs the model CI where the model applies, else the legacy
        # MAX_DIVERGENCE rule vs the comp basis.
        divergence, zscore, flag = None, None, False
        if model_pred is not None and applied and applied_metric == model_metric:
            divergence = applied / model_pred["point"] - 1
            sigma = model_pred["sigma"]
            zscore = (math.log(applied / model_pred["point"]) / sigma
                      if sigma > 0 else None)
            if zscore is not None and abs(zscore) > _ZSCORE_FLAG:
                flag = True
                flags.append(
                    f"{name}: applied {applied:.1f}x vs model basis "
                    f"{model_pred['point']:.1f}x ({divergence:+.0%}, "
                    f"z={zscore:+.1f}) — review")
        elif (basis.get("status") == "ok" and applied
                and applied_metric == basis["metric"]):
            divergence = applied / basis["derived"] - 1
            if abs(divergence) > MAX_DIVERGENCE:
                flag = True
                flags.append(
                    f"{name}: applied {applied:.1f}x vs comp basis "
                    f"{basis['derived']:.1f}x ({divergence:+.0%}) — review")

        seg_detail = {
            "status": status,
            "archetype": arch,
            "metric": (basis.get("metric") if basis.get("status") == "ok"
                       else applied_metric),
            "applied": applied,
            "llm_original": llm_pe if llm_pe else llm_evrev,
        }
        if basis.get("status") == "ok":
            seg_detail.update({
                "derived": basis["derived"],
                "comp_median": basis["comp_median"],
                "iqr": basis["iqr"],
                "n_comps": basis["n_comps"],
            })
        if model_pred is not None:
            seg_detail["model"] = {"point": model_pred["point"],
                                   "ci": model_pred["ci"],
                                   "n_obs": model_pred["archetype_n"]}
        if divergence is not None or flag:
            seg_detail["divergence_pct"] = (round(divergence * 100, 1)
                                            if divergence is not None else None)
            if zscore is not None:
                seg_detail["zscore"] = round(zscore, 2)
            seg_detail["flag"] = flag
        elif basis.get("status") == "ok":
            seg_detail["divergence_pct"] = None
            seg_detail["flag"] = False
        detail[name] = seg_detail
        counts[status] = counts.get(status, 0) + 1

    summary = ", ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "no_segments"
    if flags:
        summary += f", divergence_flags:{len(flags)}"
    basis_detail = {
        "jurisdiction": haircut_info,
        "segments": detail,
        "divergence_flags": flags,
        "summary": summary,
    }
    return segments, basis_detail


def apply_margin_basis(segments: list[dict], rs_segments: list[dict] | None,
                       *, china: bool,
                       group_g_pct: float | None = None,
                       _artifact=_UNSET) -> tuple[list[dict], dict]:
    """Fill segment ``ebit_margin`` from the learned margin model.

    Fills ONLY where no researched margin / EBIT / unit economics exists —
    never overrides (source ``margin_model_v1``). Segments matched to the
    research-note block (``rs_segments``) are NEVER filled: their multiples
    come from the note and a model margin under a note multiple would mix
    bases (2026-08-18: shifted 3690.HK NOTE-mode TP 159.2 -> 83.7 vs GS 123
    by substituting US-archetype margins for the note's economics).
    Structurally loss-making archetypes (forced ``ev_rev`` metric) are
    skipped: the margin model clamps to [0%, 60%] and would misstate their
    economics; they are valued on EV/Rev anyway. No artifact -> no-op
    (pre-learning behavior).
    """
    artifact = (_load_calibration() if _artifact is _UNSET else _artifact) or None
    detail: dict = {}
    if not artifact:
        return segments, {"summary": "no_artifact", "segments": detail}

    # Same normalized matching as apply_multiple_basis (exact key, then
    # substring, one-to-one) so the SAME segments the note governs are
    # excluded from margin filling.
    rs_keys: list = []
    for s in rs_segments or []:
        k = normalize_key(s.get("name", ""))
        if k:
            rs_keys.append(k)
    used_rs: set = set()

    def _matched_rs(name_key: str) -> bool:
        if name_key in rs_keys:
            used_rs.add(name_key)
            return True
        for k in rs_keys:
            if k not in used_rs and (k in name_key or name_key in k):
                used_rs.add(k)
                return True
        return False

    filled = 0
    for entry in segments:
        name = entry.get("name") or ""
        key = normalize_key(name)
        if key and _matched_rs(key):
            detail[name] = {"status": "research_note_kept"}
            continue
        if (entry.get("ebit_margin") is not None
                or entry.get("ebit") is not None
                or entry.get("unit_economics")):
            detail[name] = {"status": "researched_kept"}
            continue
        arch = classify_archetype(name)
        if not arch:
            detail[name] = {"status": "skipped", "reason": "no_archetype"}
            continue
        if ARCHETYPES[arch].get("metric") == "ev_rev":
            detail[name] = {"status": "skipped",
                            "reason": "ev_rev_archetype"}
            continue
        spec = ARCHETYPES[arch]
        _g = group_g_pct if group_g_pct is not None else spec.get("g_pct")
        _rev = entry.get("revenue_fwd")
        pred = _model_predict(artifact, "margin", {
            "archetype": arch, "china": china,
            "g_fwd_pct": _g if _g is not None else 0.0,
            "log_revenue_scale": (math.log(_rev)
                                  if _rev and _rev > 0 else 0.0)})
        if not pred:
            detail[name] = {"status": "model_no_cover"}
            continue
        entry["ebit_margin"] = pred["point"]
        entry["margin_source"] = "margin_model_v1"
        detail[name] = {"status": "margin_model_applied", "archetype": arch,
                        "margin": pred["point"], "ci": pred["ci"]}
        filled += 1
    return segments, {"summary": f"filled:{filled}", "segments": detail}
