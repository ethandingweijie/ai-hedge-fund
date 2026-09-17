"""KPIs the engine can compute from filed numbers, so it stops reading them off a sentence.

The framework's KPI vectors were populated by an LLM extractor reading a deep
research report, with FMP filling whatever came back empty
(``_augment_metrics_with_fmp_risk``: "Extractor wins where it has an explicit
value — FMP only fills gaps"). For a KPI that is arithmetic on filed statements
that precedence is backwards, and it is not a hypothetical:

* 2026-09-16, seven minutes apart — Alibaba's GAAP ``operating_margin_pct`` came
  back **+10.0%** for 09988.HK and **−0.3%** for BABA. One field moved the
  composite from 1.266 to 1.0363 and opened an 18.7% gap between two listings'
  blended IVs ($209.03 vs $176.05 per ADS) on a price that agreed to 0.6%.
* Keppel (BN4.SI), single-listed, so ``resolve_company_metrics`` has no sibling
  to adopt from — ``roic_pct`` came back **1.09%** on a conglomerate whose
  published accounts do not support it, and the SG Conglomerate profile marks
  ``roic_pct`` *mandatory* and scores a quality band off it.

Both numbers were extracted, not computed. Neither run was wrong about the
company; each was a different sample from the same distribution over sentences.

This module computes the subset of framework KPIs that is pure arithmetic on the
annual series the DCF engine already fetches, and lets the computed value win.
Scope is deliberately narrow — see :func:`apply_overrides`.

Two design rules, both inherited from elsewhere in the repo rather than invented:

**Unavailable, not invented.** A KPI whose inputs are missing returns nothing at
all rather than a substitute. The peak-consensus gate states the same rule
(``tests/test_cyclical_peak_consensus.py::test_it_returns_none_without_normalised_earnings_or_equity``)
and the plan states it for ROIC explicitly: "If the floor is also ≤ 0 …
``roic_pct`` is unavailable (None, band fallback), not invented."

**No new bounds.** The only bound applied to any value here is the one the
framework already declares for that KPI. Nothing is clamped, winsorised or
floored beyond the plan's ROIC denominator floor, which the owner specified.
``tests/test_cyclical_peak_consensus.py::test_the_owner_bounds_are_the_only_new_bounds``
is the standing constraint this file is written under.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

#: Proxy for the effective tax rate behind NOPAT. Kept in step with
#: ``dcf_agent._EFFECTIVE_TAX_RATE`` by
#: ``tests/test_deterministic_kpis.py::test_the_tax_rate_agrees_with_the_dcf_engine``
#: rather than imported from it: ``dcf_agent`` imports the sector framework, so
#: a framework-side import of ``dcf_agent`` would be a cycle, and a lazy import
#: would hide the coupling from both modules' readers.
EFFECTIVE_TAX_RATE = 0.21

#: The two line items this module needs that ``run_dcf_agent`` did not already
#: request. Every other input is on the existing request list. Both were added
#: there in the same change as this module — requesting a field and copying it
#: into the row are separate acts, and doing only one of them has silently
#: produced ``None`` on every row four times now
#: (``tests/test_line_items_requested_are_read.py``).
NEW_LINE_ITEMS = ("inventory", "property_plant_equipment")

#: Both spellings exist in the framework: ``rd_intensity_pct`` on three profiles
#: and ``r_and_d_intensity_pct`` on one (whose risk band is the "Stagnation
#: Gauge"). Emitting only one leaves the other profile permanently LLM-sourced,
#: which is the exact failure mode ``tests/test_kpi_canonical_vocabulary.py``
#: exists to prevent.
_RD_SPELLINGS = ("rd_intensity_pct", "r_and_d_intensity_pct")


def _num(value: Any) -> Optional[float]:
    """A finite float, or None. ``bool`` is excluded because ``True`` is 1.0.

    A numeric STRING is coerced, exactly as ``dcf_agent._safe`` coerces it — this
    is deliberate and not an oversight. Every row this module reads has already
    been through ``_safe``, which does ``float(val)`` and returns None on
    ``TypeError``/``ValueError``, so being stricter here than the engine that
    built the rows would only mean a value the rest of the valuation uses is
    silently dropped from the cross-check of it.

    The string case that does matter is on the other side — the extracted metrics
    dict in :func:`apply_overrides`. There a ``"20%"`` fails this function, which
    is what makes it a gap rather than a claim of 0.20, and the override records
    the string verbatim.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _ratio(numerator: Any, denominator: Any) -> Optional[float]:
    """numerator / denominator, or None when either is missing or the base is 0.

    A zero or negative denominator is not an error to work around — it is the
    answer being undefined. Revenue of 0 means the series row is unusable, and
    inventing a margin for it is how a card comes to display a number no filing
    supports.
    """
    n, d = _num(numerator), _num(denominator)
    if n is None or d is None or d <= 0:
        return None
    return n / d


# ── ROIC ────────────────────────────────────────────────────────────────────
#
# NOPAT / invested capital, with the plan's denominator floor. The floor exists
# because buyback-heavy and asset-light names can report equity near or below
# zero, and a financing-side capital of −$2bn does not mean a negative return —
# it means the denominator has stopped measuring capital. Taking the max against
# the operating side (working capital + net PP&E) substitutes a denominator that
# still does.
#
# What is deliberately NOT here:
#
# * **No clamp on the result.** A small positive denominator still yields a large
#   ROIC. The quality bands saturate (the SG Conglomerate band tops out at
#   min 0.12 → 1.1 "strong"), so a large value cannot run away into the
#   composite, and capping it here would be a new bound nobody chose. The raw
#   denominator is recorded in the basis so an absurd ratio is diagnosable
#   rather than mysterious.
# * **No FMP ``investedCapital``.** ``api.py`` derives that as
#   ``total_debt + total_equity − cash_and_equivalents`` and does NOT subtract
#   ``short_term_investments``, so reading it off a row would silently use a
#   different capital definition from the one specified here.
# * **No bank carve-out.** ROIC on a balance-sheet financial has no
#   interpretation for the reason Phase 1.2A exists — the deposits ARE the
#   product. It does not need one: no profile in
#   ``BALANCE_SHEET_FINANCIAL_PROFILES`` declares ``roic_pct``, so the
#   eligibility filter in :func:`apply_overrides` never writes it there.
#   ``test_no_balance_sheet_financial_profile_is_eligible_for_roic`` pins that.


def _roic(row: dict, tax_rate: float) -> tuple[Optional[float], dict]:
    """Return ``(roic, basis)``. ``roic`` is None when it cannot be computed.

    ``basis`` is always returned, including on the None paths, so a run can say
    *why* the KPI is absent rather than leaving a dash with no explanation.
    """
    # NOPAT's numerator stays `ebit`, with `operating_income` as the fallback,
    # because that is the quantity the DCF engine itself uses
    # (`nopat = ebit * (1 - _EFFECTIVE_TAX_RATE)`), and a cross-check that
    # silently measures something other than the engine's own arithmetic stops
    # being a cross-check.
    #
    # This is a live open question, not a settled one, and it is recorded here
    # rather than decided here: FMP derives `ebit` bottom-up (pre-tax income plus
    # interest expense), so it still carries the income earned by the cash and
    # short-term investments that the capital denominator below DEDUCTS — the
    # same money counted in the numerator and removed from the denominator. On
    # Alibaba's filed FY2026 that is 139,180m of `ebit` against 59,665m of
    # `operatingIncome`. Switching the preference would be more economically
    # consistent and would change ROIC for every cash-rich name, so it needs the
    # owner's call and its own golden diff.
    #
    # Inert either way today: `operating_income` is unrequested (see the KNOWN
    # GAP note in :func:`compute_from_series`), so the fallback never runs.
    # `ebit_source` records which field was used so that if the gap closes, a
    # ROIC move is attributable to the numerator rather than to the company.
    ebit = _num(row.get("ebit"))
    ebit_source = "ebit"
    if ebit is None:
        ebit = _num(row.get("operating_income"))
        ebit_source = "operating_income"
    equity = _num(row.get("total_equity"))
    basis: dict = {"tax_rate": tax_rate, "ebit": ebit,
                   "ebit_source": ebit_source if ebit is not None else None,
                   "total_equity": equity}

    if ebit is None or equity is None:
        basis["unavailable"] = (
            "ebit" if ebit is None else "total_equity")
        return None, basis

    debt = _num(row.get("total_debt")) or 0.0
    cash = _num(row.get("cash_and_equivalents")) or 0.0
    # A missing short_term_investments is treated as zero, which OVERSTATES
    # capital and so UNDERSTATES ROIC. That is the conservative direction, and
    # it is the same convention api.py's own invested_capital derivation uses.
    # The HK and SG fallback providers omit the line entirely today; Phase 1.4
    # maps it, after which this stops being a conservative guess and starts
    # being the filed number.
    sti = _num(row.get("short_term_investments")) or 0.0
    financing = equity + debt - cash - sti

    receivable = _num(row.get("accounts_receivable")) or 0.0
    inventory = _num(row.get("inventory")) or 0.0
    payable = _num(row.get("accounts_payable")) or 0.0
    ppe = _num(row.get("property_plant_equipment")) or 0.0
    operating = (receivable + inventory - payable) + ppe

    capital = max(financing, operating)
    basis.update({
        "total_debt": debt, "cash_and_equivalents": cash,
        "short_term_investments": sti,
        "capital_financing_side": financing,
        "accounts_receivable": receivable, "inventory": inventory,
        "accounts_payable": payable, "property_plant_equipment": ppe,
        "capital_operating_floor": operating,
        "capital": capital,
        "floor_bound": operating > financing,
        "nopat": ebit * (1.0 - tax_rate),
    })
    if not math.isfinite(capital) or capital <= 0.0:
        basis["unavailable"] = "capital<=0 after the operating floor"
        return None, basis
    return (ebit * (1.0 - tax_rate)) / capital, basis


# ── The series → KPI pass ───────────────────────────────────────────────────


def compute_from_series(
    rows: Sequence[dict],
    *,
    tax_rate: float = EFFECTIVE_TAX_RATE,
) -> tuple[dict, dict]:
    """Compute the deterministic KPIs from an annual series.

    ``rows`` are ``dcf_agent._extract_annual_series`` rows: oldest first, each a
    plain dict, each guaranteed a positive ``revenue`` by that builder. Returns
    ``(kpis, basis)`` where ``kpis`` holds only the keys that could actually be
    computed — a missing input omits the key rather than writing None, so the
    caller's gap-fill still sees a gap.

    ``basis`` records the filing period the values were computed on plus the
    ROIC denominator breakdown. It is what makes a changed KPI attributable to a
    changed filing rather than a changed formula.

    Values are **latest annual**, not TTM. The series is what the DCF engine
    projects from, so the composite and the valuation then read the same fiscal
    year; a TTM margin scored against an FY-based DCF is the mismatch this
    removes. It is also the only basis that exists for HK and SG, whose
    statement-growth endpoints return nothing (verified empty for
    00700/01398/00005/00388/09988).
    """
    ordered = [r for r in (rows or []) if isinstance(r, dict)]
    if not ordered:
        return {}, {"unavailable": "no annual rows"}
    latest = ordered[-1]
    prior = ordered[-2] if len(ordered) > 1 else None

    revenue = _num(latest.get("revenue"))
    period = str(latest.get("period") or "") or None
    basis: dict = {"period": period, "observations": len(ordered),
                   "basis": "latest annual"}
    if revenue is None or revenue <= 0:
        basis["unavailable"] = "revenue<=0"
        return {}, basis

    kpis: dict = {}

    # Operating margin is operating income / revenue, and ``ebit`` is NOT a
    # stand-in for operating income. FMP derives ``ebit`` bottom-up (pre-tax
    # income plus interest expense), so it carries the whole non-operating line
    # — and for a company sitting on a large cash and investment portfolio that
    # line is most of the profit. Measured 2026-09-17 on the filed FY2026
    # statements: Alibaba reports ``operatingIncome`` 59,665m against ``ebit``
    # 139,180m on 1,023,670m of revenue (5.83% vs 13.60%), and JD reports
    # 3,595m against 27,398m on 1,275,204m (0.28% vs 2.15%). Scoring the
    # ``ebit`` version against FMP's own ``operatingProfitMarginTTM`` — which is
    # computed from ``operatingIncome`` — is what turned the 1.3 backward test
    # from a pass into a false alarm on both names.
    #
    # ``operating_income`` is a recorded KNOWN GAP in
    # tests/test_line_items_requested_are_read.py: the row builder copies it, the
    # request list never asks for it, so it is None on every FMP row today. That
    # makes this key fill-only-in-principle and never-written-in-practice until
    # the gap is closed. Requesting it is not free — it also activates the
    # dormant Priority-1 branch of the bank EBIT path
    # (``operating_income + abs(provisions)``), which moves bank valuations and
    # needs its own golden diff. Omitting the key here is the inert choice: the
    # extractor keeps supplying it, drift and all, and the omission is visible in
    # ``basis["computed"]`` rather than silent.
    op_income = _num(latest.get("operating_income"))
    op_margin = _ratio(op_income, revenue)
    if op_margin is not None:
        kpis["operating_margin_pct"] = op_margin
    elif op_income is None:
        basis["operating_margin_unavailable"] = (
            "operating_income is unrequested (KNOWN GAP); ebit is not a "
            "substitute — it includes non-operating income")

    gross_margin = _ratio(latest.get("gross_profit"), revenue)
    if gross_margin is not None:
        kpis["gross_margin_pct"] = gross_margin

    fcf_margin = _ratio(latest.get("free_cash_flow"), revenue)
    if fcf_margin is not None:
        kpis["fcf_margin_pct"] = fcf_margin

    capex = _num(latest.get("capital_expenditure"))
    if capex is not None:
        # Signed negative in the feed (an outflow). Intensity is a magnitude;
        # the sign carries no information here and leaving it in would render a
        # negative capex intensity.
        kpis["capex_intensity_pct"] = abs(capex) / revenue

    rnd = _num(latest.get("research_and_development"))
    if rnd is not None:
        intensity = rnd / revenue
        for spelling in _RD_SPELLINGS:
            kpis[spelling] = intensity

    prior_revenue = _num(prior.get("revenue")) if prior else None
    growth = _ratio(revenue, prior_revenue)
    if growth is not None:
        kpis["revenue_growth_pct"] = growth - 1.0
    elif prior is None:
        basis["revenue_growth_unavailable"] = "only one annual row"

    roic, roic_basis = _roic(latest, tax_rate)
    basis["roic"] = roic_basis
    if roic is not None:
        kpis["roic_pct"] = roic

    basis["computed"] = sorted(kpis)
    return kpis, basis


# ── The precedence flip ─────────────────────────────────────────────────────


def eligible_kpi_keys(
    profile_name: str,
    framework: Optional[dict] = None,
) -> frozenset[str]:
    """The KPI keys this profile declares FMP-derivable (``extractor_only: False``).

    This is the whole of the blast radius: a deterministic value is written only
    for a key the profile itself has already marked as not needing an extractor.
    The 464 ``extractor_only: True`` KPIs — CET1, NIM, occupancy, same-store
    sales, wafer starts — stay exactly where they are, because they are quoted
    in research and are not arithmetic on a filed statement.
    """
    if framework is None:
        from src.data.sector_kpi_framework import SECTOR_KPI_FRAMEWORK
        framework = SECTOR_KPI_FRAMEWORK
    spec = (framework or {}).get(profile_name or "")
    if not isinstance(spec, dict):
        return frozenset()
    return frozenset(
        str(k.get("key")) for k in (spec.get("kpis") or [])
        if isinstance(k, dict) and k.get("extractor_only") is False and k.get("key")
    )


def apply_overrides(
    metrics: Optional[dict],
    deterministic: dict,
    profile_name: str,
    *,
    framework: Optional[dict] = None,
    basis: Optional[dict] = None,
) -> tuple[dict, list[dict]]:
    """Return ``(new_metrics, overrides)`` with the deterministic values winning.

    Never mutates ``metrics``. A key is written only when all three hold: the
    profile marks it ``extractor_only: False``, this pass computed a value for
    it, and the value differs from what is there. The third condition matters —
    an override list that records no-ops makes a run look contested when the two
    sources already agreed, which is the common case and the point of having a
    deterministic cross-check at all.

    Each override is ``{"kpi", "llm", "deterministic"}``. ``llm`` is None when
    the extractor left a gap, which is a fill rather than a reversal and reads
    differently in an audit trail.
    """
    out = dict(metrics or {})
    if not deterministic:
        return out, []
    eligible = eligible_kpi_keys(profile_name, framework)
    if not eligible:
        return out, []

    overrides: list[dict] = []
    for key in sorted(deterministic):
        if key not in eligible:
            continue
        value = _num(deterministic[key])
        if value is None:
            continue
        existing = out.get(key)
        existing_num = _num(existing)
        if existing_num is not None and abs(existing_num - value) < 1e-12:
            continue
        overrides.append({"kpi": key, "llm": existing, "deterministic": value})
        out[key] = value

    if basis is not None and (overrides or deterministic):
        # Private key: stripped on entity-cache adoption and ignored by the card
        # renderer, the same convention as `_z_scores` and `_metrics_source`.
        out["_deterministic_kpis"] = {
            "overridden": [o["kpi"] for o in overrides],
            "computed": list(basis.get("computed") or sorted(deterministic)),
            "period": basis.get("period"),
        }
    return out, overrides
