# Wave 6 (Financials): Stage 2 live routing probe

Measured 2026-09-26 against FMP `profile` (`get_fmp_classification`, `docs/baselines/w6_probe_labels.json`),
read-only. Routing is exact-case, so **these strings are the only ones a row may be written from**.
`row ->` is what `profile_for_industry` returns today; `pin ->` is `TICKER_SECTOR_LOOKUP` (sector,
profile); a pin with a non-empty profile beats the row. None of the Financials labels is in
`routing_scope`, so with industry routing off in production every unpinned name below is placed by the
ratio ladder, exactly as Waves 4 and 5 found.

Peer counts are distinct members of the industry cohort in `regional_comps_members` (local store); the
floor is `MIN_INDUSTRY_PEERS = 5` per metric, and a metric below it falls to the family or sector rung.

## Universe probed (54; the ◆ Stage 1 checkpoint decides keep or cut)

| Ticker | Exch | FMP industry (exact) | row -> | pin -> | Note |
|---|---|---|---|---|---|
| JPM, BAC, WFC, C | NYSE | `Banks - Diversified` | Financials / Money Center Bank | Money Center Bank | |
| USB | NYSE | `Banks - Diversified` | Money Center Bank | **Tech / (empty)** | ladder |
| PNC, TFC | NYSE | `Banks - Regional` | **Financials / EM Bank** | **Tech / (empty)** | US super-regionals on an EM bank row |
| GS, MS | NYSE | `Financial - Capital Markets` | Financials / Brokerage | Investment Bank | |
| SCHW | NYSE | `Financial - Capital Markets` | Brokerage | Brokerage | golden fixture |
| V, MA | NYSE | `Financial - Credit Services` | **none** | Payment Networks | |
| AXP, COF, SYF | NYSE | `Financial - Credit Services` | **none** | **Tech / (empty)** | card issuers and lenders: no profile fits |
| BRK-B | NYSE | `Insurance - Diversified` | Financials / Insurance | **Tech / (empty)** | ladder |
| AIG | NYSE | `Insurance - Diversified` | Insurance | Insurance | |
| PGR, CB | NYSE | `Insurance - Property & Casualty` | Financials / Insurance (P&C) | **Insurance** | pin overrides the better row |
| TRV | NYSE | `Insurance - Property & Casualty` | Insurance (P&C) | **Tech / (empty)** | ladder |
| MET | NYSE | `Insurance - Life` | Insurance | Insurance | |
| MMC, AON | NYSE | `Insurance - Brokers` | **none** | **Tech / (empty)** | fee businesses, not underwriters |
| BLK | NYSE | `Asset Management` | Financials / Asset Manager | Asset Manager | |
| BX, KKR, APO | NYSE | `Asset Management` | Asset Manager | Alt Asset Manager | |
| ICE, CME | NYSE/NASDAQ | `Financial - Data & Stock Exchanges` | Financials / Market Infrastructure | Market Infrastructure | |
| SPGI, MSCI | NYSE | `Financial - Data & Stock Exchanges` | Market Infrastructure | **Tech / (empty)** | ratings and index data, not exchanges |
| 01398.HK, 03988.HK, 00939.HK, 01288.HK, 03328.HK | HKSE | `Banks - Diversified` | Money Center Bank | EM Bank | the big four plus BoCom |
| 00005.HK | HKSE | `Banks - Diversified` | Money Center Bank | Money Center Bank (EU) | |
| 02888.HK | HKSE | `Banks - Diversified` | Money Center Bank | Money Center Bank | golden fixture |
| 03968.HK, 01658.HK | HKSE | `Banks - Regional` | EM Bank | EM Bank | CMB, PSBC |
| 02388.HK, 00011.HK | HKSE | `Banks - Regional` | EM Bank | Regional Bank | BOCHK, Hang Seng |
| 02628.HK, 01299.HK | HKSE | `Insurance - Life` | Insurance | **Financials / (empty)** | China Life, AIA: embedded value names |
| 00945.HK | HKSE | `Insurance - Life` | Insurance | **Tech / (empty)** | Manulife HK line |
| 02318.HK | HKSE | `Insurance - Diversified` | Insurance | **Financials / (empty)** | Ping An |
| 00388.HK | HKSE | `Financial - Data & Stock Exchanges` | Market Infrastructure | **Financials / (empty)** | HKEX |
| D05.SI | SES | `Banks` | **none** | Money Center Bank (SG) | golden fixture; the SG label has no row |
| O39.SI, U11.SI | SES | `Banks - Regional` | EM Bank | Money Center Bank (SG) | |
| S68.SI | SES | `Financial - Data & Stock Exchanges` | Market Infrastructure | Market Infrastructure (SG) | |
| G07.SI | SES | `Insurance - Life` | Insurance | **Tech / (empty)** | Great Eastern |
| J36.SI, BN4.SI | SES | `Conglomerates` (sector Industrials) | Financials / Holding Company | Conglomerate / Industrial (SG) | BN4 golden fixture; both are Wave 9 names if the owner cuts them here |

## Cohort depth and the current market, per label (2026-09-26 store)

| Label | US n / P/E / P/BV | HKSE n / P/E / P/BV | SES |
|---|---|---|---|
| `Banks - Diversified` | 20 / 14.2x / 1.78x | 7 / 8.3x / 0.79x | sector rung only (9 / 12.3x / 1.5x) |
| `Banks - Regional` | 20 / 11.6x / 1.31x | 20 / 6.4x / 0.46x | sector rung |
| `Banks` (SG only) | — | — | sector rung |
| `Financial - Capital Markets` | 14 / 15.3x / 3.20x | 19 / 11.3x / 1.21x | sector rung |
| `Financial - Credit Services` | 16 / 12.6x / 1.99x | 14 / 7.2x / 0.51x | sector rung |
| `Insurance - Diversified` | 15 / 12.0x / 1.36x | sector rung | sector rung |
| `Insurance - Property & Casualty` | 20 / 11.4x / 2.18x | sector rung | sector rung |
| `Insurance - Life` | 18 / 11.8x / 1.38x | 8 / 7.3x / 1.55x | sector rung |
| `Insurance - Brokers` | 11 / 25.9x / 4.75x | sector rung | sector rung |
| `Asset Management` | 18 / 27.0x / 2.65x | 6 / 6.0x / 0.63x | sector rung |
| `Financial - Data & Stock Exchanges` | 10 / 22.1x / 3.89x | sector rung | sector rung |
| `Conglomerates` | 7 / 18.5x / 1.55x | 9 / 5.7x / 0.41x | sector rung |

SES has no industry-depth cohort for any Financials label: every SG name prices on the SES Financial
Services sector median (P/E 12.3x, P/BV 1.5x, n=9-10). The HKSE sector rung (P/E 8.3x, P/BV 0.94x, n=40)
is bank-heavy and will price HKEX, AIA and Ping An as banks unless a family or the US cohort is chosen.

## What Stage 1 (universe, ◆ owner) has to settle

1. Keep or cut: J36.SI and BN4.SI (labelled `Conglomerates` under Industrials; Holding Company row here,
   Conglomerate / Industrial (SG) by pin); SPGI and MSCI (ratings and index data under an exchanges label);
   00945.HK (Manulife's HK line, a Canadian reporter).
2. Whether life insurers (AIA, China Life, Ping An, MET, G07.SI) get a review-gated embedded-value input
   (EV per share and VNB, cited; the pipeline pattern) so the Insurance profile's Embedded Value anchor
   (0.35) can compute. Today it reads `embedded_value_per_share` or `vnb_margin` from the extractor and
   is None otherwise; the profile then runs on P/BV and P/E (ops).
3. Whether US super-regionals (PNC, TFC, USB) take the existing Super-Regional Bank profile by a new row
   split (`Banks - Regional` is one FMP label for PNC and for a Guizhou city bank), and whether the SG
   `Banks` label gets its own row (D05.SI is pinned today).
4. Whether card issuers and consumer lenders (AXP, COF, SYF) need a profile: no Financials profile prices
   a credit-card balance sheet (loan growth, net charge-offs, CET1), and Payment Networks is wrong for them.
