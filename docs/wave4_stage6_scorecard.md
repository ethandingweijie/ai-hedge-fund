# Wave 4 (Consumer Staples): Stage 6 scorecard

Before = Stage 0 (`docs/baselines/baseline_wave4.json`), after = the registry of 2026-09-26
(`docs/baselines/after_wave4.json`): eleven labels into `routing_scope`, six new rows, three new profiles
(Agribusiness & Food Processing, Grocery & Discount Retail, Tobacco), Food & Beverage and Household /
Personal re-anchored to Forward P/E, COST pinned Membership / Subscription Retail, WMT's pin moved,
EL kept on Luxury Goods. Universe 21 (owner: BF-B and 06808.HK cut, MDLZ kept).

| Ticker | Profile before -> after | Base IV before -> after | Consensus | IV vs cons before -> after | Backtest before -> after |
|---|---|---|---|---|---|
| ADM | Mature SaaS -> Agribusiness & Food Processing | 82.57 -> 78.69 | 88.00 | −6% -> −11% | passed -> fired |
| 00682.HK | Mature SaaS -> Agribusiness & Food Processing | 0.97 -> 1.21 | — | — | passed -> passed |
| F34.SI | Agribusiness & Food (SG) (unchanged) | 3.81 -> 3.81 | — | — | fired -> fired |
| KO | Luxury Goods -> Food & Beverage | 70.11 -> 62.94 | 95.75 | −27% -> −34% | passed -> fired |
| PEP | Apparel -> Food & Beverage | 160.24 -> 169.10 | 153.64 | +4% -> +10% | fired -> passed |
| BUD | Mature Platform -> Food & Beverage | 84.47 -> 91.54 | 90.75 | −7% -> +1% | passed -> passed |
| 00168.HK | Household -> Food & Beverage | 73.23 -> 73.97 | — | — | passed -> passed |
| MDLZ | Mature SaaS -> Food & Beverage | 41.07 -> 55.15 | 70.29 | −42% -> −22% | passed -> fired |
| TSN | Mature SaaS -> Agribusiness & Food Processing | 38.83 -> 51.74 | 67.40 | −42% -> −23% | fired -> passed |
| 00288.HK | Hyperscaler -> Food & Beverage | 22.82 -> 11.62 | — | — | fired -> passed |
| 00322.HK | Household -> Food & Beverage | 10.19 -> 10.81 | — | — | fired -> fired |
| WMT | Traditional Retail -> Grocery & Discount Retail | 65.37 -> 56.81 | 128.84 | −49% -> −56% | fired -> fired |
| KR | Levered Subscription -> Grocery & Discount Retail | 42.52 -> 60.24 | 69.64 | −39% -> −13% | fired -> fired |
| D01.SI | Levered Subscription -> Traditional Retail (SG market row) | 3.32 -> 1.83 | — | — | passed -> passed |
| COST | Hyperscaler -> Membership / Subscription Retail (pin) | 561.32 -> 510.11 | 1093.67 | −49% -> −53% | fired -> fired |
| PG | Apparel -> Household / Personal | 134.13 -> 135.17 | 157.78 | −15% -> −14% | fired -> passed |
| CL | Levered Subscription -> Household / Personal | 49.08 -> 69.45 | 98.11 | −50% -> −29% | fired -> passed |
| EL | Luxury Goods (unchanged, owner) | 34.55 -> 34.55 | 102.27 | −66% -> −66% | skipped -> skipped |
| PM | Mature Platform -> Tobacco | 134.76 -> 149.32 | 212.17 | −36% -> −30% | fired -> passed |
| MO | Mature Platform -> Tobacco | 61.35 -> 80.34 | 71.33 | −14% -> +13% | fired -> fired |
| USFD | Mature SaaS -> Grocery & Discount Retail | 59.48 -> 93.98 | 117.00 | −49% -> −20% | fired -> fired |

| Measure | Before | After | Bar |
|---|---|---|---|
| Within ±30% of consensus | 6 of 15 | **11 of 15 (73%)** | ≥ 70% |
| Methodology backtest passed | 8 of 23 | **10 of 21 (48%)** | ≥ before and ≥ 50% |
| Anchor missing from blend | 2 | 1 (00682.HK) | 0 |
| Bear ≤ Base ≤ Bull | all | all | all |
| Names on a staples profile | 3 of 23 | 20 of 21 | — |

The consensus bar clears for the first time in the programme. The backtest bar does not: 48% against
50%, though up from 35%.

## The four misses, each explained

- **WMT −56%.** Grocery & Discount Retail prices it on a 13x Discount Stores EBITDAR basket; the market
  pays ~38x earnings for Walmart's e-commerce and advertising re-rating. Recorded in the proposal as a
  quality premium no basket reaches; it is an owner decision (deviation record, or a premium factor),
  not a routing fix. Moving WMT to Membership / Subscription Retail would be the wrong economics.
- **COST −53%.** The owner's pin. Membership / Subscription Retail anchors on trailing P/E against a
  static 48x, and Costco trades at ~50x forward. The profile pre-dates the waves and has not been through
  one; its statics and anchor basis are the next question for it, not COST's pin.
- **EL −66%.** Unchanged and expected: a restructuring loss year through a P/E (norm) swap on trough
  earnings, and a backtest that cannot score a negative prior-year IV. The fix is the normalised margin
  the swap feeds, which is engine work outside this wave's routing scope.
- **KO −34%.** Forward P/E on the Non-Alcoholic basket (18.9x NTM) against a market paying ~23x for
  Coca-Cola. A quality premium of the same kind as WMT's, smaller; four points outside the band.

## What moved and why

Every name that left a SaaS or platform template moved toward consensus: MDLZ +20 points, TSN +19, CL
+21, KR +26, USFD +29, PM +6, MO +27 (now above). PEP and BUD cross to slightly above consensus on the
Forward P/E anchor, which is the NTM basket doing what the trailing one could not. ADM slips 5 points on
the normalised EBITDA anchor, as expected for a name near the top of its cycle; the backtest fires on it
now because the normalised leg reads the prior year differently.

00682.HK (Chaoda) still has no anchor in the blend: both normalised legs are uncomputable on its
history and P/BV alone carries it. D01.SI (Dairy Farm) lands on Traditional Retail through the SG market
row rather than Grocery & Discount Retail; a Grocery Stores row in `markets.SG` would move it, owner call.

## Consensus premium, recorded

Consensus targets for the 15 scored names sit a median +16% above spot (range +4% to +32%). Against spot
rather than consensus, the after-blend has 9 of 15 within ±30%. See `docs/consensus_skew_assessment.md`
for the programme-wide reading.

## Ship state

Registry, routing, families, WACC rows and pins are in the tree with tests
(`tests/test_consumer_staples_wave4.py`); COST golden re-recorded and re-based with the reason. Five
Consumer WACC rows are proposed values pending the owner's confirmation against the Damodaran file.
Pre-fill: none required (no SOTP profile in the wave). C4 KPI specs owed.
