# Wave 9 (industrials, materials, metals, transport): Stage 0 to Stage 4 scorecard

Owner decisions of 2026-09-27 (Stage 1 watch-label resolutions, Stage 3 profile architecture, the data
guardrails and the Gate 0/1/2 protocol). Both columns measure the same 54 names on the local store
(`docs/baselines/baseline_wave9.json`, `docs/baselines/after_wave9.json`); review-gated inputs are not
accepted in either, so a reviewed-input leg (RNAV, SOTP (analyst), backlog) prices on its market fallback.

| Engine | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing | Proxied weight | No value | Routed by the ladder |
|---|---|---|---|---|---|---|---|
| Stage 0 (before Wave 9) | 5 of 30 | 16 of 53 | 17 of 54 | 1 | 6 | 1 | 44 |
| Stage 4 (Wave 9 engine) | 15 of 30 | 27 of 54 | 27 of 54 | 8 | 0 | 0 | 0 |

## What was built (Gates 1 and 2)

- **Scope.** 32 FMP labels brought into routing scope (map version 23, 99 labels), each on the row the owner's
  Stage 3 table names. HK and SG `Conglomerates` route to Asian Holding Company (Look-Through); the US row is
  Blended Industrial OpCo. SG airlines, shipping, packaging and E&C take their Wave 9 rows; SIA Engineering
  keeps its Wave 3 profile by an explicit override.
- **Pins.** About 70 pins across the categories, frozen in `TICKER_SECTOR_LOOKUP` and the SGX table
  (Keppel and Jardine Matheson on Asian Holding Company, Singapore Airlines on Airlines).
- **Profiles.** Nineteen new profiles and four re-specified ones (Capital Goods, Airlines, Rail / Logistics,
  Steel / Metals) at the owner's weights. Every leg that needs a reviewed input (RNAV for miners, shipping and
  aggregates; SOTP (analyst) for the Asian holdcos) falls back to a market leg until the input is accepted.
- **Cohorts.** CATL carved out of the HKSE Electrical Equipment median. The US Electrical Equipment median
  trims members above 2 sigma on forward EV/EBITDA; on 2026-09-27 Vertiv sits at the median and Bloom
  Energy is the member the rule removes. Six sub-cohorts (LTL, truckload, parcel, minimill, integrated steel,
  aggregates) price their members ahead of the label.
- **Gates.** GATE_MARGIN_PEAK moves Capital Goods' EBITDA and P/E legs to mid-cycle when the operating margin
  sits above its seven-year median by more than 1.5 sigma. GATE_BACKLOG_MULTIPLE scales Long-Cycle E&C's
  EV/EBITDA by accepted backlog cover over 1.2x revenue (unscaled, and recorded, without an accepted input).
- **Guardrails.** EBITDA rebuilt as operating income plus D&A on the capital-goods, transport and rental
  profiles, the feed value kept and a gap over 10% flagged. Lease liabilities are already inside FMP's total
  debt (Delta: 2.41 + 12.51 + 6.16 = 21.08bn), so no second capitalisation was added. FCF Yield on four
  capital-heavy profiles caps capex at 1.0x D&A. Only a property profile reads P/B as P/NAV; a miner's or a
  fleet's published NAV prices at par.
- **Asian holdco discount.** Indexed to the exchange's conglomerate median P/B (discount = 1 − P/B) inside
  the owner's 25-40% band.

## PROPOSED constants (the owner has not chosen these)

| Constant | Value | Derivation |
|---|---|---|
| Marketplace / Salvage Platform weights | Forward P/E .40, EV/EBITDA .40, FCF Yield .20 | the Information Services split, since Copart's economics are asset-light fee income |
| Blended Industrial OpCo weights | Forward EV/EBIT .50, FCF Yield .50 | the owner named the two legs, not the split |
| Battery & Energy Storage weights | Forward EV/EBITDA .60, EV/Revenue .40 | the owner named the comps, not the legs |
| Maintenance capex ceiling | 1.0x D&A | the owner named the index (D&A), not the multiple |

## Points for the owner

- **Holdco band binds.** The HKSE conglomerate median P/B is 0.41, an implied 59% discount; the band clamps it
  to 40%. The 25-40% band was not set in Wave 8 (Wave 8 fixed the CapitaLand and Swire reads); it is the
  owner's Wave 9 figure and is recorded as such.
- **Toll Road P/B.** The Stage 3 table gives Toll Road / Infrastructure a P/B leg at .20 while the note says to
  ignore book for concession assets. Built as the table says; the owner should confirm or drop the leg.
- **Not buildable from the feed.** C1 cash costs, mill operating rates, GTV for Copart, concession expiry,
  fuel-hedge books, escalator magnitudes, the ROIIC >20% gate's effect, KRX battery comps (no KRX in the
  store), pension and litigation adjustments, commodity price decks, fleet disposal margins and tonnage.
  Each needs a reviewed input or a new data source.
- **Stage 5 pre-fills needed.** NAV for the miners, shipping and aggregates (a non-property prompt), SOTP for
  the Asian holdcos, backlog for Quanta and China Railway, and a battery peer input for CATL.
- **Golden replay gap (pre-existing).** Replay prices a normalised leg on the plain cohort multiple (12.52x
  for FCX) where the live engine takes the dynamic through-cycle multiple (7.36x). Recorded, not fixed.

## Stage 4 result and what the owner should look at

The industry ladder no longer decides any of the 54 (44 at Stage 0). Within ±30% of consensus rises from 5 to
15 of 30, within ±30% of spot from 16 to 27 of 54, backtests passed from 17 to 27. Proxied weight falls from
6 names to none.

**Anchor missing on 8 names, all by design until Stage 5 inputs land.** The four Asian holdcos price on P/BV
.85 and DDM .15 because SOTP (analyst) has no accepted input. NEM and Zijin Gold price on EV/EBITDA .85 because
RNAV has none. Vertiv and Sanhua fired the margin-peak gate, so their Forward EV/EBITDA anchor moved to
EV/EBITDA (norm).

**Outliers to review before any pre-fill is accepted.**

| Name | IV vs spot | Why |
|---|---|---|
| Jardine Matheson (J36.SI) | +425% | P/BV .85 on consolidated book: the look-through fallback reads group equity the holdco does not own. Needs its SOTP input, or a minority-adjusted book. |
| Jiangxi Copper (00358.HK), CMOC (03993.HK), Southern Copper | −85%, −54%, −67% | EV/EBITDA (norm) carries the whole blend with RNAV unaccepted; the five-year normalised EBITDA sits far below today's copper-price EBITDA. |
| COSCO Shipping (01919.HK) | +181% | the same leg in reverse: normalised EBITDA includes the 2021-22 freight spike. |
| CRH | +49% | the aggregates sub-cohort multiple against CRH's larger, lower-multiple building-products mix. |

## Gate 0: feed EBITDA against the operating bridge (flagged over 10%)

Latest fiscal year from FMP; the bridge is operating income plus D&A. "Bridge prices" says whether the
profile rebuilds EBITDA through the bridge (capital goods, E&C, rental, distribution, transport); a flag
on any other profile is recorded, not acted on. Full table: `docs/baselines/w9_gate0_ebitda_bridge.json`.

| Ticker | Profile | FY | Gap | Bridge prices |
|---|---|---|---|---|
| IP | Packaging & Paper | 2025-12-31 | 757.1% | no |
| DOW | Commodity Chemicals & Ag Inputs | 2025-12-31 | 61.6% | no |
| 00001.HK | Asian Holding Company (Look-Through) | 2025-12-31 | 60.9% | no |
| 01772.HK | Commodity Chemicals & Ag Inputs | 2025-12-31 | 44.8% | no |
| 00669.HK | Consumer Durables | 2025-12-31 | 34.4% | no |
| AA | Base Metals | 2025-12-31 | 33.6% | no |
| CTVA | Commodity Chemicals & Ag Inputs | 2025-12-31 | 19.6% | no |
| 01919.HK | Container & Bulk Shipping | 2025-12-31 | 15.8% | yes |
| 03993.HK | Diversified Miners | 2025-12-31 | 14.8% | no |
| NEM | Precious Metals | 2025-12-31 | 13.0% | no |
| BN4.SI | Asian Holding Company (Look-Through) | 2025-12-31 | 12.2% | no |
| CAT | Capital Goods | 2025-12-31 | 10.3% | yes |
| VMC | Aggregates & Cement | 2025-12-31 | 10.2% | no |

## Per-ticker record

| Ticker | Profile (Stage 0 → Stage 4) | Anchor | IV vs spot, Stage 0 | IV vs spot, Stage 4 | vs consensus, Stage 0 | vs consensus, Stage 4 | Backtest | Flag |
|---|---|---|---|---|---|---|---|---|
| CAT | Capital Goods | Forward EV/EBITDA | -46% | -23% | -55% | -36% | passed | — |
| DE | Capital Goods | Forward EV/EBITDA | -46% | -25% | -49% | -29% | fired | — |
| PH | Mature Platform → Capital Goods | Forward EV/EBITDA | -51% | -12% | -59% | -26% | passed | — |
| ETN | Mature Platform → Capital Goods | Forward EV/EBITDA | -53% | -24% | -59% | -33% | passed | — |
| VRT | Mature SaaS → Capital Goods | Forward EV/EBITDA | -65% | -63% | -76% | -74% | fired | — |
| PWR | Growth SaaS → Long-Cycle E&C | Backlog-Gated EV/EBITDA | -66% | -46% | -72% | -56% | passed | — |
| GWW | Mature SaaS → Industrial Distribution | Forward EV/EBITDA | -47% | -0% | -50% | -6% | passed | — |
| URI | Mature SaaS → Equipment Rental | EV/EBITDA | -14% | -17% | -27% | -29% | passed | — |
| WM | Levered Subscription → Waste & Environmental Services | Forward EV/EBITDA | -38% | +1% | -48% | -14% | fired | — |
| CTAS | Mature Platform → Industrial Route & Uniform Services | Forward EV/EBITDA | -55% | -47% | -61% | -55% | passed | — |
| MMM | Levered Subscription → Blended Industrial OpCo | Forward EV/EBIT | -65% | -32% | -69% | -40% | fired | — |
| HON | Capital Goods → Blended Industrial OpCo | Forward EV/EBIT | -14% | -2% | -29% | -20% | fired | — |
| DAL | Airlines | Forward EV/EBITDA | -13% | -18% | -31% | -34% | passed | — |
| UNP | Mature Platform → Rail / Logistics | EV/EBITDA | -26% | -1% | -39% | -19% | passed | — |
| ODFL | Mature Platform → Trucking & Parcel Logistics | Forward EV/EBITDA | -49% | -10% | -62% | -34% | passed | — |
| UPS | Rail / Logistics → Trucking & Parcel Logistics | Forward EV/EBITDA | +63% | +31% | +29% | +3% | fired | — |
| FDX | Rail / Logistics → Trucking & Parcel Logistics | Forward EV/EBITDA | +70% | +10% | +46% | -6% | passed | — |
| LIN | Specialty Chemicals | EV/EBITDA | -21% | -21% | -34% | -34% | fired | — |
| SHW | Levered Subscription → Specialty Chemicals | EV/EBITDA | -49% | -25% | -57% | -37% | fired | — |
| DOW | Mature SaaS → Commodity Chemicals & Ag Inputs | EV/EBITDA (norm) | -31% | -12% | -44% | -29% | passed | — |
| CTVA | Mature SaaS → Commodity Chemicals & Ag Inputs | EV/EBITDA (norm) | -30% | -42% | -42% | -52% | fired | Cyclical_Peak_Consensus |
| CRH | Mature SaaS → Aggregates & Cement | Forward EV/EBITDA | +7% | +49% | -33% | -7% | passed | Growth_Inflection_Speculative |
| VMC | Mature SaaS → Aggregates & Cement | Forward EV/EBITDA | -43% | -8% | -56% | -28% | fired | — |
| NUE | Specialty Chemicals → Steel / Metals | EV/EBITDA (Norm) | -13% | +25% | -19% | +16% | fired | — |
| FCX | Mining (Major) → Base Metals | EV/EBITDA (norm) | -53% | -46% | -53% | -45% | passed | — |
| SCCO | Mature Platform → Base Metals | EV/EBITDA (norm) | -48% | -67% | -35% | -58% | fired | Cyclical_Peak_Consensus |
| NEM | Mining (Major) → Precious Metals | RNAV (published) | -22% | -2% | -31% | -13% | passed | Cyclical_Peak_Consensus |
| AA | Mature SaaS → Base Metals | EV/EBITDA (norm) | +54% | +31% | +4% | -12% | passed | — |
| IP | Mature SaaS → Packaging & Paper | EV/EBITDA (norm) | -68% | -55% | -78% | -68% | fired | — |
| PKG | Mature SaaS → Packaging & Paper | EV/EBITDA (norm) | -21% | -31% | -31% | -40% | fired | — |
| 03808.HK | Mature Platform → Capital Goods | Forward EV/EBITDA | +108% | +14% | — | — | passed | — |
| 02050.HK | Mature SaaS → Capital Goods | Forward EV/EBITDA | -30% | -7% | — | — | skipped | — |
| 03750.HK | Capital Goods → Battery & Energy Storage | Forward EV/EBITDA | -28% | -36% | — | — | skipped | — |
| 00390.HK | Hyperscaler / Tech Conglomerate → Long-Cycle E&C | Backlog-Gated EV/EBITDA | +124% | +155% | — | — | fired | — |
| 00669.HK | Consumer Durables | EV/EBITDA | -14% | -14% | — | — | passed | — |
| 00267.HK | Levered Subscription → Asian Holding Company (Look-Through) | SOTP (analyst) | — | +56% | — | — | fired | — |
| 00001.HK | Capital Goods → Asian Holding Company (Look-Through) | SOTP (analyst) | -47% | +1% | — | — | passed | — |
| 00177.HK | Mature SaaS → Toll Road / Infrastructure (HK) | DDM | +31% | -4% | — | — | fired | — |
| 00293.HK | Mature Platform → Airlines | Forward EV/EBITDA | +267% | +4% | — | — | passed | — |
| 00066.HK | Mature SaaS → Rail / Logistics | EV/EBITDA | -12% | -39% | — | — | passed | — |
| 01766.HK | Mature SaaS → Capital Goods | Forward EV/EBITDA | +72% | +12% | — | — | passed | — |
| 01919.HK | Capital Goods → Container & Bulk Shipping | EV/EBITDA (norm) | +80% | +181% | — | — | fired | — |
| 00914.HK | Capital Goods → Aggregates & Cement | Forward EV/EBITDA | +62% | +109% | — | — | fired | — |
| 03993.HK | Mature SaaS → Diversified Miners | EV/EBITDA (norm) | +29% | -54% | — | — | passed | Cyclical_Peak_Consensus |
| 01378.HK | Mature Platform → Base Metals | EV/EBITDA (norm) | +195% | +53% | — | — | passed | Cyclical_Peak_Consensus |
| 00358.HK | Mature SaaS → Base Metals | EV/EBITDA (norm) | -12% | -85% | — | — | fired | Cyclical_Peak_Consensus |
| 02899.HK | Mature SaaS → Precious Metals | RNAV (published) | +14% | +8% | — | — | passed | Cyclical_Peak_Consensus |
| 01772.HK | Capital Goods → Commodity Chemicals & Ag Inputs | EV/EBITDA (norm) | -73% | -45% | — | — | passed | — |
| BN4.SI | Conglomerate / Industrial (SG) → Asian Holding Company (Look-Through) | SOTP (analyst) | -38% | -8% | — | — | passed | — |
| J36.SI | Conglomerate / Industrial (SG) → Asian Holding Company (Look-Through) | SOTP (analyst) | -8% | +425% | — | — | fired | — |
| C6L.SI | Aviation & Marine (SG) → Airlines | Forward EV/EBITDA | +156% | +57% | — | — | fired | — |
| BS6.SI | Aviation & Marine (SG) | EV/EBITDA | +157% | +157% | — | — | fired | — |
| C52.SI | Rail / Logistics | EV/EBITDA | +120% | +117% | — | — | fired | — |
| S58.SI | Aerospace & Engineering (SG) | DCF | -42% | -42% | — | — | fired | — |
