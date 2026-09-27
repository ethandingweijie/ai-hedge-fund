# Wave 7 (Technology and communications): Stage 0 baseline

Run 2026-09-27 on the engine at ea398f6 over the 49 names of the Stage 2 probe
(`docs/wave7_stage2_probe.md`), read-only; recorded in `docs/baselines/baseline_wave7.json`. None of the
fourteen labels is in `routing_scope`; the US mega-caps reach their profiles by pin, everything else by
the ratio ladder.

| Ticker | Routed profile today | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Backtest |
|---|---|---|---|---|---|---|---|
| NVDA | Fabless | P/E (yes) | 162.02 | 345.21 | -53% | -28% | fired / MARKET_CLOSER |
| AAPL | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 183.19 | 342.13 | -46% | -46% | fired / MARKET_CLOSER |
| GOOG | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 274.94 | 431.94 | -36% | -19% | passed / TIE |
| MSFT | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 667.49 | 556.42 | +20% | +29% | fired / MARKET_CLOSER |
| META | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 681.35 | 766.94 | -11% | -9% | passed / TIE |
| AVGO | Fabless | P/E (yes) | 203.36 | 509.61 | -60% | -42% | passed / TIE |
| MU | Memory / DRAM-NAND | P/E (norm) (yes) | 293.44 | 1,542.50 | -81% | -73% | passed / MODEL_CLOSER |
| AMD | Fabless | P/E (yes) | 142.22 | 602.59 | -76% | -77% | fired / MARKET_CLOSER |
| ASML | Equipment / EDA | P/E (yes) | 1,268.48 | 2,305.75 | -45% | -27% | passed / MODEL_CLOSER |
| INTC | IDM / Foundry | EV/EBITDA (yes) | 79.09 | 110.97 | -29% | -36% | fired / MARKET_CLOSER |
| ORCL | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 262.54 | 236.52 | +11% | +92% | fired / MARKET_CLOSER |
| PLTR | Growth SaaS | NRR-adj DCF (yes) | 17.95 | 183.42 | -90% | -91% | fired / MARKET_CLOSER |
| CRM | Mature SaaS | EPV (yes) | 226.88 | 271.91 | -17% | -3% | passed / MODEL_CLOSER |
| ADBE | Mature SaaS | EPV (yes) | 402.96 | 269.84 | +49% | +71% | passed / MODEL_CLOSER |
| NOW | Mature SaaS | EPV (yes) | 46.12 | 144.38 | -68% | -66% | fired / MARKET_CLOSER |
| CSCO | Mature Platform | DCF (FCF+) (yes) | 97.09 | 131.42 | -26% | -9% | fired / MARKET_CLOSER |
| QCOM | Fabless | P/E (yes) | 249.33 | 204.48 | +22% | +23% | fired / MARKET_CLOSER |
| TXN | Equipment / EDA | P/E (yes) | 177.71 | 325.00 | -45% | -36% | fired / MARKET_CLOSER |
| AMAT | Equipment / EDA | P/E (yes) | 296.85 | 666.30 | -55% | -39% | fired / MARKET_CLOSER |
| LRCX | Equipment / EDA | P/E (yes) | 190.50 | 375.94 | -49% | -40% | passed / MODEL_CLOSER |
| KLAC | Equipment / EDA | P/E (yes) | 145.93 | 225.50 | -35% | -22% | passed / MODEL_CLOSER |
| NFLX | Mature Platform | DCF (FCF+) (yes) | 61.95 | 91.56 | -32% | -13% | fired / MARKET_CLOSER |
| DIS | Travel & Dining | EV/EBITDA (yes) | 103.99 | 126.30 | -18% | -2% | fired / MARKET_CLOSER |
| T | Stable Growth | EV/EBITDA (yes) | 34.73 | 26.78 | +30% | +37% | fired / MARKET_CLOSER |
| VZ | Stable Growth | EV/EBITDA (yes) | 41.18 | 48.58 | -15% | -13% | passed / TIE |
| TMUS | Stable Growth | EV/EBITDA (yes) | 134.39 | 233.10 | -42% | -19% | fired / MARKET_CLOSER |
| ACN | IT Services | P/E (yes) | 333.25 | 203.59 | +64% | +89% | passed / MODEL_CLOSER |
| IBM | IT Services | P/E (yes) | 134.80 | 256.69 | -47% | -40% | fired / MARKET_CLOSER |
| CRWD | Cybersecurity / Mission-Critical SaaS | DCF (FCF+) (yes) | 21.66 | 233.92 | -91% | -91% | fired / MARKET_CLOSER |
| ANET | Mature Platform | DCF (FCF+) (yes) | 113.22 | 224.85 | -50% | -45% | passed / MODEL_CLOSER |
| 00700.HK | China Internet Platform | DCF (yes) | 511.24 | — | — | +17% | fired / MARKET_CLOSER |
| 00941.HK | Stable Growth | EV/EBITDA (yes) | 114.69 | — | — | +46% | fired / MARKET_CLOSER |
| 01810.HK | Consumer Electronics / Hardware Ecosystem | Forward P/E (yes) | 17.12 | — | — | -34% | fired / MARKET_CLOSER |
| 00981.HK | IDM / Foundry | EV/EBITDA (yes) | 70.33 | — | — | +11% | passed / MODEL_CLOSER |
| 09999.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 429.70 | — | — | +138% | passed / MODEL_CLOSER |
| 00728.HK | Stable Growth | EV/EBITDA (yes) | 9.41 | — | — | +114% | fired / MARKET_CLOSER |
| 00992.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 70.79 | — | — | +91% | fired / MARKET_CLOSER |
| 09888.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 115.71 | — | — | +36% | passed / MODEL_CLOSER |
| 00762.HK | Stable Growth | EV/EBITDA (yes) | 16.12 | — | — | +189% | fired / MARKET_CLOSER |
| 01347.HK | OSAT / Packaging | EV/EBITDA (yes) | 110.68 | — | — | +0% | passed / MODEL_CLOSER |
| 00020.HK | Mature SaaS | EPV (NO) | — | — | — | — | fired / MARKET_CLOSER |
| 02382.HK | Mature SaaS | EPV (yes) | 80.08 | — | — | +23% | fired / MARKET_CLOSER |
| 01024.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 69.13 | — | — | +130% | fired / MARKET_CLOSER |
| 00285.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 59.49 | — | — | +163% | fired / MARKET_CLOSER |
| Z74.SI | Telco / Infrastructure (SG) | EV/EBITDA (yes) | 2.74 | — | — | -36% | passed / TIE |
| V03.SI | Tech Manufacturing / EMS (SG) | Forward P/E (yes) | 19.82 | — | — | +19% | passed / MODEL_CLOSER |
| CJLU.SI | Mature Platform | DCF (FCF+) (yes) | 0.47 | — | — | -51% | fired / MARKET_CLOSER |
| AWX.SI | Tech Manufacturing / EMS (SG) | Forward P/E (yes) | 3.93 | — | — | -59% | passed / TIE |
| AIY.SI | Mature Platform | DCF (FCF+) (yes) | 105.93 | — | — | +1118% | fired / MARKET_CLOSER |

| Measure | Stage 0 |
|---|---|
| Within ±30% of consensus | 10 of 30 |
| Within ±30% of spot | 18 of 48 |
| Methodology backtest passed | 19 of 49 |
| Anchor missing from blend | 1 (00020.HK) |
| Median consensus spread | +16% |
| Growth_Inflection_Speculative | NVDA (+53%), ORCL (+73%) |

## What the baseline says

**Five structural causes, all measured on the per-leg traces (`$TEMP/w7_legs.log`).**

**1. Semiconductors anchor on trailing multiples in a forward market.** Fabless (P/E .35) and Equipment /
EDA (P/E .35) read the US `Semiconductors` cohort's trailing 40.2x P/E and 32.8x EV/EBITDA against
trailing earnings, while the market pays for the next two years: AMD −77% (trailing P/E leg 107, Forward
P/E cross-check 307, spot 631), AVGO −42%, ASML −27% (P/E leg 1,082 against a Forward P/E cross-check
of 1,672 and a spot of 1,744), AMAT −39%, LRCX −40%, KLAC −22%. TXN sits on Equipment / EDA by the
empty-pin ladder; it is an analog IDM.

**2. Memory is a cyclical profile in a super-cycle.** MU −73%: the P/E (norm) anchor takes the five-year
normalised net income ($7.0bn) at 42x = 262, while FMP consensus (24 analysts) carries FY2027 revenue of
$253bn and EPS of $158 against FY2025's $37bn and $7.65, and the market cap of $1.22tn is 6.7x that
forward earnings. The engine's normalisation says the cycle will revert; the market says the earnings
base moved. That is the energy normalisation-window question again, on a bigger scale, and the Forward
P/E cross-check (forward EPS times a trailing 40x cohort multiple = 6,366) shows that a forward figure
on a trailing multiple is meaningless for a cyclical at peak.

**3. Software anchors on the wrong earnings.** Mature SaaS anchors EPV (.30) on GAAP EBIT: ServiceNow's
EPV leg is 14 against a spot of 136 (−66% blended), while Adobe's DCF (37% FCF margin, 12% growth)
reaches 653 against 235 (+71%) in a market that prices AI disruption. Growth SaaS and Cybersecurity are
DCF-heavy at 25-30% growth with a 10x terminal revenue multiple from the static table: PLTR −91%, CRWD
−91% (the owner's full-SBC rule holds). The EV legs of every tech sub-type read the static 2024 table
(`_TECH_SUBTYPE_MULTIPLES`: Mature SaaS 22x EV/EBITDA, Hyperscaler 20x) and not the cohort.

**4. The growth premium saturates.** The relative-value legs are scaled by
`1 + sensitivity x (g − g_sector) / g_sector`, capped at 1.80 outside the high-multiple tech profiles.
Against cohorts whose average growth is 1-4% (HK internet −0.5%, HK gaming 1%, telecom 2%, IT services
4-6%) any ordinary growth rate saturates the cap: ACN +89% (every multiple x1.50 on a de-rated stock:
P/E 13.1x becomes 19.8x), NetEase +138%, Kuaishou +130%, BYD Electronic +163%, Lenovo +91%, iFAST
(x1.80 on every leg). The premium is a ratio to a small denominator, which is a constant nobody re-derived.

**5. The ladder is currency-blind.** A Tech name with `revenue_base` above 100 billion becomes a
Hyperscaler, and the comparison is made in the venue currency: Lenovo (HK$649bn), NetEase, Kuaishou and
BYD Electronic all clear it in HKD or CNY and take the Hyperscaler static table (20x EV/EBITDA, then the
1.8x premium: 36x). iFAST (AIY.SI, +1,118%) is a wealth platform whose client cash flows through free
cash flow; it fell to Mature Platform's DCF (FCF margin 82%) because the SG `Software - Application` row
that sends it to WealthTech & Specialty Financials (SG) is out of scope. China Unicom (+189%) and China
Telecom (+114%) are on the generic Stable Growth table whose EV/EBITDA anchor (4.7x cohort) and DCF
(reported FCF) both price a capex-heavy state carrier the market holds at 1-2x EBITDA.

**Where it lands.** META −9%, GOOG −19%, CRM −3%, CSCO −9%, NFLX −13%, DIS −2%, VZ −13%, TMUS −19%,
QCOM +23%, Tencent +17%, SMIC +11%, Hua Hong 0%, Venture +19%. The names that land are the pinned
mega-caps and the ones whose profile happens to fit.

## What Stage 1 (universe, ◆ owner) has to settle

The four questions in `docs/wave7_stage2_probe.md` section 4; the remap and the re-derived constants
are proposed in `docs/wave7_stage3_remap_proposal.md`, all PROPOSED, nothing applied.
