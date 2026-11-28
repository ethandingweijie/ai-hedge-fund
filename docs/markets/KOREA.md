# Korea (KOSPI `.KS` / KOSDAQ `.KQ`) — market support

Status 2026-11-28, branch `worktree-agent-a7d0df3020660d8bc` (based on `main` 62feab5d). Nothing here was pushed or
deployed. All engine numbers are local runs on a temp sqlite archive seeded with the **production `regional_comps`
snapshot of 2026-11-28** (KSC 931 rows, refreshed 2026-11-28 02:56 UTC, comp age 0.3 days). The local store was 52
days stale, and any local Korean number taken without that seed would be priced on static tables.

Sections 1-7 below are the first pass (everything PROPOSED). The owner's decisions of 2026-11-28 — relayed by the
lead, not seen first-hand by this agent — are applied in **section 0**, which supersedes the proposals where they
differ.

---

## 0. Owner decisions applied (2026-11-28)

| # | Decision | Applied as |
|---|---|---|
| 1 | Bare 6-digit codes are never Korean | unchanged: `.KS/.KQ` or a `KRX:/KS:/KQ:/KOSPI:/KOSDAQ:` prefix required; `canonical_ticker("000660")` returns `000660` |
| 2 | KTB 10y 3.78% + 3.0pp floor | `valuation_constants.risk_free_by_market.markets.KR` (the `proposed` block is gone). Tax 26.4% stays proposed, unwired |
| 3 | Accept the sector pins | **36** pins (the first report said 37: a miscount) in `industry_profile_map.json` `ticker_overrides` (map v26); `korea.ACCEPTED_PROFILE_PINS` keeps the reasons; all 36 are TUNED for billing |
| 4 | CRP 0.50% everywhere | `country_risk_premium.by_currency.KRW` 0.70% → **0.50%**, now equal to `MARKET_REGISTRY["KSC"]["crp"]`. KOSPI hurdle = 3.78% + 4.41% + 0.50% = **8.69%** (was 10.05% on the UST) |
| 5 | Korean autos basket, else withhold | `PROFILE_PEER_BASKETS["Automotive (OEM)"]["KSC"]` (below); plus a Korea-wide rule: a weighted leg on the static default multiples withholds the valuation (`korea.static_multiples_verdict`, unrated path, code `no_market_multiples`) |
| 6 | Holdcos on their own measured discount, only with verified stakes | stakes NOT verifiable from DART here (no API key; press figures disagree, below). No templates added; the four holdcos are **withheld** (`korea.holdco_pending_verdict`, code `holdco_lookthrough_pending`) until a verified template exists |
| 7 | Samsung = segment SOTP | new profile `Semiconductor / Memory-led Tech Conglomerate (SOTP)`: SOTP (analyst) 0.50, P/E (norm) 0.30 anchor, EV/EBITDA 0.20, SOTP falls back to the memory legs; cyclical rules apply. Gemini `sotp` pre-fill built and **pending owner acceptance** in `industry_inputs.json`; comps-derived segment multiples below |
| 8 | Pool KOSDAQ into the KSC refresh | `regional_comps.MARKETS["KSC"] = ("KSC", "KOE")`; the read-only alias is now empty. Effect measured below |
| — | CJK fonts in the image | `fonts-nanum`, `fonts-ipafont-gothic`, `fonts-wqy-zenhei` in `docker/Dockerfile.web` and `Dockerfile.worker`, AFTER `poetry install`. Not `fonts-noto-cjk`: its OpenType/CFF outlines cannot be read by ReportLab's TTFont, so it would install ~100MB the PDF cannot use. `cjk_font.py` already looks for these three paths. The image build itself was not run here |

### Before / after (engine only, no LLM, FMP live 2026-11-28; KRW per share)

"Before" = first pass (prod comps snapshot, UST rf 4.94%, CRP 0.70%, no Korean pins). "After" = all eight decisions,
on a local KSC+KOE refresh (984 rows, 1,698 members, written to the temp archive only).

| Ticker | Spot | Before: profile / base IV / WACC | After: profile / base IV / WACC | What moved it |
|---|---|---|---|---|
| 005930.KS Samsung | 179,100 | Consumer Electronics / Hardware Ecosystem · 403,928 · 11.46% | Memory-led Tech Conglomerate (SOTP) · **withheld** (indicative 154,011) · 10.50% | no Korean basket for the archetype: P/E (norm) and EV/EBITDA would read the 18x / 12x literals (KSC has no Consumer Electronics basket; the cyclical profile blocks the sector rung), and the SOTP leg awaits the accepted `sotp` input |
| 000660.KS SK hynix | 1,110,000 | Memory / DRAM-NAND · 345,611 · 13.24% | same · **680,090** (bear 516,394 / bull 859,924; 12m 999,413) · 11.88% | rf + CRP alone: 345,611 → ~348,800 (in-memory test). The rest is KOSDAQ pooling: its KSC `Semiconductors` basket went from 7-10 KOSPI names (P/E 10.55x, EV/EBITDA 5.65x) to 18 (P/E 25.43x, EV/EBITDA 18.28x) — KOSDAQ HBM/equipment names, not memory makers. See D-new-1 |
| 005380.KS Hyundai Motor | 269,000 | Automotive (OEM) · 826,228 (static 18x / 2.0x / 12x) · 7.33% | same · 264,991 on the basket (12m 298,518), then **218,286** (12m 280,282) once common + preferred shares are counted · 6.78% (floor) | legs on the Korean autos basket (EV/EBITDA, P/E, P/BV); shares 203.3m → 262.8m (next section) |
| 015760.KS KEPCO | 26,150 | Regulated Utility · 48,365 · 5.94% | same · **33,027** · 6.78% (KTB + 3.0pp floor binds) | the floor and the Korean-curve cost of debt |
| 247540.KQ EcoPro BM | 92,000 | Capital Goods · 5,018 | Battery & Energy Storage · 18,576 | the pin |
| 196170.KQ Alteogen | 186,300 | Pre-approval Biotech · 45,829 | routed Commercial Biotech, then sent back to Pre-approval Biotech by the biopharma method layer · 45,240 | the pin is overridden downstream (D-new-3) |
| 035420.KS NAVER | 163,400 | Mature Platform · 274,652 | same · 297,698 | rf / CRP |
| 105560.KS KB Financial | 145,000 | Super-Regional Bank · 194,746 | Money Center Bank · 230,288 | the pin |
| 028260 / 402340 / 003550 / 034730 holdcos | — | various | **withheld** (look-through pending verified stakes) | decision 6 |

### Korean autos basket (decision 5) — derivation

Members (KSC store after the KSC+KOE refresh; Hyundai excluded as the subject): Kia 000270.KS, KG Mobility 003620.KS,
Hyundai Mobis 012330.KS, Hanon Systems 018880.KS, HL Mando 204320.KS, Hyundai Wia 011210.KS, Hankook Tire 161390.KS.
Why: KSC's `Auto - Manufacturers` label holds Hyundai, Kia, KG Mobility and LVMC Holdings (below the floor of 5),
so the OEMs are pooled with the large tier-1 suppliers that sell into them.

| Field | Kia | KGM | Mobis | Hanon | Mando | Wia | Hankook | **Median (n)** |
|---|---|---|---|---|---|---|---|---|
| P/E | 5.34 | −5.95 (out) | 9.13 | −10.53 (out) | 16.63 | 23.66 | 6.99 | **9.13x (5)** |
| NTM P/E | 5.03 | – | 7.19 | 7.89 | 7.05 | 8.14 | 7.56 | **7.38x (6)** |
| EV/EBITDA | 2.87 | 8.49 | 7.62 | 5.07 | 4.67 | 3.77 | 3.28 | **4.67x (7)** |
| NTM EV/EBITDA | 1.78 | – | 3.82 | 4.67 | 4.43 | 3.04 | 3.46 | **3.64x (6)** |
| P/B | 0.59 | 0.29 | 0.62 | 0.91 | 0.69 | 0.38 | 0.43 | **0.59x (7)** |

(Hyundai's own: P/E 9.32x, EV/EBITDA 12.2x — its EV carries the captive finance arm's debt, so its EV/EBITDA leg
comes out low at 146,639; the auto captive SOTP remains the proper fix, as proposed in D4.)

### KOSDAQ pooling (decision 8) — effect on the KSC medians

Universe 747 → 1,174 names; industries 102 → 116; stored rows 816 → 984. Of the 313 (key, cohort, field) cells
present both before and after (P/E, EV/EBITDA, P/B and their NTM forms), 119 are unchanged, 96 moved by more than
10%, median absolute move 3.4%; 71 cells are new (e.g. Biotechnology, Software - Application, Communication
Equipment, Semiconductors large cohort) and 8 dropped. The big movers (all-cohort, KSC-only → pooled):

| Basket | P/E | EV/EBITDA | P/B |
|---|---|---|---|
| Semiconductors (industry) | 10.55x (7) → **25.43x (18)** | 5.65x (8) → **18.28x (18)** | 1.34x → 4.10x |
| Technology (sector) | 14.31x → 22.94x | 7.38x → 15.54x | 1.42x → 3.11x |
| Healthcare (sector) | 9.87x → 20.04x | 8.20x → 16.83x | 0.84x → 3.75x |
| Biotechnology (industry) | 34.76x (5) → 36.61x (6) | 23.75x → 25.42x | 2.14x → 7.27x |
| Chemicals - Specialty | 9.78x → 23.86x | 10.16x → 10.21x | 0.52x → 0.81x |
| Auto - Parts | 6.99x → 5.83x | 4.25x → 3.68x | 0.42x → 0.42x |
| Consumer Cyclical, Industrials, Banks | within ±0.4x | ≈ | ≈ |

### Samsung segment SOTP (decision 7) — inputs

* **Gemini pre-fill** (grounded, Mirae Asset Securities note, FY2026E, **pending** acceptance; one check failed:
  SOTP / revenue 2.26x against a 0.6-1.6x plausibility band): DS revenue KRW 509.8tn at 75.3% EBIT margin, P/E 8-12x;
  DX 196.6tn at 4.1%, 10-12x; Samsung Display 30.4tn at 12.4%, 8-10x; Harman 17.5tn at 9.2%, 8-10x; net cash KRW
  167tn. The DS figures are the research's, and the 75% margin is what trips the check — review before accepting.
* **Comps-derived multiples** (KSC store after pooling) to set against the broker's ranges:
  DS (memory) — the only Korean memory peer, SK hynix, trades at P/E 4.18x / NTM 2.09x; the KSC `Semiconductors`
  large cohort (NTM P/E 18.69x, n=9) is equipment and HBM-supply names, not a memory basket.
  DX (handsets, TVs, appliances) — LG Electronics NTM P/E 9.02x; KSC `Hardware, Equipment & Parts` large NTM P/E
  11.83x (n=6). Display — LG Display NTM EV/EBITDA 2.68x (loss-making, no P/E). Harman — KSC `Auto - Parts` large
  NTM P/E 7.19x (n=7). The broker's 8-12x on DS sits far above the one Korean memory peer at the trough-of-multiple
  the market is paying for peak earnings; that gap is the owner's call at acceptance.

### Holdco stakes (decision 6) — why pending

Press reports citing DART disagree: SK Square's SK hynix stake is given as 20.0% (Edaily, 31 Jul 2026) and 20.5%
(Korean press, 2026), ~146.1m shares; SK Inc's stake in SK Square as 32.2% (I had used 31.55%). The DART OpenAPI
needs the key (D7), so no template was added. Once stakes are verified, each holdco's discount should be measured
the way section 4 D5 does it (market cap vs listed-stake GAV, net of parent debt), never set as a constant.

### Common + preferred share count (lead's data audit, 2026-11-28)

FMP's quote marketCap is inconsistent across Korean issuers with listed preferred shares: Samsung's is the common price
× common + preferred shares (6,511.9m = 5,709.6m + 802.4m), but Hyundai's (203.3m), LG Chem's (70.6m) and LG
Electronics' (162.3m) cover the common only, so the engine divided equity by common shares and inflated the
per-share value. Rule now (`korea.issuer_share_count`, applied in `dcf_agent` after the cross-check, before any owner
as-converted override): an issuer with any listed preferred line (common code ending 5 / 7 / 9 / K) is valued on
the SUM of each line's FMP `shares-float` outstanding shares (treasury stock excluded), `shares_source =
"common_plus_preferred"`, with a disclosure flag; the per-share value applies to the common and to each preferred
line (the preferred keeps its discount disclosure). Same inputs, rule off vs on:

| Ticker | Shares before → after | Base IV before → after | 12m target before → after | Note |
|---|---|---|---|---|
| 005930.KS Samsung | 6,511.9m (quote) → 6,511.9m | withheld either way (indicative 154,011) | — | FMP's count already included 005935 |
| 005380.KS Hyundai | 203.3m → **262.8m** (+ 005385 22.8m, 005387 34.3m, 005389 2.3m) | 264,991 → **218,286** (−17.6%) | 298,518 → 280,282 | the audit's 1.28 ratio confirmed |
| 005385.KS Hyundai 1st pref | 203.3m → 262.8m | 264,991 → 218,286 | 217,529 → 183,727 | valued per economic share, spot 142,200 |
| 034730.KS SK Inc | 54.5m → 55.1m (+ 03473K 0.57m) | withheld (holdco) | — | the audit's 1.34 ratio (73.1m statements) is mostly **treasury stock**, not preferred: outstanding is 54.5m common + 0.57m preferred |
| 000810.KS Samsung Fire | 39.8m → 42.8m (+ 000815 3.0m) | 449,957 → 440,366 (−2.1%) | 527,266 → 523,713 | statements' 49.1m also includes treasury stock; insurer legs read per-share book, so the move is smaller than the count |
| 051910.KS LG Chem | 71.1m → 78.3m | 327,640 → 318,088 | 281,263 → 277,618 | |
| 066570.KS LG Electronics | 162.3m → 179.5m | 205,527 → 192,883 | 193,442 → 186,460 | |

329180.KS HD Hyundai Heavy (statements 88.8m vs 104.9m outstanding, ratio 0.85) has no preferred line: the gap is
the shares issued for the Dec-2025 HD Hyundai Mipo merger, and the recency path already uses the current count.

**Tests after the decisions pass:** `tests/test_korea_market.py` 84 tests; full suite with `set -o pipefail`:
**7,265 passed, 80 skipped, 4 xfailed, 0 failed**. Goldens re-baselined for the constants hash only (14 names, every
value unchanged; reason in `tests/golden/CHANGELOG.md`); five profile-census tests moved 148 → 149 profiles.

### New owner questions raised by applying the decisions

* **D-new-1 SK hynix basket.** Pooling KOSDAQ doubled SK hynix's IV through the label basket (Semiconductors
  25.4x P/E). Proposal: a KSC memory basket for `Memory / DRAM-NAND`, or an XMKT memory set (SK hynix, Samsung,
  Micron, Kioxia, Nanya), so a memory maker is not priced on equipment names.
* **D-new-2 Samsung's group legs.** Even with the `sotp` input accepted, P/E (norm) and EV/EBITDA (50%) have no
  Korean basket, so Samsung stays withheld. It needs a basket for the archetype (e.g. SK hynix, LG Electronics,
  Samsung Electro-Mechanics, LG Innotek, LG Display) or the memory basket in D-new-1.
* **D-new-3 Alteogen.** The pin to Commercial Biotech is overridden by the biopharma method layer back to
  Pre-approval Biotech, so the accepted pin does not take effect for this name.
* **D-new-4 Store floor in won.** The comps universe floor (`MIN_MARKET_CAP` 300m) is applied in the listing
  currency, i.e. KRW 300m (~US$0.2m) for Korea, so the floor does nothing there; the top-20 / top-40 caps keep
  micro-caps out of the medians but not out of the universe.

---

## 1. What works now

| Area | State | Where |
|---|---|---|
| Ticker forms | Korean only with an explicit marker: `005930.KS`, `5930.KS` (zero-padded), `KRX:005930`, `KOSPI:` / `KOSDAQ:` / `KS:` / `KQ:` prefixes. `KRX:` covers both boards: a code in `KNOWN_LISTINGS` (~50 large caps + preferreds) takes its board, anything else `.KS`; a known code with the wrong suffix is moved to its board (`005930.KQ` → `005930.KS`). **A bare 6-digit code (`005930`, `000660`) is never claimed** — `canonical_ticker` returns it unchanged; it may be a China A-share (`000660` is SK hynix and a Shenzhen code), and the landing search dropdown lists both listings so the user picks one (lead's instruction, 2026-11-28) | `src/markets/korea.py`, `ticker_canonical.py` |
| Request guard | admits a 2-6 letter venue prefix (`KRX:005930`), still plain ASCII, one suffix | `request_guards.py` |
| Preferred shares | the line is separate (`005935.KS`, own price, no consensus page). The code's last character tells it apart (common ends in `0`; `5`/`7`/`9` = 1st/2nd/3rd preferred, `K` = new-type). Valued per ECONOMIC share like the common, with a disclosure flag giving the discount to the common | `korea.issuer_price_for_shares`, `dcf_agent` shares block |
| FMP symbols | `.KS` / `.KQ` are FMP's own symbols; no rewrite (`to_fmp_symbol` already passed them) | — |
| Landing search | Korean suggestions are enabled (were greyed out: `suggestionMarket` allowed US/HK/SG); `rankSuggestions` strips `.KS/.KQ` | `ReportPage.tsx` |
| History filter | `KR` market option | `HistoryPage.tsx` |
| Market questions | `.KS` / `.KQ` tickers named in an answer are kept | `market_ask._supported_symbol` |
| Portfolio | holdings canonicalise through `canonical_ticker`; listing currency KRW from FMP's profile, `KRWUSD` rate on FMP, so per-row KRW + USD totals work with no portfolio code change | — |
| Prices / currency | quote, 20y EOD history (5,000-row cap: 2006-08 onward), market cap all in KRW. `get_listing_currency` → KRW (no USD-quoted KRX lines, unlike SGX) | — |
| Currency symbol | web `currencySymbol()` → `₩` (was `$`); Excel family checklist `₩`; PDF keeps `KRW ` (Helvetica has no `₩`) and prints KRW / JPY / IDR / VND prices with **no decimals and thousands separators** (`KRW 1,110,000`, was `KRW 1110000.00`) | `utils.ts`, `valuation_workbook.py`, `pdf_report.py` |
| Comps | KOSPI names read the `KSC` cohort (fresh in prod). **KOSDAQ names (FMP exchange `KOE`) read nothing before this change** and fell to static tables; they now read `KSC` (lookup alias; the refresh universe is unchanged — see decision D3) | `regional_comps.LOOKUP_ALIASES`, `MARKET_REGISTRY["KSC"]` |
| Market resolution | `resolve_market("247540.KQ")` was `US` (suffix map had only `.KS`) — now `KSC`; the KOSPI rating benchmark now applies to KOSDAQ too | `sector_profiles._SUFFIX_TO_MARKET` |
| Owner constants | Korean tickers key `KSC` in `valuation_constants.market_key` (was `US`: a Korean utility could have read the US utility cost of equity, a USD rate on KRW cash flows) | `valuation_constants.py` |
| Consensus target | stockanalysis.com / S&P Global covers Korea (`krx/005930`: 36 analysts; `kosdaq/247540`: 21). Wired for outcome labels exactly as HK/SG (FMP has no Korean price-target consensus, `grades-consensus` empty too) | `target_price.py`, `valuation_outcomes.py` |
| Earnings calendar | FMP's bulk calendar is truncated at ~4,000 rows: the 2026-10-15 +30d window held 3,986 and **dropped Samsung (29 Oct) and SK hynix (27 Oct)**. A symbol missing from a truncated window now asks `/stable/earnings?symbol=` (cached per day). `latest_earnings_event` (freshness trigger) already worked per symbol | `earnings_calendar.py` |
| Filings (DART) | `korea_dart.get_dart_filing_refs` resolves the latest Business Report (사업보고서) through the DART OpenAPI; wired into `edgar_hkex_resolver` (Korean names used to go to SEC EDGAR and log "EDGAR CIK not found"); the UI phase label reads "DART". **Needs `DART_API_KEY`** (free, owner registers at opendart.fss.or.kr) — without it the run logs "DART: no API key configured" and uses FMP attribution, as SGX does | `src/markets/korea_dart.py` |
| Research prompt | a Korea block: search Korean-language sources (DART, KRX KIND, 한국경제, 매일경제, 연합인포맥스, 서울경제), use K-IFRS **consolidated (연결)** never separate (별도), units (조 = 1e12, 억 = 1e8), preliminary results (잠정실적), the preferred-line caveat, and the DART primary source when resolved | `korea.research_context` → `deep_research.py` |
| PDF Hangul | `src/utils/cjk_font.py` (market-neutral; Japan and China can reuse it): every Hangul / kana / Han run in Paragraph text is drawn in a CJK font — an embedded TrueType font when the host has one (Nanum Gothic / IPA Gothic / WenQuanYi on Debian, Malgun / MS Gothic / SimSun on Windows, or `CJK_FONT_PATH_<KR\|JP\|CN>`), else ReportLab's built-in CID fonts (HYGothic-Medium etc., not embedded). `pdf_report.Paragraph` is a thin subclass that applies it | `cjk_font.py`, `pdf_report.py` |
| Billing / coverage | `run_type` works on the suffixes unchanged: `000660.KS` → `new` (pinned before this work), `005930.KS` / `247540.KQ` → `limited`; covered → `covered` | tested |

### Statements, estimates, fiscal year (FMP, probed 2026-11-28)

* Income / balance / cash-flow statements: 5 years + quarterlies, `reportedCurrency` KRW, FY = December, filing date
  = period end (FMP stamps it). The statements are the **consolidated** K-IFRS figures (Samsung FY2025 revenue
  KRW 333.6tn = the consolidated figure). FMP offers no separate (parent-only) set, which is the right default.
* `weightedAverageShsOut` counts common + preferred (Samsung 6,737m in FY2025 vs 5,710m + 802m outstanding today).
* Analyst estimates: present (Samsung 17-18 analysts, SK hynix 22-32, EcoPro BM 10-17). **FY2028-2030 rows look
  inconsistent** (Samsung EPS 84,469 in FY2028 then 33,420 in FY2029) — the engine uses NTM/FY+1-2, but worth knowing.
* Growth endpoints exist for Korea (unlike HK). `news/stock`, revenue segmentation and price-target consensus are
  empty. Earnings-call transcripts exist for Samsung and SK hynix.
* KOSPI index `^KS11` has history from 2006 (5,000-row cap); `^KQ11` / `^KS200` are not on FMP.

---

## 2. Engine runs (no LLM; `build_state_from_lookups` + `run_dcf_agent`, FMP live, 2026-11-28)

Spot and IVs in KRW. "Profile" is what the name routes to TODAY (no Korean pins applied).

| Ticker | Profile (today) | Spot | Bear / Base / Bull IV | 12m target | WACC | Note |
|---|---|---|---|---|---|---|
| 005930.KS Samsung | Consumer Electronics / Hardware Ecosystem | 179,100 | 314,581 / 403,928 / 528,340 | 322,539 | 11.46% | FMP label "Consumer Electronics"; legs Forward P/E 0.40, EV/EBITDA 0.35, DCF 0.15, FCF yield 0.10 on the KSC **Technology sector** large cohort (no Consumer Electronics basket in KSC). Forward P/E leg 564,925 on NTM consensus EPS 73,690 |
| 005935.KS Samsung pref | same | 145,000 | — / 406,053 / — | 304,319 | 11.46% | **was 372,034** (8% below the common) before the share-count fix |
| 000660.KS SK hynix | Memory / DRAM-NAND (pinned) | 1,110,000 | 268,432 / 345,611 / 437,879 | 822,570 | 13.24% | normalised P/E 0.45, normalised EV/EBITDA 0.30, DCF 0.25: the cycle rules price mid-cycle earnings; consensus 2,957,152 is flagged **suspect** (+166%) |
| 035420.KS NAVER | Mature Platform | 163,400 | 222,691 / 274,652 / 335,769 | 242,964 | 11.64% | no KSC Internet basket; consensus 273,552 (29 analysts) |
| 005380.KS Hyundai Motor | Automotive (OEM) | 269,000 | 596,907 / 826,228 / 1,055,549 | 618,339 | 7.33% | **priced on static US-anchored literals** (P/E 18x, P/B 2.0x, EV/EBITDA 12x): KSC has no Auto - Manufacturers basket and the sector rung is blocked for cyclicals. See decision D4 |
| 247540.KQ EcoPro BM | Capital Goods | 92,000 | 5,018 / 5,018 / 11,906 | 53,191 | 9.18% | **crashed before** (TypeError in the P/E-normalisation flag, a Wave 9 bug that hit any margin-peak-gate profile with no 5y earnings); now values. Profile is wrong: proposed pin Battery & Energy Storage |
| 105560.KS KB Financial | Super-Regional Bank | 145,000 | 158,331 / 194,746 / 233,581 | 181,336 | 8.11% | bank GGM / RI / P/TBV |
| 028260.KS Samsung C&T | Long-Cycle E&C | 259,000 | 440,679 / 508,329 / 618,739 | 421,125 | 10.77% | valued as a builder; it is the Samsung holdco (D5) |
| 015760.KS KEPCO | Regulated Utility | 26,150 | 48,365 / 48,365 / 110,469 | 40,456 | 5.94% | |
| 196170.KQ Alteogen | Pre-approval Biotech | 186,300 | 34,372 / 45,829 / 52,894 | 127,650 | 10.18% | only the anchor fallback priced; proposed pin Commercial Biotech |

The large gaps to spot are mostly profile / basket problems (Hyundai, C&T, EcoPro BM, Alteogen) and the consensus
estimate path (Samsung's NTM EPS); they are listed as owner decisions below rather than tuned here.

### Effect of the PROPOSED KTB risk-free rate (in-memory acceptance only)

| Ticker | WACC as is → with KTB | Market Ke (rating hurdle) | Base IV as is → with KTB |
|---|---|---|---|
| 005930.KS | 11.46% → 10.30% | 10.05% → 8.89% | 403,928 → 404,781 |
| 000660.KS | 13.24% → 12.08% | 10.05% → 8.89% | 345,611 → 348,849 |
| 015760.KS KEPCO | 5.94% → 6.78% (**floor binds**: KTB + 3.0pp) | 10.05% → 8.89% | 48,365 → 32,810 |
| 033780.KS KT&G | 8.65% → 7.50% | 10.05% → 8.89% | 206,540 → 206,540 |
| 005935.KS | 11.46% → 10.30% | 10.05% → 8.89% | 406,053 → 406,907 |

(The local run had no FRED key, so "as is" used the 4.94% Damodaran fallback; production reads live DGS10, 4.94% on
FMP's treasury curve the same day.) KEPCO moves most: today it discounts on the US curve with a US-rate cost of debt;
with the KTB both move onto the Korean curve and the minimum spread lifts the WACC.

---

## 3. PROPOSED constants (with derivations)

**P1. Korea risk-free rate = 3.78% (KTB 10y)** — `valuation_constants.json` `risk_free_by_market.proposed.KR`.
Source: ADB AsianBondsOnline Republic of Korea page, close 2026-11-26: 10y 3.775%, 2y 3.655%, BOK base rate 3.25%.
Rounded like SG (2.286% → 2.29%). Basis: owner rule 2026-11-25, the rf matches the currency of the cash flows; Korean
listings report and quote in KRW. Effect: −116bp on every Korean Ke against the 4.94% UST.
*To accept:* move the `KR` object from `proposed` into `markets` (drop `"status"`). `capm.risk_free` ignores any row
marked PROPOSED, and has a test for it.

**P2. Korea minimum Ke / WACC spread over the KTB = 3.0pp** (same row). Carried over from the owner's SG floor, the
only precedent. With the 0.70% KRW country premium it binds below a levered beta of about 0.52 ((3.0 − 0.70) / 4.41):
KEPCO, KT, KT&G-type names. Measured: KEPCO WACC 6.78% under the floor.

**P3. Rating benchmark (KOSPI) expected return = 8.89%** = KTB 3.78% + ERP 4.41% (the house Damodaran implied ERP)
+ CRP 0.70% (owner's `country_risk_premium.by_currency.KRW`). This is what `ratings.resolve_benchmark` returns once
P1 is accepted — no separate constant is needed (owner D1, 2026-11-27: the hurdle is the valuation's own CAPM).
Today, on the US curve, it is 10.05%. Cross-check against the index itself (`^KS11`, FMP, price only, dividends
excluded — add roughly 1.5-2% for yield): 10y price CAGR 9.53% (1,978 → 4,916), 15y 6.48%, 20y 6.38%. The forward
8.89% sits between the 20-year and 10-year realised total returns.

**P4. CRP inconsistency to resolve.** `MARKET_REGISTRY["KSC"]["crp"]` = 0.50% (sovereign AA, "comparable to SG")
while the owner table `country_risk_premium.by_currency.KRW` = 0.70%. The firm CAPM uses 0.70%; the registry value
only feeds the ratings FALLBACK (no CAPM on the run) and the static sector WACC (KSC has none). Proposal: align the
registry to the owner's 0.70%. Not changed.

**P5. Korean statutory tax** (`korea.PROPOSED_STATUTORY_TAX`, not wired). 24% national top bracket + 10% local
surcharge = 26.4%; the 2025 amendment restored a 25% top bracket from FY2026 (27.5%). Today `capm._TAX` has no KR
row, so relevering and after-tax Kd use the US 21%, and `_company_tax_rate` falls back to US 21% only when a name has
no two filed years (rare). Owner to confirm the year before it is wired.

**P6. Sector pins for the top names** (`korea.PROPOSED_PROFILE_PINS`, 37 entries, each with its reason; every
profile verified to exist; none applied, none counted as tuned). Headline ones:
Samsung → Memory / DRAM-NAND (alternative: a segment SOTP), LGES / Samsung SDI / EcoPro BM → Battery & Energy Storage,
Samsung Biologics → CDMO / Life Science Tools (the WuXi Bio case), Alteogen → Commercial Biotech, NAVER / Kakao →
Mature Platform, Krafton → Media & Streaming, the four bank groups → Money Center Bank, Samsung Life → Insurance,
Samsung C&T / LG Corp / SK Inc / SK Square → Asian Holding Company (Look-Through), shipbuilders → Long-Cycle E&C,
Hanwha Aerospace / KAI → Defense Primes, KEPCO → Regulated Utility, SKT / KT → Telecom Carrier, KT&G → Tobacco.
*To accept a pin:* add it to `industry_profile_map.json` `ticker_overrides` (that also makes the name TUNED for
billing). The look-through holdco pins only route once a template exists (D5).

---

## 4. Owner decisions needed

* **D1** Accept P1 + P2 (KTB rf and floor) — moves every Korean WACC by about −1.2pp and the hurdle to 8.89%.
* **D2** CRP: align the registry 0.50% to the owner's 0.70% (P4).
* **D3** Pool KOSDAQ into the KSC refresh universe (as US pools NASDAQ/NYSE/AMEX)? Today KOSDAQ names READ the KSC
  (KOSPI) cohort but are not IN it. Pooling would deepen thin baskets (Biotechnology n=5-9, Semiconductors n=7-10)
  and change the medians every existing Korean valuation reads at the next Saturday refresh, so it is not done.
* **D4** Korean autos. KSC has no `Auto - Manufacturers` basket (Hyundai, Kia and KG Mobility are below the floor of
  5), the sector rung is blocked for cyclicals, so Hyundai prices on the branch literals P/E 18x, P/B 2.0x,
  EV/EBITDA 12x — US-anchored numbers in a market where the measured cohorts are far lower: KSC `Auto - Parts`
  (all) P/E 6.99x (n=17), EV/EBITDA 4.25x (n=17), P/B 0.42x (n=20); KSC `Consumer Cyclical` (large) P/E 7.34x,
  EV/EBITDA 4.73x, P/B 0.65x. Recomputing Hyundai's legs on the Auto - Parts medians: P/E 46,455 × 6.99 ≈ 324,700;
  P/B 443,488 × 0.42 ≈ 186,300; EV/EBITDA (16.83tn × 4.25 − 28.75tn net debt) / 203.3m ≈ 210,500 — against
  836,197 / 886,977 / 788,377 today. Options: (a) an `Autos (family)` in `INDUSTRY_FAMILIES` pooling Auto -
  Manufacturers + Auto - Parts (same market only, used only below the floor), (b) an owner XMKT basket (Hyundai, Kia,
  Toyota, Honda, GM, Ford, Stellantis...), (c) the auto captive SOTP with Hyundai Capital/Card inputs (net debt above
  includes the captive's borrowings, which also relevers beta to 2.84). Recommendation: (a) for the multiples plus (c).
* **D5** Chaebol holdcos: the holdco discount decides the answer and is not constant. Market-implied discount to
  LISTED-stake gross asset value (market caps 2026-11-28; stakes from public 2025 filings, **unverified here** —
  confirm from DART before use; GAV excludes unlisted businesses, brand royalties and parent net debt, so these
  understate the true discount):

  | Holdco | Market cap (KRW tn) | Listed-stake GAV | Implied discount | Stakes used |
  |---|---|---|---|---|
  | SK Square 402340 | 90.5 | 157.1 | **42%** | SK hynix 20.07% |
  | Samsung C&T 028260 | 42.0 | 90.0 | **53%** | SEC 5.01%, Samsung Biologics 43.06%, Samsung Life 19.34%, SDS 17.08% |
  | LG Corp 003550 | 14.1 | 20.2 | **30%** | LGE 33.67%, LG Chem 33.34%, LG Uplus 37.66%, LG H&H 34.03%, LG CNS 49.95% |
  | SK Inc 034730 | 26.0 | 43.6 | **40%** | SK Square 31.55%, SK Innovation 36.52%, SKT 30.57%, SK Biopharm 64.05% |

  30-53% — wider than the HK/SG range's middle, consistent with the Korea "conglomerate discount". No template was
  added to `holdco_sotp_templates.json`: each needs owner-verified stakes and an owner-set (or market-calibrated)
  discount. Until then the holdcos route to their FMP label (C&T: Long-Cycle E&C) or the look-through declines.
* **D6** Samsung's archetype: Memory / DRAM-NAND (cyclical normalisation, like SK hynix) or a segment SOTP.
* **D7** `DART_API_KEY`: register at opendart.fss.or.kr (free) and set it on the web + worker services; I did not
  create an account. Without it Korea runs on FMP attribution.
* **D8** PDF fonts in production: the `python:3.11-slim` image has no CJK fonts, so production uses the
  non-embedded CID fonts (Chrome / Edge / Preview substitute; Acrobat may ask for its Asian font pack). For embedded
  glyphs add `fonts-nanum` (Korean), `fonts-ipafont-gothic` (Japanese), `fonts-wqy-zenhei` (Chinese) to the
  Dockerfiles — place the `apt-get` AFTER `poetry install` so the dependency layer cache is not invalidated
  (railway_dep_drift incident) — or bundle an OFL TrueType file and set `CJK_FONT_PATH_KR`. Not changed here.
* **D9** Regulatory questions for counsel: added to `docs/LAUNCH_TODO.md` (questions only).

---

## 5. Research quality (Qwen) and smoke runs

Three LIVE runs, in-process `run_analysis_pipeline` with `PIPELINE_MODEL=qwen3.8-flash` (Qwen research + Qwen
structured phases), temp sqlite archive, prod comps snapshot, no `DATABASE_URL`, no `DART_API_KEY`. PDFs rebuilt
through `workbook_export_service.build_run_pdf` (the web export path) and read back with PyMuPDF.

| Run | Time | PM decision | Engine base IV / 12m target | Spot | Rating text | PDF | Workbook |
|---|---|---|---|---|---|---|---|
| 005930.KS | 647s | HOLD, PT KRW 334,141 | 412,289 / 327,165 | 179,100 | "Neutral vs KOSPI", hurdle 10.1% (US rf); capped at Neutral by the unreconciled-legs rule | 12 pages, 86 KRW amounts, no `$` price | 18 tabs, 43 KRW labels, no USD / `$` |
| 000660.KS | 498s | SELL (Underweight vs KOSPI), PT 908,695 | 498,309 / 908,860 | 1,110,000 | TSR −17.9% vs the index | 11 pages, KRW 1,110,000 formatting | built |
| 005935.KS (preferred) | 586s | HOLD, PT 198,261 | 212,929 / 197,666 | 145,000 | Neutral vs KOSPI | 12 pages; **MalgunGothic embedded, 70 Hangul characters drawn** (삼성전자, 영업손실, 억원 quoted from Korean sources); preferred flag present ("trades at a 19% discount to the common line") | built |

No "Default decision due to LLM failure" in any run. `edgar_hkex_resolver` logged "DART: no API key configured";
the macro-regime classifier exhausted its retries on qwen3.8-flash in the first run and used its synthetic default
(a Qwen parsing issue, not Korean-specific).

**Research quality (Qwen web search on Korean names) — weak.** Citations were mostly English secondary sources:
Samsung's run cited facebook.com (8), finance.yahoo.com (8), news.samsung.com, finance.biggo.com, x.com, trendforce,
reddit, binance, plus Mirae Asset Securities and Nikkei; 23 Hangul characters in 35k of research. SK hynix: DART 1,
Seoul Economic Daily 1. The preferred run: Maeil Business 1, 136 Hangul characters. The Korea prompt block reaches the
synthesis prompt, but the web-search queries themselves are English and name the English company. Recommendations
(not built): (1) the DART key (D7) gives a primary-source anchor and the Korean issuer name (`corp_name`); (2) add a
Korean-language query (`삼성전자 실적`, `SK하이닉스 잠정실적`) to the search phase using that name; (3) the source
tier list should demote social media for every market.

**Run-to-run variance.** The common and preferred Samsung runs produced base IVs of 412,289 and 212,929 for the same
issuer (the engine-only runs, which share inputs, give 403,928 and 406,053). The difference is research-driven: the
preferred run's research quoted revenue 235% above the filed figure and the books-divergence guard disabled its
numbers (`research_degraded`), which changed the forecast inputs. Worth an owner look before Korean names are sold:
a guard that fires changes the valuation by half.

### Tests

`tests/test_korea_market.py` (67 tests): ticker forms incl. bare-code rejection, preferred lines, request guard,
market resolution / KOSDAQ alias / benchmark, constants keying, PROPOSED rf ignored (and the status guard), market
code, consensus routing and won parsing, outcome labels, truncated earnings window, DART parsers / no-key skip /
preferred-through-common / resolver routing, research block, pins resolve and are not tuned, billing run types,
CJK markup, a Hangul PDF build, KRW price formatting, the web symbol and landing gate, the P/E-normalisation guard.
Full suite with `set -o pipefail`: 7,243 passed, 80 skipped, 4 xfailed, 1 failed —
`test_dashboard_skips_a_poisoned_legacy_row`, a microsecond timestamp comparison that passes on re-run (unrelated).

---

## 6. Risks / known gaps

* **Thin KSC baskets**: no Consumer Electronics, Internet Content & Information, Auto - Manufacturers, Life
  Insurance, Regulated Electric, Telecom or Tobacco basket (46 industry keys in KSC). Those names fall to the sector
  rung (non-cyclicals) or the static literals (cyclicals, D4).
* **Consensus estimates** beyond FY+2 look inconsistent on FMP for Korean large caps; forward legs use NTM.
* **Preferred lines**: IV per economic share equals the common's; the published target on the preferred line is
  therefore well above its own spot whenever the preferred discount is wide (Samsung pref trades 19% below the
  common). The flag discloses it; whether a preferred line should carry its own rating is an owner question.
* **News**: FMP has no Korean company news; the fast news tier covers HK/SG only, so Korean names get news through
  research and freshness searches (Qwen) only.
* **Screener / VGPM**: Korea is not in the screener or the VGPM caches (US/HK/SG). Out of scope.
* **Bare 6-digit codes**: this branch does not resolve them at all (Korea needs `.KS/.KQ` or a `KRX:`/`KS:`/`KQ:`/
  `KOSPI:`/`KOSDAQ:` prefix). The China branch maps bare 0xxxxx/3xxxxx → `.SZ` and 6xxxxx → `.SS`, so after the merge
  a bare `000660` would read as Shenzhen unless the owner decides bare digits belong to nobody (the search list
  chooses). Korea adds no rule that competes for them, so the merge has no conflict on this point.
* **Large KRW numbers**: per-share prices go to seven digits (SK hynix 1,110,000); `_fmt_fmp` writes KRW statement
  amounts as `₩333,605.9B` in the research prompt (billions, not trillions) — correct but not idiomatic.
* **DART untested live** (no key). Parsers and the request shape are unit-tested against the documented API.

---

## 7. Shared files touched (for the China / Japan merges)

| File | Change |
|---|---|
| `src/tools/ticker_canonical.py` | Korea branch after SG (`korea.to_canonical`) |
| `app/backend/request_guards.py` | `TICKER_RE` admits an optional `[A-Za-z]{2,6}:` prefix |
| `src/data/sector_profiles.py` | `MARKET_REGISTRY["KSC"]["exchanges"]` += `KOE`; `_SUFFIX_TO_MARKET` += `.KQ` |
| `src/data/regional_comps.py` | `LOOKUP_ALIASES = {"KOE": "KSC"}` merged into `_EXCHANGE_TO_MARKET` (refresh `MARKETS` unchanged) |
| `src/data/capm.py` | `market_code(ticker, base)` (→ `KR`); `_accepted()` — `risk_free` / `local_min_spread` skip PROPOSED rows |
| `src/data/valuation_constants.json` | `risk_free_by_market.proposed.KR` |
| `src/data/valuation_constants.py` | `market_key` → `KSC` for `.KS/.KQ` |
| `src/agents/analysis/dcf_agent.py` | 3 market-code sites use `capm.market_code`; preferred-line share count + flag; P/E-normalisation flag block re-guarded by `if _pe_norm_swaps:` and appended (generic Wave 9 bug) |
| `src/agents/analysis/holdco_sotp.py` | `_SUFFIX_CCY` += `.KQ` |
| `src/agents/industry/edgar_hkex_resolver.py` | Korea → DART branch |
| `src/agents/industry/deep_research.py` | DART refs kept out of the SEC branches; Korea research block; DART citation labels |
| `src/tools/target_price.py` | `krx/` + `kosdaq/` routes; `₩`/`¥` in the target regexes; UTF-8 decode |
| `src/tools/earnings_calendar.py` | per-symbol fallback when the bulk window is truncated (all markets) |
| `src/memory/valuation_outcomes.py` | Korean suffixes consult the stockanalysis consensus |
| `src/utils/pdf_report.py` | `Paragraph` subclass applying `cjk_markup`; zero-decimal prices for KRW/JPY/IDR/VND |
| `src/utils/valuation_workbook.py` | `₩` in the family-checklist symbol map |
| `app/backend/services/market_ask.py` | `.KS/.KQ` in `_supported_symbol` |
| `app/backend/routes/analysis.py` | `.KS/.KQ` in the two yfinance-suffix tuples of `/stock/{ticker}` |
| `app/frontend/src/lib/utils.ts` (CRLF kept) | `currencySymbol` → `₩` |
| `app/frontend/src/pages/ReportPage.tsx` | `suggestionMarket` 'KR'; `rankSuggestions` base regex |
| `app/frontend/src/pages/HistoryPage.tsx` | 'KR' market option |
| `app/frontend/src/lib/phaseLabels.ts` | DART venue pattern |
| `tests/conftest.py` | `DART_API_KEY` in `_SENSITIVE_ENV_KEYS` |
| `docs/LAUNCH_TODO.md` | Korea counsel questions (new section at the end) |
| *(decisions pass)* `src/data/industry_profile_map.json` | v26: 36 Korean `ticker_overrides` + a `_doc` line |
| `src/data/sector_profiles.py` | new profile `Memory-led Tech Conglomerate (SOTP)` (Semiconductor) |
| `src/agents/analysis/dcf_agent.py` | the new profile in both cyclical sets; Korean holdco / static-multiple verdicts in the unrated chain |
| `src/data/capm.py`, `src/data/report_families.py` | the new profile's industry beta row and report family |
| `src/data/regional_comps.py` | `MARKETS["KSC"] = ("KSC", "KOE")`; `LOOKUP_ALIASES` emptied; `PROFILE_PEER_BASKETS["Automotive (OEM)"]["KSC"]` |
| `src/data/valuation_constants.json` | KR rf in `markets`; KRW CRP 0.50% |
| `src/data/industry_inputs.json` | Samsung `sotp` pre-fill (pending; pure addition, LF kept) |
| `docker/Dockerfile.web`, `docker/Dockerfile.worker` | TrueType CJK font packages after `poetry install` |

New files: `src/markets/__init__.py`, `src/markets/korea.py`, `src/markets/korea_dart.py`, `src/utils/cjk_font.py`
(shared helper — China / Japan should import it rather than add their own), `tests/test_korea_market.py`,
`docs/markets/KOREA.md`. `src/markets/__init__.py` is a docstring only; if a sibling branch also creates it, keep
either.
