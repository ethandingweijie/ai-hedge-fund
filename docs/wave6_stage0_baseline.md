# Wave 6 (Financials): Stage 0 baseline

Run 2026-09-26/27 on the engine at 43887e4 (Wave 5 refinements included), over the 54 names of the
Stage 2 probe (`docs/wave6_stage2_probe.md`), read-only; recorded in `docs/baselines/baseline_wave6.json`.
Every later Wave 6 change is judged against this table. Industry routing is off in production and none
of the Financials labels is in `routing_scope`, so a name reaches its profile by pin or by the ladder.

| Ticker | Routed profile today | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Backtest |
|---|---|---|---|---|---|---|---|
| JPM | Money Center Bank | GGM (P/B) (yes) | 195.91 | 373.64 | -48% | -43% | fired / MARKET_CLOSER |
| BAC | Money Center Bank | GGM (P/B) (yes) | 44.03 | 66.00 | -33% | -22% | fired / MARKET_CLOSER |
| WFC | Money Center Bank | GGM (P/B) (yes) | 66.85 | 98.83 | -32% | -19% | fired / MARKET_CLOSER |
| C | Money Center Bank | GGM (P/B) (yes) | 116.58 | 150.18 | -22% | -13% | passed / MODEL_CLOSER |
| GS | Investment Bank | GGM (P/B) (yes) | 475.50 | 1,202.33 | -60% | -49% | fired / MARKET_CLOSER |
| MS | Investment Bank | GGM (P/B) (yes) | 82.89 | 235.25 | -65% | -58% | fired / MARKET_CLOSER |
| USB | Mature Platform | DCF (FCF+) (yes) | 72.12 | 69.80 | +3% | +22% | fired / MARKET_CLOSER |
| PNC | Mature Platform | DCF (FCF+) (yes) | 179.05 | 279.36 | -36% | -21% | fired / MARKET_CLOSER |
| TFC | Mature Platform | DCF (FCF+) (NO) | — | 54.86 | — | — | passed / TIE |
| SCHW | Brokerage | P/E (norm) (yes) | 81.55 | 126.50 | -36% | -18% | passed / MODEL_CLOSER |
| V | Payment Networks | P/E (norm) (yes) | 312.16 | 422.24 | -26% | -15% | passed / MODEL_CLOSER |
| MA | Payment Networks | P/E (norm) (yes) | 428.80 | 666.67 | -36% | -24% | fired / MARKET_CLOSER |
| AXP | Mature Platform | DCF (FCF+) (yes) | 245.98 | 378.09 | -35% | -20% | fired / MARKET_CLOSER |
| COF | Mature Platform | DCF (FCF+) (yes) | 472.08 | 253.00 | +87% | +136% | fired / MARKET_CLOSER |
| SYF | Mature Platform | DCF (FCF+) (yes) | 239.69 | 88.30 | +171% | +229% | fired / MARKET_CLOSER |
| BRK-B | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 524.66 | 604.00 | -13% | +4% | passed / TIE |
| PGR | Insurance | Embedded Value (NO) | 100.96 | 222.62 | -55% | -51% | fired / MARKET_CLOSER |
| CB | Insurance | Embedded Value (NO) | 231.51 | 357.45 | -35% | -31% | passed / TIE |
| MET | Insurance | Embedded Value (NO) | 63.84 | 103.22 | -38% | -35% | passed / TIE |
| AIG | Insurance | Embedded Value (NO) | 84.48 | 87.43 | -3% | +14% | passed / MODEL_CLOSER |
| TRV | Mature Platform | DCF (FCF+) (yes) | 328.54 | 351.11 | -6% | -10% | fired / MARKET_CLOSER |
| MMC | Mature Platform | DCF (FCF+) (yes) | 130.08 | 199.40 | -35% | -29% | fired / MARKET_CLOSER |
| AON | Mature Platform | DCF (FCF+) (yes) | 229.83 | 387.30 | -41% | -17% | fired / MARKET_CLOSER |
| BLK | Asset Manager | P/E (norm) (yes) | 1,174.57 | 1,336.57 | -12% | +8% | fired / MARKET_CLOSER |
| BX | Alt Asset Manager | SOTP (FRE+Carry) (yes) | 44.12 | 143.50 | -69% | -63% | fired / MARKET_CLOSER |
| KKR | Alt Asset Manager | SOTP (FRE+Carry) (NO) | 103.47 | 126.78 | -18% | +7% | fired / MARKET_CLOSER |
| APO | Alt Asset Manager | SOTP (FRE+Carry) (yes) | 122.12 | 145.00 | -16% | +0% | fired / MARKET_CLOSER |
| ICE | Market Infrastructure | P/E (norm) (yes) | 145.64 | 184.50 | -21% | -6% | passed / TIE |
| CME | Market Infrastructure | P/E (norm) (yes) | 310.80 | 299.00 | +4% | +18% | passed / MODEL_CLOSER |
| SPGI | Mature Platform | DCF (FCF+) (yes) | 215.03 | 512.25 | -58% | -47% | passed / TIE |
| MSCI | Mature Platform | DCF (FCF+) (yes) | 409.04 | 701.71 | -42% | -26% | fired / MARKET_CLOSER |
| 01398.HK | EM Bank | GGM (P/B) (yes) | 13.90 | — | — | +85% | fired / MARKET_CLOSER |
| 00005.HK | Money Center Bank (EU) | GGM (P/B) (yes) | 79.49 | — | — | -50% | passed / TIE |
| 03988.HK | EM Bank | GGM (P/B) (yes) | 10.90 | — | — | +82% | fired / MARKET_CLOSER |
| 00939.HK | EM Bank | GGM (P/B) (yes) | 16.96 | — | — | +78% | fired / MARKET_CLOSER |
| 01288.HK | EM Bank | GGM (P/B) (yes) | 10.14 | — | — | +56% | fired / MARKET_CLOSER |
| 02628.HK | Money Center Bank | GGM (P/B) (yes) | 42.92 | — | — | +50% | passed / MODEL_CLOSER |
| 03968.HK | EM Bank | GGM (P/B) (yes) | 59.37 | — | — | +18% | passed / MODEL_CLOSER |
| 02318.HK | Money Center Bank | GGM (P/B) (yes) | 112.89 | — | — | +114% | fired / MARKET_CLOSER |
| 01299.HK | Money Center Bank | GGM (P/B) (yes) | 41.55 | — | — | -44% | fired / MARKET_CLOSER |
| 03328.HK | EM Bank | GGM (P/B) (yes) | 18.36 | — | — | +124% | fired / MARKET_CLOSER |
| 02388.HK | Regional Bank | GGM (P/B) (yes) | 39.76 | — | — | -22% | passed / MODEL_CLOSER |
| 02888.HK | Money Center Bank | GGM (P/B) (yes) | 194.11 | — | — | -18% | passed / MODEL_CLOSER |
| 00388.HK | Payment Networks | P/E (norm) (yes) | 279.47 | — | — | -28% | passed / TIE |
| 00011.HK | Regional Bank | GGM (P/B) (yes) | 94.67 | — | — | — | passed / TIE |
| 01658.HK | EM Bank | GGM (P/B) (yes) | 9.38 | — | — | +76% | fired / MARKET_CLOSER |
| 00945.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 327.14 | — | — | -4% | fired / MARKET_CLOSER |
| D05.SI | Money Center Bank (SG) | GGM (P/B) (yes) | 43.17 | — | — | -45% | passed / TIE |
| O39.SI | Money Center Bank (SG) | GGM (P/B) (yes) | 18.88 | — | — | -41% | passed / MODEL_CLOSER |
| U11.SI | Money Center Bank (SG) | GGM (P/B) (yes) | 39.50 | — | — | -7% | fired / MARKET_CLOSER |
| S68.SI | Market Infrastructure (SG) | P/E (norm) (yes) | 11.38 | — | — | -49% | passed / TIE |
| G07.SI | Mature SaaS | EPV (yes) | 22.87 | — | — | +15% | fired / MARKET_CLOSER |
| J36.SI | Conglomerate / Industrial (SG) | EV/EBITDA (yes) | 51.53 | — | — | -8% | passed / MODEL_CLOSER |
| BN4.SI | Conglomerate / Industrial (SG) | EV/EBITDA (yes) | 6.99 | — | — | -38% | passed / MODEL_CLOSER |

| Measure | Stage 0 |
|---|---|
| Within ±30% of consensus | 11 of 30 |
| Within ±30% of spot | 28 of 52 |
| Methodology backtest passed | 23 of 54 |
| Anchor missing from blend | 6 (TFC, PGR, CB, MET, AIG, KKR) |
| Median consensus spread | +19% |

## What the baseline says

**The worst Stage 0 of the six waves, and the causes are known.** Financials has 24 profiles, more than
any sector, and every one of them was calibrated once and never measured. Three things fail at once.

**1. The bank calibration is a 2023 snapshot.** The bank block blends GGM (P/B) .35, Residual Income .30,
P/TBV .20, P/E (norm) .10 and Excess Capital .05, and the P/TBV and P/E legs read the static
`_BANK_PROFILE_CALIBRATION` table (Money Center 1.4x / 12x, Investment Bank 1.2x / 10x, EM Bank 1.2x /
9x), not the market. The US `Banks - Diversified` cohort trades at 1.78x book and 14.2x today; JPM at
3.2x tangible book and 15x forward earnings. JPM's legs: GGM 242 (target P/B 1.86 on the broker's
19.5% RoTE, 10.0% CoE, 3.0% g), Residual Income 159, P/TBV 150, Excess Capital 125, blend 196 against
343 spot. GS and MS (−49%, −58%): Investment Bank CoE 11.0%, RoTE midpoint 13.8%, P/TBV 1.2 and P/E 10
against a `Financial - Capital Markets` cohort at 3.2x and 15.3x. The engine is not wrong that the
market pays more than book-anchored legs allow; it is wrong to price that gap off constants nobody has
re-derived since the cohorts moved.

**2. The China banks overshoot for the opposite reason.** ICBC, BoC, CCB, ABC, BoCom and PSBC land +56%
to +124% above spot on EM Bank: P/TBV 1.2 and P/E 9 statics against an HK `Banks - Diversified` cohort
at 0.79x and 8.3x, a 13.0% CoE that embeds a country premium the owner set to zero on 2026-09-26, and a
`P/E (norm)` leg reading a 5-year normalised net income 132% above trailing (ICBC: RMB 977bn against
RMB 420bn TTM), which is a data artifact to inspect, not a cycle. CMB (+18%) and BOCHK (−22%) land.

**3. Everything that is not a bank is misrouted.** Ping An (+114%), China Life (+50%) and AIA (−44%)
carry empty Financials pins and fall through the ladder to Money Center Bank, so three life insurers
are priced by a bank GGM. PGR, CB (pins) and MET, AIG (row) sit on the life `Insurance` profile whose
Embedded Value anchor (.35) and Combined Ratio Gate (.15) never compute; the blend runs on P/BV at 1.45x
and P/E (ops) at 8x for a Progressive the market prices at 4.5x book. COF (+136%) and SYF (+229%) reach
Mature Platform by the ladder and are valued by a DCF (FCF+) on a card lender's cash flow (COF DCF leg
795/share). USB, PNC, TFC, AXP, TRV, MMC, AON, SPGI, MSCI: all Mature Platform by the ladder; BRK-B and
Manulife (00945.HK): Hyperscaler; Great Eastern (G07.SI): Mature SaaS; HKEX (00388.HK): Payment Networks.
BX (−63%): the Alt Asset Manager profile's four legs are all non-implementable and proxy to EPV (32.9),
P/E (norm) (66.9) and EV/Revenue (18.7); a business the market prices on distributable earnings has no
distributable-earnings leg. SGX (−49%): the SG market has no exchange cohort, so Market Infrastructure
(SG) reads the SES Financial Services sector P/E (9.8x, banks) for an exchange at 25x.

**Where it lands.** C (−13%), V (−15%), SCHW (−18%), ICE (−6%), CME (+18%), BLK (+8%), APO (0%), AIG (+14%),
CMB (+18%), BOCHK (−22%), StanChart (−18%), UOB (−7%), Jardine (−8%).

**Consensus.** Median spread +19% above spot, the yardstick's usual premium; the dual score counts both.

## What Stage 1 (universe, ◆ owner) has to settle

The four questions in `docs/wave6_stage2_probe.md` section "What Stage 1 has to settle": keep or cut
J36.SI/BN4.SI, SPGI/MSCI, 00945.HK; a review-gated embedded-value input for life insurers; a Super-Regional
row split and an SG `Banks` row; a profile for card issuers and consumer lenders. Stage 3 (methods) is
proposed in `docs/wave6_stage3_methods_proposal.md`, derived from the 2026-09-26 cohorts and marked
PROPOSED throughout; nothing in the registry has changed.
