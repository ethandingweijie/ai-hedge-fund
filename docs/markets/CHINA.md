# China A-shares (SSE `.SS` / SZSE `.SZ`)

Branch `worktree-agent-ae2d7ab84c6748057`, 2026-11-28. Base: `main` @ 62feab5d. Nothing pushed; production untouched
(all runs on a temp sqlite archive seeded from the read-only prod `regional_comps` snapshot of 2026-11-28).

**Owner decisions accepted 2026-11-28 and applied (section "Accepted decisions"):** bare six-digit codes stay
ambiguous; CGB 10y 1.59% + 3.0pp floor; the 46 sector pins; registry CRP 1.5% for SHH/SHZ in WACC and benchmark;
Ping An routed to the life-insurer profile and withheld until an embedded-value input is accepted; CJK fonts in the
production images.

China-specific logic lives in **`src/markets/china.py`** (tickers, A/H pairs, names, accepted pins and constants,
disclosure calendar, research context), **`src/tools/cninfo_api.py`** (primary filings), **`src/utils/cjk_font.py`**
(PDF CJK text) and **`app/frontend/src/lib/markets/china.ts`**. Shared files only carry hooks (list at the end).

## Status by checklist item

| # | Item | Status |
|---|------|--------|
| 1 | Tickers | **Works.** `600519.SS/.SH/.SHH`, `SH600519`, `SH.600519` → `600519.SS`; `SZ000858`, `.SHZ` → `000858.SZ`; the code must sit in the venue's range (SSE 600/601/603/605/688/689, SZSE 000-003/300/301). **A bare code (`600519`, `000660`) is left as typed** (owner, D-CN-1) and the landing search offers the A-share listing as a pick (`search_local`). Rejected (left as typed): B-shares (900xxx USD, 200xxx HKD), Beijing SE (4/8/920), index codes (`000001.SS` = SSE Composite), venue/prefix mismatch. FMP symbol = canonical. Landing search: `.SS/.SZ` suggestions enabled ("CN"), Chinese-name search (`贵州茅台`, `宁德`) via a local table of 49 large caps (FMP returns `[]` for CJK). Market questions: `600519.SS` accepted, cninfo/SSE/SZSE/CSRC tier-1. Portfolio: canonical via `canonical_ticker`. History filter has a CN market. |
| 2 | Prices / currency | **Works.** FMP profile currency `CNY` for all names probed; venue default `CNY`. Quotes, `historical-price-eod` (Moutai back to 2001-08-28), market cap in CNY units. FX = 1 (CNY reporter, CNY listing; smoke `fx_note` empty). Label **RMB** everywhere (web `currencySymbol`, statements, portfolio, screener, research prompt, PDF `RMB `, Excel `RMB`); the bare `¥` for CNY was removed from 3 frontend maps and the research prompt. |
| 3 | Fundamentals | **Works on FMP.** 600519/300750/601318: 5y income/balance/cash-flow, key-metrics, ratios, **growth endpoints populated** (unlike HK), analyst estimates (Moutai 8, CATL 10-31, Ping An 4 analysts), `/earnings` history + booked dates, dividends, EV. Missing: `price-target-consensus`, `grades-consensus`, transcripts, segments, `news/stock` (0-2 rows). **Quirk:** A-share `filingDate`/`acceptedDate` = period end (FY2025 "filed" 2025-12-31; actually 2026-04-17) — never use them as publication dates (cninfo gives the real ones). **Quirk:** CATL FY2030 estimate row has `eps=0` with 10 analysts — a zero estimate row, not a forecast. CAS (ASBE) statements, consolidated, Dec FY. Consensus target: stockanalysis.com covers A-shares (`sha/`, `she/`); the page writes `¥` and sends no charset — fixed (UTF-8 decode + `¥` in the regex). Moutai RMB 1,705 (23 analysts), CATL 402.36 (28), Ping An A 75.09 (15). |
| 4 | Valuation | **Works on live SHH/SHZ comps** (age 0.29 d, prod snapshot). CGB rf 1.59% + 3.0pp floor and CRP 1.5% **accepted and applied**. WACC market code `CN`, tax 25%. `valuation_constants.market_key` now SHH/SHZ (was "US": an A-share utility would have taken the US 7.25% CoE row). 46 pins accepted. A/H: 31 pairs mapped, research told never to use the other line's price. A-share life insurers without an accepted EV input are withheld (`input_required`). |
| 5 | Rating benchmark | CSI 300 keyed for SHH/SHZ; expected return = the run's own rf + ERP + CRP (D1) = 1.59% + 4.41% + 1.50% = **7.50%** (was 9.26% on UST + 0 CRP). The no-CAPM fallback uses the same registry 1.5%. |
| 6 | Research | Chinese-language primary-source block in the research prompt; **cninfo resolver implemented** (annual report PDF + real filing dates) and wired into Phase 2.7 (A-shares no longer hit SEC EDGAR). Live run result below. |
| 7 | Freshness / calendar | `latest_earnings_event` adds the cninfo periodic-report date; `next_earnings_date` falls back to per-symbol `/earnings` rows (the window endpoint caps at 4,000 rows and held 5 A-shares in 3 weeks). CSRC statutory deadlines in `china.statutory_deadlines`. |
| 8 | Reports | PDF: every `Paragraph` wraps CJK runs in an embedded TTF (Windows: Microsoft YaHei; production: `fonts-wqy-microhei`, now installed in `docker/Dockerfile.web` and `.worker` together with `fonts-noto-cjk`) or ReportLab's built-in CID `STSong-Light`; helper module is `src/utils/cjk_font.py` (the merge's standard name); the title shows `Kweichow Moutai Co., Ltd. 贵州茅台 (600519.SS)`. Excel: openpyxl is Unicode; workbook labels use ISO codes and `RMB`. |
| 9 | Billing / coverage | `run_type`: covered / new / limited all resolve for `.SS/.SZ`. The 46 pinned large caps are **tuned** (first run "new", 2 credits); any other A-share is **limited** (1 credit). A withheld (unrated) run returns its credit through the existing `not_sellable_reason`. |
| 10 | Regulatory | Licensing questions added to `docs/LAUNCH_TODO.md` (questions only). |
| 11 | Tests | `tests/test_china_ashares.py` (67 tests). Full suite before the decisions: 7240 passed, 80 skipped, 4 xfailed; after: **7244 passed, 80 skipped, 4 xfailed** (15m04s, `set -o pipefail`; one source-window test in test_unrated_state caught the first cut, fixed by moving the EV check into `china.ev_withhold_verdict`). One existing test updated (CNY symbol). Frontend `tsc --noEmit` clean. |

Out of scope (said explicitly): the **screener** (US/HK/SG only — no A-share universe, cache job or VGPM cadence);
**crisis replay / what-if** home benchmark (A-share holdings fall to the US sector anchor; FMP has the SSE Composite
`000001.SS` but no CSI 300 history, and the event library would need calibrated China returns); B-shares, Beijing
SE, CDRs; Stock Connect eligibility; ST/*ST price limits; insider data (SSE/SZSE shareholding-change filings are not
fetched — FMP insider endpoint is US-only).

## Verified (tickers and numbers)

FMP probes, 2026-11-28 (quotes as of the 2026-11-27 close):

| Ticker | Name | FMP exch / ccy | Price | Mkt cap (RMB bn) | FMP industry |
|---|---|---|---|---|---|
| 600519.SS | Kweichow Moutai | SHH / CNY | 1,472.36 | 1,840.6 | Beverages - Alcoholic |
| 300750.SZ | CATL | SHZ / CNY | 215.89 | 999.0 | Electrical Equipment & Parts |
| 601318.SS | Ping An Insurance | SHH / CNY | 55.01 | 901.6 | Insurance - Diversified |

cninfo (live): Moutai FY2025 annual report filed 2026-04-17, latest periodic report Q3 2026 on 2026-10-31; CATL
FY2025 filed 2026-03-10, Q3 2026-10-21; Ping An FY2025 2026-03-27, interim titled `中期报告` 2026-08-21, Q3
2026-10-29 (its listed subsidiary Ping An Bank's reports, filed under the parent, are excluded); SMIC FY2025
2026-03-27.

**Engine smoke valuations** (deterministic engine via `wave_baseline`'s state builder, no research/LLM; temp
archive with prod comps; BEFORE the owner decisions: 4 variants = code at aa8e2970 / + CGB rf / + pins / both, CRP 0):

| Ticker | Variant | Profile | Spot | Bear / Base / Bull IV | 12m PT | WACC (Ke) | rf | Consensus |
|---|---|---|---|---|---|---|---|---|
| 600519.SS | current | Food & Beverage | 1,472.36 | 1,161 / 1,528 / 2,097 | 1,543 | 6.92% | 4.85% UST | 1,705 |
| 600519.SS | + CN rf | Food & Beverage | | 1,396 / 1,835 / 2,350 | 1,617 | 4.59% (floored) | 1.59% CGB | |
| 300750.SZ | current | Capital Goods (industry map) | 215.89 | 288 / 377 / 466 | 311 | 8.24% | 4.85% | 402.36 |
| 300750.SZ | + pins | Battery & Energy Storage | | 261 / 291 / 331 | 267 | 8.90% | 4.85% | |
| 300750.SZ | + both | Battery & Energy Storage | | 261 / 291 / 331 | 259 | 5.64% | 1.59% | |
| 601318.SS | current | Insurance (P&C) (industry map) | 55.01 | 94 / 117 / 144 | 89.7 | 6.26% | — | 75.09 |
| 601318.SS | + pins | Insurance (life) | | 34 / 45 / 57 | 54.0 | 6.06% | — | |

Multiples used are the live A-share baskets: Moutai on SHH `Beverages - Alcoholic` (P/E 27.3x n=7, EV/EBITDA 18.6x
n=6, NTM P/E 20.8x n=5); CATL on SHZ `Electrical Equipment & Parts` large-cap cohort (P/E 26.7x n=10, NTM EV/EBITDA
9.1x n=7); Ping An on the SHH `Financial Services` sector rung (P/B 0.82x n=19). Rating benchmark: CSI 300 at 9.26%
before the decisions (UST rf, CRP 0); 7.50% after.

Readings: (1) on US rates the DCF leg discounts RMB cash flows at a Treasury-based rate 3.3pp above the CGB — the
same mismatch the SG audit removed; (2) Ping An routed by FMP label to the **P&C** profile (GGM on book) gives 117 vs
55 spot; on the H-share's life-insurer pin its Embedded Value leg is *uncomputable* (no accepted EV input exists for
601318.SS — the accepted one is keyed `02318.HK`) and it falls to P/BV: 45. Neither is publishable as is (D-CN-6);
(3) CATL's routed Capital Goods table gives 377, its H-share pin 291, consensus 402.

**After the owner's decisions** (same harness, current branch; "before" = the `current` rows above):

| Ticker | Profile | Spot | Bear / Base / Bull IV | 12m PT | WACC / Ke | rf / CRP | Benchmark | Before (base IV / PT / WACC) |
|---|---|---|---|---|---|---|---|---|
| 600519.SS | Food & Beverage (pin) | 1,472.36 | 1,260 / **1,684** / 2,129 | **1,572** | 5.16% (Ke 5.16%, not floored) | 1.59% CGB / 1.5% | 7.50% | 1,528 / 1,543 / 6.92% |
| 300750.SZ | Battery & Energy Storage (pin) | 215.89 | 261 / **291** / 331 | **263** | 7.14% | 1.59% / 1.5% | 7.50% | 377 / 311 / 8.24% |
| 601318.SS | Insurance (life, pin) | 55.01 | **withheld** | **withheld** | (7.56% table) | 1.59% / 1.5% | 7.50% | 117 / 89.7 (P&C route) |

Ping An is published as **Unrated — awaiting an accepted company input**: "valuation withheld: a life insurer is
valued on embedded value and no embedded-value input has been accepted for 601318.SS (the accepted input for its
H-share 02318.HK is not reused across listings)." Its Embedded Value and P/E (ops) legs were uncomputable; the
surviving P/BV / DDM / P/B ex-AOCI legs (85% of weight) are kept only as audit figures (indicative base 44.87,
12m 52.92). With the 1.5% CRP the Moutai Ke (1.59% + 0.47 × 4.41% + 1.5% = 5.16%) clears the 4.59% floor; CATL's
blend has no DCF leg, so only its 12m target (carry) moves.

**Live pipeline run (1 of the 3 allowed, BEFORE the decisions; Qwen `qwen3.8-flash`, full research, temp archive), 600519.SS, 542 s:**
profile Food & Beverage, RMB throughout; IV bear / base / bull **1,081 / 1,358 / 1,610**, blended fair value
1,365.64, 12m target **RMB 1,484.08** vs RMB 1,472.36 (+0.8%, + 3.5% dividend = 4.3% TSR) → **Neutral** against
CSI 300 9.3% (−496 bp, 4 bp from Underweight — on the accepted CGB rf + 1.5% CRP the hurdle is 7.5% and the gap −320 bp, still Neutral).
Consensus (stockanalysis, 23 analysts) RMB 1,705. Research: 14k chars, the cninfo annual-report PDF cited 3 times
(`static.cninfo.com.cn`), revenue labelled RMB, sources included the China Alcoholic Drinks Association
(`cada.cc.cn`) and the company site; few Chinese-language sources were actually quoted (12 CJK characters), so the
"search in Chinese" instruction raised primary-source use but the synthesis stays English (expected). PDF rendered
from the run: 11 pages, title `Kweichow Moutai Co., Ltd. 贵州茅台 (600519.SS)` with an embedded Microsoft YaHei
subset (Windows host), all prices `RMB`. The other two allowed live runs were not used (the engine-only runs above
cost no LLM calls). Pre-existing, not China-specific: the macro-regime classifier failed to parse on qwen3.8-flash
and used its synthetic default.

## Accepted decisions (owner, 2026-11-28)

**D-CN-1 — bare codes:** the bare-digit prefix rule is removed; `canonical_ticker("600519") == "600519"` and
`"000660"` (SK Hynix) is untouched. `.SS/.SZ/.SH/.SHH/.SHZ` and `SH`/`SZ` prefixes still canonicalise. The landing
search offers `600519.SS` for a typed `600519` (`china.bare_code_candidate`, pick-list only).

**D-CN-2 — risk-free rate and floor** (`valuation_constants.risk_free_by_market.markets.CN`, read by the engine;
`reviewed.last_reviewed` 2026-11-28):

- `value 0.0159` — 10Y China Government Bond, ADB AsianBondsOnline market watch close 2026-11-26 **1.591%**
  (1.594% at the 2026-11-18 close; 2y 1.211%, 5y 1.329%). Same source the owner used for SGS.
- `min_spread_over_rf 0.03` — the SG precedent. Without it a 0.6-beta A-share gets Ke 1.59% + 0.6 × 4.41% = 4.2%;
  Moutai (beta 0.47) is floored at 4.59% in the smoke run.
- Market cross-check: SHH sector-level **NTM P/E median 13.5x** across 9 sectors (prod `regional_comps`
  2026-11-28: Fin 7.4, Materials 9.2, Energy 10.9, Industrials 12.0, Cons. Cyclical 13.5, Utilities 15.3,
  Cons. Defensive 18.3, Healthcare 23.7, Tech 33.3) = **7.4% earnings yield, 5.8pp over the CGB**. So the market's
  implied premium is above the US ERP of 4.41% the engine applies everywhere (D-CN-3).
- Wired: `dcf_agent` passes market `CN` to `capm.firm_wacc` / `risk_free`, the bank-g cap and the market CAPM.

**D-CN-3/4 — country risk premium and benchmark:** A-shares carry the market registry's **1.5%** (`MARKET_REGISTRY`
SHH/SHZ, read through `china.a_share_crp` → `sector_profiles.market_crp`) as the total CRP in the DCF's WACC build, so
it flows into the firm CAPM, the market CAPM and the CSI 300 benchmark alike; the no-CAPM fallback already used the
registry. HK-listed CNY reporters keep the currency row (CNY 0%, owner 2026-09-26). Benchmark = 1.59% + 4.41% + 1.50%
= **7.50%**. ERP stays the global 4.41% (implied China premium ~5.8pp, not adopted). No STAR 50 / ChiNext tech
benchmark: FMP carries neither index (nor CSI 300 history — `000300.SS` returns nothing).

**D-CN-5 — sector pins** (`china.CN_PROFILE_PINS` → `CN_TICKER_SECTOR_LOOKUP`, 46 names, accepted). Basis per row: 22 mirror the owner's existing **H-share pin** for the same issuer (a test
asserts they match), the rest the industry-map row or a stated judgement. Flagged inside: LONGi is labelled
"Semiconductors" by FMP (would route to IDM / Foundry) → pinned Clean Tech; Moutai Food & Beverage vs Luxury Goods;
Wanhua Specialty vs Commodity chemicals; Yangtze Power Regulated Utility needs a SHH cost-of-equity row (the HKSE
CGB-based 6.0% row is the obvious template) or its P/Rate Base leg declines. No pin for Hikvision,
Foxconn Industrial Internet, Luxshare (no archetype fits; limited coverage).

**D-CN-6 — Ping An:** routed to the life-insurer profile (`Insurance`, its H-share pin) and withheld through the
existing unrated path (new code `input_required`, `valuation_constants.UNRATED_REASONS`) whenever an A-share's profile
is `Insurance` and its Embedded Value leg did not compute (`china.ev_input_withhold_reason`). It lifts automatically
once an EV input is accepted for `601318.SS`. The same rule covers China Life, CPIC and New China Life (pinned
`Insurance`). The H line's accepted input is NOT reused across listings (the A/H `company_key` question stays open).

**D-CN-8 — fonts:** the helper is `src/utils/cjk_font.py` (already the merge's name). `docker/Dockerfile.web` and
`docker/Dockerfile.worker` now install `fonts-wqy-microhei` (TrueType: what ReportLab can embed) and
`fonts-noto-cjk` (CFF OpenType, as requested; ReportLab cannot embed it, so `cjk_font` uses WenQuanYi).

## Still open

- **China ERP** — implied ~5.8pp vs the global 4.41% (`capm.ERP` is one constant; a per-market ERP is a code change).
- **A/H company-level inputs** — should an A-share reuse its H line's accepted company facts (EV etc.)?
  `dual_listings.company_key` does this for HK/ADR pairs; A/H is not wired (Ping An stays withheld until then or
  until an A-line input is accepted).
- **D-CN-7 one A-share market or two.** SHH and SHZ are separate comps markets; baijiu peers split (Moutai/Fenjiu in
  SHH, Wuliangye/Luzhou Laojiao in SHZ). Pooling them into one A-share cohort is the owner's call (the one-market
  rule as written keeps them apart).
- **D-CN-9 SOE tagging.** `TOP_A_SHARES.ownership` is public-knowledge context for research only, unverified against
  the 2025 annual reports; it is not a valuation input. Keep, verify, or drop.

## A/H dual listings

31 pairs mapped, all verified by matching FMP company names (each line has its own ISIN). A-share premium over H at the 2026-11-27 close
(CNYHKD 1.17855): ICBC +41%, CCB +44%, China Life +82%, PetroChina +52%, China Mobile +60%, Ping An +26%, CMB −4%,
CATL −19%, SMIC +197%, Great Wall +161%. Rules held: each line is valued against its own price in its own currency;
`china.ah_premium` is display/disclosure only; research is told not to quote one line's price for the other. Note the
consequence of "each market owns its multiples": SHH/SHZ baskets are priced at A-share levels, so the relative legs of
the same company differ between lines by roughly the premium.

## Risks

- **Point-in-time:** FMP A-share filing dates are period ends; any backtest or freshness logic reading them is
  look-ahead-biased by 1-4 months. cninfo has the true dates.
- **Estimates:** zero-valued estimate rows (CATL 2030 eps 0) — treat 0 as missing wherever estimates are consumed.
- **Routing:** unpinned A-shares (outside the 46 pins) route by FMP label, which misroutes some names.
- **Image size:** `fonts-noto-cjk` adds roughly 100 MB to each image; only `fonts-wqy-microhei` is used by the PDF.
- **Bare codes:** a user who types `600519` and submits without picking the suggestion gets an unresolved ticker
  (by design, D-CN-1).
- **cninfo** is an unauthenticated public endpoint (no SLA, may rate-limit or change shape); every call is soft-fail
  and cached 6 h; a transport failure is never cached.
- **stockanalysis.com** consensus for A-shares relies on page scraping like HK/SG.
- **Price limits** (10% main boards, 20% STAR/ChiNext) and trading suspensions are not modelled in the 12m target.

## Shared files touched (for the Korea / Japan merges)

Python — `src/tools/ticker_canonical.py` (CN branch first in `canonical_ticker`; suffix/prefix forms only), `src/data/capm.py` (`_TAX["CN"]`),
`src/data/valuation_constants.py` (`market_key` SHH/SHZ), `src/data/valuation_constants.json`
(`risk_free_by_market.markets.CN` accepted, `reviewed` 2026-11-28), `UNRATED_REASONS["input_required"]`, `src/data/industry_profile_map.py` (`market_of` CN), `src/data/coverage.py`
(`CN_TICKER_SECTOR_LOOKUP`), `src/data/sector_profiles.py` (`get_wacc_profile_for_ticker` CN lookup),
`src/agents/analysis/dcf_agent.py` (3 market-code sites → `CN`; A-share CRP = registry; EV withhold in the unrated
path; `_consensus_at_run` defers `.SS/.SZ`),
`src/agents/industry/edgar_hkex_resolver.py` (cninfo branch), `src/agents/industry/deep_research.py` (CNINFO
citation/prompt branches, China research block, CNY symbol `RMB `), `src/memory/assumption_extract.py` (cninfo
channel), `src/memory/valuation_outcomes.py` (consensus labels for `.SS/.SZ`), `src/tools/earnings_calendar.py`
(per-symbol fallback), `src/tools/target_price.py` (`sha/`/`she/`, `¥`, UTF-8 decode), `src/utils/pdf_report.py`
(CJK `Paragraph`, Chinese name in title), `app/backend/routes/analysis.py` (CN local search, FMP stock-data path),
`app/backend/services/market_ask.py` (symbol rule, tier-1 domains, prompt).
Frontend — `pages/ReportPage.tsx` (`suggestionMarket` 'CN', `rankSuggestions`, `_hkCanonical` → `cnCanonical`),
`pages/HistoryPage.tsx` (CN filter), `lib/utils.ts` (CRLF file; `currencySymbol`, CNY `RMB `),
`components/report/FinancialStatements.tsx`, `pages/PortfolioPage.tsx`, `pages/ScreenerPage.tsx`, `i18n/zh.ts`,
`lib/reportTypes.ts` (`input_required` code). Docker — `docker/Dockerfile.web`, `docker/Dockerfile.worker` (CJK fonts).
Tests — `tests/test_m2_b_guards.py` (CNY symbol expectation `¥` → `RMB `). Docs — `docs/LAUNCH_TODO.md`. Likely conflict lines with Japan: the `CNY: …, JPY: '¥'` symbol-map lines.
