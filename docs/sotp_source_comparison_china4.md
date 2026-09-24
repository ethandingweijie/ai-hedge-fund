# SOTP source check: extractor vs Gemini pre-fill (BABA, PDD, JD, 3690.HK)

Owner, 2026-09-24: "before proceeding, model checks against SOTP extractor versus SOTP
pre-fill to gauge accuracy". Both sources were priced through the engine's own leg
(`_sotp_analyst_style`) with the SAME shares, FX, net debt and holdco pin, so the only
difference between the two columns is the segment inputs. Script: session scratchpad
`compare_sotp_sources.py`; raw output `sotp_compare.json`.

- **Extractor** = the task #27 snapshot (`src/data/sotp_assumptions_v1.json`): the NOTE-mode
  extractor over Goldman notes of 10 Aug 2026, validated then against the GS TP.
- **Gemini** = `scripts/build_industry_inputs.py --kind sotp` run today; entries sit PENDING in
  `src/data/industry_inputs.json` (not accepted, not in any blend). 3690.HK failed once
  (thinking budget exhausted, no JSON) and built on the retry.
- "engine" = holdco pinned to the curated 15%, net cash restated from FMP net debt, margins
  clamped at 0, the P/E-without-earnings fallback applied: what `run_dcf_agent` does to
  every source. "as cited" = the source's own holdco and net cash.

## Headline

| Name | Spot | Consensus | GS TP (Aug) | Extractor engine / cited | Gemini engine / cited |
|---|---|---|---|---|---|
| BABA | 110.78 | 180.57 | 186 | 125.84 / 154.41 | 147.66 / 141.24 |
| PDD | 79.20 | 103.22 | 145 | 141.32 / 140.16 | 148.11 / 137.52 |
| JD | 27.16 | 35.86 | 43 | 38.29 / 38.02 | 53.69 / 56.21 |
| 3690.HK | 72.00 | 110.72 | 123 | 148.82 / 160.65 | 148.04 / 165.50 |

Distance from consensus, engine-treated: extractor −30% / +37% / +7% / +34%; Gemini
−18% / +43% / +50% / +34%. Within ±30%: one name each (extractor JD, Gemini BABA). Neither
source is the more accurate one on this set; on three of four they miss in the same
direction by a similar amount, which points at the leg, not the inputs.

## What actually drives each number

**3690.HK is the same number from both sources because neither input is being used.** The
engine's P/E anchor needs positive EBIT. The extractor gives Food Delivery and Instashopping
no EBIT (its unit-economics evidence never became a field); Gemini cites Core Local
Commerce at −2.6% margin, which the bridge clamps to 0. In both cases the leg falls to
`EV/Rev (fallback)` at the segment classifier's 3.0x: 64% of the extractor NAV and 86% of
the Gemini NAV is that constant. The cited 12x-18x P/E range never prices. This is the
first thing to fix before either source can be judged on Meituan.

**BABA: the net-cash restatement, not the segments, is the biggest swing.** The GS note's
$68B net cash (cash + short-term investments + listed stakes after a haircut) is replaced
by FMP net debt of −$12.8B, worth $29 an ADS (154 → 126). Both sources take the same hit.
On segments the two agree on Taobao-Tmall revenue ($67B) and disagree on its margin:
Gemini cites the FY2025 reported 43.6% adjusted EBITA margin, the extractor uses GS's 30%
FY27E after quick-commerce investment. Forward beats trailing here; the Gemini figure is
correct as a citation and wrong as a forward input.

**PDD: both overshoot consensus by ~40% and agree with each other.** Main-app values are
$180B vs $186B; Temu comes out at $40B either way, by a 25x P/E on a 10.5% margin
(extractor, GS) or 1.1x on $39B revenue (Gemini). The overshoot is the domestic core at
12x-12.5x on a 36-42% margin against a market paying 8x; it is the China-platform
regime, not a source defect. The consensus is 30% above spot, so both sources are inside
the band that consensus itself misses.

**JD: Gemini overshoots by 50%, the extractor lands.** Gemini cites FY2025 actual JD Retail
revenue (RMB 1,126B, $168B) with a 4.6% margin and a 10.5x midpoint; the extractor uses
GS's FY26E $152B at 4.0% and 9x, reflecting the food-delivery investment cycle. Gemini
also carries $7.7B of associates against GS's $2B. The trailing-as-forward substitution
is the defect; the store's own note says "actuals stand in for a forward year".

## Gemini pre-fill quality, on the citations themselves

- Citation coverage 4/4 = 1.0; every revenue, margin and multiple range carries a quote and
  a source URL (HKEX filings, annual reports, Phillip Securities).
- 3690.HK Core Local Commerce revenue is the GROUP total ("Total revenues 260,826,094"),
  a misattribution the review gate exists to catch.
- Three of four cite FY2025 actuals as the forward year; the bridge accepts that
  (`FORWARD_PERIOD_KINDS`) and notes it, but a SOTP on trailing revenue and trailing
  margin is a different valuation from GS's FY26E/FY27E one.
- Holdco discounts are cited at 30% (BABA, PDD), 20% (JD), 10% (3690.HK) with a stated
  basis; the engine overrides all four to the curated 15%.
- Multiple ranges are sane and narrow (BABA core 8-11x, JD Retail 8-13x, Meituan core
  12-18x) and bracket the GS multiples in every case.

## Verdict

The Gemini pre-fill is a usable segment source: its segments and multiple ranges match
the broker framework, and its two real misses (group-for-segment revenue on Meituan,
trailing-for-forward on three names) are exactly the kind of thing an owner review
catches on the pre-fill page. The extractor is not more accurate; its one advantage is
that GS had already done the forward-year work. Neither source is being priced faithfully
today, and the engine defects dominate the source difference:

1. `EV/Rev (fallback)` at 3.0x prices any P/E segment without positive EBIT. A cited P/E
   segment with no earnings should use the cited EV/Rev range if there is one, or go
   degraded with the reason, never a classifier constant.
2. Margin clamp at 0 feeds the same fallback; a loss-making segment with a cited EV/Rev
   range should price on that range.
3. Net-cash restatement from FMP net debt drops short-term investments and stakes; on
   BABA that is $29 an ADS. The cited figure should win when it is sourced, with the FMP
   figure as the check.
4. The pre-fill prompt should ask for the NEXT fiscal year's consensus segment revenue
   and margin, and the review page should show the period beside every figure.

Recommended order: fix 1-2 (engine), tighten 4 (prompt), re-run this comparison, then
proceed with the change set (bridge flip, retire promotion, pre-fill by profile).
