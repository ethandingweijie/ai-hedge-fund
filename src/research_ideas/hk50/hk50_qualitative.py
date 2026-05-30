"""
src/research_ideas/hk50/hk50_qualitative.py
============================================
Qualitative overlay for the "Long China / HK" cohort — dimensions (1) Policy
posture and (5) Moat / competitive position.

WHY THIS EXISTS
---------------
The two quant screens (High-Growth, High-Dividend) are blind to the two forces
that dominate China/HK equity returns: STATE POLICY and STRUCTURAL MOAT. WuXi
Bio screens #3 on growth yet sits under the US BIOSECURE Act; the 2021 platform
crackdown vaporised earnings that every quant screen still rated "buy". This
module scores those forces explicitly and surfaces them as a PARALLEL column +
an optional conviction gate — it is NEVER summed into the 0-100 quant screens
(mirrors how AICT modulates IV15 transparently rather than silently moving the
composite).

TWO DIMENSIONS, TWELVE SUB-METRICS (each 0-5, 5 = most favorable for a LONG)
---------------------------------------------------------------------------
  Policy posture  (POL1..POL6) -> rolls up to a 5-tier ladder:
      Tailwind > Favorable > Neutral > Headwind > Crackdown
  Moat / competitive position (MOAT1..MOAT6) -> reuses the AICT ladder,
  generalised to every sector (banks / energy / consumer / pharma / insurers):
      Fortress > Castle > Chapel > Stone > Wood

POPULATION PATHS
----------------
  v1 curated (free, deterministic):  data/hk50_qualitative_seed.json supplies a
      hand-curated policy_tier + moat_tier per name (AICT-style). assess(...,
      use_llm=False) returns dimensions built straight off the seed.
  LLM deep-research (web run):  assess(..., use_llm=True) scores each sub-metric
      with Qwen + native web search (reusing the Complacency infra — Tavily,
      FMP news, deep_research_indicator), aggregates, and re-derives the tiers.
      Sub-metric scores are cached per (hk_ticker, indicator) in the shared
      qualitative_storage table.

Requires DEEP_RESEARCH_API_KEY (Qwen) for the LLM path; the curated path needs
no keys at all.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from src.research_ideas.hk50.schemas import (
    HK50Qualitative,
    HK50QualDimension,
    QualEvidence,
    QualIndicatorScore,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SEED_PATH = _DATA_DIR / "hk50_qualitative_seed.json"


# ════════════════════════════════════════════════════════════════════════════
# Sector map — drives sector-conditional rubric anchors + web-search queries.
# Keyed by canonical HK ticker (native ADRs by their own symbol).
# ════════════════════════════════════════════════════════════════════════════

SECTOR: dict[str, str] = {
    # Internet / platform
    "0700.HK": "Internet-Platform", "9988.HK": "Internet-Platform",
    "3690.HK": "Internet-Platform", "1024.HK": "Internet-Platform",
    "9618.HK": "Internet-Platform", "9999.HK": "Internet-Gaming",
    "9626.HK": "Internet-Platform", "TCOM": "Internet-Platform",
    # Hardware / tech
    "1810.HK": "Tech-Hardware", "0992.HK": "Tech-Hardware",
    # Autos / EV
    "1211.HK": "Auto-EV", "0175.HK": "Auto-EV", "2015.HK": "Auto-EV",
    "9868.HK": "Auto-EV",
    # Consumer / retail / F&B
    "6862.HK": "Consumer-Discretionary", "6690.HK": "Consumer-Discretionary",
    "2020.HK": "Consumer-Discretionary", "2331.HK": "Consumer-Discretionary",
    "0288.HK": "Consumer-Staples", "2313.HK": "Consumer-Manufacturing",
    "CHA": "Consumer-Discretionary", "EH": "Aviation-Tech",
    "YUMC": "Consumer-Discretionary",
    # Pharma / biotech
    "1801.HK": "Pharma-Biotech", "2269.HK": "Pharma-CDMO",
    "1093.HK": "Pharma-Biotech", "1177.HK": "Pharma-Biotech",
    # Banks
    "0005.HK": "Bank", "2888.HK": "Bank", "1398.HK": "Bank-SOE",
    "0939.HK": "Bank-SOE", "3988.HK": "Bank-SOE", "3968.HK": "Bank",
    "0011.HK": "Bank", "2388.HK": "Bank",
    # Insurers
    "2318.HK": "Insurance", "2628.HK": "Insurance", "1299.HK": "Insurance",
    # Telecom (SOE)
    "0941.HK": "Telecom-SOE", "0762.HK": "Telecom-SOE", "0728.HK": "Telecom-SOE",
    # Energy / oil (SOE)
    "0883.HK": "Energy-SOE", "0857.HK": "Energy-SOE", "0386.HK": "Energy-SOE",
    "1088.HK": "Energy-SOE",
    # Utilities
    "0002.HK": "Utility", "0006.HK": "Utility", "0003.HK": "Utility",
    # Property / REIT
    "1109.HK": "Property", "0823.HK": "REIT",
}


def sector_of(hk_ticker: str) -> str:
    return SECTOR.get(hk_ticker, "?")


# ════════════════════════════════════════════════════════════════════════════
# Tier ladders + score<->tier mapping
# ════════════════════════════════════════════════════════════════════════════

# Ordered worst -> best (index = ordinal rank 0..4).
POLICY_LADDER = ["Crackdown", "Headwind", "Neutral", "Favorable", "Tailwind"]
MOAT_LADDER = ["Wood", "Stone", "Chapel", "Castle", "Fortress"]

# Representative 0-100 score for a curated tier (band midpoints).
_TIER_MIDPOINT = {0: 10.0, 1: 30.0, 2: 50.0, 3: 70.0, 4: 90.0}


def _ladder(dimension: str) -> list[str]:
    return POLICY_LADDER if dimension == "policy" else MOAT_LADDER


def tier_to_score(dimension: str, tier: str) -> Optional[float]:
    """Curated tier label -> representative 0-100 score (band midpoint)."""
    ladder = _ladder(dimension)
    if tier not in ladder:
        return None
    return _TIER_MIDPOINT[ladder.index(tier)]


def score_to_tier(dimension: str, score_0_100: Optional[float]) -> str:
    """0-100 dimension score -> named ladder bucket. 5 equal 20-pt bands."""
    if score_0_100 is None:
        return "—"
    ladder = _ladder(dimension)
    if score_0_100 >= 80:
        return ladder[4]
    if score_0_100 >= 60:
        return ladder[3]
    if score_0_100 >= 40:
        return ladder[2]
    if score_0_100 >= 20:
        return ladder[1]
    return ladder[0]


def tier_rank(dimension: str, tier: str) -> int:
    """Ordinal 0(worst)..4(best); -1 if unknown / unscored."""
    ladder = _ladder(dimension)
    return ladder.index(tier) if tier in ladder else -1


# ════════════════════════════════════════════════════════════════════════════
# Sub-metric definitions — the deep-research SCAN LIST.
# Each: code -> {dimension, label, weight, rubric, queries}
#   weight   : within-dimension weight (each dimension's weights sum to 1.0)
#   rubric   : 0-5 anchors, 5 = most favorable for a LONG
#   queries  : web-search query templates ({name}/{ticker}/{sector}) the deep-
#              research run should scan; sector-conditional anchors live in the
#              rubric prose.
# ════════════════════════════════════════════════════════════════════════════

# ─── Dimension 1 — POLICY POSTURE (POL1..POL6) ─────────────────────────────

POL1_RUBRIC = """
POLICY CYCLE / OFFICIAL STANCE toward the company's sector (the single biggest
China/HK return driver). Score where the sector sits in the regulatory cycle.

0  Active crackdown in progress (cf. 2021 platform/education rectification):
   campaign-style enforcement, public rebukes, forced restructuring/refunds.
1  Clear headwind — tightening rules, price caps, "rectification" rhetoric,
   shrinking addressable profit pool.
2  Neutral — stable rules, no strong signal either way.
3  Favorable — easing cycle, "support the private economy / consumption" signals,
   pro-growth rhetoric directed at this sector.
4  Strong tailwind — sector named a national priority ("new productive forces",
   self-reliance, low-altitude economy, AI/data-center, energy security) with
   concrete supportive policy (subsidies, mandates, SOE-reform re-rating push).
"""

POL2_RUBRIC = """
ANTI-MONOPOLY / DATA-SECURITY / SECTOR-RECTIFICATION exposure.

0  Under active antitrust action, CAC data-security review, or "rectification"
   order now; fines/structural remedies live.
1  High latent exposure (dominant platform / large data troves) with recent
   enforcement history; overhang persists.
2  Moderate exposure; past issues resolved but rules still binding.
3  Low exposure or fully remediated with regulator sign-off.
4  Not applicable / negligible (most non-platform sectors), or an explicit
   "compliant / supported" green light from regulators.
"""

POL3_RUBRIC = """
INDUSTRY LICENSING & APPROVAL REGIME — the specific gate that throttles this
sector's revenue. Sector-conditional:
  • Internet-Gaming: game-approval (版号) cadence & domestic/import quota.
  • Pharma: VBP (volume-based procurement) + NRDL price-negotiation exposure.
  • Bank/Insurance: capital rules, lending-rate (LPR) guidance, fee caps.
  • Telecom/Energy/Utility: tariff/price-cap regime, spectrum, concession terms.
  • Auto-EV: subsidy/credit (NEV) regime, purchase-tax treatment.

0  Approval gate is actively choking revenue (license freeze, deep VBP cuts,
   punitive price caps).
1  Restrictive regime materially capping the profit pool.
2  Neutral / stable licensing; predictable.
3  Supportive — steady approvals, manageable VBP, constructive tariff resets.
4  Licensing is a tailwind (fast approvals, favorable tariff/subsidy regime,
   quota expansion) OR not a binding constraint for this business.
"""

POL4_RUBRIC = """
STATE-OWNERSHIP DYNAMIC — 'national service' burden vs SOE-reform re-rating.

0  Heavy 'national service' drag: forced low-return lending, price-capped
   outputs, mandated capex/social spending that suppresses shareholder returns.
1  Net SOE drag outweighs reform benefit.
2  Neutral / not state-owned, or burden ~ balanced by support.
3  SOE-reform BENEFICIARY — explicit push for higher dividends / ROE / "valuation
   with Chinese characteristics" re-rating; improving capital discipline.
4  Strong SOE-reform tailwind (rising payout mandate, buybacks, market-cap KPIs)
   OR a private name with no national-service obligation and aligned incentives.
"""

POL5_RUBRIC = """
INDUSTRIAL-POLICY & SUBSIDY SUPPORT / domestic-substitution beneficiary.

0  Targeted by restrictive policy / being phased out (e.g. high-carbon under
   decarbonisation, or a discouraged 'disorderly capital' activity).
1  Net policy headwind to the business model.
2  No material industrial-policy angle either way.
3  Beneficiary of supportive industrial policy (localisation, trade-in/consumption
   subsidies, green-energy buildout, import-substitution).
4  Core beneficiary of a flagship national program (semis/AI self-reliance, EV/
   battery leadership, low-altitude economy, energy security) with funded support.
"""

POL6_RUBRIC = """
GEOPOLITICAL & CROSS-BORDER exposure (US-China decoupling).

0  Direct, active hit: named in US legislation/entity-list/sanctions, or an
   imminent forced-divestment / delisting (e.g. BIOSECURE Act for CDMOs).
1  High exposure — export-control, tariff, or ADR/HFCAA-delisting risk that
   could materially impair the business.
2  Moderate exposure with manageable mitigation.
3  Low exposure — primarily domestic demand, limited US-supply-chain reliance.
4  Insulated or a decoupling BENEFICIARY (domestic-substitution winner, purely
   onshore franchise).
"""

# ─── Dimension 5 — MOAT / COMPETITIVE POSITION (MOAT1..MOAT6) ──────────────

MOAT1_RUBRIC = """
SWITCHING COSTS / CUSTOMER LOCK-IN. Sector-conditional:
  • Bank/Insurance: deposit-franchise / policyholder stickiness, primary-account
    inertia. • Internet: ecosystem & account lock-in. • Pharma: formulary/
    guideline inclusion, prescriber habit. • Telecom/Utility: contract & physical
    connection inertia. • Consumer: repeat/loyalty, membership.

0  Commodity; customers churn freely, no lock-in.
1  Weak — minor inconvenience to switch.
2  Moderate — some friction but credible substitutes.
3  Strong — meaningful cost/effort/risk to switch; high retention.
4  Near-absolute lock-in (entrenched account/ecosystem, regulated connection,
   mission-critical embedded position).
"""

MOAT2_RUBRIC = """
SCALE & COST ADVANTAGE — structurally lower unit cost than rivals.

0  Sub-scale / cost disadvantage.
1  Below-average scale; cost taker.
2  At-parity scale; no clear cost edge.
3  Clear scale leader with a durable unit-cost advantage (largest balance sheet,
   lowest lifting/production cost, densest network/logistics).
4  Dominant scale + structural low-cost position rivals cannot replicate
   (e.g. lowest-cost offshore oil, integrated coal-power-rail, #1 deposit base).
"""

MOAT3_RUBRIC = """
BRAND & PRICING POWER — ability to hold/raise price without losing volume.

0  Price taker; discounting to move volume; no brand equity.
1  Weak pricing power; promotion-dependent.
2  Average; passes through some cost.
3  Strong brand / pricing power; premium positioning or regulated allowed-return
   assurance (utilities) sustaining margins.
4  Exceptional pricing power — iconic brand premium, or a guaranteed regulated
   return that de-risks pricing entirely.
"""

MOAT4_RUBRIC = """
NETWORK EFFECTS — value rises with users/participants (mostly platforms, but
also payment/clearing, marketplaces, agency forces).

0  None — standalone product, no cross-side benefit.
1  Minimal / local network effects.
2  Moderate two-sided dynamics, contestable.
3  Strong network effects creating a self-reinforcing lead.
4  Dominant winner-take-most network (social graph, super-app, clearing/payment
   rail, largest two-sided marketplace).
"""

MOAT5_RUBRIC = """
REGULATORY / LICENSING MOAT — barriers the STATE erects that protect incumbents.

0  No entry barrier; fully open competition.
1  Low barrier; licenses easy to obtain.
2  Some licensing friction.
3  License-protected oligopoly (big-bank/telecom/insurance license, banking
   charter, spectrum, utility concession) limiting entrants.
4  Near-monopoly statutory protection (note-issuing bank, sole grid/gas
   concession, RMB-clearing license, strictly capped number of operators).
"""

MOAT6_RUBRIC = """
MARKET-SHARE TRAJECTORY & DISRUPTION RESILIENCE — is the moat WIDENING or
ERODING (forward-looking; pairs with AICT for software/internet names).

0  Share collapsing / facing existential disruption (AI substitution, EV price
   war wipeout, generic cliff, technology obsolescence).
1  Losing share; credible disruptor gaining.
2  Holding share in a stable-to-competitive market.
3  Gaining share; disruption threats identified but contained.
4  Widening lead; structurally insulated from / a beneficiary of disruption.
"""

# ─── Registry ──────────────────────────────────────────────────────────────

SUBMETRICS: dict[str, dict] = {
    # Policy posture
    "POL1_policy_cycle": {
        "dimension": "policy", "weight": 0.25, "label": "Policy cycle / official stance",
        "rubric": POL1_RUBRIC,
        "queries": [
            '{name} China regulation policy 2025 2026 crackdown OR support',
            '{name} {sector} Beijing policy stance regulator',
        ],
    },
    "POL2_antitrust_data": {
        "dimension": "policy", "weight": 0.15, "label": "Anti-monopoly / data-security",
        "rubric": POL2_RUBRIC,
        "queries": [
            '{name} antitrust OR anti-monopoly OR data security review SAMR CAC',
            '{name} fine rectification platform economy',
        ],
    },
    "POL3_licensing": {
        "dimension": "policy", "weight": 0.20, "label": "Licensing & approval regime",
        "rubric": POL3_RUBRIC,
        "queries": [
            '{name} game approval license OR VBP procurement OR tariff price cap OR LPR',
            '{name} {sector} approval quota regulator 2025 2026',
        ],
    },
    "POL4_state_ownership": {
        "dimension": "policy", "weight": 0.15, "label": "SOE national-service vs reform",
        "rubric": POL4_RUBRIC,
        "queries": [
            '{name} SOE reform dividend payout state-owned national service',
            '{name} valuation with Chinese characteristics buyback',
        ],
    },
    "POL5_industrial_policy": {
        "dimension": "policy", "weight": 0.10, "label": "Industrial policy / subsidy",
        "rubric": POL5_RUBRIC,
        "queries": [
            '{name} subsidy industrial policy domestic substitution national program',
            '{name} {sector} government support 2025 2026',
        ],
    },
    "POL6_geopolitical": {
        "dimension": "policy", "weight": 0.15, "label": "Geopolitical / cross-border",
        "rubric": POL6_RUBRIC,
        "queries": [
            '{name} US sanctions entity list export control BIOSECURE delisting HFCAA',
            '{name} tariff decoupling China US 2025 2026',
        ],
    },
    # Moat / competitive position
    "MOAT1_switching_costs": {
        "dimension": "moat", "weight": 0.20, "label": "Switching costs / lock-in",
        "rubric": MOAT1_RUBRIC,
        "queries": [
            '{name} customer retention switching cost lock-in churn',
            '{name} {sector} customer stickiness loyalty',
        ],
    },
    "MOAT2_scale_cost": {
        "dimension": "moat", "weight": 0.20, "label": "Scale & cost advantage",
        "rubric": MOAT2_RUBRIC,
        "queries": [
            '{name} cost advantage scale market leader lowest cost producer',
            '{name} {sector} economies of scale margin',
        ],
    },
    "MOAT3_brand_pricing": {
        "dimension": "moat", "weight": 0.15, "label": "Brand & pricing power",
        "rubric": MOAT3_RUBRIC,
        "queries": [
            '{name} brand pricing power premium ASP',
            '{name} {sector} pricing competition discount',
        ],
    },
    "MOAT4_network_effects": {
        "dimension": "moat", "weight": 0.15, "label": "Network effects",
        "rubric": MOAT4_RUBRIC,
        "queries": [
            '{name} network effects users ecosystem platform MAU',
            '{name} {sector} two-sided marketplace scale',
        ],
    },
    "MOAT5_regulatory_moat": {
        "dimension": "moat", "weight": 0.15, "label": "Regulatory / licensing moat",
        "rubric": MOAT5_RUBRIC,
        "queries": [
            '{name} license oligopoly regulated barrier to entry concession',
            '{name} {sector} number of operators license requirement',
        ],
    },
    "MOAT6_share_trajectory": {
        "dimension": "moat", "weight": 0.15, "label": "Share trajectory / disruption",
        "rubric": MOAT6_RUBRIC,
        "queries": [
            '{name} market share gaining losing disruption competition 2025 2026',
            '{name} {sector} AI disruption new entrant threat',
        ],
    },
}

POLICY_CODES = [c for c, m in SUBMETRICS.items() if m["dimension"] == "policy"]
MOAT_CODES = [c for c, m in SUBMETRICS.items() if m["dimension"] == "moat"]

# Cache TTL: policy shifts with the news cycle; moat is structural.
_TTL_DAYS = {"policy": 14, "moat": 30}


# ════════════════════════════════════════════════════════════════════════════
# Curated seed (free, deterministic v1 path)
# ════════════════════════════════════════════════════════════════════════════


@lru_cache(maxsize=1)
def load_seed() -> dict[str, dict]:
    """Hand-curated {hk_ticker: {policy_tier, moat_tier, note}} from JSON."""
    try:
        with _SEED_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("HK50 qual seed load failed: %s", exc)
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _seed_default() -> dict:
    try:
        with _SEED_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("_default", {"policy_tier": "Neutral", "moat_tier": "Chapel"})
    except Exception:
        return {"policy_tier": "Neutral", "moat_tier": "Chapel"}


# ════════════════════════════════════════════════════════════════════════════
# LLM scoring (reuses the Complacency Qwen + web-search infra)
# ════════════════════════════════════════════════════════════════════════════

_HK50_QUAL_SYSTEM = """
You are a buy-side analyst building the LONG case for a China / Hong Kong equity.
You are scoring ONE qualitative sub-metric on a 0-5 scale where 5 is MOST
FAVORABLE for a long position and 0 is MOST UNFAVORABLE. (This is the OPPOSITE
polarity of a short-seller checklist — higher is better.)

STRICT RULES:
1. Use ONLY the evidence provided. Do NOT invent facts.
2. Every score must be backed by at least one verbatim quote. If the provided
   evidence is empty or irrelevant, return score=2 (neutral) with confidence < 0.4.
3. Trust a single high-quality source (regulator statement, official filing,
   Reuters/Bloomberg/FT/SCMP) at face value; otherwise require 2 sources.
4. Evidence quotes must be VERBATIM from the provided text.
5. Be conservative — when evidence is thin, stay near neutral (2-3) with low
   confidence rather than guessing the extremes.

SOURCE QUALITY TIERS:
  Tier 1: official regulator/ministry statements, company filings, Reuters,
          Bloomberg, FT, WSJ, SCMP, Xinhua/state policy documents.
  Tier 2: mainstream business press (Caixin, Nikkei, CNBC), sell-side notes.
  Tier 3: blogs, forums, unattributed web snippets.
When evidence is exclusively Tier 3, cap confidence at 0.50.

REASONING (chain-of-thought scaffold — follow in order):
  Step 1 — extract 3-5 facts into `extracted_facts`; tag each with the rubric
           anchor (0-5) it supports AND its source tier (1/2/3).
  Step 2 — copy the verbatim quote backing each fact into `evidence`.
  Step 3 — pick the 0-5 score from the best-supported rubric anchor.
  Step 4 — set `confidence` (Tier-1 corroborated 0.80-0.95; mixed 0.60-0.80;
           Tier-3 only ≤0.50; empty facts <0.40).
  Step 5 — one-line `summary` (≤ 200 chars).

OUTPUT JSON only:
{
  "extracted_facts": [{"fact":"...","rubric_anchor":"score N: ...","source_tier":1}],
  "evidence": [{"source":"...","quote":"...","date":"yyyy-mm-dd"}],
  "score": <0-5 integer>,
  "confidence": <0-1 float>,
  "summary": "<≤200 chars>"
}
"""


def _gather_evidence(code: str, ticker: str, name: str, sector: str) -> list[dict]:
    """Build evidence packs for one sub-metric. Web-search first (the deep-
    research run); FMP news as a structured supplement. Degrades to [] when
    no search keys are configured (LLM then returns neutral/low-conf)."""
    # Lazy imports — keep the curated path import-light and key-free.
    try:
        from src.research_ideas.complacency.qualitative import (
            _tavily_packs, _fmp_news_packs,
        )
        from src.research_ideas.complacency.evidence_sources import fetch_stock_news
    except Exception as exc:  # pragma: no cover
        logger.debug("HK50 qual evidence helpers unavailable: %s", exc)
        return []

    cfg = SUBMETRICS[code]
    packs: list[dict] = []
    for q_tmpl in cfg.get("queries", []):
        q = q_tmpl.format(name=name, ticker=ticker, sector=sector)
        try:
            packs.extend(_tavily_packs(q, days=400, max_results=4))
        except Exception as exc:
            logger.debug("Tavily pack failed (%s): %s", code, exc)
    # FMP news on the report ticker (richer text for ADR names).
    try:
        packs.extend(_fmp_news_packs(fetch_stock_news(ticker, days=180, limit=4)))
    except Exception as exc:
        logger.debug("FMP news pack failed (%s): %s", code, exc)
    return packs


# Deep-research escalation threshold (reuse the Complacency floor convention).
_DEEP_FLOOR = float(os.environ.get("HK50_QUAL_DEEP_FLOOR", "0.45"))

# Hard cap on deep-research (Qwen native web search) escalations per
# assess_hk50_qualitative() run — mirrors COMPLACENCY_DEEP_RESEARCH_MAX. Each
# escalation costs ~$0.05 + 30-120s wall-clock; without a cap a single name
# with many low-conf first-pass sub-metrics could fire 12 escalations and tie
# up the throttle for 10+ min. 3 prioritises the lowest-conf sub-metrics.
_HK50_DEEP_MAX_PER_TICKER = int(os.environ.get("HK50_QUAL_DEEP_MAX", "3"))

# Only the NEWS-SENSITIVE sub-metrics benefit from a deep web-search second
# pass: all 6 policy gauges (regulatory cycle shifts with the news flow) plus
# MOAT6 (share trajectory / disruption is forward-looking). The structural
# moat gauges (switching / scale / brand / network / licensing) are stable —
# re-running deep search rarely lifts confidence, so they're excluded to keep
# the per-ticker budget aimed where it pays off.
HK50_DEEP_RESEARCH_SUBMETRICS: set[str] = set(POLICY_CODES) | {"MOAT6_share_trajectory"}

# Per-run shared counter for deep-research escalations. MUST be a process-
# global guarded by a lock so the cap is enforced ACROSS the ThreadPool's
# worker threads (threading.local would give each worker its own budget,
# defeating the cap). Reset at the start of every assess_hk50_qualitative()
# call so the cap is per-name. Concurrent assessments share the counter —
# harmless over-application (worst case: deep research skipped on a few
# sub-metrics across a parallel run); the cohort job runs names SEQUENTIALLY
# anyway, so in practice the cap is exact.
import threading as _threading_mod

_HK50_DEEP_COUNT_LOCK = _threading_mod.Lock()
_HK50_DEEP_COUNT = 0

# Idempotent-restart threshold: on a force_refresh, sub-metrics already cached
# at or above this confidence are PRESERVED (not re-run) — re-running them just
# burns Qwen tokens for a near-identical score. A 429-cascaded run that got
# 8/12 done only re-runs the 4 that failed on the next click (ratchet effect).
_HK50_HIGH_CONF_KEEP = 0.70


def _hk50_deep_count_get() -> int:
    with _HK50_DEEP_COUNT_LOCK:
        return _HK50_DEEP_COUNT


def _hk50_deep_count_inc() -> int:
    global _HK50_DEEP_COUNT
    with _HK50_DEEP_COUNT_LOCK:
        _HK50_DEEP_COUNT += 1
        return _HK50_DEEP_COUNT


def _hk50_deep_count_reset() -> None:
    global _HK50_DEEP_COUNT
    with _HK50_DEEP_COUNT_LOCK:
        _HK50_DEEP_COUNT = 0


def score_submetric(
    hk_ticker: str,
    report_ticker: str,
    name: str,
    code: str,
    force_refresh: bool = False,
    enable_deep_research: bool = True,
) -> Optional[QualIndicatorScore]:
    """Score ONE sub-metric for one name. 14/30-day cache by dimension.

    Returns None when the LLM is unavailable (no DEEP_RESEARCH_API_KEY)."""
    if code not in SUBMETRICS:
        return None
    cfg = SUBMETRICS[code]
    dim = cfg["dimension"]
    ttl = _TTL_DAYS[dim]
    sector = sector_of(hk_ticker)

    from app.backend.services.qualitative_storage import (
        get_latest_qualitative_score, save_qualitative_score,
    )
    if not force_refresh:
        cached = get_latest_qualitative_score(hk_ticker, code, ttl)
        if cached:
            # Auto-bypass cache for low-conf entries that NEVER ran the deep-
            # research path (cached before deep shipped, or a low-conf miss).
            # Without this they'd be returned as-is forever, defeating the
            # escalation. Bypass only when ALL hold: eligible sub-metric,
            # cached conf ≤ floor, and no prior deep attempt recorded.
            already_deep = "deep" in (cached.get("model_used") or "").lower()
            eligible = (
                enable_deep_research
                and code in HK50_DEEP_RESEARCH_SUBMETRICS
                and (cached.get("confidence") or 0.0) <= _DEEP_FLOOR
                and not already_deep
                and _hk50_deep_count_get() < _HK50_DEEP_MAX_PER_TICKER
            )
            if not eligible:
                return QualIndicatorScore(
                    indicator=code,
                    score=cached["score"],
                    confidence=cached["confidence"],
                    summary=cached["summary"],
                    evidence=[QualEvidence(**e) for e in cached["evidence"]],
                    scored_at=cached["scored_at"],
                    model_used=cached["model_used"],
                )
            logger.info(
                "HK50 cached %s/%s conf %.2f ≤ floor %.2f & no deep-attempt — "
                "bypassing cache to fire deep-research.",
                hk_ticker, code, cached.get("confidence") or 0.0, _DEEP_FLOOR,
            )

    # Reuse the Complacency Qwen call + prompt builder verbatim.
    try:
        from src.research_ideas.complacency.qualitative import (
            _call_qwen_indicator, _build_user_prompt,
        )
        from src.research_ideas.complacency.web_research import deep_research_indicator
    except Exception as exc:
        logger.warning("HK50 qual: Qwen infra unavailable: %s", exc)
        return None

    packs = _gather_evidence(code, report_ticker, name, sector)
    user_prompt = _build_user_prompt(
        report_ticker, name, sector, code, cfg["rubric"], packs
    )
    output, cost = _call_qwen_indicator(_HK50_QUAL_SYSTEM, user_prompt)
    if output is None:
        return None

    final_score, final_conf = output.score, output.confidence
    final_summary = output.summary
    final_evidence = [e.model_dump() for e in output.evidence]
    model_used = "qwen-hk50-qual"

    # Low-confidence escalation -> Qwen native web search. Gated by the
    # per-ticker hard cap (lock-protected, shared across worker threads) and
    # restricted to the news-sensitive sub-metrics so the budget is spent
    # where a web pass actually lifts confidence.
    if (
        enable_deep_research
        and code in HK50_DEEP_RESEARCH_SUBMETRICS
        and final_conf <= _DEEP_FLOOR
        and _hk50_deep_count_get() < _HK50_DEEP_MAX_PER_TICKER
    ):
        n = _hk50_deep_count_inc()
        logger.info(
            "HK50 %s/%s first-pass conf %.2f ≤ floor %.2f — escalating to "
            "deep-research [budget %d/%d].",
            hk_ticker, code, final_conf, _DEEP_FLOOR, n, _HK50_DEEP_MAX_PER_TICKER,
        )
        try:
            deep = deep_research_indicator(
                ticker=report_ticker, name=name, sector=sector,
                indicator_code=code, indicator_label=cfg["label"],
                rubric=cfg["rubric"], initial_score=final_score,
                initial_confidence=final_conf, initial_summary=final_summary,
                initial_evidence=final_evidence,
            )
            if deep and deep.get("confidence", 0.0) > final_conf:
                final_score = deep["score"]
                final_conf = deep["confidence"]
                final_summary = (
                    f"{deep.get('summary','')} "
                    f"[deep: {deep.get('reasoning','')[:120]}]"
                ).strip()
                final_evidence = [
                    {"source": e.get("source", "web search"), "quote": e.get("quote", ""),
                     "date": e.get("date"), "url": e.get("url")}
                    for e in (deep.get("evidence") or [])
                ]
                model_used = "qwen-hk50-qual+deep_web_search"
                cost += 0.05
        except Exception as exc:
            logger.debug("HK50 deep-research %s/%s failed: %s", hk_ticker, code, exc)

    scored_at = datetime.now(timezone.utc).isoformat()
    try:
        save_qualitative_score(
            ticker=hk_ticker, indicator=code, score=final_score,
            confidence=final_conf, summary=final_summary, evidence=final_evidence,
            model_used=model_used, cost_usd=cost, scored_at=scored_at,
        )
    except Exception as exc:
        logger.debug("HK50 qual persist failed: %s", exc)

    return QualIndicatorScore(
        indicator=code, score=final_score, confidence=final_conf,
        summary=final_summary,
        evidence=[QualEvidence(**e) for e in final_evidence],
        scored_at=scored_at, model_used=model_used,
    )


# ════════════════════════════════════════════════════════════════════════════
# Aggregate rollup
# ════════════════════════════════════════════════════════════════════════════

# Confidence floor in the weighted average so a low-confidence sub-metric still
# contributes (just less) rather than being effectively dropped.
_CONF_FLOOR = 0.25


def aggregate_dimension(
    dimension: str,
    scored: dict[str, QualIndicatorScore],
    seed_tier: Optional[str] = None,
) -> HK50QualDimension:
    """Roll sub-metric 0-5 scores into a confidence-weighted 0-100 + tier.

    Weights renormalise over whichever sub-metrics resolved (partial scoring is
    fine). If NONE resolved, fall back to the curated seed tier's midpoint."""
    codes = POLICY_CODES if dimension == "policy" else MOAT_CODES
    present = {c: scored[c] for c in codes if c in scored}

    if not present:
        # Curated fallback.
        sc = tier_to_score(dimension, seed_tier) if seed_tier else None
        return HK50QualDimension(
            dimension=dimension, score_0_100=sc,
            tier=seed_tier or "—", n_scored=0, mean_confidence=0.0,
            summary="(curated seed)" if seed_tier else "(unscored)",
        )

    num = den = 0.0
    conf_sum = 0.0
    for c, s in present.items():
        w = SUBMETRICS[c]["weight"]
        eff = w * max(s.confidence, _CONF_FLOOR)
        num += eff * s.score
        den += eff
        conf_sum += s.confidence
    mean_score_0_5 = (num / den) if den else 0.0
    score_0_100 = round(mean_score_0_5 / 5.0 * 100.0, 1)
    tier = score_to_tier(dimension, score_0_100)
    mean_conf = round(conf_sum / len(present), 2)

    # One-line takeaway: name the strongest + weakest resolved sub-metric.
    strongest = max(present.values(), key=lambda s: s.score)
    weakest = min(present.values(), key=lambda s: s.score)
    summary = (
        f"{tier} ({score_0_100:.0f}/100). "
        f"+{SUBMETRICS[strongest.indicator]['label']} {strongest.score}/5; "
        f"-{SUBMETRICS[weakest.indicator]['label']} {weakest.score}/5."
    )

    return HK50QualDimension(
        dimension=dimension, score_0_100=score_0_100, tier=tier,
        n_scored=len(present), mean_confidence=mean_conf,
        summary=summary, indicators=present,
    )


def combined_conviction(
    quant_lead_score: float,
    policy_tier: str,
    moat_tier: str,
) -> tuple[str, list[str]]:
    """Fuse the (best of the two) quant screen scores with the two qualitative
    tiers into a conviction label + human-readable flags. NEVER changes the
    underlying quant scores — this is a parallel overlay only.

    quant_lead_score: max(growth_score, dividend_score), 0-100.
    """
    p = tier_rank("policy", policy_tier)   # 0 worst .. 4 best, -1 unknown
    m = tier_rank("moat", moat_tier)
    flags: list[str] = []

    if p < 0 and m < 0:
        return "UNSCORED", flags

    strong_quant = quant_lead_score >= 70.0
    decent_quant = quant_lead_score >= 55.0

    # Hard override: an active crackdown or a collapsing moat trumps any screen.
    if policy_tier == "Crackdown" or moat_tier == "Wood":
        if policy_tier == "Crackdown":
            flags.append("Active policy crackdown overrides screen rank (e.g. BIOSECURE / rectification).")
        if moat_tier == "Wood":
            flags.append("Eroding / sub-scale moat — competitive position at risk.")
        if strong_quant:
            flags.append("Screen-rich but qual-risk: classic policy/value trap.")
        return "POLICY-RISK", flags

    if strong_quant and p >= 3 and m >= 3:
        flags.append("Strong screen + favorable policy + wide moat.")
        return "HIGH-CONVICTION", flags
    if strong_quant and (p <= 1 or m <= 1):
        flags.append("Top screen score undercut by policy headwind or thin moat — verify it isn't a trap.")
        return "QUANT-RICH", flags
    if decent_quant and p >= 2 and m >= 2:
        return "SOLID", flags
    if not decent_quant and p >= 3 and m >= 3:
        flags.append("Qualitatively strong but screen score is middling — watch for a catalyst.")
        return "QUAL-SUPPORT", flags
    return "WATCH", flags


# ════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ════════════════════════════════════════════════════════════════════════════


def assess_hk50_qualitative(
    hk_ticker: str,
    report_ticker: Optional[str] = None,
    name: Optional[str] = None,
    quant_lead_score: float = 0.0,
    use_llm: bool = False,
    force_refresh: bool = False,
    max_workers: int = 3,
    per_submetric_timeout_s: int = 240,
    keep_high_conf_on_force: bool = True,
    on_indicator_done: Optional[Callable[[str, QualIndicatorScore], None]] = None,
) -> HK50Qualitative:
    """Build the Policy + Moat overlay for one HK50 name.

    use_llm=False  -> curated seed only (free, instant, deterministic).
    use_llm=True   -> score all 12 sub-metrics via Qwen + web search, aggregate,
                      and re-derive tiers; seed tiers are the fallback for any
                      dimension that fails to score (source -> hybrid).

    429-prevention (mirrors the Complacency screener):
      • the shared qwen_throttle token bucket inside _call_qwen_indicator,
      • a 1.5s stagger on the first `max_workers` submissions so 3 Qwen calls
        don't hit DashScope's burst limiter simultaneously at t=0,
      • a lock-protected per-name deep-research cap (_HK50_DEEP_MAX_PER_TICKER),
      • a per-sub-metric timeout so one hung call can't stall the pool,
      • idempotent restart (force_refresh preserves high-conf cached scores).
    The COHORT job runs names SEQUENTIALLY on top of all this — 3 concurrent
    Qwen calls at steady state, never 12×N.
    """
    import time as _time
    sector = sector_of(hk_ticker)
    seed = load_seed().get(hk_ticker) or _seed_default()
    seed_policy = seed.get("policy_tier")
    seed_moat = seed.get("moat_tier")

    if not use_llm:
        policy = aggregate_dimension("policy", {}, seed_tier=seed_policy)
        moat = aggregate_dimension("moat", {}, seed_tier=seed_moat)
        conv, flags = combined_conviction(quant_lead_score, policy.tier, moat.tier)
        if seed.get("note"):
            flags = [seed["note"]] + flags
        return HK50Qualitative(
            hk_ticker=hk_ticker, sector=sector, policy=policy, moat=moat,
            conviction=conv, source="curated", flags=flags,
            assessed_at=datetime.now(timezone.utc).isoformat(),
        )

    rep = report_ticker or hk_ticker
    nm = name or hk_ticker
    scored: dict[str, QualIndicatorScore] = {}
    total_cost = 0.0
    incomplete = False

    # Per-name deep-research budget resets here so the cap is per-assessment.
    _hk50_deep_count_reset()

    codes_to_run = list(SUBMETRICS.keys())

    # ── IDEMPOTENT RESTART ────────────────────────────────────────────────
    # On a force_refresh, DON'T blindly re-run every sub-metric. Preserve the
    # ones already cached at high confidence (≥ _HK50_HIGH_CONF_KEEP) and only
    # re-run the rest. A run that 429-cascaded at 8/12 then only re-fires the
    # 4 that failed — practical completeness ratchets to 100% in 2-3 clicks.
    if force_refresh and keep_high_conf_on_force:
        from app.backend.services.qualitative_storage import get_latest_qualitative_score
        kept = 0
        for code in list(codes_to_run):
            ttl = _TTL_DAYS[SUBMETRICS[code]["dimension"]]
            cached = get_latest_qualitative_score(hk_ticker, code, ttl)
            if cached and (cached.get("confidence") or 0.0) >= _HK50_HIGH_CONF_KEEP:
                preloaded = QualIndicatorScore(
                    indicator=code, score=cached["score"],
                    confidence=cached["confidence"], summary=cached["summary"],
                    evidence=[QualEvidence(**e) for e in cached["evidence"]],
                    scored_at=cached["scored_at"], model_used=cached["model_used"],
                )
                scored[code] = preloaded
                kept += 1
                if on_indicator_done:
                    try:
                        on_indicator_done(code, preloaded)
                    except Exception as exc:
                        logger.debug("on_indicator_done (preload) %s: %s", code, exc)
        codes_to_run = [c for c in codes_to_run if c not in scored]
        if kept:
            logger.info(
                "HK50 idempotent restart %s: kept %d high-conf cached, re-running %d",
                hk_ticker, kept, len(codes_to_run),
            )

    ex = ThreadPoolExecutor(max_workers=max_workers)
    try:
        # Stagger the first `max_workers` submissions by 1.5s so we don't fire
        # `max_workers` Qwen calls into DashScope's burst window at t=0. The
        # pool still runs `max_workers` concurrent at steady state.
        futures: dict = {}
        for i, code in enumerate(codes_to_run):
            futures[ex.submit(
                score_submetric, hk_ticker, rep, nm, code, force_refresh,
            )] = code
            if i < max_workers - 1:
                _time.sleep(1.5)

        # Outer cap = per-sub-metric timeout × waves needed to drain the pool.
        total_timeout = per_submetric_timeout_s * (
            (len(futures) + max_workers - 1) // max_workers
        ) if futures else 1
        try:
            for fut in as_completed(futures, timeout=total_timeout):
                code = futures[fut]
                try:
                    res = fut.result(timeout=per_submetric_timeout_s)
                except Exception as exc:
                    logger.warning("HK50 qual %s/%s failed: %s", hk_ticker, code, exc)
                    incomplete = True
                    continue
                if res is None:
                    incomplete = True
                    continue
                scored[code] = res
                if on_indicator_done:
                    try:
                        on_indicator_done(code, res)
                    except Exception as exc:
                        logger.debug("on_indicator_done failed for %s: %s", code, exc)
        except Exception as exc:
            logger.warning(
                "HK50 qual %s hit outer timeout (%d/%d scored): %s",
                hk_ticker, len(scored), len(SUBMETRICS), exc,
            )
            incomplete = True
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # pragma: no cover - <3.9
            ex.shutdown(wait=False)

    policy = aggregate_dimension("policy", scored, seed_tier=seed_policy)
    moat = aggregate_dimension("moat", scored, seed_tier=seed_moat)
    conv, flags = combined_conviction(quant_lead_score, policy.tier, moat.tier)

    used_seed_fallback = (policy.n_scored == 0) or (moat.n_scored == 0)
    if not scored:
        # Nothing scored at all -> behave like the curated path.
        if seed.get("note"):
            flags = [seed["note"]] + flags
        return HK50Qualitative(
            hk_ticker=hk_ticker, sector=sector, policy=policy, moat=moat,
            conviction=conv, source="curated", flags=flags,
            assessed_at=datetime.now(timezone.utc).isoformat(), incomplete=True,
        )

    return HK50Qualitative(
        hk_ticker=hk_ticker, sector=sector, policy=policy, moat=moat,
        conviction=conv, source="hybrid" if used_seed_fallback else "llm",
        flags=flags, assessed_at=datetime.now(timezone.utc).isoformat(),
        cost_usd=round(total_cost, 4), incomplete=incomplete,
    )


if __name__ == "__main__":
    # Manual smoke (curated path — no keys needed):
    #   .venv\Scripts\python.exe -m src.research_ideas.hk50.hk50_qualitative
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for hk, lead in [("2269.HK", 99.5), ("0700.HK", 80.0), ("1398.HK", 75.0), ("9868.HK", 60.0)]:
        q = assess_hk50_qualitative(hk, quant_lead_score=lead, use_llm=False)
        print(f"\n{hk} [{q.sector}] lead={lead}")
        print(f"  Policy : {q.policy.tier:<10s} ({q.policy.score_0_100})")
        print(f"  Moat   : {q.moat.tier:<10s} ({q.moat.score_0_100})")
        print(f"  Verdict: {q.conviction}")
        for f in q.flags:
            print(f"    - {f}")
