# Cross-sector audit against the biopharma lessons (2026-10-08 → 10-10)

This audit checks every sector profile outside biopharma against the lessons in `VALUATION_REVIEW_LESSONS.md`. That covers 138 profiles: Tech, Semiconductors, Aerospace & Defence, Consumer and every other sector, including Financials.

The audit ran the **current engine locally**. No production runs were used, archived or otherwise.

**Status:** classification is done. Nothing has been fixed. Every fix below needs the owner's confirmation before it is built.

## 1. Method

| Source | What it covers |
|---|---|
| **Code trace** (3 parallel traces with file:line evidence) | Every multiple leg's basis; time-limited-revenue overlays; whole-company legs; flags; double-count guards; net debt; named peers; capex vs D&A; share count |
| **Plain local runs** — 240 tickers, at least 2 pinned names per profile, plus label-routed names for unpinned profiles, on live FMP data | Routing, legs, peer provenance, net-debt basis, stand-ins, anchor failures, leg outliers |
| **Probe runs** — the same names re-run with a synthetic HIGH-confidence guidance block taken from today's street consensus (revenue on consensus; EPS 30% below; EBITDA margin 25% below; EPS stated in trading currency) | Rules that run only behind a guidance forecast: the street-basis guards, the share-count invariant, the E1 capex rule, one revenue path, and invariant flags. 237 probes ran; 5 names have no street consensus |

- **Comps refresh:** the local comps store was refreshed first (US, HKSE and SES, 2026-10-07). It was 15 days old, past `MAX_AGE_DAYS = 14`, so every multiple had been falling back to the static table.
- **Tooling:** `scripts/sector_leg_audit.py` (new, read-only) runs the engine and the checks: `local` and `audit` modes.

## 2. What already holds across sectors

| Lesson | Result |
|---|---|
| **Street-basis guard (EPS, EBITDA)** | Fired 126× (EPS) and 140× (EBITDA) in the probes. It abstains only below 5 analysts (owner rule): RELX, and HK/SG names with 1-3 analysts |
| **Share-count invariant** | It ran on every probe that built a guidance forecast. Results: PASS 123, CORRECTED 77, UNRESOLVED 2, no EPS endpoint 14 |
| **One revenue path (guidance forecast ↔ DCF)** | 0 mismatches across 216 forecasts. The overlay legs are not covered; see E7 |
| **Banks, insurers, brokers, GSEs** | 21 names with no guidance forecast, by design. The bank earnings model gates it off (`dcf_agent.py:16305`). The probe moved IV by 0.0% on all 21, and no weighted leg read guidance |
| **Non-guidance DCF capex** | Charges actual capex through owner-earnings FCF. Nothing sets capex := D&A |
| **Double-count guards** | Bank excess capital, Upstream PV-10, holdco look-through, alt-manager P/DE vs FRE+carry and the Defense backlog bound are all bounds or averages, not additions |
| **Failed-anchor routing** | Follows D7 / EN6: the weight goes to DCF, or to Forward P/E where the profile has no DCF |

## 3. Issue register (deduplicated by root cause)

Priority: **H** = moves a weighted leg on many names; **M** = moves a leg on a family, or a high-weight leg on a few names; **L** = display, provenance or hygiene.

### Engine-wide

| # | Root cause | Evidence (reproduced locally) | Sectors hit | Proposed fix | Pri |
|---|---|---|---|---|---|
| E1 | **The lease basis is mismatched on every live-comps EV leg.** Peer EV comes from FMP, which equals mcap + totalDebt (leases included) − cash. The subject's net debt excludes leases for US GAAP filers (`dcf_agent.py` `_valuation_net_debt`). Peer multiples therefore run on lease-inflated EV over after-rent EBITDA, and the subject deducts a lease-free net debt | Live FMP: SBUX EV 126.9bn = mcap 107.9 + debt 22.4 − cash 3.4, where debt includes 9.2bn of leases, so peer EV/EBITDA runs +7.8% high. DAL +9.4%, WMT +2.6% | Retail, Restaurants, Grocery, Airlines, Lodging, Trucking, Cruise; every US EV anchor to a lesser degree | Strip `capitalLeaseObligations` from each US GAAP member's EV in `regional_comps` before medians, so peers and subject are both ex-lease. HK/IFRS members are unchanged | H |
| E2 | **Every USD reporter is treated as US GAAP** (`_US_GAAP_NON_USD_REPORTERS` comment, `dcf_agent.py:1237`). IFRS filers reporting in USD have their leases stripped while their EBITDA excludes rent | Code (comment states it). Names: BHP, RIO, SHEL, BP, AZN, NVS, Jardine, HKLand, Wilmar | Resources, Energy, Conglomerates, SG/HK USD reporters | Use an explicit filing-basis registry (20-F/IFRS), not reporting currency | H |
| E3 | **The E1 capex rule keys only on D&A/capex > 2×.** (a) It catches content amortisation, which is a cash cost in OCF: NFLX, WBD. (b) Between 1× and 2×, capex := D&A + α·Δrev still charges acquired amortisation as cash | NFLX: probe year-1 FCF margin 27.6% vs ~20% history, IV $72 → $80 (+11%). CSCO capex 3.5% vs 1.7% history; GLPI 16.4% vs 8.6%; DKNG 3.1% vs 1.0%; MARA 101% vs 35%. 44 names exceed 2× | Media, REITs, IPP, Refining, IT services, Payments, Info services | (a) Exclude content/film amortisation (a profile or cash-flow-statement test) from the amortisation split. (b) Whenever D&A > capex, cap depreciation at capex and run the excess off as amortisation. Owner to confirm the trigger | H |
| E4 | **The street-basis guard covers only EPS and EBITDA** (`_street_basis_guard`, `dcf_agent.py:4347`). Guidance revenue and EBIT reach EV/NTM Revenue, EV/Fwd Rev, Forward P/S and Fwd EV/EBIT unchecked | Probe, with 5+ analysts: NVDA EV/NTM Rev −40% (w 0.20), AVGO −33% (0.20), PLTR −33% (0.15), MMM Fwd EV/EBIT −33% on the **0.50 anchor** | Fabless, Growth SaaS, Hyper-Growth, Defense Tech & Space, Blended Industrial | Extend the guard to `revenue` and `ebit` using the same 15% / 5-analyst rule and one basis across scenarios | H |
| E5 | **The guard cannot act when consensus EPS is ≤ 0.** It needs `cons > 0`, so a guidance EPS prices freely | WBD: Forward P/E (0.47, anchor fallback) on guidance EPS −138% vs consensus | Any loss-making name with a Forward P/E leg | When consensus ≤ 0 and guidance > 0 (or vice versa), the leg stands down rather than pricing either figure | M |
| E6 | **Forward legs fall back to trailing GAAP peer multiples** when there is no `pe_ntm` / `ev_ebitda_ntm`, or the NTM basket ranks lower (`dcf_agent.py` ~9900, ~8726). Adjusted forward metric × GAAP trailing multiple | 21 profiles in local runs, mostly HK/SG (Toll Road, Telco SG, Property SG, Banks SG, Steel, General Aviation HK, Aerospace Holdco HK), plus IPP (BEPC, CWEN) | HK/SG broadly; IPP; Steel | Label the leg trailing and price the TTM metric, or stand it down; never mix. Longer term: NTM fields for HK/SG baskets | H |
| E7 | **Overlay legs value a path no tab shows**: PPA project-finance DCF, Backlog-coverage DCF, Depleting-asset DCF, DDM. Only the LOE overlay writes back to the guidance rows | Code trace (`_leg_trace` only, ~7746, ~7894, ~7990) | IPP, Defense Primes (0.40 anchor), GA (HK), Upstream/Coal/Mining, Toll Road | Write each overlay's projection rows into the forecast / three-statement tabs, or show an explicit "valuation path" tab per leg | M |
| E8 | **The Backlog-coverage DCF ignores the forecast's margin schedule.** It passes `margin_schedule=_m_sched`, not `_pj["margin_schedule"]`, so it is not "byte-identical" to the core DCF as its comment says | Code (~7878-7900) | Defense Primes (anchor 0.40), General Aviation (HK) | Pass the forecast's FCF-margin schedule through | M |
| E9 | **Flags outlive the override that replaced them.** Only one removal rule exists (~16139). These survive: backlog bear floor, CAGR-divergence gate, revenue-scale cap, analyst dispersion, recovery-discounted normalisation, and "Forward multiples priced on guidance-derived estimates" (header) after a guard swap. "Years 1-3 bounded" prints even when nothing moved | Code trace; probe flags | All guidance-forecast runs | Tag each schedule flag with the schedule it describes and drop it when `_gf` replaces that schedule (one rule). Compose the header from `_source` | M |
| E10 | **Segment SOTP prices unmatched segments at generic 3.0×/3.5×/4.5× and counts them as priced**, then publishes the result as a cross-check. The mixed-basis EBITDA reconcile also double-counts revenue rows (LYB). SOTP 12m (probabilistic) has no gate | 156 of 240 local runs carry a default-tier SOTP shadow. NKE: Footwear/Apparel at 3.0× EV/Rev → 118.7bn | Consumer, Industrials, Energy, Materials (all shadow rows) | Treat "default" segments as unpriced, so the 85% gate refuses; take revenue-row EBITDA out before reconciling; gate SOTP 12m | M |
| E11 | **The LOE row scaling breaks row ties.** Revenue, EBIT, EPS, UFCF and capex are scaled; nopat, da, tax, amortisation and capex_maintenance are not. Forecast invariants and TV are computed before scaling. Also `_LOE_COVERED_YEARS = 2` is a constant, not the guidance horizon | Code (`dcf_agent.py` ~16450, ~215) | Biopharma (found in passing; flagged for completeness) | Scale the full row set; recompute invariants after scaling; tie covered years to `horizon_years` | M |
| E12 | **The DCF-family legs (DCF (FCF+), Backlog DCF) lose the LOE terminal multiplier**; only the core DCF has it | Code (~7932, ~7894 vs ~16518) | Biopharma | Carry `terminal_multiplier` into family projections | M |
| E13 | **SBC handling is inverted.** The 0.90 haircut sits on matched GAAP-vs-GAAP trailing EV/EBITDA and is dropped on the E23 forward reroute. FCF Yield deducts SBC from the subject only; peers' FMP FCF yield does not | Code (~8043-8048, ~8729, ~1808 vs regional_comps ~981) | Tech, Semis, SaaS | Drop the haircut on matched GAAP legs; apply it where peers are SBC-adjusted; deduct SBC on both sides of FCF Yield | M |
| E14 | **Normalised legs mismatch their peer multiples.** The subject is mean margin × current revenue, while peer `pe_norm` / `ev_ebitda_norm` is mcap or EV over mean levels; there is a trailing fallback; EV/EBIT (norm) always uses the trailing `ev_ebit` | Code (~8112, ~8598, ~8094; dynamic_multiples:661) | Memory, Mining, Steel, Shipping, Commercial Aero (anchor), Defense Primes | Peer norm on mean margin × current revenue; add `ev_ebit_norm` | M |
| E15 | **FCF Yield charges capex capped at D&A against peers' full-capex yield** (`dcf_agent.py` ~9020), and the cap drops the SBC basis. Distributable CF already fixed this with an owner yield | Code | Waste (0.30), Aggregates, Packaging, Diversified Miners | Same treatment as Distributable CF: an owner yield, or uncapped capex on both sides | M |
| E16 | **Peer provenance is lost or self-included** on these paths: ticker basket blends (BLK), developer cluster (the subject in its own median), `label_multiples_ruled` with no `exclude=`, the dynamic sector KG path, and static tables | Local: 02202.HK has no members_used. Code for the rest | Asset Managers, HK developers, Electrical Equipment, sector fallbacks | One `_peer_record()` helper with members_used and subject exclusion on every path | L |
| E17 | **The share-count invariant only runs with a guidance forecast and only on base.** HK/SG skip the quote cross-check and dilution rebasing; the dual-listing EPS basis is unverified (09988.HK) | Code (~16072, ~12353) | HK/SG, dual listings | Run invariant 3 (consensus NI / EPS vs shares) on every run and every scenario; restore the HK/SG quote cross-check | M |
| E18 | **Net-debt cash-like items are inconsistent.** Long-term marketable securities net only for biotech; customer funds are netted as cash (MELI as Tech; the Crypto sector is not excluded); finance leases are stripped with operating leases | Code (~1331, ~974; api.py:200) | AAPL-type treasuries, MELI, COIN/HOOD, AMZN/data centres | Net marketable LTI for non-financials; a float exclusion by profile; strip operating leases only | M |
| E19 | **Guidance reports `applied=True` on bank-model runs** where nothing used it | Probe: all 21 bank/insurer names, IV unchanged | Financials | Set `applied=False` with reason "bank earnings model prices this name" | L |
| E20 | **Forecast invariant failures change nothing**: terminal-multiple bound failed 71×, cash conversion 43×, operating jaws 14× (partly probe-induced) | Probe flags | All | Owner decision: does a failed invariant gate the forecast or stay a disclosure? | L |

### Industry-wide

| # | Root cause | Evidence | Profiles | Proposed fix | Pri |
|---|---|---|---|---|---|
| I1 | **Finite revenue is priced as a perpetuity** (the LOE analogue is missing). Toll-road concessions sit under a perpetuity DDM; there is no spectrum, take-or-pay or WALE expiry; the infra-REIT cap rate is a flat 8.5% | Code; profile rationale admits it (sector_profiles ~3168) | Toll Road (HK), Telecom, Midstream, REIT (infra, WALE) | A `concession` industry input (term, end-of-term value) behind review, as PPA does; DDM/DCF run off at expiry. Constants PROPOSED | M |
| I2 | **The depleting-asset DCF has no depletion curve**: flat growth over a constant 15 years, ignores guidance, no trace | Code (~7990, ~10031) | Upstream O&G, Coal, Mining (Major) | A reserve-life horizon and decline curve from an accepted reserves input | M |
| I3 | **The backlog-gated EV/EBITDA haircuts NTM consensus EBITDA, which already prices the backlog** (a double count). Backlog has no run-off and clamps guided years 1-2 | Code (~5716, ~7726, ~9798) | Long-Cycle E&C (anchor 0.50), Defense Primes | Apply the coverage haircut to trailing EBITDA only; start backlog bounds after the guided years | M |
| I4 | **Anchors that need owner inputs fail without them**, so the whole profile prices off Forward P/E or DCF | Local: BRK-B SOTP 78% → Fwd P/E; 9CI.SI 75%; BX/APO P/DE 40-44%; MET/PRU Embedded Value 37%; U14.SI NAV 55%; 01113.HK NAV 40%; BA EV/EBIT (norm) 30%; 02202.HK P/BV 65% → pro rata | Holding Co, Alt Asset Mgr, Insurance (life), Property, Commercial Aero | Data: accept the inputs (DE, SOTP, EV, NAV). Engine: none needed (D7 works) | M |
| I5 | **Stand-in legs value the company on book or revenue** | Local: Hotel RNAV ← P/BV 0.62; Utility P/Rate Base ← P/BV 0.35 (book includes goodwill); Apparel Brand Val ← EV/Revenue; Hyper-Growth Power Law ← EV/EBITDA; Energy Tech Licensor ← EPV/DCF/EV/Rev (100% stand-ins); Electronic Materials 55% on book | Hotels, Utilities, Apparel, Hyper-Growth, SMR, Electronic Materials | A `leg_fallback` into the operating legs instead of a proxy, as Asian Holdco and RE Asset Manager already do | M |
| I6 | **The Alt Asset Manager legs (P/DE, FRE + carry) omit balance-sheet investments, net debt and minorities** | Code (~8760-8784) | Alt Asset Manager | Add accepted balance-sheet NAV − debt to both legs | M |
| I7 | **Thin or duplicated baskets** | NWL listed twice in Consumer Durables (SP ~4161), which clears the 4-name floor with 3 names. Under 3 live members: Battery (CATL only), COST/BJ, CPRT/RBA, FNMA/FMCC, Commercial Aero (no basket) | Listed | Dedupe NWL; add members or fall back to the industry rung | L |
| I8 | **Customer funds counted as cash** | Code | MELI (Tech), COIN/HOOD (Crypto), asset managers' consolidated-fund debt | Covered by E18 | — |
| I9 | **Profile hygiene** | Consumer Growth weights P/E 0.20 but also lists it as excluded (SP ~2709/2712); City Gas tickers use two formats (SP ~4127 vs RC ~310); Capital Goods cites a missing `backlog_gated_long_cycle` constant; the industry_profile_map v13 note on Traditional Retail is stale; the "leases in net debt" notes are stale since plan 1C.2 | Listed | Text and config fixes | L |

### Equity-specific (data / owner; not fixed in this pass)

| # | Item |
|---|---|
| Q1 | AAPL: about $80bn+ of long-term marketable securities not counted as cash, roughly $5/share low. Fixed by E18 if accepted |
| Q2 | 09988.HK / 09888.HK: forward EPS basis vs ordinary shares is unverified (ADS ratio display-only) |
| Q3 | NKE, LYB: segment SOTP cross-check rows are misleading until E10 lands |
| Q4 | 02507.HK: base `yr1_eps_est` vs street NTM EPS needs a units/FX check |
| Q5 | Inputs needed for I4: BRK-B SOTP, BX/APO DE, MET/PRU embedded value, U14.SI / 01113.HK NAV |

## 4. Cross-sector matrix

Number of profiles affected (approximate, from runs plus the code trace). ● = anchor or 0.40+ weight affected.

| Issue | Tech/Semis | A&D | Consumer | Industrials | Energy | Mat/Res/Transport | Telco/Property/REIT | Financials |
|---|---|---|---|---|---|---|---|---|
| E1 lease basis | 3 | 1 | 12 ● | 5 ● | 2 | 4 ● | 2 | – |
| E2 USD = US GAAP | 1 | – | 1 | 2 | 3 | 4 | 1 | 1 |
| E3 capex rule | 6 | 3 | 3 | 2 | 3 | 2 | 4 | 7 |
| E4 revenue/EBIT guard | 4 | 1 ● | 1 | 1 ● | – | – | – | – |
| E5 negative-EPS guard | 1 | – | – | – | – | – | – | – |
| E6 trailing fallback | 1 | 2 | 3 | 4 | 2 | 2 | 5 | 4 |
| E7/E8 overlay path & margin | – | 3 ● | – | – | 2 ● | 3 | 1 | – |
| E9 stale flags | all | all | all | all | all | all | all | – |
| E10 default SOTP | shadow | shadow | shadow | shadow | shadow | shadow | – | – |
| E13 SBC inversion | 8 | – | – | – | – | – | – | – |
| E14 normalised basis | 1 ● | 2 ● | 2 | 2 | 2 | 8 ● | – | 3 |
| E15 FCF capex cap | – | – | – | 1 | – | 3 | – | – |
| E17/E18 shares, net debt | 2 | – | 1 | – | – | – | 1 | 3 |
| I1/I2/I3 time-limited | – | 1 | – | 2 ● | 2 | 3 ● | 3 ● | – |
| I4/I5/I6 inputs & stand-ins | 2 | 1 | 2 | 1 | 1 | – | 4 ● | 5 ● |

## 5. Owner decisions (2026-10-10)

| Item | Decision |
|---|---|
| **E20: forecast invariants** | **Selective gate.** A share-count failure keeps its existing correct-and-recheck path. A terminal-multiple-bound failure clamps the terminal value to the bound. A cash-conversion failure fades onto the bound. An operating-jaws failure stays disclose-only |
| **E3(a): content and operating intangibles** | Exclude by profile or sector (Media & Streaming and similar), plus a filings line-item check (content / film amortisation) where available. Excluded names never enter amortisation mode |
| **E3(b): acquired-amortisation threshold** | Split whenever D&A > capex (a **1.0×** threshold), tested on the **historical median ratio** rather than a single year |
| **E3: run-off period** | Keep 10 years by default; override with the filed remaining useful life of intangibles when available |
| **E1: lease basis** | Strip leases from **peer** EV (lease-free on both sides; plan 1C.2 stands). A 3-9% IV drop in retail and airlines is accepted |
| **E5: negative consensus EPS** | Forward P/E **stands down**; its weight follows the standard waterfall (WBD 0.47) |
| **E6: no NTM peer data** | **Relabel as trailing** now across the 21 profiles; defer the HK/SG NTM build |
| **E13: SBC haircut** | **Remove** the 10% haircut; GAAP-to-GAAP consistency, no non-transparent trims |
| **I5: stand-in legs** | Roll stand-ins into the operating legs; retire the P/BV proxies for Hotels and Utilities until inputs are accepted |
| **I1: concession model** | **Term:** per filing input only, no default (PPA sourcing standard). **End-of-term value by archetype:** BOT/concession (toll roads, port berths, utilities) residual = 0; renewable spectrum keeps terminal cash flows with a periodic auction-capex cycle every 10-20 years; REITs/midstream hand back at salvage/land value or a standard exit multiple. **Run-off:** engine growth to expiry, with maintenance and growth capex **tapering to near zero over the final 3-5 years**. **Multi-asset:** asset-by-asset SOTP run-off (staggered hand-backs); weighted-life fallback. **No input:** keep the perpetuity with `FLAG_UNBOUNDED_CONCESSION`, which states the estimated overvaluation |
| **I2: depletion model** | **Horizon:** 2P reserve life, capped at 30 years. **Profile:** a plateau for years 1→K, then Arps hyperbolic/harmonic decline (O&G) or linear throughput decline (mining) to exhaustion. **Price deck:** decouple the engine from a house view (operational alternative still to be specified). **No input:** keep the 15-year flat run with `FLAG_DEPLETION_UNANCHORED` |
| **I4: missing anchors** | **Two layers:** (1) accept primary inputs; (2) when an input is missing, fall back to a sector-appropriate bridge instead of generic Forward P/E. **Alt managers:** DE or FRE+PRE input; otherwise Forward P/FRE or synthetic DE ≈ operating income − net unrealised carry + realised performance fees (3-year median), with GAAP Forward P/E suppressed. **BRK-B:** a two-pillar SOTP input; otherwise synthetic two-pillar (cash and listed equities + operating pre-tax earnings × 12 − float adjustment), with the Forward P/E weight capped at 0%. **Property (01113.HK, 02202.HK, 9CI.SI, U14.SI):** cap rates / GDV / RNAV input; otherwise book × 5-year median P/B (or P/EPRA NTA) instead of Forward P/E. **Life insurers (MET, PRU):** EV or adjusted book ex-AOCI; otherwise P/B ex-AOCI, with GAAP P/E never above 15% weight. **Deep cyclicals (BA):** mid-cycle run-rate × target programme margin; otherwise the 5/7-year cycle-median EBIT on sector EV/EBIT. **Engine rules:** no single leg may absorb more than **40%** of total weight through an anchor cascade (the excess goes to the DCF or the designated secondary multiple); `FLAG_ANCHOR_COLLAPSE_CASCADE` when Forward P/E weight exceeds 50% because of a failed anchor. **Ingestion priority:** P/DE for BX/APO, then adjusted book ex-AOCI for MET/PRU |

## 6. Proposed fix batches

| Batch | Contents | Expected movement |
|---|---|---|
| **A: basis** | E1, E2, E4, E5, E6 | Lease-heavy US names' EV legs −3% to −9%; HK/SG forward legs on a single basis; guarded revenue/EBIT legs |
| **B: capex & cash** | E3, E13, E15, E18 | NFLX/WBD FCF back to cash basis; acquirers in the 1-2× band gain FCF; SBC-heavy tech FCF yield on a like basis; AAPL cash +$5/share |
| **C: one path & flags** | E7, E8, E9, E11, E12, E19 | Mostly display; Defense Primes anchor margin path (IV moves) |
| **D: SOTP & normalisation** | E10, E14, E16, E17 | Shadow SOTP rows refuse; cyclical normalised legs move |
| **E: industry models** | I1, I2, I3, I5, I6, I7, I9 | New PROPOSED constants behind accepted inputs; owner decisions on constants |
| **Owner decisions** | E20 (invariants gate or disclose), E3 trigger, I1/I2 constants, I4 inputs | — |

Each batch would be built as follows:
- a regression test in `tests/test_cross_sector_audit_1007.py`;
- the full suite run with `set -o pipefail`;
- goldens rebased only when the movement is explained;
- one commit per batch, with explicit paths;
- a push only after the owner confirms it, with no production run in flight.

## 7. Fix log

### Batch A: basis (E1, E2, E4, E5, E6), 2026-10-10

**Implemented**

| Item | Change |
|---|---|
| **E2** | New `src/data/filing_basis.py` registry (`IFRS_USD_REPORTERS`, `US_GAAP_NON_USD_REPORTERS`, `reports_us_gaap`). `dcf_agent._reports_us_gaap` reads it, so a USD reporter is no longer automatically US GAAP |
| **E1** | `regional_comps.lease_free_key_metrics` restates a US GAAP member's EV fields (EV, EV/EBITDA, EV/Sales, EV/OCF, and the NTM fields built on EV) ex leases, using one quarterly balance-sheet call per US GAAP candidate. `comps_history_backfill.member_history` does the same year by year for the through-cycle fields. **Takes effect at the next comps refresh** (production: weekly) |
| **E4** | `_street_basis_guard` now covers `revenue` (EV/NTM Revenue, EV/Fwd Rev) and `ebit` (Fwd EV/EBIT), with the same 15% / 5-analyst rule and one basis across scenarios |
| **E5** | A sign conflict (guidance > 0, consensus ≤ 0, 5+ analysts) swaps to consensus and the leg stands down; the flag says so |
| **E6** | Forward P/E and Forward EV/EBITDA with no forward peer multiple are relabelled trailing: TTM metric, trailing multiple, scenario band, `basis: "trailing (relabelled)"`. Fwd EV/EBIT prices on `ev_ebitda_ntm × (ev_ebit/ev_ebitda)`, otherwise relabels. With the NTM switch off, all three are exactly the legacy legs |
| **E9 (part)** | The "Forward multiples priced on …" header names the metrics a guard swapped back to consensus |
| **Golden isolation** | `golden_capture.frozen_comps` now serves every comps-store reader (curated / explicit baskets, ruled labels, developer cluster, label members, basket fields) from `comps.json["store_calls"]`, recorded 2026-10-10, and freezes the `credit_ratings` lookup |
| **Bug** | `regional_comps.basket_multiples` error path referenced an undefined `profile` (NameError) |

**Forward tests:** `tests/test_cross_sector_audit_1010.py`, 12 tests. Local E2E on the current engine (plain + probe):
- MMM: the 0.50 Fwd EV/EBIT anchor now prices on a forward multiple.
- NVDA, PLTR: EV/NTM Revenue swaps to consensus (−40%, −33%).
- WBD: Forward P/E stands down (consensus EPS −0.25 vs 0.67).
- U14.SI, 00177.HK: forward legs relabelled TTM.
- BHP: now IFRS with leases kept.

**Backward-test failures and fixes**

| Failure | Cause | Fix |
|---|---|---|
| 11 goldens (4 already failing at HEAD) | Golden replay read the **local** comps store for curated baskets, and the local `credit_ratings` table (written by the audit's local runs) | Froze both in replay. The remaining moves were explained: E6 shadow rows on 7 fixtures (no weighted leg moved); AAPL −2.0%, V −1.4% (+ 715994a3's E8 flag), 09988.HK −1.5%, BABA −7.9% on the recorded 2026-10-10 baskets. Rebased with `GOLDEN_UPDATE_REASON` |
| `test_tech_wave7` | My Fwd EV/EBIT rewrite dropped the tech sub-type static fallback | Restored |
| `test_comps_ntm_multiples` ×4, `test_power_wave2`, `test_bank_model` | Tests pinned the old mixed basis (forward EPS × trailing P/E) or had no fake for the new balance-sheet call | Restated to the E6 / E1 behaviour. The NTM kill switch keeps the full legacy behaviour |
| `test_valuation_fixes_0917e` ×5, `0917d` ×1 | Pinned baselines | New `_AUDIT_A_MOVED` pins; mult-line census 20 → 23 (three relabel branches); 02888 Forward P/E 134.74 → 90.70 (unweighted); V FCF Yield restated |

**Final backward run:** 6,481 passed, 1 skipped, 4 xfailed.

**Register correction:** E14 is narrower than the trace claimed. `comps_history_backfill` already builds `pe_norm` / `ev_ebitda_norm` on mean margin × current revenue (fixed 2026-09-21). What remains is the trailing fallback and the missing `ev_ebit_norm`.

**Owner note (2026-10-10):** AAPL is misclassified as Hyperscaler. A new Consumer Technology Ecosystem profile follows batch B as **batch F** (owner spec).

### Batch B: capex & cash (E3, E13, E15, E18), 2026-10-10

**Implemented**

| Item | Change |
|---|---|
| **E3** | New `guidance_forecast._capex_rule`. The amortisation split runs when the **historical median** D&A/capex exceeds 1.0× **and** net acquired intangibles cover at least one year of the excess. Run-off is over the implied remaining useful life (net intangibles ÷ annual amortisation, bounded 3-25 years), otherwise 10 years. Content profiles (Media & Streaming) and any content/film amortisation line never split. The forecast reports `capex_rule` (mode, ratio, reason, run-off basis) |
| **E13** | The 10% SBC haircut on EV/EBITDA and EV/EBIT, and the 7% one on EV/NTM Revenue, are retired |
| **E13 / E15** | FCF Yield prices reported FCF (OCF less all capex), the basis of the peers' FMP yield. Owner earnings are only a fallback. The Wave 9 capex ceiling no longer reaches the yield leg. Cyclicals use a normalised reported FCF |
| **E18** | `_apply_lti_netting`, applied once the profile is known: non-financial long-term investments count as cash unless a weighted SOTP or NAV leg prices them. Customer-fund holders (MELI, SE, PYPL, HOOD, COIN, …) and the Crypto sector keep their investments out of cash. The bridge `components` carry the LTI line |

**Not done:** separating finance leases from operating leases. The feed exposes one `capitalLeaseObligations` line.

**Forward tests:** 8 new tests (20 in the file). Local E2E (plain + probe):
- NFLX, WBD: content excluded; NFLX probe FCF margin 23.6% vs 27.6% under the old rule.
- CSCO now splits (3.11×, run-off 6.7 years).
- AVGO splits (4.0-year implied remaining life); CRM splits (10 years).
- AAPL: no split (D&A ≈ capex, no acquired intangibles, per the owner's AAPL spec); $84bn of long-term investments counted as cash.
- WM: full-capex FCF.

**Backward-test failures and fixes**

| Failure | Cause | Fix |
|---|---|---|
| Goldens AAPL, MELI, MU, V | Intended: E18 net debt (AAPL +21.9bn → −62.2bn, MU −20.9 → −25.0bn, MELI 4.9 → 7.0bn) and E15 FCF basis | Rebased with reason |
| `test_three_statement` E1 ×2 | AMGN-like fixtures carried no intangibles, so the new evidence rule blocked the split | Fixtures given ten years of intangibles |
| `0917d` owner-earnings precedence; `test_industrials_wave9` ceiling | Both pinned the subject-only bases E13/E15 retired | Restated |
| `_normalized_earnings` census 12 → 13 | New normalised reported FCF | Restated |
| `0917e` ×4, `0917d` FCF pins | Baselines | `_AUDIT_B_MOVED`: AAPL +2.6%, COST +1.2%, MU +1.1%, MELI, V |

**Final backward run:** 6,489 passed, 1 skipped, 4 xfailed. Goldens re-checked after the reason-text fix: 17 passed.

### Batch F: AAPL, Consumer Technology Ecosystem (owner spec), 2026-10-10

**Implemented**

| Item | Change |
|---|---|
| **Profile** | New Tech profile `Consumer Technology Ecosystem`. Weights: SOTP (Hardware + Services) 0.40 (anchor, fallback `P/FCF (NTM)`), DCF 0.35, Shareholder Yield 0.25, P/FCF (NTM) 0 (cross-check). Excluded: EV/EBITDA, Forward P/E, DDM |
| **Routing** | AAPL pinned to the new profile; AAPL removed from the Hyperscaler curated basket |
| **Peers** | Cross-market basket MSFT, GOOGL, RMS.PA, MC.PA, 005930.KS, SONY. Rebuilt by the weekly comps job, so production gets it at the next refresh |
| **WACC / TGR** | 8.25% (owner range 8.0-8.5%); TGR 2.5 / 2.75 / 3.0% |
| **SOTP (Hardware + Services)** | Operating net income (ex after-tax interest income) is split by segment gross profit: hardware GM 36%, services 72.5%. Hardware at 15 / 16.5 / 18× and Services at 26 / 29 / 32×. Net cash is added back. The bear case stresses services GM by 300bp (TAC). Constants are PROPOSED, inside the owner's ranges |
| **Shareholder Yield** | Normalised reported FCF / (cost of equity − net share shrink). Shrink is the median annual share-count decline, 0-5%, scaled 0.75 / 1 / 1.25 by scenario |
| **P/FCF (NTM)** | TTM FCF × NTM/TTM revenue over the peer FCF yield |
| **D7 hook** | A profile can name its anchor fallback, used before the DCF / Forward P/E order |
| **Holdings guard fix** | Only holdings-pricing legs (analyst / published SOTP, look-through, NAV, RNAV, FRE + carry) block the long-term investment netting. An operating SOTP adds net cash back, so AAPL nets its ~$84bn |
| **E3** | AAPL's D&A ≈ capex with no acquired intangibles, so no amortisation run-off, as the spec requires |

**Forward evidence (local, current engine, 2026-10-10 data):**
- AAPL base IV $161.91 (bear $131.11, bull $202.85).
- SOTP $163.51: Hardware net income $65.3bn at 16.5×, Services $46.7bn at 29×.
- DCF $185.77; Shareholder Yield $125.94 (2.6% shrink, 8.5% cost of equity); P/FCF (NTM) $160.83.
- 4 tests (24 in the file).
- **Owner note:** at these constants the blend sits well below the ~$330 spot. The spec's multiple ranges put AAPL near 21× operating earnings, against the ~40× the market pays.

**Backward-test failures and fixes**

| Failure | Cause | Fix |
|---|---|---|
| All 17 goldens | `param_version` hashes the profile table | Rebased. The AAPL fixture was re-recorded at its original 2026-09-16 end date, because its profile-keyed calls changed; base IV 184.04 → 195.07 |
| Census tests ×5 | Profile count 147 → 148 | Updated |
| `test_tech_wave7` | Pins and basket listed AAPL under Hyperscaler | Updated |
| `0917d` / `0917e` / `0917f` | AAPL pins from the Hyperscaler profile (FCF Yield leg, bear TGR, bull premium, basket) | Restated; `_AUDIT_F_MOVED` |

**Final backward run:** 6,494 passed, 1 skipped, 4 xfailed.

### Batch C: one path & honest flags (E7, E8, E9, E11, E12, E19, E20), 2026-10-10

**Implemented**

| Item | Change |
|---|---|
| **E20** (owner: selective gate) | **Terminal-multiple bound** fails → `terminal_clamp = bound / implied exit multiple`, and the DCF multiplies its terminal value by it (on top of any LOE haircut). **Cash conversion** below 0.60× → the steady-state years fade linearly onto the bound; the existing hard cap above 1.20× is unchanged. **Operating jaws:** disclose only. **Share count:** the existing correct-and-recheck path |
| **E11** | `_loe_scale_forecast` scales tax, nopat, da, amortisation and capex_maintenance too, so every row tie holds. Invariants are marked as measured before the overlay. LOE covered years = max(2, the forecast's guided horizon) + street years, in both the run and the estimate-override recompute |
| **E12** | `_dcf_projection` carries the LOE `terminal_multiplier`; DCF-family and backlog legs apply it and trace it |
| **E8** | With no accepted FCF-guidance fade, the backlog leg takes the projection's own margin schedule (guidance forecast, cascade or capex fade), so the unbounded leg really is the core DCF |
| **E9** | `_SUPERSEDED_BY_FORECAST`: a scenario whose guidance forecast replaced the engine path drops the CAGR-gate, revenue-scale-cap, analyst-dispersion and bear-backlog-floor flags, and one line names them. The forward-multiples header (batch A) names the guard swaps |
| **E19** | Guidance on a bank-model run is reported `applied: false` with the reason |
| **E7** | The depleting-asset DCF traces its 15-year path (`kind: depleting_dcf`). A new workbook tab, **Valuation paths**, shows every weighted overlay leg's year-by-year path (PPA project finance, depleting asset) |

**Forward tests:** 7 new (31 in the file); both E20 branches were confirmed to execute. Local E2E:
- LMT, NKE, COP probes drop the superseded flags (analyst dispersion; COP also the CAGR gate).
- JPM reports guidance not applied (bank model).
- LMT's unbounded backlog leg equals the core DCF.
- COP's workbook carries the Valuation paths tab, tie-out 26/26.

**Backward-test failures and fixes**

| Failure | Cause | Fix |
|---|---|---|
| `test_backlog_coverage_dcf` ×3 | Byte-identical trace parity: the family leg now traces `terminal_loe_multiplier` and the backlog leg did not | Backlog trace carries it too |
| `test_research_guidance_channel` | Source pin on the payload call | Restated to the E19 call |

**Final backward run:** 6,501 passed, 1 skipped, 4 xfailed; no golden moved.

### Batch D: SOTP, normalisation, provenance, share basis (E10, E14, E16, E17), 2026-10-10

**Implemented**

| Item | Change |
|---|---|
| **E10** | A segment whose name matches no type is **unpriced**: no generic 3.0-4.5× revenue multiple, so the 85% gate refuses a SOTP built on defaults (NKE). Mixed EBITDA/revenue tables reconcile the EBITDA rows to their revenue share of company EBITDA (no double count, LYB). The probabilistic 12m SOTP applies the same type rule and the same 85% gate |
| **E14** | New through-cycle field `ev_ebit_norm`: EV / (mean EBIT margin × the year's revenue) in the comps backfill, a band, `industry_fields` and `LIVE_TO_NORM`. EV/EBIT (norm) prices on it and otherwise discloses the trailing fallback, as does EV/EBITDA (norm). Populates when production's history backfill / quarterly update runs |
| **E16** | Ticker blends carry `members_used` and `subject_excluded`. The developer cluster no longer includes the valued name (plan 1F.3) and names its members with values. Ruled labels pass `exclude=ticker` |
| **E17** | The street share-count check runs on **every** run: consensus net income / EPS vs the model's divisor; outside ±15% it flags (ADS ratio, share class, units). HK/SG now fetch the FMP quote, so the share cross-check and recency/dilution rebasing apply there too. `HKSG_QUOTE_CROSSCHECK` switch; golden replay pins it off because the fixtures predate the call |

**Forward tests:** 8 new (39 in the file). Local E2E:
- 00700.HK, 00688.HK: shares from `quote_current_diluted`.
- NKE: the segment SOTP leaves its cross-checks.
- 00688.HK: developer cluster excludes itself, 10 peers.

**Backward-test failures and fixes**

| Failure | Cause | Fix |
|---|---|---|
| Goldens BABA, COST, FCX, V | Generic-multiple SOTP cross-checks refused (−2 unweighted rows; no IV moved) | Rebased |
| `test_aerospace_defense_wave3`, `test_dynamic_multiples_industry` ×2 | Fallback labels now disclose the trailing basis; `industry_fields` gained `ev_ebit_norm` | Restated |
| `test_realestate_wave8b` | Asserted the developer sat in its own cluster | Restated: it does not (1F.3) |

**Final backward run:** 6,509 passed, 1 skipped, 4 xfailed.
