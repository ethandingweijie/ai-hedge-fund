# Wave 3 (aerospace & defence): taxonomy, routing, methods and peers, for the owner's confirmation

Drafted 2026-09-22 from the Stage 0 baseline (`docs/wave3_stage0_baseline.md`)
and the live probe of 2026-09-21 (`docs/waves_2_5_stage2_probe.md`). **Nothing
here is written into a profile yet.** Items marked ◆ are the owner's to set.

---

## 1. Routing (Stage 2): one label, three profiles

Every US and HK name in the universe returns the single FMP label
**`Aerospace & Defense`**. A row can carry one profile, so the split is a
default row plus ticker pins. The label is not in `routing_scope` today, which
is why NOC, GD, HWM, TDG, LHX, KTOS, AVAV and RKLB are valued as software.

| Question | Proposal ◆ |
|---|---|
| Default row for `Aerospace & Defense` | **Defense Primes.** It is the most conservative of the three (trailing legs plus a contracted-backlog DCF), so an unpinned newcomer errs toward the cheaper method set; it is also the group closest to consensus on the baseline. |
| Pins | Primes: LMT, NOC, GD, RTX, LHX · Commercial: BA, GE, HWM, TDG · Defense tech & space: KTOS, AVAV, RKLB |
| Retire `Industrials/Aerospace & Defense` | Yes, in the same commit: re-pin LMT/RTX/BA/GE, and a test that no row, pin or classifier path reaches the retired name. |
| S63.SI | Stays on `Aerospace & Engineering (SG)` -- but by the SG row, not the LLM router: `Aerospace & Defense` in scope makes it deterministic. |
| S59.SI | `Airlines, Airports & Air Services` maps to `Transportation/Airlines` in the default table (US carriers), so the label must NOT join scope. Route S59.SI by `routing_scope_tickers` (the RXS.SI precedent) to `Aviation & Marine (SG)`. |
| Hong Kong | **Drop 00232.HK** (Continental Aerospace Technologies: piston engines, loss-making; the GICS table's code was wrong). AviChina is **02357.HK** and takes the Primes default. ◆ Whether to add 00317.HK (CSSC Offshore & Marine, naval yards) as a second HK name. |

RTX is placed with the primes despite Pratt & Whitney and Collins: its
Raytheon segment and its multiple (18x EV/EBITDA, between the prime and the
aftermarket clusters) put it closer to the primes' method set. ◆

## 2. Why the shared basket cannot price the split (Stage 4)

The `Aerospace & Defense` industry basket is one median over three businesses.
Measured on the 2026-09-21 refresh, US, by cluster:

| Cluster (members) | EV/EBITDA | NTM | P/E | NTM | EV/Rev | P/B |
|---|---|---|---|---|---|---|
| **Whole basket median** (18-19) | 23.0x | 21.7x | 36.3x | 25.7x | 4.8x | 7.9x |
| Defense Primes (LMT NOC GD LHX RTX TXT ESLT) | **15.0x** | 13.0x | **21.3x** | 19.0x | 2.1x | 4.0x |
| Commercial Aero & Aftermarket (GE BA HWM TDG HEI HONA CW WWD FTAI EMBJ) | **27.2x** | 23.1x | **40.2x** | 34.4x | 7.0x | 8.5x |
| Defense Tech & Space (AXON RKLB SPCX) | 85x (1) | 51x (2) | 181x (1) | 46x (1) | 30.7x (2) | 9.8x |

The whole-basket 23x describes none of them. A prime priced on it is 50%
over its own cluster; an aftermarket name is 15% under. So Stage 4 for this
wave is not a family pool (the basket is too broad, not too thin) but the
opposite: **a profile-scoped peer basket** -- the industry members filtered to
a named list, median taken the same way, peer floor of 5 applied -- ranking
ABOVE the industry rung. Today's layering has no such rung: the US curated
`SECTOR_PEER_BASKETS` exists but the regional industry median outranks it.
This is the wave's comps work, and a design the later waves reuse (staples
retail vs discounters share labels the same way).

Two data notes. FMP places **SpaceX** (SPCX, "$2.05tn") in the US basket; its
multiples are out of band and drop, but it is a member. **HKSE** carries 8 A&D
names of which 5 clear the EV/EBITDA band and none the P/E band; the HK cluster
is thin whatever is done, and 02357.HK will price on the sector rung for P/E.

The owner's bands, checked against the market ◆: US primes trade at **21x
trailing / 19x NTM P/E**, below the 25-30x band the GICS table gave; the
14-18x EV/EBITDA band holds (15.0x). Commercial aero at 27x sits above every
band. Propose the static and the C1 plausibility band per NEW profile, not per
the retired parent.

## 3. Method tables ◆

Weights sum to 1.0; every name reaches a dispatch branch. `Backlog-coverage
DCF` and `Contracted-backlog DCF` exist since 97cbc7f; `EV/Backlog` does not
yet and is the wave's second engine item.

### Defense Primes -- LMT, NOC, GD, RTX, LHX, 02357.HK

| Method | Weight | Note |
|---|---|---|
| **Backlog-coverage DCF** | 0.30, anchor | funded backlog bounds years 1-3; book-to-bill caps the ramp. Unbounded (= core DCF) until a backlog is accepted, and says so |
| EV/EBITDA | 0.25 | on the Primes basket |
| FCF Yield | 0.20 | progress-payment cash conversion |
| P/E | 0.15 | |
| **EV/EBITDA (norm)** | 0.10 | the handoff's coverage decision: through-cycle margin x current revenue, the dynamic multiple reaches it |

### Commercial Aerospace & Aftermarket -- BA, GE, HWM, TDG

| Method | Weight | Note |
|---|---|---|
| **EV/EBITDA (norm)** | 0.35, anchor | "normalised for delivery disruption" IS the through-cycle margin: BA's trailing EBITDA is a strike-and-grounding trough, GE's is a post-spin peak |
| Forward EV/EBITDA | 0.25 | consensus carries the delivery ramp |
| Forward P/E | 0.20 | |
| FCF Yield | 0.20 | on trailing cash; BA drops it (negative FCF) and says so |

Cyclical (`_CYCLICAL_PROFILES`) and convergence-faded: the delivery cycle is
the definition of the mean-reversion premise.

### Defense Tech & Space -- KTOS, AVAV, RKLB

| Method | Weight | Note |
|---|---|---|
| EV/Fwd Rev (growth-adjusted) | 0.40, anchor | `_qualified_ev_revenue_multiple` already gates the multiple |
| Rev DCF | 0.35 | revenue-path DCF with a margin ramp; the profile's `fcf_floor` must allow negative near years |
| **EV/Backlog** | 0.25 | owner-set constant, never derived from EV/Revenue (the identity trap); declines to price without one |

AVAV is Unrated today on this method set's absence, and RKLB runs on a TAM
proxy. With three computable legs both are rated; whether the values are
sane is what Stage 6 measures. EV/Backlog ◆: **the constant.** Defence-tech
backlogs convert over 2-3 years; a starting point is the cluster's EV/Revenue
(30x on two names) divided by coverage, but that is exactly the derivation the
plan forbids, so it needs an owner figure.

### Aerospace & Engineering (SG), Aviation & Marine (SG)

Unchanged; both already carry a Forward P/E leg and clear their SG bands.

## 4. WACC (C2 registry, one row per profile) ◆

Damodaran January 2026 (file read 2026-09-21): **Aerospace/Defense 7.24%**,
D/(D+E) 12.6%. Proposal: Defense Primes **7.2%**; Commercial Aero **7.5%**
(same row, plus the OEM leverage BA carries: D/(D+E) 45%); Defense Tech &
Space **9.0%** (Electrical Equipment 8.99%, the nearest row for a pre-profit
hardware maker; no Damodaran space row exists).

## 5. Inputs (Stage 5)

Backlog via the existing kind, which now carries `kind` (funded/total/RPO),
`book_to_bill` and `orders`. `_BACKLOG_VISIBILITY_PROFILES` gains the three
profiles so the bear-only floor applies too. WAVE3 list for
`build_industry_inputs.py`: backlog for LMT NOC GD RTX LHX BA GE HWM TDG KTOS
AVAV RKLB 02357.HK S63.SI; FCF guidance for none (no name guides FCF the way
GEV does; BA's is a cash-burn guide and would fail the positive check).

## 6. Engine work this implies

1. Profile-scoped peer baskets in `regional_comps` / `get_sector_peer_multiples`
   (a new rung above industry), with the basket recorded in `multiples_used`.
2. `EV/Backlog` branch reading `accepted_detail(ticker, "backlog")` and an
   owner-set `ev_backlog` constant per profile in `valuation_constants.json`.
3. Three profiles, the retired parent, pins, `routing_scope` + `_doc` bump
   (v16), `routing_scope_tickers` += S59.SI, `_CYCLICAL_PROFILES` +1,
   `_CONVERGENCE_ALPHA_PROFILES` +1, three C2 rows, three KPI specs (C4),
   C1 bands and statics per new profile.
4. `tests/test_aerospace_defense_wave3.py` on the Wave 2 pattern.

## 7. Universe as measured

US: LMT NOC GD RTX LHX · BA GE HWM TDG · KTOS AVAV RKLB. SG: S63.SI, S59.SI.
HK: 02357.HK (+ 00317.HK ◆). 00232.HK dropped.
