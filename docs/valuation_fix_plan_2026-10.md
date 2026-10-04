# Valuation fix plan: October 2026

Built 2026-10-04 from four workbook reviews: Birkenstock (BIRK), Moderna (MRNA), JD.com (9618.HK) and Boeing (BA).
These are read against `docs/valuation_lessons_handoff.md`. Row numbers (E = engine, I = industry,
Q = equity) refer to the observation sort done in that session.

The work runs in order: engine first, then industry, then single equities. Within the engine, the outputs
are fixed before the maths, because a workbook that disagrees with the engine cannot be used to check
any other fix.

**Every step follows the same method:**
- Confirm the cause before building the fix. Two earlier guesses were wrong (MELI, GEV).
- Run `wave_baseline.py` before and after.
- Re-base the golden snapshots with a stated reason.
- One commit per step, staging only the files named.
- Nothing is pushed without the owner's word.

---

## Phase 0: Set the baseline (no code changes)

1. Refresh the local comps for all 7 markets, then check `latest_refresh_age_days`.
2. Re-run BIRK, MRNA, 9618.HK and BA locally and export their workbooks. They make up the
   **review regression set**.
3. Keep the existing reference names alongside it:
   - MU, JPM, BABA and COST (the cash-conversion gate set);
   - D05.SI and 02020.HK;
   - the 17 golden fixtures.
4. Take a `wave_baseline.py` snapshot.
5. Write a **tie-out harness**. For each regression name, recompute every workbook "Check" cell in
   Python from the stored run, and report pass or fail with the size of the gap. This is the gate
   for Phase 1A: today every check fails on all four names.

## Phase 1: Engine-wide

### 1A. Make the workbook and the stored run agree (hit by 4 of 4 names)
Nothing else can be audited until the workbook and the stored run agree.

| Step | Fix | Rows | How the cause is confirmed |
|---|---|---|---|
| 1A.1 | The workbook builds its scenario drivers from the **post-gate** inputs that the engine actually priced | E9 | JD: 229.44 / 157.14 = 0.657, which equals 1.63% / 2.48%. BA: the tab shows 5.0% while the engine used 7.1–7.3%. Diff the Assumptions inputs against the stored run's post-gate inputs for all four names |
| 1A.2 | The gate ledger records what was actually applied | E28 | JD's ledger says "not applied" for a cut that was applied. BA's says "recorded, not applied" for a gate that never fired. Find where the ledger's applied flag is written |
| 1A.3 | One net-debt (equity bridge) object, written once and read by Summary, BS, DCF, SOTP and the sensitivity grids | E8, E22 | Four of four names show two or three different net-debt figures. BA's SOTP hardcodes −26,000 |
| 1A.4 | Write the capture rate and its reason (the 35% rule, or 50% when every scenario is on the same side of spot) to the workbook | E18 | [dcf_agent.py:16027](../src/agents/analysis/dcf_agent.py) |
| 1A.5 | **Rule #2:** delete any figure that carries zero weight in the blend (BA's DCF, the second EV set) | — | Decision D1 (whether to delete the zero-weight DCF or give it a minimum weight) |
| 1A.6 | Housekeeping: units, Cover company and date, EBIT margin "n/a", and a Data Gaps tab that lists this run's failed gates | E16, E32 | Cosmetic |

**Exit:** the tie-out harness passes on all four names.

### 1B. Currency and dates (every name that could hit it did)

| Step | Fix | Rows |
|---|---|---|
| 1B.1 | The three-statement Model tab takes revenue in the statement currency (BIRK placed USD in a EUR sheet; JD placed HKD in a CNY sheet). JD's buybacks must be converted at FX | E1 |
| 1B.2 | Normalised EPS and EBITDA carry an explicit currency and go through both FX lists (BIRK's 1.5255 is a EUR figure; JD's 41.7bn is unlabelled) | E2, E3 |
| 1B.3 | **Anchor the valuation date** (extends the dating rule from 6130d6e2): Year 1 is the first fiscal year ending *after* the valuation date; income comes from the same date as the balance sheet; cash already earned in a part year is not discounted again | E4 |
| 1B.4 | Consensus and guidance rows are aligned by year-end date. Empty years stay blank instead of showing zero | E5 |
| 1B.5 | Guided FCF and margin set the explicit years (rule #7). BA's guided FCF is $1–3bn against a model $4.7–6.9bn | E31 |

Decision D2 (mid-year or year-end discounting) is settled during 1B.3.

### 1C. The equity bridge (4 of 4 names show a bridge defect)

| Step | Fix | Rows |
|---|---|---|
| 1C.1 | One rule for which cash counts: short-term investments are treated the same way in every leg | E8 |
| 1C.2 | Leases follow the EBITDA basis: under IFRS 16, deduct lease liabilities; under US GAAP operating leases, leave them out | E7 |
| 1C.3 | Do not count interest twice: if net cash is added to the value, take interest income out of FCF (JD: CNY 7.6bn a year) | E21 |
| 1C.4 | Value minority interest in **listed** subsidiaries at market value (JD Logistics, JD Health) | E23. Decision D3 |
| 1C.5 | Deduct pension and retiree-benefit deficits. Find out why preferred read 0 for BA's mandatory convertible | E30 |
| 1C.6 | Use the current diluted share count, dated. BIRK used basic shares; MRNA's count was 389m against 399m outstanding | E15 |
| 1C.7 | Value tax-loss carryforwards at a haircut | E20. Decision D4 |

### 1D. The discount rate (4 of 4 names)

| Step | Fix | Rows |
|---|---|---|
| 1D.1 | Use the **agency credit rating** when one exists. Never compute a synthetic rating from leverage on near-zero book equity (BIRK came out AA; BA came out B against an actual BBB-) | E29 |
| 1D.2 | An invariant: WACC must not exceed the cost of equity when debt is cheaper. Fail loudly if it does (BA: 11.46 > 9.36) | E29 |
| 1D.3 | Record every WACC build step with its reason: sector table, country premium (zero for China and HK by owner stance), regime overlay, overhang. Show the same reason on every tab | E17 |
| 1D.4 | Check that beta is fresh and plausible (MRNA's 0.899 is stale; JD's 0.36 is meaningless), and fall back to a sector beta | E17 |

Decision D5 (whether to keep the −50bp risk-on cut) is settled during 1D.3.

### 1E. Trace the earnings inputs

| Step | Fix | Rows |
|---|---|---|
| 1E.1 | Remove one-off gains (disposals) from trailing EPS and EBITDA before applying a multiple (BA's Jeppesen sale) | E27 |
| 1E.2 | Normalisation window: leave out years distorted by financing costs (BIRK's IPO-era interest). Same fix family as the turnaround margin (GEV) | E12 |
| 1E.3 | Trace the base FCF margin. BIRK uses 20.69% where a rebuild gives 16.4%; JD uses 2.48% against about 0% in FY25. Show the result of the OE ≤ 0 rule on the front page | E6. Decision D6 |
| 1E.4 | Growth reinvestment: capex tied to growth; an ROIC-vs-WACC gate on terminal growth (backlog #6) | E13 |

### 1F. Gates, anchors, peers and scenarios

| Step | Fix | Rows |
|---|---|---|
| 1F.1 | When the anchor leg fails, flag it and degrade the run rather than quietly moving its weight onto trailing legs | E26. Decision D7 |
| 1F.2 | A failed check acts: the cash-conversion check failing at 1.50x against a 1.20x limit must apply the cap | E28 |
| 1F.3 | Leave the company out of its own peer set. Show peer names, and show the derivation from median to applied multiple, including any premium | E10, E11 |
| 1F.4 | Scenarios: an invariant that bear ≤ base ≤ bull on every driver. Scenario spread and drivers vary by company (WACC and net debt can flex) | E19, E24. Decision D8 |
| 1F.5 | Grade research sources: third-party aggregators (TIKR, Mordor) rank below filings and companies' own guidance | E25 |
| 1F.6 | Re-run the backtests once 1A–1E are done. Calibration stays proposal-only | E14 |

**Phase 1 exit:**
- The tie-out harness is clean.
- The review regression set has been re-run.
- Each reviewer's fix estimate has been compared with the result:
  - BIRK: about $34.94 blended;
  - JD: HKD 110–130;
  - BA: lower, closer to its DCF.

## Phase 2: Industry-wide

New multiples are derived from the current market and shown before use (rule #3). New constants
ship PROPOSED.

| Step | Industry | Fix | Rows |
|---|---|---|---|
| 2.1 | Biotech | Check MRNA's routing (why it went to "Operating company"). Loss-making commercial biotech routes to EV/forward revenue plus rNPV | I4 |
| 2.2 | Biotech | Make rNPV the anchor once pipeline inputs are accepted. Add cash runway and dilution as a per-share deduction when cash falls short of cumulative consensus losses | I5, I6 |
| 2.3 | Biotech | Proposed WACC band for pre-profit biotech | I7 |
| 2.4 | Retail / e-commerce | An e-commerce or first-party retail profile, which JD needs alongside the eCommerce handbook (backlog #11). Exclude supplier float from cash, measured over the season and not on the 618 or quarter-end peak | I8, I9 |
| 2.5 | Aerospace and defence | Split the baskets into commercial airframers, defence primes and aftermarket. Add Airbus as a cross-market peer | I10. Decision D9 |
| 2.6 | Order-book companies | GEV method table: backlog-coverage DCF, orders and book-to-bill. Covers BA and GEV | I11 |
| 2.7 | SOTP | Apply the holdco discount only on holdco profiles. Check that a SOTP from LLM research never prices a leg (§1.5) | I12 |
| 2.8 | Branded consumer | EV/revenue "Brand Val" leg: label and weight; check whether the footwear and apparel basket is too thin (n=6) | I2, I3 |

## Phase 3: Equity-specific

| Step | Name | Fix | Rows |
|---|---|---|---|
| 3.1 | BA | Confirm the Jeppesen gain and the convertible preferred in the 10-K. Check the segment inputs against filings. Add 737 MAX, 777X and FAA-cap scenarios | Q7–Q10 |
| 3.2 | MRNA | Pipeline pre-fill (intismeran, CMV, flu, combination vaccines, Merck split) for owner acceptance. Check 2021 opex against the 10-K | Q3, Q4 |
| 3.3 | 9618.HK | Food-delivery subsidy-war and EU-investigation scenarios | Q6 |
| 3.4 | BIRK | Insider overhang (Decision D10). Re-score against the review's estimates | Q1, Q2 |
| 3.5 | Earlier backlog | MOH blend, BABA SOTP aligned to 09988.HK, Wave 9 outliers, pre-fills waiting on the production gate | handoff §4 |

---

## Owner decisions, and the step each one blocks

| # | Decision | Blocks |
|---|---|---|
| D1 | A DCF at zero weight: delete it, or give it a minimum weight when it disagrees sharply with the blend? | 1A.5 |
| D2 | Mid-year or year-end discounting | 1B.3 |
| D3 | Minority interest in listed subsidiaries at market value | 1C.4 |
| D4 | Tax-loss carryforwards: value them, and at what haircut | 1C.7 |
| D5 | Keep the −50bp risk-on WACC cut, or limit it to a band around CAPM | 1D.3 |
| D6 | OE ≤ 0 rule for a real loss-maker: median of positive years, or start from trailing and fade | 1E.3 |
| D7 | Failed anchor leg: degrade the run, or use a named fallback leg | 1F.1 |
| D8 | Scenario spread set by company or industry (wider for binary pipelines) | 1F.4 |
| D9 | Cross-market peers for global duopolies (Airbus) | 2.5 |
| D10 | Insider overhang: a general constant or BIRK only | 3.4 |
| D11 | Rating capped at Hold when the target is below the cost of equity (a rating rule; capture is unchanged) | after 1F |

---

## Phase 1b: the MOH, JD (9618.HK), D05.SI and 9988.HK reviews (2026-10-04)

Checked against the production runs of 06:51 UTC, which already include Phase 1. Some findings were
already fixed by Phase 1 (marked), and one was not reproduced.

### Engine-wide: implemented automatically

| # | Severity | Finding | Fix |
|---|---|---|---|
| EN1 | Critical | Profiles without a growth schedule hold the year-1 growth flat for 10 years (9988.HK: -2% / +9.4% / +16.3%) | Two-stage path: the year-1 rate for 5 years, then a straight line to terminal growth by year 10 (cyclicals keep their geometric fade; a full fade cut V's and AAPL's DCF by 40-45%) |
| EN2 | Critical | Normalised earnings are priced as if recovery were immediate (MOH normalised EPS $23.3 against guidance >= $5.25; JD normalised EBITDA 2.8x FY25) | Recovery-discounted normalisation: any excess over forward earnings is discounted over the years consensus needs to reach it (at most 5) |
| EN3 | Critical | The guidance forecast prices the margin endpoint when it conflicts with guided EPS (MOH FY27 EBIT 2.7x the street); the share-count check reads PASS on 141m implied shares against 52m | When the two conflict, the EPS-consistent margin is used; the share-count check FAILs beyond 15% |
| EN4 | High | Capex-cycle trough: the DCF starts at the base margin while the latest year is a capex-driven trough (9988.HK FY26 -5% FCF, capex 12.4% of revenue) | The margin fades from trailing to base over 5 years (the D6 mechanism) when capex intensity is above 1.3x its own history |
| EN5 | -- | SOTP: the holdco discount on net cash | **Reverted.** The analyst SOTP reproduces the published method, and broker convention discounts the whole NAV (Meituan reproduces GS's HK$123 only that way). Now an owner question |
| EN6 | High | Anchor fallback: profile leg fallbacks rolled the anchor's weight pro rata without a DEGRADED flag (MOH P/E (Ops)); for insurers and banks the DCF is the wrong fallback | The anchor roll goes through D7; balance-sheet families fall back to Forward P/E |
| EN7 | Medium | Year 1 ignores interim results (JD Q2 -2.9% revenue against +3% for FY26) | The elapsed part of year 1 grows at the trailing-twelve-month rate |
| EN8 | Medium | The Comps check fails because the basket is re-read at export (basket drift); peers with zero or missing market cap are counted | Member values are frozen into the run; zero or missing market cap is excluded and flagged |
| EN9 | Medium | Statement data defects are not surfaced (negative inventory, lines zeroed, a gross margin step that doubles) | Statement sanity checks go on Data Gaps |
| EN10 | Medium | Four net-debt figures on four tabs | One reconciliation block on the Summary (each figure with its date and basis) |
| -- | Done in Phase 1 | Interest income in FCF (JD); minorities and preferred everywhere; lease basis; workbook = engine | -- |
| -- | Not reproduced | 9988.HK "P/E leg in CNY": normalised NI is taken after FX (HK$4.92 a share) | -- |

### Industry-wide: implemented automatically

| # | Severity | Industry | Fix |
|---|---|---|---|
| IN1 | Critical | Managed care | Cash and investments are regulated capital: net debt = gross debt unless a parent-only cash figure is supplied. Forward P/E anchors the profile; the DCF carries no weight (an insurer's firm cash flow is not owner cash). Curated US basket: CNC, ELV, UNH, HUM, OSCR, ALHC (not PGNY, CVS or CI) |
| IN2 | Critical | Banks | Excess Capital is an add-on to GGM, not a standalone leg (its weight goes to GGM and RI). GGM flags state the ROE actually used. Scenarios move ROE and CoE instead of x0.75 / x1.25. Tax rate from the bank's own history; payout from history (ordinary plus special) |

### Owner decisions (not changed)

- The capture rule's unanimous +15 points (MOH and D05 reviewers): your rule of 2026-09-15.
- Calibration stays at 1.0 despite MARKET_CLOSER: calibration proposes, only you accept.
- China/HK country premium zero (9988.HK reviewer): your stance of 2026-09-26.
- An e-commerce / first-party retail profile for JD (backlog #11).
- Cross-market bank peers for SGX banks (each market owns its multiples).

### Equity-specific: questions for the owner

- MOH: parent-only cash; Florida / Medicare Advantage exit; MLR by segment.
- JD: stakes in JD Logistics, JD Health, Dada and JD Industrials (D3 registry); the Ceconomy deal; the FY2025 reclassification.
- 9988.HK: the Ant Group stake and listed holdings in the SOTP input; four vs five segments; convertible notes.
- D05.SI: the capital-return programme (special dividends); a NIM-vs-SORA scenario.

## Phase 1c: the Visa (V) and Vertex (VRTX) reviews (2026-10-04)

### Engine-wide: implemented automatically

| # | Severity | Fix |
|---|---|---|
| EV1 | Critical | One macro regime per day. The first classification of the day is stored in `macro_regime_daily`, and every run, worker and web process reads it, so two runs on the same day can't land on opposite sides of risk-on/off. V and VRTX moved WACC by 2pp on a regime flip. |
| EV2 | Critical | The CAPM band is symmetric. Regime and insider overlays can't take WACC more than 1pp **above** CAPM, and the table rate itself is never cut. An implausible beta is clipped to 0.40–2.50 for this ceiling only (VRTX 0.32). Insider **selling** below 0.5% of market cap is ignored, including the conviction-sell flag (V: $83m, +16bp). |
| EV3 | High | Scenario margin shifts flow through. The wider of the scenario delta and the guidance block's own delta applies (V: bear, base and bull were within 0.5pt of each other). |
| EV4 | Critical | A guided EBITDA margin more than 10pt from both history and consensus is rejected for the consensus margin, keeping each scenario's offset (V: a "~50%" guide against a 74–78% consensus). The constant is PROPOSED. |
| EV5 | High | NWC intensity is bounded to [−10%, 30%] of incremental revenue, and years with \|ΔWC\| > 10% of revenue are excluded (V: 50%). |
| EV6 | High | Forward legs price the next twelve months: (1−e)·FY+1 + e·FY+2, where e is the elapsed share of FY+1. This applies to the consensus and to the research overlay when its FY+1 is the year in progress (VRTX: FY2026 EPS in October 2026). |
| EV7 | Medium | The terminal-multiple check is two-sided and ROIC-aware. The ceiling is max(peer median, 1.1 × the ROIC-justified multiple) and the floor is 0.5 × the peer median (V's 11.8x at a 40% ROIC passes; VRTX's 10.1x against 25x fails). |

### Industry-wide: implemented automatically

| # | Severity | Industry | Fix |
|---|---|---|---|
| IV1 | Critical | Payment networks | Asset-light, not a balance-sheet business: unlevered basis and the three-statement model. **The curated peer basket is an owner decision** (see below). |
| IV2 | Critical | Commercial Biotech, Large Cap Pharma | Pipeline rNPV is a sum-of-the-parts **add-on**: its risk-adjusted PV per diluted share is added to each operating leg, and its weight rolls into them. Pre-approval Biotech is unchanged, since there the pipeline is the company. |
| IV3 | High | Biotechnology | Long-term marketable securities count as cash (VRTX ~US$10bn); pharma strategic stakes do not. |

### Owner decisions (2026-10-04)

- **Payment Networks basket:** the toll-road basket. MA, AXP, SPGI, MCO, ICE, CME and MSCI (V excluded) price at EV/EBITDA 17.9x and P/E 23.5x live. The reviewer's processor basket gave 8.8x and 12.2x, the same defect.
- **D8 scenario WACC ±50bp: removed.** Bear and bull already move the cash flows, so one WACC applies across scenarios.
- **Pipeline add-on:** carries unapproved assets only. Approved products' sales are already in the operating revenue (VRTX production: CASGEVY, JOURNAVX and PALSONIFY).

### Equity-specific

- **V:** researched and staged as PROPOSED in `valuation_constants.bridge_adjustments`. It prices only when accepted.
  - As-converted shares: 1,880m. The convertible preferreds are inside that count, so the $514m book is not deducted.
  - Debt-like: uncovered accrued litigation of $363m. U.S. covered litigation ($822m) nets against the $888m escrow, and VE-covered is recovered from series B/C.
  - Added back: the FY2025 U.S. covered litigation provision of $2,210m.
- **VRTX:** pipeline re-check and Trikafta loss of exclusivity are deferred by the owner.
