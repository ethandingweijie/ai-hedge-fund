"""A profile KPI must not rename a key the FMP stage already fills.

The LLM extractor is only ever asked for a profile's OWN keys
(`_framework_metrics_extract` sends `spec_built["clamps"]`). A separate
FMP-derived stage (`_fmp_risk_kpis`) then gap-fills a fixed canonical set.
So a profile key that renames a canonical concept can never be populated:
the model is asked for the bespoke name, FMP fills the canonical one, and
the card renders the bespoke one as a dash.

Memory / DRAM-NAND made this visible. A live MU run carried BOTH keys in the
same payload:

    gross_margin_pct     = 0.7257     <- FMP stage filled it
    memory_gross_margin  = missing    <- what the card rendered

The bespoke name had been chosen to satisfy the statement-line-item collision
guard. Only bare `gross_margin` is a line item, so `gross_margin_pct` — used
by eight other profiles — was safe all along.

**What this test deliberately does NOT assert.** A first cut of this guard
required every mandatory KPI to be FMP-fillable, on the theory that bespoke
keys predict low completeness. That is false: Money Center Bank (SG) scores
1.00 with `cet1_ratio`, `nim_pct` and `efficiency_ratio` — all bespoke, all
LLM-extracted, all reliably present in bank research. Roughly 48 profiles
would have failed such a rule while working perfectly in production. Whether
a KPI fills depends on whether the research carries it, not on who owns the
name. A stem-matching heuristic was tried next — flag any mandatory key containing
a canonical concept name. It produced eleven false positives against one real
catch: `organic_revenue_growth_pct` (organic is not total),
`auto_gross_margin_ex_credits` (excludes regulatory credits),
`holdco_net_debt_to_ebitda` (holdco, not consolidated), and similar. All are
genuinely different measures that merely share a word. That rule was dropped
rather than buried under an allowlist that the next author would simply
append to.

What remains is precise: the one real regression, pinned, plus a check that
the canonical set has not drifted from what the FMP stage emits.
"""

from __future__ import annotations

import inspect

from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK, _fmp_risk_kpis

# Exactly what _fmp_risk_kpis() can emit — kept honest by the test below.
CANONICAL_FMP_KEYS = {
    "net_debt_to_ebitda", "debt_to_ebitda", "cash_runway_years",
    "leverage_ratio", "operating_margin_pct", "revenue_growth_pct",
    "capex_intensity_pct", "gross_margin_pct", "fcf_margin_pct",
    "fcf_conversion_pct",
}

def test_the_canonical_set_matches_the_fmp_stage():
    """A guard on the guard: if _fmp_risk_kpis grows a field and this set is
    not updated, everything below silently stops protecting anything."""
    src = inspect.getsource(_fmp_risk_kpis)
    emitted = {
        line.split('out["')[1].split('"]')[0]
        for line in src.splitlines() if 'out["' in line
    }
    missing = emitted - CANONICAL_FMP_KEYS
    assert not missing, (
        f"_fmp_risk_kpis now emits {sorted(missing)} — add them to "
        f"CANONICAL_FMP_KEYS so the rename guard stays meaningful"
    )


def test_memory_profile_uses_the_canonical_gross_margin():
    """The specific regression, pinned."""
    keys = {k["key"] for k in SECTOR_KPI_FRAMEWORK["Memory / DRAM-NAND"]["kpis"]}
    assert "gross_margin_pct" in keys
    assert "memory_gross_margin" not in keys
