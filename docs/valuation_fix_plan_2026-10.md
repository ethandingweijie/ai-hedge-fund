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
