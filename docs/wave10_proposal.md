# Wave 10: the 18 labels with a row outside routing scope — Stage 0, Stage 2 and the Stage 3 proposal

Owner request, 2026-09-27: follow the wave methodology and map the labels whose routing row exists
but sits outside routing scope, each under its own sector. Nothing below is written into a profile;
every weight and multiple is PROPOSED for the owner's checkpoint.

## Scope, by destination sector

| Sector | Labels | Joins |
|---|---|---|
| Consumer | Specialty Retail, Apparel - Retail, Apparel - Manufacturers, Apparel - Footwear & Accessories, Auto - Manufacturers, Auto - Parts, Restaurants, Gambling, Resorts & Casinos, Leisure, Luxury Goods, Travel Lodging, Travel Services, Furnishings, Fixtures & Appliances | Wave 10 proper |
| Energy (utilities) | Regulated Gas, Renewable Utilities | the Wave 2 power and transition profiles |
| Health care | Drug Manufacturers - Specialty & Generic, Medical - Pharmaceuticals | the Wave 5 health care profiles |
| Resources | Other Industrial Metals & Mining | Wave 9 (a dead row: no member carries the label; FMP now uses Industrial Materials, which Wave 9 routes) |

## Stage 0: the engine today (71 names, docs/baselines/baseline_wave10.json)

Measured with each unpinned name's sector seeded from its own label's row — the router's best case
(see the harness note below). Within ±30% of consensus 9 of 33, of spot 30 of 71, backtest passed
14 of 73, anchor missing 4.

Every unpinned name is placed by the ratio ladder, and the consumer ladder reads growth and margin,
not the business: restaurants (Chipotle, Guming, Super Hi), car makers (Ferrari, Chery, Great Wall,
Jardine Cycle & Carriage), auto suppliers (Weichai, Fuyao), Marriott and Midea all land on Apparel /
Athletic Wear; Honda and Genuine Parts on Traditional Retail; Atmos Energy, ENN and three renewable
generators on Merchant Power; Haleon, Hengrui and Royalty Pharma on CDMO / Life Science Tools. MGM and
Hasbro publish no value (surviving weight 0.3 and 0.2).

Harness note: scripts/wave_baseline.py seeds an unpinned name's sector with "Tech" (the state
builder's fallback), so an unpinned consumer or pharma name used to run down the Tech ladder. The
first Stage 0 pass did exactly that (24 names on Mature SaaS). The script now takes
--sector-from-label. Earlier waves' Stage 0 figures for unpinned names carry the same artefact;
their Stage 4 figures do not, because the wave pinned or routed every name.

Data anomalies to clear before Stage 4: Fast Retailing (06288.HK, +5,391% against spot: JPY
statements against an HKD depositary line), Centurion (OU8.SI, +814%), Honda (+142%, ADR ratio).

## Stage 2: live cohorts (local store, 2026-09-22 refresh; value / peer count)

| Label | Exch | P/E | NTM P/E | EV/EBITDA | NTM EV/EBITDA | P/B | FCF yield |
|---|---|---|---|---|---|---|---|
| Specialty Retail | US | 20.8x/18 | 17.6x/18 | 14.1x/18 | 12.3x/18 | 5.3x | 4.9% |
| Specialty Retail | HK | 15.9x/8 | 15.2x/5 | 9.5x/11 | 9.0x/5 | 1.7x | 8.0% |
| Apparel - Retail | US | 10.6x/16 | 12.4x/15 | 9.6x/18 | 8.2x/16 | 2.3x | 6.3% |
| Apparel - Manufacturers | US / HK | 18.6x / 9.7x | 12.2x / - | 11.2x / 5.6x | 9.5x / - | 2.6x / 1.2x | 10.5% / 14.1% |
| Apparel - Footwear | US / HK | 15.1x / 6.2x | 11.3x / 8.0x | 10.4x / 4.6x | 7.6x / 4.7x | 3.6x / 0.9x | 6.2% / 4.8% |
| Auto - Manufacturers | US / HK | 15.8x / 14.4x | 10.4x / 7.1x | 13.3x / 9.3x | 7.7x / 3.6x | 1.4x / 1.6x | 8.2% / 15.0% |
| Auto - Parts | US / HK | 17.1x / 12.9x | 10.4x / 10.6x | 10.6x / 7.6x | 5.5x / 5.9x | 1.7x / 1.8x | 8.4% / 9.2% |
| Restaurants | US / HK | 20.5x / 15.0x | 19.8x / 12.9x | 15.0x / 4.5x | 13.2x / 5.3x | - / 1.3x | 4.4% / 6.9% |
| Casinos & Resorts | US / HK | 16.5x / 9.0x | 14.5x / 8.9x | 9.5x / 7.8x | 8.2x / 6.4x | 3.7x / 1.3x | 5.5% / 12.3% |
| Leisure | US / HK | 19.5x / 10.8x | 16.4x / - | 11.9x / 5.9x | 9.8x / - | 2.7x / 1.0x | 8.9% / 5.6% |
| Luxury Goods | HK | 8.7x/12 | 7.5x/5 | 4.3x/11 | 5.6x/5 | 1.2x | 17.2% |
| Travel Lodging | US / HK | 28.5x / 13.9x | 19.5x / - | 14.9x / 11.4x | 11.5x / - | 6.5x / 0.2x | 4.5% / 10.4% |
| Travel Services | US | 17.3x/12 | 12.9x/13 | 12.5x/14 | 7.9x/13 | 2.8x | 7.6% |
| Furnishings & Appliances | US / HK | 15.1x / 11.6x | 13.3x / 11.0x | 9.8x / 5.8x | 9.2x / 5.8x | 2.0x / 1.3x | 7.8% / 7.2% |
| Drug Mfrs - Spec & Generic | US / HK | 34.6x / 18.0x | 14.5x / 12.7x | 13.2x / 7.9x | 9.7x / 6.8x | 2.2x / 1.3x | 5.1% / 6.5% |
| Medical - Pharmaceuticals | HK | 16.0x/12 | 12.3x/6 | 9.5x/12 | 8.8x/6 | 1.2x | 6.6% |
| Regulated Gas | US / HK | 17.2x / 10.1x | 16.7x / 8.7x | 11.5x / 10.4x | 9.2x / 5.5x | 1.5x / 0.8x | - / 7.8% |
| Renewable Utilities | US / HK | 55.9x / 8.8x | - | 10.0x / 8.5x | 11.8x / - | 2.0x / 0.3x | - / -1.5% |

Curated baskets measured (five-name floor): QSR franchisors (MCD, YUM, QSR, DPZ, WEN) NTM P/E 14.9x,
NTM EV/EBITDA 13.0x; asset-light lodging (MAR, HLT, IHG, H, WH, CHH) NTM P/E 25.9x, NTM EV/EBITDA
17.2x against the label's 11.5x; China city gas (HK & China Gas, ENN, BEH, CR Gas, China Gas) NTM P/E
10.0x, NTM EV/EBITDA 5.6x, P/B 0.8x; US gas LDCs NTM P/E 16.1x, NTM EV/EBITDA 10.0x; generics and
specialty (TEVA, ZTS, VTRS, ELAN, ANIP) NTM P/E 9.8x, NTM EV/EBITDA 7.2x; HK hotel owners P/B 0.3x;
HK apparel contract makers (Shenzhou, Crystal, Yue Yuen, Stella, Star Shine) resolve. Under the floor
(three or four names, so they would fall to the label median unless the floor is waived): off-price
retail (TJX, ROST, BURL), auto-parts retail (ORLY, AZO, AAP), online travel (BKNG, EXPE, ABNB, TRIP),
cruise lines (RCL, CCL, NCLH, VIK), US yieldcos.

## Stage 3: proposal, by sector (PROPOSED; the owner decides)

### Consumer (Wave 10 proper)

1. Travel & Dining is four businesses. Split it:
   - Restaurants: Forward EV/EBITDA .45 (anchor), Forward P/E .35, FCF Yield .20; the QSR franchisor
     basket for US franchisors, the label cohort otherwise. MCD, SBUX and the HK chains are pinned to
     Travel & Dining today; pins beat rows, so they are re-pinned to the new profile.
   - Casinos & Integrated Resorts: Forward EV/EBITDA .50 (anchor), EV/EBITDA (norm) .30 (Macau's
     cycle), FCF Yield .20. Galaxy and Sands China are pinned to Travel & Dining and are re-pinned.
   - Lodging: asset-light franchisors on Forward EV/EBITDA .50 (anchor), Forward P/E .30, FCF Yield
     .20 over the asset-light basket; the HK hotel owners (HK & Shanghai Hotels, Shangri-La, Langham)
     on an HK market row to an owner profile: RNAV (published) .50 falling back to P/B, EV/EBITDA .30,
     DDM .20, since they trade at 0.2-0.3x book.
   - Travel Services: online travel on Forward P/E .40 (anchor), Forward EV/EBITDA .40, FCF Yield .20;
     cruise lines on Forward EV/EBITDA .50 (anchor), EV/EBITDA (norm) .30, Forward P/E .20. Both
     baskets sit under the five-name floor: the owner decides between a floor waiver and the label.
2. Auto - Manufacturers: the row points at Automotive & EV (EV/Revenue anchor), which is right for
   the EV growth names already pinned there and wrong for Honda, Stellantis, Chery and Great Wall.
   Re-point the row to Automotive (OEM) (EV/EBITDA .40 anchor, P/E .30, P/BV .20, FCF Yield .10);
   the EV names keep their pins; Ferrari pinned to Luxury Goods (it trades on a luxury P/E, not an OEM
   multiple).
3. Auto - Parts: a new Auto Parts & Suppliers profile, cyclical: EV/EBITDA (norm) .45 (anchor),
   Forward P/E .35, FCF Yield .20. Genuine Parts pinned to Industrial Distribution (Wave 9); O'Reilly
   and AutoZone (Specialty Retail label) on an auto-parts retail basket if the floor is waived.
4. Retail: Traditional Retail re-specified to Forward EV/EBITDA .40 (anchor), Forward P/E .35, FCF
   Yield .25 (EV/Revenue and ROIC vs WACC out: a revenue multiple on a retailer rewards low margins).
   Off-price (TJX, ROST, BURL) as a sub-cohort if the floor is waived. MercadoLibre and Sea must be
   pinned (Hyper-Growth Platform) before Specialty Retail enters scope, or the row sends them to
   Traditional Retail.
5. Apparel (Manufacturers, Footwear): the row stays on Apparel / Athletic Wear with its proxied Brand
   Val leg (priced as EV/Revenue today) replaced by Forward P/E; the HK contract makers price on
   their own sub-cohort (a brand multiple on an OEM overstates them: Bosideng +126%, Crystal +44%).
6. Leisure: the row points at Consumer Growth, whose method table lists P/E and also excludes it.
   A new Leisure Products & Brands profile: Forward P/E .40 (anchor), Forward EV/EBITDA .40, FCF
   Yield .20. Li Ning pinned to Apparel / Athletic Wear beside ANTA.
7. Luxury Goods: keep the row. The HK gold jewellers (Chow Tai Fook, Luk Fook, Chow Sang Sang, Laopu,
   Zhou Liu Fu) as a curated HK basket, because the maisons (Prada) and the gold retailers are one
   label at 8.7x trailing P/E.
8. Furnishings, Fixtures & Appliances: keep the row on Consumer Durables.

### Energy (utilities, with Wave 2)

9. Regulated Gas: US gas utilities to Regulated Utility (P/Rate Base with review-gated rate-base
   pre-fills, as Wave 2 built). The HK and China city-gas distributors are not rate-base regulated
   (connection fees and a gas-sales spread): an HK market row to a new City Gas Distribution
   (HK / China) profile — Forward P/E .40 (anchor), EV/EBITDA .30, DDM .30 — on the China city-gas
   basket.
10. Renewable Utilities: keep the row on IPP. Stage 0 shows the PPA-backed DCF anchor surviving on
    part of the set only (Brookfield 0.45); PPA inputs for the renewable generators are a Stage 5
    pre-fill, and US yieldcos wait on a floor decision.

### Health care (with Wave 5)

11. Drug Manufacturers - Specialty & Generic: a new Specialty & Generic Pharma profile — Forward P/E
    .40 (anchor), EV/EBITDA .35, FCF Yield .25 — on the generics and specialty basket; Large Cap
    Pharma's rNPV leg has nothing to price on a generics book. The China innovators (Hengrui, Hansoh,
    Sino Biopharm) stay on Large Cap Pharma by pin; Haleon pinned to Household / Personal (consumer
    health); Zoetis and Elanco on the new profile (the Animal Health profile was removed today).
12. Medical - Pharmaceuticals: the row to the same Specialty & Generic Pharma profile; JD Health
    pinned to Pharma Distribution; Corcept, Kiniksa and Liquidia pinned to Commercial Biotech;
    Royalty Pharma on the new profile, flagged (a royalty book is a DCF of receipts, a later input).

### Resources (with Wave 9)

13. Other Industrial Metals & Mining: delete the row (no member carries the label).

## Owner questions

1. The Travel & Dining split into four profiles (Q1), and the HK hotel-owner row.
2. Auto - Manufacturers row to Automotive (OEM); Ferrari to Luxury Goods.
3. The new profiles: Auto Parts & Suppliers, Leisure Products & Brands, City Gas Distribution
   (HK / China), Specialty & Generic Pharma, and the Travel & Dining four — weights as proposed?
4. The five-name floor for the three- and four-name baskets (off-price, auto-parts retail, online
   travel, cruise, yieldcos): waive for curated baskets, or take the label median?
5. Traditional Retail re-specified without EV/Revenue and ROIC.
6. The pins ahead of scope (MELI, SE, Li Ning, Ferrari, GPC, Haleon, JD Health, the three biotechs).
7. Delete the dead metals row.
