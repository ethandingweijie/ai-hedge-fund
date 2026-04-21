# Equitable — Changelog

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
