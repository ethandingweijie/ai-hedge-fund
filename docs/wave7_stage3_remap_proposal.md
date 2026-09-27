# Wave 7 (Technology and communications): architecture review and remap, PROPOSED

Derived from the comps store and FMP on 2026-09-27 and shown with the derivation. Nothing applied; the
owner accepts, re-weights or rejects at the ◆ checkpoint. Constants are proposed, never written, here.

## 1. What the review found (Stage 0 causes, in architecture terms)

| Component | As built | Effect measured | Proposal |
|---|---|---|---|
| `_TECH_SUBTYPE_MULTIPLES` (static 2024 table) | EV/EBITDA, EV/EBIT, P/S for every Tech-sector profile named in it, overriding the live cohort; also the terminal multiple every growth profile converges to (Mature SaaS 10x revenue) | Mature SaaS 22x vs live 17.0x (NTM 10.9x); Hyperscaler 20x vs 16.4x (NTM 14.6x); growth DCFs terminate at 10x revenue against a Software - Application cohort at 5.1x | Re-derive from the live baskets (section 3) as owner constants with a quarterly clock, or retire the override and let the cohort price with the table as the fallback only |
| Growth premium (`1 + s x (g − g_sec)/g_sec`, cap 0.60-1.80) | Ratio to the cohort's average growth | Saturates at 1.8x on any cohort growing 1-4%: ACN, NetEase, Kuaishou, Lenovo, BYD Electronic, iFAST | Absolute-spread form `1 + k x (g − g_sec)` with the cap at the tech-growth band (0.85-1.30) for every profile, or floor `g_sec` at 5%; k is an owner constant (section 4) |
| Ratio ladder, Tech branch | `revenue_base > 100e9` -> Hyperscaler, in the venue currency | Lenovo, NetEase, Kuaishou, BYD Electronic become hyperscalers | Compare `revenue_base_usd` (engine fix, no constant) |
| Semiconductor anchors | Trailing P/E on Fabless and Equipment / EDA; TXN on Equipment by the ladder | AMD −77%, AVGO −42%, ASML −27%, AMAT −39%, LRCX −40% | Forward P/E anchors on the NTM cohort (section 2); Analog / Mixed-signal IDM profile for TXN, ADI, NXPI, MCHP, ON |
| Memory / DRAM-NAND | P/E (norm) .45 on a five-year normalised NI | MU −73% against a consensus that has tripled the earnings base | Owner question: the normalisation window (energy precedent), or a super-cycle regime flag; the Forward P/E cross-check must use a forward multiple |
| Mature SaaS | EPV .30 anchor on GAAP EBIT | NOW −66%, ADBE +71% | Forward P/E .35 anchor on the live NTM cohort, DCF .30, EV/EBITDA .20, FCF Yield .15; EPV to cross-check |
| Stable Growth (telco) | Generic EV/EBITDA .35, DDM .25, DCF .25, EPV .15 | China Unicom +189%, China Telecom +114%, T +37%; TMUS −19%, VZ −13% | Telecom Carrier profile per market (section 2); SG keeps Telco / Infrastructure (SG) |
| No profile | `Entertainment`, `Electronic Gaming & Multimedia`, `Communication Equipment`, `Advertising Agencies`, `Information Technology Services` | NFLX, DIS, NetEase, CSCO, ANET, ACN, IBM by pin or ladder | Media & Streaming and Networking & Communication Equipment (new); IT Services and Ad / Consulting rows for the existing profiles; NetEase to China Internet Platform by HK market map |
| Balance-sheet financial gate | Conditional set keyed by profile | iFAST on Mature Platform (+1,118%) never reaches the Tier 2 customer-balance test | Bringing `Software - Application` into scope routes it to WealthTech & Specialty Financials (SG) by the SG market map |

## 2. Routing (Stage 2): rows, scope, market maps and pins

Fourteen labels enter `routing_scope`.

| FMP label | Row -> profile | Market maps | Pins |
|---|---|---|---|
| `Semiconductors` | Semiconductor / IDM / Foundry (default; INTC, SMIC, Hua Hong) | | Fabless: NVDA AVGO AMD QCOM MRVL ARM; Equipment / EDA: ASML AMAT LRCX KLAC TER; **Analog / Mixed-signal IDM (new)**: TXN ADI NXPI MCHP ON; Memory: MU WDC STX SNDK; SG keeps Tech Manufacturing / EMS (SG) for AWX |
| `Software - Infrastructure` | Tech / Mature SaaS | | Hyperscaler: MSFT ORCL; Growth SaaS: PLTR SNOW DDOG NET; Cybersecurity: CRWD PANW ZS |
| `Software - Application` | Tech / Mature SaaS | SG -> WealthTech & Specialty Financials (SG) (exists; iFAST) | 00020.HK SenseTime: owner cut or High-Growth Tech / AI |
| `Internet Content & Information` | Tech / Mature Platform | **HK -> China Internet Platform** (Baidu 09888.HK, Kuaishou 01024.HK join Tencent) | Hyperscaler: GOOG META |
| `Electronic Gaming & Multimedia` (new row) | Tech / **Media & Streaming (new)** (EA, TTWO) | **HK -> China Internet Platform** (NetEase 09999.HK) | |
| `Entertainment` (new row) | Tech / **Media & Streaming (new)** | | NFLX, DIS (pin moves off Travel & Dining), WBD |
| `Consumer Electronics` | Tech / Consumer Electronics / Hardware Ecosystem | | AAPL keeps Hyperscaler |
| `Computer Hardware`, `Hardware, Equipment & Parts` | Tech / Mature Platform (US) | **HK -> Consumer Electronics / Hardware Ecosystem** (Lenovo, Sunny Optical, BYD Electronic); SG keeps Tech Manufacturing / EMS (SG) | ANET -> Networking & Communication Equipment |
| `Communication Equipment` (new row) | Tech / **Networking & Communication Equipment (new)** | | CSCO ANET CIEN MSI JNPR |
| `Telecommunications Services` | Telco / **Telecom Carrier (new)** (replaces Stable Growth as the row) | HK -> Telecom Carrier with the HK cohort; SG keeps Telco / Infrastructure (SG) | T VZ TMUS CMCSA CHTR; 00941.HK 00728.HK 00762.HK |
| `Information Technology Services` (new row) | ProfessionalServices / IT Services (existing) | | ACN IBM CTSH INFY |
| `Advertising Agencies` (new row) | ProfessionalServices / Ad / Consulting (existing) | | |
| `Semiconductor Equipment & Materials` | Semiconductor / Equipment / EDA | | no members in the store today; the row is for the day FMP relabels |

Cuts for the owner: 00020.HK (SenseTime, loss-making, prices nothing), CJLU.SI (fibre trust, Wave 8
infrastructure), AIY.SI (a Financials platform; keep only as the routing check).

## 3. The static tech table, re-derived (owner constants, PROPOSED)

Baskets read from the comps store on 2026-09-27 (`regional_comps_members`, medians).

| Profile | Table today (EV/EBITDA / P/E / EV/Rev) | Basket | Trailing median | NTM median | Proposed |
|---|---|---|---|---|---|
| Hyperscaler / Tech Conglomerate | 20.0 / 28.0 / 7.5 | AAPL MSFT GOOG META AMZN ORCL | 16.4 / 25.3 / 9.2 | 14.6 / 22.6 | 16.4 / 25.3 / 9.2, forward legs on 14.6 / 22.6 |
| Mature SaaS | 22.0 / 28.0 / 10.0 | CRM ADBE NOW INTU WDAY ADSK | 17.0 / 24.6 / 5.1 | 10.9 / 15.3 | 17.0 / 24.6 / 5.1; the terminal convergence target for every growth profile moves from 10x to 5.1x revenue (the largest single effect in the wave) |
| Mature Platform | 18.0 / 24.0 / 6.5 | GOOG META BKNG UBER EBAY SPOT | 18.0 / 19.9 / 4.6 | 13.9 / 19.0 | 18.0 / 19.9 / 4.6 |
| Growth SaaS, Cybersecurity, Hyper-Growth, High-Growth AI | 45-65 / 65-100 / 22-30 | PLTR CRWD SNOW DDOG NET PANW ZS | 450 / 651 / 26.4 (trailing earnings near zero) | 74.1 / 87.7 | EV/NTM Revenue 26.4x as the only meaningful multiple; the earnings multiples stay cross-checks |
| Early Platform, Levered Subscription | 25 / 35 / 4; 12 / 18 / 4 | no names in this wave | | | unchanged, re-derive in Wave 8 |

Semiconductor profiles (live cohort, no static table): Fabless NTM P/E 31.6x, EV/NTM Revenue 22.0x;
Equipment 30.7x NTM; Analog IDM 25.7x NTM; Memory 14.2x NTM on a super-cycle EPS. Telecom carriers: US
5.9x EV/EBITDA, 9.2x NTM P/E, 11% FCF yield; HK 4.8x / 13.9x / 9%. Media & Streaming 13.9x NTM
EV/EBITDA, 21.7x NTM P/E. Networking 18.4x / 24.4x. IT Services 7.9x / 12.7x.

## 4. Method tables (PROPOSED)

| Profile | Anchor | Other legs | Derivation |
|---|---|---|---|
| Fabless | Forward P/E .35 | EV/NTM Revenue .20, DCF .25, EV/EBITDA .20 | the market pays for FY+1/FY+2 (AMD 40x trailing, 31.6x NTM) |
| Equipment / EDA | Forward P/E .35 | DCF .30, EV/EBITDA .20, FCF Yield .15 | ASML lands on its Forward P/E cross-check today |
| Analog / Mixed-signal IDM (new) | Forward P/E .35 | EV/EBITDA .25, DCF .25, FCF Yield .15 | TXN ADI: 25.7x NTM, mid-cycle margins |
| Memory / DRAM-NAND | P/E (norm) .45 (keep, cyclical) | EV/EBITDA .30, DCF .25 | owner question 3: normalisation window or a super-cycle flag; Forward P/E cross-check on the NTM cohort multiple, never the trailing |
| Mature SaaS | Forward P/E .35 | DCF .30, EV/EBITDA .20, FCF Yield .15; EPV and LBO Floor to cross-checks | NOW's EPV on GAAP EBIT was the anchor |
| Hyperscaler | EV/EBITDA .40 (keep) on the live basket | Forward P/E .25 (replaces trailing P/E), DCF .25, FCF Yield .10 | |
| Telecom Carrier (new) | EV/EBITDA .35 on the market's carrier cohort | FCF Yield .25, DDM .20, DCF .20; EPV dropped | capex-heavy: cash yield and payout, not EBIT capitalisation |
| Media & Streaming (new) | Forward P/E .35 | EV/EBITDA .30, DCF .25, FCF Yield .10 | NFLX DIS WBD TTWO EA: 21.7x NTM |
| Networking & Communication Equipment (new) | Forward P/E .35 | EV/EBITDA .25, DCF .25, FCF Yield .15 | CSCO ANET: 24.4x NTM |
| Growth SaaS, Cybersecurity | unchanged structure | terminal convergence to the re-derived Mature SaaS | the owner's CRWD rule stands; PLTR and CRWD stay far below spot by construction and are flagged, not chased |

Growth premium (owner constant): proposed `k` such that 10pp of growth above the cohort adds 25% to the
multiple, capped 0.85-1.30 for every profile (today 0.60-1.80 outside the high-multiple tech set). On
ACN it takes the premium from 1.50 to about 1.13; on NetEase from 1.80 to 1.16.

## 5. Questions for the owner (◆ before Stage 4)

1. Universe: cut 00020.HK, CJLU.SI, AIY.SI; keep DIS on Media & Streaming.
2. The static tech table: re-derive as in section 3 (owner constants, quarterly clock) or retire the
   override in favour of the live cohort with the table as fallback.
3. Memory: normalisation window or a super-cycle regime flag for MU; the Forward P/E cross-check on
   NTM multiples in every case.
4. The growth premium bound (section 4) as an owner constant.
5. New profiles: Telecom Carrier, Media & Streaming, Networking & Communication Equipment, Analog /
   Mixed-signal IDM; forward anchors on Fabless, Equipment / EDA and Mature SaaS.
6. HK internet: Baidu, Kuaishou and NetEase onto China Internet Platform by market map.
7. The ladder currency fix (engine defect, no constant): apply in Stage 4 regardless.
