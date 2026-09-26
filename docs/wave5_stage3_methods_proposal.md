# Wave 5 (Health Care): taxonomy, routing, methods and the pipeline input, as built

Owner decisions, 2026-09-26: the Commercial Biotech profile specification (Forward P/E .35, EV/Forward
Revenue .25, DCF .25, rNPV (Pipeline) .15); the rNPV leg is fed by a review-gated pipeline pre-fill and
stays quarantined until accepted, with the blend re-weighting to 1.0 when it is absent; MOH joins UNH and
CVS on Managed Care; HCA activates the US provider profile. Universe 17: the 16 probe names less VEEV
(health-care software, labelled Technology, stays on Tech) plus MOH and HCA.

Multiples read from the US baskets on 2026-09-26.

## 1. Why Stage 0 missed

None of the seven health labels was in `routing_scope`, so 12 of 16 names were placed by the ratio
ladder: Vertex on MedTech / Devices, Medtronic on Large Cap Pharma, Baxter on Mature SaaS with no
anchor, MicroPort on Pre-approval Biotech producing no value. LLY's −56% against spot was not the
empty pipeline leg dragging (it is dropped and the rest re-weighted to 1.0) but a trailing P/E anchor at
57% of the blend on a name the market prices on forward growth.

## 2. Taxonomy and routing (Stage 2)

| FMP label | Row -> profile | Names | US basket |
|---|---|---|---|
| `Drug Manufacturers - General` | Biopharma / Large Cap Pharma | LLY, PFE, AMGN, 01093.HK (pinned; FMP labels CSPC Biotechnology) | n14-16 |
| `Biotechnology` | Biopharma / Pre-approval Biotech (default row) | none of the 17 by row; VRTX and 01801.HK pinned to **Commercial Biotech (new)** | n13-17 |
| `Medical - Devices` (new row) | Biopharma / MedTech / Devices | MDT, SYK, BAX (pinned), 00853.HK (pinned) | n18-20 |
| `Medical - Instruments & Supplies` | Biopharma / MedTech / Devices | (existing row) | n12-19 |
| `Medical - Healthcare Plans` (new row) | HealthcareServices / Managed Care | UNH, CVS, MOH | n8-11 |
| `Medical - Care Facilities` | HealthcareServices / Healthcare Providers / Services; SG market row -> Healthcare Provider (SG) | HCA; BSL.SI | n20 |
| `Medical - Diagnostics & Research` | Biopharma / CDMO / Life Science Tools | TMO, 02269.HK (pinned; labelled Biotechnology) | n16-18 |

## 3. Method tables

### Commercial Biotech (new, owner spec) -- VRTX, 01801.HK

| Method | Weight | Anchor | Derivation |
|---|---|---|---|
| Forward P/E | 0.35 | yes | US Biotechnology 17.2x NTM (n13) against 20.3x trailing |
| EV/Fwd Rev | 0.25 | | 6.2x EV/Revenue (n17); normalises early-launch profitability |
| DCF | 0.25 | | long stage-2 growth; patent durability and cash conversion |
| rNPV (Pipeline) | 0.15 | | from the accepted pipeline input only; quarantined until accepted |

Excluded: EV/R&D, Cash Runway (a self-funding company has outgrown both), P/BV, EPV. rNPV commercial
defaults: 42% peak operating margin, 18% tax.

### Large Cap Pharma (existing) -- LLY, PFE, AMGN, 01093.HK

Unchanged: P/E .40 anchor, rNPV (Pipeline) .30, DCF .20, EV/EBITDA .10. The pipeline leg now reads the
accepted input only. Open question for the owner: the trailing P/E anchor against 14.7x NTM on the
Drug Manufacturers basket (25.1x trailing); a Forward P/E anchor would follow the Wave 4 pattern.

### MedTech / Devices (existing) -- MDT, SYK, BAX, 00853.HK

Unchanged: EV/Revenue .40 anchor, DCF (5-yr) .30, P/E .20, ROIC vs WACC .10. Basket: Devices 3.5x
EV/Revenue, 17.1x NTM P/E.

### Managed Care, CDMO / Life Science Tools, Healthcare Providers / Services, Healthcare Provider (SG)

Unchanged; Managed Care and CDMO already priced their names within the band at Stage 0.

## 4. The pipeline input (Stage 5, owner spec)

Kind `pipeline` in the review-gated store. Gemini lists late-stage assets (Phase 2, Phase 3, filed,
approved pre-peak) with indication, cited consensus peak sales, launch year, loss-of-exclusivity year and
a cited PTRS or the therapeutic-area benchmark for the phase. The bridge turns each into the asset dict
the rNPV leg consumes (`peak_sales_usd`, `phase`, `launch_year`, `ptrs_override`); an uncited or
out-of-range PTRS is ignored and the phase table prices it. Checks on the gate: summed peak sales against
revenue (0.02x to 8x), late-stage phases only, launch years 2024-2040, PTRS cited and in range, assets
with cited peak sales, canonical URLs.

Engine rule: the rNPV leg reads the accepted input first; the extractor's research assets price only a
Pre-approval Biotech, whose research is the only source it has; a revenue name with nothing accepted has
the leg quarantined (None), the blend re-weights the rest to 1.0, and the base scenario carries the
quarantine flag naming how many extractor assets were held.

Pre-fill built for LLY, AMGN, PFE, VRTX, 01801.HK, 01093.HK; all pending.

## 4b. The owner's full taxonomy (Stage 1 expansion, 2026-09-26)

After the 17-name build the owner set the seven-profile universe across US, HK and SG. Applied as
pins; two corrections flagged and taken, one skip.

| Profile | US | HK | SG |
|---|---|---|---|
| Large Cap Pharma | JNJ ABBV MRK BMY (+LLY PFE AMGN) | 01177.HK 03692.HK 02196.HK (+01093.HK) | |
| Commercial Biotech | REGN BIIB ALNY BGNE (+VRTX) | 09926.HK 06160.HK 06990.HK (+01801.HK) | |
| Pre-approval Biotech | CRSP BEAM KYMR | 02197.HK 09688.HK | |
| MedTech / Devices | ABT BSX ISRG EW BDX (+MDT SYK BAX) | 01666.HK 02190.HK 02160.HK 02252.HK (+00853.HK) | |
| CDMO / Life Science Tools | DHR ILMN A CRL IQV (+TMO) | 02268.HK 01548.HK 03759.HK 02359.HK (+02269.HK) | |
| Managed Care | ELV CI CNC HUM (+UNH CVS MOH) | (Asia insurers stay in Financials) | |
| Healthcare Providers / Services | THC UHS ENSG (+HCA) | 01099.HK 03320.HK 01515.HK 06078.HK | Q0F.SI A50.SI QC7.SI on Healthcare Provider (SG) |

Corrections: 00992.HK is Lenovo, not a MicroPort spin-off (the spin-offs 02160.HK and 02252.HK are
pinned); 02162.HK is Kangji Medical, a device maker, so Clover 02197.HK is the Chapter 18A name. CTLS is
delisted (skipped). 01099.HK (Sinopharm) and 03320.HK (CR Pharma) are distributors pinned to the provider
profile on the owner's placement; a Pharma Distribution profile exists if the owner prefers it. 06160.HK
is BeiGene's HK line, BGNE the ADR; both pinned. The expanded after-measurement is in
`docs/wave5_stage6_scorecard.md`.

## 5. WACC

Commercial Biotech uses the Biopharma sector rate (8.5%) through the engine; the rNPV profile override
table keeps Large Cap Pharma 7.85% and Pre-approval Biotech 11.0%. A Commercial Biotech row for the rNPV
override is the owner's to set.

## 6. Engine work shipped

Map v18 (+7 labels in scope, +2 rows); Commercial Biotech profile, statics and rNPV defaults; pins VRTX,
01801.HK, MDT, SYK, BAX, 00853.HK, 01093.HK, 02269.HK, MOH, HCA; the pipeline kind end to end (schema,
prompt, bridge, store detail, gate label, build path, engine bridge and quarantine rule);
`tests/test_healthcare_wave5.py`. Golden re-based for the profile-weights digest only.
