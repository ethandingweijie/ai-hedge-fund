# Delta report: the 2026-09-26 remediation, dry-run across energy, staples, China platforms and financials

Delta_IV = IV_new − IV_old, both on the same day. IV_old comes from the pre-fix engine run from a
worktree at commit `5306d5b` for energy, the China platforms and the financials, and from the morning's
Wave 4 after-measurement for staples. Fixes in the new engine: one equity bridge (minority interest and
preferred deducted on the DCF family and the analyst SOTP as on the EV legs); DDM at cost of equity with
a coverage cap; the owner's zero country premium for China and Hong Kong (the HK sector table's embedded
150bps removed); EV/EBITDAR renamed EV/EBITDA (the airline anchor now joins the mid-cycle swap; no
airline is in this universe); trading vs statement currency in the payload (label only). Owner
instruction: do not tune toward consensus; flag genuine valuation reasons as such.

Raw rows: `docs/baselines/delta_report_2026-09-26.json`; after-runs
`docs/baselines/dryrun_{energy,staples,china_fin}_after_fixes.json`.

| Sector | n | median Delta | within 30% of consensus | within 30% of spot | median IV vs spot |
|---|---|---|---|---|---|
| Energy | 17 | 0% | 8 of 16 | 8 of 17 | −15% |
| Staples | 21 | 0% | 10 of 15 | 13 of 21 | −3% |
| China platforms and financials | 19 | 0% | 2 of 5 | 5 of 19 | +17% |

## Names that moved, and why

| Ticker | IV old → new | Delta | Cause | Classification |
|---|---|---|---|---|
| 00700.HK Tencent | 431.51 → 511.24 | +18% | zero country premium: DCF WACC 11.0% → 9.75% | owner basis |
| 03690.HK Meituan | 112.84 → 146.28 | +30% | zero country premium | owner basis |
| 09988.HK | 117.26 → 139.59 | +19% | zero country premium, net of minority interest | owner basis |
| PDD | 263.25 → 290.14 | +10% | zero country premium | owner basis |
| BABA | 143.95 → 151.94 | +6% | zero country premium, net of minority interest | owner basis |
| 09618.HK | 183.43 → 199.69 | +9% | zero country premium; JD Logistics minority interest partly offsets | owner basis |
| JD | 57.69 → 58.00 | +1% | premium up, minority interest down: net flat | owner basis |
| 00001.HK CK Hutchison | 45.22 → 36.28 | −20% | minority interest on the DCF bridge (the group consolidates large outside stakes) | error fixed |
| 9CI.SI CapitaLand Investment | 1.28 → 0.74 | −42% | DDM now at cost of equity and minority interest deducted; SOTP (published) uncomputable so the legs that moved carry the blend | error fixed, and a profile to review: −72% against spot on two surviving legs |
| WMB | 42.48 → 34.76 | −18% | minority interest on the DCF bridge (consolidated JV interests) | error fixed |
| ET | 31.00 → 27.71 | −11% | minority interest (consolidated subsidiaries) | error fixed |
| KMI | 30.59 → 27.92 | −9% | minority interest | error fixed |
| OKE | 127.86 → 117.53 | −8% | minority interest | error fixed |
| CVX | 87.32 → 82.03 | −6% | minority interest | error fixed |
| XOM | 65.19 → 63.00 | −3% | minority interest | error fixed |
| 00883.HK CNOOC | 30.32 → 31.82 | +5% | zero country premium | owner basis |
| 00168.HK Tsingtao | 73.97 → 60.97 | −18% | zero country premium is not the driver (HKD reporter, Forward P/E anchor); the HK Beverages basket refreshed between the two runs (n=3, thin) | data: thin basket, flag |
| PM | 149.32 → 144.87 | −3% | DDM at cost of equity | error fixed |
| MO | 80.34 → 77.01 | −4% | DDM at cost of equity | error fixed |
| 00682.HK, F34.SI, 00322.HK, 00288.HK | −2% to −5% | | minority interest, small | error fixed |

Golden fixtures (same fixes, replayed on recorded inputs): 02888_HK 258.53 → 256.74 (preferred equity),
FCX 26.78 → 24.31 (Grasberg minority interest), 09988_HK 103.54 → 129.26 and BABA 150.42 → 166.74 (zero
premium, net of minority interest), COST re-recorded on its new profile list. AAPL, BN4.SI, C38U.SI,
D05.SI, MELI, MU, SCHW, U96.SI, V unchanged.

## Deviation from consensus after the fixes, classified

Not tuned; each gap named for what it is.

**Genuine stance, owner basis (China).** PDD +181% and JD +62% against consensus, +274% and +119%
against spot, and Meituan +104% against spot. With the country premium at zero, the DCF anchor prices
domestic cash flows at a 9.75% WACC while the market applies a China discount the engine does not. BABA
is tagged Growth_Inflection_Speculative (consensus 65% above spot) and scored apart. These are the
numbers the owner's basis produces; the multiples legs on these names still read a US Tech basket
(audit item A7, open), which is the one remaining engine question here.

**Genuine stance, through-cycle normalisation (energy).** XOM −61%, CVX −60%, VLO −49%, MPC −41%
against spot on the EV/EBITDA (norm) anchor: mid-cycle EBITDA over a window that includes the 2020-21
trough. Recorded as the refiners' deviation in Wave 1; the integrateds show the same mechanism. The
question for the owner is the normalisation window, not the anchor.

**Genuine stance, re-rated names.** WMB −50% against spot (midstream basket cannot reach a 30x name),
LLY-type premia are outside this universe. BKR −38%.

**Error, now fixed.** The midstream MI deductions, CK Hutchison, CapitaLand Investment, the tobacco DDM
rate. Each moved toward or past the market in the direction the accounting demands, not toward consensus.

**Data gaps, flagged.** 5WH.SI (Rex International) anchor uncomputable, IV +1297% against spot on P/BV
alone: exclude or fix the anchor. 03337.HK +222%, 02883.HK +51% against spot on thin HK oilfield
baskets. 9CI.SI −72% with its published SOTP uncomputable. 00682.HK +442% on P/BV alone. Tsingtao's
thin Beverages basket.

**Profiles that pre-date the waves and now show.** Membership / Subscription Retail (COST −53% vs
consensus on a 48x static), Real Estate Asset Manager (SG) (9CI.SI), Payment Networks (V −26% vs
consensus, −15% vs spot), Brokerage (SCHW −36% / −18%). None was touched today; each is a wave of its
own.

## Baseline management

Golden snapshots regenerated once at the end of the remediation with the reason recorded in
`tests/golden/CHANGELOG.md`; the perturbation check still fails as it must. Tagged `golden-2026-09-26`.
