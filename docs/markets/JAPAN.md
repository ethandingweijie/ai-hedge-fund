# Japan (Tokyo Stock Exchange, `.T`): market support

Status 2026-11-28 (owner decisions applied the same day; see "Owner decisions applied"). Branch `worktree-agent-ace83d5112ed4975c`, based on local `main` 62feab5d. The worktree was
first created from upstream `origin/main` (virattt). I reset it to the owner's `main` before starting. Nothing is
pushed and production was not touched: `DATABASE_URL` was unset, and runs used a temp sqlite seeded with the
2026-11-28 production `regional_comps` snapshot (JPX: 2,278 rows, refreshed 0.3 days before).

Japan logic is in `src/markets/japan.py` (tickers, fiscal year, research context, yen label, ADR map) and
`src/markets/japan_filings.py` (TDnet / EDINET / ADR 20-F resolver). Shared files get one-line branches; they are
all listed at the end. PDF CJK text goes through `src/utils/cjk_font.py`, the module shared with the Korea and
China branches (byte-identical copy of the Korea branch's file).

## What works (checklist)

| # | Item | Status |
|---|---|---|
| 1 | Tickers | `7203.T`, `130A.T`, `TYO:7203`, `TSE:7203`, `JP:7203`, `7203.JP` and `7203.TYO` all canonicalise to `7203.T` (`canonical_ticker`). A bare `7203` stays Hong Kong (`07203.HK`), which keeps the existing contract. The request guard accepts only the Tokyo venue prefixes, so `ABC:XYZ` is still rejected. The FMP symbol is the canonical form. Landing search enables `.T` suggestions (`suggestionMarket` -> `'JP'`). Market questions keep `.T` tickers. Portfolio rows are canonical and carry JPY plus a USD rate. |
| 2 | Prices / currency | Listing currency is JPY (venue default plus FMP profile). Reporting currency is also JPY, so the FX step is identity. The FMP quote and EOD history are covered (5,000-row cap: from 2006-06 for 7203/8306/9984; 130A.T from its 2024 IPO). Market cap is in JPY. JPY prints as **JP¥** everywhere: web (`currencySymbol`, statements, SOTP panel, screener), PDF, Excel and engine flags. A bare `¥` in a Tokyo PDF narrative is rewritten to `JP¥`. JPY has no minor unit, but prices print with 2 decimals as for other markets (cosmetic). |
| 3 | Fundamentals | FMP covers Japan in full: annual and quarterly statements, TTM key metrics, analyst estimates (7203.T: 21 analysts FY3/28), earnings calendar (actual + estimate), grades, and growth endpoints (35 fields, unlike HK). `price-target-consensus` and `news/stock` are empty. The consensus target comes from stockanalysis.com, which now parses `¥` (7203: JP¥3,835 from 19 analysts; 8306: JP¥3,859 from 10; 9984: JP¥6,667 from 18). A new listing (130A.T) has quotes and history but no statements, so it falls to the generic no-data path. |
| 3b | March fiscal year | FMP labels a year by its end date (year to 2026-03-31 = `fiscalYear` 2026). The engine's `FY` + end-year labels follow it, and FY+1 is the first estimate period after the run date (2027-03-31). NTM weighting is correct: on 2026-11-28, 66% of FY3/27 has elapsed. The research prompt states the convention and the MUFG/Sony "start-year" naming trap. Every Tokyo report adds a fiscal-year naming line to its data limitations. The live 7203.T run used FY2027 = year to March 2027 consistently. |
| 4 | Valuation | JPX comps are live and keyed `JPX` (113 industry keys, 11 sector keys, fresh). Tokyo names discount on the **10y JGB 2.70%** with a **3.0pp** minimum Ke/WACC spread and a **0.6% JPY country premium**; tax falls back to Japan's statutory 29.74%. Utility CoE rows are keyed `JPX` (no US borrowing). Megabanks run on their own **Money Center Bank (JP)** calibration. Long-term investments (cross-shareholdings) are netted into the equity bridge by the existing E18 rule, as for AAPL. All owner-accepted 2026-11-28. |
| 5 | Rating benchmark | **TOPIX** (owner-accepted). Hurdle = the run's own rf + ERP + CRP: 2.70% + 4.41% + 0.60% = **7.71%** (it was 9.35% on US rates). |
| 6 | Research / filings | The Qwen prompt gains a Japan context block: Japanese-language search terms (決算短信, 業績予想, 有価証券報告書), TDnet/EDINET/JPX sources, the fiscal-year rule, accounting basis, 百万円/億円 units, cross-shareholdings, and "write JP¥". Filings resolver: (a) the ADR 20-F on EDGAR when its accounting basis matches the Tokyo filing (Toyota, Sony, Honda, Takeda, Nomura, ORIX); (b) otherwise the latest TDnet 決算短信 PDF, scanned only on FMP's earnings days. Measured: 8306.T found in 2.0s (13 Nov 2026, J-GAAP); 9984.T in 1.9s (10 Nov 2026, IFRS). (c) EDINET 有価証券報告書 is implemented but needs `EDINET_API_KEY` (free registration). It was not verified live: there is no key in `.env.local`. |
| 7 | Freshness / earnings | The earnings trigger already uses the FMP earnings calendar, which covers Japan (7203.T next on 2027-02-10). The freshness web search adds the TSE code plus Japanese sources. TDnet `業績予想の修正` (forecast revisions) are classified but not yet a trigger (open item). |
| 8 | Reports | **PDF**: Helvetica cannot draw CJK. `cjk_font.cjk_markup` (shared module) wraps CJK runs in a CJK font; the web and worker Docker images now install TrueType CJK fonts (`fonts-ipafont-gothic`, `fonts-nanum`, `fonts-wqy-zenhei`) so production PDFs embed them. Earlier text: It embeds a TrueType font when one exists (Windows MS Gothic; Linux IPA Gothic / Droid Sans Fallback) and otherwise uses ReportLab's CID font (HeiseiKakuGo-W5), which is not embedded. The live 7203.T PDF embeds `MS-Gothic` (60 KB, 16 segments pass the truncation check). **Excel**: 53 cells name JPY/JP¥. One cell (the backtest note) prints `$`; that is a cross-market defect, listed under risks. |
| 9 | Billing / coverage | 34 Tokyo names are now **tuned** (owner-accepted pins; `run_type` = `new`, 2 credits, for a first run); every other Tokyo name, and SoftBank Group, stays `limited`. A stored research run makes any of them `covered`. |
| 10 | Regulatory | 10 licensing questions are added to `docs/LAUNCH_TODO.md` (FIEA 投資助言・代理業, offshore registration, advertising, pre-contract documents, JSDA, TDnet/EDINET reuse, JPX data licence, APPI, JCT). They are questions only, with no conclusions. |
| 11 | Tests | `tests/test_japan_market.py`: 64 offline tests (every rule above, including the accepted decisions). After the owner decisions: full suite **7,250 passed, 80 skipped, 4 xfailed**, `set -o pipefail` (first pass: 7,177 before the decisions). Census tests that count profiles moved 148 -> 149 (+Money Center Bank (JP)); golden snapshots re-pinned with a reason: only `param_version` moved (valuation_constants changed; no golden ticker is Japanese). Frontend `tsc --noEmit` clean. |

Out of scope, documented rather than built:
- Portfolio crisis replay / what-if has no JP region (no calibrated TOPIX/N225 event baskets), so Tokyo holdings anchor to US sector rows, as an unknown market always has.
- The screener has no Japan universe.
- TDnet forecast revisions are not a freshness trigger.
- EDINET XBRL parsing.
- Japanese-language UI.

## Verification

Engine-only valuations (no LLM, FMP + temp store) ran for 14 large caps, with the JGB proposal off (production
behaviour) and on (`RF_APPLY_PROPOSED=JP`). Real local runs (Qwen research + full pipeline + product PDF/Excel)
were made for 3 tickers, the agreed maximum.

**Real local runs** (Qwen qwen3.8-flash research + full pipeline; production behaviour, i.e. JGB proposal NOT applied; product PDF and workbook rebuilt through `workbook_export_service`):

| Ticker | Run | Profile | Spot | Base IV (bear / bull) | 12m target | Rating | Street (stockanalysis, n) | Filing source resolved | PDF / Excel |
|---|---|---|---|---|---|---|---|---|---|
| 7203.T Toyota | 655 s | Automotive (OEM) | JP¥3,384 | 3,198 (2,608 / 3,788) | 3,679 | Neutral (TSR 11.5% vs TOPIX hurdle 9.35%) | 3,835 (19) | ADR 20-F (TM, IFRS, filed 2026-06-10) | 60 KB, MS-Gothic embedded / 53 JPY cells |
| 8306.T MUFG | 578 s | Money Center Bank | JP¥3,192 | 2,597 (2,091 / 3,137) | 3,136 | Underweight (TSR 1.0%, -834 bp) | 3,859 (10) | TDnet 決算短信 2026-11-13 (J-GAAP) | 66 KB, MS-Gothic / 52 JPY cells |
| 9984.T SoftBank Group | 546 s | Telecom Carrier (wrong archetype) | JP¥2,984.5 | **14,265** (9,801 / 17,115) | 9,359 | Neutral | 6,667 (18) | TDnet 決算短信 2026-11-10 (IFRS) | 69 KB, MS-Gothic / 44 JPY cells |

What the runs showed:
- Research used Japanese primary sources (決算短信, TDnet, Kabutan) and quoted company forecasts with dates (Toyota FY2027 revenue forecast JP¥53.5tn, 5 Nov 2026; MUFG net income forecast raised to JP¥2,800bn, 13 Nov 2026).
- Toyota's run labelled fiscal years by end year throughout. **MUFG's run kept MUFG's own start-year label** ("FY2026 (year ending 2027-03)"): the year end is stated, but the label contradicts the engine's convention -- see Risks.
- No "Default decision due to LLM failure"; the macro classifier fell back to its default on 7203.T (pre-existing, not Japan-specific).
- The narrative still contains bare `¥` (~300-400 per payload, LLM text) even with the JP¥ instruction; the PDF rewrites them to JP¥, the web view shows them as written.
- **SoftBank Group's IV of 14,265 (4.8x spot) is not publishable**: valued as a telecom carrier, the EV/EBITDA leg capitalises consolidated SoftBank Corp. / Arm EBITDA and the 2-stage DCF runs on consolidated cash flows, then the research-fed run moved it further from the engine-only 5,181. This is the item-7 holdco problem, measured.

**Engine-only** (no LLM; 14 large caps; WACC and base IV with the JGB proposal off vs on):

| Ticker | Profile | Spot | WACC (UST) | Base IV (UST) | WACC (JGB, proposed) | Base IV (JGB) | Change |
|---|---|---|---|---|---|---|---|
| 7203.T | Automotive (OEM) | 3,384 | 9.20% | 3,198 | 7.42% | 3,198 | +0.0% |
| 6758.T | Consumer Electronics / Hardware Ecosystem | 4,413 | 9.77% | 4,275 | 7.53% | 4,570 | +6.9% |
| 8306.T | Money Center Bank | 3,192 | 5.25% | 2,850 | 5.25% | 2,838 | -0.4% |
| 6861.T | Mature Platform | 14,845 | 10.68% | 9,585 | 8.44% | 10,984 | +14.6% |
| 9984.T | Telecom Carrier | 2,984 | 5.94% | 5,181 | 5.70% | 5,257 | +1.5% |
| 9983.T | Traditional Retail | 85,330 | 8.05% | 38,416 | 5.81% | 38,416 | +0.0% |
| 8035.T | IDM / Foundry | 8,220 | 11.10% | 2,849 | 8.86% | 3,039 | +6.7% |
| 8058.T | Blended Industrial OpCo | 5,155 | 7.41% | 7,974 | 5.70% | 8,082 | +1.4% |
| 9432.T | Telecom Carrier | 169 | 5.94% | 249 | 5.70% | 250 | +0.3% |
| 4063.T | Commodity Chemicals & Ag Inputs | 5,640 | 8.66% | 3,661 | 6.42% | 3,661 | +0.0% |
| 7974.T | Media & Streaming | 9,034 | 8.29% | 8,382 | 6.05% | 8,743 | +4.3% |
| 8766.T | Insurance (P&C) | 594 | 6.25% | 501 | 6.25% | 496 | -0.8% |
| 4502.T | Specialty & Generic Pharma | 7,341 | 8.71% | 3,728 | 6.64% | 3,728 | +0.0% |
| 6501.T | Blended Industrial OpCo | 5,168 | 8.40% | 6,132 | 6.16% | 7,038 | +14.8% |

Reading the table: the JGB proposal lowers WACC by ~2.2pp wherever the firm CAPM sets it, and moves IV only through the DCF legs (Keyence +14.6%, Hitachi +14.8%, Sony +6.9%, Tokyo Electron +6.7%, Nintendo +4.3%); multiples-only profiles (Toyota, Fast Retailing, Shin-Etsu, Takeda) do not move. Banks and insurers are on the bank/insurer CoE path and barely move (MUFG -0.4%). The table also shows the routing defects the pins address: Tokyo Electron on `IDM / Foundry` (IV 2,849 vs spot 8,220), Takeda on Specialty & Generic (3,728 vs 7,341), Keyence on `Mature Platform`.

The local US rf is the Damodaran fallback (4.94%) because there is no FRED key locally. Production uses live
DGS10, so the "UST" columns overstate production's rf slightly.

## Owner decisions applied (2026-11-28)

| # | Decision | Where it lives | What it does |
|---|---|---|---|
| 1 | rf = 10y JGB **2.70%** (MoF close 2026-11-26) with a **3.0pp** min spread | `valuation_constants.risk_free_by_market.markets.JP` (status removed, `accepted: owner, 2026-11-28`) | `capm.risk_free("JP")` returns it as owner-set; the firm CAPM, market CAPM, bank g cap and Kd rebasing all read it |
| 2 | Benchmark **TOPIX** | `ratings._BENCHMARKS[("JPX", False)]` | hurdle 7.71% on the run's own CAPM (rf 2.70 + ERP 4.41 + CRP 0.60) |
| 3 | Megabanks on **Money Center Bank (JP)**: P/TBV 1.7x, CoE 8.8% | `dcf_agent._BANK_PROFILE_CALIBRATION`, `INDUSTRY_VALUATION_PROFILES.Financials`, `capm.FINANCIAL_PROFILES`, `CROSS_MARKET_BASKETS` (peer set = the three megabanks), KPI prompt / framework, report family | P/E 13.0x = megabank trailing median (MUFG 13.04 / SMFG 14.06 / Mizuho 12.93); target ROE 12% (TTM 12.4 / 10.4 / 12.9%); ggm_g 2.7% = the JGB cap; CET1 / RWA density / terminal spread / fade years carried from the US row. **US rows untouched** (tested). |
| 4 | **34 pins** accepted (all but SoftBank Group) | `industry_profile_map.json` v26 `ticker_overrides` + `routing_scope_tickers` | names become tuned; megabanks -> Money Center Bank (JP); Tokyo Electron / Advantest -> Equipment / EDA; Keyence -> Capital Goods; Murata -> Electronic Materials; Takeda -> Large Cap Pharma; Mizuho -> megabank; Shin-Etsu -> Electronic Materials & Industrial Diversified (the proposed value); trading houses stay on Blended Industrial OpCo |
| 5 | LTI / cross-shareholdings netted at carrying value, like AAPL | **no code change needed** -- see below | |
| 6 | JPY CRP **0.6%** (MARKET_REGISTRY JPX) | `valuation_constants.country_risk_premium.by_currency.JPY = 0.006` | the engine's CRP is keyed by statement currency, so every JPY reporter gets 0.6% in the firm CAPM, the market CAPM and the benchmark |

**Correction to my first report (item 5):** the E18 long-term-investments netting (`_apply_lti_netting`) already
applied to Tokyo non-financials before any change -- I had said it did not. The before and after numbers are the
same because it was on both times:

| Company | Long-term investments netted | Net debt before -> after netting | Guard that prevents double counting |
|---|---|---|---|
| Toyota (7203.T) | JP¥16,105bn | JP¥28,973bn -> JP¥12,869bn | profile prices no holdings (no SOTP / NAV leg); netted once (`components.long_term_investments` set -> no second pass) |
| Mitsubishi Corp (8058.T) | JP¥8,246bn | JP¥5,057bn -> -JP¥3,189bn | as above; no holdco template, so no equity-accounted D2 bridge adds the stakes again (`_lti_already` blocks it if one is added) |
| Keyence / Sony / Hitachi | 1,692 / 1,690 / 867bn | | |
| SoftBank Group (9984.T) | JP¥37,089bn | JP¥26,480bn -> -JP¥10,609bn | -- this is part of why its number is unusable (below) |

What the netting cannot see: Mitsubishi Corp's equity-method stakes enter at BOOK (that is what the feed carries),
while their income sits in net income; the profile's legs are Forward EV/EBIT and FCF Yield, so EV/EBIT excludes
that income (no double count) and FCF counts only the dividends received. Where an owner later adds a holdco
template, the existing 6B carve-out moves those stakes from book-as-cash to market.

### Measured effect (engine-only, same temp store, same day; "before" = commit 2e2547db production behaviour)

| Ticker | Profile before -> after | WACC / bank rate before -> after | Base IV before -> after | Spot | 12m target before -> after |
|---|---|---|---|---|---|
| 7203.T Toyota | Automotive (OEM) | 9.20% -> 7.76% | 3,198 -> 3,198 (multiples-only profile) | 3,384 | 3,679 -> 3,625 |
| 8306.T MUFG | Money Center Bank -> **(JP)** | 5.25% -> 6.85% | 2,850 -> **2,722** | 3,192 | 3,286 -> 3,223 |
| 8316.T SMFG | -> Money Center Bank (JP) | -> 6.85% | -> 2,670 | 3,050 | -> 3,107 |
| 8411.T Mizuho | Super-Regional -> Money Center Bank (JP) | -> 6.85% | -> 6,078 | 7,702 | -> 7,491 |
| 6861.T Keyence | Mature Platform -> **Capital Goods** | 10.68% -> 7.50% | 9,585 -> **10,266** | 14,845 | 13,446 -> 13,421 |
| 6501.T Hitachi | Blended Industrial OpCo | 8.40% -> 6.76% | 6,132 -> **6,736** | 5,168 | 5,920 -> 6,053 |
| 8035.T Tokyo Electron | IDM / Foundry -> **Equipment / EDA** | 11.10% -> 9.21% | 2,849 -> **4,947** | 8,220 | 5,749 -> 6,715 |
| 6857.T Advantest | -> Equipment / EDA | -> 8.94% | -> 16,984 | 26,585 | -> 23,693 |
| 4502.T Takeda | Specialty & Generic -> Large Cap Pharma (engine dispatches "Big Pharma (Consolidated DCF)") | 8.71% -> 7.50% | 3,728 -> 5,096 | 7,341 | 5,939 -> 6,926 |
| 6758.T Sony | Consumer Electronics / Hardware Ecosystem | 9.77% -> 8.13% | 4,275 -> 4,473 | 4,413 | 4,769 -> 4,772 |
| 8058.T Mitsubishi Corp | Blended Industrial OpCo | 7.41% -> 5.85% | 7,974 -> 8,012 | 5,155 | 6,996 -> 6,909 |
| 9984.T SoftBank Group | Telecom Carrier (unchanged, not pinned) | 5.94% -> 5.70% (floored) | 5,181 -> 5,257 | 2,984.5 | 4,073 -> 4,040 |

Readings:
- The JGB curve lowers firm-CAPM WACC by 1.4-3.2pp net of the new 0.6% CRP. IV moves only through DCF legs:
  Hitachi +9.9%, Keyence +7.1% (with the re-route), Sony +4.6%. Multiples-only profiles (Toyota) do not move.
- MUFG's IV FALLS 4.5%. The lower P/TBV (1.7x vs the US 2.2x) outweighs the lower CoE (8.8% vs 9.3%) in the GGM
  and RI legs. Megabank IVs sit 15-21% below spot, so all three would rate Underweight against a 7.71% hurdle
  unless research moves their ROE.
- Tokyo Electron's IV rises 74% on the equipment profile but stays 40% below spot; Advantest is 36% below.
  These are consistent with the profile change and with the engine's full-SBC / RONIC = WACC conventions,
  not with the market's AI-capex premium.
- Each "before" figure was computed with US-curve rates (local fallback 4.94%) and no CRP; production used live
  DGS10, so the production "before" WACCs were slightly different.

### SoftBank Group (9984.T): not publishable until the owner supplies holdco inputs
Kept as it is, per the owner: Telecom Carrier, unpinned, limited coverage, NOT marked unrated.
- **Live run:** IV 14,265 against a 2,984.5 spot.
- **Engine-only:** 5,257.
- **Why:** EV/EBITDA on consolidated SoftBank Corp./Arm EBITDA, plus the E18 netting of JP¥37.1tn of investments at
  carrying value, plus a 2-stage DCF on consolidated cash flows. This is three views of the same assets, none of
  them a holdco NAV.
- **Inputs needed:** the stakes (Arm ~90%, SoftBank Corp. ~40%, Vision Funds, OpenAI), standalone holdco net debt
  (SBG results presentation, 10 Nov 2026), a value basis for the unlisted holdings, and the owner's discount.
  These go into a `holdco_sotp_templates.json` entry, then the pin
  `["Financials", "Asian Holding Company (Look-Through)"]`.
- **Listed look-through alone:** JP¥30.7tn against a JP¥17.0tn market cap.

### Gaps closed in the same change
- **MUFG fiscal-year label:** `japan.relabel_fiscal_years`, applied where the engine reads the 2G guidance block
  (`dcf_agent`, on read, so cached extractions are fixed too and no normaliser version bump is needed). A Tokyo
  FY+1 label at or before the last reported year end moves forward ("FY2026" with 2026-03-31 reported ->
  "FY2027"). The change is recorded in `fiscal_year_relabelled`.
- **Backtest note:** prints the listing currency (`_backtest_ccy_symbol`: JP¥ / S$ / HK$ / $) instead of a literal
  `$`. This fixes HK and SG too.
- **Bare ¥ in the narrative:** `normalize_yen` now runs on the stored research (`deep_research`, annotated,
  sections) for Tokyo tickers, so the web view, PM and PDF all read JP¥. The PDF rewrite stays as a backstop
  for covered runs that reuse older research.
- **Not re-run live:** the 3-run Qwen budget was already used, so these were verified by unit tests only.

## Derivations (proposed 2026-11-28, accepted by the owner the same day)
Kept from the first report for the record. Where a heading says PROPOSED or "nothing changed", read the
table above for the accepted state. Item 8's claim that the EV legs give no value to cross-shareholdings was
wrong: the E18 netting already applied.

### 1. Risk-free rate: 10y JGB 2.70% (`valuation_constants.risk_free_by_market.JP`, status PROPOSED)
- **Derivation:** Ministry of Finance JGB constant-maturity curve (`jgbcme.csv`), 10Y close 2026-11-26 **2.704%**.
  November 2026 had 17 closes, mean 2.81%, range 2.70% (26 Nov) to 2.93% (16 Nov). The SG row used the latest
  close, so the same rule gives 2.70%.
- **Why:** JPY cash flows must be discounted on the JPY curve, the rule behind the SG row. Without the row, a Tokyo
  name discounts at the US 10y (~4.9%), about 2.2pp too high.
- **Note:** the JGB is no longer "very low". At 2.70% it sits ABOVE the SGS 10y (2.29%).
- **Behaviour today:** `capm.risk_free` skips a PROPOSED row, so Japan stays on DGS10 exactly as before.
  `RF_APPLY_PROPOSED=JP` applies it for local measurement only.
- **To accept:** delete `"status": "PROPOSED"` from the JP row.

### 2. Minimum Ke / WACC spread over the JGB: 3.0pp (same row, `min_spread_over_rf`)
- This mirrors the owner's SG floor.
- On 2.70% + 4.41% x beta (+ CRP), it binds only below beta ~0.55. NTT's own beta is -0.17, KDDI's is -0.10 and
  SoftBank Corp's is 0.09, so the industry beta carries them.
- In the measurement it bound on 9984.T's WACC (5.70%) and on no Ke.
- Japanese desks rarely use a Ke below ~5.5-6%. Owner to confirm 3.0pp, or set a Japan-specific figure.

### 3. Rating benchmark: TOPIX (`ratings._BENCHMARKS[("JPX", False)]`)
- The name changed from Nikkei 225 to TOPIX. TOPIX is the cap-weighted Prime-market index that the Japanese buy
  side benchmarks against; the Nikkei is price-weighted and dominated by a few names (Fast Retailing, Tokyo
  Electron, Advantest, SoftBank Group).
- The expected return is NOT a new constant. It is rf + ERP + CRP from the run's CAPM build:
  - today, on UST: 4.94% + 4.41% + 0 = **9.35%**;
  - with proposal 1: 2.70% + 4.41% + 0 = **7.11%**.
- **Cross-check before accepting:** TOPIX's own implied return (forward earnings yield ~6-7% at ~15-16x, or
  dividend yield ~2.2% + buyback yield + nominal growth). These figures are indicative and were NOT measured
  here: FMP serves no TOPIX valuation series.
- On UST the hurdle is ~2.2pp higher than on the JPY curve, which biases every Tokyo rating toward Underweight.
  That is the SG problem the owner fixed on 2026-11-27.
- **Owner decision:** accept TOPIX, or keep the Nikkei 225 name.

### 4. JPY country risk premium (owner decision; nothing changed)
The engine reads the owner's `country_risk_premium.by_currency`. That table has no JPY row, so Japan carries
**0%**, the same treatment as the owner's CNY/HKD zero. `MARKET_REGISTRY` carries 0.60% for JPX (sovereign A+),
but only the ratings fallback reads it. Options:
- 0% (status quo, mature market);
- 0.60% (registry);
- Damodaran's current Japan CRP (look up the January 2026 table before choosing).
Adding `"JPY": x` to `by_currency` applies it.

### 5. Megabank calibration (`japan_profile_pins.json > bank_calibration_proposal`)
MUFG, SMFG and Mizuho route to `Money Center Bank`, whose calibration row is US: P/TBV 2.2x, CoE 9.3%,
target ROE 12%.
- **Measured P/TBV:** spot price on 30 Sep 2026 tangible book gives MUFG **1.73x**, SMFG **1.58x**, Mizuho **1.80x**
  (median 1.73x). The JPX "Banks - Diversified" P/B median is 1.13x. At the US 2.2x, MUFG's P/TBV leg (21% weight)
  prices at ~JP¥4,070 against a JP¥3,192 spot.
- **Market-implied CoE:** from MUFG's P/B and ROE, (12.4% - 2.7%) / (CoE - 2.7%) = 1.58 gives CoE **8.8%**. The
  CAPM at beta 1 on the JGB curve gives 7.1%, which no Japanese bank desk uses.
- **Proposed** `Money Center Bank (JP)`: P/TBV 1.7x, CoE 8.8%, target ROE 12%. Check the target against the
  megabanks' medium-term plans.
- Also proposed: pin Mizuho (FMP: "Banks - Regional" -> US `Super-Regional Bank`) to the megabank profile.

### 6. Profile pins: 35 names (`src/markets/japan_profile_pins.json`, inactive)
Re-routes proposed:
- Keyence: `Mature Platform` -> `Capital Goods`.
- Tokyo Electron and Advantest: `IDM / Foundry` -> `Equipment / EDA`.
- Murata: `Mature Platform` -> `Electronic Materials & Industrial Diversified`.
- Takeda: Specialty & Generic -> `Large Cap Pharma`.
- Mizuho: as in item 5.
- SoftBank Group: `Telecom Carrier` -> `Asian Holding Company (Look-Through)`. This is blocked on inputs; see
  item 7.

Owner choices:
- Shin-Etsu: Commodity Chemicals vs Electronic Materials, or a SOTP.
- The five sogo shosha: keep `Blended Industrial OpCo` or create a Trading House profile. The JPX "Conglomerates"
  cohort is mostly these five, so their relative legs are already effectively a trading-house basket.

The other 21 rows confirm today's route and would only mark the names *tuned*. A pin also changes billing from
`limited` (1 credit) to `new` (2 credits). Every proposed profile exists in `INDUSTRY_VALUATION_PROFILES` (tested).

### 7. SoftBank Group (9984.T): holdco discount is to be derived, not assumed
Today it values as a telecom carrier: EV/EBITDA on consolidated SoftBank Corp. EBITDA plus a 2-stage DCF.
- **Listed look-through** (FMP 2026-11-27):
  - Arm at ~90%: ARM US$182.5bn x JPY 159.5 = **JP¥26.2tn**;
  - SoftBank Corp at ~40%: 9434.T JP¥11.2tn = **JP¥4.5tn**;
  - sum **JP¥30.7tn**, against SBG's market cap **JP¥17.0tn**.
  This is before the Vision Funds, OpenAI and other unlisted assets, and before holdco net debt.
- The consolidated net debt (JP¥26.5tn) includes the subsidiaries' own debt and is NOT the holdco figure.
- Inputs needed for a `holdco_sotp_templates.json` entry: the stakes and SBG's standalone net debt (from the latest
  results presentation, 10 Nov 2026; the TDnet 決算短信 PDF is resolved automatically), plus a value basis for the
  unlisted holdings.
- Then the market-implied discount to GAV can be measured, and the owner sets the discount
  (holdco_discount_not_constant).
- Until then, the pin stays proposed, and the report's 9984.T number should be read as limited coverage.

### 8. Cash-rich balance sheets and cross-shareholdings (owner decision; nothing changed)
The bridge nets cash and short-term investments. It does not net `long_term_investments` (strategic stakes) for
non-biotech names. Long-term investments as a share of market cap (FMP, 30 Sep 2026):

| Company | LTI / market cap | Cash + STI / market cap |
|---|---|---|
| Toyota | 40% (JP¥16.1tn) | 36% |
| Mitsubishi Corp | 48% | 10% |
| Keyence | 9% | 8% |
| Sony | 7% | 8% |
| Hitachi | 4% | 7% |

EV legs (EV/EBITDA, EV/EBIT) therefore give no value to listed cross-shareholdings (政策保有株式) or to
equity-method affiliates whose income sits below EBITDA. P/E legs capture the dividends and equity-method income.

Proposal for the owner: on EV legs for Tokyo non-financials, add investment securities at fair value net of ~30%
tax on the unrealised gain. Exclude trading houses whose profile prices holdings. Disclose it in the bridge.

## Still open for the owner
1. SoftBank Group holdco inputs and discount (above).
2. A trading-house profile for the five sogo shosha (they stay on Blended Industrial OpCo).
3. EDINET API key (`EDINET_API_KEY`) if 有価証券報告書 links are wanted.
4. Licensing questions in `docs/LAUNCH_TODO.md`, for counsel.
5. Megabank CET1 target / RWA density / fade years: carried from the US row, not re-derived.

## Risks
- **SoftBank Group is not publishable** until the owner supplies the holdco inputs: live IV 14,265 vs spot
  2,984.5. It is kept as it is and not marked unrated, per the owner.
- **Fiscal-year naming:** the 8306.T run kept MUFG's start-year "FY2026". This is now relabelled on read
  (`relabel_fiscal_years`). It is unit-tested but not re-run live. Labels inside the free-text narrative are not
  rewritten.
- **Accounting basis mix:** J-GAAP (MUFG, most mid-caps), IFRS (Toyota, Sony, SoftBank Group) and US GAAP (Nomura,
  ORIX, Canon, FUJIFILM, Komatsu, now in `filing_basis.US_GAAP_NON_USD_REPORTERS` for the lease bridge).
  - J-GAAP amortises goodwill, so its P/E is not comparable with IFRS peers in the same JPX cohort.
  - J-GAAP operating leases come on balance sheet only from April 2027.
  - The basis is read live from the TDnet title (〔IFRS〕/〔日本基準〕/〔米国基準〕) but is not yet fed to the engine.
- **TDnet scraping:** HTML pages, no API, and about one month retained. Disable with `JP_TDNET=off`. On a page
  change, the resolver returns nothing and the run uses FMP attribution.
- **Toyota's beta relevers at captive-finance debt (1.98).** It does not move IV today, because the OEM profile has
  no DCF leg. It will matter when the captive SOTP runs.
- **Cross-market `$` literals:** the backtest note is fixed. `$` remains in the PT-IV guard text and the
  financial editor template (HK/SG/JP alike).
- **Research divergence check:** on 7203.T it flagged "revenue 97% below the reported figure", because a segment
  figure was read as group revenue. The check works as designed; the narrative is not used for numbers.
- **Macro classifier:** on qwen3.8-flash it failed to parse and injected its default on the 7203.T run. This is
  pre-existing and not Japan-specific.

## Shared files touched (for the China / Korea merge)
| File | Change |
|---|---|
| `src/tools/ticker_canonical.py` | JP branch first in `canonical_ticker` (+ doctest lines) |
| `src/tools/fmp_transcripts.py` | JP branch in `to_fmp_symbol` |
| `app/backend/request_guards.py` | `TICKER_RE` admits `TYO:/TSE:/JPX:/JP:` prefix; example text |
| `app/backend/services/market_ask.py` | `_supported_symbol` JP branch; prompt names Tokyo |
| `app/backend/services/analysis_service.py` | `TSE OK`/`TSE:` statuses normalised to Done |
| `src/agents/analysis/dcf_agent.py` | `Money Center Bank (JP)` calibration + `_BANK_PROFILES`; `_backtest_ccy_symbol`; `relabel_fiscal_years` on the guidance block; import `_is_jp_ticker`; `_STATUTORY_TAX["JP"]`; `_company_tax_rate` JP; firm-CAPM / market-CAPM / bank-g-cap market `"JP"`; `_consensus_at_run` defers `.T`; `_CCY_SYMBOLS["JPY"]="JP¥"` |
| `src/data/capm.py` | `_TAX["JP"]`; `proposed_rf_applied` + PROPOSED-row skip in `risk_free` (generic); `Money Center Bank (JP)` in `FINANCIAL_PROFILES` |
| `src/data/valuation_constants.json` | `risk_free_by_market.markets.JP` (accepted); `country_risk_premium.by_currency.JPY` 0.006 |
| `src/data/industry_profile_map.json` | v26: 34 Tokyo `ticker_overrides` + `routing_scope_tickers` |
| `src/data/sector_profiles.py` | `Money Center Bank (JP)` profile, peers, bank-profile set |
| `src/data/regional_comps.py` | `CROSS_MARKET_BASKETS["Money Center Bank (JP)"]` |
| `src/data/report_families.py`, `src/data/sector_kpi_framework.py`, `src/agents/industry/sector_prompts.py` | register `Money Center Bank (JP)` beside the SG row |
| `docker/Dockerfile.web`, `docker/Dockerfile.worker` | TrueType CJK font layer AFTER the poetry install |
| `src/data/valuation_constants.py` | `market_key` returns `JPX` for `.T` |
| `src/data/industry_profile_map.py` | `market_of` returns `JP` |
| `src/data/filing_basis.py` | 5 Tokyo US-GAAP filers |
| `src/decisions/ratings.py` | JPX benchmark -> TOPIX |
| `src/tools/target_price.py` | `tyo/` route; `¥` in the target/low/high regexes |
| `src/memory/valuation_outcomes.py` | `.T` joins the stockanalysis consensus path |
| `src/memory/freshness.py` | JP search hint |
| `src/agents/industry/edgar_hkex_resolver.py` | JP branch -> `japan_filings.resolve_filing_ref` |
| `src/agents/industry/deep_research.py` | Japan research context + TDnet filing context appended to `_base_context`; JP¥ normalisation of the stored narrative |
| `src/utils/pdf_report.py` | `Paragraph` subclass (`cjk_font.cjk_markup` + JP¥), `_CJK_HINT`, `JPY` symbol |
| `src/utils/report_disclosures.py` | `JP¥`; Tokyo fiscal-year limitation |
| `src/utils/valuation_workbook.py` | `JPY: JP¥` in the symbol table |
| `src/agents/analysis/sotp_report_extras.py` | `JPY: JP¥` |
| `app/frontend/src/lib/utils.ts` (CRLF kept) | `currencySymbol` `.T` -> `JP¥`; `JPY: 'JP¥'` |
| `app/frontend/src/pages/ReportPage.tsx` | `suggestionMarket` `'JP'` (exported); rank strips `.T` |
| `app/frontend/src/components/report/FinancialStatements.tsx`, `SotpAnalystPanel.tsx`, `app/frontend/src/pages/ScreenerPage.tsx` | `JPY: 'JP¥'` |
| `docs/LAUNCH_TODO.md` | new "Japan" section at the end |

New files: `src/markets/__init__.py`, `src/markets/japan.py`, `src/markets/japan_filings.py`,
`src/markets/japan_profile_pins.json` (record of the pins, now ACCEPTED), `src/utils/cjk_font.py` (byte-identical
to the Korea branch's module; `pdf_fonts.py` was removed), `tests/test_japan_market.py`, `docs/markets/JAPAN.md`.

Merge notes:
- `cjk_font` is identical on the Japan and Korea branches, so it merges cleanly. It picks a font by kana / hangul
  / Han; a kanji-only Japanese run draws in the Han font, which is legible but has Chinese glyph shapes.
  `_CJK_HINT` in pdf_report now only drives the JP¥ rewrite.
- The `"JP" if _is_jp_ticker(...)` branches sit on the same lines a CN/KR branch would edit, so expect trivial
  conflicts there. A shared `market_code(ticker)` helper would remove them; it is suggested as a follow-up after
  the three branches merge.
