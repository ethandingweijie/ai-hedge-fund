# Wave 2 (power & transition): taxonomy and methods, for the owner's confirmation

Drafted 2026-09-21 from the Stage 0 baseline (`docs/waves_2_5_stage0_baseline.md`)
and the live probe (`docs/waves_2_5_stage2_probe.md`). **Nothing here is written
into a profile yet.** Every weight, multiple and rate below is a proposal; the
ones marked ◆ are the owner's to set.

Universe as confirmed 2026-09-21: NEE, DUK, SO, 00002.HK, 00006.HK · VST, CEG,
NRG, 01816.HK · ENPH, FSLR, NXT · CCJ, LEU · BE · GEV, SMR.

---

## 1. Routing (Stage 2)

| FMP industry (measured) | Row today | Proposed row | Reached by pin / override instead |
|---|---|---|---|
| `Regulated Electric` | Energy / Regulated Utility | unchanged, **add to `routing_scope`** | 00006.HK -> Regulated Utility (owner-confirmed; FMP labels it IPP) |
| `Independent Power Producers` | Energy / IPP | unchanged, add to scope | VST (already), **CEG, NRG -> Merchant Power**; 01816.HK pin gets its profile filled: IPP |
| `Solar` | none | **Energy / Solar & Wind Equipment**, add to scope | ENPH and FSLR pins move off IPP; NXT needs none |
| `Uranium` | none | **Energy / Uranium & Nuclear Fuel**, add to scope | LEU pin moves off `Resources / (empty)`; CCJ needs none |
| `Electrical Equipment & Parts`, `Industrial - Machinery` | Industrials / Capital Goods | **unchanged and NOT added to scope** | BE, SMR, GEV by pin only: these labels hold hundreds of unrelated industrials |

Why the IPP row stays on IPP rather than Merchant Power: the label is one string
for two businesses. In HKSE its members are tariff-and-contract generators (CGN
Power, China Resources Power, Huaneng), which is what the IPP profile describes;
in the US its large members are merchant (VST, CEG, NRG, TLN). A row can carry
one profile, so the row keeps the contracted default and the US merchants are
pinned. An unpinned US merchant name would land on IPP, which errs toward the
more conservative contracted-cash-flow method set.

`Renewable Utilities` already routes to IPP and is not in this universe; it
joins `routing_scope` only if the owner adds a name from it.

## 2. What the baseline says has to change

1. **A free-cash-flow DCF cannot anchor a regulated utility.** NEE, DUK and SO
   all drop the 0.60 DCF leg as non-positive. That is not a data problem: a
   utility growing its rate base spends more on capex than it earns in operating
   cash flow, every year, by design, and is paid for it through allowed returns.
   The anchor has to be the thing the regulator actually sets.
2. **`P/Rate Base` is P/BV under another name** (the plan's finding, confirmed:
   `value_key` is `P/BV` on all three).
3. **`Utility P/E` prices EBITDA x EV/EBITDA**, not earnings x P/E.
4. **`LBO Floor` and `LBO Analysis` are uncomputable on every name that carries
   them** (VST, CEG, NRG, 00002.HK, 00006.HK, CCJ, 01816.HK). Dead weight that
   renormalises silently onto the other legs.
5. **Merchant Power and Energy Tech Licensor have no static multiples at all**,
   so a thin or missing basket has nothing to fall back to.

## 3. Proposed method tables ◆

Weights sum to 1.0. Every name is one that already reaches a dispatch branch,
except the two marked *new*, which are this wave's engine work.

### Regulated Utility (re-validated in place) — NEE, DUK, SO, 00002.HK, 00006.HK

| Method | Today | Proposed | Input |
|---|---|---|---|
| **P/Rate Base** *(new real branch)* | 0.20, proxy P/BV | **0.35, anchor** | accepted `rate_base` (amount, equity ratio, allowed ROE) -> `(ROE - g) / (CoE - g)` on the equity layer. Falls to P/BV at the same weight when no figure is accepted, so no weight is lost |
| **P/E** (replacing `Utility P/E`) | 0.15, computed as EV/EBITDA | **0.30** | EPS x peer P/E. The mislabelled leg is removed from `_EV_MULTIPLE_METHODS` |
| DDM | 0.05 | **0.25** | utilities distribute 60-70% of earnings; the dividend is the most stable thing they publish |
| DCF | 0.60, anchor | **0.10** | kept for the rare FCF-positive utility; drops visibly otherwise |

### Merchant Power — VST, CEG, NRG

| Method | Today | Proposed | Note |
|---|---|---|---|
| EV/EBITDA | 0.40 | **0.45, anchor** | the sector's own quoting convention |
| FCF Yield | 0.30 | **0.25** | |
| Forward P/E | - | **0.15** | consensus earnings carry the PTC floor and the data-centre contracts that trailing EBITDA cannot |
| Power Price DCF | 0.20, prose "proxied by DCF" | **0.15**, with a machine-readable `proxy: "DCF"` | a prose note is the failure class at `dcf_agent.py:5442-5449` |
| LBO Floor | 0.10 | **removed** | uncomputable on all three |

### IPP (re-validated in place) — 01816.HK, and any unpinned IPP-labelled name

| Method | Today | Proposed |
|---|---|---|
| PPA-backed DCF | 0.50, prose note | **0.40, anchor**, `proxy: "DCF"` declared |
| EV/EBITDA | 0.15 | **0.35** |
| P/BV (was `NAV (Project)`, proxy P/BV) | 0.30 | **0.15**, named as what it computes |
| DDM | 0.05 | **0.10** |

**01816.HK's negative base IV, traced 2026-09-21.** It is a method-fit defect,
not a bridge bug. EBITDA HK$38.4bn x the HK IPP median 8.34x = EV HK$320bn,
against net debt HK$391bn plus minorities HK$62bn: equity −HK$133bn, floored at
zero. The forward EV/EBITDA cross-check does the same (−HK$104bn). The DCF runs
at a 10.82% WACC with a 0% terminal growth rate, on a government-tariff nuclear
operator, and lands at −3.24. The debt is real but a large part of it funds
reactors under construction that earn no EBITDA yet, so **any EV-based leg
subtracts the cost of assets whose earnings it cannot see**; the market carries
the name at roughly 15-16x EV/EBITDA for exactly that reason. The only leg that
priced was the Forward P/E cross-check, at HK$2.10.

◆ Recommendation: **pin 01816.HK to Regulated Utility, not IPP.** Its on-grid
tariffs are set by the NDRC, and the proposed Regulated Utility table is
equity-based throughout (P/Rate Base falling to P/BV, P/E, DDM), which is the
basis that survives construction-phase debt. On the proposed IPP table only
P/BV and DDM (0.25 of the weight) would price it.

### Solar & Wind Equipment (new; cyclical) — ENPH, FSLR, NXT

| Method | Weight | Note |
|---|---|---|
| **EV/EBITDA (norm)** | **0.35, anchor** | through-cycle margin x current revenue; the dynamic multiple reaches it (US Solar 18.1x through-cycle, market now 13.7x) |
| Forward P/E | 0.25 | |
| DCF | 0.25 | |
| EV/Revenue | 0.15 | qualified by `_qualified_ev_revenue_multiple` |

Joins `_CYCLICAL_PROFILES`: ENPH's margin peaked in 2022-23 and the policy
cycle (IRA credits, tariffs, net-metering) drives the whole basket together.

### Uranium & Nuclear Fuel (new; cyclical) — CCJ, LEU

| Method | Weight | Note |
|---|---|---|
| **EV/EBITDA (norm)** | **0.35, anchor** | |
| **Contracted-backlog DCF** *(new; shares `_backlog_bounded_dcf` with Wave 3)* | **0.30** | accepted `backlog`; runs unbounded, and says so, when none is accepted |
| Forward P/E | 0.20 | |
| P/BV | 0.15 | |

Honest expectation: the US `Uranium` basket has no EV/EBITDA or P/E median at
all (most members are pre-production), so the normalised leg prices on the
profile's static multiple until a basket forms. This profile may well ship
**observation-only**.

### The three names with no label of their own ◆

| Name | Plan said | Recommendation | Why |
|---|---|---|---|
| **BE** | Energy Tech Licensor | **Solar & Wind Equipment** (renamed **Clean Energy Equipment** if BE joins) | Bloom manufactures, sells and services hardware. It licenses nothing, and Energy Tech Licensor carries 80% of its weight on legs that cannot be computed |
| **SMR** | open | **Energy Tech Licensor, shipped unrated** | NuScale is genuinely a design licensor, and pre-revenue: every leg is uncomputable today. Same treatment as Pre-approval Biotech: routed, labelled, not priced |
| **GEV** | open | **stays Capital Goods by explicit pin; observation-only in Wave 2; re-measured in Wave 3** | −75% with nothing dropped and nothing proxied is a method-fit gap: a $100bn+ backlog that trailing multiples cannot see. The backlog-coverage DCF is Wave 3's engine work, and GEV is its natural first non-defence user |

## 4. Static multiples, derived from the market first ◆

Standing rule: no multiple is picked silently. Readings are the local comps
store (refreshed 2026-09-19) and today's through-cycle table. Live comps still
win any field they resolve; these are the fallbacks.

| Profile | Market | Field | Live median | Through-cycle | **Proposed static** |
|---|---|---|---|---|---|
| Regulated Utility | US | EV/EBITDA / P/E / P/B | 12.78x / 19.77x / 2.02x (n=20) | 11.86x / 19.48x | **unchanged: 12.5x / 18.0x / 2.0x** already sit on the market |
| Regulated Utility | HKSE | EV/EBITDA / P/E / P/B | 9.80x / 13.18x / 0.92x | 10.47x / 15.60x | **10.0x / 14.0x / 0.95x** (no HK key exists today) |
| IPP | US | EV/EBITDA / P/B | 12.07x / 2.90x (n=7-9) | 11.05x | 9.0x today is **below both readings**: propose **11.0x**, P/B 1.5x -> **2.0x** |
| IPP | HKSE | EV/EBITDA / P/E / P/B | 8.34x / 7.38x / 0.80x | 8.28x / 10.39x | **8.3x / 9.0x / 0.80x** |
| Merchant Power | US | EV/EBITDA | 12.07x (same basket; market now 18.1x on the normalised basis) | 11.05x | **11.0x**, FCF yield **5.0%** ◆ the basket is mid re-rating on data-centre demand; the through-cycle figure is the defensible fallback |
| Solar & Wind Equipment | US | EV/EBITDA / P/E / EV/Rev | 22.22x / 20.22x / 2.78x (n=5-8) | 18.13x / 22.25x | **18.0x / 21.0x / 2.8x** |
| Solar & Wind Equipment | HKSE | - | no basket (3 members) | - | **none; pools through a family** |
| Uranium & Nuclear Fuel | US | EV/Rev / P/B | 12.79x / 4.08x; **no EV/EBITDA or P/E median** | - | ◆ **owner-set or left empty.** No market reading exists to derive from, and inventing one breaks the standing rule |

SG: no static table, by the standing decision.

## 5. WACC (C2 registry, one row per profile) ◆

Rates are cited per row against Damodaran January 2026 when written, as Wave 1
did. Existing: Regulated Utility 4.5%, IPP 6.0%, Merchant Power 6.5%.

| Profile | Proposed | Basis to cite |
|---|---|---|
| Solar & Wind Equipment | to be read from the file | Damodaran *Green & Renewable Energy* is a generator's rate and too low for a manufacturer; the honest row is *Electrical Equipment* or *Semiconductor Equipment* |
| Uranium & Nuclear Fuel | to be read from the file | *Metals & Mining*, the row Wave 1 used as the Resources rate |
| Energy Tech Licensor | unchanged (flat Energy rate) | unrated, so inert |

## 6. Peer families (Stage 4)

`Solar` and `Uranium` are thin in HKSE (3 and 2 members) and absent in SES.
Proposed `INDUSTRY_FAMILIES` entry: **Power & Transition (family)** = Regulated
Electric, Regulated Gas, Independent Power Producers, Renewable Utilities,
Diversified Utilities, Solar, Uranium — same market only, ranking between
industry and sector, exactly as *Oil, Gas & Coal (family)* does.

One caution for the owner: that family pools equipment makers with generators.
For HKSE solar (Xinyi, GCL) a generator's multiple is a poor stand-in. The
alternative is to leave HK solar on the sector rung and flag it. ◆

## 7. New engine work this implies

1. `P/Rate Base` real branch + `rate_base` kind (the three edits that must land
   together, per the plan), added to `_LOOKTHROUGH_METHODS` so the proxy stays
   requested beside it.
2. `Utility P/E` removed from `_EV_MULTIPLE_METHODS`; goldens re-baselined with
   a named reason if any fixture carries the profile.
3. `_backlog_bounded_dcf` with the contracted-backlog dispatch, joined to
   `_DCF_FAMILY_NAMES`.
4. Two `_PROFILE_WACC["Energy"]` rows, two KPI specs plus the five Wave 1
   backfills (C4), `_CYCLICAL_PROFILES` +2.
5. The 01816.HK negative-IV defect.
