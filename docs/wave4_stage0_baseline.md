# Wave 4 (Consumer Staples): Stage 0 baseline

Run 2026-09-26 on the current engine (the SOTP change set of the same day in place), over the 23
names the Stage 2 probe identified (`docs/waves_2_5_stage2_probe.md`, W4). Read-only; recorded in
`docs/baselines/baseline_wave4.json`. Every later Wave 4 change is judged against this table.

| Ticker | Routed profile today | Anchor (in blend) | Base IV | Consensus | IV vs cons | Backtest |
|---|---|---|---|---|---|---|
| ADM | Mature SaaS | EPV (yes) | 82.57 | 88.00 | −6% | passed / MODEL_CLOSER |
| 00682.HK | Mature SaaS | EPV (NO) | 0.97 | — | — | passed / MODEL_CLOSER |
| F34.SI | Agribusiness & Food (SG) | Forward P/E (yes) | 3.81 | — | — | fired / MARKET_CLOSER |
| KO | Luxury Goods | P/E (Premium) (yes) | 70.11 | 95.75 | −27% | passed / TIE |
| PEP | Apparel / Athletic Wear | EV/EBITDA (yes) | 160.24 | 153.64 | +4% | fired / MARKET_CLOSER |
| BUD | Mature Platform | DCF (FCF+) (yes) | 84.47 | 90.75 | −7% | passed / MODEL_CLOSER |
| BF-B | Mature Platform | DCF (FCF+) (yes) | 18.50 | — | — | passed / MODEL_CLOSER |
| 00168.HK | Household / Personal | P/E (yes) | 73.23 | — | — | passed / TIE |
| MDLZ | Mature SaaS | EPV (yes) | 41.07 | 70.29 | −42% | passed / MODEL_CLOSER |
| TSN | Mature SaaS | EPV (yes) | 38.83 | 67.40 | −42% | fired / MARKET_CLOSER |
| 00288.HK | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 22.82 | — | — | fired / MARKET_CLOSER |
| 00322.HK | Household / Personal | P/E (yes) | 10.19 | — | — | fired / MARKET_CLOSER |
| WMT | Traditional Retail | EV/EBITDAR (yes) | 65.37 | 128.84 | −49% | fired / MARKET_CLOSER |
| KR | Levered Subscription | DCF (Levered) (yes) | 42.52 | 69.64 | −39% | fired / MARKET_CLOSER |
| 06808.HK | Household / Personal | P/E (NO) | 2.75 | — | — | fired / MARKET_CLOSER |
| D01.SI | Levered Subscription | DCF (Levered) (yes) | 3.32 | — | — | passed / MODEL_CLOSER |
| COST | Hyperscaler / Tech Conglomerate | EV/EBITDA (yes) | 561.32 | 1093.67 | −49% | fired / MARKET_CLOSER |
| PG | Apparel / Athletic Wear | EV/EBITDA (yes) | 134.13 | 157.78 | −15% | fired / MARKET_CLOSER |
| CL | Levered Subscription | DCF (Levered) (yes) | 49.08 | 98.11 | −50% | fired / MARKET_CLOSER |
| EL | Luxury Goods | P/E (Premium) (yes) | 34.55 | 102.27 | −66% | skipped / UNSCORABLE |
| PM | Mature Platform | DCF (FCF+) (yes) | 134.76 | 212.17 | −36% | fired / MARKET_CLOSER |
| MO | Mature Platform | DCF (FCF+) (yes) | 61.35 | 71.33 | −14% | fired / MARKET_CLOSER |
| USFD | Mature SaaS | EPV (yes) | 59.48 | 117.00 | −49% | fired / MARKET_CLOSER |

| Measure | Stage 0 |
|---|---|
| Within ±30% of consensus | 6 of 15 |
| Methodology backtest passed | 8 of 23 |
| Anchor missing from blend | 2 (00682.HK, 06808.HK) |
| Bear ≤ Base ≤ Bull | 23 of 23 |
| Errors | 0 |

## What the baseline says

**Almost nothing here is on a staples profile.** Of 23 names, three land on a profile written for
their business (F34.SI on Agribusiness & Food (SG); 00168.HK and 00322.HK on Household / Personal).
The rest route by the LLM classifier or the fallback: five packaged-food and distribution names on
Mature SaaS, valued on earnings power with an LBO floor; KO and EL on Luxury Goods; PEP and PG on
Apparel; COST and 00288.HK on Hyperscaler / Tech Conglomerate; the tobacco and brewer names on
Mature Platform; grocers on Levered Subscription. The misses follow the routing, not the arithmetic:
every name valued on a SaaS or platform template sits 36% to 66% under consensus.

**The two names with no anchor in the blend** are both HK: 00682.HK (Chaoda, four legs uncomputable,
IV HK$0.97) and 06808.HK (Sun Art, P/E uncomputable on a loss year). Both need a profile whose anchor
is a balance-sheet or revenue leg.

**Where the engine is already close** it is the trailing-multiple names: ADM (−6%), PEP (+4%), BUD
(−7%), MO (−14%), PG (−15%). None of those is on a staples profile either; they are close because a
generic EV/EBITDA or DCF on a stable cash generator lands near the market whatever the label says.

**Consensus is unusable on 8 of 23**, all HK and SG, as recorded for the earlier waves.

## What Stage 1 (universe, owner checkpoint) has to settle

1. Keep or cut from the probe list: BF-B and MDLZ carry thin FMP labels (3 members each in every
   market) and no consensus for BF-B; 06808.HK is Consumer Cyclical by FMP label and override-only.
2. COST is a golden fixture and shares `Discount Stores` with WMT: any row for that label must ship
   with a COST pin in the same commit (probe finding, unchanged).
3. Tobacco routes to Household today. Its own profile or a Household sub-row is a taxonomy decision.

Stage 3 (methods) proposal follows the checkpoint, on the owner's framework for staples: the probe
already records which labels are thin per market and where a family pooling is required.
