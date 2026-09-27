# Wave 9 (Industrials, materials, metals, transport): Stage 2 live routing probe and architecture review

Measured 2026-09-27 against FMP `profile` (`get_fmp_classification`, `docs/baselines/w9_probe_labels.json`),
read-only. Routing is exact-case, so **these strings are the only ones a row may be written from**. `row ->`
is what `profile_for_ticker` returns today; `pin ->` is the ticker lookup; a pin with a non-empty profile
beats the row. **No Wave 9 label is in `routing_scope`** (Aerospace & Defense, Coal and Residential
Construction are, from Waves 3, 1 and 8), so in production the rows below are not applied and every
unpinned name is placed by the ratio ladder.

## 1. Universe probed (54; the ◆ Stage 1 checkpoint decides keep or cut)

The largest members by market cap in each label and market, from the comps store; names earlier waves own
are left out (GE Vernova, Cameco, Bloom Energy: Wave 2; the A&D names: Wave 3).

**US**

| Ticker | Name | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|
| CAT | Caterpillar Inc. | `Agricultural - Machinery` | Capital Goods | **(empty)** |
| DE | Deere & Company | `Agricultural - Machinery` | Capital Goods | **(empty)** |
| PH | Parker-Hannifin Corporation | `Industrial - Machinery` | Capital Goods | — |
| ETN | Eaton Corporation plc | `Electrical Equipment & Parts` | Capital Goods | — |
| VRT | Vertiv Holdings Co | `Electrical Equipment & Parts` | Capital Goods | — |
| PWR | Quanta Services, Inc. | `Engineering & Construction` | Capital Goods | — |
| GWW | W.W. Grainger, Inc. | `Industrial - Distribution` | **none** | — |
| URI | United Rentals, Inc. | `Rental & Leasing Services` | **none** | — |
| WM | Waste Management, Inc. | `Waste Management` | **none** | — |
| CTAS | Cintas Corporation | `Specialty Business Services` | IT Services | — |
| MMM | 3M Company | `Conglomerates` | Holding Company | — |
| HON | Honeywell International Inc. | `Conglomerates` | Holding Company | **(empty)** |
| DAL | Delta Air Lines, Inc. | `Airlines, Airports & Air Services` | Airlines | Airlines |
| UNP | Union Pacific Corporation | `Railroads` | Rail / Logistics | — |
| ODFL | Old Dominion Freight Line, Inc. | `Trucking` | **none** | — |
| UPS | United Parcel Service, Inc. | `Integrated Freight & Logistics` | Rail / Logistics | **(empty)** |
| FDX | FedEx Corporation | `Integrated Freight & Logistics` | Rail / Logistics | **(empty)** |
| LIN | Linde plc | `Chemicals - Specialty` | Specialty Chemicals | **(empty)** |
| SHW | The Sherwin-Williams Company | `Chemicals - Specialty` | Specialty Chemicals | — |
| DOW | Dow Inc. | `Chemicals` | **none** | — |
| CTVA | Corteva, Inc. | `Agricultural Inputs` | **none** | — |
| CRH | CRH plc | `Construction Materials` | **none** | — |
| VMC | Vulcan Materials Company | `Construction Materials` | **none** | — |
| NUE | Nucor Corporation | `Steel` | **none** | **(empty)** |
| FCX | Freeport-McMoRan Inc. | `Copper` | Mining (Major) | Mining (Major) |
| SCCO | Southern Copper Corporation | `Copper` | Mining (Major) | — |
| NEM | Newmont Corporation | `Gold` | Mining (Major) | Mining (Major) |
| AA | Alcoa Corporation | `Aluminum` | Mining (Major) | — |
| IP | International Paper Company | `Paper, Lumber & Forest Products` | **none** | — |
| PKG | Packaging Corporation of America | `Packaging & Containers` | **none** | — |

**HK**

| Ticker | Name | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|
| 03808.HK | Sinotruk (Hong Kong) Limited | `Agricultural - Machinery` | Capital Goods | — |
| 02050.HK | Zhejiang Sanhua Intelligent Cont | `Industrial - Machinery` | Capital Goods | — |
| 03750.HK | Contemporary Amperex Technology  | `Electrical Equipment & Parts` | Capital Goods | **(empty)** |
| 00390.HK | China Railway Group Limited | `Engineering & Construction` | Capital Goods | — |
| 00669.HK | Techtronic Industries Company Li | `Manufacturing - Tools & Accessories` | Capital Goods | Consumer Durables |
| 00267.HK | CITIC Limited | `Conglomerates` | Holding Company | — |
| 00001.HK | CK Hutchison Holdings Limited | `Conglomerates` | Holding Company | **(empty)** |
| 00177.HK | Jiangsu Expressway Company Limit | `Industrial - Infrastructure Operations` | **none** | — |
| 00293.HK | Cathay Pacific Airways Limited | `Airlines, Airports & Air Services` | Airlines | — |
| 00066.HK | MTR Corporation Limited | `Railroads` | Rail / Logistics | — |
| 01766.HK | CRRC Corporation Limited | `Railroads` | Rail / Logistics | — |
| 01919.HK | COSCO SHIPPING Holdings Co., Ltd | `Marine Shipping` | Rail / Logistics | **(empty)** |
| 00914.HK | Anhui Conch Cement Company Limit | `Construction Materials` | **none** | **(empty)** |
| 03993.HK | CMOC Group Limited | `Industrial Materials` | **none** | — |
| 01378.HK | China Hongqiao Group Limited | `Aluminum` | Mining (Major) | — |
| 00358.HK | Jiangxi Copper Company Limited | `Copper` | Mining (Major) | — |
| 02899.HK | Zijin Mining Group Co., Ltd. | `Gold` | Mining (Major) | — |
| 01772.HK | Ganfeng Lithium Co., Limited | `Chemicals` | **none** | **(empty)** |

**SG**

| Ticker | Name | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|
| BN4.SI | Keppel Corporation Limited | `Conglomerates` | Conglomerate / Industrial (SG) | Conglomerate / Industrial (SG) |
| J36.SI | Jardine Matheson Holdings Limite | `Conglomerates` | Conglomerate / Industrial (SG) | Conglomerate / Industrial (SG) |
| C6L.SI | Singapore Airlines Limited | `Airlines, Airports & Air Services` | Aviation & Marine (SG) | Aviation & Marine (SG) |
| BS6.SI | Yangzijiang Shipbuilding (Holdin | `Industrial - Machinery` | Offshore Marine & Resources (SG) | Aviation & Marine (SG) |
| C52.SI | ComfortDelGro Corporation Limite | `Railroads` | Rail / Logistics | Rail / Logistics |
| S58.SI | SATS Ltd. | `General Transportation` | Rail / Logistics | Aerospace & Engineering (SG) |

## 2. The labels and the market's multiples (2026-09-27 store; cohort all)

Thirty-five labels in the four families, 383 US, 249 HKSE and 19 SES members. US: n / trailing P/E / NTM P/E /
EV/EBITDA / NTM EV/EBITDA / P/B / FCF yield / revenue growth; HKSE: n / P/E / EV/EBITDA / P/B.

| Label | US | HKSE | Row today |
|---|---|---|---|
| `Agricultural - Machinery` | 10 / 29.7 / 20.4 / 15.6 / 11.4 / 2.2 / 3.9% / −3.6% | 5 / 11.8 / 8.4 / 1.2 | Capital Goods |
| `Industrial - Machinery` | 20 / 28.5 / 21.9 / 19.4 / 14.6 / 5.2 / 4.1% / 5.4% | 16 / 14.5 / 9.7 / 1.8 | Capital Goods |
| `Electrical Equipment & Parts` | 20 / 35.8 / 26.4 / 24.4 / 18.5 / 5.0 / 3.2% / 6.0% | 13 / 15.2 / 11.5 / 1.7 | Capital Goods |
| `Engineering & Construction` | 20 / 28.8 / 20.7 / 21.3 / 13.3 / 6.0 / 4.2% / 13.1% | 19 / 12.9 / 12.8 / 0.9 | Capital Goods |
| `Manufacturing - Tools & Accessories` | 8 / 25.5 / 17.2 / 13.8 / 10.9 / 2.9 / 4.8% | — | Capital Goods |
| `Manufacturing - Metal Fabrication` | 9 / — / 19.2 / 15.6 / 12.4 / 1.6 | 7 / 33.4 / 9.3 / 2.8 | **none** |
| `Industrial - Distribution` | 19 / 26.6 / 21.6 / 16.0 / 15.4 / 4.1 / 4.0% | 6 / — / 5.6 / 1.4 | **none** |
| `Rental & Leasing Services` | 17 / 22.9 / 16.9 / 10.6 / 8.4 / 2.4 / 7.5% | 5 / 8.3 / — / 0.9 | **none** |
| `Waste Management` | 10 / — / 29.7 / 15.7 / 13.8 / 4.2 / 4.3% | 13 / 5.4 / 6.6 / 0.6 | **none** |
| `Specialty Business Services` | 20 / 25.3 / 17.7 / 13.2 / 11.3 / 2.6 / 5.4% | 19 / 28.7 / 18.0 / 1.6 | IT Services |
| `Conglomerates` | 12 / 18.5 / 18.8 / 11.9 / 11.4 / 1.6 / 5.5% | 17 / 5.7 / 8.8 / 0.4 | Holding Company (SG: Conglomerate / Industrial (SG)) |
| `Industrial - Infrastructure Operations` | — | 12 / 10.4 / 11.0 / 0.7 | **none** |
| `Airlines, Airports & Air Services` | 20 / 13.4 / 10.3 / 7.7 / 6.1 / 2.5 / 6.3% | 7 / 31.0 / 11.7 / 1.5 | Airlines |
| `Railroads` | 9 / 26.3 / 20.4 / 14.4 / 12.4 / 4.3 / 4.0% | 6 / 13.4 / 7.8 / 0.9 | Rail / Logistics |
| `Trucking` | 13 / 50.4 / 22.4 / 11.7 / 7.6 / 2.1 / 5.2% | — | **none** |
| `Marine Shipping` | 20 / 7.3 / 9.2 / 6.3 / 5.4 / 1.8 / 7.2% | 19 / 11.2 / 8.0 / 1.0 | Rail / Logistics |
| `Integrated Freight & Logistics` | 14 / 25.7 / 18.6 / 13.8 / 9.8 / 2.3 / 4.9% | 16 / 13.5 / 8.3 / 1.1 | Rail / Logistics |
| `Chemicals` | 14 / 12.5 / 9.2 / 10.5 / 7.4 / 1.2 / 9.9% / −9.1% | 7 / 15.0 / 8.9 / 0.7 | **none** |
| `Chemicals - Specialty` | 20 / 29.8 / 19.2 / 16.5 / 11.7 / 3.8 / 3.6% | 18 / 14.9 / 11.0 / 1.5 | Specialty Chemicals |
| `Agricultural Inputs` | 11 / 22.3 / 13.1 / 7.7 / 6.1 / 1.3 / 6.0% | 7 / 8.4 / 6.5 / 0.8 | **none** |
| `Construction Materials` | 20 / 22.4 / 17.9 / 13.1 / 10.9 / 3.4 / 4.4% | 16 / 14.6 / 8.5 / 0.6 | **none** |
| `Industrial Materials` | 19 / 25.3 / 17.7 / 8.0 / 8.2 / 3.8 / 5.2% | 9 / 13.3 / 6.6 / 0.7 | **none** (BHP, RIO, VALE, CMOC) |
| `Packaging & Containers` | 16 / 17.6 / 13.4 / 9.7 / 7.6 / 1.7 / 7.9% | 6 / — / — / 1.6 | **none** (SG: Specialty Chemicals) |
| `Paper, Lumber & Forest Products` | 8 / 19.2 / 16.0 / 9.3 / 7.4 / 1.3 / 6.2% | 7 / — / — / 0.5 | **none** |
| `Steel` | 16 / 21.6 / 12.1 / 9.9 / 7.0 / 1.4 / 4.3% | 15 / 11.5 / 8.1 / 0.5 | **none** (the Steel / Metals profile has no row) |
| `Aluminum` | — | 5 / 8.3 / 4.4 / 1.0 | Mining (Major) |
| `Copper` | 7 / 18.9 / 19.0 / 12.5 / 7.7 / 3.2 / 3.7% / 10.5% | 6 / 8.5 / 3.8 / 1.4 | Mining (Major) |
| `Gold` | 20 / 15.4 / 10.9 / 8.4 / 6.3 / 3.1 / 6.7% / 28.5% | 12 / 14.2 / 8.7 / 3.0 | Mining (Major) |
| `Other Precious Metals` | 19 / 13.9 / 9.6 / 10.3 / 7.7 / 3.0 / 1.6% | — | Mining (Major) |

Also in the families with shallow cohorts: `Industrial - Specialties`, `Industrial - Pollution & Treatment
Controls`, `Security & Protection Services`, `Business Equipment & Supplies`, `Silver`, `Uranium`
(Cameco is a Wave 2 name), `Manufacturing - Miscellaneous`, `Manufacturing - Textiles`. `Staffing &
Employment Services` and `Consulting Services` are Wave 10 by the plan; `Financial - Conglomerates` was
named for Wave 6 and never built.

## 3. The architecture as it stands

**Profiles.** Industrials: Capital Goods (EV/EBITDA .40, FCF Yield .30, ROIC vs WACC .20, P/E .10) is the
row for every machinery, electrical, construction and tools label; Backlog-Gated Long Cycle (the Wave 3
backlog DCF) exists for long-cycle names and no row sends any Wave 9 label to it; Automotive (OEM); six
SG and HK profiles (Conglomerate / Industrial (SG), Offshore Marine & Resources (SG), Aviation & Marine
(SG) and the A&D ones). Materials: Steel / Metals (EV/EBITDA (Norm) .50, cyclical) with no row at all, and
Specialty Chemicals. Transportation: Airlines, and Rail / Logistics which also takes parcel carriers and
container shipping. Resources: Mining (Major) (EV/EBITDA (norm) .60, Depleting Asset DCF .30, P/BV .10,
cyclical) for gold, copper, aluminium and the other precious metals alike. None of the Wave 9 profiles
carries statics or a basket, so every relative leg reads whatever cohort the label resolves to.

**What the rows get wrong, from the probe.**
- `Conglomerates` -> Financials / Holding Company prices 3M, Honeywell, CITIC and CK Hutchison on an SOTP
  (analyst) anchor at .70 that none of them has an input for; the US industrial conglomerates are
  operating companies, and the Asian ones are the look-through case of Wave 6 (Berkshire, Keppel).
- `Specialty Business Services` -> IT Services prices Cintas (uniform rental) and Copart on an IT
  consulting table.
- `Marine Shipping` and `Integrated Freight & Logistics` -> Rail / Logistics puts COSCO (container
  shipping at 8x earnings, a deep cycle) and UPS / FedEx (parcel networks) on a railroad's table.
- Waste, rentals, distribution, trucking, construction materials, chemicals, agricultural inputs,
  packaging, paper, industrial materials and steel have no row, so they are placed by the ladder: the same
  misroute pattern Waves 4 to 8 found.
- Mining (Major) is one table for gold (28% revenue growth in the cohort, 8.4x EBITDA), copper (12.5x) and
  aluminium; gold miners price on the metal price and reserves, copper on the development pipeline.

**Cycles.** Steel / Metals and Mining (Major) are in `_CYCLICAL_PROFILES` (the peak-consensus trigger, the
normalised anchor); chemicals, shipping, airlines and agricultural inputs are cyclical in the store's own
growth figures (−9%, 2.6%, 8.2%, −8.7%) and are not.

## 4. What Stage 1 (universe, ◆ owner) has to settle

1. Keep or cut: CATL (03750.HK, a battery maker under `Electrical Equipment & Parts`, the largest name in
   the wave at HK$2.3tn), Techtronic (00669.HK, pinned Consumer Durables), Ganfeng Lithium (01772.HK,
   lithium under `Chemicals`), CMOC (03993.HK, copper and cobalt under `Industrial Materials`), SATS
   (S58.SI, `General Transportation`, pinned Aerospace & Engineering (SG)).
2. The conglomerates: US industrial conglomerates on an operating profile; the Asian conglomerates (CK
   Hutchison, CITIC, Jardine, Keppel) on a look-through SOTP with the review-gated `sotp` input, the Wave 6
   Holding Company pattern.
3. New profiles the labels need: Waste & Environmental Services, Equipment Rental, Industrial
   Distribution, Construction Materials (aggregates and cement), Commodity Chemicals, Agricultural Inputs,
   Packaging & Paper, Trucking / Parcel & Logistics, Container & Bulk Shipping (cyclical), and a split of
   Mining (Major) into precious metals and base metals.
4. Rows for the labels with none and the re-rows above, with HK market maps where the HK cohort is a
   different business (HK machinery and construction trade at 10 to 15x earnings and under 2x book).
