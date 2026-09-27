# Wave 8 (Real estate): architecture review and remap, PROPOSED

Derived from the comps store and FMP on 2026-09-27 and shown with the derivation. Nothing applied; the
owner accepts, re-weights or rejects at the ◆ checkpoint. Constants are proposed, never written, here.

## 1. What the review found (Stage 0 causes, in architecture terms)

1. No real-estate label routes; 24 of 34 names are placed by the ladder (REIT, Mature SaaS, Mature
   Platform), including every Hong Kong name and two US non-REITs.
2. The REIT profile's four legs read a static April table (`_REIT_SUBTYPE_MULTIPLES`) chosen by a keyword
   classifier; the live cohort is fetched and ignored. The table's cap rates are off the market by 60 to
   240 bp in both directions, and "tower" routes American Tower to the office cap rate.
3. Developers and landlords have no profile outside Singapore and are capitalised as rent collectors; the
   Singapore "NAV" is the same cap-rate formula and fails on a hotel-heavy developer.
4. CapitaLand Investment lacks the fund-manager input its profile anchors on; Iron Mountain, CBRE and
   D.R. Horton have no profile at all.

## 2. Routing (Stage 2): rows, scope, market maps and pins

Rows to write (exact FMP strings) and the thirteen labels for `routing_scope` (map v22):

| Label | US row | HK market map | SG market map (exists today) |
|---|---|---|---|
| `REIT - Retail`, `REIT - Industrial`, `REIT - Office`, `REIT - Residential`, `REIT - Diversified`, `REIT - Healthcare Facilities`, `REIT - Specialty`, `REIT - Hotel & Motel` | RealEstate / REIT (sub-type from the label, section 3) | RealEstate / REIT (thin HK cohorts fall to the table, flagged) | S-REIT |
| `REIT - Mortgage` | **out of scope** (a levered spread business: P/B and dividend, a Financials profile; propose Wave 9 or exclusion) | — | — |
| `Real Estate - Development` | Homebuilder / Land Developer (new; 8 US members) | Property Developer (HK / China) (new) | Property Developer (SG) |
| `Real Estate - Diversified` | RealEstate / REIT (1 US member) | Landlord / Investment Property (HK) (new) | Property Developer (SG) |
| `Real Estate - Services` | Real Estate Services (new: CBRE, JLL, CWK) | Real Estate Services (property managers: 20 members at 4.7x EBITDA, 1.4x book) | Real Estate Agency (SG) |
| `Residential Construction` | Homebuilder / Land Developer (new; 18 members) | — | — |

Pins (owner set): WELL VTR (REIT, healthcare); PLD (industrial); PSA EXR (self-storage); EQIX DLR (data
centre); AMT CCI SBAC (REIT, **tower**, a new sub-type); IRM (REIT, specialty); SPG (retail); O (net lease);
00823.HK Link (REIT, retail); CBRE JLL (Real Estate Services); DHI LEN PHM NVR TOL (Homebuilder); 00016.HK
01113.HK 00688.HK 01109.HK 00083.HK 02202.HK 00960.HK (Property Developer (HK / China)); 01972.HK 00004.HK
00012.HK (Landlord / Investment Property (HK)); U06.SI (Property Developer (SG), row in scope); 9CI.SI stays
on Real Estate Asset Manager (SG) and takes the `alt_manager` input (section 4).

## 3. The REIT sub-type table, re-derived from the market (owner constants, PROPOSED)

The sub-type comes from the label and the pins, not the keyword classifier. The cap rate is the cohort's
median EBITDA yield (the engine's own NOI definition, `1 / EV-EBITDA`), large cohort where it has five
members, else all; the P/FFO and P/AFFO multiples cannot be read from the store today (no FFO field), so
the April table stays for them as PROPOSED constants until the comps job carries `p_ffo`.

| Sub-type (label) | Table today (cap / P/FFO / P/AFFO) | Market implied cap, 2026-09-27 (n) | Proposed cap |
|---|---|---|---|
| healthcare (`REIT - Healthcare Facilities`) | 6.0% / 15x / 17x | 5.06% large (8); 6.83% all (16) | 5.1% |
| industrial (`REIT - Industrial`) | 5.5% / 18x / 20x | 5.42% large (6); 6.40% all (13) | 5.4% |
| self_storage (pins PSA EXR) | 5.5% / 19x / 21x | inside `REIT - Industrial`; no separate cohort | 5.5% (keep) |
| data_center (pins EQIX DLR) | 5.0% / 22x / 25x; premium 4.5% / 23x / 26x | inside `REIT - Specialty` large 5.12% (8); EQIX alone 3.3% | 5.0%; drop the premium tier or make it a research `cap_rate_market` only |
| **tower (new; pins AMT CCI SBAC)** | reads office 7.5% today | `REIT - Specialty` large 5.12% (8) | 5.1% / 20x / 22x (P/FFO from the specialty NTM P/E 31.7x is GAAP; PROPOSED) |
| specialty other (`REIT - Specialty`, IRM) | default 6.5% / 15x / 17x | 5.69% all (15) | 5.7% |
| retail (`REIT - Retail`) | 6.2% / 14x / 15x | 6.94% large (10); 6.77% all (20) | 6.9% |
| net_lease (pin O) | 5.0% / 16x / 18x | inside `REIT - Retail`; no separate cohort | 5.0% (keep; Green Street 5.2%) |
| office (`REIT - Office`) | 7.5% / 12x / 13x | 6.51% large (7); 6.69% all (13) | 6.5% |
| residential (`REIT - Residential`) | 5.5% / 17x / 19x | 6.43% large (7); 6.75% all (14) | 6.4% |
| hospitality (`REIT - Hotel & Motel`) | 8.0% / 11x / 12x | 9.11% (11) | 9.1% |
| default (`REIT - Diversified`) | 6.5% / 15x / 17x | 6.61% large (8); 7.84% all (17) | 6.6% |
| HK REITs (Link) | default 6.5% | `REIT - Retail` HKSE 3 members: no cohort | 6.5% table, flagged `HK_REIT_THIN_COHORT` |
| S-REIT NAV leg | default 6.5% | `REIT - Diversified` SES 4.70% (8), Industrial 5.32% (6), Retail 5.45% (6) | by label, 4.7 to 5.5% |

**Two ways to make it live (owner question 3).** (a) The cap rate resolves from the cohort at run time
(the Wave 7 pattern: live first, table as fallback), so the constant is the *rule*, not the number. (b) A
review-gated `nav` kind (published NAV per share or cap rate per name from the annual report, Green Street
or a broker note) anchors the NAV leg when accepted, the SOTP pattern the plan named; the table stays for
names without one. The two are not exclusive; (a) covers the universe, (b) the names that matter.

## 4. Method tables (PROPOSED)

| Profile | Anchor | Other legs | Derivation |
|---|---|---|---|
| REIT (keep) | NAV (market cap rate) .45 | P/FFO .30, P/AFFO .15, DDM .10 | structure holds; the basis moves to the live cohort (section 3); Forward EV/EBITDA on the live cohort as the cross-check the trace already prints |
| Property Developer (HK / China) (new) | P/B .40 (RNAV proxy: HKSE development cohort 0.40x, large 0.42x) | RNAV (published) .25 via a `nav` kind, falling back to P/B; Forward P/E .20 (12.8x NTM HKSE); DDM .15 (the cohort yields 5 to 7%) | the market pays 0.4x a book that already carries revalued investment property; negative-earnings names (Vanke, Longfor) price on book and dividend or not at all, flagged |
| Landlord / Investment Property (HK) (new) | NAV (market cap rate) .40 on the HKSE `Real Estate - Diversified` implied 6.2% | P/B .30 (cohort 0.30x: the structural discount), DDM .30 | rent collectors; the cap-rate NAV is right here and the discount to it is the market's, shown not tuned |
| Homebuilder / Land Developer (new) | P/B .35 (1.18x all, 1.79x large) | Forward P/E .35 (12.4x NTM), EV/EBITDA .20 (11.9x), FCF Yield .10 (5.6%) | a cyclical priced on book and next year's earnings; P/E (norm) as the cross-check |
| Real Estate Services (new) | Forward P/E .40 (15.7x NTM US; 7.1x HKSE) | EV/EBITDA .30 (14.9x US; 4.7x HKSE), DCF .20, FCF Yield .10 | asset-light fee businesses; each market on its own cohort |
| Real Estate Asset Manager (SG) (keep) | P/E (norm) .40 | SOTP (published) .35 fed by the `alt_manager` kind (FRE, DE, P/FRE range), DDM .25 | CapitaLand Investment is a fund manager; the Wave 6 input fits and the leg is uncomputable without it |
| S-REIT, Property Developer (SG), Real Estate Agency (SG) (keep) | unchanged | unchanged | five of six S-REITs land; City Developments' uncomputable NAV is a Stage 4 check (hotel EBITDA), not a method change |

Growth premium: the 0.85–1.30 band applies; REIT legs already run at premium 1.0 and stay there.

## 5. Questions for the owner (◆ before Stage 4)

1. Universe: cut or keep D.R. Horton (a homebuilder; the wave would carry a Homebuilder profile), CBRE and
   CapitaLand Investment (services and fund management), Iron Mountain, and the distressed China developers
   Vanke and Longfor (no earnings to price).
2. Sub-type by label and pin instead of the keyword classifier, with a new `tower` sub-type for AMT, CCI,
   SBAC.
3. The cap rate: live market-implied cohort rate with the April table as fallback (a), a review-gated `nav`
   kind per name (b), or both.
4. The re-derived table in section 3 as PROPOSED constants for the P/FFO and P/AFFO legs the store cannot
   yet read live.
5. New profiles: Property Developer (HK / China), Landlord / Investment Property (HK), Homebuilder / Land
   Developer, Real Estate Services.
6. Mortgage REITs out of scope (a Financials profile in a later wave) rather than on the REIT NAV.
7. CapitaLand Investment on the `alt_manager` kind (FRE and distributable earnings) rather than a new input.
