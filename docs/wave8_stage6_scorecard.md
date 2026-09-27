# Wave 8 (Real estate): Stage 6 scorecard

Measured 2026-09-27 on the Stage 4 engine over the 34-name universe (every probed name kept, owner
decision 1 as amended), read-only against the local store; recorded in `docs/baselines/after_wave8.json`
against `docs/baselines/baseline_wave8.json`. Dual score against spot and consensus (only the twelve US
names carry a usable consensus). "IV if accepted" is a preview in a copy of the local archive with the
Wave 8 pre-fills accepted (published NAVs, CapitaLand Investment's fund-manager input); every pre-fill is
pending on the owner's gate.

| Universe | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing | No value |
|---|---|---|---|---|---|
| Stage 0 (34 names) | 7 of 12 | 16 of 31 | 10 of 34 | 5 | 3 |
| Stage 4, inputs pending | 8 of 12 | 21 of 31 | 15 of 34 | 6 | 3 |
| Stage 4, inputs accepted (preview) | 9 of 12 | 22 of 32 | 15 of 34 | 2 | 2 |

## What moved and why

Within ±30% of spot 16 of 31 to 21 of 31; of consensus 7 of 12 to 8 of 12; the backtest passes on 15
(10). Every one of the 34 names now reaches an owner-named profile (twelve labels in `routing_scope`, four
new profiles, the owner's directory as 85 pins in the US and Hong Kong lookup and 14 Singapore rows), the REIT legs read the market,
and developers are no longer capitalised as rent. The names that do not price are the ones the engine
now refuses to price: the two distressed developers by rule, and Link REIT until its published NAV is
accepted.

**Sub-type by label and pin (decision 2).** American Tower −42% to +4%: the `tower` sub-type at 5.1%
in place of the office 7.5% the word "tower" earned it. Iron Mountain −61% to −45% on the REIT profile
(specialty) instead of Mature SaaS. Equinix −29% to −35%: the premium data-centre tier is gone (decision
4) and the live Specialty cohort implies 5.1% where the table gave 4.5%; the market holds Equinix at
about 3.3%, a premium the cohort median cannot carry (the Apple pattern).

**The market's cap rate (decision 3a).** Simon +35% to +25% (retail 6.9% live, 6.2% table), Realty
Income +20% to 0%, Welltower −80% to −78% and Ventas −45% to −37% (healthcare 5.1%). The residual on
the two health-care names is the P/FFO leg: the April table's 15x against a market paying 40 to 50x
forward FFO for Welltower's growth, and the comps store has no FFO field to read a live multiple from.
One consequence of live-first worth stating: for a pinned sub-type inside a broader label (net lease
inside `REIT - Retail`, self-storage inside `REIT - Industrial`, towers and data centres inside
`REIT - Specialty`) the live cohort is the label's, so the pin's own table cap applies only when the
cohort is missing. That is the decision as taken, recorded here.

**Landlords (decision 5).** Sun Hung Kai +39% to −7%, CK Asset +49% to +14%, Sino Land 0% to −27%,
Wharf −37% to −53%, Hongkong Land −10% to −25% on the HKSE cohort (the first pass had keyed its comps
override "HK", which the store does not know, and priced it at +54% with no multiples at all; corrected
to "HKSE"). Swire −79% to −39% and Henderson −36% to −41% price on P/B and the dividend alone: their
NAV leg is non-positive because FMP's EBITDA for a Hong Kong landlord carries the investment-property
revaluation loss through the income statement, so NOI ≈ EBITDA fails exactly where a landlord's NAV
matters most. The published-NAV input (decision 3b) is the remedy, and it is on the gate for both.

**China developers.** China Resources Land +93% to +6% and China Overseas Land +85% to +32% on book at
the cohort's 0.40x, next year's earnings and the dividend. Vanke and Longfor fire the distress rule (two
loss years; profit down 92% with net debt 0.84x equity): the P/B anchor stands down, no value is
published, `Distressed_Developer` is recorded. Both are exactly the outcome the gate was built for.

**US non-REITs.** D.R. Horton +5% to +16% on the Homebuilder profile (book at the large cohort's 1.8x,
forward earnings at 16x), CBRE −4% to −7% on Real Estate Services.

**Singapore.** The S-REIT NAV leg reads the SES cohort's implied cap rate: Mapletree Pan Asia −30% to
−8%, Keppel DC +33% to +26%, CICT −20% to −23%. Singapore Land +9% to −5% on Property Developer (SG)
now that its row is in scope. City Developments is unchanged (−64%, NAV uncomputable: a Stage 6 check
on its hotel EBITDA). CapitaLand Investment is unchanged at −72% until its fund-manager input is
accepted.

**Link REIT** still publishes no value: FMP records a negative net income (fair-value loss) and no
EBITDA, so FFO is negative and NOI is missing; only the DDM computes (5% of the weight) and the run is
Unrated by the surviving-weight floor. Its published NAV per unit is on the gate.

**With the pre-fills accepted (preview, archive copy).** Twenty of the 24 Wave 8 pre-fills pass their
checks and were accepted in a copy of the local archive; the four that fail stay on the gate as failures
(Simon's appraised NAV is a 2022 figure, China Overseas Land's a 2023 one, Longfor's book NAV is 5.2x its
market cap, and CapitaLand Investment's fund-manager input came back twice without a cited
distributable-earnings amount). Consensus 8 of 12 to 9 of 12, spot 21 of 31 to 22 of 32, anchor missing
6 to 2. The US inputs are appraised or consensus NAVs (Green Street, S&P, FactSet, Morningstar) and move
the names toward the market: Equinix −35% to −12%, Prologis −17% to −10%, Welltower −78% to −68%,
Digital Realty −7% to −2%, American Tower +4% to +25%, Realty Income 0% to +16%. The Hong Kong inputs are
what Gemini could cite, and for every landlord that is the company's book value per share (the IFRS
fair-valued portfolio), so accepting them prices the names at book: Henderson −41% to +38%, Wharf −53%
to +31%, CK Asset +14% to +40%, Swire −39% to +14%, Sun Hung Kai −7% to +15%, Link REIT from no value
to +49%, City Developments −64% to −12%, Singapore Land −5% to +36%. That is the structural discount to
book the Hong Kong market has paid for a decade, now visible on the record rather than hidden in an
uncomputable leg. Which of the twenty to accept is the owner's call name by name; a book NAV is a
different input from an appraised one and the basis field on each entry says which it is. Vanke's
accepted book NAV changes nothing: the distress gate holds and no value is published.

## Open for the owner (flags, not builds)

1. P/FFO and P/AFFO still read the April table (15x healthcare, 22x data centre) because the comps
   store carries no FFO field; Welltower (−78%) and Ventas (−37%) are the cost. Adding a per-member
   `p_ffo` (price / (net income + D&A)) to the weekly comps job would make the leg live; a build for the
   owner to call.
2. Live-first means the label's cohort cap beats a pinned sub-type's table cap (Realty Income, Public
   Storage, Equinix, Digital Realty, the towers). Recorded, not tuned.
3. FMP's EBITDA carries revaluation losses for Hong Kong landlords and Link REIT, so the cap-rate NAV
   fails on them; the `nav` pre-fills are the remedy and are pending on the gate.
4. Two SGX lookup rows were corrected against the directory (CMOU.SI is Keppel Pacific Oak US REIT, not
   CDL Hospitality; CWBU.SI is Cromwell European REIT, not NetLink). Two more are suspect and were left in
   place: the row naming SK6U.SI "Parkway Life" (the directory says C2PU.SI) and D8DU.SI "Digital Core"
   (the directory says DCRU.SI); both directory symbols were added as new rows.
5. The local comps store is dated 2026-09-22; no production run was made this wave.

## Per-ticker record

| Ticker | Profile now | Anchor (in blend) | IV Stage 0 | IV Stage 4 | IV if accepted | Spot | Consensus | vs spot S0 | vs spot S4 | vs spot accepted | vs cons S0 | vs cons S4 | Regime flag | Backtest |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WELL | REIT | NAV (Cap Rates) (yes) | 45.63 | 50.62 | 74.73 | 231.21 | 258.08 | -80% | -78% | -68% | -82% | -80% | — | fired / MARKET_CLOSER |
| PLD | REIT | NAV (Cap Rates) (yes) | 109.45 | 110.47 | 119.40 | 133.03 | 155.15 | -18% | -17% | -10% | -29% | -29% | — | passed / TIE |
| EQIX | REIT | NAV (Cap Rates) (yes) | 719.32 | 650.41 | 891.19 | 1,008.08 | 1,246.25 | -29% | -35% | -12% | -42% | -48% | — | fired / MARKET_CLOSER |
| AMT | REIT | NAV (Cap Rates) (yes) | 98.51 | 176.61 | 212.13 | 169.03 | 209.89 | -42% | +4% | +25% | -53% | -16% | — | passed / TIE |
| DLR | REIT | NAV (Cap Rates) (yes) | 167.86 | 165.61 | 175.36 | 178.61 | 222.35 | -6% | -7% | -2% | -25% | -26% | — | fired / MARKET_CLOSER |
| SPG | REIT | NAV (Cap Rates) (yes) | 276.41 | 255.70 |  | 204.82 | 223.60 | +35% | +25% |  | +24% | +14% | — | fired / MARKET_CLOSER |
| PSA | REIT | NAV (Cap Rates) (yes) | 257.32 | 259.61 | 267.16 | 287.97 | 328.33 | -11% | -10% | -7% | -22% | -21% | — | passed / TIE |
| O | REIT | NAV (Cap Rates) (yes) | 66.40 | 55.77 | 64.50 | 55.54 | 65.28 | +20% | +0% | +16% | +2% | -15% | — | fired / MARKET_CLOSER |
| VTR | REIT | NAV (Cap Rates) (yes) | 47.46 | 54.63 | 51.85 | 86.64 | 99.93 | -45% | -37% | -40% | -53% | -45% | — | fired / MARKET_CLOSER |
| CBRE | Real Estate Services (was Mature SaaS) | Forward P/E (yes) | 128.74 | 125.96 |  | 134.74 | 179.00 | -4% | -7% |  | -28% | -30% | — | fired / MARKET_CLOSER |
| DHI | Homebuilder / Land Developer (was Mature SaaS) | P/BV (yes) | 148.34 | 163.60 |  | 141.51 | 159.33 | +5% | +16% |  | -7% | +3% | — | passed / TIE |
| IRM | REIT (was Mature SaaS) | NAV (Cap Rates) (yes) | 43.25 | 61.46 | 56.93 | 111.38 | 142.75 | -61% | -45% | -49% | -70% | -57% | — | passed / TIE |
| 00016.HK | Landlord / Investment Property (HK) (was REIT) | NAV (Cap Rates) (yes) | 148.70 | 100.01 | 122.86 | 107.10 | — | +39% | -7% | +15% | — | — | — | passed / TIE |
| 01109.HK | Property Developer (HK / China) (was Mature SaaS) | P/BV (yes) | 55.34 | 30.35 | 33.22 | 28.64 | — | +93% | +6% | +16% | — | — | — | passed / MODEL_CLOSER |
| 01113.HK | Landlord / Investment Property (HK) (was REIT) | NAV (Cap Rates) (yes) | 68.54 | 52.54 | 64.47 | 46.00 | — | +49% | +14% | +40% | — | — | — | passed / MODEL_CLOSER |
| 01972.HK | Landlord / Investment Property (HK) (was Mature Platform) | NAV (Cap Rates) (NO) | 5.07 | 14.95 | 27.80 | 24.32 | — | -79% | -39% | +14% | — | — | — | fired / MARKET_CLOSER |
| 00688.HK | Property Developer (HK / China) (was REIT) | P/BV (yes) | 23.02 | 16.45 |  | 12.42 | — | +85% | +32% |  | — | — | — | fired / MARKET_CLOSER |
| 00012.HK | Landlord / Investment Property (HK) (was REIT) | NAV (Cap Rates) (NO) | 16.65 | 15.49 | 36.19 | 26.22 | — | -36% | -41% | +38% | — | — | — | fired / MARKET_CLOSER |
| 00823.HK | REIT (was Mature Platform) | NAV (Cap Rates) (NO) | — | — | 55.32 | — | — | — | — | +49% | — | — | — | passed / TIE |
| 00083.HK | Landlord / Investment Property (HK) (was Mature Platform) | NAV (Cap Rates) (yes) | 9.90 | 7.21 | 11.10 | 9.94 | — | -0% | -27% | +12% | — | — | — | fired / MARKET_CLOSER |
| 00004.HK | Landlord / Investment Property (HK) (was Mature Platform) | NAV (Cap Rates) (yes) | 12.29 | 9.12 | 25.47 | 19.50 | — | -37% | -53% | +31% | — | — | — | fired / MARKET_CLOSER |
| 02202.HK | Property Developer (HK / China) (was Mature SaaS) | P/BV (NO) | — | — | — | — | — | — | — | — | — | — | Distressed_Developer | fired / MARKET_CLOSER |
| 00960.HK | Property Developer (HK / China) (was Mature Platform) | P/BV (NO) | — | — |  | — | — | — | — |  | — | — | Distressed_Developer | fired / MARKET_CLOSER |
| H78.SI | Landlord / Investment Property (HK) (was Property Developer (SG)) | NAV (Cap Rates) (yes) | 7.73 | 6.41 |  | 8.57 | — | -10% | -25% |  | — | — | — | passed / TIE |
| C38U.SI | S-REIT | DDM (S-REIT) (yes) | 1.80 | 1.73 |  | 2.24 | — | -20% | -23% |  | — | — | — | passed / MODEL_CLOSER |
| 9CI.SI | Real Estate Asset Manager (SG) | P/E (norm) (yes) | 0.74 | 0.74 |  | 2.60 | — | -72% | -72% |  | — | — | — | fired / MARKET_CLOSER |
| A17U.SI | S-REIT | DDM (S-REIT) (yes) | 2.67 | 2.70 |  | 2.28 | — | +17% | +18% |  | — | — | — | passed / TIE |
| C09.SI | Property Developer (SG) | NAV (NO) | 2.98 | 2.98 | 7.25 | 8.26 | — | -64% | -64% | -12% | — | — | — | fired / MARKET_CLOSER |
| U14.SI | Property Developer (SG) | NAV (yes) | 9.91 | 9.75 |  | 8.46 | — | +17% | +15% |  | — | — | — | passed / MODEL_CLOSER |
| N2IU.SI | S-REIT | DDM (S-REIT) (yes) | 0.86 | 1.12 |  | 1.22 | — | -30% | -8% |  | — | — | — | fired / MARKET_CLOSER |
| M44U.SI | S-REIT | DDM (S-REIT) (yes) | 1.25 | 1.27 |  | 1.10 | — | +14% | +15% |  | — | — | — | passed / MODEL_CLOSER |
| ME8U.SI | S-REIT | DDM (S-REIT) (yes) | 1.98 | 2.00 |  | 1.86 | — | +6% | +8% |  | — | — | — | fired / MARKET_CLOSER |
| AJBU.SI | S-REIT | DDM (S-REIT) (yes) | 2.82 | 2.67 |  | 2.12 | — | +33% | +26% |  | — | — | — | fired / MARKET_CLOSER |
| U06.SI | Property Developer (SG) (was Mature Platform) | NAV (yes) | 3.41 | 2.98 | 4.26 | 3.13 | — | +9% | -5% | +36% | — | — | — | passed / MODEL_CLOSER |
