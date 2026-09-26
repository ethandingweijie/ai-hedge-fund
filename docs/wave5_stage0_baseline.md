# Wave 5 (Health Care): Stage 0 baseline

Run 2026-09-26 on the remediated engine (one equity bridge, DDM at cost of equity, zero country premium
for China and Hong Kong), over the 16 names the Stage 2 probe identified (`docs/waves_2_5_stage2_probe.md`,
W5). Read-only; recorded in `docs/baselines/baseline_wave5.json`. Every later Wave 5 change is judged
against this table.

| Ticker | Routed profile today | Anchor (in blend) | Base IV | Consensus | IV vs cons | IV vs spot | Backtest |
|---|---|---|---|---|---|---|---|
| AMGN | Large Cap Pharma | P/E (yes) | 374.60 | 394.58 | −5% | −10% | passed / TIE |
| VRTX | MedTech / Devices | EV/Revenue (yes) | 378.97 | 570.93 | −34% | −28% | passed / TIE |
| 01801.HK | MedTech / Devices | EV/Revenue (yes) | 68.89 | — | — | −31% | passed / MODEL_CLOSER |
| LLY | Large Cap Pharma | P/E (yes) | 517.30 | 1343.07 | −61% | −56% | fired / MARKET_CLOSER |
| PFE | Large Cap Pharma | P/E (yes) | 37.01 | 28.50 | +30% | +29% | fired / MARKET_CLOSER |
| 01093.HK | CDMO / Life Science Tools | P/E (yes) | 11.59 | — | — | +24% | passed / MODEL_CLOSER |
| MDT | Large Cap Pharma | P/E (yes) | 96.75 | 98.00 | −1% | +9% | passed / TIE |
| SYK | MedTech / Devices | EV/Revenue (yes) | 212.50 | 372.39 | −43% | −22% | fired / MARKET_CLOSER |
| 00853.HK | Pre-approval Biotech | rNPV (NO) | — | — | — | — | passed / MODEL_CLOSER |
| BAX | Mature SaaS | EPV (NO) | 21.43 | 25.00 | −14% | −8% | fired / MARKET_CLOSER |
| UNH | Managed Care | P/E (Ops) (yes) | 288.52 | 473.89 | −39% | −23% | passed / MODEL_CLOSER |
| CVS | Managed Care | P/E (Ops) (yes) | 89.43 | 111.20 | −20% | 0% | passed / MODEL_CLOSER |
| BSL.SI | Mature Platform | DCF (FCF+) (yes) | 0.76 | — | — | −8% | passed / MODEL_CLOSER |
| TMO | CDMO / Life Science Tools | P/E (yes) | 563.63 | 631.42 | −11% | −16% | passed / TIE |
| 02269.HK | Large Cap Pharma | P/E (yes) | 56.65 | — | — | +7% | passed / MODEL_CLOSER |
| VEEV | Mature SaaS | EPV (yes) | 192.88 | 290.50 | −34% | −31% | fired / MARKET_CLOSER |

| Measure | Stage 0 |
|---|---|
| Within ±30% of consensus | 6 of 11 |
| Within ±30% of spot | 12 of 15 |
| Methodology backtest passed | 11 of 16 |
| Anchor missing from blend | 2 (00853.HK, BAX) |
| Median consensus spread | +8% |

## What the baseline says

**Better than any prior Stage 0, and for a reason.** Health care has profiles already (Biopharma,
HealthcareServices, Healthcare), consensus targets sit only 8% above spot here, and 12 of 15 names are
within 30% of spot. The misses are routing, as in Wave 4, but fewer.

**The routing errors.** Vertex is a commercial biotech on the MedTech / Devices profile; Medtronic is a
device maker on Large Cap Pharma; WuXi Biologics (02269.HK) is a CDMO on Large Cap Pharma while CSPC
(01093.HK), a drug maker, sits on CDMO; Baxter fell to Mature SaaS through the ladder and has no anchor;
Raffles Medical (BSL.SI) is on Mature Platform. MicroPort (00853.HK) is a device maker routed to
Pre-approval Biotech and produces no value at all: rNPV, EV/R&D and Cash Runway are all uncomputable for
a revenue-generating company.

**The pharma anchor problem.** Large Cap Pharma carries an rNPV (Pipeline) leg at 0.30 that is
uncomputable on every name (no pipeline inputs), so the profile runs on P/E and its cross-checks. That is
why LLY sits at −56% against spot: a trailing P/E on a name the market prices on a GLP-1 revenue path to
2030. The pipeline leg needs a review-gated input (the Stage 5 pattern: cited pipeline value or
consensus peak sales) before the profile can price a growth pharma.

**Where it is right.** AMGN −10%, MDT +9%, TMO −16%, CVS 0%, PFE +29% against spot. Managed Care and Life
Science Tools are on the right profiles and land.

## What Stage 1 (universe, owner checkpoint) has to settle

1. Keep or cut: 00853.HK (produces nothing today; a device profile fixes it), BSL.SI (only SG name, thin
   Care Facilities basket), VEEV (health-care software; FMP labels it Technology).
2. Whether Large Cap Pharma's pipeline leg becomes a review-gated Gemini input (the SOTP pattern) or is
   struck until one exists.
3. Whether devices split from pharma by FMP label (`Medical - Devices` and `Medical - Instruments &
   Supplies` have no row today) into one MedTech profile, and biotech by stage (commercial VRTX vs
   pre-approval).

Stage 3 (methods) proposal follows the checkpoint, on the owner's framework for health care.
