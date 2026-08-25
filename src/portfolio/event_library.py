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

Windows are the curation; the benchmark numbers were aligned to FMP
stable EOD history during the P2 forward gate (2026-08-25).
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
        }


EVENTS: list[EventSpec] = [
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
