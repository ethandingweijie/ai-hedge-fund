# Waves 6-8: the sectors still outside the closed loop

Inventory 2026-09-26, read-only: `industry_profile_map.json` v18 against the 151 FMP industry labels
the comps store carries for US, HKSE and SES (`regional_comps_members`, level=industry). The wave
methodology stays as before: Stage 0 baseline -> ◆ universe -> taxonomy -> methods derived from the
current market and shown -> peers -> Gemini pre-fill where a review-gated input is the only honest
source -> scorecard -> ship. Nothing here is built; this is the ◆ checkpoint on order and scope.

## 1. Where coverage stands

| State | Labels | What it means |
|---|---|---|
| In `routing_scope` (Waves 1-5) | 29 | Row measured against the closed loop; oil & gas, power, A&D, staples, health |
| Row exists, outside scope | 56 | Legacy row routes the name, but the profile was never measured on a wave and several targets are suspect (below) |
| No row at all | 68 | Name falls to the ratio ladder; the misroutes Waves 4 and 5 found came from this state |

Profiles exist in numbers for Financials (24), Tech (15), Industrials (14), Consumer (16), but a profile
that exists is not a profile that has been measured: Stage 0 of every wave so far found the routing,
not the method table, was the miss.

## 2. Candidate waves, sized from the comps store

| Wave | Scope | Labels | Members US / HKSE / SES | Profiles today | Labels with no row |
|---|---|---|---|---|---|
| 6 | Financials: banks, insurance, asset managers, exchanges, credit, conglomerates | 17 | 231 / 133 / 14 | 24 | Credit Services, Insurance Brokers/Specialty/Reinsurance, Financial Conglomerates, Investment Banking, Mortgages |
| 7 | Technology and communications: software, internet, hardware, semis, comms equipment, gaming, media, telco, IT services, advertising | 16 | 247 / 185 / 14 | 15 Tech + 5 Semi + 2 Telco + 3 ProfServices | Communication Equipment, Electronic Gaming & Multimedia, Entertainment, IT Services, Advertising Agencies, Software - Services, Healthcare Information Services |
| 8 | Real estate: nine REIT labels, developers, diversified, services, homebuilders | 13 | 192 / 63 / 46 | REIT, S-REIT, Property Developer (SG), Real Estate Agency (SG) | 8 of 9 REIT labels, Residential Construction |
| 9 | Industrials, materials, metals, transport | 28 | 383 / 249 / 19 | Capital Goods, Backlog-Gated Long Cycle, Steel / Metals, Specialty Chemicals, Mining (Major), Airlines, Rail / Logistics | Steel, Chemicals, Construction Materials, Industrial Materials, Distribution, Waste, Rental & Leasing, Packaging, Paper, Silver, Trucking, Infrastructure Operations |
| 10 | Consumer discretionary remainder, education, personal services, the rest of utilities, staffing and consulting | 25 | 331 / 287 / 17 | 16 Consumer (built pre-wave), Regulated Utility, IPP | Auto Dealerships, Department Stores, Education & Training, Personal Products, Regulated Water, Diversified Utilities, Staffing, Consulting |

## 3. Proposed order and why

**Wave 6 Financials.** The largest weight in both Asian markets (HSI and STI are bank- and
insurer-heavy: 1398, 0005, 3988, 0939, 2318, 1299, D05, O39, U11) and the sector whose legacy rows are
most wrong for the names they serve. Twenty-four profiles exist but none has been through a wave, and
the Financials engine paths (P/BV vs ROE, DDM at Ke, embedded value for life insurers) are the ones the
remediation touched most.

**Wave 7 Technology and communications.** The largest US weight and the HK platform names beyond the
China Internet Platform profile (Xiaomi 1810, SMIC 0981, China Mobile 0941, NetEase 9999, Baidu 9888,
Lenovo 0992). Seven labels have no row; the semis rows exist but `Semiconductor Equipment & Materials`
has no members in the store, so ASML/AMAT/LRCX route by pin or ladder.

**Wave 8 Real estate.** Eight of nine REIT labels have no row and SES is REIT-heavy (46 members: CICT,
Ascendas, Mapletree, Keppel DC, Frasers). The two rows that exist misroute: `Real Estate - Development`
-> REIT prices Sun Hung Kai 0016, CK Asset 1113, Henderson 0012, Longfor 0960 and Vanke 2202 as REITs.
NAV / cap-rate inputs are the natural review-gated pre-fill (the SOTP pattern).

Waves 9 and 10 follow; the owner may prefer Industrials before Real Estate on US weight (CAT, DE, ETN,
UNP, LIN) or because Backlog-Gated Long Cycle already has the backlog pre-fill. The sizing table is the
argument either way.

## 4. Legacy rows outside scope whose target looks wrong (to test at each Stage 0, not to fix now)

| Row | Routes to | Why it is suspect |
|---|---|---|
| `Banks - Regional` | EM Bank | US regionals (and O39/U11 on SES) on an EM bank profile while Regional Bank and Super-Regional Bank exist |
| `Insurance - Life` | Insurance | Life insurers (AIA 1299, Ping An 2318, China Life 2628, Prudential) price on embedded value and VNB, not P/E |
| `Real Estate - Development`, `Real Estate - Diversified` | REIT | Developers and landlords priced as REITs; only SG has a developer profile |
| `Real Estate - Services` | Ad / Consulting | CBRE, 9CI (CapitaLand Investment) on an advertising profile |
| `Internet Content & Information` | Mature Platform | Tencent 0700, Baidu 9888, GOOG, META on one mature-platform table |
| `Consumer Electronics` | Hyperscaler / Tech Conglomerate | AAPL fits; Xiaomi 1810 and Sony do not |
| `Telecommunications Services` | Stable Growth | China Mobile 0941, Singtel Z74, VZ, T: a generic table where Telco / Infrastructure (SG) exists for SG only |
| `Renewable Utilities` | IPP | Contracted renewables on a merchant-adjacent table (Wave 2 kept the row for HK IPPs) |
| `Specialty Business Services` | IT Services | Catch-all label; CTAS and CPRT are not IT services |
| `Auto - Parts`, `Leisure` | Consumer Durables, Consumer Growth | Ladder-era rows never measured |

## 5. Probe universes to confirm exact FMP labels (Stage 2 probe, read-only)

Largest members by market cap in the store; the probe fixes the exact label strings a row may be
written from, as `docs/waves_2_5_stage2_probe.md` did.

- **W6** US: BRK-B JPM V MA BAC MS GS WFC C AXP; HK: 1398 0005 3988 0939 1288 2628 3968 2318 1299 3328 2388 2888; SG: D05 O39 U11 S68 BN4 G07 J36.
- **W7** US: NVDA AAPL GOOG MSFT META AVGO MU AMD ASML INTC ORCL PLTR; HK: 0700 0941 1810 0981 9999 0728 3986 0992 9888 0762; SG: Z74 V03 CJLU AWX AIY.
- **W8** US: WELL PLD EQIX AMT DLR SPG PSA O VTR CBRE DHI IRM; HK: 0016 1109 1113 1972 0688 0012 0823 0083 0004 2202 0960; SG: H78 C38U 9CI A17U C09 U14 N2IU M44U ME8U AJBU U06.

## 6. What the owner decides at this checkpoint

1. The order: 6 Financials, 7 Tech and communications, 8 Real estate as proposed, or Industrials ahead.
2. Whether Wave 6 splits banks from insurance (two Stage 0 tables, one wave) given embedded-value
   inputs for life insurers would be a new review-gated kind.
3. Whether the suspect legacy rows in section 4 are re-measured inside their wave (proposed) or left.
