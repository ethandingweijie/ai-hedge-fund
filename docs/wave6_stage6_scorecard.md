# Wave 6 (Financials): Stage 6 scorecard

Measured 2026-09-27 on the Stage 4 engine over the 51-name universe (54 probed, J36.SI, BN4.SI and
00945.HK cut by the owner). Dual score against spot and consensus. "IV if accepted" is a preview in a
copy of the local archive with the eight Stage 5 pre-fills accepted (four life insurers' embedded value,
three alt managers' distributable earnings, the Berkshire look-through); every pre-fill is still pending
on the owner's gate.

| Universe | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing |
|---|---|---|---|---|
| Stage 0 (51 names) | 11 of 30 | 26 of 49 | 21 of 51 | 6 |
| Stage 4, inputs pending | 14 of 27 | 27 of 46 | 27 of 51 | 9 |
| Stage 4, inputs accepted (preview) | 16 of 30 | 28 of 49 | 27 of 51 | 2 |

## What moved and why

**Banks.** The re-derived calibration (decision 3) closes most of the money-center gap: JPM −43% to
−36% against spot, BAC −22% to −8%, WFC −19% to −5%, C −13% to +4%, Standard Chartered −18% to −3%,
HSBC −50% to −31%. GS and MS improve (−49% to −31%, −58% to −44%) but the Investment Bank profile still
prices book where the market pays 2.5-3x tangible for the franchise. Truist prices for the first time
(+9%); USB moves from +22% to −21% on Super-Regional Bank. The China banks come in (ICBC +85% to +70%,
BoCom +124% to +104%) but stay far above spot on the zero-premium 9.75% cost of equity: the engine now
records the market's own implied rate on every one of them (ICBC: GGM inverted at 0.49x tangible book
implies 15.8% against 9.8%) as GATE_BANK_IMPLIED_COE, never applied. That is the owner's stance, flagged.

**Lenders.** COF +136% to −29%, SYF +229% to −1% off the Mature Platform DCF and onto P/TBV and normalised
earnings. AXP −20% to −65%: the one card issuer the market prices at 6x tangible book on a 33% RoTE; the
cohort P/TBV of 1.3x is dominated by COF, SYF and ALLY and the GGM midpoint RoTE (18% target with 33%
realised) cannot carry it. The quality-premium pattern, as with ISRG and WMT; flagged, not tuned.

**Insurance.** P&C on its own profile with a PROPOSED calibration row (2.2x book, 9.0% CoE, 15% RoTE from
the US cohort; it had been reading the bank "default" row): PGR −51% to −21%, AIG +14% to −4%, CB −31% to
−34%, TRV −10% to −39%. The Combined Ratio Gate stands down and rolls into P/E (ops) on every name (no
source yet). Life: AIA, China Life, Ping An and Great Eastern leave the bank GGM; with their embedded
values accepted (P/EV 0.82x, 1.31x, 1.92x, 1.07x) the anchor prices, and the blend lands China Life +49%,
Ping An +57%, AIA −48%, Great Eastern −33% against spot: the EV leg is 0.35 and the HK life cohort's 1.55x
P/BV carries the rest. MetLife has no group EV (US GAAP) and its pre-fill fails by design.

**Fee businesses.** Brokers MMC −29% to −6%, AON −17% to +5%; data SPGI −47% to −22%, MSCI −26% to +6%;
SGX −49% to −10% on the global exchange basket (decision 5, a documented exception in
`comps_exchange_overrides`). Alt managers price nothing until the `alt_manager` input is accepted (the
profile's surviving weight is 0.30, under the blend floor); accepted, BX lands −11% against consensus
(P/DE 25.5x on $6.02 forward DE per share), KKR +4%, APO −31% (Lazard's 11-13x P/DE range is the low
outlier of the three sources and the owner may prefer a broader basis).

**Berkshire.** Holding Company by pin; the first look-through pre-fill carried the equity-method
associates ($19.9bn) and not the $300bn listed portfolio, so the SOTP input now has a separate cited
`listed_investments_at_market` field and the pre-fill was rebuilt (see the BRK-B row and
`docs/baselines/after_wave6_preview_brk.json`).

## Per name

| Ticker | Profile now | Anchor (in blend) | IV Stage 0 | IV Stage 4 | IV if accepted | Spot | Consensus | vs spot S0 | vs spot S4 | vs spot accepted | vs cons S4 | Backtest |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| JPM | Money Center Bank | GGM (P/B) (yes) | 195.91 | 219.59 |  | 343.06 | 373.64 | -43% | -36% |  | -41% | fired |
| BAC | Money Center Bank | GGM (P/B) (yes) | 44.03 | 52.13 |  | 56.70 | 66.00 | -22% | -8% |  | -21% | passed |
| WFC | Money Center Bank | GGM (P/B) (yes) | 66.85 | 79.16 |  | 82.97 | 98.83 | -19% | -5% |  | -20% | passed |
| C | Money Center Bank | GGM (P/B) (yes) | 116.58 | 140.23 |  | 134.28 | 150.18 | -13% | +4% |  | -7% | passed |
| GS | Investment Bank | GGM (P/B) (yes) | 475.50 | 643.08 |  | 935.45 | 1,202.33 | -49% | -31% |  | -47% | passed |
| MS | Investment Bank | GGM (P/B) (yes) | 82.89 | 110.66 |  | 196.31 | 235.25 | -58% | -44% |  | -53% | fired |
| USB | Super-Regional Bank | GGM (P/B) (yes) | 72.12 | 46.90 |  | 59.33 | 69.80 | +22% | -21% |  | -33% | fired |
| PNC | Super-Regional Bank | GGM (P/B) (yes) | 179.05 | 169.85 |  | 225.64 | 279.36 | -21% | -25% |  | -39% | fired |
| TFC | Super-Regional Bank | GGM (P/B) (yes) | — | 51.90 |  | 47.66 | 54.86 | — | +9% |  | -5% | passed |
| SCHW | Brokerage | P/E (norm) (yes) | 81.55 | 81.55 |  | 99.03 | 126.50 | -18% | -18% |  | -36% | passed |
| V | Payment Networks | P/E (norm) (yes) | 312.16 | 312.16 |  | 367.38 | 422.24 | -15% | -15% |  | -26% | passed |
| MA | Payment Networks | P/E (norm) (yes) | 428.80 | 428.80 |  | 567.65 | 666.67 | -24% | -24% |  | -36% | fired |
| AXP | Card Issuer & Consumer Lender | P/TBV (yes) | 245.98 | 106.89 |  | 308.89 | 378.09 | -20% | -65% |  | -72% | fired |
| COF | Card Issuer & Consumer Lender | P/TBV (yes) | 472.08 | 141.94 |  | 200.20 | 253.00 | +136% | -29% |  | -44% | passed |
| SYF | Card Issuer & Consumer Lender | P/TBV (yes) | 239.69 | 71.93 |  | 72.94 | 88.30 | +229% | -1% |  | -19% | passed |
| BRK-B | Holding Company | SOTP (analyst) (NO) | 524.66 | — | — | — | 604.00 | +4% | — | — | — | fired |
| PGR | Insurance (P&C) | GGM (P/B) (yes) | 100.96 | 162.49 |  | 205.50 | 222.62 | -51% | -21% |  | -27% | fired |
| CB | Insurance (P&C) | GGM (P/B) (yes) | 231.51 | 218.60 |  | 333.37 | 357.45 | -31% | -34% |  | -39% | fired |
| MET | Insurance | Embedded Value (NO) | 63.84 | 64.27 |  | 97.70 | 103.22 | -35% | -34% |  | -38% | passed |
| AIG | Insurance (P&C) | GGM (P/B) (yes) | 84.48 | 71.39 |  | 74.17 | 87.43 | +14% | -4% |  | -18% | passed |
| TRV | Insurance (P&C) | GGM (P/B) (yes) | 328.54 | 221.67 |  | 363.04 | 351.11 | -10% | -39% |  | -37% | fired |
| MMC | Insurance Broker | Forward P/E (yes) | 130.08 | 172.57 |  | 182.70 | 199.40 | -29% | -6% |  | -13% | passed |
| AON | Insurance Broker | Forward P/E (yes) | 229.83 | 292.47 |  | 277.99 | 387.30 | -17% | +5% |  | -24% | fired |
| BLK | Asset Manager | P/E (norm) (yes) | 1,174.57 | 1,174.57 |  | 1,086.31 | 1,336.57 | +8% | +8% |  | -12% | fired |
| BX | Alt Asset Manager | P/DE (Forward) (NO) | 44.12 | — | 128.12 | — | 143.50 | -63% | — | +8% | — | fired |
| KKR | Alt Asset Manager | P/DE (Forward) (NO) | 103.47 | — | 131.79 | — | 126.78 | +7% | — | +36% | — | fired |
| APO | Alt Asset Manager | P/DE (Forward) (NO) | 122.12 | — | 100.53 | — | 145.00 | +0% | — | -17% | — | fired |
| ICE | Market Infrastructure | P/E (norm) (yes) | 145.64 | 145.64 |  | 154.31 | 184.50 | -6% | -6% |  | -21% | passed |
| CME | Market Infrastructure | P/E (norm) (yes) | 310.80 | 310.80 |  | 264.48 | 299.00 | +18% | +18% |  | +4% | passed |
| SPGI | Financial Data & Ratings | Forward P/E (yes) | 215.03 | 314.77 |  | 403.30 | 512.25 | -47% | -22% |  | -39% | fired |
| MSCI | Financial Data & Ratings | Forward P/E (yes) | 409.04 | 584.31 |  | 551.36 | 701.71 | -26% | +6% |  | -17% | passed |
| 01398.HK | EM Bank | GGM (P/B) (yes) | 13.90 | 12.82 |  | 7.52 | — | +85% | +70% |  | — | fired |
| 00005.HK | Money Center Bank (EU) | GGM (P/B) (yes) | 79.49 | 108.44 |  | 157.60 | — | -50% | -31% |  | — | passed |
| 03988.HK | EM Bank | GGM (P/B) (yes) | 10.90 | 9.99 |  | 5.98 | — | +82% | +67% |  | — | fired |
| 00939.HK | EM Bank | GGM (P/B) (yes) | 16.96 | 15.81 |  | 9.54 | — | +78% | +66% |  | — | fired |
| 01288.HK | EM Bank | GGM (P/B) (yes) | 10.14 | 9.41 |  | 6.52 | — | +56% | +44% |  | — | fired |
| 02628.HK | Insurance | Embedded Value (NO) | 42.92 | 32.61 | 42.47 | 28.58 | — | +50% | +14% | +49% | — | passed |
| 03968.HK | EM Bank | GGM (P/B) (yes) | 59.37 | 57.45 |  | 50.50 | — | +18% | +14% |  | — | passed |
| 02318.HK | Insurance | Embedded Value (NO) | 112.89 | 72.72 | 82.74 | 52.70 | — | +114% | +38% | +57% | — | passed |
| 01299.HK | Insurance | Embedded Value (NO) | 41.55 | 27.32 | 38.57 | 73.75 | — | -44% | -63% | -48% | — | fired |
| 03328.HK | EM Bank | GGM (P/B) (yes) | 18.36 | 16.71 |  | 8.20 | — | +124% | +104% |  | — | fired |
| 02388.HK | Regional Bank | GGM (P/B) (yes) | 39.76 | 39.76 |  | 51.15 | — | -22% | -22% |  | — | passed |
| 02888.HK | Money Center Bank | GGM (P/B) (yes) | 194.11 | 232.00 |  | 238.00 | — | -18% | -3% |  | — | passed |
| 00388.HK | Market Infrastructure | P/E (norm) (yes) | 279.47 | 324.16 |  | 388.40 | — | -28% | -17% |  | — | passed |
| 00011.HK | Regional Bank | GGM (P/B) (yes) | 94.67 | 94.67 |  | — | — | — | — |  | — | passed |
| 01658.HK | EM Bank | GGM (P/B) (yes) | 9.38 | 8.68 |  | 5.33 | — | +76% | +63% |  | — | fired |
| D05.SI | Money Center Bank (SG) | GGM (P/B) (yes) | 43.17 | 42.84 |  | 78.00 | — | -45% | -45% |  | — | passed |
| O39.SI | Money Center Bank (SG) | GGM (P/B) (yes) | 18.88 | 18.73 |  | 32.01 | — | -41% | -41% |  | — | passed |
| U11.SI | Money Center Bank (SG) | GGM (P/B) (yes) | 39.50 | 39.23 |  | 42.57 | — | -7% | -8% |  | — | fired |
| S68.SI | Market Infrastructure (SG) | P/E (norm) (yes) | 11.38 | 20.23 |  | 22.40 | — | -49% | -10% |  | — | passed |
| G07.SI | Insurance | Embedded Value (NO) | 22.87 | 9.19 | 13.41 | 19.88 | — | +15% | -54% | -33% | — | passed |

## Routing and rendering checks (owner request, 2026-09-27)

- **Routing**: 51 of 51 names resolve to the owner's profile through pin or row, and every one of their
  FMP labels is in `routing_scope` (map v20).
- **PDF and Excel**: rendered from live engine runs for JPM, PGR, 01398.HK, S68.SI, and for 01299.HK and BX
  with their pre-fills accepted in an archive copy. Every report carries the base IV, the blended legs
  (GGM, Residual Income, P/TBV on the banks; GGM and P/E (ops) with the Combined Ratio Gate stand-down on
  Progressive; Embedded Value from the owner-accepted input on AIA; P/DE (Forward) on Blackstone), the
  profile name, and the flags (the implied-CoE flag on ICBC; the accepted-input provenance lines) in both
  the PDF text and the workbook cells (Blend, Banks, Target sheets).
- **Berkshire**: three Gemini passes; the third carries the $323.8bn listed portfolio and $251bn cash but
  gives P/E ranges without segment earnings for three of four segments, so the engine marks them Degraded
  and the SOTP does not publish. It stays pending with `segments price in the engine: False` on the gate.

## Open for the owner

1. Accept or revoke the eight pre-fills on the Model Accuracy gate (four life insurers, three alt
   managers, Berkshire); MetLife's fails by design (no group EV) and can be omitted.
2. The PROPOSED Insurance (P&C) calibration row (2.2x / 11.4x / 9.0% / 15%).
3. AXP's placement: keep on Card Issuer & Consumer Lender with the flag, or a premium-franchise pin.
4. GS and MS: the Investment Bank calibration (2.4x tangible, 9.5%) still leaves −31% and −44%; the
   market's 2.5-3.0x is a fee-franchise premium the book-anchored legs do not carry.
5. The Combined Ratio Gate's source: a review-gated `insurance_kpis` kind, or leave the stand-down.
