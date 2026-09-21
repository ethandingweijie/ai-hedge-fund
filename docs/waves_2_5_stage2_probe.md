# Waves 2-5: Stage 2 live routing probe

Measured 2026-09-21 against FMP `profile` (`get_fmp_classification`), read-only. Routing is exact-case, so **these strings are the only ones a row may be written from**. `row ->` is what `profile_for_ticker` returns today; `pin ->` is `TICKER_SECTOR_LOOKUP` (sector, profile); a pin with a non-empty profile beats the row.

Peer counts are distinct members of the largest cohort in `regional_comps_members` (local store, refreshed 2026-09-19; the table caps a cohort at 20). Floor: `MIN_INDUSTRY_PEERS = 5`.


## W2 power

| Ticker | Exch | FMP sector | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|---|
| NEE | NYSE | Utilities | `Regulated Electric` | Energy / Regulated Utility | Energy / Regulated Utility |
| DUK | NYSE | Utilities | `Regulated Electric` | Energy / Regulated Utility | Energy / Regulated Utility |
| SO | NYSE | Utilities | `Regulated Electric` | Energy / Regulated Utility | Energy / Regulated Utility |
| 00002.HK | HKSE | Utilities | `Regulated Electric` | Energy / Regulated Utility | none |
| 00006.HK | HKSE | Utilities | `Independent Power Producers` | Energy / IPP | none |
| VST | NYSE | Utilities | `Independent Power Producers` | Energy / IPP | Energy / Merchant Power |
| CEG | NASDAQ | Utilities | `Independent Power Producers` | Energy / IPP | none |
| NRG | NYSE | Utilities | `Independent Power Producers` | Energy / IPP | none |
| ENPH | NASDAQ | Energy | `Solar` | None | Energy / IPP |
| FSLR | NASDAQ | Energy | `Solar` | None | Energy / IPP |
| NXT | NASDAQ | Energy | `Solar` | None | none |
| CCJ | NYSE | Energy | `Uranium` | None | none |
| LEU | NYSE | Energy | `Uranium` | None | Resources / (empty) |
| BE | NYSE | Industrials | `Electrical Equipment & Parts` | Industrials / Capital Goods | Energy / IPP |
| GEV | NYSE | Industrials | `Industrial - Machinery` | Industrials / Capital Goods | Industrials / (empty) |
| SMR | NYSE | Industrials | `Electrical Equipment & Parts` | Industrials / Capital Goods | Industrials / (empty) |
| 01816.HK | HKSE | Utilities | `Independent Power Producers` | Energy / IPP | Energy / (empty) |

| Industry | US | HKSE | SES | Thin |
|---|---|---|---|---|
| `Regulated Electric` | 20 | 8 | 0 | SES |
| `Independent Power Producers` | 9 | 7 | 1 | SES |
| `Solar` | 8 | 3 | 0 | HKSE, SES |
| `Uranium` | 9 | 2 | 0 | HKSE, SES |
| `Electrical Equipment & Parts` | 20 | 13 | 0 | SES |
| `Industrial - Machinery` | 20 | 16 | 1 | SES |

## W3 A&D

| Ticker | Exch | FMP sector | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|---|
| LMT | NYSE | Industrials | `Aerospace & Defense` | None | Industrials / Aerospace & Defense |
| NOC | NYSE | Industrials | `Aerospace & Defense` | None | none |
| GD | NYSE | Industrials | `Aerospace & Defense` | None | none |
| RTX | NYSE | Industrials | `Aerospace & Defense` | None | Industrials / Aerospace & Defense |
| BA | NYSE | Industrials | `Aerospace & Defense` | None | Industrials / Aerospace & Defense |
| GE | NYSE | Industrials | `Aerospace & Defense` | None | Industrials / Aerospace & Defense |
| HWM | NYSE | Industrials | `Aerospace & Defense` | None | none |
| TDG | NYSE | Industrials | `Aerospace & Defense` | None | none |
| LHX | NYSE | Industrials | `Aerospace & Defense` | None | none |
| KTOS | NASDAQ | Industrials | `Aerospace & Defense` | None | none |
| AVAV | NASDAQ | Industrials | `Aerospace & Defense` | None | none |
| RKLB | NASDAQ | Industrials | `Aerospace & Defense` | None | none |
| S63.SI | SES | Industrials | `Aerospace & Defense` | Industrials / Aerospace & Engineering (SG) | none |
| S59.SI | SES | Industrials | `Airlines, Airports & Air Services` | Industrials / Aviation & Marine (SG) | none |
| 00232.HK | HKSE | Industrials | `Aerospace & Defense` | None | none |
| 02357.HK | HKSE | Industrials | `Aerospace & Defense` | None | none |

| Industry | US | HKSE | SES | Thin |
|---|---|---|---|---|
| `Aerospace & Defense` | 20 | 8 | 3 | SES |
| `Airlines, Airports & Air Services` | 20 | 7 | 2 | SES |

## W4 staples

| Ticker | Exch | FMP sector | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|---|
| ADM | NYSE | Consumer Defensive | `Agricultural Farm Products` | None | none |
| 00682.HK | HKSE | Consumer Defensive | `Agricultural Farm Products` | None | none |
| F34.SI | SES | Consumer Defensive | `Agricultural Farm Products` | Consumer / Agribusiness & Food (SG) | none |
| KO | NYSE | Consumer Defensive | `Beverages - Non-Alcoholic` | Consumer / Food & Beverage | Consumer / (empty) |
| PEP | NASDAQ | Consumer Defensive | `Beverages - Non-Alcoholic` | Consumer / Food & Beverage | Consumer / (empty) |
| BUD | NYSE | Consumer Defensive | `Beverages - Alcoholic` | Consumer / Food & Beverage | none |
| BF-B | NYSE | Consumer Defensive | `Beverages - Wineries & Distilleries` | None | none |
| 00168.HK | HKSE | Consumer Defensive | `Beverages - Alcoholic` | Consumer / Food & Beverage | Consumer / (empty) |
| MDLZ | NASDAQ | Consumer Defensive | `Food Confectioners` | None | none |
| TSN | NYSE | Consumer Defensive | `Agricultural Farm Products` | None | none |
| 00288.HK | HKSE | Consumer Defensive | `Packaged Foods` | Consumer / Food & Beverage | none |
| 00322.HK | HKSE | Consumer Defensive | `Packaged Foods` | Consumer / Food & Beverage | Consumer / (empty) |
| WMT | NASDAQ | Consumer Defensive | `Discount Stores` | None | Consumer / Traditional Retail |
| KR | NYSE | Consumer Defensive | `Grocery Stores` | None | none |
| 06808.HK | HKSE | Consumer Cyclical | `Department Stores` | None | Consumer / (empty) |
| D01.SI | SES | Consumer Defensive | `Grocery Stores` | Consumer / Traditional Retail | none |
| COST | NASDAQ | Consumer Defensive | `Discount Stores` | None | none |
| PG | NYSE | Consumer Defensive | `Household & Personal Products` | Consumer / Household / Personal | Consumer / (empty) |
| CL | NYSE | Consumer Defensive | `Household & Personal Products` | Consumer / Household / Personal | none |
| EL | NYSE | Consumer Defensive | `Household & Personal Products` | Consumer / Household / Personal | Consumer / Luxury Goods |
| PM | NYSE | Consumer Defensive | `Tobacco` | Consumer / Household / Personal | none |
| MO | NYSE | Consumer Defensive | `Tobacco` | Consumer / Household / Personal | none |
| USFD | NYSE | Consumer Defensive | `Food Distribution` | Consumer / Traditional Retail | none |

| Industry | US | HKSE | SES | Thin |
|---|---|---|---|---|
| `Agricultural Farm Products` | 13 | 7 | 4 | SES |
| `Beverages - Non-Alcoholic` | 11 | 10 | 1 | SES |
| `Beverages - Alcoholic` | 8 | 3 | 0 | HKSE, SES |
| `Beverages - Wineries & Distilleries` | 3 | 1 | 1 | US, HKSE, SES |
| `Food Confectioners` | 3 | 1 | 1 | US, HKSE, SES |
| `Packaged Foods` | 20 | 18 | 4 | SES |
| `Discount Stores` | 9 | 0 | 0 | HKSE, SES |
| `Grocery Stores` | 11 | 4 | 2 | HKSE, SES |
| `Department Stores` | 5 | 9 | 1 | SES |
| `Household & Personal Products` | 20 | 16 | 0 | SES |
| `Tobacco` | 7 | 3 | 0 | HKSE, SES |
| `Food Distribution` | 6 | 6 | 1 | SES |

## W5 health

| Ticker | Exch | FMP sector | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|---|
| AMGN | NASDAQ | Healthcare | `Drug Manufacturers - General` | Biopharma / Large Cap Pharma | Biopharma / (empty) |
| VRTX | NASDAQ | Healthcare | `Biotechnology` | Biopharma / Pre-approval Biotech | Biopharma / (empty) |
| 01801.HK | HKSE | Healthcare | `Biotechnology` | Biopharma / Pre-approval Biotech | Biopharma / (empty) |
| LLY | NYSE | Healthcare | `Drug Manufacturers - General` | Biopharma / Large Cap Pharma | Biopharma / Large Cap Pharma |
| PFE | NYSE | Healthcare | `Drug Manufacturers - General` | Biopharma / Large Cap Pharma | Biopharma / (empty) |
| 01093.HK | HKSE | Healthcare | `Drug Manufacturers - General` | Biopharma / Large Cap Pharma | Biopharma / (empty) |
| MDT | NYSE | Healthcare | `Medical - Devices` | None | Biopharma / (empty) |
| SYK | NYSE | Healthcare | `Medical - Devices` | None | Biopharma / (empty) |
| 00853.HK | HKSE | Healthcare | `Medical - Devices` | None | Biopharma / (empty) |
| BAX | NYSE | Healthcare | `Medical - Instruments & Supplies` | Biopharma / MedTech / Devices | none |
| UNH | NYSE | Healthcare | `Medical - Healthcare Plans` | None | HealthcareServices / Managed Care |
| CVS | NYSE | Healthcare | `Medical - Healthcare Plans` | None | HealthcareServices / Managed Care |
| BSL.SI | SES | Healthcare | `Medical - Care Facilities` | Healthcare / Healthcare Provider (SG) | none |
| TMO | NYSE | Healthcare | `Medical - Diagnostics & Research` | Biopharma / CDMO / Life Science Tools | Biopharma / CDMO / Life Science Tools |
| 02269.HK | HKSE | Healthcare | `Biotechnology` | Biopharma / CDMO / Life Science Tools | Biopharma / (empty) |
| VEEV | NYSE | Technology | `Software - Application` | Tech / Mature SaaS | Tech / Mature SaaS |

| Industry | US | HKSE | SES | Thin |
|---|---|---|---|---|
| `Drug Manufacturers - General` | 16 | 3 | 1 | HKSE, SES |
| `Biotechnology` | 20 | 19 | 0 | SES |
| `Medical - Devices` | 20 | 16 | 0 | SES |
| `Medical - Instruments & Supplies` | 20 | 6 | 1 | SES |
| `Medical - Healthcare Plans` | 11 | 0 | 0 | HKSE, SES |
| `Medical - Care Facilities` | 20 | 14 | 4 | SES |
| `Medical - Diagnostics & Research` | 20 | 5 | 0 | SES |
| `Software - Application` | 20 | 15 | 1 | SES |

## What the measurement changes in the plan

**Wave 2**
- `Solar` and `Uranium` are clean FMP labels: one row each reaches ENPH/FSLR/NXT and CCJ/LEU. Both are thin in HKSE (3, 2) and absent in SES, so each needs an `INDUSTRY_FAMILIES` entry.
- Fuel cells and SMRs have **no label of their own**: BE and SMR are `Electrical Equipment & Parts`, GEV is `Industrial - Machinery`, all routing to Capital Goods by row. They can only be reached by pin or `ticker_overrides`, never by a row.
- 00006.HK (Power Assets) is labelled `Independent Power Producers` and routes to IPP; it is a regulated-utility holding company. Needs an override, owner call.
- CEG and NRG carry no pin and route to IPP by row, while VST (same label) is pinned Merchant Power. One label, two profiles: the row can carry only one, the rest are pins.
- 01816.HK routes to IPP by row, with an empty-profile pin.

**Wave 3**
- Every US and HK name returns the single label `Aerospace & Defense`. **The three-way split cannot be expressed by industry rows.** One of the three profiles takes the row as the default; the other two are reached by `ticker_overrides` only. Which profile is the default is an owner decision at the taxonomy checkpoint.
- S59.SI (SIA Engineering) is labelled `Airlines, Airports & Air Services` and routes to Aviation & Marine (SG), not to the A&D profile. It is an MRO business.
- HKSE has 8 A&D members (clears the floor); SES has 3 (does not).

**Wave 4**
- TSN is labelled `Agricultural Farm Products`, the same as ADM, not Packaged Foods.
- WMT and COST share `Discount Stores`; COST has **no pin**, so a `Discount Stores` row would move COST unless it is pinned to Membership / Subscription Retail in the same commit. COST is a golden fixture.
- 06808.HK (Sun Art) is `Department Stores` under Consumer Cyclical: override only.
- `Beverages - Wineries & Distilleries` (BF-B; FMP's symbol is `BF-B`, not `BF.B`) and `Food Confectioners` (MDLZ) have no row and are thin in every market including the US (3 members each). They must pool into a family.
- `Tobacco` routes to Household / Personal today; US 7 members, HKSE 3, SES 0.

**Wave 5**
- `Medical - Devices` has **no row**: MDT, SYK and 00853.HK fall to the classifier, while `Medical - Instruments & Supplies` (BAX) reaches MedTech / Devices. A missing row the plan did not list.
- The `Biotechnology` row sends VRTX and 01801.HK to Pre-approval Biotech. Both are profitable commercial biotechs; extending `routing_scope` to `Biotechnology` without handling them would misroute both.
- `Medical - Healthcare Plans` has no row (as planned) and no HKSE or SES basket at all.
- `Drug Manufacturers - General` is thin in HKSE (3).
