# Wave 3 (aerospace & defence): Stage 6 scorecard, before and after

Before: `docs/baselines/baseline_wave3.json` (2026-09-21, engine at 0f981f0). After: `docs/baselines/after_wave3.json` (2026-09-23, the Wave 3 commits 337e3f2 + 4877ee2, backlog and SOTP inputs accepted in the LOCAL store to measure). Same universe, same script (`scripts/wave_baseline.py`), local store, dynamic multiples on. No rate base is accepted yet, so `P/Rate Base` still prices through its P/BV proxy in every row below.

| Ticker | Profile before -> after | Base IV before -> after | Consensus | IV vs cons before -> after | Proxied wt | Surviving wt | Anchor in blend | Backtest before -> after |
|---|---|---|---|---|---|---|---|---|
| LMT | Aerospace & Defense -> **Defense Primes** | 535.89 -> 554.43 | 644.86 | -17% -> -14% | 0% -> 0% | 1.0 -> 1.0 | yes -> yes | passed -> fired |
| NOC | Mature SaaS -> **Defense Primes** | 521.44 -> 506.07 | 621.70 | -16% -> -19% | 0% -> 0% | 0.95 -> 1.0 | yes -> yes | passed -> fired |
| GD | Mature SaaS -> **Defense Primes** | 346.33 -> 359.55 | 422.57 | -18% -> -15% | 0% -> 0% | 0.95 -> 1.0 | yes -> yes | passed -> passed |
| RTX | Aerospace & Defense -> **Defense Primes** | 138.87 -> 125.07 | 238.50 | -42% -> -48% | 0% -> 0% | 1.0 -> 1.0 | yes -> yes | fired -> fired |
| BA | Aerospace & Defense -> **Commercial Aerospace & Engines** | 101.66 -> 133.49 | 273.13 | -63% -> -51% | 0% -> 0% | 0.8 -> 0.7 | yes -> NO | skipped -> skipped |
| GE | Aerospace & Defense -> **Commercial Aerospace & Engines** | 161.66 -> 164.17 | 411.60 | -61% -> -60% | 0% -> 0% | 1.0 -> 0.45 | yes -> NO | fired -> passed |
| HWM | Mature Platform -> **Niche Aerospace Components** | 106.29 -> 174.13 | 331.10 | -68% -> -47% | 0% -> 0% | 0.9 -> 1.0 | yes -> yes | fired -> fired |
| TDG | Mature Platform -> **Niche Aerospace Components** | 780.46 -> 1229.88 | 1465.14 | -47% -> -16% | 0% -> 0% | 0.9 -> 1.0 | yes -> yes | fired -> fired |
| HEI | None -> **Niche Aerospace Components** | - -> 196.26 | 397.00 | - -> -51% | 0% -> 0% | None -> 1.0 | NO -> yes | None -> fired |
| LHX | Mature Platform -> **Defense Primes** | 223.61 -> 292.36 | 340.00 | -34% -> -14% | 0% -> 0% | 0.9 -> 1.0 | yes -> yes | fired -> passed |
| KTOS | Mature SaaS -> **Defense Tech & Space** | 16.14 -> 49.28 | 98.15 | -84% -> -50% | 0% -> 0% | 0.7 -> 0.65 | yes -> yes | fired -> passed |
| AVAV | Growth SaaS -> **Defense Tech & Space** | - -> 198.95 | 220.33 | - -> -10% | 25% -> 0% | 0.2 -> 0.65 | NO -> yes | passed -> fired |
| RKLB | High-Growth Tech / AI -> **Defense Tech & Space** | 9.75 -> 10.85 | 109.20 | -91% -> -90% | 60% -> 0% | 0.5 -> 0.65 | NO -> yes | fired -> fired |
| S63.SI | Aerospace & Engineering (SG) | 6.00 -> 4.54 | - | - -> - | 0% -> 0% | 1.0 -> 1.0 | yes -> yes | passed -> passed |
| S59.SI | Growth SaaS -> **Aviation & Marine (SG)** | 1.29 -> 1.70 | - | - -> - | 5% -> 0% | 1.0 -> 1.0 | yes -> yes | fired -> passed |
| 00232.HK | Mature SaaS -> **GA Engines & Aftermarket (HK)** | 0.22 -> 0.49 | - | - -> - | 0% -> 0% | 0.95 -> 1.0 | yes -> yes | passed -> fired |
| 02357.HK | Hyperscaler / Tech Conglomerate -> **Aerospace Holdco (HK)** | 14.87 -> 29.32 | - | - -> - | 0% -> 46% | 0.9 -> 1.0 | yes -> yes | fired -> fired |
| 02507.HK | None -> **General Aviation (HK)** | - -> 60.39 | - | - -> - | 0% -> 0% | None -> 1.0 | NO -> yes | None -> passed |

| | Before | After | Bar |
|---|---|---|---|
| Within +/-30% of consensus | 3 of 11 | 6 of 13 | >= 70% |
| Methodology backtest passed | 6 of 16 | 7 of 18 | >= before and >= 50% |
| Anchor missing from the blend | 2 | 2 | 0 |
| Mean weight on proxied legs | 6% | 3% | - |
| Bear <= Base <= Bull | 15 of 16 | 18 of 18 | all |


## Verdict: every name on its own profile, deterministically; the consensus bar is not cleared

Within ±30% of consensus 3 of 11 -> **6 of 13 (46%)** against a 70% bar; backtest 6 of 16 -> 7 of 18 (39%)
against 50%. No name is valued as software any more; every US and HK name routes by the row or a pin, and
both SG names by the map rather than the LLM router. Weight on proxied legs 6% -> 3%; Bear ≤ Base ≤ Bull on
all 18 (was 15 of 16). The primes moved toward consensus (LMT −17% -> −14%, GD −18% -> −15%, LHX −34% ->
−14%), AVAV went from Unrated to −10%, TDG from −47% to −16%.

## Every miss, explained

- **GE (−60%) and the Commercial Aerospace & Engines anchor.** `EV/EBIT (norm)` reads the basket's EV/EBIT
  median, a comps field that landed with this wave; the local members store predates it, so the anchor was
  uncomputable on both BA and GE and the blend ran on EV/EBITDA and P/E (norm) at 0.45 of intended weight
  (GE) and on the SOTP (BA). It prices on the next refresh -- production's is Saturday 26 Sep. GE also has no
  SOTP inputs yet (only BA, AviChina and ST Engineering were pre-filled). Re-measured below once the local
  refresh completes.
- **BA (−51%).** The accepted Gemini SOTP prices it at $157 a share (segments $163.7bn at the cited midpoints,
  net debt −$25.9bn, holdco 10%); the trailing EV/EBITDA and P/E legs sit on trough earnings and pull the blend
  to $133. The SOTP is the owner's primary method for Boeing and it is now the heaviest surviving leg.
- **RTX (−48%).** Its own multiple (18x EV/EBITDA) sits above the primes basket (15.3x) it is now priced on:
  Pratt and Collins are commercial businesses. The owner placed RTX with the primes; the number says the
  market does not. Recorded, not tuned.
- **HWM (−47%), HEI (−51%): the PEG constant.** A 2.2 PEG on HEI's 13.5% growth gives a fair P/E of 30x
  against its 43x NTM; HWM's 22% growth gives 48x, but its FCF Yield and ROIC legs at the basket's 2.5% yield
  pull it down. TDG at −16% is the profile working. The PEG is derived from the basket and marked "owner to
  confirm"; a higher constant is the owner's call, not a tuning.
- **KTOS (−50%), RKLB (−90%): `Rev DCF` cannot price a loss-maker.** It is a projection-family leg and the
  OE≤0 gate disables the whole family for a name with negative owner earnings, so both ran on EV/Fwd Rev and
  EV/Revenue alone (0.65 of weight). The profile needs a revenue-path DCF with a target margin (the
  `Rev DCF (Mkt Sh)` shape) or the owner-set EV/Backlog constant; both are open decisions. RKLB at −90% is
  the AI/space re-rating on a 30x EV/Revenue name priced at the industry's 4.8x.
- **02357.HK: two SOTPs.** The accepted Gemini SOTP was promoted into the blend (0.38) and the look-through
  NAV, with no owner-authored template, fell to its P/BV proxy (0.46). One SOTP is enough; the look-through
  leg should give way to the analyst SOTP when that is accepted. Stage 5 item.
- **HK and SG carry no usable consensus target**, so five names are outside the 13.

## Carried forward

Backlog-coverage DCF bounds bind nowhere yet (every prime's base growth is positive and covered). The
EV/Backlog constant and the Defense Tech revenue-DCF shape are owner decisions. HWM and TDG report no backlog
(correct); Cirrus's "1,059" is aircraft, not dollars, and stays pending. Rev DCF, EV/EBIT (norm) and SOTP
availability are the three things that decide whether this wave's after-numbers are the profiles' or the
plumbing's, and the first two are fixable without an owner decision.

## Addendum: GE and BA re-measured on a basket that carries EV/EBIT (local refresh, 2026-09-23)

The local US refresh (1,917 names, 25 minutes) now carries `ev_ebit`; the A&D industry median is 27.2x
(n=18). GE's anchor prices: `EV/EBIT (norm)` 0.40, EV/EBITDA 0.33, P/E (norm) 0.27, surviving weight 0.45 ->
0.75, base IV $164 -> **$186 (−55%)**, backtest passed. BA's anchor still cannot: its five-year normalised
EBIT is negative, so the SOTP from the accepted inputs carries the most weight ($157 a share) and the blend
stays at **$133 (−51%)**. Both are in `docs/baselines/after_wave3.json`. Production's basket gains the field
on Saturday 26 September.

## Addendum: the three engine rules (owner, 2026-09-23), re-measured

| Rule | Name | Before | After |
|---|---|---|---|
| 1 — `Rev DCF (Target Margin)` is the OE≤0 fallback, exempt from the gate; EBIT margin ramps over 5 years to 13% (KTOS, profile) / 16.5% (RKLB, ticker), taxed at 21%, floor lifted for the ramp; no EV/Backlog | KTOS | $49.28, surviving 0.65 | **$44.22, surviving 1.00** (−55%) |
| | RKLB | $10.85, surviving 0.65 | **$16.35, surviving 1.00** (−85%) |
| 2 — PEG 2.2 locked under `OWNER_OVERRIDE_PENDING`; runs as the active baseline; the leg at 1.9x and 2.5x rides on the trace, the base-scenario flag and the workbook's Summary sheet ("Owner overrides pending") | TDG | $1,229.88 | $1,228.12 (−16%), flagged |
| 3 — analyst SOTP over look-through: once promoted, the look-through leg is computed, published as an unweighted cross-check with its variance against the analyst figure (`GATE_SOTP_PRECEDENCE`), never blended | 02357.HK | two SOTPs: analyst 0.38 + look-through→P/BV 0.46 | **one SOTP** at 0.70, DCF 0.16, Forward P/E 0.14; look-through shadow-only |

Rule 1 does what it was asked to: the projection family's 0.35 is no longer lost on an unprofitable name, and it
does not lift either name toward consensus -- a 13-16% terminal margin on today's revenue base is a small
number against a market that prices a re-rated growth path (RKLB at 30x sales). That gap is the AI/space
regime, not the plumbing, and it is now measured on a full method set.

## Addendum: SOTP precedence reaches a templated look-through (owner, 2026-09-23)

Rule 3 was first scoped to the look-through that has no template and would price as its P/BV proxy; whether it
should also displace a look-through that COMPLETES from a template (S08.SI, BN4.SI, U96.SI, 02020.HK) was left
open. The owner closed it: the analyst SOTP takes structural precedence either way. A template applies one
top-down formula across the holding structure, while the SOTP isolates each business line on its own multiple
and keeps holding-level items (holdco discount, deferred tax on the portfolio, minority haircuts) segregated,
so the template cannot outvote it and, sitting beside it, would double-count. The call site now shadows a
completing look-through whenever the SOTP in the blend is the owner-ACCEPTED one (Gemini-cited segments and
multiple ranges, review-gated, origin-tagged by the bridge). The extractor's machine-built SOTP is not that
framework: on BN4.SI (2026-09-15) it said S$7.12 against a S$11.70-14.30 ground truth and the look-through was
the leg that had it right, so beside a completing template it keeps sharing the family weight as decided
then. With no template every analyst SOTP displaces the P/BV proxy, as in the first cut. The 2026-09-15
sharing decision otherwise applies only to SOTPs that remain in the blend (segments, published).

Closing it exposed a defect in the first cut: the shadowed look-through still counted as a family peer when the
promoted weight was split, so half of the 3.0 went to a leg that was then removed and renormalised away. The
analyst SOTP carried 1.5, not 3.0. The shadowed leg is now excluded from the split.

| Name | Rule 3 as first cut | Extended rule |
|---|---|---|
| 02357.HK | SOTP (analyst) 0.70, DCF 0.16, Forward P/E 0.14; base HK$30.78, target HK$16.88 | **SOTP (analyst) 0.82, DCF 0.10, Forward P/E 0.08; base HK$32.94, target HK$17.96**; look-through shadow-only |

No name carries an owner-accepted SOTP beside a templated look-through today (accepted `sotp` inputs: 02357.HK,
BA, S63.SI), so the extension changes nothing else until one is accepted for a conglomerate
(`docs/baselines/after_wave3_precedence.json`). Note for that day: the bridge only reads the accepted input
when the extractor built no SOTP of its own, so on a name with both, the machine SOTP is the one in the blend.
