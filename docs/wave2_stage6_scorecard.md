# Wave 2 (power & transition): Stage 6 scorecard, before and after

Before: `docs/baselines/baseline_wave2.json` (2026-09-21, engine at 0f981f0). After: `docs/baselines/after_wave2.json` (2026-09-21, the Wave 2 commit). Same universe, same script (`scripts/wave_baseline.py`), local store, dynamic multiples on. No rate base is accepted yet, so `P/Rate Base` still prices through its P/BV proxy in every row below.

| Ticker | Profile before -> after | Base IV before -> after | Consensus | IV vs cons before -> after | Proxied wt | Surviving wt | Anchor in blend | Backtest before -> after |
|---|---|---|---|---|---|---|---|---|
| NEE | Regulated Utility | 53.71 -> 58.93 | 102.11 | -47% -> -42% | 50% -> 39% | 0.4 -> 0.9 | NO -> yes | fired -> fired |
| DUK | Regulated Utility | 132.67 -> 118.90 | 135.33 | -2% -> -12% | 50% -> 39% | 0.4 -> 0.9 | NO -> yes | fired -> passed |
| SO | Regulated Utility | 77.36 -> 66.75 | 97.00 | -20% -> -31% | 50% -> 39% | 0.4 -> 0.9 | NO -> yes | passed -> fired |
| 00002.HK | Mature SaaS -> **Regulated Utility** | 55.80 -> 44.00 | - | - -> - | 0% -> 35% | 0.7 -> 1.0 | yes -> yes | passed -> fired |
| 00006.HK | Mature Platform -> **Regulated Utility** | 20.95 -> 33.52 | - | - -> - | 0% -> 35% | 0.9 -> 1.0 | yes -> yes | fired -> fired |
| VST | Merchant Power | 78.26 -> 101.34 | 217.45 | -64% -> -53% | 0% -> 0% | 0.7 -> 0.85 | yes -> yes | passed -> passed |
| CEG | Mature SaaS -> **Merchant Power** | 90.04 -> 124.00 | 354.75 | -75% -> -65% | 0% -> 0% | 0.95 -> 1.0 | yes -> yes | passed -> passed |
| NRG | Levered Subscription -> **Merchant Power** | 108.73 -> 123.11 | 202.90 | -46% -> -39% | 25% -> 0% | 0.4 -> 0.85 | NO -> yes | passed -> passed |
| ENPH | IPP -> **Clean Tech / Power Equipment OEM** | 24.66 -> 29.71 | 44.23 | -44% -> -33% | 32% -> 0% | 0.95 -> 1.0 | yes -> yes | passed -> passed |
| FSLR | IPP -> **Clean Tech / Power Equipment OEM** | 209.02 -> 224.05 | 271.94 | -23% -> -18% | 32% -> 0% | 0.95 -> 1.0 | yes -> yes | fired -> fired |
| NXT | Early Platform -> **Clean Tech / Power Equipment OEM** | 168.09 -> 128.34 | 147.02 | +14% -> -13% | 50% -> 0% | 1.0 -> 1.0 | yes -> yes | passed -> passed |
| CCJ | Mature Platform | 16.07 -> 16.07 | 135.00 | -88% -> -88% | 0% -> 0% | 0.9 -> 0.9 | yes -> yes | passed -> passed |
| LEU | Upstream Oil & Gas | 64.93 -> 64.93 | 223.78 | -71% -> -71% | 0% -> 0% | 1.0 -> 1.0 | yes -> yes | fired -> fired |
| BE | IPP -> **Clean Tech / Power Equipment OEM** | 8.39 -> 46.55 | 285.40 | -97% -> -84% | 30% -> 0% | 1.0 -> 0.65 | yes -> NO | fired -> passed |
| GEV | Capital Goods | 316.62 -> 316.62 | 1266.38 | -75% -> -75% | 0% -> 0% | 1.0 -> 1.0 | yes -> yes | fired -> fired |
| SMR | Aerospace & Defense -> **Energy Tech Licensor** | 3.61 -> 3.90 | 11.25 | -68% -> -65% | 0% -> 25% | 0.0 -> 0.2 | NO -> NO | fired -> fired |
| 01816.HK | Merchant Power -> **Regulated Utility** | -3.24 -> 2.49 | - | - -> - | 0% -> 39% | 0.0 -> 0.9 | NO -> yes | fired -> fired |

| | Before | After | Bar |
|---|---|---|---|
| Within +/-30% of consensus | 4 of 14 | 3 of 14 | >= 70% |
| Methodology backtest passed | 8 of 17 | 8 of 17 | >= before and >= 50% |
| Anchor missing from the blend | 6 | 2 | 0 |
| Mean weight on proxied legs | 19% | 15% | - |
| Bear <= Base <= Bull | 17 of 17 | 17 of 17 | all |


## Verdict: the plumbing is fixed; the consensus bar is NOT cleared

What improved, all of it structural: no name in the universe is valued on a
software profile any more (CLP, Constellation, NRG, Nextracker were); the
regulated utilities price on 0.90 of their intended weight instead of 0.40;
CGN Power has a positive value (HK$2.49, was -3.24); weight on mislabelled
legs fell; every re-routed name moved TOWARD consensus. Anchor missing: 6 -> 2,
both genuine (below).

What did not: 3 of 14 within +/-30% of consensus against a 70% bar, and the
backtest at 8 of 17 (47%) against 50%. By the plan's own rule that is a wave
that does not ship live on its scorecard. The decision is the owner's.

## Every outlier, explained

- **The AI-power trade (VST -53%, CEG -65%, NRG -39%, BE -84%, GEV -75%, CCJ -88%,
  LEU -71%, SMR -65%): 8 of the 11 misses.** These are 12-month targets on names
  the market has re-rated on data-centre load growth. The US IPP basket's
  normalised EV/EBITDA reads 18.1x "market now" against an 11.1x through-cycle;
  the engine prices at the through-cycle figure by design. No mid-cycle method
  set reaches these targets, and tuning multiples until it did would be the
  multiple-chasing the through-cycle work exists to prevent. This is the same
  open deviation Wave 1 recorded for the refiners, in the opposite direction.
- **CCJ, LEU: unchanged, by construction.** `Uranium` has no row: the owner's
  primary method (P/NAV at a long-term contract price) needs inputs the engine
  does not have, and the synthetic EV/EBITDA formula did not survive the paste.
  They remain on Mature Platform and Upstream Oil & Gas until that profile exists.
- **GEV: unchanged, by decision.** Observation-only until Wave 3's
  backlog-coverage DCF exists; its accepted backlog ($176bn, pending review) is
  the input that leg needs.
- **BE (-97% -> -84%): anchor dropped.** No positive through-cycle EBITDA, so
  EV/EBITDA (norm) cannot price and the blend runs on Forward P/E, DCF and
  EV/Revenue (0.65 of intended weight). Consensus $285 is roughly 20x sales; the
  OEM profile's 2.8x is the basket's. The owner's own argument -- an OEM cost
  structure "cannot organically sustain" licensor multiples -- is what the
  number says.
- **SMR: unrated in substance.** Pre-revenue; 0.20 of intended weight survives,
  on a forward-revenue multiple alone. The engine has no "unrated" state, so it
  still publishes a number ($3.90). That state is needed for Wave 5's
  pre-revenue biotech too and should be built once, there.
- **NEE -42%, SO -31% (DUK -12% is inside).** Peer-median P/E (19.8x) and P/B
  (2.0x) against names that trade at a premium to the basket; consensus targets
  are also 12 months forward. SO moved OUT of the band (-20% -> -31%) because its
  old number was 60% short of weight and happened to land close; the new one is
  lower and better founded. NEE's accepted rate order will not close the gap:
  at the owner's 7.25% cost of equity the justified multiple on FPL's equity
  layer is about 1.8x, close to the 2.0x proxy it replaces.
- **No HK consensus**: CLP, Power Assets and CGN return no usable target (see
  `consensus_target_unusable_rules`), so they are outside the 14.

## Owner decision, 2026-09-21: ship the structural fixes; the scorecard gate is overridden

> "Accept the Structural Fixes, Override the Scorecard Gate: the architectural cleaning of profiles, removal
> of software templates for utilities, and cleaner blend weights are massive quality improvements."

Wave 2 ships live. The consensus bar was not cleared and is not claimed to have been.

**The AI-power trade is isolated as a structural regime deviation**, recorded in
`valuation_constants.json` (`regime_deviations.ai_power`): VST, CEG, NRG, BE, GEV, CCJ, LEU, SMR. It changes no
number. Every run of those names carries the note on all three scenarios -- through-cycle by design, gap recorded
and not tuned away -- and the scorecard counts the regime once instead of eight times:

| | Before | After |
|---|---|---|
| Within +/-30% of consensus, all names | 4 of 14 | 3 of 14 |
| The AI-power regime (8 names) | 0 of 8 | 0 of 8 |
| **Ex regime** (NEE, DUK, SO, ENPH, FSLR, NXT) | 4 of 6 | 3 of 6 |

Ex regime the wave still sits below the 70% bar: SO (-31%) and ENPH (-33%) are just outside the band and NEE
(-42%) well outside, for the reasons above. That is stated rather than rounded away.

**Carried forward, at the owner's priority:** GEV's backlog-coverage DCF (its $176.3bn backlog is pre-filled and
pending review), and an explicit Unrated / Pre-Revenue state so SMR stops publishing a fractional-weight number.
