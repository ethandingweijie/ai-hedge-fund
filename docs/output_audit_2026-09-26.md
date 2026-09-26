# Valuation methods, PDF and Excel: correctness and completeness audit (2026-09-26)

Owner ask: "check that the valuation methods, pdf outputs and excel output is financially correct and
structured. no missing info." Read-only audit by a delegated review pass over `dcf_agent.py` (every
method branch, blend, DCF projection, SOTP leg, equity bridges, 12m targets, payload assembly), both
renderers and the frontend payload types; pure helpers probed in-process; sample PDF and Excel rendered
from the one local archive row with an analyst SOTP (D05.SI, 2026-08-29) plus a synthetic row carrying
the new fields (the archive predates `leg_inputs`, `gate_evaluations`, `pt_bridge`, `rating_state`).
The two HIGH findings and the PDF flags finding were re-verified at source by the session before this
was written. Nothing is fixed yet; every fix below is a proposal.

## A. Correctness defects

| # | Severity | Finding | Where | Mispricing example |
|---|---|---|---|---|
| A1 | HIGH | The payload's currency label is the STATEMENT currency for every non-HK name whose statements are not in its listing currency. Numbers are converted to the listing currency; the label is not. | `dcf_agent.py:14584` (`_output_currency = reported_currency`, HK-only override at 14586); published at 15213 | BABA archive row: IV 133.02 USD/ADS printed as "RMB 133.02"; workbook Cover "Currency CNY". Same for USD-reporting SGX names and any TWD/EUR/JPY ADR. |
| A2 | HIGH | Generic DDM discounts dividends at WACC, not cost of equity, and capitalises the last DPS with no payout check (only a REIT AFFO cap). | `dcf_agent.py:7015-7038` | D05.SI: WACC 7.5% vs bank CoE 8.8%: 2.448/0.055 = S$44.5 vs 2.448/0.068 = S$36.0, +24%. Levered insurer: +80%. Profiles carrying DDM: RE Asset Manager (SG) .25, Mortgage/GSE .15, Insurance (P&C) .15, Holding Company .10, Insurance .05, and now Tobacco .20. The S-REIT DDM correctly uses CoE. |
| A3 | MEDIUM | Minority interest is deducted on the EV-multiple legs but not in the DCF bridge or the analyst SOTP NAV. One company, two equity bridges. | `_ev_to_equity_ps` 5592-5623 vs `_project_dcf` 3787 and `_sotp_analyst_style` 3008 | EV 10bn, ND 2bn, MI 1bn, 100m sh: multiples leg 70/sh, DCF leg 80/sh. BABA MI ~US$16.5bn ~ $6.8/ADS never deducted from SOTP or DCF. |
| A4 | MEDIUM | Forward P/E and Forward EV/EBITDA pair an NTM metric with a TRAILING peer multiple unless `NTM_FORWARD_MULTIPLES_ENABLED` is on (owner measured and left OFF, 2026-09-21). | `dcf_agent.py:6597, 6620, 7587-7610` | Basket EPS growth 15%: trailing 20x on NTM EPS $5 = $100 vs like-for-like $87, +15%, on a 0.30-0.40 leg. The trace labels the basis. **Now the anchor on Food & Beverage, Household, Grocery & Discount Retail and Tobacco (Wave 4).** |
| A5 | MEDIUM | "EV/EBITDAR" is EV/EBITDA under another name: no rent add-back, no lease-liability deduction, EBITDA metric, `ev_ebitda` multiple. Anchor at .35 (Grocery & Discount Retail), .40 (Traditional Retail), .50 (Airlines). | `dcf_agent.py:5691, 6018` | Airline: EBITDA 1.0bn, rent 0.6bn, ND 3.0bn, 500m sh, peer 6.0x EBITDAR: engine $6.00 vs like-for-like $4.80. Direction unpredictable when IFRS-16 peers mix with a US-GAAP target. |
| A6 | MEDIUM (verify) | Target net debt nets short-term investments; peer EV from FMP `enterpriseValueTTM` may net cash only. Cash-rich names lifted on every EV leg. | `_net_debt_net_of_investments` 952-978; `regional_comps.py:577` | BABA STI RMB 184.7bn ~ +$10.5/ADS if the FMP definition is cash-only. Needs the FMP definition confirmed. |
| A7 | MEDIUM | `cn_adr_haircut` (x0.40) keys on statement currency CNY and exists only on the US Consumer static row: a CNY Consumer ADR is cut 60% on every multiple leg; its HK line and non-Consumer Chinese ADRs are not. | `sector_profiles.py:3384`; applied at ten sites | Inconsistent across listings of one company. |
| A8 | LOW | Flat 21% tax on EPV and ROIC vs WACC for every market. | `_EFFECTIVE_TAX_RATE` 674 | HK 16.5%: EV −5.4%; China 25%: +5%. |
| A9 | LOW | Residual Income (non-bank) and ROE vs CoE use WACC as the cost of equity; RI carries an undocumented 0.5 haircut. | 7140, 7231 | |
| A10 | LOW | 12m-target ordering only flagged, not repaired, when spot is missing. | 14557-14571 | Bites only when the spot fetch fails. |
| A11 | LOW | PDF FX header prints `reported_currency→USD @ rate` for what is a source→reporting rate. | `pdf_report.py:1321` | "SGD→USD @ 1.0000" on D05.SI. |
| A12 | LOW (verify) | A cited SOTP net cash that includes listed stakes may double-count stakes also in `associates_investments`. | `_refresh_sotp_net_cash` | Depends on the accepted inputs. |

Also disclosed by name, not defects: `Rev DCF (Target Margin)` uses after-tax EBIT margin as FCF margin
(no D&A/capex/WC); PEG falls back to revenue growth when only one forward EPS year exists.

## B. Structural and completeness gaps

### PDF
1. **Forward flags never print.** `_section_2f` reads `dcf_data["forward_flags"]` (:1289) but flags live under each scenario (`dcf_t["base"]["forward_flags"]`). Verified on all three samples: no PEG OWNER_OVERRIDE_PENDING, no net-cash variance flag, no SOTP precedence or cross-check spread, no Degraded-SOTP disclosure, no dropped-leg or inversion flag reaches the PDF.
2. **Degraded SOTP is silent** (block returns `[]` when per-share is None; the reason exists only as a flag, see 1). Half of the design decision is met (no per-share printed), half missed (reason not surfaced).
3. **Unrated**: page-1 banner correct; but the decision block, decision section and valuation summary print `price_target`, `blended_iv` and "Target derivation not recorded for this run" without consulting `rating_state`. `rating_state.note` and `indicative_iv` never shown.
4. **Weights column shows intended profile weights, not effective ones**; no `legs_dropped`, `weight_surviving`, `single_method`.
5. No gate ledger, no calibration detail beyond PASS/MISS, no per-method inputs (multiple, base metric, basis, ND, MI), no PEG override, no `consensus_pt`, no `12m_pt_method`, no static-vs-live provenance.
6. Analyst SOTP block hard-codes "(USD)" headers and never prints `fx_to_reporting`, so the bridge to the per-share figure cannot be rebuilt from the page.
7. Wrong currency symbol for ADRs (A1); wrong FX header (A11).
8. Shadow methods render under "Cross-checks" but are not labelled as profile shadows and carry no spread.

### Excel
1. **No `rating_state` handling**: an Unrated name publishes the rebuilt blend as the headline IV and links a 12-month target; no banner; Target tab says "Run predates the unified target rule".
2. **Degraded SOTP**: no SOTP tab (built only from a `kind=="sotp"` trace, which a degraded table never emits); reason only in Backtest flags.
3. `legs_dropped`, `weight_surviving`, intended weights, `single_method` not shown; the renormalisation is invisible.
4. Gate ledger drops `basis`, `breaches`, `replaced`, `band`, `conflicts`, `ordering_composition`.
5. **Margin-schedule DCF legs cannot be rebuilt** and can be mis-linked: `_dcf_block` writes a flat margin formula, and `_same_projection` (:189-206) does not compare `fcf_margin`, so a `Rev DCF (Target Margin)` or backlog-fade leg sharing the core growth path is linked to the core DCF cell and shows the wrong value with no check.
6. `SOTP (segments)` trace carries `segments`, not `table`: the SOTP tab renders an empty block for it.
7. Not shown: `sotp_breakdown` extras (sentence, sources, ground truth, multiple basis, forward estimates), `12m_pt_method`, `consensus_pt`, `fx_note`, `routing_trace`, `methods_unavailable`, `profile_fallback_used`, `shares_source`, the unclamped IV value, the input audits under `leg_inputs`.
8. Cover currency and the Assumptions FX line inherit A1.

### Frontend (reference)
`reportTypes.ts` does not type `leg_inputs`, `legs_dropped`, `gate_evaluations`, `pt_bridge`, `calibration_record`, `weight_surviving`: those fields are invisible on all three surfaces.

## C. Verified correct
Blend renormalisation and proxy resolution; EV-to-equity bridge sign and floors; net-debt sign
everywhere; FCF Yield inversion and refusal threshold; the Degraded SOTP contract (probe); cited vs
restated net cash with the 20% variance flag and an exact FX round trip; scenario ordering clamp with
its gate record; the unified 12m-target rule, band and `pt_bridge`; PEG fair-P/E and the override
sensitivity; shadow methods unweighted with both gate records; SOTP FX and dual-listing share of one
company value; HK output currency; Excel Owner-overrides-pending block, gate ledger, SOTP rebuild with
FX row, Blend effective weights with check, Target formula with check, full DCF rebuild; PDF analyst
SOTP block, segment SOTP block, Valuation Summary from `pt_bridge`, cross-check rows, Unrated banner.

## D. Proposed fix order (owner to choose; several move the golden baseline)

1. **A1** one line: `_output_currency = _target_ccy` after the FX block; labels correct themselves. No valuation change.
2. **PDF flags** (B-PDF-1): read the base scenario's flags; add a flags-and-gates block. No valuation change.
3. **Unrated gating** in both renderers; **Degraded SOTP** published in the payload (rows + reason) and rendered. No valuation change.
4. **A2** DDM at `cost_of_equity(profile, market)` with a payout sanity and a `kind="ddm"` trace. Moves every DDM-carrying profile (incl. Wave 4 Tobacco) down; golden re-base with reason.
5. **A3** minority interest in the DCF bridge and SOTP NAV. Moves every DCF; golden re-base; largest owner decision here.
6. **A5** implement EBITDAR properly or rename the rows "EV/EBITDA (lease proxy)". Owner decision.
7. **Excel B5** margin-schedule rebuild and `_same_projection` on `fcf_margin`; **B6** segments tab; disclosure block (intended vs effective, dropped, surviving); gate ledger columns.
8. A4 stays as the owner decided (flag OFF) unless revisited; A6/A7/A8/A9 owner constants after verification.
