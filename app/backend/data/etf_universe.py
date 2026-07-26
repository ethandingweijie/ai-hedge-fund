"""
app/backend/data/etf_universe.py
=================================
Curated ETF ticker candidates for the Robo Strategy allocation engine.

Only a ticker symbol + asset-class `bucket` is hand-authored here — everything
else (price, expense ratio, AUM, sector composition, country composition, top
holdings) is fetched live from FMP by `services/etf_metadata_service.py` and
is never hardcoded, matching the momentum-screen convention of a curated
symbol list + live-fetched data (see src/research_ideas/data/momentum_sectors.json).

`bucket` is a MUTUALLY EXCLUSIVE asset-class tag used only for the allocation
engine's category partition (stock / bond / commodity / reit — mirrors the
prototype's `getBaseAllocation()` split). Deliberate fix vs. the ported
prototype: the original TS engine's "stock" bucket was defined by exclusion
(`!["Bond","Commodity"].includes(category)`), which accidentally also
included REIT-tagged ETFs — a REIT fund could then be picked once via the
stock-bucket ranking AND again via the REIT-bucket ranking, producing a
duplicate ticker with two allocation line items. Here `bucket` is a single
explicit value per ticker so that can't happen.

Coverage note: China and India each get their own dedicated group (6 and 4
tickers) rather than being folded into a single generic "Emerging Markets"
bucket, so a user tilting heavily toward either has real breadth to select
from — including KSTR (KraneShares SSE STAR Market 50, tracking China's
STAR50 tech/innovation board).

Coverage note (2026-07-26 expansion): added dedicated Europe and Asia-Pacific
groups — the prior "intl_developed" group (VEA/EFA/VXUS/EWJ) was all blended
multi-region funds, so a user maxing the questionnaire's Europe or
Asia-Pacific geography weight (added in the same pass that added those
regions) had no pure-play to actually select, only approximations. Also
broadened EM breadth beyond China/India (Brazil, Taiwan), US factor/dividend
coverage beyond SCHD/VUG/VTV, and bond/commodity coverage (TIPS, munis, EM
debt, silver, oil) which was previously narrow.
"""
from __future__ import annotations

from typing import Literal, TypedDict

Bucket = Literal["stock", "bond", "commodity", "reit"]


class EtfUniverseEntry(TypedDict):
    ticker: str
    bucket: Bucket
    group: str  # human-readable grouping, for debugging/admin display only


ETF_UNIVERSE: list[EtfUniverseEntry] = [
    # ── Broad US equity ──────────────────────────────────────────────────────
    {"ticker": "VTI",  "bucket": "stock", "group": "us_broad"},
    {"ticker": "VOO",  "bucket": "stock", "group": "us_broad"},
    {"ticker": "VUG",  "bucket": "stock", "group": "us_broad"},
    {"ticker": "VTV",  "bucket": "stock", "group": "us_broad"},
    {"ticker": "SCHD", "bucket": "stock", "group": "us_broad"},

    # ── US sector equity (11, matches _SCREENER_SECTORS taxonomy) ───────────
    {"ticker": "XLK",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLC",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLY",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLP",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLE",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLF",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLV",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLI",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLB",  "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLRE", "bucket": "stock", "group": "us_sector"},
    {"ticker": "XLU",  "bucket": "stock", "group": "us_sector"},

    # ── International developed equity (blended, multi-region) ──────────────
    {"ticker": "VEA",  "bucket": "stock", "group": "intl_developed"},
    {"ticker": "EFA",  "bucket": "stock", "group": "intl_developed"},
    {"ticker": "VXUS", "bucket": "stock", "group": "intl_developed"},
    {"ticker": "EWJ",  "bucket": "stock", "group": "intl_developed"},   # Japan

    # ── Europe equity (pure-play — VEA/EFA/VXUS above are blended, not
    #    Europe-specific, so a user maxing the Europe geography weight had
    #    nothing to actually select before this group existed) ───────────────
    {"ticker": "VGK",  "bucket": "stock", "group": "europe"},   # broad Europe incl. UK
    {"ticker": "EZU",  "bucket": "stock", "group": "europe"},   # Eurozone-only

    # ── Asia-Pacific developed equity (pure-play, ex the blended EWJ above) ──
    {"ticker": "VPL",  "bucket": "stock", "group": "apac_developed"},   # broad developed Pacific
    {"ticker": "EWA",  "bucket": "stock", "group": "apac_developed"},   # Australia
    {"ticker": "EWY",  "bucket": "stock", "group": "apac_developed"},   # South Korea

    # ── Emerging markets — broad ─────────────────────────────────────────────
    {"ticker": "VWO",  "bucket": "stock", "group": "em_broad"},
    {"ticker": "EEM",  "bucket": "stock", "group": "em_broad"},

    # ── China equity ──────────────────────────────────────────────────────────
    {"ticker": "FXI",  "bucket": "stock", "group": "china"},   # large-cap
    {"ticker": "MCHI", "bucket": "stock", "group": "china"},   # broad MSCI China
    {"ticker": "ASHR", "bucket": "stock", "group": "china"},   # CSI 300 A-shares
    {"ticker": "KWEB", "bucket": "stock", "group": "china"},   # China internet
    {"ticker": "CQQQ", "bucket": "stock", "group": "china"},   # China technology
    {"ticker": "KSTR", "bucket": "stock", "group": "china"},   # STAR50 (Shanghai tech/innovation board)

    # ── India equity ──────────────────────────────────────────────────────────
    {"ticker": "INDA", "bucket": "stock", "group": "india"},   # broad MSCI India
    {"ticker": "INDY", "bucket": "stock", "group": "india"},   # large-cap India 50
    {"ticker": "SMIN", "bucket": "stock", "group": "india"},   # India small-cap
    {"ticker": "FLIN", "bucket": "stock", "group": "india"},   # broad India, low-cost

    # ── Other emerging markets (single-country, beyond China/India) ─────────
    {"ticker": "EWZ",  "bucket": "stock", "group": "em_other"},   # Brazil
    {"ticker": "EWT",  "bucket": "stock", "group": "em_other"},   # Taiwan

    # ── US factor / dividend equity (beyond SCHD/VUG/VTV above) ─────────────
    {"ticker": "VYM",  "bucket": "stock", "group": "us_factor"},   # high dividend yield
    {"ticker": "MTUM", "bucket": "stock", "group": "us_factor"},   # momentum factor
    {"ticker": "QUAL", "bucket": "stock", "group": "us_factor"},   # quality factor

    # ── Bonds ─────────────────────────────────────────────────────────────────
    {"ticker": "BND",  "bucket": "bond", "group": "bonds"},
    {"ticker": "AGG",  "bucket": "bond", "group": "bonds"},
    {"ticker": "TLT",  "bucket": "bond", "group": "bonds"},
    {"ticker": "BNDX", "bucket": "bond", "group": "bonds"},
    {"ticker": "HYG",  "bucket": "bond", "group": "bonds"},
    {"ticker": "TIP",  "bucket": "bond", "group": "bonds"},   # inflation-protected (TIPS)
    {"ticker": "MUB",  "bucket": "bond", "group": "bonds"},   # national municipal bond
    {"ticker": "EMB",  "bucket": "bond", "group": "bonds"},   # emerging-market debt

    # ── Commodities ───────────────────────────────────────────────────────────
    {"ticker": "GLD",  "bucket": "commodity", "group": "commodities"},
    {"ticker": "DBC",  "bucket": "commodity", "group": "commodities"},
    {"ticker": "SLV",  "bucket": "commodity", "group": "commodities"},   # silver
    {"ticker": "USO",  "bucket": "commodity", "group": "commodities"},   # crude oil

    # ── REITs ─────────────────────────────────────────────────────────────────
    {"ticker": "VNQ",  "bucket": "reit", "group": "reits"},
    {"ticker": "VNQI", "bucket": "reit", "group": "reits"},

    # ── Broad / satellite ─────────────────────────────────────────────────────
    {"ticker": "QQQ",  "bucket": "stock", "group": "satellite"},
    {"ticker": "IWM",  "bucket": "stock", "group": "satellite"},
]


def all_tickers() -> list[str]:
    return [e["ticker"] for e in ETF_UNIVERSE]


def bucket_for(ticker: str) -> Bucket | None:
    for e in ETF_UNIVERSE:
        if e["ticker"] == ticker:
            return e["bucket"]
    return None
