# Wave 3 (aerospace & defence): Stage 0 baseline

Run 2026-09-22 with `scripts/wave_baseline.py --wave 3`, current engine (a7b833b), local store refreshed 2026-09-21, dynamic multiples on, forward overlay off. Raw record: `docs/baselines/baseline_wave3.json`. The "before" every Wave 3 change is judged against.

| Ticker | Routed profile | Reached by | Anchor (in blend?) | Proxied wt | Dropped legs | Surviving wt | Base IV | Consensus | IV vs cons | Backtest |
|---|---|---|---|---|---|---|---|---|---|---|
| LMT | Aerospace & Defense | router | EV/EBITDA (yes) | 0% | - | 1.0 | 535.89 | 644.86 | -17% | passed / TIE |
| NOC | Mature SaaS | ladder | EPV (yes) | 0% | LBO Floor (uncomputable) | 0.95 | 521.44 | 621.70 | -16% | passed / TIE |
| GD | Mature SaaS | ladder | EPV (yes) | 0% | LBO Floor (uncomputable) | 0.95 | 346.33 | 422.57 | -18% | passed / MODEL_CLOSER |
| RTX | Aerospace & Defense | router | EV/EBITDA (yes) | 0% | - | 1.0 | 138.87 | 238.50 | -42% | fired / MARKET_CLOSER |
| BA | Aerospace & Defense | router | EV/EBITDA (yes) | 0% | FCF Yield (uncomputable) | 0.8 | 101.66 | 273.13 | -63% | skipped / UNSCORABLE |
| GE | Aerospace & Defense | router | EV/EBITDA (yes) | 0% | - | 1.0 | 161.66 | 411.60 | -61% | fired / MARKET_CLOSER |
| HWM | Mature Platform | ladder | DCF (FCF+) (yes) | 0% | LBO Floor (uncomputable) | 0.9 | 106.29 | 331.10 | -68% | fired / MARKET_CLOSER |
| TDG | Mature Platform | ladder | DCF (FCF+) (yes) | 0% | LBO Floor (uncomputable) | 0.9 | 780.46 | 1465.14 | -47% | fired / MARKET_CLOSER |
| LHX | Mature Platform | ladder | DCF (FCF+) (yes) | 0% | LBO Floor (uncomputable) | 0.9 | 223.61 | 340.00 | -34% | fired / MARKET_CLOSER |
| KTOS | Mature SaaS | ladder | EPV (yes) | 0% | DCF (2-stage) (uncomputable); LBO Floor (uncomputable) | 0.7 | 16.14 | 98.15 | -84% | fired / MARKET_CLOSER |
| AVAV | Growth SaaS | ladder | NRR-adj DCF (**no**) | 25% | NRR-adj DCF (uncomputable); DCF (uncomputable); Rev DCF (ARR) (uncomputable) | 0.2 | - | 220.33 | - | passed / MODEL_CLOSER |
| RKLB | High-Growth Tech / AI | ladder | Reverse DCF (**no**) | 60% | Reverse DCF (uncomputable); SOTP (published) (uncomputable) | 0.5 | 9.75 | 109.20 | -91% | fired / MARKET_CLOSER |
| S63.SI | Aerospace & Engineering (SG) | router | DCF (yes) | 0% | - | 1.0 | 6.00 | - | - | passed / MODEL_CLOSER |
| S59.SI | Growth SaaS | ladder | NRR-adj DCF (yes) | 5% | - | 1.0 | 1.29 | - | - | fired / MARKET_CLOSER |
| 00232.HK | Mature SaaS | ladder | EPV (yes) | 0% | LBO Floor (uncomputable) | 0.95 | 0.22 | - | - | passed / MODEL_CLOSER |
| 02357.HK | Hyperscaler / Tech Conglomerate | ladder | EV/EBITDA (yes) | 0% | FCF Yield (uncomputable) | 0.9 | 14.87 | - | - | fired / MARKET_CLOSER |

**Scorecard before:** 3 of 11 within ±30% of consensus (bar 70%); backtest passed 6 of 16; anchor missing from the blend on 2; one name (AVAV) Unrated on 0.20 surviving weight.

## What it shows

- **Only the four pinned names reach the A&D profile** (LMT, RTX, BA, GE). Every unpinned US name is valued by
  the classifier as software or a platform: NOC, GD and KTOS as `Mature SaaS`; HWM, TDG and LHX as
  `Mature Platform`; AVAV as `Growth SaaS`; RKLB as `High-Growth Tech / AI`. With `Aerospace & Defense` outside
  `routing_scope`, an unpinned name's SECTOR defaults to Tech before the Industrials ladder is ever reached, so the
  C3 fix (unmapped Industrials -> Capital Goods) cannot help until the label is in scope.
- **The primes are the closest to consensus** (LMT -17%, NOC -16%, GD -18%) even on the wrong profiles, because
  their trailing multiples and earnings are stable. The three-way split is about the OTHER two groups.
- **Commercial aero and aftermarket are 47-68% below consensus** (BA, GE, HWM, TDG): trailing multiples on
  delivery-disrupted earnings, exactly what the plan's "normalised for delivery disruption" methods are for.
- **Defence tech and space cannot be priced on earnings at all.** AVAV is Unrated (three of its four legs
  uncomputable); RKLB runs 60% on a TAM-penetration proxy; KTOS is -84% on an EPV of trough earnings.
- **Singapore is non-deterministic.** S63.SI reached `Aerospace & Engineering (SG)` through the LLM router, not
  the map (`Aerospace & Defense` is not in scope); S59.SI (SIA Engineering, an MRO) fell to `Growth SaaS`.
- **Hong Kong.** 00232.HK is Continental Aerospace Technologies (piston engines, loss-making), not AVIC -- the
  owner's GICS table had the wrong code; AviChina is 02357.HK, valued here as a Hyperscaler. No HK name returns a
  usable consensus target.
