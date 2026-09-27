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
| Step 3: FFO field + REIT (Specialty / OpCo) (A) | 8 of 12 | 19 of 32 | 16 of 34 | 3 | 2 |
| Step 3: FFO field + REIT (Specialty / OpCo) (A), inputs accepted (preview) | 10 of 12 | 20 of 32 | 16 of 34 | 2 | 2 |

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

## Step 3 (Bucket A): the FFO field, the live P/FFO leg, REIT (Specialty / OpCo)

Built: `p_ffo` joins the comps fields (price / (net income + D&A) on the latest annual cash-flow statement,
the Nareit definition; band 3 to 120x), read for REIT-labelled members only, one cash-flow call each, in
the weekly refresh and in `scripts/backfill_comps_field.py` (which filled the local store on 2026-09-27:
146 US REIT members, 137 fetched, 128 in band, 18 label medians). The P/FFO leg reads the live cohort
first (a curated basket, else the label's industry cohort) and the April table only as the fallback, with
the growth premium (forward EPS growth against the cohort) as the escalator on either basis; the source is
on the leg trace. `REIT (Specialty / OpCo)` (P/FFO .40 anchor, Forward EV/EBITDA .30, NAV (Cap Rates) .30
on clean NOI) takes Welltower, Ventas, Iron Mountain, Equinix, Digital Realty, American Tower, Crown Castle
and SBA, and its curated eight-name basket is the cohort the anchor reads: **27.5x trailing P/FFO on the
day**, measured, not set. The passive REIT profile is NAV .60 / P/FFO .40 (it was .50 / .30 / .15 / .05).
FMP publishes no FFO line and no forward FFO, so the anchor is trailing FFO with forward EPS growth as
the escalator, as decided.

The market's trailing P/FFO by cohort (US, 2026-09-27; the April table paid 11 to 23x):

| Label | all | large | April table |
|---|---|---|---|
| REIT - Healthcare Facilities | 14.7x (14) | 20.9x (8) | 15x |
| REIT - Specialty | 16.1x (14) | 20.1x (8) | 22x data centre, 15x default |
| REIT - Industrial | 16.6x (15) | 16.7x (7) | 18x |
| REIT - Retail | 12.8x (20) | 12.5x (10) | 14x retail, 16x net lease |
| REIT - Residential | 11.7x (13) | 12.1x (7) | 17x |
| REIT - Diversified | 10.8x (15) | 14.4x (7) | 15x |
| REIT - Office | 8.6x (11) | 10.0x (6) | 12x |
| REIT - Hotel & Motel | 9.8x (11) | 10.2x (5) | 11x |
| OpCo basket (eight names) | 27.5x (8) | | |

Two things the table says before the measurement does. The cohort medians sit at or below the April table
for most labels, so the live basis will not by itself lift Welltower and Ventas toward a market that pays
them 49x and 24x: the basket's 27.5x is the operating REITs' own median and the escalator is what is left.
And the residential and office cohorts trade well below the table, so the passive REITs on those labels
will move down on the live basis. Both are what "measured, not set" means.

Verification on the eight OpCo names and one passive REIT (`docs/baselines/w8b_step3_verify.log`):

| Name | Step 2 (pending) | Step 3 | P/FFO leg | Forward EV/EBITDA leg | Clean-NOI NAV leg |
|---|---|---|---|---|---|
| Welltower | −67% | −52% (111 against 231) | 118 at 27.5x | 111 | 103 |
| Ventas | −37% | −9% | 92 | 67 | 74 |
| Iron Mountain | −54% | −3% | 108 | 150 | 66 |
| Equinix | −29% | −11% | 954 | 899 | 832 |
| Digital Realty | −7% | +8% | 239 | 170 | 157 |
| American Tower | +26% | +56% | 271 | 238 | 277 (clean NOI overstated, step 1) |
| Crown Castle | — | +17% | 71 | 79 | 91 |
| SBA | — | +29% | 354 | 139 | 103 |
| Prologis (passive, 60 / 40) | −32% | −37% | 108 at the live 16.6x industrial | | 67 (clean NOI understated, step 1) |

Six of the eight operating names land inside the band on the basket's own multiple; Welltower closes 15
points and stays outside because the market pays it 49x FFO against the basket's 27.5x; American Tower and
SBA overshoot, the towers' clean NOI (step 1) and a P/FFO that sits on their FFO after heavy amortisation.

**The escalator does not fire on a REIT, and this is structural.** The growth premium's quality gate reads
`forward_roic <= wacc -> premium 1.0`, and a REIT's forward ROIC is EBIT over depreciated invested capital
(Welltower 1.0%, Equinix 1.0% against a 5.5 to 7.5% WACC), so every REIT prices at premium 1.0 and the
forward-EPS-growth escalator the decision named never reaches the FFO multiple. A REIT's return should be
read on FFO over invested capital (or the gate skipped for the OpCo profile); that is a method decision for
the owner, recorded here and not built. Until then the OpCo anchor is the basket median with no growth
term, which is why Welltower stops at −52%.

Passive REITs on the live P/FFO move with their cohorts (residential 11.7x and office 8.6x sit under the
April table; industrial 16.6x and retail 12.8x near it), and Prologis carries step 1's understated NOI
through the .60 NAV leg: −37%.

**Preview with the 21 pre-fills accepted** (`docs/baselines/after_wave8b_step3_preview.json`): the OpCo
names move as in the verification with their appraised NAVs beside the live multiple (Iron Mountain −49% ->
−4%, Ventas −40% -> −14%, Equinix −12% -> −4%, Digital Realty −2% -> +10%, Welltower −68% -> −54%, American
Tower +25% -> +48%); the passive names shift with their cohorts (Public Storage −7% -> −1%, Realty Income
+16% -> +10%). Eleven of twenty within the band (ten at step 2). Link REIT's par NAV now sits at +56% as
its P/FFO leg computes on the live cohort.

## Per-ticker record (IV against spot at each stage)

| Ticker | Profile now | vs spot Stage 0 | vs spot Wave 8 | vs spot Step 1 | vs spot Step 2 | vs spot Step 3 | vs cons (latest) | Flag (latest) |
|---|---|---|---|---|---|---|---|---|
| WELL | REIT (Specialty / OpCo) | -80% | -78% | -67% | -67% | -52% | -57% | — |
| PLD | REIT | -18% | -17% | -32% | -32% | -37% | -46% | — |
| EQIX | REIT (Specialty / OpCo) | -29% | -35% | -29% | -29% | -11% | -28% | — |
| AMT | REIT (Specialty / OpCo) | -42% | +4% | +26% | +26% | +56% | +25% | — |
| DLR | REIT (Specialty / OpCo) | -6% | -7% | -10% | -10% | +8% | -13% | — |
| SPG | REIT | +35% | +25% | -1% | -1% | -8% | -15% | — |
| PSA | REIT | -11% | -10% | -45% | -45% | -46% | -53% | — |
| O | REIT | +20% | +0% | +23% | +23% | +51% | +28% | — |
| VTR | REIT (Specialty / OpCo) | -45% | -37% | -37% | -37% | -9% | -21% | — |
| CBRE | Real Estate Services | -4% | -7% | -7% | -7% | -8% | -30% | — |
| DHI | Homebuilder / Land Developer | +5% | +16% | +16% | +16% | -3% | -14% | — |
| IRM | REIT (Specialty / OpCo) | -61% | -45% | -54% | -54% | -3% | -24% | — |
| 00016.HK | Landlord / Investment Property (HK) | +39% | -7% | -22% | -22% | -22% | — | — |
| 01109.HK | Property Developer (HK / China) | +93% | +6% | +6% | +6% | +6% | — | — |
| 01113.HK | Landlord / Investment Property (HK) | +49% | +14% | +161% | +161% | +161% | — | — |
| 01972.HK | Landlord / Investment Property (HK) | -79% | -39% | -33% | -33% | -33% | — | — |
| 00688.HK | Property Developer (HK / China) | +85% | +32% | +32% | +32% | +32% | — | — |
| 00012.HK | Landlord / Investment Property (HK) | -36% | -41% | -41% | -41% | -41% | — | — |
| 00823.HK | REIT | — | — | -3% | -3% | -2% | — | — |
| 00083.HK | Landlord / Investment Property (HK) | -0% | -27% | -37% | -37% | -37% | — | — |
| 00004.HK | Landlord / Investment Property (HK) | -37% | -53% | -5% | -5% | -5% | — | — |
| 02202.HK | Property Developer (HK / China) | — | — | — | — | — | — | Distressed_Developer |
| 00960.HK | Property Developer (HK / China) | — | — | — | — | — | — | Distressed_Developer |
| H78.SI | Landlord / Investment Property (HK) | -10% | -25% | -62% | -62% | -62% | — | — |
| C38U.SI | S-REIT | -20% | -23% | -22% | -22% | -22% | — | — |
| 9CI.SI | Real Estate Asset Manager (SG) | -72% | -72% | -68% | -68% | -68% | — | — |
| A17U.SI | S-REIT | +17% | +18% | +25% | +25% | +25% | — | — |
| C09.SI | Property Developer (SG) | -64% | -64% | -14% | -14% | -14% | — | — |
| U14.SI | Property Developer (SG) | +17% | +15% | +38% | +38% | +38% | — | — |
| N2IU.SI | S-REIT | -30% | -8% | +7% | +7% | +7% | — | — |
| M44U.SI | S-REIT | +14% | +15% | +13% | +13% | +13% | — | — |
| ME8U.SI | S-REIT | +6% | +8% | +18% | +18% | +18% | — | — |
| AJBU.SI | S-REIT | +33% | +26% | +5% | +5% | +5% | — | — |
| U06.SI | Property Developer (SG) | +9% | -5% | -21% | -21% | -21% | — | — |
