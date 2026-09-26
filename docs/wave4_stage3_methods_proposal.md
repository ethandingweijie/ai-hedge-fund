# Wave 4 (Consumer Staples): taxonomy, routing, methods and peers, for the owner's confirmation

Stage 1 decided (owner, 2026-09-26): BF-B cut (thin label, no consensus), 06808.HK cut (Consumer
Cyclical by label, mainland hypermarkets, override debt), MDLZ kept as the essential staple anchor.
COST ships pinned to Membership / Subscription Retail in the same commit as any `Discount Stores` row.
Tobacco gets a standalone profile. Universe: 21 names.

Every multiple below is read from the comps store as of 2026-09-26 (local US and HKSE baskets; the
SES basket is empty locally and reads from production's weekly refresh), and every one is a proposal
until you say so. ◆ marks a checkpoint.

## 1. Why Stage 0 missed (recap)

Industry routing is switched off for all twelve staples labels: `routing_scope` holds only the Wave 1-3
labels. The map's own rows for beverages, packaged foods, household, tobacco and food distribution are
never applied, six labels have no row, and 20 of 23 names fell to the financial-ratio ladder, which
priced Coca-Cola as Luxury Goods and Mondelez as Mature SaaS. Stage 2 is the same fix Waves 2 and 3
applied: put the labels in scope, write the rows, pin the exceptions.

## 2. Taxonomy and routing (Stage 2) ◆

| FMP label | Row -> profile | Names | Basket today (US / HKSE) |
|---|---|---|---|
| `Beverages - Non-Alcoholic` | Consumer / Food & Beverage (existing, revised) | KO, PEP | 11 / 9 |
| `Beverages - Alcoholic` | Consumer / Food & Beverage | BUD, 00168.HK | 6 / 3 (thin) |
| `Packaged Foods` | Consumer / Food & Beverage | MDLZ, 00322.HK, 00288.HK | 17 / 17 |
| `Food Confectioners` | Consumer / Food & Beverage, pooled into the Packaged Foods family | (MDLZ carries it) | 3 / 1 -> family |
| `Beverages - Wineries & Distilleries` | Consumer / Food & Beverage, pooled into the Beverages family | none after the BF-B cut | 3 / 1 -> family |
| `Agricultural Farm Products` | Consumer / **Agribusiness & Food Processing (new)** | ADM, TSN, 00682.HK | 9-12 / 6 |
| `Discount Stores` | Consumer / **Grocery & Discount Retail (new)**; COST pinned Membership / Subscription Retail | WMT | 8-9 / 0 |
| `Grocery Stores` | Consumer / Grocery & Discount Retail | KR, D01.SI | 8-9 / 0 |
| `Food Distribution` | Consumer / Grocery & Discount Retail (today: Traditional Retail) | USFD | 5-6 / 6 |
| `Household & Personal Products` | Consumer / Household / Personal (existing) | PG, CL, EL | 13-17 / 10-11 |
| `Tobacco` | Consumer / **Tobacco (new)** | PM, MO | 6-7 / 3 (thin) |
| `Department Stores` | no row (Sun Art cut) | -- | -- |

Pins that must ship with the rows: COST -> Membership / Subscription Retail (golden fixture; its
baseline moves and is re-based with the reason); F34.SI stays on Agribusiness & Food (SG) by market.
HKSE has no members for Discount, Grocery or Food Distribution and three for Alcoholic and Tobacco, so
those rows need `INDUSTRY_FAMILIES` entries or the market's static fill.

## 3. Method tables ◆

Multiples come from the label's basket at run time; the statics below are the fallback and the
re-peg reference, read from the same baskets today.

### Food & Beverage (revised) -- KO, PEP, BUD, MDLZ, 00168.HK, 00322.HK, 00288.HK

Today: P/E .50 anchor, DCF (2-stage) .30, EV/EBITDA .15, Brand Valuation .05 (the last is a proxy leg).

| Method | Weight | Anchor | Derivation |
|---|---|---|---|
| Forward P/E | 0.35 | yes | NTM basket: Non-Alc 18.9x (n9), Alcoholic 14.5x (n7), Packaged 11.7x (n19); HK Packaged 9.2x |
| EV/EBITDA | 0.25 | | Non-Alc 16.0x, Alcoholic 8.5x, Packaged 10.2x; HK Packaged 5.9x |
| DCF | 0.25 | | stable margins; TGR 2.5-3.0% |
| FCF Yield | 0.15 | | replaces Brand Valuation, which never priced |

Trailing P/E is 26.5x on Non-Alc against 18.9x NTM: the trailing anchor over-prices the beverage
names, so the anchor moves to NTM. One profile, three baskets: the label decides the multiple.

### Agribusiness & Food Processing (new) -- ADM, TSN, 00682.HK

| Method | Weight | Anchor | Derivation |
|---|---|---|---|
| EV/EBITDA (norm) | 0.35 | yes | US Agricultural 9.5x (n12); HK 14.8x (n6); crush spreads and protein margins mean-revert |
| P/E (norm) | 0.25 | | trailing 21.7x against 11.8x NTM: trough earnings inflate the trailing figure |
| DCF | 0.25 | | through-cycle margin schedule |
| P/BV | 0.15 | | asset-heavy floor (silos, plants, crush capacity) |

Mirrors Agribusiness & Food (SG), which prices Wilmar on Forward P/E .45 because SG consensus is
denser; the US and HK names carry normalised legs instead because their trailing prints are at a
cycle trough. 00682.HK had four uncomputable legs at Stage 0; P/BV gives it an anchor.

### Grocery & Discount Retail (new) -- WMT, KR, D01.SI, USFD

| Method | Weight | Anchor | Derivation |
|---|---|---|---|
| EV/EBITDAR | 0.35 | yes | lease-heavy; Discount 13.2x EV/EBITDA (n9), Grocery 8.7x (n9), Food Distribution 13.1x (n6) |
| Forward P/E | 0.25 | | Discount 18.1x, Grocery 12.8x, Food Distribution 15.5x |
| FCF Yield | 0.20 | | |
| DCF | 0.20 | | |

WMT is the recorded exception: the market pays ~38x for it against a 19x Discount Stores basket, and
no basket multiple reaches a $129 consensus from a $65 Stage 0. That gap is a quality premium the
owner either records as a deviation or prices with an owner-set premium factor; it is not a routing
fix. COST is not in this profile (pinned Membership / Subscription Retail).

### Household / Personal (existing, kept) -- PG, CL, EL

Today: P/E .40 anchor, EV/EBITDA .30, DCF .20, ROIC .10. Proposal: anchor to Forward P/E (US 16.8x
NTM, n18, against 22.2x trailing; HK 9.4x), otherwise unchanged. EL is the open question: FMP labels
it Household, the router chose Luxury Goods, and its FY2025 is a loss year, so on this profile it
prices on P/E (norm) through the swap.

### Tobacco (new, owner decision) -- PM, MO

| Method | Weight | Anchor | Derivation |
|---|---|---|---|
| FCF Yield | 0.35 | yes | cash-return business; US Tobacco basket n6-7 |
| Forward P/E | 0.25 | | 11.9x NTM (n5) against 20.0x trailing |
| DDM | 0.20 | | payout is the investment case; the SG profiles already carry the leg |
| EV/EBITDA | 0.20 | | 11.7x (n7) |

Cost of equity above the Consumer default: regulatory and ESG risk carry a premium. Proposal 8.0-8.5%
against 7.5% sector default; the figure is yours (§4).

### Membership / Subscription Retail (existing) -- COST by pin; Agribusiness & Food (SG) -- F34.SI

Unchanged.

## 4. WACC (C2 registry, one row per profile) ◆

| Profile | Proposed | Basis |
|---|---|---|
| Food & Beverage | 6.8-7.2% | below the 7.5% Consumer default: lowest beta group in the sector |
| Household / Personal | 6.8-7.2% | same |
| Grocery & Discount Retail | 7.0-7.5% | lease leverage, thin margins |
| Agribusiness & Food Processing | 8.0-8.5% | commodity cyclicality |
| Tobacco | 8.0-8.5% | regulatory and ESG premium |

Rates to be cited per row against the Damodaran January 2026 file the Wave 2 rows used.

## 5. Inputs (Stage 5)

No SOTP profile in Wave 4: none of the 21 is a conglomerate. WH Group (00288.HK) is the borderline case
(Smithfield US and China pork); it stays on Food & Beverage unless you want its two geographies as
parts. No review-gated inputs are required. C4 KPI specs owed per profile: organic growth and price/mix
(F&B, Household), same-store sales and membership fee income (Retail), crush margin and protein
spread (Agribusiness), volume decline and pricing (Tobacco).

## 6. Engine work this implies

- `routing_scope` +10 labels; rows per §2; `INDUSTRY_FAMILIES` for Wineries and Confectioners and for
  the thin HKSE labels; COST pin in the same commit.
- Two new profiles plus Tobacco; Food & Beverage and Household / Personal revised; statics for the new
  profiles from §3; five `_PROFILE_WACC["Consumer"]` rows (the table does not exist yet).
- Census pins (profiles 113 -> 116), swap population (two new normalised legs), COST golden re-base.

## 7. Questions ◆

1. EL: Household / Personal (label, proposed) or Luxury Goods (router's choice)?
2. WH Group: Food & Beverage, or a SOTP on its two geographies?
3. USFD: Grocery & Discount Retail (proposed) or stay on Traditional Retail?
4. WMT: record the quality premium as a deviation, or set an owner premium factor?
5. Tobacco cost of equity: 8.0-8.5% as proposed?
