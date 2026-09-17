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

## 2026-09-16T21:20:53+00:00

- regenerated at HEAD: `3c22f2e`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Phase 1.2B: cyclical peak consensus gets a mid-cycle cross-check, OBSERVATION-ONLY.

THE DEFECT. MU published a base intrinsic value of $293.14 built on a one-year consensus jump of +100.0% held flat for ten years. The forward EPS driving it is $156.08 against a five-year realised diluted EPS history of [5.14, 7.74, -5.34, 0.70, 7.59] - a max of $7.74, a mean of $3.17, a sigma of $5.54. The forward number is 20.2x the best year the company has ever had, 10.1x the 2x-max line ($15.48) and 11.0x mean-plus-two-sigma ($14.25). Nothing in the engine noticed, because nothing compared the forward consensus to the history that the same engine already had in hand.

WHAT SHIPPED. A trigger with two arms (forward EPS > 2.0x the highest EPS in the available history, or > mean + 2 sigma; sigma stands down below 4 observations, where a two-point stdev is noise with a decimal place) and a mid-cycle alternative (P/B-ROE, computed from normalised - not reported - earnings, with the two owner floors CoE - g >= 2.5% and justified P/B >= 0.20x, each recording a flag when it binds and no upper cap). Both are recorded per run as GATE_CYCLICAL_PEAK_CONSENSUS. Mid-cycle leg swaps (P/E -> P/E (norm), EV/EBITDA -> EV/EBITDA (norm)) are computed and recorded in the same entry, which also carries base_iv_path_a and base_iv_path_b so the row is scoreable from the ledger alone.

WHY `applied: False`. The plan's shipping rule requires a backward test clearing hit-rate >= 0.50, MAE no worse than legacy, and at least 10 scoreable firings before an item goes live. This item has ZERO scoreable firings: the plan's sample is MU FY2018 and FY2022, FCX 2021, NUE 2021, and the live feed caps history at five annual rows, so none of those dates exist yet - Phase 3's EDGAR back-fill is the prerequisite. Separately, historical CONSENSUS is not archived anywhere, so even with the history the trigger's input has to be proxied by realised next-year EPS, a perfect-foresight stand-in that FAVOURS path A and therefore makes the eventual test conservative rather than merely approximate. scripts/backtest_valuation_fixes.py --item 1.2B reports PENDING / observation-only and prints the discrimination table.

THE DEFERRED MOVE, DELIBERATELY NOT TAKEN. The plan's Verification section lists "MU forward legs routed to P/B-ROE" as an expected golden move. IT IS NOT IN THIS DIFF, and that is the point of shipping observation-only. Measured on the frozen fixture, the path-B leg is P/B-ROE (mid-cycle) = 58.9 against a published base IV of 293.14 - a factor of ~5. Promoting it would move MU's intrinsic value by roughly that much on zero completed backward test. When the forward or backward test clears the bar and the owner promotes the gate, that move lands in its own golden regeneration with its own CHANGELOG entry, attributable to one mechanism. Deferring it also keeps Phase 2.1's composite squash (MU 1.63 -> 1.29) from being entangled with it in a single baseline rewrite, which would make neither move reviewable.

EXPECTED MOVES, AND ONLY THESE. Both are IV-neutral by construction: the P/B-ROE leg is put into method_values by setdefault so it is visible in method_iv_table, but _blend_methods iterates the PROFILE's method list, so a key no profile names can never receive weight. Path B is blended into separate dicts and its flags into a scratch list.

- MU (Memory / DRAM-NAND): FOUR fields. gate_metrics ["fcf_margin","revenue_growth"] -> ["fcf_margin","midcycle_leg_weight_share","revenue_growth"]. scenarios.base.method_iv_table gains `P/B-ROE (mid-cycle)` = 58.9. scenarios.base.methods_count 7 -> 8. scenarios.base.forward_flags gains "Cyclical peak-consensus gate (observation-only, not applied): mid-cycle legs EV/EBITDA->EV/EBITDA (norm); peak trigger fired (multiple-of-max+mean-plus-sigma, fwd EPS 156.08 vs 15.48). IV unchanged; both paths recorded for the forward test." BASE IV UNCHANGED AT 293.14. Bear and bull are byte-identical: the trigger is evaluated on the base scenario only, and the flag is published on base only.
- FCX (Mining (Major)): ONE field. gate_metrics [] -> ["peak_trigger"], recorded because the trigger was evaluated and did NOT fire - forward EPS $2.95 sits 49.2% below the 2x-max line ($5.80) and 11.5% below mean-plus-two-sigma ($3.33). A non-firing is a scoreable observation and must be visible, or the forward test can only ever score names that fired. BASE IV UNCHANGED AT 25.58.
- The two gate_metrics names differ because the record's `metric` field is `midcycle_leg_weight_share` when there is a leg to swap (MU: EV/EBITDA -> EV/EBITDA (norm), 0.30 of profile weight) and `peak_trigger` when there is not (FCX: Mining (Major) already carries EV/EBITDA (norm) and P/BV, so the swap list is empty by design). The trigger's own arithmetic is inside the record either way.
- The other 12 tickers are byte-identical. V (428.47), SCHW (75.41), 02888_HK (284.38) and D05_SI (47.89) carry no cyclical profile, so the gate is not evaluated for them at all.

A BUG THE GOLDEN DIFF CAUGHT AND 42 UNIT TESTS DID NOT. The first version appended the observation flag to `ticker_forward_flags`. That list is snapshotted per scenario at `forward_flags = list(ticker_forward_flags)` EARLIER in the loop, so anything appended after that point is published on the NEXT scenario: base - the scenario the trigger was actually evaluated on - lost the flag, and bear and bull inherited an observation about a scenario they never ran. The pre-fix diff showed exactly that (scenarios.bear.forward_flags and scenarios.bull.forward_flags changed, scenarios.base.forward_flags did not). Fixed by appending to `forward_flags`, and pinned by test_the_observation_flag_lands_on_the_scenario_it_was_measured_on, which asserts the enclosing block appends to the right list and not to the wrong one. No unit test could have caught this: the flag plumbing is only observable end-to-end, which is what item 7 exists for.

A SECOND MISTAKE THE SAME DIFF CAUGHT, IN THE TESTS. The published flag says "vs 15.48". 15.48 is 2 x 7.7424, the DILUTED five-year max: the LineItem field `shares_outstanding` maps to FMP's `weightedAverageShsOutDil`, not `weightedAverageShsOut`, so `_historical_eps_series` divides net income by the diluted count. The test fixture had been built from basic-share EPS (FY22 7.81, FY25 7.65), whose max line is 15.62, and all 42 tests passed anyway - every assertion was written in terms of the fixture, so nothing in the file was anchored to anything the engine emits. The fixture now holds the diluted series and test_the_lines_are_the_ones_the_golden_baseline_publishes pins all four lines against the recorded flag string. Diluted is also the CORRECT side to compare against, since analyst consensus EPS is diluted too; had it been basic on one side and diluted on the other the trigger would have been measuring a share-count change.

DEVIATIONS FROM THE PLAN'S LITERAL WORDING, all recorded rather than silently taken.
- Normalised earnings use `_normalized_earnings`, an IQR-filtered MEAN over a five-year window, not the plan's "median over all available years". Reusing the engine's existing normaliser is deliberate: a second normaliser that disagreed with the one behind P/E (norm) would make the two mid-cycle legs incomparable, which is worse than either choice on its own.
- Cost of equity is proxied by the run's WACC. `_compute_ggm_pb` resolves CoE and g from the bank calibration, and the broker tables carry no entry for any cyclical profile, so there is no CoE to read. The engine's own `ROE vs CoE` leg already does the same thing. Consequence: the owner's CoE - g >= 2.5% floor is a NEW and tighter bound than the engine had, since dcf_agent already forces tgr = wacc - 0.005 whenever wacc <= tgr. Clamp-and-flag, no upper cap, per the owner's floors. It is also conservative for a cyclical, whose equity beta sits above the blended WACC it is charged.
- Upstream Oil & Gas is in _CYCLICAL_PROFILES but receives NO mid-cycle leg: its three legs are P/CF (anchor, 0.60), Depleting Asset DCF (0.30) and P/BV (0.10), and the swap map only covers P/E and EV/EBITDA. Its forward P/CF leg therefore still projects a peak cash-flow year flat. Reported to the owner as a hole in the item rather than papered over with an invented normalised-P/CF multiple.
- The plan's "suppress forward P/E and forward EV/EBITDA, route the weight to P/B-ROE" reads as a reweighting and is not one today: no cyclical profile carries a Forward P/E or Forward EV/EBITDA leg in its method list, so the weight the trigger frees is zero and the leg is recorded for the forward test rather than blended. Path B still removes them from its own value map, so the mechanism is in place for the day a profile does carry one.
- Mining (Major) and Digital Asset Mining produce no swaps either, but for the opposite reason: they already carry normalised legs. FCX is the measurement, and it is why the swap list skips a leg whose target is already present.
- EV/EBITDAR (Airlines) is deliberately untouched. Normalising lease-adjusted EBITDAR would mean normalising the lease adjustment, and the lease-normalisation gap is already tracked separately.

BACKWARD TEST (scripts/backtest_valuation_fixes.py --item 1.2B): PENDING, shipped observation-only. What was measured while pending is discrimination, not correctness, and the two are not interchangeable: MU fires both arms and FCX fires neither, and on the same 2x-max line MU's forward is 10.1x it while FCX's is 0.51x - separated by a factor of twenty, not by a threshold tuned to land between them. That says the trigger can tell a cycle top from an ordinary year. It says nothing about whether substituting the mid-cycle leg improves the estimate, which is the only question scoring can answer.

## 2026-09-17T03:17:52+00:00

- regenerated at HEAD: `d181f7f`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Phase 1.3a: framework KPIs that are arithmetic on filed statements stop being quoted out of research narrative.

THE DEFECT. The framework's KPI vectors were populated by an LLM extractor reading a deep research report, with FMP filling whatever came back empty ("Extractor wins where it has an explicit value - FMP only fills gaps"). For a KPI that is arithmetic on a filed statement that precedence is backwards, and it is not hypothetical. 2026-09-16, seven minutes apart: Alibaba's GAAP operating_margin_pct came back +10.0% for 09988.HK and -0.3% for BABA, and that one field moved the composite from 1.266 to 1.0363 and opened an 18.7% gap between two listings' blended IVs ($209.03 vs $176.05 per ADS) on a price that agreed to 0.6%. Keppel (BN4.SI), single-listed so there is no sibling to adopt from, returned roic_pct 1.09% on a conglomerate whose published accounts do not support it - and the SG Conglomerate profile marks roic_pct mandatory and scores a quality band off it. Neither run was wrong about the company; each was a different sample from the same distribution over sentences.

WHAT SHIPPED. src/data/deterministic_kpis.py computes the subset of framework KPIs that is pure arithmetic on the annual series the engine already fetches, on a LATEST ANNUAL basis (the series is what the DCF projects from, so composite and projection read the same fiscal year; it is also the only basis that exists for HK and SG, whose statement-growth endpoints return nothing - verified empty for 00700/01398/00005/00388/09988). apply_overrides lets the computed value win, and only for a key the profile already marks extractor_only: False, so the 464 extractor_only: True KPIs (CET1, NIM, occupancy, same-store sales, wafer starts) are untouched. Two rules inherited rather than invented: unavailable-not-invented (a KPI whose inputs are missing returns nothing rather than a substitute), and no new bounds (the only bound applied is the plan's owner-specified ROIC denominator floor, max(financing side, working capital + net PP&E), which exists because buyback-heavy and asset-light names report equity near or below zero and a capital of -$2bn is not a negative return). The result is NOT clamped: a small positive denominator still yields a large ROIC, the quality bands saturate, and capping it here would be a bound nobody chose. inventory and property_plant_equipment were added to the production request list - the ROIC floor's two inputs, and requesting a field and copying it into the row are separate acts that have silently produced None on every row four times now. GATE_DETERMINISTIC_KPI_PRECEDENCE records both halves of the pair (raw_input_path_a is the composite re-derived with the overridden keys reverted on the SAME post-overlay vector, so it is apples-to-apples) and emits ONLY when a computed value actually displaced an extracted one, so a run where the two agreed does not inflate the firing count the shipping rule is scored on.

EXPECTED MOVES, AND ONLY THESE. 4 of 14 fixtures move; ten are byte-identical (02888_HK, AAPL, C38U_SI, COST, D05_SI, FCX, MELI, MU, SCHW, V). NO BASE IV MOVES ANYWHERE - 09988_HK 160.84, BABA 193.13, BN4_SI 5.20, U96_SI 5.73 are identical before and after, and iv_multi, method_values, weights, the 12m targets and every scenario IV are unchanged. NO COMPOSITE MULTIPLIER MOVES EITHER: the gate record's raw_input_path_a equals its gated_output_path_b to six decimals on all four (1.036288, 1.036288, 0.881234, 1.000000). Each mover differs in exactly 4 leaves: gate_metrics gains composite_multiplier (the new gate record, which emits only when an override fired, so only these four show it), and the "Framework metrics (...)" string inside forward_flags changes on all three scenarios. Per name:
- 09988_HK and BABA (Hyperscaler / Tech Conglomerate, FY2026, one issuer served from the same FMP financials): revenue_growth_pct 0.09 -> 0.027423176865088106, capex_intensity_pct 0.124 -> 0.12400187560444284, fcf_margin_pct -0.05 -> -0.04955112487422705. The growth move is the substantive one and the backward test scores it as the strongest single observation in the row: extracted 0.0900, computed 0.0274, FMP reference 0.0274 - an exact match, delta -228.4%. The other two are the extractor's rounded quotation replaced by unrounded arithmetic on the same number.
- BN4_SI (Conglomerate / Industrial (SG), FY2025): roic_pct 0.0109 -> 0.05061769004842151, the KPI the defect was reported on.
- U96_SI (same profile, FY2025): roic_pct gap -> 0.0879905875641757 - a FILL, not a reversal; the extractor left the key empty and the flag string gains it. completeness stays 0.67.

WHY THE COMPOSITE DID NOT MOVE, MEASURED RATHER THAN ASSUMED. Three different mechanisms, each verified against the band thresholds and the bridge's quality_note, and each worth knowing because "the IV did not move" is NOT evidence the KPI was right:
- 09988_HK / BABA: quality is a min over the `megacap_q` correlation group, and the group floor is 0.85 on both paths - but the KPI EARNING IT CHANGED IDENTITY. Path A reads "[megacap_q] operating_margin_pct=-0.003 weak (0.85x)"; path B reads "[megacap_q] revenue_growth_pct=0.027423176865088106 decel (0.85x)". The computed growth crossed DOWN through the 0.07 threshold into the "decel" band, which happens to carry the same 0.85 as the band the extracted operating margin was sitting in. capex_intensity_pct stays "in-band" (1.00x) on both, and fcf_margin_pct is named in neither quality_tiers nor risk_adjustment for this profile, so it cannot reach the multiplier at all.
- BN4_SI: the fix moved ROIC 4.6x and crossed no threshold. The SG Conglomerate quality band is >=0.12 -> 1.1, >=0.07 -> 1.0, >=0.0 -> 0.9, so every ROIC between 0% and 7% scores identically at 0.90x ("weak" on both paths). The motivating defect - roic_pct 1.09% on a conglomerate whose published accounts do not support it, on a profile that marks the key mandatory - produced a wrong DISPLAYED number and a multiplier that was right by luck of band placement. What the fix corrects here is the record.
- U96_SI: numerically inert, epistemically not. Path A is quality_extracted 0/1 with quality_note "no quality KPIs supplied" and Q defaulting to 1.0; path B is 1/1 with "roic_pct=0.0879905875641757 in-band (1.00x)". Same multiplier, but the score moves from ASSUMED neutral because nothing was known to MEASURED neutral - a difference the bridge records and the frontend's low-confidence badge reads, and one that would have moved the multiplier had 0.088 landed in any band other than the one equal to the default.
Two consequences to carry forward. First, quality_z is None in every one of these bridges, so the peer-relative path did not fire and all four multipliers are band-driven - consistent with defect 3's dormant z-scores. Second, band coarseness of this kind means a materially wrong KPI can be valuation-invisible, so the golden baseline is a weak instrument for this class of defect and the backward test against the FMP reference is the one that caught it.

THE MOVE THAT IS NOT IN THIS DIFF, AND WHY THAT IS THE POINT. operating_margin_pct appears in NO override list, on any fixture, and the extractor's value survives untouched (09988_HK and BABA both keep -0.003). That absence is a fix, not an omission. This pass originally computed the operating margin as ebit / revenue, and FMP's ebit is NOT operating income: it is derived bottom-up as pre-tax income plus interest expense, so it carries the whole non-operating line, and for a company sitting on a large cash and investment portfolio that line is most of the profit. Measured 2026-09-17 against the filed statements: Alibaba FY2026 reports operatingIncome 59,665m against ebit 139,180m on 1,023,670m of revenue (5.83% vs 13.60%), and JD FY2025 reports 3,595m against 27,398m on 1,275,204m (0.28% vs 2.15%). Had that shipped, both Alibaba fixtures would have carried operating_margin_pct = 0.1360 in this baseline - 45x the extractor's -0.003 and 2.33x the filed 5.83% - feeding the quality composite and therefore the multiples leg of IV. The golden replay did not catch it and could not have: it compares against a baseline written by the same code, so a defect present on both sides is invisible. The backward test caught it, and caught it only once the harness was fixed - leg B flipped PENDING -> REJECT (12 pairs, MAE 121.1% -> 136.3%, three new operating_margin_pct FALSE_ALARMs) the moment it started requesting the line items production requests instead of gate_backtest._LINE_ITEMS' nine-item subset, which omits ebit, gross_profit, inventory and property_plant_equipment. FMP's own ratios-ttm.operatingProfitMarginTTM (0.0421 for 09988.HK, 0.0020 for 09618.HK) is computed from operatingIncome, which is what makes it the right reference and the ebit ratio a false alarm against it. operating_margin_pct now requires operating_income and omits the key when that is absent.

THE COST OF THAT CHOICE, STATED PLAINLY. operating_income is a recorded KNOWN GAP (tests/test_line_items_requested_are_read.py): the row builder copies it, the request list never asks for it, so it is None on every FMP row today. Omitting the key is therefore the inert choice, and it means THE DRIFT LEG A FOUND IS NOT FIXED BY THIS CHANGE. Leg A's only mover is operating_margin_pct (09988.HK, 0.1 -> -0.003 across two production runs 109.1 minutes apart on one filing), and the extractor keeps supplying that value, drift and all. Closing the gap means adding operating_income to the request list, which also activates the dormant Priority-1 branch of the bank EBIT path (operating_income + abs(provisions), dcf_agent.py:3272) and so moves every bank valuation - an owner decision with its own golden diff, not a field to slip into this change. The forward test must scope its zero-drift assertion to _deterministic_kpis.computed and not to the profile's eligible keys, or it will fail on a drift this change never claimed to fix.

ALSO IN THIS COMMIT, because it changes what a flag means. roic_pct flips extractor_only True -> False on two profiles (Pharma Distribution, Conglomerate / Industrial (SG)). The flag's sense shifts with it: it used to mean "supplied by _fmp_risk_kpis", and _fmp_risk_kpis does not supply roic_pct at all (roicTTM is null on this plan, even for 9988.HK). It now means "derivable from filed statements". eligible_kpi_keys consequently grants PERMISSION beyond compute_from_series' CAPABILITY - it returns every extractor_only: False key, while the pass derives seven families (operating margin, gross margin, FCF margin, capex intensity, R&D intensity under both spellings, revenue growth, ROIC). apply_overrides intersects the two so nothing wrong is written, but four declared-eligible keys across the seven backward-test candidates can never be: net_debt_to_ebitda (09988.HK, BABA, ICE), debt_to_ebitda (U96.SI) and equity_to_assets_pct (SCHW) are outside the seven families, and operating_margin_pct (all three HK names) is inside them and declined by design. The ROIC numerator stays ebit with operating_income as fallback, because that is the quantity the engine itself uses (nopat = ebit * (1 - _EFFECTIVE_TAX_RATE)) and a cross-check that silently measures something else stops being a cross-check. Recorded as an open owner question rather than decided here: ebit still carries income earned by the cash and short-term investments the capital denominator DEDUCTS, so the same money is counted in the numerator and removed from the denominator. basis["ebit_source"] records which field was used so that if the gap closes, a ROIC move is attributable to the numerator rather than to the company.

BACKWARD TEST (scripts/backtest_valuation_fixes.py --item 1.3, live FMP, as-of 2026-09-17): PENDING, shipped LIVE (applied=True). Leg B scores 9 (ticker, KPI) pairs across 3 tickers - HELPED=2, NEUTRAL=6, FALSE_ALARM=1, hit-rate 88.9% against a 50% bar, mean relative error 65.4% extracted -> 23.9% deterministic against a "no worse" bar. Both accuracy bars clear; only the >=10-pair count fails, and widening the window cannot fix that (the production archive holds 17 tickers in total). The sample is thinner than the pair count reads: 09988.HK and BABA are one issuer, so 9 pairs are about 6 independent observations across 2 issuers. The single FALSE_ALARM is the documented latest-annual-vs-TTM basis mismatch, verified against JD's FY2021-25 FCF margins (0.024943 / 0.034255 / 0.036422 / 0.038208 / 0.003770) rather than assumed - FY2025 genuinely is a trough at 0.377% while FMP's TTM spans Q4-2025..Q3-2026 and reads 2.43%; the deterministic value is arithmetically right for its own basis, and that basis is the one the DCF projects from. Observation-only was NOT chosen, and the reason has to carry its scope: for every key this pass writes, the "do nothing" arm is a number quoted out of a research report, and once the arithmetic is measurably closer to the reference there is no case for the quote - but leg A is a caveat on that, not support for it, since the key it caught drifting is the one this pass declines. The >=10-pair obligation stays open.

## 2026-09-17T04:42:35+00:00

- regenerated at HEAD: `30f2772`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Deprecate GGM book target dispatch for non-bank financials; eliminate synthetic positive tangible book fallback. Owner decisions 2a and 2b, 2026-09-17. EXPECTED MOVERS, BOTH INVESTIGATED BEFORE REGENERATING: V (12m_pt_method "GGM target P/B x book value per share" -> "convergence toward normalised-earnings intrinsic value (35% of the IV-spot gap)"; targets 9.63 / 12.84 / 16.06 -> 340.85 / 394.12 / 433.09; base IV UNCHANGED at 428.47) and SCHW (targets 29.68 / 39.57 / 49.47 -> 33.31 / 44.41 / 55.52, +12.23% on all three; GGM provenance ROE 20.8% -> 22.6% at target P/B 1.42x -> 1.59x; base IV UNCHANGED at 75.41). Twelve fixtures byte-identical (02888_HK, 09988_HK, AAPL, BABA, BN4_SI, C38U_SI, COST, D05_SI, FCX, MELI, MU, U96_SI); no base IV, scenario IV, iv_multi, method value, effective weight or gate record moves anywhere. Decision 2c (the two-sided 12m PT band) is deliberately NOT in this regeneration and ships as its own commit with its own diff.

THE DEFECT. The 12-month target dispatched on the SECTOR:

    _use_pe_only = (profile_name in _BANK_PROFILES or sector == "Financials")

so every Financials name was priced off a Gordon Growth target P/B applied to book value, including fee-driven franchises with no deposit base and no meaningful tangible book. When the method needed a tangible book per share and did not find a positive one, the engine supplied one. Visa, on the baseline being replaced: a 12m target of $12.84 against a base IV of $428.47 - 3.0% of its own valuation - published as "GGM target P/B x book value per share". ICE took the same path for the same reason.

WHAT SHIPPED.

2a. The dispatch now reads `_use_pe_only = _bsf_is_financial` - the classification `_gate_balance_sheet_financial` already made ~1900 lines earlier in the same loop body, when Phase 1.2A stripped the EV/DCF/FCF legs. Reusing that value rather than calling `_is_balance_sheet_financial` a second time is the substance of the change: the legs the IV is built from and the method the 12m target is built from now come from one answer, and cannot drift apart. Released: Market Infrastructure, Market Infrastructure (SG), Payment Networks, Real Estate Asset Manager (SG), plus any Tier-2 profile measuring below the customer-balance ratio. All 16 Tier-1 profiles unchanged, including Brokerage (owner decision 4).

2b, three parts. (i) The floor `max(_eq - _gw - _in, _eq * 0.70)` is deleted from `_bank_ggm_assumptions`, where that expression actually lives - see "A naming discrepancy in the instruction" below. The `if _teq > 0` guard immediately beneath it already handled a negative correctly by declining the midpoint and falling back to the profile's target ROE; the floor's only effect was to defeat that guard, turning "cannot measure realised RoTE" into "measured a RoTE on an invented book". (ii) `_compute_ggm_pb` gains a hard stop: a tangible book per share <= 0 returns None, computed independently as the reported FMP `tangible_book_value_per_share` when present (accepted even when NEGATIVE, which is precisely the value `_compute_bank_metrics` discards) and otherwise as (equity - goodwill - intangibles) / shares with no floor. `_compute_method_value` propagates None and `_blend_methods` renormalises over the survivors, so the weight is dropped rather than reallocated by hand. (iii) `_compute_ggm_pb`'s docstring records the new failure mode.

EXPECTED MOVES, AND ONLY THESE. 2 of 14 fixtures move; twelve are byte-identical (02888_HK, 09988_HK, AAPL, BABA, BN4_SI, C38U_SI, COST, D05_SI, FCX, MELI, MU, U96_SI). NO BASE IV MOVES ANYWHERE - V stays at 428.47 and SCHW at 75.41, and iv_multi, method_iv_table, effective_weights, every scenario IV and every gate record are unchanged on both. Both movers differ only in the 12m target and the flag strings that quote it.

V - 2a, and 2a alone. `12m_pt_method` goes from "GGM target P/B x book value per share" to "convergence toward normalised-earnings intrinsic value (35% of the IV-spot gap)", and the targets go 9.63 / 12.84 / 16.06 -> 340.85 / 394.12 / 433.09. The chain, all of it printed by the replay:

    [V] Profile: Payment Networks | Anchor: P/E (norm) | WACC=7.3%
    [convergence-cap] V: high_sbc=False reaccel=False max_capture=35% spot=$375.62
      bear: pt 314.29 -> $341   base: pt 425.97 -> $394   bull: pt 537.16 -> $433

Reclassified as a non-balance-sheet financial, V takes the standard forward waterfall, which on its own produced base $425.97 against a base IV of $428.47 - a 0.6% gap, i.e. a sane target. That is the whole fix. The convergence branch then fires on top of it, because V's anchor is "P/E (norm)" and its new label IS in `_FORWARD_CONSENSUS_PT_LABELS`, so `_norm_led` is now True and the same mechanism shipped for MU replaces the waterfall value with `_convergence_bound(scen_iv, spot, 0.35) = spot + 0.35 * (scen_iv - spot)`:

    base  375.62 + 0.35 * (428.47 - 375.62) = 394.12
    bear  375.62 + 0.35 * (276.28 - 375.62) = 340.85
    bull  375.62 + 0.35 * (539.81 - 375.62) = 433.09

All three reproduce to the cent, ordering is preserved, and every target now sits between its own scenario IV and spot. `_gate_balance_sheet_financial`'s docstring already anticipates this class of consequence as deliberate: a name re-anchored onto "P/E (norm)" that reaches a forward-consensus label "newly takes the normalised-earnings convergence path". It was a prediction; it is now a measurement.

Consequence for decision 2c: with base IV 428.47, the band [0.33x, 2.50x] is [141.4, 1071.2] and all three of V's new targets sit inside it. 2a resolves Visa unaided and 2c becomes a backstop for V rather than the fix. MELI is the only remaining baseline firing (bear 1241.83 against its own bear IV of 4588.16 = 0.271x), and MELI's own fixture flag says why - "Cash-conversion (observed, not applied): ... reported FCF is 2.0x what a repeatable basis supports" - i.e. the observation-only 1.2B gate the owner chose not to apply. That is an IV-side defect the PT band cannot distinguish from a PT-side one, and it belongs to 2c's diff, not this one.

SCHW - 2b(i), on a name predicted to be unchanged and therefore investigated rather than waved through. All three targets move by exactly +12.23% (29.68 / 39.57 / 49.47 -> 33.31 / 44.41 / 55.52) and the flag's own provenance string names the cause:

    - GGM (P/B): target P/B 1.42x -> $39.57/sh [ROE 20.8% (realised+target midpoint), CoE 10.0% (profile), g 3.5% (profile)]
    + GGM (P/B): target P/B 1.59x -> $44.41/sh [ROE 22.6% (realised+target midpoint), CoE 10.0% (profile), g 3.5% (profile)]

Same source, same CoE, same g - only the ROE moved, and it moved because the floor was binding. SCHW's 2026-06-30 balance sheet in the fixture: equity $49,884m, goodwill $12,298m, intangibles $7,300m, so true tangible equity is $30,286m = 0.607x equity. Goodwill and intangibles are 39.3% of equity, above the floor's 30% tolerance, so `max(_teq, 0.70 * equity)` substituted a book 15.3% larger than the one SCHW actually has, understating realised RoTE, understating the midpoint against Brokerage's 16% target ROE, and understating the target P/B. Uniform across the three scenarios because the GGM value is scenario-independent.

SCHW's base IV does not move, and that is the point worth pinning: its blend is P/E (norm) 0.5833 + P/BV 0.4167 with `weight_dcf = 0.0` and no GGM leg at all, so the GGM value reaches only the 12m target. Owner decision 4 is intact - 1.2A still strips the EV/DCF/FCF legs on Brokerage ("[SCHW] Balance sheet financial: EV/DCF/FCF legs removed (40% of profile weight)") and the DCF weight stays 0. This move is a book-value basis correction, not the re-admission of a cash flow method; the owner's direction for SCHW's undervaluation (normalised earnings and through-cycle NIM in the Forward P/E and Residual Income legs) is still open.

A corroboration that was already in the code. The card's RoTE divergence guard compares the ROE being valued against `_compute_bank_metrics["rote"]`, which is `net_income / tbv` where `tbv` is BACK-SOLVED from FMP's reported `tangibleBookValuePerShare` (17.4559 for SCHW at 2026-06-30) whenever that is positive - so it was never floored. It reported "29.2% realised in the filings", which is exactly the realised RoTE the unfloored midpoint now implies (2 x 22.6% - Brokerage's 16.0% target ROE = 29.2%), while the floored denominator had been implying 2 x 20.8% - 16.0% = 25.6%. Two halves of one card disagreed by ~360bps, and part of the divergence the guard was flagging for a human to investigate was created by the engine's own floor rather than by anything in the filings. After this change both read 29.2% and the guard's residual -655bps is the genuine mean-reversion haircut it exists to surface.

WHAT THE BASELINE DOES NOT EXERCISE, stated plainly. The `_compute_ggm_pb` hard stop never fires on any of the 14 fixtures. V no longer reaches the GGM path (that is 2a), and no other fixture has a non-positive tangible book. Its verification is the 30 unit tests in `tests/test_valuation_fixes_0917.py` - reported negative TBVPS, derived negative TBV, zero, a positive reported book, the JPM-shaped over-strip case, and a missing field - plus the assertion that `_compute_bank_metrics` still SYNTHESIZES a $5.60 book for the Visa-shaped row, which is the fabrication the hard stop bypasses rather than the floor having been quietly deleted where it still earns its keep. Same shape of gap as decision 1: the golden baseline is a weak instrument for a path no fixture takes, and closing it means recording a fixture from a production run of a released profile.

A NAMING DISCREPANCY IN THE INSTRUCTION, and how it was resolved. The instruction says "In `_compute_ggm_pb`, delete the artificial floor `max(_eq - _gw - _in, _eq * 0.70)`" - but that expression does not appear in `_compute_ggm_pb`. It lives in `_bank_ggm_assumptions`. The similarly-shaped floor in `_compute_bank_metrics` is `max(equity - (goodwill or 0) - (intang or 0), equity * 0.70)` and is DELIBERATELY LEFT IN PLACE: its comment records that JPM's blind-strip derivation over-strips by ~$15B against the issuer's own convention (which retains MSRs as tangible), producing $90.81/sh instead of the reported $106.85/sh, and that floor feeds P/TBV and Residual Income for every bank. Deleting it would have moved bank valuations well outside this decision's scope and regressed a real bank for a derivation artifact. So: the quoted expression is deleted where it exists, the hard stop goes in the named function, and `_compute_ggm_pb` keeps reading the floored metric for its `_return_on_book_basis` conversion so no genuine bank's multiple changes basis. Both halves are pinned by test.

ALSO IN THIS COMMIT. `tests/test_valuation_fixes_0917.py` (30 tests: the dispatch classification over all released and Tier-1 profiles, Brokerage kept per decision 4, the hard stop, the midpoint, and source pins on both floors). `tests/test_valuation_fixes_0916b.py` gains a class docstring on `TestNormalisedEarningsTargetConverges` carrying this reason string verbatim, as instructed; its assertion is unchanged, because `_FORWARD_CONSENSUS_PT_LABELS` is not edited - the GGM label was never a forward-consensus path, and 2a is what now keeps a non-bank from reaching it.

## 2026-09-17T05:32:25+00:00

- regenerated at HEAD: `d609c65`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Two-sided 12m PT band [0.33x, 2.50x] of each scenario IV - owner decision 2c, 2026-09-17. EXPECTED MOVER, INVESTIGATED BEFORE REGENERATING: MELI ONLY, 8 leaves - 12m_targets bear/base/bull 1241.83 / 1763.50 / 2273.31 -> 1554.60 / 3141.26 / 3334.71 (+25.19% / +78.13% / +46.69%); 12m_pt_method "EV/EBITDA or EV/Revenue forward multiple" -> "validation fallback: base IV / (1 + CoE 11.44%) x 0.75/1.00/1.25, bounded to [0.33x, 2.50x] of each scenario IV"; gate_metrics gains pt_over_scenario_iv; and all three scenarios.<s>.forward_flags gain one "VALIDATION ERROR: 12m PT band violated" line. BASE IV UNCHANGED at 5578.42 and NO scenario IV, iv_multi, method_iv_table value, effective weight, wacc or growth leaf moves anywhere. Thirteen fixtures byte-identical (02888_HK, 09988_HK, AAPL, BABA, BN4_SI, C38U_SI, COST, D05_SI, FCX, MU, SCHW, U96_SI, V) - V measures 0.886x / 0.920x / 0.802x of its own scenario IVs post-2a and does not fire, which is why 2c is a backstop for Visa rather than the fix.


## 2026-09-17T08:54:12+00:00

- regenerated at HEAD: NOT REGENERATED — the diff is empty, measured (17 passed in 42.58s) and explained below
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Reject non-positive FCF yields; drop the leg when the target yield is invalid — owner decision 5, 2026-09-17. ZERO EXPECTED MOVERS, and the reason is structural rather than lucky. A LATENT FUTURE MOVE IS RECORDED HERE BECAUSE THIS ENTRY IS THE ONLY PLACE IT WILL BE VISIBLE: `_clean` has exactly one call site (`compute_medians`, the weekly WRITE path) and `load_comps` returns stored rows without re-filtering them, so a replay reads medians computed under whichever band was live when that fixture's comps were last refreshed. After the next refresh the stored medians change, and a future re-record will therefore differ from today's for a reason that appears nowhere in that future diff.

THE DEFECT. `dcf_agent._compute_method_value`'s FCF-Yield leg read `target_yield = max(target_yield, 0.01)`. A non-positive peer median is not a cheap stock, it is the absence of a benchmark, and flooring it manufactured one: the leg then publishes `fcf_per_share / 0.01` = 100x free cash flow per share. SCHW's industry cohort `Financial - Capital Markets` measured -0.003152 on production in BOTH the `all` (18 peers) and `large` (10 peers) cohorts, so a $10.00 FCF/share name would have published $1,000.00 — a 100x capitalisation, inside a silent clamp, on the blend's own terms. Replaced with `if target_yield <= MIN_VALID_FCF_YIELD: return None`, which drops the leg and lets the blend renormalise over the survivors: the same answer decision 2b gives a non-positive tangible book, because unavailable is not invented.

WHICH HALF ACTUALLY DOES THE WORK, measured rather than assumed, because the two halves of this decision are not equally load-bearing and describing them as one mechanism would be wrong. IT IS THE BAND, NOT THE DROP. When a cohort's median becomes unstorable the leg does not necessarily disappear: `get_sector_peer_multiples` layers the static table UNDER the cohort per field (`merged = {**static}`, then `for field, row in regional.items(): merged[field] = row["value"]`), so a vanished cohort row reverts to the static value — `table.get(profile_name) or table.get(sector) or {}`. All 39 entries of the static table carry an `fcf_yield`, and every one is already inside the new band (range 0.02 to 0.075), so the leg survives on a static-sourced figure stamped `basis: "static"` in `_comp_basis`, which is the provenance mechanism that exists precisely so a fallback is never silent. For SCHW specifically that means its corrupt cohort reverts to the static Brokerage 0.055 rather than dropping — moot in practice, since 1.2A strips FCF Yield from Brokerage entirely and SCHW dispatches no FCF leg at all.

The leg drops only when the DIVIDED target yield is <= 0.005. The divisor `sm * growth_premium` reaches 1.25 x 1.80 = 2.25, so a stored cohort median can only drop if it is <= 0.005 x 2.25 = 0.01125, and then only in a high-divisor scenario. Full census of the 1040 live `fcf_yield` rows (read-only, newest computed_at 2026-09-13): 297 (28.6%) are <= 0, 50 (4.8%) sit in (0, 0.005], 4 (0.4%) exceed 0.25 — together 351 rows (33.8%) that the band makes unstorable at the next refresh; 59 (5.7%) sit in (0.005, 0.01125] and are the ONLY rows whose leg can ever drop; 630 (60.6%) sit above 0.01125 and always survive every scenario. Smallest positive value on production: 0.000116, which under the old floor became 0.01 — a 96x error. So the drop is a narrow, scenario-dependent backstop and the band is the fix; the 100x risk is eliminated because the corrupt median can no longer be stored, not because the leg refuses it.

The check is on the DIVIDED target yield, after `/(sm * growth_premium)`, which is where the deleted `max()` sat and is not the same test as checking the raw median. That divisor reaches 1.25 x 1.80 = 2.25, so a median that is valid as a reading can still produce an absurd required yield: 0.006 survives at sm=1.0/gp=1.0 and drops at 0.006/2.25 = 0.002667 in a bull scenario. A consequence worth stating plainly because it is not a bug: at the band's lower edge the three scenarios can carry different `methods_used` lists for a reason that has nothing to do with the company. `peer.get("fcf_yield", 0.05)` deliberately survives the check — a missing key is data absence, not an invalid reading, and 0.05 clears the floor.

WHAT THE BASELINE DOES NOT EXERCISE. Measured across all 12 FCF-leg calls the 14 fixtures make — AAPL peer +0.035 giving target yields 0.025437–0.046667, COST peer +0.020 giving 0.014710–0.026667, V peer +0.030 giving 0.020973–0.040000 — the old `max(target_yield, 0.01)` NEVER BOUND ONCE. SCHW makes no FCF-leg call at all (1.2A strips FCF Yield from Brokerage, so its methods are exactly `["P/BV", "P/E (norm)"]` in all three scenarios). The defect was production-only, so the golden baseline cannot see this fix; that is asserted rather than noted, by four pins in `tests/test_valuation_fixes_0917d.py` covering the call count, the yield ranges, a grid over the seven measured `growth_premium` values, and the unchanged published FCF-Yield values (AAPL 134.25466 / 191.00852 / 246.29898, COST 633.88736 / 903.05734 / 1149.11953, V 270.15654 / 410.29821 / 515.23788, to 5e-3). Verification is instead the 37 unit tests there plus a direct read of production Postgres: 1040 `fcf_yield` rows across seven exchanges, of which 297 (28.6%) are <= 0, 50 (4.8%) sit in (0, 0.005], and 4 (0.4%) exceed 0.25.

THE HIGH SIDE IS A SECOND EFFECT AND IS NAMED AS SUCH. Tightening to `(0.005, 0.25)` also drops four rows above 0.25 (SHH +0.345632 on 11 peers, SHH +0.338160 on 9, HKSE +0.338160 on 9, US +0.250729 on 6). Those are distress or a one-off cash flow, not a benchmark, so the drop is right — but it is 4 of 1040, and it is a consequence of the same decision rather than the decision itself.

AN EXISTING TEST ENDORSED THE DEFECT. `tests/test_regional_comps.py::TestClean::test_negative_fcf_yield_is_kept` asserted `rc._clean("fcf_yield", [-0.03, 0.05]) == [-0.03, 0.05]` under the docstring "Unlike P/E, a negative FCF yield is meaningful and in-band." This was not a coverage gap — it was coverage that argued for the bug, and the argument is not silly, so the inversion is recorded rather than quietly rewritten. The distinction it draws is the wrong one: a P/E is a MULTIPLE, where signedness is a data-quality judgement about what a loss-maker means; an FCF yield is a DENOMINATOR, where a divisor <= 0 means the arithmetic has no answer at all.

A HARNESS TRAP FOUND WHILE TESTING THIS, recorded because its failure message misdiagnoses the cause. Hoisting `from src.data.regional_comps import MIN_VALID_FCF_YIELD` to `dcf_agent`'s module scope failed 14 of 17 golden tests with `AssertionError: AAPL: 1 recorded call(s) never used — the fixture is stale.` Following that message would have regenerated 14 fixtures and baked in a replay that reaches for the network with a dummy API key. The real mechanism: `regional_comps` binds `_fmp_get` BY VALUE (`from src.tools.api import _fmp_get`), which an attribute patch on `src.tools.api` cannot retroactively fix, and `golden_replay.replay_fixture` imports `dcf_agent` at line 400 — outside the `with gc.pinned_env(), gc.Replayer(calls) as rp:` block that opens at line 409. Whether a module holds the recorder or the real function therefore depends on when it is first imported. The import is lazy inside the leg, following `sector_profiles`, which has zero module-level first-party imports and imports `regional_comps` lazily at L3203/L3206/L3257. Three tests now pin the constraint by name, including one that enumerates every first-party module binding `_fmp_get` by value and asserts none is reachable from `dcf_agent`'s module scope, so the next hoist fails with an explanation instead of a misdiagnosis.
## 2026-09-17T10:08:45+00:00

- regenerated at HEAD: `a4bce28`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Gate B's Y10 margin multiplied a one-shot absolute delta by 10; drop the surviving multiplier and publish the value under its real name (margin_delta_absolute)

THE DEFECT. Gate B read its Year-10 FCF margin as `_y10_fcf_margin = fcf_margin_base + md_abs * 10`. But `md_abs` is a ONE-SHOT absolute delta applied to EVERY year: a few lines later the same block hands it to `_project_dcf` as `margin_delta_absolute=md_abs` alongside `margin_delta_per_year=0.0`, with the inline comment "superseded by md_abs". `_project_dcf` branches on exactly that (`if margin_delta_absolute is not None: margin_t = max(fcf_margin_base + margin_delta_absolute, fcf_floor)`), so year 10 carries the delta once, not ten times. The `* 10` was left behind when an annual drift became a one-shot step — the multiplier survived the rename of the thing it multiplied, and the Tier 2a comment directly above it still described the old `fcf_margin_base x (1 + margin_delta_per_year x 10)` form, so the code read as consistent with its own documentation.

THE EXACT ERROR FACTOR, derived rather than observed. Since `md_abs = fcf_margin_base * (m - 1)` where `m = _MARGIN_DELTA_MULT[scenario]` (or the high-SBC variant), the published Y10 margin was `fmb * (10m - 9)` against a correct `fmb * m`. The projected ROIC is LINEAR in that margin — `_y10_ic = ic_val * _y10_rev_mult` and `_y10_rev = rev_base * _y10_rev_mult` carry the same multiplier, so it cancels — which makes the ROIC error exactly `(10m - 9)/m`:

    bear, m = 0.80 (standard):  -1.25x  ->  margin -fmb,      ROIC negative
    bear, m = 0.65 (high SBC):  -3.85x  ->  margin -2.5*fmb,  ROIC negative
    bull, m = 1.20 (standard):  +2.50x  ->  margin  3*fmb,    ROIC inflated
    bull, m = 1.15 (high SBC):  +2.17x  ->  margin  2.5*fmb,  ROIC inflated
    base, m = 1.00 (either):     1.00x  ->  unchanged

WHY NOTHING CAUGHT IT: the last row. In base `md_abs` is 0 — and additionally `guidance_margin_adj` is 0 for all 14 fixtures, measured — so `10 x 0 = 0` and base was arithmetically immune. ALL 14 `base_iv` VALUES ARE BIT-IDENTICAL BEFORE AND AFTER THIS FIX, and so are all 14 base `growth_premium` values (02888_HK 0.891, AAPL 1.067, COST 1.068, D05_SI 0.701, MELI 1.075, SCHW 1.104, V 1.139, the remaining seven 1.0). The defect hid behind an unmoved headline number for as long as it did because the headline number is computed in the one scenario the multiplier cannot reach. The only base leaves that moved at all moved from the new alias key plus FCX/MELI's 12m band flags.

TWO DOWNSTREAM READERS, NOT ONE, which is what makes the blast radius wider than Gate B. (a) Gate B's own threshold, `_gate_b_threshold = {"bear": wacc, "base": wacc*0.5, "bull": -inf}[scenario]`: a negative projected ROIC is below any positive WACC, so the gate fired and zeroed terminal growth. (b) The growth-premium quality gate, which reads the SAME `forward_roic` variable that Gate B's block overwrites (`forward_roic = _forward_roic_proj; roic_source = "Y10 projected"`), and computes `_quality = min(1.0, (forward_roic - wacc) / wacc)`. `_quality` SATURATES AT 1.0 for any ROIC >= 2x WACC, so a 2.5x inflation erases the distinction between a company earning twice its cost of capital and one earning ten times it — every true ROIC above 0.8x wacc was already being scored perfect. Symmetrically, in bear the forced-negative ROIC drove `_quality` to 0 and `_gp_raw` to exactly 1.0, shutting the premium off for names that had earned it. That saturation is how FCX's bull premium came to sit pinned on its 1.80 clamp ceiling.

NAMED MOVES — BEAR. Gate B DEACTIVATES on 5 fixtures, each gaining a terminal value, a real growth premium and a higher bear IV:

    02888_HK  tgr 0.0 -> 0.01   gp 1.0 -> 0.969   IV 236.57 -> 236.10   (-0.2%)
    AAPL      tgr 0.0 -> 0.02   gp 1.0 -> 1.038   IV 146.37 -> 156.17   (+6.7%)   iv_dcf 146.32 -> 168.96, tv_pct 0.5422 -> 0.6030
    COST      tgr 0.0 -> 0.01   gp 1.0 -> 1.028   IV 923.28 -> 956.33   (+3.6%)   iv_dcf 356.37 -> 390.04, tv_pct 0.5947 -> 0.6322
    MELI      tgr 0.0 -> 0.02   gp 1.0 -> 1.051   IV 4588.16 -> 4940.12 (+7.7%)   iv_dcf 3714.80 -> 4163.12, tv_pct 0.4004 -> 0.4522
    V         tgr 0.0 -> 0.01   gp 1.0 -> 1.129   IV 276.28 -> 310.93   (+12.5%)  iv_dcf 368.06 -> 409.36, tv_pct 0.6438 -> 0.6792

02888_HK IS THE ONE BEAR IV THAT FELL, and the fall is correct rather than anomalous: it is the only one of the five with no `iv_dcf` leaf in the bear column at all, i.e. its bear IV carries no DCF weight — `weight_dcf = 0.0` in all three scenarios and `methods_used = ["Excess Capital", "GGM (P/B)", "P/E (norm)", "P/TBV", "Residual Income"]`, the bank path, in which a DCF is not one of the legs. So the terminal value it gains adds nothing to the published number, while its premium moving off the forced 1.0 down to 0.969 shaves the two P/E legs that ARE in the blend (Forward P/E 142.70 -> 138.28, P/E (norm) 137.05 -> 132.80) — a net -0.2%. Naming the direction matters because "a fix that adds a terminal value must raise the IV" is the intuition a reader would otherwise apply.

BEAR SIGN FLIPS — 8 fixtures where the gate STILL FIRES, but on a positive ROIC that is genuinely below the bear threshold. Their bear IVs are BIT-IDENTICAL; the only diff in each is the flag text plus the new alias key (2 leaves apiece):

    09988_HK  -6.7% -> +5.4%  (threshold 14.4%)   IV 114.32
    BABA      -6.7% -> +5.4%  (threshold 13.1%)   IV 142.27
    BN4_SI    -2.3% -> +1.8%  (threshold 10.2%)   IV 7.37
    C38U_SI   -2.8% -> +2.2%  (threshold  5.7%)   IV 1.41
    FCX       -4.1% -> +3.2%  (threshold  7.3%)   IV 16.06
    MU        -2.0% -> +1.6%  (threshold 10.7%)   IV 222.82
    SCHW      -7.3% -> +5.9%  (threshold  6.2%)   IV 51.21
    U96_SI    -2.3% -> +1.9%  (threshold 10.4%)   IV 2.33

THESE EIGHT ARE A PROOF, NOT JUST A MEASUREMENT. Each post-fix ROIC equals -0.80x its pre-fix value to display precision, and -0.80 is exactly the correction factor `m/(10m - 9)` at `m = 0.80`. A second, unrelated defect in the same path would not leave the ratio pinned to the profile multiplier. MELI, the only high-SBC fixture in the set, obeys the OTHER factor instead — `0.65/(6.5 - 9) = -0.26` — taking -119.4% to +31.04%, which clears its 11.4% WACC and is why the largest printed error in the whole baseline is the one that DEACTIVATES the gate rather than re-signing it.

D05_SI IS THE ONE FIXTURE THE x10 NEVER REACHED. Bear IV 39.21, tgr 0.01, gp 0.65 — all three identical before and after. Its bear ROIC never tripped the gate in the first place, so its single differing leaf is the new alias key. Worth recording because it is the control: a fixture in the same baseline that the defect could have hit and did not.

THE WHOLE GOLDEN BEAR COLUMN HAD NO TERMINAL VALUE. 13 of 14 fixtures carried `tgr = 0.0` in bear before this fix; post-fix exactly the 8 legitimate failures remain at 0.0 and 6 carry a real terminal growth rate. A baseline in which a scenario-wide valuation component was uniformly absent is a baseline that could not have detected this class of error, and that is the finding to carry forward, not just the corrected numbers.

NAMED MOVES — BULL. The quality gate unbinds on SIX fixtures. **THIS CORRECTS THE FIGURE IN THE APPROVAL THAT AUTHORISED THIS WORK, WHICH SAID NINE.** The nine came from an earlier report of mine that string-matched `bull:` across the snapshot diff and counted hits inside multi-line `forward_flags` prose — flag text mentioning the bull scenario, not bull leaves. Measured from the snapshot pair, the true set is six, and `tests/test_valuation_fixes_0917e.py::test_bull_quality_gate_unbinds_on_exactly_six_not_nine` now asserts `len(_BULL_MOVED) == 6` against literal pre-fix values so the count cannot silently drift back:

    FCX       gp 1.800 -> 1.000   IV 72.62 -> 45.52    (-37.3%, the largest move in the baseline)
    SCHW      gp 1.654 -> 1.267   IV 141.18 -> 108.18  (-23.4%)
    09988_HK  gp 1.167 -> 1.000   IV 241.34 -> 213.42  (-11.6%)
    C38U_SI   gp 1.200 -> 1.000   IV 2.34 -> 2.27      (-3.0%)
    BABA      gp 1.019 -> 1.000   IV 251.56 -> 247.76  (-1.5%)
    02888_HK  gp 0.719 -> 0.815   IV 329.53 -> 331.93  (+0.7%)

Every one of those is a premium coming DOWN toward neutral, except 02888_HK, whose bull premium was a DISCOUNT (0.719) and so shrinks toward 1.0 from below — the only bull IV that rose. The other eight fixtures (AAPL 1.101, BN4_SI 1.0, COST 1.088, D05_SI 0.731, MELI 1.086, MU 1.0, U96_SI 1.0, V 1.144) are unmoved in bull and stay at their pre-fix values; their premiums were never on a clamp and their ROICs were already reading correctly or already saturating identically.

EVERY PREMIUM-SCALED LEG IN A MOVED SCENARIO MOVED WITH IT — 79 leg and blend leaves in total, and the size of that set is the finding, not a detail. `_compute_method_value` builds its multiple as `mult = <peer stat> * sm * growth_premium` in 14 of its 16 `mult` assignments, so for those legs a published value is LINEAR IN `growth_premium`; the FCF-Yield leg reaches the same place from the other side, putting the premium in the denominator of a denominator (`target_yield = peer_fcf_yield / (sm * growth_premium)`, value = `(fcf/shares) / target_yield`). THE TWO THAT OMIT IT ARE DELIBERATE AND DOCUMENTED AT THEIR OWN BRANCHES: NAV Discount (`_mnav * sm` — a market-observed mNAV already prices growth, so re-multiplying would double-count) and P/TBV (`cfg["p_tbv"] * sm` — a bank's book multiple is set by ROE vs CoE, not top-line growth, and the premium's revenue base for a bank is gross interest income). The bank legs computed outside that family (GGM P/B, Residual Income, Excess Capital) and the S-REIT DDM are premium-independent as well. A premium correction is therefore not a local edit — it re-prices every premium-scaled leg in the blend at once.

02888_HK SHOWS THE SPLIT CLEANLY, because it is the one fixture whose blend straddles both families. Its six bear legs divide exactly along the line above: Forward P/E 142.70 -> 138.28 and P/E (norm) 137.05 -> 132.80 moved, while Excess Capital 188.02, GGM (P/B) 252.98, P/TBV 170.72 and Residual Income 230.90 are BIT-IDENTICAL. The same split in bull, where the two P/E legs rise (135.22 -> 153.21, 164.23 -> 186.07) and the four bank legs again do not. Only P/E (norm) of the two is in this fixture's weighted `methods_used` — Forward P/E is recorded in the table but not blended — and nothing downstream of the blend contributed either: `iv_multi_post` is exactly 1.1 x `iv_multi` here both before and after (bear 215.0645 -> 214.6404 against 236.571 -> 236.1044, both -0.197%; bull 299.5749 -> 301.759 against 329.5323 -> 331.9349, both +0.729%), so the post-adjustment is a constant scale. That is the whole of its -0.2% bear and +0.7% bull move.

The 79 leaves are confined to EXACTLY the 11 (fixture, scenario) pairs whose premium moved: the 5 bear deactivations and the 6 bull unbindings. The 8 bear sign-flip fixtures show NO leg moves at all, their premium having stayed at the forced 1.0 — which is the independent confirmation that their bear IVs are bit-identical, arriving from a different direction than the IV comparison itself. Largest single leg moves: FCX bull `EV/EBITDA (norm)` 77.05 -> 37.22 and `SOTP (segments)` 84.54 -> 41.38, roughly halved because FCX's bull premium fell the full 1.80 -> 1.00; C38U_SI bull `Forward EV/EBITDA` 3.87 -> 3.00. On the blend side `iv_multi` and `iv_multi_post` moved on the same 11 pairs, while `iv_dcf` moved on only the FOUR bear deactivations that carry DCF weight (AAPL 146.32 -> 168.96, COST 356.37 -> 390.04, MELI 3714.80 -> 4163.12, V 368.06 -> 409.36) — 02888_HK has no `iv_dcf` leaf in its bear column at all, which is the precise mechanism behind its -0.2%.

ONE PRE-EXISTING TEST BROKE, AND IT IS THE ONLY ONE IN THE SUITE THAT DID. `tests/test_valuation_fixes_0917d.py::test_the_published_fcf_yield_values_are_unchanged` pinned nine FCF-Yield leg values in order to prove decision 5 moved none of them. Three of the nine were BEAR values on AAPL, COST and V — all three of them deactivations above — so the pin was correct when written and was broken by a later, named move rather than by a regression. It is re-struck, and re-struck as a RELATIONSHIP rather than as nine new literals: the bear value must equal its recorded pre-Gate-B figure times the premium the same fixture now publishes (`rel=1e-3`, which is the persisted premium's own 3dp rounding — the leg uses the unrounded value, so 1.03762 is stored as 1.038 and a literal-times-literal check would be loose by 4e-4), while base and bull must remain bit-identical to what decision 5 recorded. Renamed `..._match_the_current_baseline`, because "unchanged" is no longer what it asserts and a test whose name overstates its claim is how the original defect stayed hidden. The lesson generalises and is worth stating plainly: ANY pin on a premium-scaled leg value is implicitly a pin on `growth_premium`, so a premium fix will always look like a regression to it.

NAMED MOVES — 12m TARGETS. FCX's target band FIRES FOR THE FIRST TIME: its bear PT of $42.71 was 2.659x its own bear IV of $16.06, outside the `[0.33x, 2.50x]` band that decision 2c introduced. All three targets are replaced — base 38.37 -> 23.85, bear 24.09 -> 17.89, bull 70.50 -> 29.81 — `12m_pt_method` flips to "validation fallback: base IV / (1 + CoE 7.25%) x 0.75/1.00/1.25, bounded to [0.33x, 2.50x] of each scenario IV", `gate_metrics` gains `pt_over_scenario_iv` alongside `peak_trigger`, and a VALIDATION ERROR flag appears in all three scenarios. This is the band doing its job on a name whose bear IV fell by more than half: the old PT was struck against an inflated bear case and would otherwise have been published unchanged.

MELI's floor mechanics move as expected. Its bear ratio falls 0.271x -> 0.251x, so `12m_targets.bear` rises 1554.60 -> 1630.24 (base 3141.26 and bull 3334.71 unmoved), and that re-triggers the POLICY CONFLICT flag: "bear band floor 1,630.24 exceeds the high-SBC ceiling 1,554.60 — band floor kept" (the ceiling being spot $1,828.94 x `_HIGH_SBC_BEAR_CEILING_MULT` 0.85), present in all three scenarios. The floor wins because it is the owner-specified bound and the ceiling is a heuristic; the conflict is surfaced rather than resolved silently, which is the behaviour 2c was written for.

The remaining 12m moves are incidental consequences of the IV changes above and are named for completeness: 02888_HK bear 233.78 -> 233.55 and bull 280.26 -> 281.47; BABA bull 180.47 -> 178.57; C38U_SI bull 2.29 -> 2.26; COST bear 912.32 -> 928.84; V bear 340.85 -> 352.98. NO MOVE OUTSIDE THIS NAMED SET OCCURRED — the delta was computed by script from the two snapshot files (`scratchpad/golden_delta_gateB.py`, pre-fix baseline preserved at `scratchpad/snapshots_prefix.json`), not transcribed from a replay log, precisely so that claim is checkable.

THE LEDGER RENAME IS A PAYLOAD CONTRACT CHANGE, so both keys are written. `margin_delta_absolute` is authoritative; `margin_delta_per_year` remains as a permanent read-compatibility alias carrying the identical value, because archived `web_runs` rows and already-deployed frontend builds read the old name and would render a blank cell otherwise. Consumers updated: `golden_replay._SCENARIO_KEYS` now pins BOTH spellings, so a replay that ever lets them diverge fails the baseline instead of shipping the divergence; `pdf_report._margin_delta_abs` prefers the new key and falls back to the old; `DcfMethodologyPanel.tsx` reads `c.margin_delta_absolute ?? c.margin_delta_per_year` under a header now reading "Margin Δ (Y1–10)"; `reportTypes.ts` carries the new field plus a `@deprecated` JSDoc on the old one explaining it never was an annual drift. 42 new leaves enter the baseline (14 fixtures x 3 scenarios x 1 new key); alias equality violations across all 42 pairs: NONE. `web_run.json` in `tests/fixtures/golden/*/` is a replay INPUT but `build_state` does not read either key, so old fixtures are harmless and the replay recomputes `md_abs`. The 9 historical occurrences in `tests/audit/fixtures/{AAPL,DLR,NVO}__*.json` are left as-is — they are records of what was published, not inputs.

A SECOND MANIFESTATION IN `pdf_report`, WITH ITS MATERIALITY STATED NARROWER THAN FIRST REPORTED. Both sensitivity grids rebuilt the projection as `margin + delta * t`, drifting the margin over ten years when the engine steps once and holds flat. **An earlier report of mine claimed "every bear grid cell used a margin the engine never used." That was false and is corrected here: there ARE no bear grid cells.** Both grids read `dcf_ticker.get("base")`, so the delta they mis-scaled is the BASE delta — which is `guidance_margin_adj`, and is 0 for every one of the 14 fixtures and for any run where management guidance does not move the margin. `0 * t` is still 0, so for those runs the two formulas agree exactly and the misreading produced identical output. It was LATENT, NOT INERT. For a guided name with a non-zero base delta it was very live: on a synthetic base (rev 10e9, fmb 0.20, gr 0.10, wacc 0.09, tgr 0.02, floor -0.05, net_debt 1e9, shares 10e9), `md = -0.04` gave engine 4.1374 against the old grid's -0.7493 (118.1% divergence) and `md = +0.04` gave 6.2561 against 13.9285 (122.6%); at `md = 0`, 0.0%. Worse, the grid's own `_sens_warn` check would have fired blaming "revenue_base or shares_outstanding unit mismatch (check FX conversion)" — sending the reader to two fields that were perfectly fine. Separately, `_section_2f`'s traceability table had the ARITHMETIC right all along (`bear_yr1_fcf = (bear_fcf + bear_md)*100`, one step) and the LABEL wrong: `_pct_delta` printed `±X.XX%/yr` under a row headed "Margin delta / year". THAT MISLABEL WAS UNCONDITIONAL — it misdescribed every PDF ever produced. It now reads `±X.XXpp` under "Margin delta (one-shot, Y1–Y10)". All three docstrings (`_margin_delta_abs`, `_sensitivity_table` CHECK 3, and the test module) state the corrected scope, and `test_both_grids_read_the_base_scenario_so_the_delta_is_usually_zero` pins the base-only reading so a future edit that passes a bear or bull block into a grid re-checks the delta.

WHAT IS PINNED. `tests/test_valuation_fixes_0917e.py`, 63 tests, in six sections: (A) the arithmetic — the multiplier is gone from the assignment in six spacings including `md_abs * _PROJECTION_YEARS`, the delta is still handed to the projector as absolute, the buggy margin was an exact multiple of the right one across 2 profile families x 3 scenarios, the four ROIC error factors, the eight sign flips obey `-0.80x` to a tolerance of `0.80*0.05 + 0.05 = 0.09` (both sides are one-decimal printouts, so the two rounding half-widths compose; FCX's -4.1 -> +3.2 needs 0.08), MELI obeys the other factor, and `_project_dcf` holds a one-shot delta flat for ten years while still drifting on the legacy per-year branch; (B) the coupling — source order, the threshold block, a ROIC at or below WACC forcing the premium to exactly 1.0, saturation at 2x WACC, and the forced-shut premiums at their baseline values; (C) the ledger — both keys written, both pinned, alias equality across 14x3, base delta 0.0 x14, and `md_abs == round(fmb*(m-1), 4)` recomputed from each fixture's OWN profile with its sign checked; (D) the PDF — helper precedence and never-raises over 5 payloads, neither grid scaling by year (4 forbidden forms, the step hoisted above the `for t` loop and appearing exactly once), the `_section_2f` label, the base-only reading, the grid centre reproducing the engine IV to 0.005 with no divergence warning, the warning firing when the stored IV really is wrong, and the verbatim pre-fix closure transcribed as a control at the two measured divergences; (E) the named moves — every literal in this entry, including the six-not-nine bull count; (F) the leg-wide scaling — the 14-of-16 `mult` census with the two documented exceptions named by their exact source lines, the FCF-Yield leg's denominator form, 02888_HK's six legs and its constant 1.1 post-scale, and the absence of a DCF leg there (`weight_dcf = 0.0` and `iv_dcf` present-but-None in all three scenarios, a distinction an earlier draft of that test got wrong by using `.get()`).

AN OBSERVATION FOUND WHILE VERIFYING (F), recorded because it is a real inconsistency and this entry is the only place it will surface. `method_iv_table` and `methods_used` are NOT the same set. The table has at least one leg the weighted list lacks in ALL 42 (fixture, scenario) pairs — AAPL's table carries four such legs — and the weighted list names a leg with no table entry in exactly six pairs (BN4_SI bear `EV/EBITDA` and base `DCF`, MELI `Power Law Score` in all three, U96_SI base `DCF`). The natural reading is that the table records every leg COMPUTED while `methods_used` records the subset that carries weight, which would make the forward multiples and the SOTP legs informational; but that is inference, not something this fix establishes, and the six reverse cases do not fit it cleanly. `test_the_leg_table_is_a_superset_of_the_weighted_set` pins both directions as exact literals so the gap cannot widen silently, and settles nothing about whether it should be there.

CORRECTIONS TO EARLIER REPORTS, listed together because five separate claims of mine were wrong and only measurement caught any of them: (i) bull affected SIX fixtures, not nine — the nine came from matching `bull:` inside multi-line flag prose rather than reading bull leaves; (ii) the standard bull margin multiplier is 1.20, not 1.15 — 1.15 is `_MARGIN_DELTA_MULT_HIGH_SBC`, and the factor table above is now read from `_scenario_mults_for_profile` rather than hardcoded; (iii) bear `growth_premium` moves off the forced 1.0 for FIVE fixtures (exactly the five deactivations), not two; (iv) the `pdf_report` blast radius, corrected above — "every bear grid cell used a margin the engine never used" was false, because there are no bear grid cells; (v) a first draft of this very entry claimed every multiples leg is linear in the premium, which the source contradicts — 14 of 16, with NAV Discount and P/TBV deliberately exempt, and 02888_HK is the fixture that proves the exemption is real. Items (iii), (iv) and (v) were all caught by writing the number down and then going to look for it.

VERIFICATION. Regenerated at HEAD `a4bce28`, `_meta.generated_at` 2026-09-17T10:08:45+00:00, fixtures recorded at `30b26702d3c786e1835dd8e5cc629191e3d75c95`, 17 golden tests passed in 45.67s. Full suite after the fix, the re-baseline and the re-struck decision-5 pin: **4784 passed, 1 deselected, 0 failed in 251.15s** (the deselect being the pre-existing `tests/test_intl_provider.py::TestApiRouting::test_branches_are_guarded`). The secret gate was run over all 45 committed and fixture paths with 8 of 9 `SECRET_ENV_VARS` armed — `leaked: []`. It reports clean with only 3 armed if `.env` is loaded instead of `.env.local`, and FMP_API_KEY is among the 6 it then misses, so the armed count was checked and not assumed; that under-arming is filed separately. Frontend: `tsc --noEmit` and `eslint --max-warnings 0` both clean on the two changed files. The TSX change is NOT browser-verified — `app/frontend` has no test runner, and exercising the panel needs an archived run carrying `dcfRange` scenario data; the type-level fallback is what the two static checks cover.

## 2026-09-17T12:40:34+00:00

- regenerated at HEAD: `18688c6`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Item 3b: persist the audit fields that drove two gates (forward_roic, roic_source, sector_g_avg and its cohort, composite_bridge including bank_clamp, normalized_net_income). Verified ADDITIVE before regenerating: 14/14 fixtures replayed with removed={} changed={} and base_iv identical; 45 new leaves per fixture, 46 on the two banks that carry bank_clamp, 39 on U96_SI whose sector_g_avg_basis is None.

NO VALUATION MOVED. This entry is additive by construction and was proven additive before the baseline was regenerated, not after. All 14 fixtures were replayed against the previous snapshot and classified leaf by leaf (`scratchpad/drive_delta_additive.py`, one subprocess per fixture — replaying several in one interpreter contaminates module-level caches and produces a base_iv the harness never would): **0 removed leaves, 0 changed leaves, and all 14 `base_iv` values bit-identical**. Re-checked against the regenerated file: same result. 671 new leaves across the 14 fixtures, and nothing else. If a number below looks like a move, it is a number that was always there and could not previously be read.

WHAT WAS ADDED, and the cost that justified it. The baseline could see `growth_premium` and `tgr` — the OUTPUTS — but not `forward_roic`, `roic_source` or `_sector_g_avg`, the INPUTS that produce them; nor the composite's decomposition behind `composite_applied`; nor `normalized_net_income`, which several legs divide by and none recorded. The price was paid in full during the Gate B audit immediately above: to establish which fixtures had terminal growth zeroed and why, the ROIC had to be regex-ed back out of its own flag prose, `"Gate B (bear): Forward ROIC (-7.3% [Y10 projected]) < threshold (5.9%)"`, first across 49 archived production rows and then again across four live ones. That is offline blind-fitting, and blind-fitting is what let the `× 10` survive as long as it did — with the ROIC in the payload, `tgr == 0` sitting beside `forward_roic > wacc` is a contradiction a diff can see. Four fields are now persisted: `forward_roic` and `roic_source` per scenario, `sector_g_avg` and `sector_g_avg_basis` per scenario, and `composite_bridge` and `normalized_net_income` at top level (the bridge is computed once before the scenario loop, so triplicating it would have made a per-scenario divergence representable — and silently possible).

LEAF CENSUS, because the counts differ and the differences are findings. 45 new leaves on twelve fixtures; **46 on 02888_HK and D05_SI**, the extra one being `composite_bridge.bank_clamp`, which exists only where the clamp fires; **39 on U96_SI**, six fewer because `sector_g_avg_basis` is `None` there rather than a three-field dict, in all three scenarios.

THE NEW INVARIANT, which is the one that actually closes the loop. The Y10 ROIC is linear in the Y10 FCF margin — `_y10_ic` and `_y10_fcf` carry the same revenue multiplier, so it cancels — and that margin is `fcf_margin_base + margin_delta_absolute`. All three terms are now in the same document, so

    roic[s] / roic[base]  ==  (fmb + md[s]) / (fmb + md[base])

must hold, and since `md = fmb * (m - 1)` the `fmb` cancels too, reducing the right side to `_MARGIN_DELTA_MULT[s]`: **bear/base is 0.80 and bull/base is 1.20 on all thirteen fixtures that compute a ROIC, and 0.65 / 1.15 on MELI, the only high-SBC profile in the set.** Under the defect above, bear's margin was `fmb * (10m - 9)` instead of `fmb * m`, which puts the left side at −1.00 where the right side says 0.80. The Gate B `× 10` is now detectable from the payload alone, with no prose and no regex. Both tests carry a quantisation-derived tolerance rather than a flat one, because every input is published at four decimals and for a fixture like COST (`fmb` 0.0232, `md` −0.0046, itself −0.00464 rounded) a single rounding moves the expected ratio by 1.8e-3 — the whole of the observed gap. Tolerance lands under 1% against a 225% error, so the headroom is three orders of magnitude.

VERIFIED BY MUTATION, not by passing. `scratchpad/mutation_check_0917f.py` applies twenty mutations to an in-memory copy of the regenerated snapshot — each one a defect these fields exist to catch — and then runs **every test in the module** against it, not just the ones the table predicts. Three things are scored: a CONTROL pass requiring all 46 test functions to pass against the unmutated snapshot first, and refusing to score anything if one does not (without it, a test failing for an unrelated reason "catches" every mutation aimed at it and the count still reads 20/20); at least one test tripping per mutation; and every predicted catcher actually tripping, a prediction that does not come true being scored as a failure because it means the table asserts coverage the suite does not have. **Result: 20 of 20 caught, 0 survived, 0 unresolvable names, 0 wrong predictions.**

Three of the twenty are separate reconstructions of the `× 10`. SCHW's bear ROIC sign-flipped to −5.87% is caught by five tests: the two ratio tests, the survivors-are-positive census, the field-vs-flag-prose equality, and the test pinning that SCHW fires by six percent of WACC. V's bear ROIC set to `fmb*(10m-9)` — the defect's actual arithmetic, which re-signs it to −41.72% — is caught by six: the two ratio tests, the five-deactivations census, the quality-gate saturation set, and both tests that read the bear `tgr` column against the ROIC. AAPL's inflated tenfold with the sign kept is caught by three: the two ratio tests and monotonicity.

Monotonicity catches the third and NOT the second, and the difference is worth recording because it is the kind of thing a narrower sweep hides: `−0.4172 < 0.4172 < 0.5007` is still in ascending order, so a sign flip that preserves magnitude is invisible to a test that only compares the three scenarios against each other, while `4.423 > 0.5529` is not. Only the arithmetic relationship to `margin_delta_absolute` catches both. That relationship is what closes the real coverage gap — the five deactivated fixtures have no Gate B prose left to compare a mutated field against, so nothing else in the payload can see their ROIC move.

The other seventeen: the margin delta reverting to a per-year drift; the persisted ROIC disagreeing with the flag prose that used to be its only record; `roic_source` drifting from the bracket the flag prints; monotonicity broken in bear; D05_SI growing a ROIC it never had; the bank clamp ceasing to be applied; Decision 1 regressing so SCHW is clamped again; `was_capped` disappearing; a cohort flipping to `large` (which is item 3a landing, and the reason the basis leaf exists); a live cohort falling back to `static`; U96_SI gaining a measured average; `normalized_net_income` mis-scaled across the two Alibaba lines; the Normalized-NI prose diverging from the field; a sub-score note going silent; the weights ceasing to sum to one; a fixture silently starting to extract KPIs; and the z-score path switching on.

FINDINGS THE NEW LEAVES PRODUCED ON FIRST READING. These are the return on the change and none of them required a new run.

*D05_SI has no ROIC at all* — `forward_roic` is `None` with `roic_source` "n/a" in all three scenarios, the exhaustive chain's else branch, meaning neither the Y10 projection nor the trailing fallback produced a value. That is why Gate B never reached it, and it settles a question the previous entry could only answer by inference: pre-3b, "the gate did not fire" and "the gate had nothing to judge" were the same absence of a flag.

*The cohort census, which is item 3a measured from the baseline rather than re-derived.* All eight US fixtures resolve `growth_avg` as `{basis: static, cohort: US, peer_count: None}`, so `_peer_for_gp` omitting `market_cap` cannot change a US name's cohort — there is no live cohort to change. All five HK/SG fixtures that resolve at all resolve a LIVE cohort, and **every one of them is the whole-grouping `all` cohort, never the size-matched `large` one**: 02888_HK `industry/all` n=7, 09988_HK `industry/all` n=18, BN4_SI `sector/all` n=22, C38U_SI `industry/all` n=5, D05_SI `sector/all` n=9. The three legs inside `_compute_method_value` pass `(_market_cap or revenue_base * 10)` and get `large`. So the divergence is confined to exactly the HK/SG set, as predicted, and the baseline now says so without an audit script.

*02888_HK carries a sector growth average of +45.92%* from seven peers — the single largest exposure in item 3a. `_gp_raw_growth = 1 + 0.30*(g - avg)/avg` is INVERSELY proportional to that average, so a bank growing at a few percent against a 45.9% benchmark has its raw growth term pushed well below 1.0, and the quality gate then lifts it only part way back to the 0.891 its base premium shows. A production census of `regional_comps` found 436 groupings carrying both cohorts and **401 of the 436 disagreeing**; for 02888_HK's own grouping the whole-industry average is +17.11% against +4.88% for large-caps, a −12.24pp spread that moves the growth premium from 0.788 to 1.007 — roughly +28% — on a bank growing at 5%, and the premium scales 14 of the 16 leg multiples.

*U96_SI's sector growth average is fabricated.* No `_comp_basis` at all, so `_sector_g_avg` is the hardcoded 0.08 — with no provenance attached, which is precisely what `sector_g_avg_basis` was added to fix. The same-profile A/B proves 0.08 is a default and not a property of the sector: **BN4_SI has the identical profile, `Conglomerate / Industrial (SG)`, and measures 0.0643 from 22 peers.** A 28% difference in the denominator of the growth premium for two companies the taxonomy treats as the same kind of company. U96_SI escapes the consequence only by luck — its bear ROIC of 1.9% is below WACC either way, so the quality gate forces `_gp_raw` to exactly 1.0 and the fabricated average never reaches the published premium.

*C38U_SI sits exactly on `MIN_INDUSTRY_PEERS`* — five peers, and the constant is five. One fewer constituent and it drops silently to the static table: the same silent-fallback shape that let the 2026-09-13 comps outage run seventeen days undetected. The margin is now visible, so a drop becomes a named golden move instead of a quiet one.

*MELI is the only capped composite in the set*, and it is capped hard: `raw_composite` 2.174 against `cap_high` 1.85, `composite_score` 100, `was_capped` true. The decomposition behind it is two "elite" quality KPIs (take-rate expansion 150bp, Rule of 40 at 83.4) at a 0.70 quality weight, a `commodity_weight` of 0.0, and a risk sub-score of 0.92 (soft contribution margin) that cannot reach them. That is the machinery behind MELI's base IV of 5578.42 — the known live overstatement — and until now the baseline published only the resulting 1.85 with nothing to read it against.

*`final_multiplier` is the PRE-clamp value.* For the two money-center banks the bridge publishes 1.278 and 1.453 as `raw_composite` and again as `final_multiplier`, with `bank_clamp` recording the move as a string (`"1.278x → 1.100x (bank quality already priced in ROE)"`), while `composite_applied` carries the clamped 1.100 in all three scenarios and `iv_multi_post / iv_multi` equals it exactly on all 42 pairs. This is what makes Decision 1 auditable at last: the clamp's input, its output and the string that describes it are all checkable, and so is its negative case — **SCHW, profile `Brokerage`, has no `bank_clamp` leaf at all** and an applied multiplier of its own raw 1.0. Before this entry the baseline published `composite_applied` alone, where a clamp to 1.100 was indistinguishable from a profile that simply scored 1.1.

*Five fixtures run the composite on zero extracted KPIs* — AAPL, C38U_SI, FCX, SCHW and V, all with `quality_extracted == 0` and `risk_extracted == 0`. Each still emits a confident answer: `composite_score` 50, `final_multiplier` exactly 1.0, `tier_label` "in-band". Nothing in that output says "measured nothing". **Three of the five are names from the undervaluation investigation**, so their composite contributes precisely nothing to the verdict — not because they are average but because no KPI reached it. The notes name what was missing (`capex_intensity_pct not extracted`, `equity_to_assets_pct not extracted`, `rebates_and_incentives_pct_rev not extracted`, `leverage_ratio not extracted`, `net_debt_to_ebitda not extracted`).

*FCX gives 80% of its composite weight to a band it could not score.* `commodity_weight` is 0.8, the highest in the baseline, on a Mining (Major) in `_CYCLICAL_PROFILES`; the commodity sub-score is 1.0 with a note naming three absent price KPIs; `mandatory_missing` is empty; the tier still reads "in-band"; and `cap_high` is 1.7 rather than 1.85, so the cyclical ceiling is present even where the cyclical evidence is not. This is the general defect — a missing band scoring as in-band — at its highest weight.

*The z-score path fires on no fixture at all.* `quality_z`, `risk_z`, `quality_cohort`, `risk_cohort` and `risk_cap_gate_kpi` are `None` in all 14, so every sub-score in the baseline is a raw threshold band and none is cohort-normalised. That is the same absence Phase 2.1 looks for from the other direction (no `tanh`, no `0.175` in `zscore_engine.py`), now visible from the valuation side.

*`normalized_net_income` agrees with its own prose* on the four fixtures that carry the "Normalized NI: TTM … → 5y-cycle $X.XXB" flag — D05_SI S$12.75B, FCX $2.99B, MU $7.04B, U96_SI S$0.71B — and the other ten carry no such flag, so the field distinguishes "not normalized" from "normalized to this". U96_SI's `completeness_score` of 0.67 is now in the baseline too, which is where the stale-0.67 question can be settled rather than argued.

A SIBLING FIELD IS MISLABELLED, recorded here and deliberately NOT pinned. Persisting `normalized_net_income` immediately exposed that `revenue_base_usd` equals `revenue_base` bit-for-bit on **all 14 fixtures, including three whose `reported_currency` is not USD**: 02888_HK and 09988_HK (HKD) and BABA (CNY). For 09988_HK that publishes 1.195e12 as a USD figure. The two Alibaba lines show what `revenue_base` is actually in: their revenue and their net income differ by the same factor, exactly 7.8180×, and their net margins are identical to four decimals at 0.0947 — the same company at two scales, not two different measurements. 7.8180 is within 0.34% of the 7.84469 HKD/USD rate 02888_HK carries, which is the agreement to expect from two fixtures captured on different dates. So 09988_HK's `revenue_base` is HKD and BABA's is USD, and `revenue_base_usd` is an unconverted copy that happens to be right for one and 7.8× wrong for the other. The payload does not let a reader convert it either: 09988_HK's own `fx_rate` is 1.1677, which is neither HKD/USD (≈7.8) nor CNY/HKD (≈1.09). `normalized_net_income` inherits the same currency-relativity, which is why its test asserts the margin — a ratio of two same-currency figures, so currency-free — and never the level across fixtures. Not pinned, because a passing test would freeze the mislabel as intended behaviour; filed instead.

DELIBERATELY NOT DONE: item 3a. `_peer_for_gp` still passes no `market_cap`, and L10001 still resolves it as `(_market_cap or 0.0)`. Both are documented at the call site with the measured size of the divergence, and `sector_g_avg_basis.cohort` now makes the resulting cohort readable from the baseline. Aligning them is a valuation change that will re-price essentially every HK/SG fixture through `growth_premium` and all fourteen premium-scaled legs — plus, for BN4_SI, a discontinuity rather than a scaling, since `Conglomerates` sits at +2.20% on `all` against −0.56% on `large` and the guard `if _sector_g_avg > 0.005 else 1.0` flips the premium from computed to forced. Bundling that into an audit-field commit would make this entry's delta unattributable, which is the one thing the additive proof above exists to guarantee. It gets its own measured, named golden update.

VERIFICATION. **99 value tests** in `tests/test_valuation_fixes_0917f.py` (46 test functions, parametrised across the 14 fixtures) and **10 structural tests** in `tests/test_valuation_fixes_0917f_struct.py` — 109 collected in total, all passing; 17 golden tests passed in 42.44s during the regeneration. Snapshot 196,570 → 227,403 bytes. Regenerated at HEAD `18688c6`, `_meta.generated_at` 2026-09-17T12:40:34+00:00, fixtures recorded at `30b26702d3c786e1835dd8e5cc629191e3d75c95` — unchanged, since no fixture was re-captured and no recorded call was touched.


## 2026-09-17T13:42:40+00:00

- regenerated at HEAD: `9654f82`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Item 3a: align the market_cap resolution at _peer_for_gp and the 12m-target peer call with the three _compute_method_value legs, so the growth-premium benchmark comes from the same size-matched cohort as the multiples it scales. Measured across all 14 fixtures BEFORE regenerating: exactly 18 leaves move, on 2 fixtures only - 09988_HK sector_g_avg 0.0625 -> 0.1359 (industry/all n=18 -> industry/large n=9) and BN4_SI 0.0643 -> 0.0793 (sector/all n=22 -> sector/large n=11), each x3 scenarios, plus their basis cohort and peer_count. 0 added, 0 removed, base_iv identical on all 14, and not one growth_premium, leg multiple or 12m target moved: both names have forward_roic below WACC in every scenario, so the quality gate forces _gp_raw to 1.0 and the average never reaches the premium. The other four HK/SG fixtures cannot move - HKSE Banks - Diversified, SES REIT/Real Estate and SES Financial Services store no large rung at all.

THE CHANGE, which is three arguments. `_peer_for_gp` — the call that resolves the benchmark every leg multiple is scaled by — passed no `market_cap` at all, so it defaulted to `0.0`, which `get_regional_multiples` reads as falsy and which is what disables the size-matched `large` rungs. The 12m-target peer call resolved it as `(_market_cap or 0.0)` where the three `_compute_method_value` legs have always resolved it as `(_market_cap or revenue_base * 10)`. Both now pass the legs' expression, so all five sites read

    market_cap=(_market_cap or revenue_base * 10)

The fallback is copied rather than reinvented: when a quote carries no market cap the legs still resolve a size, and a different fallback here would reintroduce the divergence the change exists to close.

WHAT MOVED: eighteen leaves, two fixtures, and nothing else. Measured before the baseline was regenerated, across all fourteen fixtures, one subprocess each — first the six HK/SG names with AAPL and SCHW as controls, then the remaining six US ones. A US name can only move if the claim that `_regional_peer_multiples` returns `{}` for it is wrong, so the controls exist to falsify that claim rather than to confirm the change:

| fixture | leaf | before | after |
|---|---|---|---|
| 09988_HK | `scenarios.{bear,base,bull}.sector_g_avg` | 0.0625 | **0.1359** |
| 09988_HK | `…sector_g_avg_basis.cohort` | `all` | **`large`** |
| 09988_HK | `…sector_g_avg_basis.peer_count` | 18 | **9** |
| BN4_SI | `scenarios.{bear,base,bull}.sector_g_avg` | 0.0643 | **0.0793** |
| BN4_SI | `…sector_g_avg_basis.cohort` | `all` | **`large`** |
| BN4_SI | `…sector_g_avg_basis.peer_count` | 22 | **11** |

Three leaves × three scenarios × two fixtures = 18. **0 leaves added, 0 removed, and `base_iv` bit-identical on all fourteen.** The `basis` level is unchanged for both (`industry` for 09988_HK, `sector` for BN4_SI) — the alignment moves down a rung of the same ladder, not across to a different grouping.

WHY NOT ONE VALUATION MOVED, which is the finding and not a disappointment. `_gp_raw` is forced to exactly 1.0 whenever `forward_roic <= wacc`, and **both fixtures the alignment reached are below WACC in all three scenarios** — 09988_HK at 5.4 / 6.7 / 8.1% against a 14.4% WACC, BN4_SI at 1.8 / 2.3 / 2.8% against 10.2%. Their `sector_g_avg` is therefore an input to a term that is never used, and their `growth_premium` is 1.0 on both sides of the change. No `growth_premium`, no leg multiple, no scenario IV, no blend, no `12m_target` and no `tgr` moved anywhere in the set. The alignment is correct and, on this baseline, inert.

The corollary is why it is still worth shipping. The average DOES reach the premium for any name whose ROIC clears WACC, and **02888_HK is that name in this set** — base ROIC 10.44% against a 7.55% WACC, premium 0.891, computed rather than forced, and it scales fourteen of the sixteen leg multiples. It is also the one fixture the alignment cannot touch, for the reason below.

WHY THE OTHER FOUR HK/SG FIXTURES CANNOT MOVE. `get_regional_multiples` only accepts a `large` rung the store actually holds, with at least `MIN_INDUSTRY_PEERS` (5) or `MIN_SECTOR_PEERS` (8) constituents in it. `scratchpad/probe_3a_rungs.py` enumerated the local comps store for `field='growth_avg'` — 190 rows across 118 groupings — and read each fixture's grouping off it:

| fixture | grouping | `all` | `large` | can it size-match? |
|---|---|---|---|---|
| 02888_HK | HKSE industry Banks - Diversified | n=7 | **absent** | no |
| C38U_SI | an SES industry | n=5 | **absent** | no |
| D05_SI | SES sector Financial Services | n=9 | **absent** | no |
| U96_SI | no live rung at all → static 0.08 | — | — | not applicable |

For C38U_SI the grouping name is not recoverable — `_comp_basis` records `basis`, `cohort` and `peer_count` and not the key — but the conclusion does not depend on which one it is: **all seven SES industry groupings in the store hold no `large` row** (Hardware Equipment & Parts n=5, REIT - Diversified n=9, REIT - Industrial n=7, REIT - Retail n=5, Real Estate - Development n=9, Real Estate - Diversified n=5, Real Estate - Services n=5). No `market_cap` of any size can reach a rung that is not there.

THE RUNG LADDER PRE-EMPTS, and this is the structural finding. Rungs are built in the order industry/large, industry/all, sector/large, sector/all and resolved **per field, first-come** (`if field in resolved: continue`). So a thin WHOLE-INDUSTRY median pre-empts a size-matched SECTOR one. 02888_HK takes HKSE Banks - Diversified's 7-peer `all` average of **+45.92%** and never reaches HKSE Financial Services' **20-peer `large`** rung (`min_market_cap` 313.4B), which is stored and would size-match. That is the largest remaining exposure in the growth premium and the alignment cannot close it: passing a bigger `market_cap` changes nothing when the first rung that answers is not cohort-gated. Reordering the ladder — or requiring a cohort match before a whole-grouping fallback — is a valuation-policy decision, not a `market_cap` argument, and it belongs to the owner. Filed.

TWO PREDICTIONS IN THE PREVIOUS ENTRY WERE WRONG, and are corrected here rather than by rewriting an entry that is already pushed. That entry said the alignment "will re-price essentially every HK/SG fixture through `growth_premium` and all fourteen premium-scaled legs". It re-priced none: two fixtures changed an input, no fixture changed an output. The prediction counted fixtures that resolve a live cohort and assumed each would move, without checking either half of what actually gates the move — that a `large` rung exists for the grouping, and that the ROIC clears WACC so the premium is computed at all. Neither check was in the source; both are now, as tests.

The second was more specific and more wrong. It said BN4_SI would show "a discontinuity rather than a scaling, since `Conglomerates` sits at +2.20% on `all` against −0.56% on `large` and the guard `if _sector_g_avg > 0.005 else 1.0` flips the premium from computed to forced." BN4_SI is SES, not HKSE, and resolves SES **Industrials**: 0.0643 → 0.0793, both above the 0.005 guard, no flip, and a scaling rather than a discontinuity. The error was conflating a PROFILE name — BN4_SI's profile is `Conglomerate / Industrial (SG)` — with an FMP INDUSTRY grouping called `Conglomerates`, which is an HKSE row belonging to a different exchange and a different fixture set. The two words are not interchangeable and the previous entry treated them as if they were.

THE U96/BN4 GAP COLLAPSED, AND IT IS A COINCIDENCE. The previous entry recorded that U96_SI fabricates its `sector_g_avg` (hardcoded 0.08, no `_comp_basis`) while BN4_SI, same profile, measured 0.0643 from 22 peers — a 28% difference in the denominator of the growth premium for two companies the taxonomy treats as the same kind of company. BN4_SI now measures **0.0793** from 11 size-matched peers, which is within 1% of U96_SI's constant. That agreement must not be read as the constant being vindicated: one number is measured from a peer set that a comps refresh will move and the other is a literal that no input can change, and they arrived at the same place from unrelated directions. `test_u96_and_bn4_share_a_profile_but_not_a_sector_growth_average` is written to assert that they still DIFFER, so a future refresh that separates them again is not a failure and a future one that makes them equal is.

THE GOLDEN DELTA UNDERSTATES THE PRODUCTION EFFECT, and cannot do otherwise. The replay resolves comps from the LOCAL store — `load_comps` reads `_db.query`, not `_fmp_get`, so the golden Replayer does not intercept it and the fixtures encode whatever the local store held at capture time. That store has 190 `growth_avg` rows against production's **1057**, with 436 groupings carrying both cohorts and 401 of the 436 disagreeing. Production will therefore resolve `large` for groupings this baseline cannot see, including possibly the four above once the weekly refresh writes the missing rungs. The values pinned here — 0.1359 and 0.0793 — are pinned against the smaller store, and a local comps refresh will move them, which is a named golden move rather than a regression. Nothing in the fixture set can measure the production size of this change; saying so is the honest alternative to quoting the 401-of-436 census as though it were the result.

VERIFIED BY MUTATION. `scratchpad/mutation_check_0917f.py` now carries twenty-six mutations — the twenty from the previous entry with the two cohort ones renamed to the tests that replaced them, plus six for this one. All twenty-six are caught by at least one test, with a control pass requiring all 48 test functions to pass against the unmutated snapshot first, and **0 missed, 0 unresolvable names, 0 wrong predictions**. The six new ones are paired deliberately, because the two leaves this entry changed can come apart in either direction and an inconsistent pair is invisible in every IV in the baseline: reverting 09988_HK's cohort to `all` while leaving the average at its `large` value is caught by three tests, and reverting the average to 0.0625 while leaving the cohort at `large` is caught by exactly one — the test that pins the number the cohort resolved to. Two more attack the inertness itself: setting a mover's `growth_premium` to 1.05 trips only `test_the_alignment_changed_no_valuation_because_both_movers_are_gated`, and raising 09988_HK's bear ROIC above its WACC trips eight, which is the coupling that would make this entry's "no valuation moved" claim void.

VERIFICATION. **102 value tests** in `tests/test_valuation_fixes_0917f.py` (48 test functions, parametrised across the 14 fixtures) and **13 structural tests** in `tests/test_valuation_fixes_0917f_struct.py` — 115 collected in total, all passing; 32 golden tests passed in 42.58s during the regeneration. Snapshot 227,403 → 227,943 bytes. Regenerated at HEAD `9654f82`, `_meta.generated_at` 2026-09-17T13:42:40+00:00, fixtures recorded at `30b26702d3c786e1835dd8e5cc629191e3d75c95` — unchanged, since no fixture was re-captured and no recorded call was touched. The four structural tests added here — net three, since `test_the_divergence_is_documented_at_the_call_site` was replaced by the two that pin the aligned state — are source-shape guards rather than value guards, and that is deliberate: a divergence reintroduced at one of the five call sites would move production and leave this baseline almost entirely unchanged, so counting the identical expressions is the only check that survives the store-size gap above.

## 2026-09-17T15:00:48+00:00

- regenerated at HEAD: `c690850`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: Items 2+3 of the owner's queued mechanical fixes. Item 2 binds the peer market_cap ONCE as resolved_mcap and passes it to all five call sites, with a falsy-revenue_base guard; expected to move nothing. Item 3 puts _y10_fcf_margin through the same clamp _project_dcf applies to every projected year. Named golden delta, measured before regenerating: exactly 3 leaves, all forward_roic, no base_iv move on any of the 14 -- COST bear 0.1603->0.1724 (Consumer floor +0.02 binds, raw bear margin 0.0186), V bull 0.5007->0.4417 and C38U_SI bull 0.0331->0.0296 (0.60 cap, raw 0.680 and 0.670).

### Item 2 — one market cap, five call sites

`c690850` aligned the five peer call sites by writing the legs' expression,
`(_market_cap or revenue_base * 10)`, at each of them. Five identical expressions
is still five chances to edit one, and nothing but a source-shape test would
notice. This binds it once as `resolved_mcap` and passes it by name, so the
divergence is structurally impossible rather than merely asserted-against.

Two things changed beyond the hoist, both deliberate:

- **The standardised fallback is the owner's, not the legs'.** It reads
  `_market_cap or (revenue_base * 10.0 if revenue_base else None)`. The guard is
  the part that was missing at all five sites: `revenue_base` is
  `most_recent["revenue"]` and can be `None`, so the bare expression raised
  `TypeError: unsupported operand type(s) for *: 'NoneType' and 'int'` on any
  name with no revenue line and no quote market cap. `None` is the honest answer
  there — with no size information the caller gets whole-grouping medians and
  `_comp_basis` says so, instead of a fabricated cap inventing a `large` rung
  that was never earned.
- **Placement is asserted, not assumed.** `revenue_base` is assigned TWICE inside
  `run_dcf_agent` — the anchor, and again inside the FX block after the whole
  series is multiplied by the rate. A binding hoisted above either would capture
  a stale value and produce a size in the wrong currency, silently, because the
  result is still a float. `test_resolved_mcap_is_bound_after_both_of_its_inputs_are_final`
  pins the line-index ordering: two `revenue_base` assignments, one `_market_cap`,
  one binding, below all three, above all five uses.

**Measured move: zero.** All 14 fixtures, all leaves, bit-identical. That is the
expected result and not a vacuous one — the fallback only fires when the quote
carries no market cap, which no fixture does, so this commit is bought entirely
by the structural guarantee and the `None`-revenue crash it closes.

**The blind spot this exposes, measured in production.** The golden replay
resolves comps from the LOCAL store (`load_comps` reads `_db.query`, not
`_fmp_get`, so the Replayer never intercepts it), and that store has **no US rows
at all**. Live SCHW resolves `industry/large n=10` at +28.22% with a growth
premium of 0.962/0.933, while its fixture records `static/US` at 0.05 with a
premium of 1.104. So `c690850` re-priced a US name in production and this
baseline reported "no valuation moved" — a true statement about the baseline and
a misleading one about the change. The comment at `_peer_for_gp` claimed "US names
are unaffected"; that claim is now corrected in the source, and
`test_the_alignment_comment_still_carries_the_measured_size` asserts the
correction is present, because a comment carrying a checkable number beside an
unchecked false scope reads as though both were checked. **No US fixture can ever
exercise a live cohort here.** Filed as a chip.

### Item 3 — the Y10 estimate clamps like the cash-flow engine

`_project_dcf` has always run `min(max(fcf_margin_base + margin_delta_absolute,
fcf_floor), 0.60)` on every projected year. `_y10_fcf_margin` — the terminal
state Gate B judges, and the input to the persisted `forward_roic` — ran the bare
sum. Gate B therefore decided whether terminal growth survives by comparing a
ROIC computed off a margin the valuation never uses against the WACC.

MSTR is the case that surfaced it and is **not in the fixture set**:
`fcf_margin_base = -21.0605`, so `md_abs` is POSITIVE at +4.2121 and the unfloored
Y10 margin is -16.85% against the -5.0% Tech floor the engine actually runs at — a
projected ROIC of -11.1% where the DCF is sitting on its floor. The guard for the
eleven fixtures where no clamp binds is therefore source-shape, not value-shape:
`test_the_y10_estimate_and_the_engine_read_the_same_clamp` reads `_project_dcf`,
`run_dcf_agent` and `pdf_report.py` and requires all three to carry the same
expression. `fcf_floor` is reused from its single binding in `run_dcf_agent`
rather than re-derived, and `0.60` is now `_FCF_MARGIN_CAP`, so the two cannot
drift.

**There is a THIRD copy of the clamp.** `src/utils/pdf_report.py:1394` already had
the right shape — written by `7ba9aa8` — and reads `fcf_floor` off the payload,
but hardcodes the 0.60 cap it cannot read. Before this commit the published
sensitivity grid clamped and the Gate B estimate beside it did not: the table and
the prose described different companies. The cap being a literal in one place and
a constant in another is filed as a chip.

**The floor is not uniformly negative, and my reasoning that it was inert was
wrong.** I had carried "all fixture `fcf_margin_base` are positive, so a bear
margin at 0.80 × fmb stays above a floor of −0.05." That is true of the DEFAULT
and false of the TABLE at `src/data/sector_profiles.py:656`: Consumer +0.02,
Industrials +0.02, Materials +0.01, Telco +0.05, REIT +0.05,
ProfessionalServices +0.05. COST is Consumer, its bear raw margin is
0.0232 − 0.0046 = 0.0186, and +0.02 binds. The cap half was predicted correctly;
the floor half was found by measuring before regenerating, which is what the
verification contract exists to force.

### The three leaves, and why no valuation moved

| fixture | leaf | before → after | half | arithmetic |
|---|---|---|---|---|
| V | `scenarios.bull.forward_roic` | 0.5007 → 0.4417 | cap | fmb 0.5667 × 1.20 = 0.680; 0.4417/0.5007 = 0.8822 ≈ 0.60/0.680 |
| C38U_SI | `scenarios.bull.forward_roic` | 0.0331 → 0.0296 | cap | fmb 0.5583 × 1.20 = 0.670; 0.0296/0.0331 = 0.894 ≈ 0.60/0.670 |
| COST | `scenarios.bear.forward_roic` | 0.1603 → 0.1724 | floor | Consumer +0.02 against a raw bear margin of 0.0186; 0.1603 × (0.02/0.0186) = 0.17237 |

`base_iv` is bit-identical on all 14 and no Gate B firing changed, for two
separate reasons:

- **The quality gate is saturated on both cap cases.** COST bear 0.1724 against
  WACC 0.0725 is 2.38×, V bull 0.4417 against 0.0725 is 6.09×, and
  `_quality = min(1.0, (forward_roic − wacc)/wacc)` is already at 1.0 either side
  of the move. C38U_SI bull 0.0296 against WACC 0.0573 is still below, so `_gp_raw`
  is forced to 1.0 as before.
- **Bull's Gate B threshold is `float("-inf")`**, so no bull ROIC can trip it, and
  COST's bear ROIC clears WACC on both sides of the floor.

### The invariant this bought, and the hole it opened

`test_the_three_scenario_roics_are_the_base_one_scaled_by_the_margin_delta` is now
stated against the CLAMPED margin on both sides:

    roic[s] / roic[base] == clamp(fmb + md[s]) / clamp(fmb + md[base])

Before this commit the identity divided bare sums and still passed on a payload
where the gate and the engine disagreed — it was comparing the estimate against
itself, not against the cash flows the valuation is built from. Stating it
against the clamp makes it a test of the parity.

**Clamping makes that map non-injective, and the first 32-mutation sweep found
the consequence.** Reverting COST's bear `margin_delta_absolute` to the
pre-`7ba9aa8` per-year form (−0.0046 → −0.046) SURVIVED all 51 tests: −0.0228 and
+0.0186 both clamp to the Consumer floor of +0.02, so `forward_roic` is
bit-identical under the mutation. It is not an equivalent mutation —
`margin_delta_absolute` drives all ten projected years inside `_project_dcf`, so
the IV moves — but no ROIC can see it. Closed by asserting the identity on the
INPUT for exactly the three pairs where a clamp binds
(`md = fmb × (m − 1)`, which the clamp cannot flatten), with
`assert clamped == 3` so a fourth binding forces the coverage question rather
than silently inheriting a skipped branch. Second sweep: **32 of 32 caught, 0
missed, 0 unresolvable names, 0 wrong predictions.**

The clamped set is DERIVED, not tabulated: `test_the_clamp_binds_on_exactly_three_scenario_margins`
recomputes `_binds(name, s)` across all 14 fixtures and compares to the named
three, so a future comps or profile change that pushes another fixture into the
clamp fails with a name on it.

### One pre-existing guard collided, and which side moved

The full suite came back **1 failed, 4927 passed**:
`tests/test_valuation_fixes_0917e.py::test_the_multiplier_is_gone_from_the_assignment`,
which asserted the literal string `_y10_fcf_margin = fcf_margin_base + md_abs` —
the exact post-`7ba9aa8` line that item 3 wrapped in the clamp. Two source-shape
guards, one line, and they contradicted each other directly: item 3's
`test_the_y10_estimate_and_the_engine_read_the_same_clamp` asserts that string is
ABSENT.

0917e is the stale side and it moved. Its purpose is that the `× 10` multiplier is
gone, and the bare-sum form was incidental to that purpose; the fossil sweep over
all six spacings is unchanged and still runs against the whole engine source. The
shape assertion is now the clamped pair, `_y10_fcf_margin = min(` plus
`max(fcf_margin_base + md_abs, fcf_floor), _FCF_MARGIN_CAP)`, and is deliberately
NOT loosened to a substring that both forms satisfy — such a substring would also
satisfy `md_abs * 10` reappearing inside the clamp.

This is recorded here rather than only in the commit message because it is the
shape of failure the golden suite exists to make attributable: a guard written for
one fix silently became a guard against the next one, and only running the whole
suite — not the four modules the change touched — surfaced it.

Re-run after the 0917e fix: **4933 passed, 0 failed, 1 deselected**. The three
leaves above are the whole of the golden delta and all three are in the named
set, so the snapshot was regenerated without investigating anything.



## 2026-09-17T17:54:37+00:00

- regenerated at HEAD: `6a76492`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: _normalized_earnings outlier floor made relative: max(iqr*2, abs(med)*0.30) replaces max(iqr*2, 0.05). Expected moves, all investigated: 09988_HK+BABA normalized_net_income -9.47% (FY2025 excluded; iqr*2 now binds at 0.0348 because the 0.05 floor is gone, relative term loses) and a new Normalized NI audit flag crossing its 15% threshold in all three scenarios, base IV unchanged because methods_used is plain P/E; FCX normalized_ebitda +4.66% / normalized_ebit +6.02% (FY2021 re-admitted, relative term 0.1135 beats iqr*2 0.0528) moving EV/EBITDA (norm) and base IV 25.58->26.69.

### What changed, and why two mechanisms run in opposite directions

`_normalized_earnings` drops a window year when `|margin − median|` exceeds a
threshold. That threshold was `max(iqr * 2, 0.05)` — an absolute five-point
floor — and is now `max(iqr * 2, abs(med) * 0.30)`, with the 0.30 named
`_NORMALIZED_OUTLIER_REL`. The defect being removed: a fixed 5pp is a fixed
fraction of nothing, so its strictness depends entirely on how thin the business
runs. At EL's 14.1% median a 5pp allowance is a 35% relative move; at a 5%
median it is a 100% relative move, so a 90% collapse is averaged straight in.
The rule was loosest exactly where consumer margins are thinnest, which is what a
trough looks like — precisely when normalization is load-bearing.

Removing the floor has **two** effects, in opposite directions, and only the
first was intended. Both are exercised by the moved fixtures:

- **(a) Tightening.** Wherever `iqr * 2 < 0.05`, the floor was propping the
  threshold up and its removal lets the dispersion bind — regardless of where the
  relative term lands. This is what moved 09988_HK and BABA.
- **(b) Loosening.** Wherever `abs(med) * 0.30 > iqr * 2`, the relative term
  overrides the series' own dispersion. On a rich-margin name 30% of the median
  is a large absolute allowance: at FCX's 37.8% median it is 11.3pp against a
  dispersion of 5.3pp. This is what moved FCX.

An earlier draft of the source comment claimed the change only ever loosens the
filter for thin-margin names. The diff below falsified that and the comment was
corrected before commit.

### The four moved fixtures, exactly

**09988_HK and BABA — `normalized_net_income` −9.47% on both, base IV unmoved.**
They share the identical net-income margin series (same company; the ratio is
currency-invariant, so the FX step cannot be what moved it):
`[0.07297, 0.08379, 0.08501, 0.13059, 0.10120]`, median 0.08501. `iqr * 2` =
0.03482, relative term 0.02550 — **the relative term loses**, and the threshold
falls 0.0500 → 0.03482 on the floor's removal alone. FY2025, at +53.6% above
the median, is newly excluded and the average drops from 0.094710 to 0.085741.

Base IV is bit-identical (160.84 and 193.13) because **nothing consumes the
number**: `methods_used` is `['DCF', 'EV/EBITDA', 'P/E']` — plain trailing P/E,
no normalized leg. `normalized_ebitda` is untouched in both (09988_HK's
dispersion is 0.05288, above the old floor, so `iqr * 2` bound before and after).
09988_HK's `normalized_ebit` also moved −7.11% and BABA's did not, because their
FY2025 EBITDA margins differ (0.20827 vs 0.18334) across fixture vintages.

**FCX — `normalized_ebitda` +4.66%, `normalized_ebit` +6.02%, base IV
25.58 → 26.69 (+4.34%), bear IV 16.06 → 16.90 (+5.23%).** EBITDA margins
`[0.45887, 0.39830, 0.37825, 0.37191, 0.34020]`, median 0.37825. `iqr * 2` =
0.05278, relative term 0.11348 — **the relative term wins**, FY2021 is read back
in, and the average rises from 0.372166 to 0.389507. FCX's anchor IS
`EV/EBITDA (norm)`, so unlike the two names above this reaches the valuation:
the leg moves +6.79% base / +8.03% bear / +6.23% bull. `normalized_net_income`
is unchanged (dispersion 0.13441 dominates both rules), and FCX's
`Normalized NI` flag was already firing before at +36% — its gate set is
unchanged and only the 12m-PT-band prose moved with the numbers.

The bear leg at **+5.23% is outside the harness's stated ±5% tolerance on
numeric leaves**, and is accepted deliberately; the reasoning is below.

**C38U_SI — `normalized_net_income` −3.06% (934589280.552267 →
906032487.846545), and nothing else.** Net-income margins
`[0.82992, 0.50173, 0.55295, 0.58858, 0.57324]`, median 0.57324. `iqr * 2` =
0.07126, relative term 0.17197 — mechanism **(b)** again, on a very
high-margin S-REIT where 30% of the median is 17.2pp. FY2022 is re-admitted and
the average falls 0.571592 → 0.554127.

The old rule excluded FY2022 by **0.00025**: its deviation is 0.07151 against a
threshold of 0.07126. That is a near-tie decided by the fourth decimal place, so
the pre-change behaviour was an artefact rather than a judgement. The old rule
also trimmed BOTH tails — 2021 at 0.82992 (a property-revaluation peak) and 2022
at 0.50173 (the trough) — leaving three middle years, which for a REIT whose net
margin swings on fair-value gains is exactly the wrong sample: a mid-cycle
average should span the swing, not delete both ends of it. The new rule excludes
the revaluation peak and keeps the trough, which is the symmetric answer.

Base IV does not move and no flag changes state, because — as with 09988_HK and
BABA — nothing in this profile's method set consumes `normalized_net_income`.

### Why this move was invisible until the snapshot was regenerated

The focused golden run before regeneration reported **3 failed**, and the
per-fixture diff built from those failures named three fixtures. C38U_SI moved
−3.06%, which is **inside the ±5% tolerance**, so its test passed and it never
appeared. The move was only found by comparing the regenerated
`snapshots.json` against `HEAD` field by field across all 14 fixtures — which
then showed exactly four changed and ten byte-identical.

That is a general trap, not a one-off: **a tolerance is a reporting threshold,
not a change detector.** Counting failures under-reports the blast radius of any
change whose effects are small but real, and a CHANGELOG written from the failure
list would have under-named this baseline's own moves. The snapshot-to-snapshot
comparison is the only complete method, and it is what the named set below was
built from.


### The FCX judgement call, stated rather than left implicit

FCX's five years are a **monotone decline**, not a spike around a central value.
There is no outlier here in any statistical sense, only the first point of a
trend. The function's own docstring prescribes Damodaran — "the mean of
(field / revenue) over the window" — with exclusion reserved for one-offs:
goodwill write-downs, COVID, a special dividend. A commodity peak is not a
one-off; it is the cycle the average exists to span.

The old rule also trimmed **asymmetrically**: it dropped 2021 at deviation
+0.0806 while keeping 2025 at deviation −0.0381, which biases a "mid-cycle"
margin low on any trending series. A genuine peak-strip would have to drop 2022
and 2023 as well; dropping only the single highest point was an artefact of
where a fixed threshold happened to fall. Under the new rule FCX's normalized
EBITDA margin equals its plain five-year mean, which is the documented method.

The available alternative — capping the relative term at the 0.05 it replaces,
`max(iqr * 2, min(abs(med) * REL, 0.05))` — is provably monotone-tightening
against the old rule, so it would fix (a) and leave FCX untouched entirely. **It
was not taken because it is a clamp, and clamps on estimates are the owner's
choice, not the engine's.** Recorded here so it can be chosen later with the
numbers in front of it.

### Two predictions that were falsified, and the lessons

**The substring pre-count.** Counting `'P/E (norm)'`, `'EV/EBITDA (norm)'` and
`'Normalized P/E'` occurrences per fixture blob predicted **7** moved fixtures
(02888_HK, D05_SI, FCX, MU, SCHW, U96_SI, V) and reported 09988_HK and BABA as
having no normalized leg at all. The actual delta is **4** fixtures
(09988_HK, BABA, C38U_SI, FCX): the count got FCX right, wrongly named six, and
missed three. It missed both mechanisms because it looked for the presence of a
leg rather than for where the threshold changed — and the leg's presence is not
what determines the move. 09988_HK and BABA moved a field no leg reads at all,
while MU, SCHW, V, D05_SI and U96_SI have legs whose dispersion already
dominated the threshold under both rules. **A substring count over a JSON blob is
not a predictor of golden blast radius.**

**The failure-count diff.** The focused run reported `3 failed`, and the
per-fixture diff built from those three named three fixtures. C38U_SI's −3.06% is
inside the ±5% tolerance so its test passed and it never appeared; only the
field-by-field snapshot-to-snapshot comparison across all 14 found it. **A
tolerance is a reporting threshold, not a change detector**, and a named set
written from a failure list under-names the baseline it is meant to describe.

The set below is built from the snapshot comparison, which is complete: 4
fixtures changed, 10 byte-identical.

### The named move set

| Fixture | Fields | Cause | Base IV |
| --- | --- | --- | --- |
| 09988_HK | `normalized_net_income` −9.47%; `forward_flags` ×3 | (a) FY2025 excluded | 160.84 unchanged |
| BABA | `normalized_net_income` −9.47%; `forward_flags` ×3 | (a) FY2025 excluded | 193.13 unchanged |
| C38U_SI | `normalized_net_income` −3.06% | (b) FY2022 re-admitted | unchanged |
| FCX | `normalized_ebitda` +4.66%, `normalized_ebit` +6.02%; `EV/EBITDA (norm)` ×3; bear `intrinsic_value`/`_pre_composite`/`iv_multi`/`iv_multi_post`; `forward_flags` ×3 | (b) FY2021 re-admitted | 25.58 → 26.69 (+4.34%) |

The `forward_flags` changes are the new `Normalized NI` audit flag on 09988_HK
and BABA (crossing 15% for the first time) and, on FCX, prose-only movement of
the existing 12m-PT-band violation (2.659x → 2.552x, still outside [0.33, 2.50],
so no behavioural flip). **No gate changed state on any fixture**: the gate-set
comparison was run separately from the prose comparison precisely so that a
cosmetic rewording could not be mistaken for a new firing, and vice versa.


### Two findings this investigation surfaced that were not in the brief

1. **The `Normalized NI` audit flag makes a promise the engine does not keep.**
   Its prose ends "— `P/E (norm)` will use normalized figure", and it is gated
   only on the value existing and `abs(delta) > 0.15`. It never checks whether
   the profile has such a leg, and **75 of the 99 profiles do not**. This became
   observable because the −9.47% move pushed 09988_HK and BABA past 15%
   (TTM RMB120.96B → 5y-cycle RMB102.49B, −15%) and the flag fired on both for
   the first time, in all three scenarios, while `methods_used` stayed plain
   `P/E` and base IV did not move. The disclosure is wrong today and becomes
   true only once margin-deviation routing exists. Pinned by
   `test_the_normalized_ni_flag_promises_a_leg_most_profiles_do_not_have`.
2. **The `else med` fallback in `_normalized_earnings` is unreachable.** `q1` is
   taken at index `n // 4` and `q3` at `3 * n // 4`, so for n = 5 the points at
   s[1] and s[3] each sit within one IQR of the median s[2] by construction, and
   `threshold ≥ 2 · iqr ≥ iqr`. At least three survivors are guaranteed for every
   input that reaches the branch, so `len(filtered) >= 2` can never be false.
   This matters because the relative-floor change was partly justified by that
   fallback handling degenerate near-breakeven series; it does not, and cannot.
   Pinned by `test_the_short_paths_are_unchanged_and_the_median_fallback_is_unreachable`.

Also counted while writing those tests: normalized leg names are **not
case-consistent** — `EV/EBITDA (norm)` on three profiles and `EV/EBITDA (Norm)`
on one. Any exact-string dispatch silently misses one. Not fixed (a rename with
its own blast radius), pinned by `test_the_normalized_leg_names_are_not_case_consistent`.

### Still open, deliberately

`_mean_fcf_margin` carries the **identical** `max(iqr * 2, 0.05)` line and
produces `fcf_margin_base` — the most load-bearing number in the DCF, feeding the
margin schedule, the ROIC projection and every scenario multiplier. The same
thin-margin blindness therefore applies with far greater consequence than it did
here. It was not changed: this edit was authorised for `_normalized_earnings`
only. The asymmetry is pinned by
`test_the_sibling_fcf_normalizer_still_carries_the_absolute_floor`, which strips
comments before matching — a naive substring search matches the historical note
inside `_normalized_earnings` that quotes the old expression verbatim, and
reported the defect as still present on its first run.

### Also in this commit: the four consumer archetype pins

`NKE`, `ONON`, `EL` and `02020.HK` are pinned in `TICKER_SECTOR_LOOKUP`. **None
of the four is a golden fixture**, so the pins have zero blast radius on this
baseline — asserted by
`test_the_pins_override_a_classification_that_would_otherwise_move`. Three of the
four are genuine policy overrides of what the ladder returns (ONON → Consumer
Growth, EL → Luxury Goods, 02020.HK → Apparel / Athletic Wear); NKE's pin agrees
with the classifier and is documentation. The brief's suggested
`Prestige Beauty & Personal Care` **does not exist in any sector**, and because
the D3 override guard falls back silently to the classified profile, pinning it
would have defeated itself with no error.

### Also in this commit: the prior re-baseline's guards had to be re-pinned

This is the same shape of failure the entry above documents for the `× 10` fix —
a guard written for one fix silently became a guard against the next one — and it
recurred within two re-baselines, which is worth recording as a rate rather than
as an accident. Five tests failed against the regenerated snapshot and every one
of them was in `0917e`/`0917f`, the modules whose declared purpose is to pin the
named move set. Each failure was legitimate, and each new value had been derived
independently during the investigation before the snapshot was regenerated:

| Test | Old | New |
| --- | --- | --- |
| `test_base_iv_is_unchanged_in_all_fourteen` | FCX 25.58 | **26.69** |
| `test_the_sign_flips_changed_only_their_flag_text` | FCX bear 16.06 | **16.90** |
| `test_fcx_bull_premium_came_off_its_clamp_ceiling` | 45.52 | **46.92** |
| `test_fcx_12m_band_fires_for_the_first_time` | (17.89, 23.85, 29.81) | **(18.66, 24.89, 31.11)** |
| `…alibaba_lines_agree_on_the_currency_invariant_margin` | 0.0947 | **0.0857** |

They were re-pinned rather than re-valued, because four of the five had a name or
a docstring that stopped being true and a constant swap would have hidden that:

- The first is now `test_base_iv_is_unchanged_in_thirteen_and_fcx_is_the_named_exception`.
  Its old name asserted a fact about all fourteen that is no longer a fact, and a
  test whose name overclaims is worse than one that fails, because it keeps being
  read as evidence for the stronger claim.
- `_BEAR_IV_UNMOVED["FCX"]` was **left at 16.06**. That table records the state
  the `× 10` fix left behind, and that fix genuinely did not move FCX's bear leg;
  overwriting the entry would have made the table claim otherwise. The test now
  asserts both halves — the historical 16.06 and the live 16.90 — so neither can
  be silently replaced by the other. `_FLOOR_MOVED` carries this re-baseline's
  values separately.
- The bull-premium test keeps `45.52` in its prose as the value the `× 10` fix
  produced, and adds the cumulative 35.4% cut from the pre-fix 72.62 as an
  explicit assertion so the two figures cannot drift apart unnoticed.

Two of the re-pins turned up defects in the guards themselves, independent of
this change:

- `_NORM_NI_RE` was `5y-cycle [S$]*\$?([\d.]+)B` — it handled `S$` and `$` and
  nothing else, so it could not match the RMB prefix on 09988_HK and BABA. When
  the −9.47% move made both fixtures fire the flag for the first time,
  `test_the_persisted_net_income_is_the_one_the_flag_announced` **kept passing on
  a list of four while six fired**. A guard that is green and wrong is worse than
  no guard, and this is the exact trap `0917f` warns about one section earlier
  ("String-matching a log is not counting a payload") — the file contained the
  warning and the instance of the thing it warned about. The regex is now
  currency-agnostic and the expected list is the thing that catches a re-narrowing.
- `gate_metrics` is a **list of gate ids with no values attached**. The ratio the
  PT band computed exists only in the flag prose, so there is no payload to assert
  on. The new assertion reads it out of the text and says so, then cross-checks
  the prose against the two numbers it was computed from (the PT and the bear IV,
  both pinned independently) so the string cannot drift from the payload without
  the ratio going with it.


## 2026-09-17T20:36:40+00:00

- regenerated at HEAD: `d0c2c84`
- fixtures recorded at: `30b26702d3c786e1835dd8e5cc629191e3d75c95`
- tickers: 14
- tolerance: ±5% on numeric leaves
- reason: GATE_GROWTH_REINVESTMENT added OBSERVATION-ONLY (applied: False on every run; no call site passes sales_to_capital, so _project_dcf is byte-identical to the previous baseline). Expected moves are exactly two field kinds and NO numeric leaf: (1) gate_metrics gains "reinvestment_margin_deduction" on all 14; (2) scenarios.{base,bear,bull}.forward_flags gains the disclosure line on 13 of 14 — D05_SI is excluded because its invested capital is unmeasurable, so S/C is None and the flag is correctly absent rather than silent. Every intrinsic_value, iv_dcf, forward_roic, tgr, method_iv_table, weight and target is unchanged; verified by grepping the update run for out_of_tolerance and finding zero. The mechanism was built, wired live and measured first, and the measurement is recorded at the gate's site: base IV moved on 9 of 14 over -9.45% to +17.40% with the sign INVERTED on 09988_HK (+17.40%) and BABA (+15.60%), because the deduction exceeded the base margin, the floor absorbed it, iv_dcf went to None, and the blend renormalised that leg's weight onto higher multiples (weight_dcf 0.2778 -> 0.0, weight_multi -> 1.0, methods_used loses DCF). Those live numbers are NOT in this baseline; they are recorded so the charge is not re-wired on the strength of its algebra alone.


### What moved, and how it was checked

Two field kinds and nothing else, confirmed by a snapshot-to-snapshot diff of
every leaf rather than by counting test failures — a ±5% tolerance is a
reporting threshold, and a move small enough to hide inside it is still a move
nobody named.

- `projection.gate_metrics` on **14 of 14**: gains
  `reinvestment_margin_deduction`.
- `projection.scenarios.{base,bear,bull}.forward_flags` on **13 of 14**: gains
  the disclosure line. D05_SI is absent because its invested capital is
  unmeasurable, so `_sales_to_capital` returns None and there is no ratio to
  disclose. That asymmetry is expected and is the one place where "no flag"
  means "no data" rather than "no charge" — the gate record still fires on
  D05_SI, with its `basis` naming the absence.
- **Numeric leaf moves: 0.** Verified twice, by the diff above and by grepping
  the pre-regeneration run for `out_of_tolerance` and finding none.

### Why this is observation-only, and what the live run measured

The charge was built, wired live at all four `_project_dcf` call sites plus the
`_y10_fcf_margin` parity site, and measured before anything was regenerated. The
measurement is the reason it ships observed. Per fixture, each replayed in its
own subprocess: base IV moved on **9 of 14** over a range of **−9.45% to
+17.40%**, and the sign **inverted** on the two hyper-growth names the charge
exists for.

| fixture | base IV | the mechanism, from the payload |
|---|---|---|
| 09988_HK | 160.84 → 188.82 **+17.40%** | `iv_dcf` 88.09 → None, `weight_dcf` 0.2778 → 0.0, `weight_multi` → 1.0, `methods_count` 5 → 4, `methods_used` loses `DCF`, `tv_pct` 0.3714 → 0.0 |
| BABA | 193.13 → 223.25 **+15.60%** | same, and `12m_targets` base 151.25 → 166.31 (+9.96%), bear +10.37%, bull +8.50%; `tgr` 0.03 → 0.0 |
| SCHW | 75.41 → 68.28 −9.45% | `forward_roic` 0.0734 → 0.0, `tgr` 0.02 → 0.0, bull −21.09% |
| MELI | 5578.42 → 5098.45 −8.60% | `iv_dcf` −22.23%; the leg survives, so the charge does what it says |
| V | 428.47 → 399.20 −6.83% | `iv_dcf` −24.41% |
| AAPL | 219.80 → 208.15 −5.30% | `iv_dcf` −18.40% |
| MU | 293.14 → 287.44 −1.94% | `iv_dcf` **−65.43%** (34.81 → 12.03) and base IV barely moves — that leg carries almost no weight |
| COST | 1324.57 → 1302.82 −1.64% | `iv_dcf` −13.30% |
| 02888_HK | 284.38 → 285.68 +0.46% | `forward_roic` −16.38%; `bear.tgr` 0.01 → 0.0 |
| BN4_SI, C38U_SI, FCX, U96_SI, D05_SI | unchanged | `iv_dcf` was **already** None on four of these, so there was no leg to break; D05_SI was never charged |

Three findings, none of them about the formula.

**A dropped leg renormalises onto the survivors.** Where the DCF is the LOW leg
— which is exactly where it is doing its job — removing it RAISES the blended
IV. So "a more conservative input" produced "a less conservative output" on two
names, and the price target followed on one. This is a property of the blend,
not of this charge: anything that can drive a DCF leg non-positive has it.

**`revenue / invested_capital` is not sales-to-capital for every profile.** The
measured ratio spans 0.063 (C38U_SI, an S-REIT whose invested capital is ~16×
its revenue) to 10.912 (COST), a factor of 173. On the REIT it levies +98.04pp
against a 55.83pp margin. On the four fixtures where the ratio is least
meaningful the IV did not move at all, because their DCF leg was already gone —
"the ratio is meaningless" and "the ratio is harmless" are different claims and
only a clean measurement separates them.

**The deduction exceeded the base margin on seven names.** 09988_HK 9.51% −
10.72pp, BABA 9.51% − 10.96pp, SCHW 11.53% − 16.19pp, FCX 5.39% − 12.94pp,
C38U_SI 55.83% − 98.04pp, BN4_SI 9.79% − 16.00pp, U96_SI 7.16% − 11.61pp.
Nothing in the engine decides what should happen then; the sector floor absorbs
it, and the floor is a solvency guard, not a capital-rationing rule.

The post-mortem's own worked example is right for its own population: ONON at
g = 28% and S/C = 1.85 deducts 11.8pp, and an apparel grower's
revenue/invested-capital ratio IS capital turnover. What is missing is a scope —
which profiles may be charged at all — and a rule for a deduction larger than
the margin it comes out of. Neither is a calibration constant, so neither is
chosen here, and no bound is added: a bound would be a clamp nobody asked for,
and no single bound is simultaneously tight on a retailer and loose on a REIT.

### A measurement error, found and corrected

The first blast-radius table reported 09988_HK at **+44.49%** and BN4_SI at
**+26.54%**, and base IV moving on 14 of 14. Both were wrong, and the numbers
were quoted into source comments and an xfail reason before the error was
caught. The cause: that probe replayed all 14 fixtures in **one process**.
`tests/test_golden_valuations.py::test_golden_replay_is_deterministic` exists
precisely because the valuation path has ~ten process-lifetime caches and a leak
between tickers once moved BN4.SI's baseline 15% depending on test order. Here
it moved BN4_SI 26% **with the change under test switched off**, which is the
only reason the artifact was visible at all.

Every figure above was re-measured one subprocess per fixture and then
re-confirmed against the golden harness, which isolates the same way. The
contaminated probe was rewritten to shell out rather than deleted, so the trap
is harder to fall into a second time.

### What is kept and what is switched off

Kept, because deleting a built and tested mechanism is how the next attempt
reimplements it slightly differently:

- `_sales_to_capital(row)` and `_reinvestment_margin_deduction(g, s_to_c)`, the
  latter split out so the loop, `_y10_fcf_margin` and the `pdf_report.py`
  sensitivity grid cannot each grow their own copy of the identity;
- `_project_dcf`'s 15th parameter, defaulting to None, which reproduces the
  legacy flat-margin projection byte-for-byte (pinned at 1e-12 against the
  algebraic form computed inline in the test, not against the engine agreeing
  with itself);
- `reinvest_margin_deduction` on each annual row, PRE-floor — the live run
  floored five fixtures and the pre-floor figure is the only thing that shows it;
- `GATE_GROWTH_REINVESTMENT`, `applied: False`, with `basis` distinguishing
  "unmeasurable" from "not acted on".

Switched off: all four call sites pass no ratio; `_y10_fcf_margin` does not
deduct; the `_DEPLETING_DCF` path is out of scope permanently, on two grounds
recorded at the call site.

The gate count in `tests/test_consumer_discretionary_gates.py` moves seven →
eight, and its `not any("INVENT" in g ...)` assert still guards what it says it
guards — `"REINVESTMENT"` does not contain `"INVENT"` — which is now pinned as a
fact rather than left for the reader to check.
