# Wave 7 (Technology and communications): Stage 6 scorecard

Measured 2026-09-27 on the Stage 4 engine over the 46-name universe (49 probed; 00020.HK, CJLU.SI and
AIY.SI cut by the owner, decision 1), read-only against the local store; recorded in
`docs/baselines/after_wave7.json` against `docs/baselines/baseline_wave7.json`. Dual score against spot
and consensus (HK and SG names carry no usable consensus, `docs/consensus_target_unusable_rules.md`).

| Universe | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing |
|---|---|---|---|---|
| Stage 0 (46 names) | 10 of 30 | 18 of 46 | 19 of 46 | 0 |
| Stage 4 | 9 of 30 | 19 of 46 | 21 of 46 | 0 |

## What moved and why

The wave's gain is structural, not a consensus score: every one of the 46 names now reaches an
owner-named profile (fourteen labels in `routing_scope`, four new profiles, forty-eight pins), the
ratio ladder no longer promotes Hong Kong names to Hyperscaler on a dollar threshold read in HKD, and
a profile with a curated basket is priced on one cohort instead of on each name's FMP industry. Against
consensus the count is 9 of 30 (10 before); against spot 19 of 46 (18 before); the methodology
backtest passes on 21 (19 before).

**The ladder in dollars (decision 7).** The largest single effect. NetEase +138% to +34% against spot,
Lenovo +91% to +22%, BYD Electronic +163% to +49%, Kuaishou +130% to +56%: each had cleared the
US$100bn Hyperscaler line with HK$ or RMB revenue and taken the hyperscaler multiples. They route by
the HK market map now (China Internet Platform; Consumer Electronics / Hardware Ecosystem). Hua Hong
(01347.HK) moves from OSAT / Packaging to IDM / Foundry by row: 0% to −32%.

**One profile, one cohort (decision 2, alternative).** Retiring the static table in favour of the live
cohort first produced MSFT +51% and ORCL +143%: the "live cohort" for a hyperscaler was whatever FMP
industry the name carried, Software - Infrastructure for the two of them (30.3x EV/EBITDA, six names),
Consumer Electronics for Apple, Internet Content for Alphabet and Meta. The build therefore gives the
three profiles whose table was demoted the baskets the table was re-derived from
(`regional_comps.PROFILE_PEER_BASKETS`: the six hyperscalers, the six mature SaaS names, the six
mature platforms). On the basket (16.4x EV/EBITDA, 14.5x NTM on 2026-09-27) MSFT lands −12% against
spot (+29% before), ORCL +6% (+92%), META −17% (−9%), GOOG −27% (−19%), AAPL −50% (−46%). Apple and
Alphabet are the quality-premium pattern again (AXP, ISRG, WMT): the market pays the median hyperscaler
16x and Apple 25x, and a cohort median cannot carry that. Flagged, not tuned. The table stays as the
fallback for a market with no basket.

**Forward anchors (decision 5).** Mature SaaS on Forward P/E: CRM −3% to +8%, NOW −66% to −60%, ADBE
+71% to +90%. Adobe trades at 9.6x NTM earnings against a 22x cohort; the model prices the cohort and
refuses the discount, which is a disruption call the engine does not make. Fabless on Forward P/E:
NVDA −28% to −19%, AVGO −42% to −30%, AMD −77% to −68%, QCOM +23% to +39% (Qualcomm at 14x NTM against
the 23.5x Semiconductors cohort, the same shape as Adobe). Equipment names move the other way, AMAT
−39% to −44%, LRCX −40% to −44%, KLAC −22% to −25%: the Forward P/E is 0.85 x 22.8x, the premium
floor binding on names growing below the cohort's 18% (decision 4). Texas Instruments onto Analog /
Mixed-signal IDM: −36% to −34%.

**New profiles.** Telecom Carrier: T +37% to +22%, China Mobile +46% to +19%, TMUS −19% to −15%, VZ
−13% unchanged; China Telecom (+111%) and China Unicom (+174%) stay far above spot because the HK
carrier cohort's 4.8x EV/EBITDA is more than double what the market pays for the two state carriers,
and a state-owned-carrier discount is a regime decision for the owner, not a multiple to tune. Media &
Streaming: NFLX −13% to +2%, DIS −2% to +10%. Networking & Communication Equipment: CSCO −9% to +6%;
ANET −45% to −46% (Arista at 40x NTM against a 24x cohort).

**Growth premium (decision 4).** The absolute spread with the 0.85–1.30 band takes Accenture from
+89% to +63% (premium 1.50 to about 1.13, as the proposal said) and V03.SI from +19% to +31%. The
floor binds on Adobe, Qualcomm and Applied Materials among the probed names and on both bank goldens
(02888.HK 287.71 to 288.95, D05.SI 43.21 to 44.44, the ratio penalty of 0.65–0.79 lifting to 0.85).
On the goldens SCHW 76.50 to 72.28 and COST 525.00 to 514.36 give back part of a ratio premium.

**Memory (decision 3).** Micron unchanged at 293.44 (−73% against spot) and now carries the
`Cyclical_Peak_Consensus` regime flag: the peak-consensus trigger is recorded on the run rather than
silently resolving to the normalised P/E. NTM forward multiples are on by default in every case.

**Unchanged by construction.** PLTR −92%, CRWD −91% (the owner's CRWD rule), IBM −40%, INTC −36%,
Tencent +17%, Xiaomi −34%, SMIC +11%, and the three Singapore names, whose profiles the wave did not
touch.

## Open for the owner (flags, not builds)

1. Hyperscaler basket median against the individual premium: AAPL −50%, GOOG −27% on a 16.4x cohort
   the market prices at 25x for Apple. Same pattern as AXP, ISRG, WMT; a quality premium is an owner
   constant if it is anything.
2. The 0.85 floor binds on mature names growing below their cohort (equipment, Adobe, Qualcomm, the
   banks) and cuts every premium-scaled leg 15%. The band is the owner's; the binding count is reported
   here so the constant can be revisited on the quarterly clock.
3. China state carriers at +111% and +174%: HK carrier cohort 4.8x against their own 2x EV/EBITDA.
4. Adobe (+90%) and Qualcomm (+39%): the market's discount to cohort is a disruption call; the engine
   prices the cohort and flags the gap.
5. The local comps store is dated 2026-09-22; production refreshes weekly, so the production numbers
   will differ at the margin. No production run was made this wave.

## Per-ticker record

| Ticker | Profile now | Anchor (in blend) | IV Stage 0 | IV Stage 4 | Spot | Consensus | vs spot S0 | vs spot S4 | vs cons S0 | vs cons S4 | Regime flag | Backtest |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| NVDA | Fabless | Forward P/E (yes) | 162.02 | 181.41 | 225.07 | 345.21 | -28% | -19% | -53% | -47% | Growth_Inflection_Speculative | fired / MARKET_CLOSER |
| AAPL | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 183.19 | 170.13 | 341.07 | 342.13 | -46% | -50% | -46% | -50% | — | fired / MARKET_CLOSER |
| GOOG | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 274.94 | 249.13 | 341.08 | 431.94 | -19% | -27% | -36% | -42% | — | passed / TIE |
| MSFT | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 667.49 | 453.43 | 516.17 | 556.42 | +29% | -12% | +20% | -19% | — | passed / MODEL_CLOSER |
| META | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 681.35 | 620.17 | 751.66 | 766.94 | -9% | -17% | -11% | -19% | — | fired / MARKET_CLOSER |
| AVGO | Fabless | Forward P/E (yes) | 203.36 | 246.40 | 352.81 | 509.61 | -42% | -30% | -60% | -52% | — | passed / MODEL_CLOSER |
| MU | Memory / DRAM-NAND | P/E (norm) (yes) | 293.44 | 293.44 | 1,082.28 | 1,542.50 | -73% | -73% | -81% | -81% | Cyclical_Peak_Consensus | passed / MODEL_CLOSER |
| AMD | Fabless | Forward P/E (yes) | 142.22 | 204.51 | 630.63 | 602.59 | -77% | -68% | -76% | -66% | — | passed / MODEL_CLOSER |
| ASML | Equipment / EDA | Forward P/E (yes) | 1,268.48 | 1,196.42 | 1,743.94 | 2,305.75 | -27% | -31% | -45% | -48% | — | passed / MODEL_CLOSER |
| INTC | IDM / Foundry | EV/EBITDA (yes) | 79.09 | 79.09 | 123.00 | 110.97 | -36% | -36% | -29% | -29% | — | fired / MARKET_CLOSER |
| ORCL | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 262.54 | 144.94 | 137.08 | 236.52 | +92% | +6% | +11% | -39% | Growth_Inflection_Speculative | fired / MARKET_CLOSER |
| PLTR | Growth SaaS | NRR-adj DCF (yes) | 17.95 | 15.52 | 189.67 | 183.42 | -91% | -92% | -90% | -92% | — | fired / MARKET_CLOSER |
| CRM | Mature SaaS | Forward P/E (yes) | 226.88 | 252.95 | 234.02 | 271.91 | -3% | +8% | -17% | -7% | — | passed / MODEL_CLOSER |
| ADBE | Mature SaaS | Forward P/E (yes) | 402.96 | 448.00 | 235.47 | 269.84 | +71% | +90% | +49% | +66% | — | passed / MODEL_CLOSER |
| NOW | Mature SaaS | Forward P/E (yes) | 46.12 | 54.88 | 135.62 | 144.38 | -66% | -60% | -68% | -62% | — | fired / MARKET_CLOSER |
| CSCO | Networking & Communication Equipment (was Mature Platform) | Forward P/E (yes) | 97.09 | 113.28 | 106.70 | 131.42 | -9% | +6% | -26% | -14% | — | passed / TIE |
| QCOM | Fabless | Forward P/E (yes) | 249.33 | 280.47 | 201.97 | 204.48 | +23% | +39% | +22% | +37% | — | fired / MARKET_CLOSER |
| TXN | Analog / Mixed-signal IDM (was Equipment / EDA) | Forward P/E (yes) | 177.71 | 183.50 | 278.07 | 325.00 | -36% | -34% | -45% | -44% | — | fired / MARKET_CLOSER |
| AMAT | Equipment / EDA | Forward P/E (yes) | 296.85 | 273.05 | 485.00 | 666.30 | -39% | -44% | -55% | -59% | — | passed / TIE |
| LRCX | Equipment / EDA | Forward P/E (yes) | 190.50 | 177.71 | 315.21 | 375.94 | -40% | -44% | -49% | -53% | — | passed / MODEL_CLOSER |
| KLAC | Equipment / EDA | Forward P/E (yes) | 145.93 | 140.87 | 187.92 | 225.50 | -22% | -25% | -35% | -38% | — | passed / TIE |
| NFLX | Media & Streaming (was Mature Platform) | Forward P/E (yes) | 61.95 | 72.59 | 71.14 | 91.56 | -13% | +2% | -32% | -21% | — | fired / MARKET_CLOSER |
| DIS | Media & Streaming (was Travel & Dining) | Forward P/E (yes) | 103.99 | 116.49 | 106.16 | 126.30 | -2% | +10% | -18% | -8% | — | fired / MARKET_CLOSER |
| T | Telecom Carrier (was Stable Growth) | EV/EBITDA (yes) | 34.73 | 30.94 | 25.39 | 26.78 | +37% | +22% | +30% | +16% | — | fired / MARKET_CLOSER |
| VZ | Telecom Carrier (was Stable Growth) | EV/EBITDA (yes) | 41.18 | 40.89 | 47.08 | 48.58 | -13% | -13% | -15% | -16% | — | passed / TIE |
| TMUS | Telecom Carrier (was Stable Growth) | EV/EBITDA (yes) | 134.39 | 140.03 | 165.43 | 233.10 | -19% | -15% | -42% | -40% | — | fired / MARKET_CLOSER |
| ACN | IT Services | P/E (yes) | 333.25 | 286.61 | 176.11 | 203.59 | +89% | +63% | +64% | +41% | — | passed / MODEL_CLOSER |
| IBM | IT Services | P/E (yes) | 134.80 | 134.30 | 225.51 | 256.69 | -40% | -40% | -47% | -48% | — | fired / MARKET_CLOSER |
| CRWD | Cybersecurity / Mission-Critical SaaS | DCF (FCF+) (yes) | 21.66 | 21.67 | 252.13 | 233.92 | -91% | -91% | -91% | -91% | — | fired / MARKET_CLOSER |
| ANET | Networking & Communication Equipment (was Mature Platform) | Forward P/E (yes) | 113.22 | 110.52 | 206.55 | 224.85 | -45% | -46% | -50% | -51% | — | passed / MODEL_CLOSER |
| 00700.HK | China Internet Platform | DCF (yes) | 511.24 | 511.24 | 436.60 | — | +17% | +17% | — | — | — | fired / MARKET_CLOSER |
| 00941.HK | Telecom Carrier (was Stable Growth) | EV/EBITDA (yes) | 114.69 | 93.66 | 78.45 | — | +46% | +19% | — | — | — | fired / MARKET_CLOSER |
| 01810.HK | Consumer Electronics / Hardware Ecosystem | Forward P/E (yes) | 17.12 | 17.12 | 25.90 | — | -34% | -34% | — | — | — | fired / MARKET_CLOSER |
| 00981.HK | IDM / Foundry | EV/EBITDA (yes) | 70.33 | 70.33 | 63.35 | — | +11% | +11% | — | — | — | passed / MODEL_CLOSER |
| 09999.HK | China Internet Platform (was Hyperscaler / Tech Conglomerate) | DCF (yes) | 429.70 | 243.08 | 180.90 | — | +138% | +34% | — | — | — | passed / MODEL_CLOSER |
| 00728.HK | Telecom Carrier (was Stable Growth) | EV/EBITDA (yes) | 9.41 | 9.28 | 4.39 | — | +114% | +111% | — | — | — | fired / MARKET_CLOSER |
| 00992.HK | Consumer Electronics / Hardware Ecosystem (was Hyperscaler / Tech Conglomerate) | Forward P/E (yes) | 70.79 | 45.40 | 37.16 | — | +91% | +22% | — | — | — | fired / MARKET_CLOSER |
| 09888.HK | China Internet Platform (was Hyperscaler / Tech Conglomerate) | DCF (yes) | 115.71 | 108.04 | 85.30 | — | +36% | +27% | — | — | — | fired / MARKET_CLOSER |
| 00762.HK | Telecom Carrier (was Stable Growth) | EV/EBITDA (yes) | 16.12 | 15.29 | 5.58 | — | +189% | +174% | — | — | — | fired / MARKET_CLOSER |
| 01347.HK | IDM / Foundry (was OSAT / Packaging) | EV/EBITDA (yes) | 110.68 | 74.55 | 110.40 | — | +0% | -32% | — | — | — | passed / MODEL_CLOSER |
| 02382.HK | Consumer Electronics / Hardware Ecosystem (was Mature SaaS) | Forward P/E (yes) | 80.08 | 61.72 | 64.90 | — | +23% | -5% | — | — | — | fired / MARKET_CLOSER |
| 01024.HK | China Internet Platform (was Hyperscaler / Tech Conglomerate) | DCF (yes) | 69.13 | 46.81 | 30.02 | — | +130% | +56% | — | — | — | fired / MARKET_CLOSER |
| 00285.HK | Consumer Electronics / Hardware Ecosystem (was Hyperscaler / Tech Conglomerate) | Forward P/E (yes) | 59.49 | 33.71 | 22.66 | — | +163% | +49% | — | — | — | fired / MARKET_CLOSER |
| Z74.SI | Telco / Infrastructure (SG) | EV/EBITDA (yes) | 2.74 | 2.74 | 4.28 | — | -36% | -36% | — | — | — | passed / TIE |
| V03.SI | Tech Manufacturing / EMS (SG) | Forward P/E (yes) | 19.82 | 21.98 | 16.72 | — | +19% | +31% | — | — | — | passed / MODEL_CLOSER |
| AWX.SI | Tech Manufacturing / EMS (SG) | Forward P/E (yes) | 3.93 | 3.53 | 9.61 | — | -59% | -63% | — | — | — | passed / TIE |
