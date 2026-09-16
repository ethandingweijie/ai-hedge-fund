# Golden valuation snapshots

Every entry is one `--update-snapshots` run. An entry
exists because a pinned end-to-end valuation moved, and
says why.

## 2026-09-16T14:01:25+00:00

- commit: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: initial baseline: end-to-end valuations pinned at 30b2670 (Phase 0, item 7)

## 2026-09-16T15:32:41+00:00

- commit: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Phase 1.1 (defect 1): CAGR-divergence gate + convergence fade (alpha=0.5). Expected movers, all investigated before regenerating. BN4_SI (Conglomerate / Industrial (SG)): gate fires on the analyst-band base +20.3% -> +5.0% (bands scaled x0.25; CAGR -2.5%, 22.7pp divergence), DCF leg dropped in every scenario (base iv_dcf 7.7124 -> None, bull 59.7998 -> None) so weight_dcf 0.25 -> 0.0, weight_multi 0.75 -> 1.0, methods_count 5 -> 4; base IV 5.83 -> 5.20, bull 20.29 -> 7.12, bear 7.37 unchanged; gate_metrics [] -> ['revenue_growth']. U96_SI (same profile): gate on the raw band base +39.3% -> +5.0% (bands scaled x0.13, 41.7pp); base IV 4.48 -> 5.73 (UP, because the old DCF leg 2.4759 sat BELOW the multiples blend 5.7269 at weight 0.3846 - removing a value-destructive leg raises IV), bull 12.88 -> 9.13, bear 2.33 unchanged. MU (Memory / DRAM-NAND): gate +100.0% -> +12.8% (bands x0.13; CAGR +7.8%, 92.2pp) then fade; iv_dcf base 51.7233 -> 34.8066 (-32.7%), bull 113.442 -> 49.2619 (-56.6%); blended base IV only 297.37 -> 293.14 (-1.4%). FCX (Mining (Major)): fade only, NO gate (consensus within 15pp of CAGR), g unchanged at 0.1405; DCF leg base 40.75 -> 16.93 (-58.5%), bear 15.07 -> 7.94, bull 156.31 -> 33.41 (-78.6%); blended IV unchanged at 25.58. iv_multi is byte-identical in all six BN4/U96 scenarios and Forward P/E + Forward EV/EBITDA do not move anywhere - they read forward_consensus (absolute analyst EPS), not the growth-derived yr1_eps_est - so the change is confined to the DCF/growth leg. 10 tickers unchanged: 02888_HK, 09988_HK, AAPL, BABA, C38U_SI, COST, D05_SI, MELI, SCHW, V. MELI staying quiet (42% CAGR, consensus near it) is the anchoring failure the plan warned against; the fade target is tgr_table, never historical CAGR. Cross-cutting observation for defect 3: MU's and FCX's blended IVs barely move despite 33-79% DCF-leg corrections - direct evidence of composite dilution, addressed in Phase 2.1.

## 2026-09-16T16:00:40+00:00

- commit: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Phase 0 remediation: replay now pins the wall clock to each fixture's captured_at. multiples_used.comp_age_days was computed as datetime.now() minus a FROZEN computed_at, so it advanced 1.0 per day of real time: the baseline written at 14:01 UTC carried 3.23 days and would have breached its own +/-5% tolerance (3.39) by roughly 18:00 the same day, failing 02888_HK, 09988_HK, C38U_SI and D05_SI with no code change at all - none of them a name Phase 1.1 touched. Only field expected to move is comp_age_days, from the generation-time age to the capture-time age (3.29 -> 3.20). No valuation moves: base_iv is unchanged for all fourteen names, and iv_multi is byte-identical. The freeze matters beyond cosmetics - shifting the frozen clock 100 days drops the comps past MAX_AGE_DAYS entirely and moves bull IV, the P/B and EV/EBITDA cohorts, peer counts and the 12m targets, so without it these fixtures would have silently degraded within weeks and been re-baselined as though a code change had caused it. Verified by tests/test_golden_clock.py, which shifts the frozen clock ten days and asserts comp_age_days moves by exactly ten and nothing else does.

## 2026-09-16T16:55:37+00:00

- regenerated at HEAD: `a740f0e`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Harness attribution fix, no engine change. _meta carried a single 'commit' field sourced from the replay results' meta.commit, which is the commit the FIXTURES were recorded at, while the golden failure message rendered it as 'baseline pinned <when> at <sha>' - reading as the engine that produced the pinned numbers. All three CHANGELOG entries therefore claimed 30b2670 while the second and third were actually written at c87d38c and a740f0e, so a golden failure pointed two commits early at an engine that provably did not produce the baseline. _meta now records both, separately and labelled: 'commit' (fixtures recorded at) and 'regenerated_at_commit' (HEAD at regeneration, i.e. the parent of the commit carrying the baseline). The changelog line '- commit:' is replaced by two labelled lines, and the failure message renders both plus an explicit fallback for baselines that predate the new field rather than silently naming the fixture commit again. Expected moves: _meta.regenerated_at_commit added; _meta.generated_at advanced. NO projection field moves on any of the fourteen names - this regeneration exists to add the field, and a diff against the previous baseline is asserted to be _meta-only. Pinned by tests/test_golden_snapshots.py.

## 2026-09-16T18:56:54+00:00

- regenerated at HEAD: `9eb2dc6`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Phase 1.2A: balance-sheet financials lose their EV, DCF and FCF legs. A bank's liabilities are its product, so enterprise value subtracts the thing being valued; 02888.HK published a forward EV/EBITDA of HK$730 per share on that basis and no test caught it. Two tiers - Tier 1 is the profile (16 names: every bank variant, insurance, brokerage, holdco), Tier 2 is a customer-balance-to-assets measurement above 0.30, applied only to the 7 fee-based profiles where a name routed there may still be float-funded in fact. Market Infrastructure and Market Infrastructure (SG) are exempt on measured matched pass-through collateral; Real Estate Asset Manager (SG) is in neither tier because its profile carries no EV/DCF/FCF leg to strip.

EXPECTED MOVES, and only these:
- SCHW (Brokerage, Tier 1): DCF, FCF Yield and Forward EV/EBIT legs stripped. Base IV 91.12 -> 75.41 (-17.24%), bear 59.64 -> 51.21 (-14.13%). methods_count 8 -> 6, methods_used [DCF, FCF Yield, P/BV, P/E (norm)] -> [P/BV, P/E (norm)], iv_dcf 131.6844 gone, iv_multi 80.9831 -> 75.4125, weight_dcf 0.2 -> 0.0, weight_multi 0.8 -> 1.0, effective weights renormalised 0.35/0.25 -> 0.5833/0.4167. gate_metrics gains ev_dcf_weight_share. Bull additionally loses the P/BV (asset floor) 0.04 leg and the 80/20 Rule flag, which was that leg's only consumer.
- 02888_HK and D05_SI: the three Forward EV/EBITDA shadow rows (base/bear/bull) are no longer computed. INTRINSIC VALUE UNCHANGED at 284.38 and 47.89 - Money Center Bank and Money Center Bank (SG) carry no EV/DCF/FCF leg, so no blend weight moved. gate_metrics gains forward_ev_ebitda_row and forward_flags gains the suppression note. This is the second record the gate emits: classification, not 'did the strip remove something', because keying the shadow skip off the strip left the HK$730 row in place for every bank profile. The golden baseline caught that; ~140 unit tests exercising the gate in isolation did not.
- V (428.47) and MU (293.14) MUST NOT MOVE and did not: Payment Networks and Memory / DRAM-NAND measure 0.2420 and 0.2034 against the 0.30 threshold.

Also in this commit, and the reason the numbers above are trustworthy: accounts_payable was being served from the CASH-FLOW statement's accountsPayables - a working-capital DELTA, one letter apart from the balance sheet's accountPayables stock. search_line_items merges the four statements flat before translating and the translation is first-wins, so the delta won on 36 of 36 names measured: AAPL 0.902bn instead of 69.86bn (77x understated), COST 49x, BABA 0 instead of 358.55bn, and HOOD/ICE/CME/BN4.SI/U96.SI/C38U.SI negative. Four consumers read that flow as a level. It moved no intrinsic value - dcf_agent reads the field only through the Tier-2 ratio - but it put S68.SI at 0.3135 against a 0.4738 truth, 0.0135 over the threshold the clearing-house exemption is justified against. Fixed in _BALANCE_MAP; pinned by tests/test_cashflow_delta_shadowing.py including a data-driven scan of every recorded golden payload, and reconciled against the frozen fixtures by test_the_frozen_sgx_dict_is_what_the_live_feed_produces. Nine tests fail when the mapping is put back.

BACKWARD TEST (scripts/backtest_valuation_fixes.py --item 1.2A, live FMP, as-of 2026-09-17): ACCEPT. 32 scored labels, 1 unmeasurable (BRK.B returns no FMP rows). TP=17 FP=0 TN=14 FN=1, precision 100.0% against a 50.0% bar, recall 94.4% against a 90.0% bar. The single false negative is HOOD: the plan lists it under Brokerage but live routing gives Crypto Exchange, a profile in neither tier - a routing gap, not a classifier miss, and its measured ratio is 0.9326 so the economics are a brokerage's. AAPL is the row that justifies the two-tier design: at 0.4593 it is well above 0.30 and correctly not stripped, because trade credit from a contract-manufacturing base is not customer money and the ratio cannot tell the two apart. Tier 2 is a conditional second tier for that reason and must never be promoted to a classifier.

