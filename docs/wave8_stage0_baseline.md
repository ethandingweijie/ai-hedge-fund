# Wave 8 (Real estate): Stage 0 baseline

Run 2026-09-27 on the engine at 9ab56782 over the 34 names of the Stage 2 probe
(`docs/wave8_stage2_probe.md`), read-only against the local store; recorded in
`docs/baselines/baseline_wave8.json`. None of the thirteen real-estate labels is in `routing_scope`: nine
Singapore names are pre-classified by the strategic router (the SGX profile hints), PSA by pin, and the
other 24 by the ratio ladder. Only the twelve US names carry a usable consensus.

| Ticker | Routed profile today | Anchor (in blend) | Base IV | Spot | Consensus | IV vs cons | IV vs spot | Surviving w | Backtest |
|---|---|---|---|---|---|---|---|---|---|
| WELL | REIT | NAV (Cap Rates) (yes) | 45.63 | 231.21 | 258.08 | -82% | -80% | 1.0 | fired / MARKET_CLOSER |
| PLD | REIT | NAV (Cap Rates) (yes) | 109.45 | 133.03 | 155.15 | -29% | -18% | 1.0 | fired / MARKET_CLOSER |
| EQIX | REIT | NAV (Cap Rates) (yes) | 719.32 | 1,008.08 | 1,246.25 | -42% | -29% | 1.0 | fired / MARKET_CLOSER |
| AMT | REIT | NAV (Cap Rates) (yes) | 98.51 | 169.03 | 209.89 | -53% | -42% | 1.0 | fired / MARKET_CLOSER |
| DLR | REIT | NAV (Cap Rates) (yes) | 167.86 | 178.61 | 222.35 | -25% | -6% | 1.0 | fired / MARKET_CLOSER |
| SPG | REIT | NAV (Cap Rates) (yes) | 276.41 | 204.82 | 223.60 | +24% | +35% | 1.0 | passed / TIE |
| PSA | REIT | NAV (Cap Rates) (yes) | 257.32 | 287.97 | 328.33 | -22% | -11% | 1.0 | passed / MODEL_CLOSER |
| O | REIT | NAV (Cap Rates) (yes) | 66.40 | 55.54 | 65.28 | +2% | +20% | 1.0 | fired / MARKET_CLOSER |
| VTR | REIT | NAV (Cap Rates) (yes) | 47.46 | 86.64 | 99.93 | -53% | -45% | 1.0 | fired / MARKET_CLOSER |
| CBRE | Mature SaaS | Forward P/E (yes) | 128.74 | 134.74 | 179.00 | -28% | -4% | 1.0 | fired / MARKET_CLOSER |
| DHI | Mature SaaS | Forward P/E (yes) | 148.34 | 141.51 | 159.33 | -7% | +5% | 1.0 | passed / MODEL_CLOSER |
| IRM | Mature SaaS | Forward P/E (yes) | 43.25 | 111.38 | 142.75 | -70% | -61% | 0.55 | fired / MARKET_CLOSER |
| 00016.HK | REIT | NAV (Cap Rates) (yes) | 148.70 | 107.10 | — | — | +39% | 1.0 | passed / MODEL_CLOSER |
| 01109.HK | Mature SaaS | Forward P/E (yes) | 55.34 | 28.64 | — | — | +93% | 0.7 | fired / MARKET_CLOSER |
| 01113.HK | REIT | NAV (Cap Rates) (yes) | 68.54 | 46.00 | — | — | +49% | 1.0 | fired / MARKET_CLOSER |
| 01972.HK | Mature Platform | DCF (FCF+) (yes) | 5.07 | 24.32 | — | — | -79% | 0.45 | skipped / UNSCORABLE |
| 00688.HK | REIT | NAV (Cap Rates) (yes) | 23.02 | 12.42 | — | — | +85% | 1.0 | fired / MARKET_CLOSER |
| 00012.HK | REIT | NAV (Cap Rates) (NO) | 16.65 | 26.22 | — | — | -36% | 0.5 | fired / MARKET_CLOSER |
| 00823.HK | Mature Platform | DCF (FCF+) (NO) | — | — | — | — | — | 0.0 | skipped / UNSCORABLE |
| 00083.HK | Mature Platform | DCF (FCF+) (yes) | 9.90 | 9.94 | — | — | -0% | 0.9 | fired / MARKET_CLOSER |
| 00004.HK | Mature Platform | DCF (FCF+) (yes) | 12.29 | 19.50 | — | — | -37% | 0.9 | skipped / UNSCORABLE |
| 02202.HK | Mature SaaS | Forward P/E (NO) | — | — | — | — | — | 0.0 | skipped / UNSCORABLE |
| 00960.HK | Mature Platform | DCF (FCF+) (NO) | — | — | — | — | — | 0.15 | fired / MARKET_CLOSER |
| H78.SI | Property Developer (SG) | NAV (yes) | 7.73 | 8.57 | — | — | -10% | 0.8 | fired / MARKET_CLOSER |
| C38U.SI | S-REIT | DDM (S-REIT) (yes) | 1.80 | 2.24 | — | — | -20% | 1.0 | passed / MODEL_CLOSER |
| 9CI.SI | Real Estate Asset Manager (SG) | P/E (norm) (yes) | 0.74 | 2.60 | — | — | -72% | 0.65 | fired / MARKET_CLOSER |
| A17U.SI | S-REIT | DDM (S-REIT) (yes) | 2.67 | 2.28 | — | — | +17% | 1.0 | passed / MODEL_CLOSER |
| C09.SI | Property Developer (SG) | NAV (NO) | 2.98 | 8.26 | — | — | -64% | 0.45 | fired / MARKET_CLOSER |
| U14.SI | Property Developer (SG) | NAV (yes) | 9.91 | 8.46 | — | — | +17% | 1.0 | passed / MODEL_CLOSER |
| N2IU.SI | S-REIT | DDM (S-REIT) (yes) | 0.86 | 1.22 | — | — | -30% | 1.0 | fired / MARKET_CLOSER |
| M44U.SI | S-REIT | DDM (S-REIT) (yes) | 1.25 | 1.10 | — | — | +14% | 1.0 | passed / TIE |
| ME8U.SI | S-REIT | DDM (S-REIT) (yes) | 1.98 | 1.86 | — | — | +6% | 1.0 | fired / MARKET_CLOSER |
| AJBU.SI | S-REIT | DDM (S-REIT) (yes) | 2.82 | 2.12 | — | — | +33% | 1.0 | passed / TIE |
| U06.SI | Mature Platform | DCF (FCF+) (yes) | 3.41 | 3.13 | — | — | +9% | 0.9 | passed / MODEL_CLOSER |

| Measure | Stage 0 |
|---|---|
| Within ±30% of consensus | 7 of 12 |
| Within ±30% of spot | 16 of 31 |
| Methodology backtest passed | 10 of 34 |
| Anchor missing from blend | 5 (00012.HK, 00823.HK, 02202.HK, 00960.HK, C09.SI) |
| No intrinsic value at all | 3 (Link REIT, Vanke, Longfor) |

## What the baseline says

**Four structural causes, measured on the per-leg traces (`docs/baselines/w8_legs.log`, thirteen names).**

**1. Nothing routes.** With no label in scope, 24 of 34 names are placed by the ratio ladder: nine US
REITs and four Hong Kong developers land on REIT, three US names and two China developers on Mature SaaS
(D.R. Horton and CBRE now take the Mature SaaS *basket* multiples Wave 7 installed, 15.3x NTM), and six
Hong Kong and Singapore property companies on Mature Platform, whose DCF (FCF+) has nothing to work with
on a landlord (Swire −79%, Wharf −37%) and no legs at all on Link REIT. China Resources Land at +93% is a
developer on a software forward P/E. Only Singapore routes, and Singapore Land (U06.SI) falls through even
there: it has no router pre-classification and its `Real Estate - Diversified` market-map row is out of
scope.

**2. The REIT legs never read the market.** The four REIT legs take the cap rate and the P/FFO and P/AFFO
multiples from `_REIT_SUBTYPE_MULTIPLES`, a 2026-04 table keyed by a keyword classifier; the live cohort is
fetched (`multiples_used` shows `REIT - Healthcare Facilities`, `REIT - Specialty`) and ignored. American
Tower matches the word "tower" in the `office` bucket and is capitalised at 7.5% (NAV 82 against a spot of
169, −42%); Welltower is `healthcare` at 6.0% and 15x FFO while the market holds it at 61x EBITDA and 73x
NTM earnings (−80%); Ventas −45%; Equinix at the table's tightest 4.5% still lands −29% because the market
pays about 30x EBITDA (3.3% implied). Simon at +35% is the reverse: the retail cap of 6.2% is tighter than
the 6.9% the retail cohort implies. Read off the store as the cohort's median EBITDA yield (the engine's
own NOI definition), the market's implied cap rates today are: healthcare large 5.1% (table 6.0%),
industrial large 5.4% (5.5%), specialty large 5.1% (AMT reads 7.5%, EQIX 4.5%), retail 6.9% (6.2%),
office 6.5% (7.5%), residential 6.4% (5.5%), hotel 9.1% (8.0%). The Wave 6 bank calibration and the Wave 7
tech table were the same finding: a basis nobody re-derived.

**3. Developers are capitalised as if they were rent.** `NAV = NOI / cap_rate − debt + cash` with NOI ≈
EBITDA takes Sun Hung Kai's and CK Asset's development profit at a 6.5% cap rate: +39% and +49% against
spot, China Overseas Land +85%, while the HKSE development cohort trades at 0.40x book, 12.8x NTM earnings
and an 8.5% implied EBITDA yield. Henderson Land's NAV is uncomputable (anchor missing, −36% on the rest);
Vanke (two loss years, RMB −49.5bn and −88.6bn) and Longfor (profit down 92% in two years to RMB 1.0bn,
revenue down 46%) produce no value on the ladder profiles they land on. The RNAV-discount pattern that Property
Developer (SG) was built for has no Hong Kong or China profile to live in, and the Singapore "NAV" is the
same cap-rate formula (City Developments: uncomputable, −64% on DDM and P/E alone).

**4. Singapore holds, with two exceptions.** Five of six S-REITs land within ±30% on the published-broker
DDM (C38U −20%, A17U +17%, M44U +14%, ME8U +6%, N2IU −30%; Keppel DC +33%), UOL +17% and Hongkong Land
−10% on the developer profile. CapitaLand Investment at −72% is a fund manager whose SOTP (published) leg
is uncomputable, so P/E (norm) on depressed normalised earnings and a DDM carry it; the `alt_manager` kind
Wave 6 built is the input it needs. Iron Mountain at −61% on Mature SaaS is a records-storage REIT the
market prices at 30x FFO.

**Where it lands.** O +2% against consensus, DHI −7%, PSA −22%, DLR −25%, SPG +24%, CBRE −28%, PLD −29%:
the names that land are the ones whose table cap rate happens to sit near the market's, or that reached a
generic forward P/E close to their own cohort's by coincidence.

## What Stage 1 (universe, ◆ owner) has to settle

The four questions in `docs/wave8_stage2_probe.md` section 4; the remap, the market-derived cap-rate table
and the method tables are proposed in `docs/wave8_stage3_remap_proposal.md`, all PROPOSED, nothing applied.
