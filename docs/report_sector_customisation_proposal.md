# Production report: from a generic PM assessment to a profile-keyed report

Owner request, 2026-09-27, after the JPM production report review. Recommendation only; nothing built.

## 1. What the JPM report showed

Three faults, only one of them a valuation fault.

| Fault | Layer | Cause |
|---|---|---|
| FCF −US$147.8bn and net debt US$599bn in Key financials | Report | one generic key-financials block for every sector (fixed 2026-09-27 for balance-sheet financials; the same generic block still serves 118 other profiles) |
| "consensus claim of +45%", "41.7 percentage-point divergence", "ROTCE of 17%" in the thesis | Narrative | none of the three exists in the valuation record (growth base 14.2% on total income; GGM RoTE 19.5%). The rationale prompt demands "at least TWO specific figures" per theme and supplies a handful of anchors, so the writer manufactures figures to meet the density rule |
| SELL beside "Analyst revisions: ACCELERATING UP" and "News sentiment: BULLISH" with no reconciliation | Report | the rating is a valuation call, the Signals panel is momentum, and the page never says which one the reader is being asked to act on |
| Blended IV −39% against spot | Valuation | the bank block prices book; a 3x tangible-book franchise premium is not in the residual-income fade (Wave 6 open item, owner constants) |

The engine already knows the sector: 118 profiles, each with a method table, and a KPI framework keyed
by profile (`src/data/sector_kpi_framework.py`, 92 profiles: NIM, CET1, RoTE for Money Center Bank;
combined ratio for P&C; VNB for life; backlog for long-cycle; PV-10 for upstream; NRR for SaaS). The
report layer discards that and renders one template.

## 2. The recommendation: one report template per profile family

Key the report on the resolved profile, not the sector string, and let each family declare four things.

### 2a. The family table

| Family (profiles) | Key financials rows | Valuation exposition | Thesis skeleton the writer must follow | Signals that belong |
|---|---|---|---|---|
| **Banks** (Money Center, Super-Regional, Regional, EM, Investment Bank, SG, EU, Card Issuer) | Revenue, net income, total equity, TBV/share, DPS, **RoTE, NIM, CET1, efficiency ratio** | GGM inputs (RoTE, CoE, g, target P/B) against spot P/TBV; residual-income fade; excess capital; the implied-CoE flag when it fires | 1 rating and TSR; 2 where the book multiple sits against the GGM (the "our GGM assumes" line, already required); 3 NII and credit cycle from the KPI framework; 4 capital return; 5 the primary risk | revisions, dividend safety; **not** FCF, **not** net debt |
| **Insurance** (P&C, life) | Premiums, combined ratio (P&C) or VNB and EV/share (life), book value, solvency | P&C: GGM on book and the combined-ratio gate; life: P/EV on the accepted embedded value with the quarantine state named | 1 rating; 2 P/EV or P/B against the return on it; 3 underwriting or new-business trend; 4 capital; 5 risk | reserve releases, rate cycle |
| **Alt asset managers, exchanges, brokers, data** | FRE, DE, fee-paying AUM, carry receivable (alts); revenue, EBITDA margin, FCF (fee platforms) | P/DE on the accepted input (alts); Forward P/E and EV/EBITDA (fee platforms) | 1 rating; 2 the multiple on the earnings the market prices (DE, not GAAP); 3 flows or volumes; 4 operating leverage; 5 risk | flows, volumes |
| **Energy** (upstream, integrated, refining, midstream, OFS, coal) | Revenue, EBITDA, FCF, net debt, **production, realised price, PV-10 or backlog** | EV/EBITDA (norm) with the normalisation window stated; PV-10 coverage; SOTP (segments) for refiners | 1 rating; 2 mid-cycle multiple and where the cycle sits; 3 volume and price; 4 capital return; 5 risk | commodity strip, regime flag |
| **Consumer** (staples, discretionary, retail) | Revenue, EBIT margin, FCF, net debt, **same-store or volume/price mix** | Forward P/E on the NTM basket; DDM at Ke where declared; EV/EBITDA (norm) for cyclicals | 1 rating; 2 forward multiple against the basket; 3 volume/price and margin; 4 payout; 5 risk | pricing, inventory |
| **Health care** (pharma, biotech, devices, services, managed care) | Revenue, EBIT, FCF, **pipeline assets and PTRS (pharma/biotech), MLR (managed care), same-facility volume (providers)** | rNPV state (accepted or quarantined, and what it covers); the Forward P/E sanity gate when it fires; the structural flag on managed care | 1 rating; 2 the anchor and its basis; 3 pipeline or utilisation; 4 pricing and reimbursement; 5 risk | trial readouts, MLR trend |
| **Industrials and A&D** | Revenue, EBIT margin, FCF, net debt, **backlog, book-to-bill** | backlog-coverage DCF with the accepted backlog named; EV/EBIT (norm) | 1 rating; 2 backlog visibility; 3 margin; 4 capital; 5 risk | orders, book-to-bill |
| **Technology, semis, telecom, media** (after the Wave 7 remap) | Revenue growth, gross and FCF margin, SBC, net cash, **NRR (SaaS), NTM growth, capex intensity (telco)** | forward anchor against the live basket; the terminal convergence stated; SBC treatment stated | 1 rating; 2 forward multiple against the basket; 3 growth durability; 4 SBC and capital return; 5 risk | revisions, GIS flag |
| **REITs, property, holdcos** (Wave 8) | NAV, cap rate, occupancy, gearing, DPU | NAV discount; SOTP look-through with the accepted inputs | | |

Each family entry is data, not code: the rows, the exposition fields and the skeleton live beside the
profile in the registry, and the PDF and workbook read them the way they already read `methods`.

### 2b. Narrative fidelity, independent of the family

- **Numbers come from the record.** Every figure the writer may cite is put in an explicit anchors dict
  (blended IV, legs and their inputs, multiples with source, KPI values, consensus, spot, target, gates
  and flags with their numbers). After generation, every number in the rationale is matched against the
  dict with a tolerance; an unmatched number fails the draft and it is rewritten with the offending
  sentence removed. This is the numeric leak guard the LLM Summary already relies on, applied to the
  thesis.
- **The density rule bends to the record.** "At least two figures per theme" becomes "at least two
  figures per theme from the anchors"; a theme with no anchor to cite states the point without a number.
- **Flags are quoted, not paraphrased into findings.** A basis flag ("consensus measured against total
  income, not gross revenue") can be cited as what it is; it cannot become "a consensus claim of +45%".

### 2c. Reconciliation line in the decision block

When revisions or sentiment run against the rating, one fixed sentence: "Momentum signals (revisions
accelerating up, sentiment bullish) run against this valuation-driven rating; the rating is a 12-month
total-return call, not a momentum call." No LLM involvement; the condition is computed.

### 2d. The workbook follows the same key

The workbook already carries profile tabs (Banks, SOTP). The family table adds: Insurance (EV, combined
ratio), Alt manager (DE, FRE, carry), Pipeline (rNPV assets and PTRS), Backlog, Reserves (PV-10), each
populated only when the profile declares the leg, and the Summary's key metrics block reads the
family's rows.

## 3. Build order and size

1. **Narrative-fidelity guard** (2b): the writer's anchors dict and the post-generation number check.
   Smallest change, largest credibility gain; it would have caught all three JPM figures. One module,
   one test file.
2. **Family registry and key-financials rows** (2a rows, 2d Summary block): the balance-sheet financial
   case is done; generalise it to a `report_family` attribute on the profile with a rows list, and have
   both renderers read it. Two renderers, one registry change, a test per family.
3. **Valuation exposition and thesis skeleton** (2a middle columns): the writer receives the family's
   skeleton as the structure of the five themes and the exposition fields as the required "our method
   assumes" line. Prompt and anchors change; the contract test pins the wording per family.
4. **Reconciliation line** (2c): a computed sentence; trivial once 2 exists.
5. **Workbook family tabs** (2d): after the families settle.

## 4. What the owner decides

1. Key the report on the profile family as in 2a, and the family list itself.
2. The narrative-fidelity rule: unmatched numbers fail the draft (recommended), or are flagged in the PDF.
3. Whether the thesis skeleton is mandatory per family or advisory.
4. Build order as in section 3, or the guard alone first.


## 5. Status

- 2026-09-27: owner "proceed with the family list". Built: `src/data/report_families.py` (nine families
  plus the operating-company default; every one of the 121 profile entries maps to exactly one family,
  pinned by `tests/test_report_families.py`); the PDF key-financials block and a "Key metrics (family)"
  block on the workbook Summary read the family's rows from the run's per-year raw financials (derived
  ratios: RoE, EBIT and FCF margin, SBC and capex intensity, net debt to EBITDA and to equity). The
  exposition, skeleton and signals fields are declared per family and not yet consumed (build steps 3
  and 4). Step 1, the narrative-fidelity guard, is not built.

- 2026-09-27 (later): owner "develop industry specific PM agent architecture ... use industry specific
  language", then "use family writer profiles rather than family agents". Built as writer PROFILES on the
  one existing PM agent (`src/agents/pm/industry_pm.py`): (1) desk rules and vocabulary per family
  appended to the system prompt, in the owner's focus order (energy, aerospace and defence, consumer
  staples, health care, technology; banks, insurance, fee financials, property shorter); (2) a
  deterministic family checklist computed from the valuation record (anchor and where it sits, legs
  with their multiples and sources, gates and flags, the cycle or backlog or pipeline state the desk
  leads with) appended to the anchors; (3) the number guard: every number in the draft must appear in
  the inputs the writer was given, one retry naming the offenders, then the sentences that still carry
  them are removed; the verdict is stored as `rationale_fidelity` on the decision. The density rule now
  reads "from the anchors, the family checklist or the research digest supplied". No per-industry
  agent exists. Tests: `tests/test_industry_pm.py`.

- 2026-09-27 (owner "Build"): the remaining items. (3) The family thesis skeleton is in the system prompt
  as the theme order and checked after generation (theme count against the skeleton, theme 1 opening
  with the rating), recorded under `rationale_fidelity.skeleton`. (4) The reconciliation line: when news
  sentiment, analyst revisions or insider activity run against the rating, one computed sentence is
  stored as `signals_reconciliation` and printed under the rating callout in the PDF. (5) The workbook
  carries a Family tab: exposition, skeleton, signals, excluded metrics and the checklist computed from
  the run's valuation record. Every item in section 3 is built.
