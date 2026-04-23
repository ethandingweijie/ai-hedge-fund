# Equitable — Changelog

## v1.9.2 — 2026-04-24 (Sector-Aware Deep-Research Prompt Architecture)

### Architecture shift — (sector, profile_name) drives the Section 2F prompt
Previously deep research sent ONE generic 2,000-word Section 2 prompt on every
ticker. 2F asked "the anchor KPI" regardless of sector. This missed critical
sector disclosures (REIT portfolio mix, Bank NPL coverage, Biopharma pipeline)
and caused downstream extractors (`_extract_reit_metrics`, `_extract_bank_metrics`)
to return `{}` because the upstream research didn't contain the data they needed.

New `src/agents/industry/sector_prompts.py` routes to a matched 2F block using
`(sector, profile_name)` from the strategic router + REIT sub-type from the
classifier. 11 sector-specific prompt blocks:

- **REIT generic** — portfolio composition at finest granularity, cap rate,
  WALE, occupancy, AFFO coverage, **cost of debt + hedging ratio + ICR** for
  SGX / HK business trusts subject to MAS 45-50% aggregate leverage caps
- **REIT net-lease** — tenant industry diversification top-10, investment-grade
  %, **rent escalator structure** (fixed / CPI-linked / CPI+floor-cap / market
  review), weighted-avg escalator rate
- **REIT data center** — MW capacity mix, colocation vs wholesale vs
  interconnection, hyperscaler concentration, AI demand commitments in GW/MW
- **Bank** — CET1, **P/TBV Golden Ratio** with explicit ROE−CoE spread
  identity, NIM rate sensitivity per 100 bps, NPL coverage, management
  overlays, capital return yield decomposition
- **Asset Manager** — **FRE vs Carry/Performance Fee split** (markets pay
  20-30x for FRE vs 5-10x for carry), fee rate by product (passive 5-10bps /
  active 40-70bps / alts 80-150bps)
- **Insurance** — split P&C (P/BV anchor, combined ratio) vs Life (Embedded
  Value, VNB margin) vs Health (MLR) valuation frameworks
- **Payment Networks** — GPV, cross-border volume growth, take rate, VAS mix
- **Biopharma** — pipeline enumeration (name, phase, TA, peak sales, launch
  year, competitors), upcoming PDUFA/readouts, LOE exposure next 5 years
- **Tech Hyperscaler** — cloud revenue growth, **AI Capex vs Cloud Revenue
  Capture ratio** (revenue-per-dollar-of-capex), GPU capacity, FCF absorption
- **Mature SaaS** — ARR, NRR, Rule of 40, **Post-SBC FCF** (reported FCF
  minus SBC, shareholder-economic FCF), dilution rate
- **Growth SaaS** — LTV/CAC with **churn context** (inputs: ACV, gross
  margin, annual churn, CAC; red-flag signal when high Magic Number masks
  high churn), NRR vs Gross Retention split

### Extractor gating — `needs_extractor(extractor, sector, profile_name)`
Previously all 6 extractors ran unconditionally on every ticker (~4,800 output
tokens per run). Now sector-aware:

- `dcf_calibration` + `segment_scenarios` run universally (2 extractors × N tickers)
- `reit_metrics` runs only for RealEstate / REIT sector
- `bank_metrics` runs only for Financials + bank-family profile_name
- `pipeline_assets` runs only for Biopharma
- `saas_metrics` runs only for Tech + SaaS-family profile_name

Per-ticker token savings:
- Tech ticker: 6 → 3 extractors (−50%)
- Energy ticker: 6 → 2 extractors (−67%)
- REIT ticker: 6 → 3 extractors (−50%)

### Backward compatibility
- `_build_research_system(year)` without sector/profile args still returns
  the generic 2F block. Existing callers unaffected.
- Ran forward-compat test on 14 diverse tickers (O, DLR, EQIX, SPG, JPM, GS,
  BLK, V, MRNA, MSFT, ADBE, SNOW, AAPL, XOM). All routed to the correct
  sector-specific prompt; AAPL (unspecified Tech profile) correctly falls to
  generic Tech block.

### Gemini review refinements — applied to prompts
- Bank: added **P/TBV Golden Ratio** valuation identity (Fair P/TBV ≈ (ROE−g)/(CoE−g))
- Asset Manager: **FRE vs performance fee split** — markets pay dramatically
  different multiples
- Insurance: separated **P/BV anchor (P&C)** from **Embedded Value (Life)**
- REIT / net-lease: **rent escalator structure** — critical in higher-for-
  longer rate environments; **hedging ratio on floating debt**
- Tech / Hyperscaler: **cloud revenue capture per $ of capex** — the core
  market debate on H100 ROI
- Mature SaaS: **Post-SBC FCF** — shareholder-economic FCF after dilution
- Growth SaaS: **LTV/CAC with churn inputs** — catches the "high magic
  number masks high churn" valuation trap

### Files changed
- `src/agents/industry/sector_prompts.py` (new, 455 lines)
- `src/agents/industry/deep_research.py` — `_build_research_system` signature,
  `_research_one_ticker` propagates `profile_name`, REIT sub-type pre-classified,
  extractor tasks filtered via `needs_extractor()`

---

## v1.9.1 — 2026-04-23 (REIT sub-type calibration refinement)
- `data_center` cap 4.5% → 5.0% (Green Street Apr 2026 consensus; 4.5% was
  EQIX-tier only)
- New `data_center_premium` sub-type (4.5% cap, 23x P/FFO, 26x P/AFFO) for
  interconnection-moat REITs (EQIX, Interxion)
- `self_storage` cap 5.2% → 5.5% (matches PSA / EXR consensus)
- `retail` cap 6.8% → 6.2% (Class-A mall / grocery-anchored strip blend;
  previous penalized SPG-quality operators)
- Verified NAV impact: DLR $168 → $145 (more conservative), EQIX now $708
  (premium tier), PSA $254 → $279, SPG $267 → $302

---

## v1.9 — 2026-04-22 (Sector-Specific Valuation UI + TBV/NAV Calibration)

### REIT Valuation Panel (frontend)
- Ships `app/frontend/src/components/report/reit/REITValuationPanel.tsx` — 8 sector-specific sub-panels.
- **NAV Hero card**: centered NAV/sh with upside vs current price + quad grid below (NOI / GAV / Debt tinted red / Cash tinted green).
- **REIT Key Stats grid**: Implied cap, Dist. yield, AFFO coverage, Leverage, Occupancy, WALE, FFO/sh, AFFO/sh with threshold color-coding.
- **Distribution Quality gauge** with 100% safety line for AFFO coverage.
- **NPI + DPU history** 5-year bar charts (CLINT-style) via recharts.
- **Portfolio Composition pies** (asset class + geography) — always rendered with classified sub-type fallback (single 100% slice) when research extractor hasn't populated subtype_mix/geographic_mix.
- **Cap-Rate × NOI-Growth 3×3 sensitivity matrix** with peer cell highlighted.
- Backend: `dcf_range.reit_breakdown` emitter with 5y history arrays.

### Bank Valuation Panel (frontend)
- Ships `app/frontend/src/components/report/bank/BankValuationPanel.tsx` — 8 panels matching DBS / OCBC institutional research driver hierarchy (Asset Quality, NIM, ROE, Capital Management, Loan Growth).
- **P/TBV Fair Value Hero** (Gordon-growth identity): `Fair = TBV × (1 + (ROE−CoE) / CoE)`. JPM Fair Value $186.00 (TBV $106.85 × 1.74x), GS $472.21 (TBV $377.94 × 1.25x).
- **Quad grid**: TBV/sh, BVPS, ROE tinted green when ≥ target, CET1 buffer tinted.
- **ROE vs CoE Spread gauge** with zero-line marker + "strong value creator / creator / marginal / below-CoC / destroyer" labels.
- **Capital Return card**: total yield hero (div + buyback) + 4-tile row with payout ratio + CET1 distributable surplus.
- **PPOP, NIM, CIR, BVPS 5y bar charts** via `_compute_ppop` 3-tier fallback (operating_income+provisions → NII+non_int_inc−opex → revenue−int_exp−opex with positivity gate).
- **NIM Rate Sensitivity tile** (research-sourced) — shows "X bps NIM per 100 bps rate" with forward guidance caption.
- **Book Quality card** — NPL, NPL coverage gauge with 100% safety line, credit cost, management overlays.
- Backend: `dcf_range.bank_breakdown` emitter gated on `sector=="Financials" + profile in _BANK_PROFILE_CALIBRATION` OR `"Bank" in profile_name` OR `profile=="Mortgage/GSE"`.

### Research extractor schema expansion
- `_extract_bank_metrics()` schema expanded with:
  - `npl_coverage_ratio` (OCBC-style 150% = 1.50)
  - `management_overlays_bn` (OCBC reports S$700m = 0.70)
  - `nim_rate_sensitivity_bps` (DBS reports 11 bps per 100 bp rate move)
  - `forward_loan_growth_guidance`, `forward_nim_guidance` (verbatim mgmt quotes)

### Sector routing + data integrity fixes
- **JPM TBV bug (Gemini critique)** — `_BALANCE_MAP` was mapping `goodwillAndIntangibleAssets` → `intangible_assets`, causing `_compute_bank_metrics` to double-count goodwill and hit the 70%-of-equity floor. Fixed to map `intangibleAssets` directly (just intangibles, no goodwill). Also added FMP's pre-computed `tangibleBookValuePerShare` from `/stable/ratios` as primary TBV/sh source. **JPM Fair Value $158.08 → $186.00** (within 1% of Gemini's $187.15).
- **Realty Income NAV bug (Gemini critique)** — O was classified as `default` sub-type with 6.5% cap rate, producing NAV/sh $24.55 vs actual BV $40-45. Added new `net_lease` sub-type (cap 5.0%, P/FFO 16x, P/AFFO 18x, maint capex 1%) and keyword classifier with "net lease / triple net / nnn / single-tenant / realty income / agree realty / spirit realty / wpc / w.p. carey / broadstone". **O NAV/sh $24.55 → $42.66**. Cascades to ADC ($80.96), WPC ($80.39), NNN, BNL.
- **12M PT methodology — REITs** — previously routed through `_use_pe_only` (EPS × 35x RealEstate peer PE) which is conceptually wrong because REIT GAAP EPS is depressed by non-cash D&A. New REIT branch: `FFO/sh × (1+g) × P/FFO_sub-type` blended 60/40 with AFFO path. No growth_premium on top (multiples already embed growth). DLR 12M PT base $136 → $237, matching 22x P/FFO_fwd market multiple.
- **_BALANCE_MAP extended** with `netLoans` / `loansAndLeasesReceivables` / `loansHeldForInvestment` / `totalDeposits` for future bank loan-book coverage (FMP coverage inconsistent, but cheap to add).

### Deterministic ticker routing
- 27 major US REITs + 6 net-lease REITs added to `TICKER_SECTOR_LOOKUP`: DLR, EQIX, PSA, EXR, ARE, WELL, VTR, AVB, EQR, MAA, ESS, VICI, BXP, VNO, STAG, HST, RHP, APLE, KIM, FRT, REG, MAC, DOC, OHI, ADC, NNN, WPC, SRC, BNL. Removes dependency on LLM sector classifier.
- SGX banks upgraded: O39.SI (OCBC) and U11.SI (UOB) from generic "Banks" profile to "Money Center Bank" (same calibration as D05.SI DBS).

### Backfill tooling
- `scripts/backfill_reit_breakdown.py` and `scripts/backfill_bank_breakdown.py` — re-derive `{reit,bank}_breakdown` from line items for archived runs. Targets `web_runs.full_result_json`.
- `POST /admin/backfill-reit-breakdown` + `/admin/backfill-bank-breakdown` — one-shot HTTP endpoints gated behind `DB_UPLOAD_SECRET`. Dry-run default; ticker filter + force re-derive supported.
- Backfill auto-corrects `sector` column when archived rows had LLM-misclassified sectors.

### Frontend wiring
- REIT + Bank panels wired into all 3 render paths: `pages/ReportPage.tsx` (live), `pages/ReportViewPage.tsx` (historic desktop), `components/v2/V2ReportView.tsx` (mobile).
- Gate ordering: REIT → Bank → generic DCF Ladder.
- Zinc palette alignment: swapped shadcn `bg-card` / `border-border` / `text-foreground` tokens to explicit `bg-white dark:bg-zinc-900` / `border-zinc-200 dark:border-zinc-800` / `text-zinc-900 dark:text-zinc-50` to match v2 card surfaces exactly.
- TypeScript types: `ReitBreakdown` + `BankBreakdown` interfaces added to `lib/reportTypes.ts`.

### Build numbers
- `app/backend/main.py` FastAPI version → 1.9.0
- `app/frontend/package.json` → 1.9.0
- `pyproject.toml` → 1.9.0

---

## v1.8 — 2026-04-22

### Tier 2 Bank Methodology (institutional rebuild)
- **2-stage Residual Income** replaces primitive ROE-CoE spread. ROE fades linearly current → profile target over 5-10 years; BVPS compounds at `retention × ROE_t`; terminal spread (+50-100 bps moat premium for GSIBs/Super-Regionals/Indian privates) captures durable excess returns over perpetuity.
- **P/TBV multiple** replaces P/BV — strips goodwill + intangibles to match Basel regulatory capital definition. 70%-of-equity floor prevents pathological data-artifact strips.
- **CET1 Excess Capital overlay** — CET1 > target returns `(actual - target) × RWA × 0.70` per share (asymmetric haircut: only 70% distributable); CET1 < target subtracts deficit at full haircut (regulator forces retention). RWA proxied via sub-profile-specific asset ratios (GSIB 0.55x, Regional 0.70x, IB 0.40x) when FMP lacks it.
- **P/E (norm) through-cycle fallback** — when normalized NI isn't computed, uses `equity × target_ROE` instead of trailing NI. Immune to credit-cycle provision distortion.
- **Buyback-aware retention rate** — includes `share_buyback` alongside dividends (JPM returned ~$25B via repurchases in 2024; dividend-only retention overstated by 30+ pp).
- **Profile weights flipped**: RI 55% / P/TBV 25% / P/E (norm) 15% / Excess Capital 5%. Dropped "ROE vs CoE" (double-counted RI per Gemini critique).
- **10 bank sub-profiles with geography-aware calibration**: Money Center (US/EU), Regional, Super-Regional, EM Bank (China SOE), EM Bank Premium (India private, 7y fade + 16% target ROE), Investment Bank, Mortgage/GSE, Neo/Challenger (10y J-curve fade), Brokerage.
- **HK/SG classification fix**: 00005.HK HSBC → Money Center Bank (EU), 01398.HK ICBC / 00939.HK CCB / 03988.HK BOC / 03968.HK CMB → EM Bank, D05.SI DBS → Money Center Bank. Previously empty sub-profile → fell to generic Financials.
- **Deep research `_extract_bank_metrics()`** — LLM extracts CET1, NIM, efficiency ratio, NPL, management target ROE/ROTCE, loan/deposit growth, dividend payout. Overrides profile defaults in RI + Excess Capital dispatches.

### Tier 2 REIT NAV / P/FFO / P/AFFO (replaces all-proxy)
- **NAV (Cap Rates)** — `NOI / cap_rate − total_debt + cash`. Scenario-invariant (asset-backed, not growth-driven).
- **P/FFO + P/AFFO** — sub-type-specific multiples, replacing prior P/E proxy (GAAP earnings depressed by non-cash real-estate D&A).
- **AFFO-gated DDM** — clamps dividends to AFFO/share, catches yield-trap valuations of unsustainable distributions.
- **11 REIT sub-types with maintenance capex caps**: data_center 2% / lab 2.5% / industrial 3% / self_storage 3% / residential 4% / healthcare 4% / retail 5.5% / office 6% / hospitality 7.5% / infrastructure 8.5% of revenue. Protects AFFO on growth REITs with heavy acquisition capex.
- **SGX REIT sub-type aware** — Capitaland India (office), Capitaland China (retail), Keppel Infrastructure (infrastructure), Mapletree Logistics (industrial), Frasers Centrepoint (retail), Keppel DC (data_center), Ascott (hospitality).
- **Deep research `_extract_reit_metrics()`** — cited cap rate, occupancy, WALE, sub-type/geo mix, DPU vs AFFO coverage, leverage. Overrides sub-type defaults via `cap_rate_market`.

### Tier 2 Biopharma rNPV (replaces DCF proxy)
- **2-stage rNPV**: per-asset `peak_sales × op_margin × (1-tax) × ramp_profile × cumulative_PoS × discount(years_to_launch)`.
- **PHASE_POS_TABLE**: Ph1 9.6%, Ph2 15.3%, Ph3 49.3%, Filed 85%, Approved 100% (BIO 2011-2020 industry stats + FDA historical).
- **Therapeutic-area PoS multipliers** — Oncology 0.55x, CNS 0.60x, Rare 1.7x, Hematology 1.4x, GLP-1 1.30x (BIO TA-specific rates).
- **Bell-shaped commercial stream** (20/50/80% ramp + 7 years peak + 40/20/10% LOE decay) replaces level annuity.
- **Profile-specific WACC + margin**: Large Cap Pharma 7.85% / 45% op margin / 14% tax (Irish IP structure); Pre-approval Biotech 11% / 40% / 21% (clinical-stage premium + US statutory).
- **`_extract_pipeline_assets()` extractor** from deep research sections 2A/2D/2F — per-asset JSON with name, phase, peak_sales, launch_year, indication, evidence.

### Tier 3 Insider-Activity WACC Overlay
- Net 12m insider buying / selling translates to ±bp WACC modifier (capped at ±50 bp). Cluster buys get additional tightening; CEO/CFO conviction sells widen. Consumes existing `state["data"]["insider_activity"]` (previously unused by DCF). Threshold gate at 0.02% of mkt cap suppresses noise.

### Backend + Frontend Hotfixes
- **Chart latency regression fixed (1s → 10s back to 1s)**: US stock endpoint now uses FMP `/historical-price-eod/full` (CDN-served) + parallelizes FMP history + yf.info + FMP key-metrics-ttm + ratios-ttm + quote via `asyncio.gather`. Previously 4 sequential fetches.
- **/analysis/financials 500 on SGX tickers fixed**: FMP returns 402 Payment Required for `.SI` suffix. Routed SGX tickers to yfinance via `search_sg_line_items`, mirroring HK → AKShare pattern.
- **REIT 12m PT $5.23 overshoot fixed**: REITs no longer fall through EV/EBITDA waterfall (produces nonsense on high-LTV REITs with D&A-inflated EBITDA). Now gated to P/E-only path alongside banks.
- **SGX D&A + DPS coverage added to yfinance mapping**: `Reconciled Depreciation` maps to `depreciation_and_amortization`; DPS derived by summing `.dividends` event series per fiscal year. Unblocks FFO + DDM methods for SGX REITs.

### Deep Research Prompt Enrichment
- **Financials sector** — explicit asks for CET1 (decimal), NIM last 4Q with direction, efficiency ratio with sub-profile target bands, management target ROE/ROTCE cited from earnings calls, dividend sustainability vs payout policy.
- **Real Estate sector** — cited portfolio cap rate (CBRE/JLL/Knight Frank weighted avg), sub-type mix (office/retail/industrial/DC %), geographic mix (US/India/China %), DPU vs AFFO coverage, aggregate leverage.

### Valuation Engine Coverage Validation
Synthetic-pipeline tests on realistic FY2024 financials:
- **Banks** (JPM / GS / DBS / ICBC): 56% / 46% / 56% / 162% coverage vs market. 46-56% on GSIBs reflects value-discipline anchor; ICBC 162% = "fair value before China governance discount" (intentional).
- **REITs** (CY6U / AU8U / A7RU / M44U / J69U / AJBU / HMN): 125% / 136% / 124% / 109% / 76% / 122% / 66% coverage with research cap rate overrides. Every overridden ticker moved toward 100%.
- **Biopharma** (CRSP / BEAM / MRNA / BIIB / VRTX / PFE): 69% / 12% / 72% / 48% / 30% / 27% pipeline-only contribution. BEAM 12% reflects market-priced Ph1 optionality beyond aggregate BIO PoS.

### Known v1.5+ Follow-ups
- Insurance Embedded Value rebuild (still proxied to P/BV)
- Alt Asset Manager SOTP (still proxied to EPV)
- EM regional governance discount overlay (China VIE, India G-Sec live yield)
- Live local-yield CoE fetchers (India 10Y, China 10Y via FRED OECD series)
- HDFC/ICICI India stress test

---

## v1.5 — 2026-04-18

### SGX (Singapore) Ticker Support
- **Financial metrics**: Compute 34/39 FinancialMetrics fields from yfinance statements (REITs 87%, banks 69%). Growth metrics (revenue, earnings, EPS, FCF, EBITDA, book value — YoY), ratios (operating margin, P/S, debt-to-assets, quick, cash, operating CF, interest coverage), per-share (BVS, FCFPS, EV). Bank-specific: EPS from NI/shares, ROIC proxied by ROE, interest coverage from NII/OpEx.
- **Pydantic mapping fix**: SG `financial_metrics.py` returned short keys (`pe`, `pb`) but `FinancialMetrics` model expects long names (`price_to_earnings_ratio`). Added key mapping + pre-fill all missing model fields with `None`.
- **LineItem fix**: SG line_items missing required `ticker`/`report_period`/`period`/`currency` — now injected.
- **InsiderTrade fix**: SG insider trades missing `issuer`, `is_board_director`, `transaction_price_per_share`, `shares_owned_before/after`, `security_title` — pre-filled with `None`.
- **CompanyNews hardened**: Same safety pattern applied to SG news dispatch.
- **Ticker mapping fix**: `OXMU.SI` is Prime US REIT on yfinance, not CapitaLand India Trust. Correct code is `CY6U.SI`. Fixed in `universe.py` and `sector_profiles.py`.
- **SG screener crash**: `_compute_fast_vgpm_universe` expected `list[dict]` but SG passed `dict[str, dict]`. Fixed with dict→list conversion.

### Deep Research — Qwen for All Markets
- **`DEEP_RESEARCH_MODEL=qwen3.6-plus`** set on Railway. All tickers (US, HK, SG) now use Qwen 3.6-plus with `enable_search=True` for live web search. Previously US tickers defaulted to Claude and fell to Tier 3 knowledge-only when Anthropic web search failed.
- Streaming output (reasoning_content + content) compatible across all markets — same DashScope API key, endpoint, model, and SSE keepalive.

### SSE & iOS Reliability
- **Polling timeout**: 10 min → 30 min. Qwen deep research can take 15-20+ min.
- **Crash detection hardened**: Require 6 consecutive "not running" polls (60s) before declaring crash, up from 3. iOS suspends JS timers when screen is off — queued polls fired rapidly on wake, triggering false "analysis ended" errors.
- **Crash state stays amber**: Timeout state remains "reconnecting" (not "error") with softer message. Pipeline may still be running on server.
- **Wake-up handler**: Checks `/analysis/status` first on unlock. If pipeline alive, restarts polling with fresh crash counter.
- **Stale run filter**: Extended from 30 min to 45 min.

### Duplicate Pipeline Prevention
- **History → Ongoing → View**: Previously called `startStream()` which POSTed `/analysis/run`, triggering a new pipeline. Now uses `startPolling()` — polls for progress without submitting a new run.
- **Auto-reconnect on refresh**: Same fix — polls instead of re-submitting.
- Only the "Run Analysis" button triggers `POST /analysis/run`.

### Parallel Ticker Data Isolation
- **Financial chart bleed fix**: When switching from VEEV to A7RU via History, `liveResult` still held VEEV's data. `liveTicker` resolved to VEEV, so charts fetched VEEV's financials. Fix: clear `liveResult` on ticker switch; derive `liveTicker` from `ticker` state (not stale `liveResult`) when pipeline is running.
- **White screen crash**: `setLiveResult` called but not destructured from context — `ReferenceError` crashed React.

### UI & Branding
- **App icon**: Uniform #2e7d32 green square with white strikethrough "e" — no circle edges. iOS rounds corners automatically.

---

## v1.4 — 2026-04-17

### SSE Reconnect
- Fix SSE lost → stuck on progressing when results completed
- Click ongoing ticker in History to switch SSE stream view

### Branding & UX
- "Welcome to Equitable!" with forced login
- Equitable icon in hamburger drawer header
- Shift profile/settings to hamburger menu, remove top-right profile
- PWA manifest + app icons for home screen install
- iOS safe area: shift controls below status bar (Dynamic Island/notch)
- Phase milestone notifications (deep research start/done, investors, risk, completion)
- Audio ping + vibration on completion

### SGX Foundation
- SGX press releases handled by Qwen deep research (SGX API requires auth)
- SG ticker detection, universe, VGPM metrics, prices, market cap, line items, insider trades, news
- SG sector WACC, REIT VGPM weights, SGX_TICKER_SECTOR_LOOKUP

### Deep Research
- Qwen 3.6-plus streaming with reasoning_content
- SSE keepalive during Qwen streaming (progress every 15s)
- enable_thinking incompatible with enable_search — use enable_search only

### HK Enhancements
- HKEXnews announcement search (JSF POST scraping)
- Annual reports from HKEXnews
