# Wave 8b (Real estate remediation B -> C -> A -> D): Stage 0 to Stage 4 tracking report

Owner's remediation architecture of 2026-09-27, executed in the owner's order: clean cash NOI (B), the
cohort P/NAV read on a published NAV (C), the FFO field and the REIT (Specialty / OpCo) profile (A), the
developer sub-cohorts with the contagion rule (D). Each step is measured over the same 34-name universe
(`docs/baselines/after_wave8b_step*.json`) against the Stage 0 baseline and the Wave 8 Stage 4 engine, so
the improvement is attributable step by step. Read-only against the local store; previews with the
review-gated inputs accepted are run in an archive copy.

| Engine | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing | No value |
|---|---|---|---|---|---|
| Stage 0 (before Wave 8) | 7 of 12 | 16 of 31 | 10 of 34 | 5 | 3 |
| Wave 8 Stage 4 (35af44b2 + 3da3c337) | 8 of 12 | 21 of 31 | 15 of 34 | 6 | 3 |
| Step 1: clean NOI (B) | 6 of 12 | 19 of 32 | 17 of 34 | 3 | 2 |
| Step 2: cohort P/NAV (C) | 6 of 12 | 19 of 32 | 17 of 34 | 3 | 2 |
| Step 2: cohort P/NAV (C), inputs accepted (preview) | 9 of 12 | 19 of 32 | 17 of 34 | 2 | 2 |

## Step 1 (Bucket B): clean cash NOI

Built: `_compute_reit_metrics` derives NOI as revenue less cost of revenue for every profile that prices a
cap-rate NAV (REIT, S-REIT, Landlord / Investment Property (HK), Property Developer (SG)); statutory EBITDA
is the fallback only where cost of revenue is zero or missing, as decided, and the basis is printed on the
REIT flag and the NAV leg trace (`noi_basis`, `gross_asset_value`, `total_debt`, `cash`). Golden: C38U.SI
1.74 -> 1.76.

**Measured over the 34 names: the step fixes what it was aimed at and regresses the names it was not.**
Within ±30% of spot 21 of 31 -> 19 of 32; of consensus 8 of 12 -> 6 of 12; backtest 15 -> 17; anchors missing
6 -> 3; no value 3 -> 2.

| Direction | Names (vs spot, Wave 8 -> step 1) | Why |
|---|---|---|
| Fixed (the IFRS landlords the step was for) | Link REIT no value -> −3%; City Developments −64% -> −14%; Wharf −53% -> −5%; Swire −39% -> −33%; Simon +25% -> −1% | the revaluation loss no longer sits inside NOI; gross profit capitalised at the cohort cap rate returns a NAV |
| Regressed: FMP's cost of revenue is not property opex | Public Storage −10% -> −45%; Prologis −17% -> −32%; Iron Mountain −45% -> −54%; Realty Income 0% -> +23%; American Tower +4% -> +26% | for several US REITs FMP's `costOfRevenue` carries depreciation (and for others almost nothing), so revenue less cost of revenue is not NOI on those statements: it understates PSA and PLD and overstates O and AMT against the EBITDA the step replaced |
| Regressed: revenue includes development sales | CK Asset +14% -> +161%; Hongkong Land −25% -> −62%; UOL +15% -> +38%; Sun Hung Kai −7% -> −22% | a hybrid's gross profit is development margin plus rent; capitalising it at a landlord cap rate is the developer-as-rent error again, now through the clean line instead of EBITDA. The published NAV at the cohort's P/NAV (step 2) is the anchor these names need |
| Unmoved by design | Henderson −41% (NAV still non-positive) | clean NOI HK$8.1bn at 6.6% is HK$123bn of gross assets against HK$158bn of debt less HK$21bn cash: the land bank and associates sit outside a rental NAV; step 2's published NAV is its leg |

The step is committed as decided (universal, EBITDA only as the fallback) and the regression is recorded
rather than tuned away. Two derivable variants are measured beside it for the owner to choose from: (V1)
the larger of clean NOI and statutory EBITDA, since each basis fails in one direction; (V2) clean NOI only
where EBITDA is not positive or is under half of clean NOI (the revaluation signature), EBITDA otherwise.
Neither introduces a constant. Both leave the hybrids (CK Asset, Hongkong Land, UOL) to the published NAV.

**V1 measured on the ten names step 1 moved most** (`.wave8b_v1.log`, no engine change, a probe):

| Name | Wave 8 | Step 1 as decided | V1: larger of clean NOI and EBITDA | Basis V1 chose |
|---|---|---|---|---|
| Public Storage | −10% | −45% | −10% | EBITDA (FMP's cost of revenue carries depreciation) |
| Prologis | −17% | −32% | −17% | EBITDA |
| Iron Mountain | −45% | −54% | −45% | EBITDA |
| Hongkong Land | −25% | −62% | −25% | EBITDA |
| Sun Hung Kai | −7% | −22% | −7% | EBITDA |
| Link REIT | no value | −3% | −3% | clean NOI (EBITDA negative on the revaluation) |
| American Tower | +4% | +26% | +26% | clean NOI, overstated: FMP's cost of revenue is a fraction of tower opex |
| Realty Income | 0% | +23% | +23% | clean NOI, overstated for the same reason |
| CK Asset | +14% | +161% | +161% | clean NOI: development gross profit capitalised as rent (hybrid) |
| UOL | +15% | +38% | +38% | the SG developer NAV path; hybrid |

V1 reverses five of the eight regressions and keeps the Link fix; it does not touch American Tower and
Realty Income, where clean NOI is the overstated basis, nor the hybrids. V2 (clean NOI only where EBITDA
is not positive or is under half of clean NOI) would take EBITDA on American Tower (6.1bn against 7.8bn
clean, a 0.78 ratio) and Realty Income and keep clean NOI on Swire (1.6bn against 10.1bn), Link and Wharf,
so it is the variant the evidence points to; it is reasoned here, not yet measured. The hybrids are the
published NAV's to price (step 2) on any variant. **Owner decision B is open: as decided, V1 or V2.** Steps
2 to 4 proceed on the engine as decided; a change of variant is one line and a re-measurement.

## Step 2 (Bucket C): a published NAV read at the cohort's trailing four-quarter P/NAV

Built: `_cohort_p_nav_4q` takes the median of the cohort's stored P/B over every `as_of` within 365 days
(weekly refreshes and the quarterly backfill alike; today one point per cohort, 2026-09-21, so the median is
that point until the store accumulates quarters), for an industry or curated-basket cohort on a real-estate
label on HKSE or SES, where book is NAV. `_calibrated_published_nav` prices the NAV (Cap Rates) and RNAV
(published) legs at published NAV x that median and writes `raw_published_nav`, `cohort_p_nav_median_4q`,
`calibrated_nav`, the cohort, the point count and the window to the leg trace and `_nav_calibration`. On US
GAAP (historic-cost book, P/B is not P/NAV) an appraised NAV stays at par, and the run flag says which.
Golden: none moved (no fixture carries an accepted NAV).

Verification on the four names, published (book) NAV accepted in an archive copy
(`docs/baselines/after_wave8b_step2_verify.json`):

| Name | Pending (step 1) | Accepted at par (Wave 8) | Accepted at the cohort P/NAV (step 2) | Cohort read |
|---|---|---|---|---|
| CK Asset | +161% | +40% | −19% | HKSE Real Estate - Diversified 0.30x |
| Henderson Land | −41% | +38% | −34% | same |
| Wharf | −5% | +31% | −28% | same |
| Link REIT | −3% | +49% | +49% | none: `REIT - Retail` on HKSE has three members, so Link's P/B resolves on the sector rung, which the rule does not read; the NAV stays at par |

Three of four land inside or at the edge of the band and the contradiction of Wave 8 (a par NAV beside a
0.30x P/B) is gone. Henderson at −34% sits just outside: 0.30x is the whole diversified cohort's discount and
the market pays Henderson about 0.35x. Link is the thin-cohort case: **owner question for step 2**, whether
a thin label cohort (under five members) may fall to the exchange's real-estate sector rung P/B (HKSE 0.51x
today, flagged as a sector read) rather than to par. Not built; par is the conservative reading.

**Preview over the 21 accepted pre-fills at the cohort P/NAV** (`docs/baselines/after_wave8b_step2_preview.json`):
the rule removes every par-NAV overshoot and lands most of them just below the band. CK Asset +40% -> −19%,
Wharf +31% -> −28%, China Resources Land +16% -> −5%; Sun Hung Kai +15% -> −34%, Sino Land +12% -> −33%,
Swire +14% -> −32%, Henderson +38% -> −34%, Singapore Land +36% -> −34%, City Developments −12% -> −41%. Ten
of twenty within the band (Wave 8's par preview: eleven), with the misses now on the other side and closer.

Two readings for the owner, both derivations rather than defects:
1. **The cohort discount is deeper than the majors' own.** HKSE `Real Estate - Diversified` trades at
   0.30x book on both the whole cohort (10 names) and the large half (6 names, 0.295x), while the market pays
   Sun Hung Kai, Swire and Sino about 0.35 to 0.45x. Reading the discount on the name's own size rung (built
   here: the read follows the cohort the store resolved `pb` on) changes nothing today because the two rungs
   coincide. A curated basket of the majors (the owner's category 4 list is already `Landlord / Investment
   Property (HK)`'s basket in `SECTOR_PEER_BASKETS`) would give the P/NAV read their own median; that is the
   same wiring as the Wave 7 hyperscaler baskets, one line in `regional_comps.PROFILE_PEER_BASKETS`, and a
   decision, because it moves the majors' anchors by 5 to 10 points.
2. **An accepted published NAV replaces a working computed NAV.** City Developments' clean-NOI NAV of
   S$10.42 (−14% blended) is replaced by its book NAV S$8.41 x the SES developer cohort's 0.59x = S$4.96
   (−41%). The rule as decided says the published figure prices ahead; where the computed NAV already
   exists and is inside the band, the owner may prefer the published NAV as the cross-check instead. Not
   changed here.
Link REIT stays at +49% at par (the thin-cohort question above).

## Per-ticker record (IV against spot at each stage)

| Ticker | Profile now | vs spot Stage 0 | vs spot Wave 8 | vs spot Step 1 | vs spot Step 2 | vs cons (latest) | Flag (latest) |
|---|---|---|---|---|---|---|---|
| WELL | REIT | -80% | -78% | -67% | -67% | -70% | — |
| PLD | REIT | -18% | -17% | -32% | -32% | -41% | — |
| EQIX | REIT | -29% | -35% | -29% | -29% | -42% | — |
| AMT | REIT | -42% | +4% | +26% | +26% | +1% | — |
| DLR | REIT | -6% | -7% | -10% | -10% | -28% | — |
| SPG | REIT | +35% | +25% | -1% | -1% | -10% | — |
| PSA | REIT | -11% | -10% | -45% | -45% | -52% | — |
| O | REIT | +20% | +0% | +23% | +23% | +5% | — |
| VTR | REIT | -45% | -37% | -37% | -37% | -45% | — |
| CBRE | Real Estate Services | -4% | -7% | -7% | -7% | -30% | — |
| DHI | Homebuilder / Land Developer | +5% | +16% | +16% | +16% | +3% | — |
| IRM | REIT | -61% | -45% | -54% | -54% | -64% | — |
| 00016.HK | Landlord / Investment Property (HK) | +39% | -7% | -22% | -22% | — | — |
| 01109.HK | Property Developer (HK / China) | +93% | +6% | +6% | +6% | — | — |
| 01113.HK | Landlord / Investment Property (HK) | +49% | +14% | +161% | +161% | — | — |
| 01972.HK | Landlord / Investment Property (HK) | -79% | -39% | -33% | -33% | — | — |
| 00688.HK | Property Developer (HK / China) | +85% | +32% | +32% | +32% | — | — |
| 00012.HK | Landlord / Investment Property (HK) | -36% | -41% | -41% | -41% | — | — |
| 00823.HK | REIT | — | — | -3% | -3% | — | — |
| 00083.HK | Landlord / Investment Property (HK) | -0% | -27% | -37% | -37% | — | — |
| 00004.HK | Landlord / Investment Property (HK) | -37% | -53% | -5% | -5% | — | — |
| 02202.HK | Property Developer (HK / China) | — | — | — | — | — | Distressed_Developer |
| 00960.HK | Property Developer (HK / China) | — | — | — | — | — | Distressed_Developer |
| H78.SI | Landlord / Investment Property (HK) | -10% | -25% | -62% | -62% | — | — |
| C38U.SI | S-REIT | -20% | -23% | -22% | -22% | — | — |
| 9CI.SI | Real Estate Asset Manager (SG) | -72% | -72% | -68% | -68% | — | — |
| A17U.SI | S-REIT | +17% | +18% | +25% | +25% | — | — |
| C09.SI | Property Developer (SG) | -64% | -64% | -14% | -14% | — | — |
| U14.SI | Property Developer (SG) | +17% | +15% | +38% | +38% | — | — |
| N2IU.SI | S-REIT | -30% | -8% | +7% | +7% | — | — |
| M44U.SI | S-REIT | +14% | +15% | +13% | +13% | — | — |
| ME8U.SI | S-REIT | +6% | +8% | +18% | +18% | — | — |
| AJBU.SI | S-REIT | +33% | +26% | +5% | +5% | — | — |
| U06.SI | Property Developer (SG) | +9% | -5% | -21% | -21% | — | — |
