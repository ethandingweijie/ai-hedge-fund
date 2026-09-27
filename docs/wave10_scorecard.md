# Wave 10 scorecard: the 18 out-of-scope rows (Stage 0 → Stage 4)

Owner decisions 1-7 of docs/wave10_proposal.md (2026-09-27/28), built in f26db2eb. Both columns measure the
same 71 names on the local store with each unpinned name's sector seeded from its label row
(`scripts/wave_baseline.py --sector-from-label`, the router's best case).

| Engine | Within ±30% of consensus | Within ±30% of spot | Backtest passed | Anchor missing |
|---|---|---|---|---|
| Stage 0 | 9 of 33 | 30 of 71 | 14 | 4 |
| Stage 4 | 14 of 35 | 34 of 71 | 16 | 7 |

## What improved
Placement is right everywhere the owner decided: restaurants, car makers, hotels, the auto aftermarket and
the pharma names no longer land on Apparel / Athletic Wear or technology templates. MercadoLibre +175% → +42%
against spot on its pin; TJX and ROST move to -24% / -18% on the off-price sub-cohort; Genuine Parts
-53% → -19% on Industrial Distribution; Chery and Great Wall +354% / +361% → +50% / +159% on Automotive (OEM);
Marriott and Hilton -43% / -48% → -29% / -31% on the asset-light basket; Zoetis +94% → +5% and Haleon +30% → -3%;
the HK hotel owners, re-measured after the anchor fix, +9% and +21%.

## What did not, and why (for the owner)
- Renewable Utilities on IPP: Longyuan +231%, Brookfield (BEPI) +374%, Enlight -88%. The PPA-backed DCF anchor
  does not compute on them (surviving weight 0.6); the row stays as decided, and PPA inputs or a
  renewable-owner profile are the next decision.
- Specialty & Generic Pharma: Teva -1% → -50% on the generics basket (9.8x NTM P/E): a levered name whose
  equity is small against its EBITDA-based legs; Royalty Pharma -16% (a royalty book, flagged in the proposal).
- Casinos: MGM +267% (anchor missing), Wynn Macau +67%; the Macau and Las Vegas operators share one profile.
- Signet +140% on Luxury Goods; Fast Retailing still +776% (JPY statements against an HKD line, a data issue).
- Anchor missing on 7: MINISO (09896.HK), Jardine C&C (its Holding Company override), MGM, Corcept, Longyuan,
  Brookfield, and Shangri-La (its EV/EBITDA anchor is non-positive; book prices the NAV weight).

## Per-ticker record

| Ticker | Profile | vs spot, Stage 0 | vs spot, Stage 4 | vs cons, Stage 0 | vs cons, Stage 4 |
|---|---|---|---|---|---|
| MELI | Consumer Growth → Hyper-Growth Platform | +175% | +42% | +125% | +16% |
| ORLY | Apparel / Athletic Wear → Traditional Retail | -19% | -39% | -35% | -51% |
| 01880.HK | Household / Personal → Traditional Retail | -5% | -17% | — | — |
| 09896.HK | Apparel / Athletic Wear → Traditional Retail | +177% | — | — | — |
| 06288.HK | Apparel / Athletic Wear → Traditional Retail | +5391% | +776% | — | — |
| 01368.HK | Traditional Retail → Apparel / Athletic Wear | +18% | +36% | — | — |
| TJX | Apparel / Athletic Wear → Traditional Retail | -44% | -24% | -58% | -44% |
| ROST | Apparel / Athletic Wear → Traditional Retail | -34% | -18% | -43% | -29% |
| 03998.HK | Apparel / Athletic Wear → Apparel / Athletic Wear | +126% | +125% | — | — |
| 02232.HK | Apparel / Athletic Wear → Apparel / Athletic Wear | +44% | +44% | — | — |
| RL | Luxury Goods → Apparel / Athletic Wear | -6% | -24% | -27% | -41% |
| LEVI | Household / Personal → Apparel / Athletic Wear | -1% | +2% | -32% | -30% |
| DECK | Luxury Goods → Apparel / Athletic Wear | +70% | +73% | +15% | +17% |
| CROX | Luxury Goods → Apparel / Athletic Wear | -14% | +15% | -21% | +6% |
| 02313.HK | Apparel / Athletic Wear → Apparel / Athletic Wear | +33% | +33% | — | — |
| 00551.HK | Household / Personal → Apparel / Athletic Wear | -3% | +40% | — | — |
| RACE | Apparel / Athletic Wear → Luxury Goods | -61% | -48% | -66% | -55% |
| HMC | Traditional Retail → Automotive (OEM) | +142% | +139% | +134% | +131% |
| 09973.HK | Apparel / Athletic Wear → Automotive (OEM) | +354% | +50% | — | — |
| 02333.HK | Apparel / Athletic Wear → Automotive (OEM) | +361% | +159% | — | — |
| C07.SI | Apparel / Athletic Wear → Holding Company | +27% | — | — | — |
| 02338.HK | Apparel / Athletic Wear → Auto Parts & Suppliers | +77% | -25% | — | — |
| 03606.HK | Apparel / Athletic Wear → Auto Parts & Suppliers | +19% | -30% | — | — |
| GPC | Traditional Retail → Industrial Distribution | -53% | -19% | -58% | -28% |
| MGA | Household / Personal → Auto Parts & Suppliers | +4% | +10% | -2% | +3% |
| CMG | Apparel / Athletic Wear → Restaurants | -24% | -29% | -44% | -48% |
| YUM | Luxury Goods → Restaurants | -14% | -23% | -31% | -38% |
| 01364.HK | Apparel / Athletic Wear → Restaurants | -8% | -24% | — | — |
| 09658.HK | Apparel / Athletic Wear → Restaurants | +51% | -17% | — | — |
| 02282.HK | Luxury Goods → Casinos & Integrated Resorts | +4% | +18% | — | — |
| 01128.HK | Traditional Retail → Casinos & Integrated Resorts | -32% | +67% | — | — |
| LVS | Traditional Retail → Casinos & Integrated Resorts | +6% | +25% | -31% | -18% |
| MGM | Apparel / Athletic Wear → Casinos & Integrated Resorts | — | +267% | — | +146% |
| 09992.HK | Consumer Growth → Leisure Products & Brands | -34% | -44% | — | — |
| 02331.HK | Apparel / Athletic Wear → Apparel / Athletic Wear | +150% | +150% | — | — |
| AS | Traditional Retail → Leisure Products & Brands | -34% | -29% | -59% | -55% |
| HAS | Luxury Goods → Leisure Products & Brands | — | -4% | — | -21% |
| 01929.HK | Household / Personal → Luxury Goods | -2% | -19% | — | — |
| 01913.HK | Luxury Goods → Luxury Goods | -1% | -1% | — | — |
| 06181.HK | Traditional Retail → Luxury Goods | -24% | -9% | — | — |
| TPR | Luxury Goods → Luxury Goods | +31% | +31% | -15% | -15% |
| SIG | Household / Personal → Luxury Goods | +72% | +140% | +44% | +101% |
| AGS.SI | Agribusiness & Food (SG) → Agribusiness & Food (SG) | +41% | +41% | — | — |
| 00069.HK | Traditional Retail → Hotel Owner-Operator (HK) | +1% | +9% | — | — |
| 00045.HK | Household / Personal → Hotel Owner-Operator (HK) | -44% | +21% | — | — |
| MAR | Apparel / Athletic Wear → Lodging (Asset-Light) | -43% | -29% | -48% | -36% |
| HLT | Consumer Growth → Lodging (Asset-Light) | -48% | -31% | -53% | -38% |
| OU8.SI | Consumer Growth → Specialised Accommodation (SG) | +814% | +35% | — | — |
| RCL | Traditional Retail → Cruise Lines | -25% | -18% | -48% | -43% |
| VIK | Traditional Retail → Cruise Lines | -55% | -60% | -66% | -70% |
| 00300.HK | Apparel / Athletic Wear → Consumer Durables | +3% | +2% | — | — |
| 01999.HK | Household / Personal → Consumer Durables | +71% | +78% | — | — |
| SN | Traditional Retail → Consumer Durables | -61% | -56% | -66% | -62% |
| SGI | Apparel / Athletic Wear → Consumer Durables | -27% | -26% | -51% | -50% |
| 01276.HK | CDMO / Life Science Tools → Large Cap Pharma | -4% | -13% | — | — |
| 00867.HK | CDMO / Life Science Tools → Specialty & Generic Pharma | +37% | +35% | — | — |
| TAK | Large Cap Pharma → Specialty & Generic Pharma | -8% | -22% | — | — |
| TEVA | Large Cap Pharma → Specialty & Generic Pharma | -1% | -50% | -15% | -57% |
| HLN | CDMO / Life Science Tools → Household / Personal | +30% | -3% | +18% | -12% |
| 06618.HK | Large Cap Pharma → Pharma Distribution | -3% | -17% | — | — |
| 00874.HK | Large Cap Pharma → Specialty & Generic Pharma | +191% | +112% | — | — |
| RPRX | CDMO / Life Science Tools → Specialty & Generic Pharma | +11% | -16% | +3% | -22% |
| CORT | MedTech / Devices → Commercial Biotech | -18% | +33% | -24% | +24% |
| 00003.HK | Regulated Utility → City Gas Distribution (HK / China) | -47% | -55% | — | — |
| 02688.HK | Merchant Power → City Gas Distribution (HK / China) | +70% | +44% | — | — |
| ATO | Merchant Power → Regulated Utility | -33% | -28% | -43% | -39% |
| NI | Regulated Utility → Regulated Utility | -22% | -22% | -39% | -39% |
| 00916.HK | Regulated Utility → IPP | +49% | +231% | — | — |
| 03868.HK | Merchant Power → IPP | +187% | +90% | — | — |
| ENLT | Merchant Power → IPP | -34% | -88% | -51% | -91% |
| BEPI | Merchant Power → IPP | -19% | +374% | — | — |
| LULU | Apparel / Athletic Wear → Apparel / Athletic Wear | +95% | +95% | +102% | +102% |
| ZTS | Large Cap Pharma → Specialty & Generic Pharma | +94% | +5% | +58% | -14% |
