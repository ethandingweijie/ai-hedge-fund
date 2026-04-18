# Equitable — Changelog

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
