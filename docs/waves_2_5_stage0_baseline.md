# Wave 2 (power & transition): Stage 0 baseline

Run 2026-09-21 with `scripts/wave_baseline.py --wave 2`, current engine, local store, dynamic multiples on (the local table was populated the same day; production's switch-on is Step C). Raw record: `docs/baselines/baseline_wave2.json`. This is the "before" every Wave 2 change is judged against.

| Ticker | Routed profile | Anchor (in blend?) | Proxied wt | Dropped legs | Surviving wt | Base IV | Consensus | IV vs cons | 12m vs cons | Backtest |
|---|---|---|---|---|---|---|---|---|---|---|
| NEE | Regulated Utility | DCF (**no**) | 50% | DCF 60% (non_positive) | 0.4 | 53.71 | 102.11 | -47% | -35% | fired / MARKET_CLOSER |
| DUK | Regulated Utility | DCF (**no**) | 50% | DCF 60% (non_positive) | 0.4 | 132.67 | 135.33 | -2% | -9% | fired / MARKET_CLOSER |
| SO | Regulated Utility | DCF (**no**) | 50% | DCF 60% (non_positive) | 0.4 | 77.36 | 97.00 | -20% | -15% | passed / TIE |
| 00002.HK | Mature SaaS | EPV (yes) | 0% | DCF (2-stage) 25% (non_positive); LBO Floor 5% (uncomputable) | 0.7 | 55.80 | - | - | - | passed / TIE |
| 00006.HK | Mature Platform | DCF (FCF+) (yes) | 0% | LBO Floor 10% (uncomputable) | 0.9 | 20.95 | - | - | - | fired / MARKET_CLOSER |
| VST | Merchant Power | EV/EBITDA (yes) | 0% | Power Price DCF 20% (non_positive); LBO Floor 10% (uncomputable) | 0.7 | 78.26 | 217.45 | -64% | -50% | passed / TIE |
| CEG | Mature SaaS | EPV (yes) | 0% | LBO Floor 5% (uncomputable) | 0.95 | 90.04 | 354.75 | -75% | -52% | passed / TIE |
| NRG | Levered Subscription | DCF (Levered) (**no**) | 25% | DCF (Levered) 40% (non_positive); LBO Analysis 20% (uncomputable) | 0.4 | 108.73 | 202.90 | -46% | -49% | passed / MODEL_CLOSER |
| ENPH | IPP | PPA-backed DCF (yes) | 32% | DDM 5% (uncomputable) | 0.95 | 24.66 | 44.23 | -44% | -31% | passed / MODEL_CLOSER |
| FSLR | IPP | PPA-backed DCF (yes) | 32% | DDM 5% (uncomputable) | 0.95 | 209.02 | 271.94 | -23% | -26% | fired / MARKET_CLOSER |
| NXT | Early Platform | GMV-TAM Pen (yes) | 50% | - | 1.0 | 168.09 | 147.02 | +14% | -15% | passed / MODEL_CLOSER |
| CCJ | Mature Platform | DCF (FCF+) (yes) | 0% | LBO Floor 10% (uncomputable) | 0.9 | 16.07 | 135.00 | -88% | -60% | passed / MODEL_CLOSER |
| LEU | Upstream Oil & Gas | EV/OCF (yes) | 0% | - | 1.0 | 64.93 | 223.78 | -71% | -52% | fired / MARKET_CLOSER |
| BE | IPP | PPA-backed DCF (yes) | 30% | - | 1.0 | 8.39 | 285.40 | -97% | -52% | fired / MARKET_CLOSER |
| GEV | Capital Goods | EV/EBITDA (yes) | 0% | - | 1.0 | 316.62 | 1266.38 | -75% | -50% | fired / MARKET_CLOSER |
| SMR | Aerospace & Defense | EV/EBITDA (**no**) | 0% | EV/EBITDA 40% (uncomputable); Backlog DCF 30% (uncomputable); FCF Yield 20% (uncomputable); P/E 10% (uncomputable) | 0.0 | 3.61 | 11.25 | -68% | -45% | fired / MARKET_CLOSER |
| 01816.HK | Merchant Power | EV/EBITDA (**no**) | 0% | EV/EBITDA 40% (non_positive); FCF Yield 30% (uncomputable); Power Price DCF 20% (non_positive); LBO Floor 10% (uncomputable) | 0.0 | -3.24 | - | - | - | fired / MARKET_CLOSER |

**Scorecard before:** 4 of 14 within ±30% of consensus (bar: 70%); backtest passed 8 of 17; anchor missing from the blend on 6; Bear ≤ Base ≤ Bull holds on all 17.

## What it shows

- **Misrouted by the classifier.** 00002.HK (CLP) and CEG are valued as `Mature SaaS`; CCJ and 00006.HK as `Mature Platform`; NRG as `Levered Subscription`; NXT as `Early Platform`; LEU as `Upstream Oil & Gas`; SMR as `Aerospace & Defense`. None has industry routing in scope and none carries a pin with a profile.
- **Every Regulated Utility loses its anchor.** NEE, DUK and SO all drop the 0.60 DCF leg as non-positive, so 40% of the intended weight prices the name, and half of what survives is `P/Rate Base` computed as P/BV. The label says rate base; the number is book value.
- **Solar on a PPA-backed DCF.** ENPH and FSLR are equipment manufacturers priced on the IPP profile (pinned), 32% proxied. BE, a fuel-cell maker, prices at $8.39 against a $285 consensus on the same profile.
- **Two names have no surviving weight at all.** SMR (every leg uncomputable; pre-revenue) and 01816.HK (base IV −3.24, the CGN Power defect recorded as open at the close of Wave 1).
- **GEV at −75%** on Capital Goods with nothing dropped and nothing proxied: a method-fit problem, not a plumbing one.
- No HK name returned a consensus target (see `consensus_target_unusable_rules`); HK scorecard rows need the stockanalysis.com read.
