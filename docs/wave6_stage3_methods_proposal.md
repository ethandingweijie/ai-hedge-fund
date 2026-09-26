# Wave 6 (Financials): taxonomy, routing and methods, PROPOSED

Everything below is derived from the comps store and FMP on 2026-09-26 and shown with its derivation.
Nothing is applied. The owner accepts, re-weights or rejects at the ◆ checkpoint; cost-of-equity rows
are owner constants and are proposed, never written, here.

## 1. Routing (Stage 2): rows, scope and pins

Thirteen labels enter `routing_scope`. Rows written from the exact FMP strings in the probe.

| FMP label | Row -> profile | Change | Names |
|---|---|---|---|
| `Banks - Diversified` | Financials / Money Center Bank | keep | JPM BAC WFC C USB; 00005.HK 02888.HK; the China big four and BoCom keep their EM Bank pins |
| `Banks - Regional` | Financials / **Super-Regional Bank** (was EM Bank) | **re-target** | PNC TFC by row; 03968.HK 01658.HK keep EM Bank pins; 02388.HK 00011.HK keep Regional Bank pins; O39.SI U11.SI keep Money Center Bank (SG) pins |
| `Banks` (SG string) | Financials / Money Center Bank (SG) | **new row**, SG market map | D05.SI |
| `Financial - Capital Markets` | Financials / Brokerage | keep | SCHW; GS MS keep Investment Bank pins |
| `Financial - Credit Services` | Financials / **Card Issuer & Consumer Lender** (new) | **new row + profile** | AXP COF SYF; V MA keep Payment Networks pins |
| `Insurance - Diversified` | Financials / Insurance (P&C) | **re-target** (was Insurance) | AIG; BRK-B -> Holding Company by pin; 02318.HK -> Insurance (life) by pin |
| `Insurance - Property & Casualty` | Financials / Insurance (P&C) | keep; **PGR, CB pins corrected** | PGR CB TRV |
| `Insurance - Life` | Financials / Insurance | keep; **empty pins filled** | MET; 02628.HK 01299.HK 00945.HK G07.SI |
| `Insurance - Brokers` | Financials / **Insurance Broker** (new) | **new row + profile** | MMC AON |
| `Asset Management` | Financials / Asset Manager | keep | BLK; BX KKR APO keep Alt Asset Manager pins |
| `Financial - Data & Stock Exchanges` | Financials / Market Infrastructure | keep; SPGI MSCI pinned **Financial Data & Ratings** (new) | ICE CME 00388.HK; S68.SI keeps Market Infrastructure (SG) |
| `Conglomerates` | Financials / Holding Company | keep the row; J36.SI BN4.SI keep their SG pins | owner: cut or keep in Wave 6 |

Peer-basis notes that follow the market registry (each market owns its multiples): the HKSE
`Insurance - Life` cohort (n=8, 7.3x / 1.55x) carries AIA, China Life, Ping An; SES has no industry
cohort for any Financials label, so SGX and Great Eastern price on the SES sector rung unless the owner
allows the global exchange basket for a one-exchange market (question 5).

## 2. Method tables, derived from the current market

Cohort medians 2026-09-26 (US unless stated): `Banks - Diversified` 14.2x P/E, 1.78x P/B; `Banks -
Regional` 11.6x, 1.31x; `Financial - Capital Markets` 15.3x, 3.20x; `Financial - Credit Services` 12.6x,
1.99x; `Insurance - P&C` 11.4x, 2.18x; `Insurance - Life` 11.8x, 1.38x; `Insurance - Brokers` 25.9x,
4.75x; `Asset Management` 27.0x, 2.65x; `Financial - Data & Stock Exchanges` 22.1x, 3.89x. HKSE:
`Banks - Diversified` 8.3x, 0.79x; `Banks - Regional` 6.4x, 0.46x; `Insurance - Life` 7.3x, 1.55x.

### 2a. Banks (Money Center, Super-Regional, Regional, EM, Investment Bank, SG): keep the legs, re-derive the constants

The GGM .35 / Residual Income .30 / P/TBV .20 / P/E (norm) .10 / Excess Capital .05 structure is the
owner's framework and stays. What Stage 0 shows is stale constants, so the proposal is a re-derivation
of `_BANK_PROFILE_CALIBRATION` from the cohorts, shown as a table for the owner to set:

| Profile | Today p_tbv / pe / CoE / target RoTE / g | Market-implied 2026-09-26 | Derivation |
|---|---|---|---|
| Money Center Bank | 1.4 / 12.0 / 10.0% / 12% / 3.0% | p_tbv **2.2**, pe **14.2**, CoE **9.3%** | US Banks - Diversified P/B 1.78 on book; on tangible book ~2.2x (JPM 3.2x, BAC 1.9x, WFC 1.9x, C 1.0x). CoE from GGM inversion on JPM: g + (RoTE − g)/(P/TBV) = 3% + 16.5% / 2.6 = 9.3% (broker basis 10.0%) |
| Super-Regional Bank | 1.3 / 11.0 / 9.5% / 11% / 2.5% | p_tbv **1.6**, pe **11.6**, CoE 9.5% (keep) | US Banks - Regional P/B 1.31 on book, ~1.6x tangible (PNC 1.7x, TFC 1.3x, USB 1.9x) |
| Investment Bank | 1.2 / 10.0 / 11.0% / 13% / 3.0% | p_tbv **2.4**, pe **15.3**, CoE **9.5%** | Capital Markets P/B 3.20 on book (GS 2.5x TBV, MS 3.0x TBV); inversion on GS: 3% + (13.8% − 3%) / 2.5 = 7.3%, on MS with 18% RoTE: 3% + 15% / 3.0 = 8.0%; 9.5% keeps a spread over the money-center rate |
| EM Bank (China big four, BoCom, PSBC) | 1.2 / 9.0 / 13.0% / 14% / 4.0% | p_tbv **0.6**, pe **6.4-8.3**, CoE **9.5-10.0%** (zero country premium, owner 2026-09-26), target RoTE **10-11%** | HKSE Banks - Diversified 0.79x / 8.3x, Banks - Regional 0.46x / 6.4x; realised RoTE ICBC 9.0%, ABC 10.4%, BoCom 8.6% (2026-06-30 books); inversion on ICBC at 0.57x TBV: 4% + (9% − 4%) / 0.57 = 12.8% is the market's own CoE and is what the zero-premium decision overrides; flag, do not chase |
| EM Bank (Premium) (CMB) | 2.0 / 14.0 / 13.0% / 16% / 5.0% | p_tbv **1.0**, pe **7-9**, CoE 9.5-10.0% | CMB 1.0x book, RoTE 14%; CMB landed at +18% on the current table |
| Money Center Bank (SG) | 2.0 / 13.0 / 8.8% / 14.5% / 3.1% | p_tbv **1.9**, pe **12.3**, keep CoE 8.6-9.1% (broker tables in `_BANK_GGM_OVERRIDES`) | DBS 2.1x book, OCBC 1.3x, UOB 1.2x; SES sector rung 12.3x |
| Money Center Bank (EU) (HSBC) | 0.8 / 8.0 / 11.0% / 10% / 2.0% | p_tbv **1.3**, pe **10**, CoE **9.5%**, target RoTE **15%** | HSBC 1.3x TBV, RoTE 15.6% 1H26; the 2023 "EU bank at 0.8x" world is gone |

Also to inspect at Stage 4 before any constant moves: the `P/E (norm)` leg's normalised net income on
the China banks (ICBC 5-year RMB 977bn against RMB 420bn TTM) reads like a units or line-item artifact.

### 2b. Insurance: split life from P&C properly, give life its input

**Insurance (P&C)** (PGR CB TRV AIG): keep GGM (P/B) .40 anchor, Combined Ratio Gate .25, P/E (ops) .20,
DDM .15. Market: 2.18x book, 11.4x. The Combined Ratio Gate never computed on any name at Stage 0
(extractor field absent); proposal: the gate reads the combined ratio from the cited review-gated input
below or stands down with its weight rolled into P/E (ops), the Commercial Biotech pattern.

**Insurance (life)** (MET, AIA, China Life, Ping An, Great Eastern, Manulife): Embedded Value .35 anchor
never computed at Stage 0. Proposal (owner question 2): a review-gated kind `embedded_value` (Gemini
pre-fill, cited: EV per share, VNB, VNB margin, fiscal period, statement currency) feeding
`embedded_value_per_share`; until accepted the leg is quarantined and the blend renormalises around P/BV
.30, P/E (ops) .15, DDM .05, Combined Ratio Gate .15 (which is also None on a life insurer: proposal
removes it from the life profile). HKSE `Insurance - Life` cohort 7.3x / 1.55x is the basis for the HK
names; MET on the US cohort 11.8x / 1.38x.

**Holding Company** for BRK-B (owner question): SOTP / NAV .70 proxies to P/BV today; Berkshire at 1.6x
book. A cited look-through (equity portfolio at market + operating businesses at segment multiples) is
the Aerospace Holdco pattern and is a Stage 5 pre-fill if the owner keeps BRK-B in the wave.

### 2c. New profiles (PROPOSED)

| Profile | Names | Methods | Derivation |
|---|---|---|---|
| **Card Issuer & Consumer Lender** (Financials) | AXP COF SYF | P/TBV .35 anchor, P/E (norm) .30, GGM (P/B) .25, Excess Capital .10; excluded DCF, EV/EBITDA, EV/Revenue | `Financial - Credit Services` 12.6x / 1.99x; AXP 6x book at 33% RoTE, COF 1.2x, SYF 1.6x: a RoTE-driven P/TBV with a through-cycle P/E (credit losses normalise) and no cash-flow leg, since FCF on a lender is loan growth with the sign flipped (COF DCF 795/share at Stage 0) |
| **Insurance Broker** (Financials) | MMC AON | Forward P/E .35 anchor, EV/EBITDA .30, DCF .25, FCF Yield .10; excluded P/BV, GGM, Embedded Value | `Insurance - Brokers` 25.9x / 4.75x: fee businesses with no underwriting balance sheet; the Payment Networks structure with the anchor on forward earnings |
| **Financial Data & Ratings** (Financials) | SPGI MSCI (MCO, FDS if kept) | Forward P/E .35 anchor, EV/EBITDA .25, DCF .25, FCF Yield .15 | `Financial - Data & Stock Exchanges` 22.1x / 3.89x but the ratings and index names trade 30-35x; a sub-basket of the four (SPGI MSCI MCO FDS) is the statics source; distinct from exchanges (clearing float, transaction volumes) |
| **Alt Asset Manager** (existing, re-specified) | BX KKR APO | **P/DE (Forward) .40 anchor**, SOTP (FRE + carry) .30, Forward P/E .20, DDM .10 | All four current legs are non-implementable proxies (EPV, P/E (norm), EV/Revenue) and priced BX at 44 against 118. Distributable earnings and fee-related earnings are disclosed every quarter and citable: proposal is a review-gated kind `alt_manager` (FRE, DE, net accrued carry, fee-paying AUM, cited) feeding P/DE and the FRE + carry SOTP; the market pays 20-25x DE (BX 25x, KKR 20x, APO 17x on 2026-09-26 quotes against FY26 consensus DE) |

### 2d. Market Infrastructure (SG)

SGX (S68.SI) at −49%: `P/E (norm)` .45 anchor reads the SES sector 9.8x for an exchange the market prices
at 25x. Owner question 5: allow the global exchange basket (US `Financial - Data & Stock Exchanges`
22.1x; HKEX 00388.HK 35x) as the statics for a one-exchange market, or keep the registry rule and accept
the miss.

## 3. Cost of equity (owner constants, PROPOSED)

The owner's `cost_of_equity` table has one row (Regulated Utility). The bank block reads
`_BANK_PROFILE_CALIBRATION` and the three broker overrides (D05.SI, O39.SI, JPM). Proposal: move the
bank CoE into the owner table, keyed by market, with the market-implied derivations above as the
proposed values: US money center 9.3-10.0%, US super-regional 9.5%, US investment bank 9.5%, SG 8.6-9.1%,
HK/China 9.5-10.0% with zero country premium (the market-implied 12.8% is recorded as the flag band's
upper edge, never applied), EU 9.5%. Insurance P&C 9.0%, life 9.5% (US), 10.0% (HK) from CAPM at
current 10-year yields, betas 0.8-1.1, ERP 5%: proposed, to be set.

## 4. Review-gated inputs this wave needs (Stage 5)

`embedded_value` (life insurers), `alt_manager` (FRE/DE/carry), `combined_ratio` (P&C) or a single
`insurance_kpis` kind carrying both; `lookthrough` for BRK-B if kept. Each follows the pipeline pattern:
cited figures with period labels, hard checks on the gate, quarantined until accepted, blend re-weights
to 1.0 meanwhile.

## 5. Questions for the owner (◆ before Stage 4)

1. Universe: cut or keep J36.SI, BN4.SI (Wave 9 conglomerates), SPGI, MSCI (new profile or Wave 7 data),
   00945.HK (Manulife HK line).
2. Life insurers: build the `embedded_value` kind (recommended) or strike the Embedded Value leg.
3. Banks: accept the re-derived calibration table in 2a as PROPOSED constants, and the zero-premium China
   CoE (the market's own implied 12.8% is flagged, not applied).
4. New profiles: Card Issuer & Consumer Lender, Insurance Broker, Financial Data & Ratings; Alt Asset
   Manager re-specified on distributable earnings with its own input kind.
5. SGX: global exchange basket for a one-exchange market, or keep the registry rule.
6. BRK-B: Holding Company with a look-through pre-fill, or Insurance (P&C) by row.
