"""
src/portfolio/event_library.py
===============================
P2 — curated crisis/event library for the quantitative replay engine.

Each event carries:
  • key / name        — stable identifiers (keys are the API contract)
  • window            — first/last stress dates (inclusive)
  • curated benchmark stats — SPY/QQQ window return + max drawdown as
    curated reference numbers. The replay engine RECOMPUTES both live
    from price history and flags divergence beyond _BENCH_TOLERANCE_PP
    (a data-quality tripwire, e.g. FMP split adjustment drift).
  • macro snapshot    — categorical 5-dim regime description of the
    event, keyed identically to regime_state.json's `regime` block
    (risk_appetite, rate_direction, dollar_trend, volatility_regime,
    recession_risk) so the engine can score similarity against today's
    regime deterministically.
  • sector_performance — per-GICS-sector window returns (the 11 SPDR
    sector ETFs), curated reference numbers sorted best→worst. Same
    calibration recipe as the benchmarks (FMP stable EOD, 1dp). Sector
    ETFs that did not exist yet (XLRE pre-2015, XLC pre-2018) fail the
    coverage guard and are honestly omitted, not zero-filled.

Windows are the curation; the benchmark and sector numbers were aligned
to FMP stable EOD history during the P2 forward gate (2026-08-25) and
the sector/dot-com calibration (2026-08-26).

LIBRARY_VERSION must be bumped whenever event content changes in a way
that makes cached replay payloads stale — it is baked into the replay
cache key (snapshot_hash) so pre-bump caches recompute instead of
serving outdated payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Bumped when curated event content changes (new events, re-calibrated
# numbers) so cached replays keyed on snapshot_hash miss and recompute.
LIBRARY_VERSION = 2


# Regime vocabulary — must stay aligned with macro_regime.py outputs so
# similarity scoring is exact-match on these labels.
RISK_APPETITE = ("risk-on", "mixed", "risk-off")
RATE_DIRECTION = ("cutting", "neutral", "hiking")
DOLLAR_TREND = ("falling", "neutral", "rising")
VOLATILITY_REGIME = ("low", "medium", "high", "extreme")
RECESSION_RISK = ("low", "medium", "high", "severe")

# Curated-vs-live benchmark divergence that trips the cross-check flag.
_BENCH_TOLERANCE_PP = 8.0


@dataclass(frozen=True)
class MacroSnapshot:
    risk_appetite: str
    rate_direction: str
    dollar_trend: str
    volatility_regime: str
    recession_risk: str
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "risk_appetite": self.risk_appetite,
            "rate_direction": self.rate_direction,
            "dollar_trend": self.dollar_trend,
            "volatility_regime": self.volatility_regime,
            "recession_risk": self.recession_risk,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class SectorPerf:
    """One GICS sector's window return during the event (via its SPDR ETF)."""
    sector: str
    symbol: str          # sector SPDR ETF (XLK, XLF, …)
    return_pct: float    # curated window return, 1dp (live-free reference)

    def as_dict(self) -> dict:
        return {
            "sector": self.sector,
            "symbol": self.symbol,
            "return_pct": self.return_pct,
        }


@dataclass(frozen=True)
class EventSpec:
    key: str
    name: str
    start: str                    # YYYY-MM-DD (first stress day)
    end: str                      # YYYY-MM-DD (last stress day)
    spy_return_pct: float         # curated reference (live cross-checked)
    spy_max_dd_pct: float         # negative, curated reference
    qqq_return_pct: float
    qqq_max_dd_pct: float
    macro: MacroSnapshot
    tags: tuple[str, ...] = field(default_factory=tuple)
    # Per-sector window returns, sorted best→worst. Empty for synthetic
    # test events; every curated event carries its calibrated set.
    sectors: tuple[SectorPerf, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "window": {"start": self.start, "end": self.end},
            "benchmarks": {
                "spy_return_pct": self.spy_return_pct,
                "spy_max_dd_pct": self.spy_max_dd_pct,
                "qqq_return_pct": self.qqq_return_pct,
                "qqq_max_dd_pct": self.qqq_max_dd_pct,
            },
            "macro": self.macro.as_dict(),
            "tags": list(self.tags),
            "sector_performance": [s.as_dict() for s in self.sectors],
        }


EVENTS: list[EventSpec] = [
    EventSpec(
        key="dotcom_2000",
        name="Dot-com bust (2000–01)",
        start="2000-03-24",       # SPY pre-bust peak
        end="2001-09-21",         # SPY post-bust trough (post-9/11 low)
        spy_return_pct=-36.7, spy_max_dd_pct=-36.7,
        qqq_return_pct=-76.1, qqq_max_dd_pct=-76.1,
        macro=MacroSnapshot(
            risk_appetite="risk-off", rate_direction="cutting",
            dollar_trend="rising", volatility_regime="high",
            recession_risk="high",
            notes="Tech/telecom bubble deflation; Fed cut 475bp through "
                  "2001; 2001 recession. A breadth event — defensives "
                  "(staples, utilities) ROSE while tech fell ~70%.",
        ),
        tags=("equity-bubble", "tech", "recession"),
        sectors=(
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=14.2),
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=12.3),
            SectorPerf(sector="Energy", symbol="XLE", return_pct=-10.6),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-12.0),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-21.7),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-25.4),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-25.7),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-29.7),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-71.3),
            # Real Estate (XLRE) and Communication Services (XLC) ETFs
            # did not exist yet — coverage guard excludes them honestly.
        ),
    ),
    EventSpec(
        key="gfc_2008",
        name="Global Financial Crisis (2007–09)",
        start="2007-10-09",       # SPY all-time pre-crisis peak
        end="2009-03-09",         # SPY crisis trough
        spy_return_pct=-56.5, spy_max_dd_pct=-56.5,
        qqq_return_pct=-51.8, qqq_max_dd_pct=-53.6,
        macro=MacroSnapshot(
            risk_appetite="risk-off", rate_direction="cutting",
            dollar_trend="rising", volatility_regime="extreme",
            recession_risk="severe",
            notes="Emergency Fed cuts; flight to safety; banking-system "
                  "solvency stress. Peak-to-trough equity bear market.",
        ),
        tags=("credit", "banking", "recession"),
        sectors=(
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=-30.6),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-39.7),
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=-44.9),
            SectorPerf(sector="Energy", symbol="XLE", return_pct=-48.8),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-52.2),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-57.5),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-58.2),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-63.3),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-82.5),
            # XLRE/XLC did not exist yet — coverage guard excludes them.
        ),
    ),
    EventSpec(
        key="euro_2011",
        name="Eurozone Sovere Debt Crisis (2011)",
        start="2011-05-02",
        end="2011-10-03",
        spy_return_pct=-19.3, spy_max_dd_pct=-19.3,
        qqq_return_pct=-13.3, qqq_max_dd_pct=-16.1,
        macro=MacroSnapshot(
            risk_appetite="risk-off", rate_direction="hiking",
            dollar_trend="rising", volatility_regime="high",
            recession_risk="high",
            notes="ECB hiked into the crisis (Apr/Jul 2011); sovereign "
                  "default risk repriced peripheral Europe.",
        ),
        tags=("sovereign", "europe", "policy-error"),
        sectors=(
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=-1.1),
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=-7.4),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-13.4),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-13.7),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-16.9),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-26.5),
            SectorPerf(sector="Energy", symbol="XLE", return_pct=-28.8),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-29.7),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-30.8),
            # XLRE/XLC did not exist yet — coverage guard excludes them.
        ),
    ),
    EventSpec(
        key="q4_2018",
        name="Q4-2018 Fed-hike / liquidity shock",
        start="2018-09-20",
        end="2018-12-24",
        spy_return_pct=-20.2, spy_max_dd_pct=-20.2,
        qqq_return_pct=-22.3, qqq_max_dd_pct=-22.9,
        macro=MacroSnapshot(
            risk_appetite="risk-off", rate_direction="hiking",
            dollar_trend="rising", volatility_regime="high",
            recession_risk="medium",
            notes="Fed hiked Dec-2018 amid QT; year-end liquidity air "
                  "pocket; reversed on the Jan-2019 pivot.",
        ),
        tags=("rates", "liquidity", "policy"),
        sectors=(
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=-3.3),
            SectorPerf(sector="Real Estate", symbol="XLRE", return_pct=-11.0),
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=-11.9),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-14.6),
            SectorPerf(sector="Communication Services", symbol="XLC", return_pct=-20.3),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-21.9),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-22.4),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-23.0),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-23.3),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-24.5),
            SectorPerf(sector="Energy", symbol="XLE", return_pct=-28.3),
        ),
    ),
    EventSpec(
        key="covid_2020",
        name="Covid crash (Feb–Mar 2020)",
        start="2020-02-19",       # SPY pre-crash peak
        end="2020-03-23",         # SPY trough
        spy_return_pct=-34.1, spy_max_dd_pct=-34.1,
        qqq_return_pct=-28.1, qqq_max_dd_pct=-28.6,
        macro=MacroSnapshot(
            risk_appetite="risk-off", rate_direction="cutting",
            dollar_trend="rising", volatility_regime="extreme",
            recession_risk="severe",
            notes="Sudden-stop shock; emergency cuts to zero + QE in "
                  "weeks; sharpest drawdown in history, fastest V-recovery.",
        ),
        tags=("exogenous", "liquidity", "recession"),
        sectors=(
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=-24.6),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-28.3),
            SectorPerf(sector="Communication Services", symbol="XLC", return_pct=-30.0),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-31.5),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-33.8),
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=-36.0),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-36.6),
            SectorPerf(sector="Real Estate", symbol="XLRE", return_pct=-38.3),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-42.0),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-43.3),
            SectorPerf(sector="Energy", symbol="XLE", return_pct=-57.0),
        ),
    ),
    EventSpec(
        key="rate_shock_2022",
        name="Rate shock / inflation bear (2022)",
        start="2022-01-03",
        end="2022-10-12",
        spy_return_pct=-25.4, spy_max_dd_pct=-25.4,
        qqq_return_pct=-34.6, qqq_max_dd_pct=-34.6,
        macro=MacroSnapshot(
            risk_appetite="risk-off", rate_direction="hiking",
            dollar_trend="rising", volatility_regime="high",
            recession_risk="medium",
            notes="Aggressive hiking cycle vs 40-year-high inflation; "
                  "duration assets repriced; stock/bond correlation turned "
                  "positive.",
        ),
        tags=("rates", "inflation", "duration"),
        sectors=(
            SectorPerf(sector="Energy", symbol="XLE", return_pct=40.0),
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=-12.3),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-12.6),
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=-13.2),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-19.2),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-23.3),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-23.4),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-33.6),
            SectorPerf(sector="Real Estate", symbol="XLRE", return_pct=-33.8),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-33.9),
            SectorPerf(sector="Communication Services", symbol="XLC", return_pct=-39.1),
        ),
    ),
    EventSpec(
        key="svb_2023",
        name="SVB / regional-bank stress (2023)",
        start="2023-02-15",
        end="2023-03-15",
        spy_return_pct=-6.0, spy_max_dd_pct=-6.9,
        qqq_return_pct=-3.3, qqq_max_dd_pct=-6.7,
        macro=MacroSnapshot(
            risk_appetite="mixed", rate_direction="hiking",
            dollar_trend="neutral", volatility_regime="medium",
            recession_risk="medium",
            notes="Stress concentrated in regional banks (KRE ~−30%) and "
                  "duration-heavy balance sheets; broad indices fell "
                  "modestly (SPY ~−6%) — a dispersion event, not a "
                  "market-wide DD.",
        ),
        tags=("banking", "rates", "dispersion"),
        sectors=(
            SectorPerf(sector="Consumer Staples", symbol="XLP", return_pct=-0.9),
            SectorPerf(sector="Utilities", symbol="XLU", return_pct=-1.6),
            SectorPerf(sector="Technology", symbol="XLK", return_pct=-3.0),
            SectorPerf(sector="Communication Services", symbol="XLC", return_pct=-3.5),
            SectorPerf(sector="Health Care", symbol="XLV", return_pct=-4.4),
            SectorPerf(sector="Industrials", symbol="XLI", return_pct=-6.2),
            SectorPerf(sector="Real Estate", symbol="XLRE", return_pct=-7.7),
            SectorPerf(sector="Consumer Discretionary", symbol="XLY", return_pct=-8.6),
            SectorPerf(sector="Materials", symbol="XLB", return_pct=-8.6),
            SectorPerf(sector="Energy", symbol="XLE", return_pct=-12.6),
            SectorPerf(sector="Financials", symbol="XLF", return_pct=-14.5),
        ),
    ),
]


def events_as_dicts() -> list[dict]:
    return [e.as_dict() for e in EVENTS]


def get_event(key: str) -> EventSpec | None:
    for e in EVENTS:
        if e.key == key:
            return e
    return None


BENCH_TOLERANCE_PP = _BENCH_TOLERANCE_PP
