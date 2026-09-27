# Wave 8 (Real estate): Stage 2 live routing probe and architecture review

Measured 2026-09-27 against FMP `profile` (`get_fmp_classification`, `docs/baselines/w8_probe_labels.json`),
read-only. Routing is exact-case, so **these strings are the only ones a row may be written from**.
`row ->` is what `profile_for_ticker` returns today; `pin ->` is `TICKER_SECTOR_LOOKUP`; a pin with a
non-empty profile beats the row. **No real-estate label is in `routing_scope`**, so in production the rows
below are not applied: every unpinned name is placed by the ratio ladder (Stage 0 shows China Resources
Land and CBRE on Mature SaaS for that reason), and only PSA (REIT) carries a pinned profile.

## 1. Universe probed (34; the ◆ Stage 1 checkpoint decides keep or cut)

**US**

| Ticker | Name | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|
| WELL | Welltower Inc. | `REIT - Healthcare Facilities` | **none** | **(empty)** |
| PLD | Prologis, Inc. | `REIT - Industrial` | **none** | **(empty)** |
| EQIX | Equinix, Inc. | `REIT - Specialty` | **none** | **(empty)** |
| AMT | American Tower Corporation | `REIT - Specialty` | **none** | **(empty)** |
| DLR | Digital Realty Trust, Inc. | `REIT - Specialty` | **none** | **(empty)** |
| SPG | Simon Property Group, Inc. | `REIT - Retail` | REIT | **(empty)** |
| PSA | Public Storage | `REIT - Industrial` | **none** | REIT |
| O | Realty Income Corporation | `REIT - Retail` | REIT | **(empty)** |
| VTR | Ventas, Inc. | `REIT - Healthcare Facilities` | **none** | **(empty)** |
| CBRE | CBRE Group, Inc. | `Real Estate - Services` | Ad / Consulting | — |
| DHI | D.R. Horton, Inc. | `Residential Construction` | **none** | — |
| IRM | Iron Mountain Incorporated | `REIT - Specialty` | **none** | — |

**HK**

| Ticker | Name | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|
| 00016.HK | Sun Hung Kai Properties Limited | `Real Estate - Development` | REIT | **(empty)** |
| 01109.HK | China Resources Land Limited | `Real Estate - Development` | REIT | — |
| 01113.HK | CK Asset Holdings Limited | `Real Estate - Development` | REIT | **(empty)** |
| 01972.HK | Swire Properties Limited | `Real Estate - Development` | REIT | — |
| 00688.HK | China Overseas Land & Investment L | `Real Estate - Development` | REIT | **(empty)** |
| 00012.HK | Henderson Land Development Company | `Real Estate - Diversified` | REIT | **(empty)** |
| 00823.HK | Link Real Estate Investment Trust | `REIT - Retail` | REIT | — |
| 00083.HK | Sino Land Company Limited | `Real Estate - Development` | REIT | — |
| 00004.HK | Wharf (Holdings) Limited | `Real Estate - Development` | REIT | — |
| 02202.HK | China Vanke Co., Ltd. | `Real Estate - Development` | REIT | — |
| 00960.HK | Longfor Group Holdings Limited | `Real Estate - Development` | REIT | — |

**SG**

| Ticker | Name | FMP industry (exact) | row -> | pin -> |
|---|---|---|---|---|
| H78.SI | Hongkong Land Holdings Limited | `Real Estate - Development` | Property Developer (SG) | — |
| C38U.SI | CapitaLand Integrated Commercial T | `REIT - Retail` | S-REIT | — |
| 9CI.SI | CapitaLand Investment Limited | `Real Estate - Services` | Real Estate Agency (SG) | — |
| A17U.SI | CapitaLand Ascendas REIT | `REIT - Industrial` | S-REIT | — |
| C09.SI | City Developments Limited | `Real Estate - Development` | Property Developer (SG) | — |
| U14.SI | UOL Group Limited | `Real Estate - Development` | Property Developer (SG) | — |
| N2IU.SI | Mapletree Pan Asia Commercial Trus | `REIT - Diversified` | S-REIT | — |
| M44U.SI | Mapletree Logistics Trust | `REIT - Industrial` | S-REIT | — |
| ME8U.SI | Mapletree Industrial Trust | `REIT - Industrial` | S-REIT | — |
| AJBU.SI | Keppel DC REIT | `REIT - Diversified` | S-REIT | — |
| U06.SI | Singapore Land Group Limited | `Real Estate - Diversified` | Property Developer (SG) | — |

Notes. FMP labels Public Storage `REIT - Industrial` and the tower and data-centre names `REIT -
Specialty`; the REIT profile's own sub-type classifier (`_classify_reit_subtype`, keyword on ticker and
lookup notes) decides the cap rate and P/FFO within the profile, and its `office` bucket matches the word
"tower", so American Tower reads the office cap rate (7.5%) unless the notes say otherwise. D.R. Horton
is `Residential Construction` under Consumer Cyclical: a homebuilder, not real estate to FMP. CapitaLand
Investment is a fund manager under `Real Estate - Services`, routed to Real Estate Agency (SG) by the SG
market map while a Real Estate Asset Manager (SG) profile exists for exactly this business.

## 2. Cohort depth and the current market (2026-09-27 store; n / P/B / P/E / EV/EBITDA / FCF yield)

| Label | US | HKSE | SES |
|---|---|---|---|
| `REIT - Retail` | 20 / 1.78x / 25.3x / 14.8x / 5.1% | 3 (sector rung) | 5 / 0.77x / 13.9x / 18.3x / 10.6% |
| `REIT - Industrial` | 15 / 1.82x / 28.7x / 15.6x / 5.4% | 1 | 7 / 0.80x / 15.7x / 18.8x / 8.3% |
| `REIT - Specialty` | 16 / 2.40x / 25.4x / 17.6x / 5.4% (large 5: 7.2x P/B, 31x) | — | 1 |
| `REIT - Healthcare Facilities` | 16 / 1.82x / 25.5x / 14.6x / 4.9% | — | 2 |
| `REIT - Office` | 15 / 1.05x / 29.2x / 14.9x / 9.3% | 1 | 2 |
| `REIT - Residential` | 14 / 2.00x / 33.1x / 14.8x / 6.3% | — | — |
| `REIT - Diversified` | 19 / 1.04x / 22.0x / 12.8x / 8.1% | 5 / 0.29x / — / — / 10.9% | 9 / 0.73x / 13.9x / 21.3x / 7.3% |
| `REIT - Hotel & Motel` | 11 / 1.10x / 24.7x / 11.0x / 7.4% | 1 | 1 |
| `REIT - Mortgage` | 20 / 0.61x / 6.5x / — / 7.9% | — | — |
| `Real Estate - Development` | 8 / 1.78x / 10.7x / 8.5x / 16.9% | 20 / 0.40x / 14.9x / 11.8x / 6.8% (NTM P/E 12.8x) | 9 / 0.59x / 11.1x / 21.9x / 3.4% |
| `Real Estate - Diversified` | 1 | 12 / 0.30x / — / 16.2x / 1.5% | 5 / 0.35x / — / 32.9x / 13.1% |
| `Real Estate - Services` | 19 / 1.83x / 40.2x / 14.9x / 5.0% (NTM P/E 15.7x) | 20 / 1.38x / 9.7x / 4.7x / 8.0% | 5 / 0.72x / 18.8x / — / 8.2% |
| `Residential Construction` | 18 / 1.18x / 13.7x / 11.9x / 5.6% (NTM 12.4x) | — | — |

The market's own statement, read off the store: US REITs trade at 1.0–2.4x book and 25–33x GAAP earnings
(depreciation-depressed, which is why the profile uses P/FFO); Hong Kong developers and landlords at
0.30–0.40x book and 12–15x earnings; Singapore REITs at 0.73–0.80x book and 14–16x; Singapore developers
at 0.59x; US homebuilders at 1.2x book and 12–14x earnings.

## 3. The architecture as it stands

**Profiles.** RealEstate: REIT (NAV (Cap Rates) .50 anchor, P/FFO .30, P/AFFO .15, DDM .05; statics 15x
EV/EBITDA, 14x P/E, 1.0x P/B) and S-REIT (DDM (S-REIT) .55 anchor, NAV (Cap Rates) .30, P/AFFO .15).
Property (SG only): Property Developer (SG) (NAV .55, DDM .25, P/E (norm) .20), Real Estate Agency (SG),
Specialised Accommodation (SG); Financials: Real Estate Asset Manager (SG) (P/E (norm) .40, SOTP
(published) .35, DDM .25). No profile for a Hong Kong or China developer, a Hong Kong landlord, a US
homebuilder or a US real-estate services firm.

**The REIT NAV.** `NAV = NOI / cap_rate − debt + cash`, NOI and FFO derived from FMP statements
(`_compute_reit_metrics`: FFO = net income + D&A, AFFO = FFO − normalised maintenance capex), the cap rate
and P/FFO / P/AFFO from `_REIT_SUBTYPE_MULTIPLES`, a 2026-04 table (BofA, Green Street) keyed by a
keyword classifier on the ticker and lookup notes: data centre 5.0% / 22x, premium data centre 4.5% / 23x,
industrial 5.5% / 18x, self-storage 5.5% / 19x, net lease 5.0% / 16x, healthcare 6.0% / 15x, retail 6.2% /
14x, office 7.5% / 12x, hospitality 8.0% / 11x, default 6.5% / 15x. A research `cap_rate_market` overrides
the table when deep research extracts one. There is no `nav` or `cap_rate` review-gated kind: the cap rate
is an owner constant nobody has re-derived since April, and the classifier is keyword routing that the
Wave 3 to 7 pattern replaced everywhere else with rows and pins.

**Developers on the REIT profile.** `Real Estate - Development` -> REIT is the row (not in scope), and
the ladder lands the same names on REIT or Mature SaaS. A developer's NOI is its development margin, so the
cap-rate NAV capitalises a sales profit as if it were rent; the market prices these names at 0.3–0.4x a
book that already carries revalued investment property, which is the RNAV-discount pattern the SG
Property Developer profile was built for and the HK names cannot reach.

**S-REITs** are the one calibrated corner: DDM off DPU with a published broker CoE / g table per
sub-sector (`_SREIT_DDM_CALIBRATION`, 6.3–8.5% / 0.5–2.75%) and per-ticker broker overrides. Wave 8 on
SGX is a measurement, not a rebuild, unless Stage 0 says otherwise.

## 4. What Stage 1 (universe, ◆ owner) has to settle

1. Keep or cut: DHI (a homebuilder under Consumer Cyclical; the wave would need a Homebuilder profile),
   CBRE and 9CI.SI (services and fund management, not property), IRM (records storage that converted to a
   REIT), China Vanke and Longfor (distressed China developers whose earnings are negative).
2. Rows for the eight REIT labels with no row, and whether the REIT profile's sub-type comes from the
   label (`REIT - Healthcare Facilities` -> healthcare) instead of the keyword classifier.
3. New profiles the labels need: Property Developer (HK / China) on RNAV with the market's discount,
   Landlord / Investment Property (HK) for Swire, Wharf, Hongkong Land-type rent collectors, Homebuilder
   (US) on P/B and cycle-normalised P/E, Real Estate Services (US) for CBRE-type brokerage.
4. Whether the cap-rate table is re-derived (Green Street / NCREIF, quarterly clock) or becomes a
   review-gated `nav` kind (published NAV or cap rate per name, the SOTP pattern the plan named).
