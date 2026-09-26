# Wave 5 (Health Care): Stage 6 scorecard

Measured 2026-09-26 on the Stage 4 engine (commit 924dabb): the 17-name build universe
(`docs/baselines/after_wave5.json`) and the 51 names the owner's seven-profile taxonomy added
(`docs/baselines/after_wave5_expanded.json`). Dual score: IV against spot and against consensus, with the
consensus spread as the covariate; GIS marks Growth_Inflection_Speculative (spread over 50%). Every
pipeline pre-fill is still pending, so the rNPV (Pipeline) leg is quarantined on every revenue name and
the blend re-weights without it.

| Universe | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing |
|---|---|---|---|---|
| Build 17 (Stage 0: 6 of 11, 12 of 15, 11 of 16, 2) | 7 of 12 | 14 of 17 | 11 of 17 | 0 |
| Owner expansion 51 | 16 of 26 | 25 of 47 | 16 of 51 | 10 |
| All 68 | 23 of 38 | 39 of 64 | 27 of 68 | 10 |

The 70% consensus bar is not cleared: 61% overall. The misses fall into five groups, below; two are
placements for the owner, one is a profile limit the review-gated pipeline input exists to fix, one is
the quality-premium pattern seen on WMT and COST, and one is names the engine correctly refuses to
price.

## 1. Placements to revisit (owner)

- **01666.HK is Tong Ren Tang Technologies**, a traditional Chinese medicine manufacturer, pinned to
  MedTech / Devices on the owner's list; EV/Revenue on a pharma gives +622% against spot. It belongs
  with the China pharma names (Large Cap Pharma row, or a China Pharma profile if the owner wants one).
- **01099.HK Sinopharm and 03320.HK CR Pharma are distributors** on the provider profile: +165% and
  +233% against spot, because the hospital EV/EBITDA and P/E (Ops) legs price a 5-6x distributor on a
  hospital cohort. The registry has a Pharma Distribution profile and the HK `Medical - Distribution`
  cohort has 7 members. Recommend moving both.
- **01515.HK CR Medical** (+207%) is a hospital operator priced at 2.2x EV/EBITDA against an HK care
  cohort median near 5x: the engine says cheap against peers. Genuine stance, flagged not fixed.

## 2. The Commercial Biotech profile on loss-making HK Chapter 18A names

Akeso 09926.HK (anchor uncomputable, −56%), Kelun-Biotech 06990.HK (−84%, DCF and rNPV both
uncomputable, Forward P/E on a first-profit year) and BeiGene (BGNE −27%, 06160.HK −14%) are
commercial-stage but not yet at steady earnings. Forward P/E is the wrong anchor for them until the
pipeline pre-fill is accepted, which is what the owner's quarantine rule is for: the leg prices nothing
synthetic today. REGN (+63% vs spot, +55% vs consensus) is the opposite: a 17.2x basket Forward P/E on a
name the market prices at 12x for Eylea erosion. Both are the profile's statics, not a bug.

## 3. Devices: the quality premium

ISRG −59%, BSX −15% vs spot (−43% vs consensus), EW −26%: the 3.5x Devices EV/Revenue basket cannot
carry a franchise the market prices at 15-20x revenue. Same pattern as WMT and COST in Wave 4. BDX (−2%),
ABT (+1%), 02190.HK (+14%), 02160.HK (+3%) land. MicroPort Robotics 02252.HK (−89%) is a single-leg
EV/Revenue on a pre-scale robot maker; the other three legs are uncomputable and the blend runs on 40%
of its weight.

## 4. Services and tools

Managed Care overshoots: CI +78%, ELV +41%, HUM +29% against spot, all on P/E (Ops) after the 2025
Medicare Advantage de-rating; the consensus spread there is +7% to +24%, so the market also expects
recovery, less of it. CNC prices nothing (loss year). UHS +61% and ENSG −47% straddle the hospital
cohort; THC −10% lands. Tools: DHR −14%, A +13%, IQV +14%, ILMN −17% land; CRL's P/E anchor is
uncomputable (loss year) and the blend runs at 65%.

## 5. Correctly unpriced

CRSP, 02197.HK (Clover) and A50.SI (Thomson Medical, negative EBITDA) produce no value: rNPV has no
accepted pipeline, Forward P/E has no earnings. That is the honest output until a pipeline input is
built and accepted for the pre-approval names (WAVE5 list extension for the owner).

## Per profile

### Large Cap Pharma

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| AMGN | Large Cap Pharma | P/E (yes) | 374.60 | 394.58 | -5% | -10% | -5% | passed / TIE |
| LLY | Large Cap Pharma | P/E (yes) | 517.30 | 1,343.07 | -61% | -56% | +13% | fired / MARKET_CLOSER |
| PFE | Large Cap Pharma | P/E (yes) | 37.01 | 28.50 | +30% | +29% | -1% | fired / MARKET_CLOSER |
| 01093.HK | Large Cap Pharma | P/E (yes) | 11.38 | — | — | +22% | — | passed / TIE |
| JNJ | Large Cap Pharma | P/E (yes) | 304.31 | 286.93 | +6% | +12% | +6% | passed / MODEL_CLOSER |
| ABBV | Large Cap Pharma | P/E (yes) | 203.60 | 287.08 | -29% | -23% | +9% | fired / MARKET_CLOSER |
| MRK | Large Cap Pharma | P/E (yes) | 173.82 | 158.12 | +10% | +17% | +6% | fired / MARKET_CLOSER |
| BMY | Large Cap Pharma | P/E (yes) | 77.35 | 69.22 | +12% | +23% | +10% | fired / MARKET_CLOSER |
| 01177.HK | Large Cap Pharma | P/E (yes) | 5.57 | — | — | -0% | — | passed / MODEL_CLOSER |
| 03692.HK | Large Cap Pharma | P/E (yes) | 31.70 | — | — | -5% | — | passed / MODEL_CLOSER |
| 02196.HK | Large Cap Pharma | P/E (yes) | 34.76 | — | — | +100% | — | fired / MARKET_CLOSER |
### Commercial Biotech

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| VRTX | Commercial Biotech | Forward P/E (yes) | 449.43 | 570.93 | -21% | -15% | +8% | passed / MODEL_CLOSER |
| 01801.HK | Commercial Biotech | Forward P/E (yes) | 94.61 | — | — | -5% | — | passed / MODEL_CLOSER |
| REGN | Commercial Biotech | Forward P/E (yes) | 1,283.98 | 827.71 | +55% | +63% | +5% | passed / TIE |
| BIIB | Commercial Biotech | Forward P/E (yes) | 268.00 | 245.88 | +9% | +18% | +8% | passed / MODEL_CLOSER |
| ALNY | Commercial Biotech | Forward P/E (yes) | 262.87 | 347.33 | -24% | +3% | +36% | fired / MARKET_CLOSER |
| BGNE | Commercial Biotech | Forward P/E (yes) | 134.27 | 258.00 | -48% | -27% | +40% | fired / UNSCORABLE |
| 09926.HK | Commercial Biotech | Forward P/E (NO) | 41.25 | — | — | -56% | — | fired / MARKET_CLOSER |
| 06160.HK | Commercial Biotech | Forward P/E (yes) | 186.12 | — | — | -14% | — | fired / MARKET_CLOSER |
| 06990.HK | Commercial Biotech | Forward P/E (yes) | 76.83 | — | — | -84% | — | fired / MARKET_CLOSER |
### Pre-approval Biotech

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| CRSP | Pre-approval Biotech | rNPV (NO) | — | 67.83 | — | — | +25% | passed / TIE |
| BEAM | Pre-approval Biotech | rNPV (NO) | 46.26 | 59.50 | -22% | +91% | +145% GIS | fired / MARKET_CLOSER |
| KYMR | Pre-approval Biotech | rNPV (NO) | 59.75 | 138.07 | -57% | -47% | +23% | fired / MARKET_CLOSER |
| 02197.HK | Pre-approval Biotech | rNPV (NO) | — | — | — | — | — | passed / MODEL_CLOSER |
| 09688.HK | Pre-approval Biotech | rNPV (NO) | 15.78 | — | — | -20% | — | passed / MODEL_CLOSER |
### MedTech / Devices

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| MDT | MedTech / Devices | EV/Revenue (yes) | 90.29 | 98.00 | -8% | +2% | +11% | fired / MARKET_CLOSER |
| SYK | MedTech / Devices | EV/Revenue (yes) | 212.50 | 372.39 | -43% | -22% | +37% | fired / MARKET_CLOSER |
| 00853.HK | MedTech / Devices | EV/Revenue (yes) | 5.97 | — | — | -2% | — | passed / MODEL_CLOSER |
| BAX | MedTech / Devices | EV/Revenue (yes) | 33.10 | 25.00 | +32% | +42% | +7% | fired / MARKET_CLOSER |
| ABT | MedTech / Devices | EV/Revenue (yes) | 102.15 | 121.50 | -16% | +1% | +20% | fired / MARKET_CLOSER |
| BSX | MedTech / Devices | EV/Revenue (yes) | 37.29 | 65.20 | -43% | -15% | +48% | fired / MARKET_CLOSER |
| ISRG | MedTech / Devices | EV/Revenue (yes) | 165.42 | 491.15 | -66% | -59% | +21% | fired / MARKET_CLOSER |
| EW | MedTech / Devices | EV/Revenue (yes) | 63.82 | 101.70 | -37% | -26% | +18% | fired / MARKET_CLOSER |
| BDX | MedTech / Devices | EV/Revenue (yes) | 180.70 | 186.88 | -3% | -2% | +2% | passed / MODEL_CLOSER |
| 01666.HK | MedTech / Devices | EV/Revenue (yes) | 22.31 | — | — | +622% | — | fired / MARKET_CLOSER |
| 02190.HK | MedTech / Devices | EV/Revenue (yes) | 24.62 | — | — | +14% | — | fired / MARKET_CLOSER |
| 02160.HK | MedTech / Devices | EV/Revenue (yes) | 1.32 | — | — | +3% | — | fired / MARKET_CLOSER |
| 02252.HK | MedTech / Devices | EV/Revenue (yes) | 2.26 | — | — | -89% | — | fired / MARKET_CLOSER |
### CDMO / Life Science Tools

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| TMO | CDMO / Life Science Tools | P/E (yes) | 563.63 | 631.42 | -11% | -16% | -6% | passed / TIE |
| 02269.HK | CDMO / Life Science Tools | P/E (yes) | 49.64 | — | — | -6% | — | passed / MODEL_CLOSER |
| DHR | CDMO / Life Science Tools | P/E (yes) | 192.68 | 223.77 | -14% | -14% | -0% | fired / MARKET_CLOSER |
| ILMN | CDMO / Life Science Tools | P/E (yes) | 224.68 | 210.67 | +7% | -17% | -22% | fired / MARKET_CLOSER |
| A | CDMO / Life Science Tools | P/E (yes) | 195.43 | 170.00 | +15% | +13% | -2% | passed / TIE |
| CRL | CDMO / Life Science Tools | P/E (NO) | 147.79 | 298.33 | -50% | -50% | +2% | fired / MARKET_CLOSER |
| IQV | CDMO / Life Science Tools | P/E (yes) | 307.91 | 246.13 | +25% | +14% | -9% | fired / MARKET_CLOSER |
| 02268.HK | CDMO / Life Science Tools | P/E (yes) | 33.45 | — | — | -59% | — | passed / TIE |
| 01548.HK | CDMO / Life Science Tools | P/E (NO) | 22.50 | — | — | -48% | — | fired / MARKET_CLOSER |
| 03759.HK | CDMO / Life Science Tools | P/E (yes) | 39.60 | — | — | +30% | — | fired / MARKET_CLOSER |
| 02359.HK | CDMO / Life Science Tools | P/E (yes) | 156.77 | — | — | -25% | — | passed / MODEL_CLOSER |
### Managed Care

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| UNH | Managed Care | P/E (Ops) (yes) | 288.52 | 473.89 | -39% | -23% | +26% | passed / MODEL_CLOSER |
| CVS | Managed Care | P/E (Ops) (yes) | 89.43 | 111.20 | -20% | +0% | +25% | passed / MODEL_CLOSER |
| MOH | Managed Care | P/E (Ops) (yes) | 294.78 | 205.45 | +43% | +55% | +8% | fired / MARKET_CLOSER |
| ELV | Managed Care | P/E (Ops) (yes) | 559.07 | 444.06 | +26% | +41% | +12% | fired / MARKET_CLOSER |
| CI | Managed Care | P/E (Ops) (yes) | 482.85 | 338.33 | +43% | +78% | +24% | fired / MARKET_CLOSER |
| CNC | Managed Care | P/E (Ops) (NO) | — | 68.29 | — | — | +10% | fired / MARKET_CLOSER |
| HUM | Managed Care | P/E (Ops) (yes) | 513.50 | 424.21 | +21% | +29% | +7% | fired / MARKET_CLOSER |
### Healthcare Providers / Services

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| HCA | Healthcare Providers / Services | EV/EBITDA (yes) | 387.50 | 457.53 | -15% | -11% | +5% | passed / TIE |
| THC | Healthcare Providers / Services | EV/EBITDA (yes) | 233.28 | 283.36 | -18% | -10% | +9% | fired / MARKET_CLOSER |
| UHS | Healthcare Providers / Services | EV/EBITDA (yes) | 287.14 | 193.00 | +49% | +61% | +8% | passed / MODEL_CLOSER |
| ENSG | Healthcare Providers / Services | EV/EBITDA (yes) | 92.21 | 215.00 | -57% | -47% | +23% | fired / MARKET_CLOSER |
| 01099.HK | Healthcare Providers / Services | EV/EBITDA (yes) | 39.40 | — | — | +165% | — | fired / MARKET_CLOSER |
| 03320.HK | Healthcare Providers / Services | EV/EBITDA (yes) | 14.29 | — | — | +233% | — | fired / MARKET_CLOSER |
| 01515.HK | Healthcare Providers / Services | EV/EBITDA (yes) | 7.33 | — | — | +207% | — | fired / MARKET_CLOSER |
| 06078.HK | Healthcare Providers / Services | EV/EBITDA (yes) | 12.72 | — | — | +32% | — | passed / TIE |
### Healthcare Provider (SG)

| Ticker | Profile | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Cons spread | Backtest |
|---|---|---|---|---|---|---|---|---|
| BSL.SI | Healthcare Provider (SG) | EV/EBITDA (yes) | 0.74 | — | — | -11% | — | passed / MODEL_CLOSER |
| Q0F.SI | Healthcare Provider (SG) | EV/EBITDA (yes) | 1.47 | — | — | -42% | — | passed / TIE |
| A50.SI | Healthcare Provider (SG) | EV/EBITDA (NO) | — | — | — | — | — | fired / MARKET_CLOSER |
| QC7.SI | Healthcare Provider (SG) | EV/EBITDA (yes) | 0.61 | — | — | +20% | — | passed / MODEL_CLOSER |

## Status

Shipped in 924dabb; unpushed with 42ea7e2, 5306d5b, 84a6778 (tag golden-2026-09-26), 8ab17de. Open for
the owner: the three placements in section 1; pipeline acceptance for LLY, AMGN, PFE, VRTX, 01801.HK,
01093.HK on the Model Accuracy gate; Large Cap Pharma anchor (trailing vs Forward P/E); a Commercial
Biotech rNPV WACC row; pipeline pre-fills for the pre-approval names.
