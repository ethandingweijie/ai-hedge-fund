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
| Stage 4, inputs accepted (preview) | 9 of 12 | 23 of 32 | 15 of 34 | 2 | 2 |

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
on its hotel EBITDA). CapitaLand Investment is unchanged at −72% until its sum-of-the-parts input is
accepted (below).

**Link REIT** still publishes no value: FMP records a negative net income (fair-value loss) and no
EBITDA, so FFO is negative and NOI is missing; only the DDM computes (5% of the weight) and the run is
Unrated by the surviving-weight floor. Its published NAV per unit is on the gate.

**With the pre-fills accepted (preview, archive copy).** Twenty of the 24 Wave 8 pre-fills pass their
checks and were accepted in a copy of the local archive, with CapitaLand Investment's `sotp` input added
below; the four that fail stay on the gate as failures
(Simon's appraised NAV is a 2022 figure, China Overseas Land's a 2023 one, Longfor's book NAV is 5.2x its
market cap, and CapitaLand Investment's `alt_manager` input came back twice without a cited
distributable-earnings amount, which is why it moved to the `sotp` kind, below). Consensus 8 of 12 to 9 of 12, spot 21 of 31 to 23 of 32, anchor missing
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

**CapitaLand Investment (decision 7, Option A).** The company reports fee income-related business
earnings and an investment book, not US-style distributable earnings, so the `alt_manager` kind could not
describe it and its two pre-fills failed for want of a cited DE amount. The profile's SOTP leg now reads
the `sotp` kind through SOTP (analyst) (the published-table leg it had read was a hand-typed broker table
this ticker never had), with the weight rolling into P/E (norm) until an input is accepted. The second
`sotp` pre-fill prices: fee business S$1.23bn revenue at a 37% margin and 15 to 20x earnings, the
investment business S$1.02bn at 1 to 2x sales, S$13.5bn of associates and S$7.7bn of listed REIT stakes
at market, S$5.8bn net debt, a 15% holdco discount. Its one failing check is soft (segment revenues are
FY2025, not the forward year), which the owner may waive. Accepted in the archive copy it publishes
S$1.90 against a spot of S$2.60, −27% (−72% before), with the SOTP leg in the blend at its full weight.

## Structural weaknesses behind the misses (owner request, 2026-09-27)

Thirteen of 34 names sit outside ±30% of spot (or consensus) with the inputs pending, ten with them
accepted. Each miss is attributed below to the leg that produces it, from the per-leg traces
(`docs/baselines/w8_legs_after.log`, `.wave8_legs_outliers.log`). Five causes, two of them structural to
the engine, one to the data, one to the market, one by design.

| Cause | Names (vs spot, pending -> accepted) | What the trace shows | Fix path |
|---|---|---|---|
| **A. Growth REITs on a landlord's anchor.** NAV (Cap Rates) .50 capitalises FMP's EBITDA at a cohort cap rate and P/FFO reads the April table (15x healthcare, 15x specialty). | WELL −78% -> −68%; VTR −37% -> −40%; IRM −45% -> −49%; EQIX −35% -> −12% | Welltower: FFO $3.08bn against a $150bn market cap is 49x FFO; the table pays 15x, the NAV leg 45bn of gross assets against a $170bn enterprise value. Even the appraised NAV ($87, Green Street) is 0.38x the share price: the market prices senior-housing operating growth, not the building. Ventas 24x FFO in the market against 15x; Iron Mountain 28x against 15x, with a records-storage operating business the cap-rate NAV cannot see. Equinix closes to −12% only through its appraised NAV. | Structural. (1) An FFO field in the weekly comps job so P/FFO reads the live cohort (build); (2) a growth-REIT sub-class (data centre, senior-housing operators, towers, records) anchored on forward P/FFO rather than NAV, an owner method decision; (3) until then these four are flagged as profile mismatches, not tuned. |
| **B. Revaluation inside EBITDA.** NOI ≈ EBITDA, and FMP's EBITDA for an IFRS landlord carries the investment-property fair-value change. | Swire −39% -> +14%; Henderson −41% -> +38%; Link no value -> +49%; City Developments −64% -> −12% | Swire's and Henderson's NAV legs come out non-positive (a revaluation loss swallows the rent), Link's FFO is −HK$7.3bn on a fair-value loss and its EBITDA is missing, CDL's NAV is non-positive on its hotel EBITDA; each blend falls to P/B and the dividend or to a single leg. With a published NAV accepted the leg returns, and Swire and CDL land. | Data, then structural. Derive NOI from rental revenue and a property margin (or the company's own net property income) instead of EBITDA for the landlord and REIT profiles (build); the published NAV is the remedy today and is on the gate. |
| **C. The Hong Kong discount to book.** The market pays 0.30–0.42x book for HK landlords; a published NAV is book. | CK Asset +14% -> +40%; Henderson -> +38%; Wharf −53% -> +31%; Link -> +49%; Sun Hung Kai −7% -> +15%; Singapore Land −5% -> +36% | Pending, the P/B leg at the cohort's 0.30–0.42x carries the discount and the names land or sit below spot; accepted, the NAV leg reads book NAV at 1.0x and the blend rises to book. The discount is the market's regime, not a data error (the holdco-discount finding of 2026-09-24 again). | Market regime, owner decision. Read the published NAV at the cohort's P/NAV (the HKSE `Real Estate - Diversified` P/B of 0.30x today) rather than at par, so the leg carries the discount the market pays: derivable from the store and shown, no constant. Or accept the US-style inputs (appraised) and revoke the HK book NAVs one by one. |
| **D. One cohort multiple for a spectrum of developers.** P/BV at the cohort median 0.42x on every developer. | China Overseas Land +32% (0.28x book in the market); China Resources Land +6% | COLI's P/B leg at the cohort's 0.42x is 18.7 against a spot of 12.4; the cohort median sits above the names the market rates weaker. | Flagged. A credit-quality tiering of the P/B multiple would be an owner constant; the distress gate already removes the two names the market prices as credit. |
| **E. Inputs that were missing.** | CapitaLand Investment −72% -> −27%; City Developments (above) | The SOTP leg had no input under either the published-table or the `alt_manager` kind; the `sotp` input prices it. | Built this wave (Option A). |
| **F. By design.** | Vanke, Longfor: no value | GATE_DISTRESSED_DEVELOPER stands the book anchor down. | None. |

**Improvement, measured.** Against spot 16 of 31 -> 21 of 31 -> 23 of 32 with inputs; against consensus 7 of
12 -> 8 of 12 -> 9 of 12; the backtest 10 -> 15; anchors missing 5 -> 6 -> 2; no value 3 -> 3 -> 2 (the two
by design). Of the ten still outside with inputs accepted: three are cause A (the growth-REIT anchor),
five are cause C (book against the Hong Kong discount, which the pending run carried correctly through the
P/B leg), one is cause D, two are by design. Cause B is closed by the inputs; cause E is closed by the build.
The two changes that would move the count most are the FFO field for A and reading a published NAV at the
cohort's P/NAV for C; both are proposed above, neither is built.

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
6. CapitaLand Investment's `sotp` pre-fill carries FY2025 segment revenues under an FY2026E label; the
   period check fails softly and acceptance is the owner's waiver. The failed `alt_manager` entry is
   omitted with the reason.

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
| 9CI.SI | Real Estate Asset Manager (SG) | P/E (norm) (yes) | 0.74 | 0.74 | 1.90 | 2.60 | — | -72% | -72% | -27% | — | — | — | fired / MARKET_CLOSER |
| A17U.SI | S-REIT | DDM (S-REIT) (yes) | 2.67 | 2.70 |  | 2.28 | — | +17% | +18% |  | — | — | — | passed / TIE |
| C09.SI | Property Developer (SG) | NAV (NO) | 2.98 | 2.98 | 7.25 | 8.26 | — | -64% | -64% | -12% | — | — | — | fired / MARKET_CLOSER |
| U14.SI | Property Developer (SG) | NAV (yes) | 9.91 | 9.75 |  | 8.46 | — | +17% | +15% |  | — | — | — | passed / MODEL_CLOSER |
| N2IU.SI | S-REIT | DDM (S-REIT) (yes) | 0.86 | 1.12 |  | 1.22 | — | -30% | -8% |  | — | — | — | fired / MARKET_CLOSER |
| M44U.SI | S-REIT | DDM (S-REIT) (yes) | 1.25 | 1.27 |  | 1.10 | — | +14% | +15% |  | — | — | — | passed / MODEL_CLOSER |
| ME8U.SI | S-REIT | DDM (S-REIT) (yes) | 1.98 | 2.00 |  | 1.86 | — | +6% | +8% |  | — | — | — | fired / MARKET_CLOSER |
| AJBU.SI | S-REIT | DDM (S-REIT) (yes) | 2.82 | 2.67 |  | 2.12 | — | +33% | +26% |  | — | — | — | fired / MARKET_CLOSER |
| U06.SI | Property Developer (SG) (was Mature Platform) | NAV (yes) | 3.41 | 2.98 | 4.26 | 3.13 | — | +9% | -5% | +36% | — | — | — | passed / MODEL_CLOSER |
