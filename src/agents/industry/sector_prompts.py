"""
src/agents/industry/sector_prompts.py
======================================
Sector + profile-specific prompt blocks for Section 2F (KPI framework).

Current architecture sends ONE generic 2F asking for "the anchor KPI" on every
ticker. This is wasteful (tokens) and lossy (sector-specific disclosures go
missing — REITs don't disclose portfolio mix at finest granularity, banks
don't quantify NIM rate sensitivity, biotech doesn't enumerate pipeline PoS).

This module produces a matched 2F block given (sector, profile_name) from the
strategic router. Callers: `_build_research_system` in deep_research.py.

Taxonomy matches the existing calibration tables:
  - REIT sub-types       → _REIT_SUBTYPE_MULTIPLES (dcf_agent.py)
  - Bank sub-profiles    → _BANK_PROFILE_CALIBRATION
  - Tech sub-types       → _TECH_SUBTYPE_MULTIPLES
  - Biopharma profile    → INDUSTRY_VALUATION_PROFILES["Biopharma"]

Fallback: generic 2F when no match.
"""
from __future__ import annotations


# ── Shared header prefix — applied to every sector-specific block ───────────

_SECTION_2F_HEADER = """
──────────────────────────────────────────
2F. INDUSTRY-SPECIFIC KPI FRAMEWORK
──────────────────────────────────────────
Purpose: Every industry has 3-5 metrics that actually predict forward
performance. Generic financial metrics miss what matters. The asks below
are tailored to this ticker's sector and sub-profile — answer ALL of them
precisely.

BE HONEST. If the research doesn't substantiate a specific data point, write
"not disclosed" rather than omitting the line. Never invent, estimate, or
extrapolate a figure to fill a gap — an honest "not disclosed" is far more
valuable downstream than a fabricated number that contaminates the valuation.
Cite the publisher and date for every figure you DO surface, and flag any
value whose source you cannot identify.
"""


# ── REIT / RealEstate ───────────────────────────────────────────────────────

_REIT_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 PORTFOLIO COMPOSITION (MANDATORY — disclose at the finest granularity
the annual report shows; DO NOT collapse sub-categories into broad buckets):
  • Asset-class mix as % of GAV or NOI. Enumerate exactly as the REIT
    discloses (e.g. "61% IT parks, 11% industrial & logistics, 8% data
    centers, 20% other"). Preserve granular labels like "business_park",
    "IT_park", "interconnection", "net_lease", "logistics_park",
    "student_housing", "medical_office", "senior_housing", "life_science_lab".
  • Geographic mix as % of revenue or GAV. Country + sub-region where
    disclosed (e.g. "Bangalore 35%, Chennai 22%, Hyderabad 21%, Mumbai 14%,
    Pune 8%").

2F.2 PORTFOLIO CAP RATE — weighted-average implied cap rate from the annual
report's valuation table. Prefer CBRE / JLL / Knight Frank / Colliers
appraisal sources. Report as decimal (0.055 = 5.5%), not percentage.

2F.3 OPERATING KPIs: occupancy %, WALE (weighted-average lease expiry in
years), same-store NOI growth YoY, rent escalation rate embedded in leases.

2F.4 DISTRIBUTION QUALITY: DPS or DPU $/sh (or local cents), AFFO $/sh,
AFFO coverage ratio (DPS ÷ AFFO/sh), payout policy (% of distributable
income the REIT targets distributing).

2F.5 CAPITAL STRUCTURE (MANDATORY — DPU safety hinges on this):
  • Aggregate leverage ratio (debt / GAV or debt / NAV as %). For SGX
    REITs: cite vs MAS cap (45% standard, 50% subject to compliance).
  • WEIGHTED-AVERAGE COST OF DEBT (current %) + trajectory vs 1Y ago.
  • FIXED-RATE vs FLOATING-RATE debt % split.
  • HEDGING RATIO on floating exposure (% swapped to fixed).
  • Debt maturity wall next 3 years ($B maturing by year).
  • ICR (Interest Coverage Ratio) — especially for SGX business trusts
    where MAS requires ≥1.5x or ≥2.5x for higher leverage.

2F.6 DEVELOPMENT PIPELINE: % of GAV under construction or committed,
expected stabilized yield-on-cost, timing of next delivery.

2F.7 RESEARCH NAV: if any sell-side analyst or Green Street has published a
NAV/sh estimate, cite it with the analyst name and date. This is the
institutional consensus NAV the market compares against our computed NAV.
"""


_REIT_NET_LEASE_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 PORTFOLIO COMPOSITION — for net-lease REITs (O, ADC, NNN, WPC, BNL):
  • Tenant industry diversification as % of base rent (e.g. "convenience
    stores 10%, dollar stores 8%, home improvement 7%, QSR 6%, drug stores
    5%, supermarkets 4%, ..."). Enumerate top 10.
  • Investment-grade tenant % of rent.
  • Geographic mix as % of rent (US state-level if disclosed).
  • Property type split: standalone retail / restaurant / industrial / other.

2F.2 PORTFOLIO CAP RATE — blended acquisition cap rate (cited most recent
quarter) AND implied cap rate from reported NAV. Net-lease blue-chips
typically 5.0-5.5%.

2F.3 LEASE ECONOMICS (MANDATORY — rent escalators are the primary
valuation driver in higher-for-longer rate environments):
  • Weighted-average lease term (WALT, years).
  • RENT ESCALATOR STRUCTURE — split % of leases by escalator type:
    - Fixed annual bumps (typical 1.0-2.5%, typical US net-lease)
    - CPI-linked / uncapped inflation
    - CPI-linked with floor/cap (e.g. "CPI with 1%/3% floor/cap")
    - Market-rate review periodic
    - Flat / no escalator
  • Lease-term-end tenant-retention rate from history.
  • Weighted-average escalator rate embedded (e.g. "1.4% weighted avg").

2F.4 DISTRIBUTION QUALITY: DPS TTM, AFFO $/sh, AFFO coverage ratio,
consecutive-years-of-dividend-increases (blue-chip signal), monthly vs
quarterly payer.

2F.5 CAPITAL STRUCTURE (DPU safety in a rates cycle depends on this):
  • Aggregate leverage (debt/GAV or debt/asset %).
  • WEIGHTED-AVERAGE COST OF DEBT (current %) and trajectory vs prior year.
  • FIXED-RATE vs FLOATING-RATE debt % split (net-lease blue-chips
    typically 90%+ fixed; SGX REITs vary widely).
  • HEDGING RATIO on floating exposure (e.g. 60% of floating is swapped
    to fixed).
  • Debt maturity wall next 3 years ($B maturing by year).

2F.6 EXTERNAL GROWTH: LTM acquisition volume ($), deployed at average cap
rate X%, funded with Y% equity / Z% debt. Spread between acquisition cap
rate and cost of capital is the net-lease external growth engine.

2F.7 RESEARCH NAV: any sell-side NAV/sh estimate with analyst + date.
"""


_REIT_DATA_CENTER_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 PORTFOLIO COMPOSITION (data center specific):
  • Capacity mix: MW total, MW under contract (leased), MW under
    development, MW development pipeline.
  • Colocation % vs wholesale/hyperscale %.
  • Interconnection revenue as % of total revenue (if disclosed).
  • Tenant concentration: top 10 tenants % of ARR.
  • Hyperscaler exposure: % of ARR from Microsoft, Google, AWS, Meta,
    Oracle, IBM, ByteDance (any disclosed).

2F.2 PORTFOLIO CAP RATE — implied from recent acquisitions or analyst
consensus. Stabilized data centers typically 5.0-5.5% (wholesale) /
4.5-5.0% (interconnection-heavy like EQIX).

2F.3 OPERATING KPIs: leased MW %, development yield on cost (YoC),
stabilized yield vs acquisition cap rate, pricing power (YoY lease rate
increase on renewals), occupancy % on stabilized sites.

2F.4 AI DEMAND SIGNAL: AI-specific capacity commitments (GW or MW),
hyperscaler capex intentions referenced, contracted AI workloads.
This is the forward-growth driver for data center REITs FY2025-2027.

2F.5 DISTRIBUTION & CAPITAL: DPS, AFFO/sh, AFFO coverage, development
funding source (FFO + equity issuance vs debt vs JV).

2F.6 POWER: contracted power PPAs in GW, renewable mix %, power cost
trajectory. Power is the binding constraint on data-center growth.

2F.7 RESEARCH NAV: sell-side NAV/sh + development-pipeline NPV.
"""


# ── Financials — banks / insurance / asset managers / payments ──────────────

_BANK_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 CAPITAL ADEQUACY:
  • CET1 ratio (current quarter) vs regulatory minimum + management target.
  • Leverage ratio (Tier 1 / total exposure).
  • RWA density (RWA / total assets).
  • Buyback capacity ($B distributable above CET1 target).

2F.2 P/TBV "GOLDEN RATIO" (MANDATORY — the valuation anchor for banks):
  • Current P/TBV multiple (price / tangible book value per share).
  • Trailing ROE and forward ROE (management target).
  • The valuation identity: Fair P/TBV ≈ (ROE − g) / (CoE − g). When
    ROE > CoE, bank should trade at premium to TBV; when ROE < CoE,
    discount. Report the implied ROE — CoE SPREAD in basis points.

2F.3 EARNINGS QUALITY:
  • NIM (4-quarter trend: direction + magnitude).
  • Efficiency ratio / CIR (vs sub-profile target band — e.g. 40-45%).
  • ROE (trailing 4Q + MANAGEMENT TARGET explicitly quoted from earnings
    call; state call date).
  • ROA (bank quality signal at 1%+ = healthy).

2F.4 ASSET QUALITY (CRITICAL):
  • NPL ratio (non-performing loans / total loans).
  • NPL COVERAGE RATIO (provisions / NPLs, e.g. OCBC reports 150% = 1.50).
  • Net charge-offs (annualized).
  • MANAGEMENT OVERLAYS (specific $ disclosed, e.g. "S$700m in management
    overlays for macro uncertainty").

2F.5 LOAN BOOK:
  • Loan growth YoY (most recent + guided forward).
  • Loan type mix (commercial / consumer / mortgage / credit card /
    international — as % of total loans).
  • Geographic mix by revenue or loans (domestic vs international).

2F.6 FUNDING / DEPOSIT:
  • Core deposit growth YoY.
  • Loan-to-deposit ratio (LDR).
  • Core deposits vs wholesale funding % mix.

2F.7 CAPITAL RETURN (MANDATORY):
  • Dividend yield (TTM).
  • Buyback $ amount (LTM) and buyback yield.
  • Total payout ratio (div + buyback / net income).
  • CET1 surplus over target ($B).

2F.8 RATE SENSITIVITY:
  • Disclosed NIM CHANGE PER 100 BPS RATE MOVE (e.g. DBS research on OCBC:
    "11 bps of NIM per 100 bps"). If only 1-bp-language disclosed,
    convert.
  • Asset/liability duration gap or IRRBB disclosure if available.

2F.9 FORWARD GUIDANCE: verbatim management commentary on NIM trajectory,
loan growth, credit cost for FY+1.
"""


_ASSET_MANAGER_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 AUM trajectory — total AUM, net flows (LTM + last 4 quarters), market
appreciation vs net-flow decomposition. Fee-earning vs non-fee-earning AUM.

2F.2 EARNINGS QUALITY — FRE vs CARRY split (MANDATORY):
  • Fee-Related Earnings (FRE) as % of total earnings — stable,
    high-multiple income. Markets pay 20-30x+ for FRE growth.
  • Performance fees / Carried Interest as % of total earnings —
    cyclical, lower-multiple. Markets pay 5-10x for realized carry.
  • Explicit $ breakout: base management fees, performance fees,
    carry crystallizations, transaction/advisory fees (if applicable).

2F.3 Fee rate / take rate — management fee % on AUM (weighted by product
mix: passive ETFs 5-10bps, active equity 40-70bps, alternative 80-150bps).
Trajectory of take rate over last 3 years (mix shift winning or losing).

2F.4 Performance fees / Carry — realized in LTM, net accrued carry balance
($B unrealized), incentive-eligible AUM, high-water-mark status for hedge
fund strategies.

2F.5 Operating leverage — comp ratio (% of revenue), non-comp opex,
operating margin trajectory.

2F.6 Distribution economics — third-party channels vs direct vs proprietary.
Retail vs institutional mix.

2F.7 Capital: balance-sheet use (seed capital, GP commitment), leverage,
buyback pace.

2F.8 Fundraising runway for alt managers — latest flagship vintage size,
target next vintage, time to hard close.
"""


_PAYMENT_NETWORK_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 VOLUME metrics:
  • Gross payment volume (GPV, $T) growth YoY.
  • Cross-border volume growth YoY (higher-take-rate mix).
  • Authenticated transactions (V/MA report separately).

2F.2 TAKE RATE — revenue / GPV. Trajectory (mix shift toward value-added
services, cross-border, B2B).

2F.3 VALUE-ADDED SERVICES — revenue % from non-core data/fraud/advisory
services. Growth rate premium to core payment network revenue.

2F.4 GEOGRAPHIC mix (US / intl developed / emerging), emerging-markets
penetration as forward-growth engine.

2F.5 Regulatory exposure — ongoing investigations (antitrust, interchange
fee caps), Durbin Amendment impact, RBI / PSD2 equivalents.

2F.6 Capital return — div + buyback yield, payout ratio.
"""


_INSURANCE_KPI_PROMPT = _SECTION_2F_HEADER + """
VALUATION ANCHOR (identify which applies to this insurer — disclose BOTH
if it's a diversified holding like AIG / MetLife / Allianz):
  • P&C (Property & Casualty) → BOOK VALUE is king. Report P/BV ratio,
    BV/sh growth, ROE. Combined ratio drives earnings.
  • Life / Annuity → EMBEDDED VALUE is king. Report EV/sh, VNB (Value of
    New Business), VNB margin, EV operating earnings.
  • Health → Medical Loss Ratio (MLR) and premium yield are primary.

2F.1 PRIMARY VALUATION METRICS (choose by line-of-business):
  • P/BV and BV/sh growth for P&C.
  • EV, EV growth YoY, VNB, VNB margin for Life.
  • MLR + premium yield for Health.

2F.2 COMBINED RATIO (P&C): loss ratio + expense ratio. Below 100% = under-
writing profit. Trajectory over catastrophe seasons. Separate auto / homeowner /
commercial / reinsurance lines if disclosed.

2F.3 INVESTMENT YIELD — float invested at portfolio yield %. Duration gap
to liabilities. Asset allocation (govies / IG credit / HY / equities /
alternatives).

2F.4 PREMIUM / PRODUCT GROWTH YoY:
  • P&C: net written premium by line (commercial P&C, personal P&C, reinsurance).
  • Life: new premium, annuity sales, pension risk transfer volume.
  • Retention rate on renewals (P&C) / persistency rate (Life).

2F.5 Reserves adequacy — loss-development triangles, prior-year reserve
development (positive = releases, negative = strengthening).

2F.6 ROE, dividend track, buyback pace. Capital ratios (RBC / Solvency II
equivalents / HKIA RBC for APAC insurers).
"""


# ── Biopharma ──────────────────────────────────────────────────────────────

_BIOPHARMA_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 PIPELINE (MANDATORY — enumerate each asset in development):
For each asset: NAME | INDICATION | PHASE (preclin/Ph1/Ph2/Ph3/Filed/Approved)
| THERAPEUTIC AREA (Oncology/CNS/Rare/Hematology/Metabolic/CV/Immunology/
Other) | CONSENSUS PEAK SALES $B | EXPECTED LAUNCH YEAR | KEY COMPETITORS.
Label internal-origin vs in-licensed/acquired assets.

2F.2 REGULATORY MILESTONES NEXT 12M: upcoming PDUFA dates, advisory
committee meetings, expected Ph3 readouts. For each: date + expected
outcome + base/bear/bull stock impact.

2F.3 LOE / PATENT CLIFF: % of current revenue at risk through next 5
years. Named assets losing exclusivity + year. Defensive strategy
(formulation pivots, IP extensions, generic partnerships).

2F.4 R&D PRODUCTIVITY: R&D spend / revenue (%), R&D spend per new Ph3
readout, per-NME cost (industry benchmark ~$2.6B).

2F.5 COMMERCIAL INFRASTRUCTURE: US vs international sales force size,
payer mix (Medicare / commercial / VA / international), rebate pressure.

2F.6 MANAGEMENT GUIDANCE: FY revenue + EPS guidance, R&D spend guidance,
peak-sales estimates cited in earnings calls for top 3 pipeline assets.
"""


# ── Tech / SaaS sub-profiles ───────────────────────────────────────────────

_TECH_HYPERSCALER_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 CLOUD / AI REVENUE breakout:
  • Cloud revenue (Azure / AWS / GCP) growth YoY.
  • AI-specific revenue or "AI-attached" workload revenue growth.
  • Constant-currency growth separately.

2F.2 AI CAPEX vs CLOUD REVENUE CAPTURE — the core valuation debate:
  • Annual AI capex $B committed, next 3 years guided.
  • GPU capacity installed + delivered (H100/H200/B200 if disclosed).
  • Capex splits (datacenter / networking / chips / other).
  • CLOUD REVENUE CAPTURE: cloud revenue $ growth ÷ capex $ = revenue-
    per-$-of-capex signal. This is what markets ask: "Is the H100 Capex
    translating into matching cloud revenue growth?" Report the ratio
    and its trajectory over last 4 quarters.
  • Free cash flow impact: capex / operating cash flow, and whether FCF
    is still growing in $B terms or being absorbed by capex.

2F.3 CORE FRANCHISE KPIs (per company):
  MSFT: commercial bookings growth, Office 365 seats, LinkedIn revenue.
  GOOGL: search revenue, YouTube ads, YouTube subs.
  AMZN: AWS margin, 1P retail growth, advertising revenue.
  META: DAU, ads pricing (vs impressions), Reality Labs losses.
  ORCL: OCI revenue growth, fusion ERP/CRM migration.

2F.4 OPERATING LEVERAGE: operating margin trajectory net of AI capex
absorption. SBC as % of revenue (dilution signal).

2F.5 CAPITAL RETURN: buyback pace, dividend yield (where applicable),
payout ratio.

2F.6 REGULATORY: DOJ/FTC antitrust proceedings, EU DMA compliance,
China tech rivalry.
"""


_TECH_MATURE_SAAS_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 ARR (absolute $B + QoQ growth), subscription bookings growth YoY.
Organic ARR growth separated from acquired.

2F.2 NET REVENUE RETENTION (NRR) — most recent disclosed quarter.
Look for phrases: "NRR", "net dollar retention", "dollar-based net retention",
"$NRR", "net expansion rate", "ACV retention rate" (Salesforce uses this),
"customer retention revenue", "recurring revenue retention". If the company
reports under a non-standard name, map it to NRR. Benchmark vs 110-120%
healthy, <105% deterioration signal. If not directly disclosed: derive from
management cohort commentary on expansion vs churn.

Gross retention separately: look for "gross retention", "gross dollar
retention", "logo retention", or compute as NRR − expansion% when both
are cited. Mature SaaS gross retention typically 90-95% (vs Growth SaaS 95%+).

2F.3 RULE OF 40: Revenue Growth % + FCF Margin % = Rule of 40 score.
>60 = best-in-class, 40-60 = healthy, <40 = value question.

2F.4 POST-SBC FCF (MANDATORY — the true shareholder-economic FCF):
  • Reported FCF (post-capex) $M.
  • Stock-based compensation (SBC) $M.
  • POST-SBC FCF = reported FCF − SBC. Report as $M and as % of revenue.
  • Dilution rate = SBC / market cap (annualized). Markets increasingly
    value SaaS on post-SBC economics.

2F.5 OPERATING LEVERAGE: non-GAAP operating margin, path to GAAP
margin (reconciliation).

2F.6 CUSTOMER METRICS: enterprise customer count growth, $1M+ ACV
customer count, net-new logo count.

2F.7 AI PRODUCT MONETIZATION: specific AI SKU pricing, AI-attached ARR
%, AI seat attach rate to core product.

2F.8 SBC as % of revenue (dilution signal), organic R&D, M&A pipeline.
"""


_TECH_GROWTH_SAAS_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 ARR growth (absolute + % QoQ + constant currency where relevant).
Net new ARR quarterly trajectory (acceleration / deceleration).

2F.2 NRR (most recent) — growth SaaS benchmarks 115%+ pre-efficiency pivot.
Gross retention ≥95%.

2F.3 RULE OF 40 — calculate and score. Growth SaaS should be 50-70.

2F.4 UNIT ECONOMICS (MANDATORY):
  • CAC Payback (months) — target <18 months mature, 24-36 for expansion.
  • MAGIC NUMBER (net new ARR / S&M spend) — target >1.0.

  • LTV/CAC RATIO — follow this exact protocol:

    Step 1 — FULLY-LOADED CAC: Divide total Sales & Marketing (S&M) spend
    by NET NEW LOGOS (not the total customer base). If logo count is
    unavailable, use S&M / Net New ARR to find the "CAC Ratio."

    Step 2 — LTV CALCULATION: Use the formula
      LTV = (Blended ACV × Subscription Gross Margin) / Annual Revenue Churn

    Step 3 — LOGIC CHECK: Calculate the Payback Period
      Payback = CAC / (ACV × Margin)
    If the resulting LTV/CAC exceeds 10x or the Payback is under 6
    months, flag it as a potential "outlier" and re-calculate using
    ONLY the "Enterprise" cohort (i.e. swap blended ACV + new-logo
    count for the enterprise-cohort-specific ACV + enterprise net new
    logo count, since enterprise S&M intensity is much higher per logo
    than SMB).

    Step 4 — OUTPUT: Provide a step-by-step math table with the inputs
    and intermediate values:
      | Metric              | Value | Source |
      | S&M spend (period)  | $X    | 10-K   |
      | Net New Logos       | N     | IR     |
      | Fully-Loaded CAC    | S&M/N | computed |
      | Blended ACV         | $Y    | revenue / customers |
      | Subscription GM %   | Z%    | non-GAAP |
      | Annual Revenue Churn| C%    | 1 − gross retention |
      | LTV                 | (Y×Z)/C | computed |
      | LTV/CAC             | LTV/CAC | computed |
      | Payback (months)    | CAC/(Y×Z)×12 | computed |
    Then a brief 1-2 sentence VERDICT on whether the company is
    under-investing in growth relative to its efficiency (high LTV/CAC
    + long payback = under-spending on S&M; low LTV/CAC + short payback
    = over-spending or churning). If you triggered the Step-3 outlier
    re-calculation, present BOTH the blended and enterprise-cohort
    rows so the reader can see the cohort effect.

  • NRR ÷ Gross Retention split: if NRR=115% but Gross Retention=85%,
    business is reliant on expansion to offset 15% churn.

2F.5 CUSTOMER growth: $100k+ ACV customer count growth, enterprise
mix trending.

2F.6 CASH RUNWAY (if not FCF positive): quarters of cash at current
burn, path to profitability.

2F.7 SBC as % of revenue (growth SaaS often 20-30%, dilution signal).
Also report POST-SBC FCF = FCF − SBC as % of revenue.
"""


_TECH_GENERIC_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 Revenue growth YoY (organic vs acquired).

2F.2 Gross margin trajectory (mix / pricing power signal).

2F.3 Operating margin — GAAP + non-GAAP reconciliation if different.

2F.4 Product/segment mix — top 3 product lines % of revenue.

2F.5 Geographic mix (US / intl).

2F.6 Capital allocation: R&D % of revenue, SBC % of revenue, capex, buybacks.

2F.7 Management guidance for FY+1 revenue and operating margin.
"""


# ── Generic fallback ───────────────────────────────────────────────────────

_GENERIC_KPI_PROMPT = _SECTION_2F_HEADER + """
2F.1 The anchor KPI — the single number that best predicts this company's
revenue 12 months forward (e.g. Bookings / Backlog / GMV / AUM / MWh
contracted / NRR / ARR / same-store-sales / pipeline $). Trend over last
6 quarters. Consensus expectation vs your expectation. Why they differ.

2F.2 The leading indicator — the metric that predicts the anchor KPI 2-3
quarters in advance (e.g. web traffic, trial signups, pilot contract
count, permit filings, IEA demand data). Current reading and implication.

2F.3 The margin indicator — the metric that best predicts EBITDA margin
12 months forward (e.g. mix shift %, utilisation rate, headcount per $M
revenue, gross retention, take rate, MLR, hedge ratio).

2F.4 The risk indicator — the early warning signal for competitive
deterioration. Current reading and threshold that would trigger re-evaluation.

2F.5 Industry data sources — the 3-5 best external data sources specific
to this industry.

2F.6 Management Guidance & Forward Estimates — extract quantitative forward
guidance from earnings calls, investor presentations, or press releases.
Report as exact dollar figures:
  • FY revenue guidance: $XX.XB - $XX.XB (midpoint $XX.XB)
  • FY EBITDA guidance: $XX.XB - $XX.XB (midpoint $XX.XB)
  • Capex guidance: $XX.XB
  • Margin targets: XX% - XX%
If no explicit guidance is available, state "No quantitative guidance found"
and note the date of the most recent earnings call.
"""


# ── Routing table — (sector, profile_name) → prompt block ──────────────────

_SECTOR_PROFILE_PROMPTS: dict[tuple[str, str], str] = {
    # ── RealEstate / REIT ──
    # Note: profile_name "R.E.I.T." is used in TICKER_SECTOR_LOOKUP for every US
    # REIT including data centers (DLR, EQIX), net-lease (O, ADC), industrial
    # (PLD), healthcare (WELL), etc. — so it's a GENERIC REIT key here.
    # Finer-grained selection happens via _REIT_SUBTYPE_SPECIALIZATIONS (below),
    # which takes priority when reit_subtype is passed to get_kpi_prompt().
    ("RealEstate", "R.E.I.T."):                 _REIT_KPI_PROMPT,
    ("RealEstate", "Retail (REITs)"):           _REIT_KPI_PROMPT,
    ("RealEstate", ""):                         _REIT_KPI_PROMPT,  # fallback for unspecified
    ("REIT",       ""):                         _REIT_KPI_PROMPT,  # SGX sector alias

    # ── Financials — banks (all 10 sub-profiles in _BANK_PROFILE_CALIBRATION) ──
    ("Financials", "Money Center Bank"):        _BANK_KPI_PROMPT,
    ("Financials", "Money Center Bank (EU)"):   _BANK_KPI_PROMPT,
    ("Financials", "Money Center Bank (SG)"):   _BANK_KPI_PROMPT,
    ("Financials", "Regional Bank"):            _BANK_KPI_PROMPT,
    ("Financials", "Super-Regional Bank"):      _BANK_KPI_PROMPT,
    ("Financials", "EM Bank"):                  _BANK_KPI_PROMPT,
    ("Financials", "EM Bank (Premium)"):        _BANK_KPI_PROMPT,
    ("Financials", "Investment Bank"):          _BANK_KPI_PROMPT,
    ("Financials", "Brokerage"):                _BANK_KPI_PROMPT,
    ("Financials", "Neo/Challenger"):           _BANK_KPI_PROMPT,
    ("Financials", "Mortgage/GSE"):             _BANK_KPI_PROMPT,

    # ── Financials — non-banks ──
    ("Financials", "Asset Manager"):            _ASSET_MANAGER_KPI_PROMPT,
    ("Financials", "Alt Asset Manager"):        _ASSET_MANAGER_KPI_PROMPT,
    ("Financials", "Insurance"):                _INSURANCE_KPI_PROMPT,
    ("Financials", "Payment Networks"):         _PAYMENT_NETWORK_KPI_PROMPT,
    ("Financials", "Fintech/Stablecoin"):       _PAYMENT_NETWORK_KPI_PROMPT,
    ("Financials", "Market Infrastructure"):    _PAYMENT_NETWORK_KPI_PROMPT,

    # ── Biopharma ──
    ("Biopharma",  "Drugs (Pharmaceutical)"):   _BIOPHARMA_KPI_PROMPT,
    ("Biopharma",  "Drugs (Biotechnology)"):    _BIOPHARMA_KPI_PROMPT,
    ("Biopharma",  "Healthcare Products"):      _BIOPHARMA_KPI_PROMPT,
    ("Biopharma",  "LifeSciTools"):             _BIOPHARMA_KPI_PROMPT,

    # ── Tech / SaaS ──
    ("Tech",       "Hyperscaler / Tech Conglomerate"): _TECH_HYPERSCALER_KPI_PROMPT,
    ("Tech",       "Mature Platform"):                 _TECH_MATURE_SAAS_KPI_PROMPT,
    ("Tech",       "Mature SaaS"):                     _TECH_MATURE_SAAS_KPI_PROMPT,
    ("Tech",       "Growth SaaS"):                     _TECH_GROWTH_SAAS_KPI_PROMPT,
    ("Tech",       "Cybersecurity / Mission-Critical SaaS"): _TECH_GROWTH_SAAS_KPI_PROMPT,
    ("Tech",       "Hyper-Growth Platform"):           _TECH_GROWTH_SAAS_KPI_PROMPT,
    ("Tech",       "High-Growth Tech / AI"):           _TECH_GROWTH_SAAS_KPI_PROMPT,
    ("Tech",       "Early Platform"):                  _TECH_GROWTH_SAAS_KPI_PROMPT,
    ("Tech",       "Levered Subscription"):            _TECH_GENERIC_KPI_PROMPT,
    ("Tech",       ""):                                 _TECH_GENERIC_KPI_PROMPT,

    # ── Sector fallbacks (used when profile_name is empty/unknown) ──
    ("Financials", ""):                                 _GENERIC_KPI_PROMPT,
    ("Biopharma",  ""):                                 _BIOPHARMA_KPI_PROMPT,
}


# REIT sub-type specialization — when the classifier identifies a specific
# sub-type (data_center, net_lease) we can further specialize on top of the
# (sector, profile) key.
_REIT_SUBTYPE_SPECIALIZATIONS: dict[str, str] = {
    "data_center":         _REIT_DATA_CENTER_KPI_PROMPT,
    "data_center_premium": _REIT_DATA_CENTER_KPI_PROMPT,
    "net_lease":           _REIT_NET_LEASE_KPI_PROMPT,
}


def get_kpi_prompt(
    sector: str,
    profile_name: str = "",
    reit_subtype: str | None = None,
) -> str:
    """
    Returns the matched 2F KPI prompt block for the given classification.

    Lookup priority:
      1. REIT sub-type specialization (if sector is RealEstate/REIT and
         reit_subtype is a specialized sub-type like "net_lease")
      2. Exact (sector, profile_name) match
      3. Sector-only fallback (first matching sector, any profile)
      4. Generic fallback

    Callers pass sector + profile_name from state["data"]. reit_subtype
    is optional — only populated when REIT classifier has run.
    """
    # Tier 1: REIT sub-type specialization
    if reit_subtype and sector in {"RealEstate", "REIT"}:
        specialized = _REIT_SUBTYPE_SPECIALIZATIONS.get(reit_subtype)
        if specialized:
            return specialized

    # Tier 2: exact (sector, profile) match
    key = (sector or "", profile_name or "")
    if key in _SECTOR_PROFILE_PROMPTS:
        return _SECTOR_PROFILE_PROMPTS[key]

    # Tier 3: sector-only fallback (first matching sector regardless of profile)
    for (sec, _), block in _SECTOR_PROFILE_PROMPTS.items():
        if sec == sector:
            return block

    # Tier 4: generic fallback
    return _GENERIC_KPI_PROMPT


# ── Public loose-match sector helpers ─────────────────────────────────────────
#
# The LLM-driven classifier emits sector as a free string. TICKER_SECTOR_LOOKUP
# returns canonical forms ("Biopharma", "Tech", "Financials", "RealEstate"),
# but unknown tickers fall through to LLM inference where variants are common
# ("Biotechnology", "Pharmaceuticals", "Technology", "Software", "Information
# Technology", "Banking", "Real Estate", "REIT", …). Strict equality gates
# silently mis-route those — observed on MRNA: stored sector was "Biopharma"
# on the frontend but the extractor gate ran BEFORE normalisation against the
# LLM variant, so pipeline_assets was skipped and the panel was empty.
#
# These helpers mirror the frontend `isBiopharmaSector` / `isTechSector`
# helpers in `app/frontend/src/lib/utils.ts` so backend and frontend gates
# route identically. Callers that previously wrote `sector == "Biopharma"`
# should switch to `is_biopharma_sector(sector)` to avoid silent drops.


def is_biopharma_sector(sector: str) -> bool:
    """Match 'Biopharma', 'Biotechnology', 'Biotech', 'Pharmaceuticals',
    'Healthcare/Biotech', etc. — any string containing one of the root tokens."""
    s = (sector or "").lower()
    return "biopharm" in s or "biotech" in s or "pharmaceutical" in s


def is_tech_sector(sector: str) -> bool:
    """Match 'Tech', 'Technology', 'Software (...)', 'Information Technology',
    'IT Services'. Canonical form 'Tech' matches too."""
    s = (sector or "").lower()
    return (
        s == "tech" or s == "technology" or "software" in s
        or "information technology" in s or s == "it" or "it services" in s
    )


def is_bank_sector(sector: str) -> bool:
    """Match 'Financials', 'Financial Services', 'Banking', 'Banks'.
    Note: also returns True for insurance/asset-management — callers that need
    bank-only should additionally check profile_name for 'Bank'."""
    s = (sector or "").lower()
    return "financial" in s or "bank" in s


def is_reit_sector(sector: str) -> bool:
    """Match 'RealEstate', 'Real Estate', 'REIT', 'REITs', 'Property Trust'."""
    s = (sector or "").lower()
    return "realestate" in s.replace(" ", "") or "reit" in s or "property trust" in s


def needs_extractor(
    extractor: str,
    sector: str,
    profile_name: str = "",
    ticker: str = "",
) -> bool:
    """
    Returns True if the given extractor should run for (sector, profile_name).

    Used by the parallel extractor fan-out in deep_research.py to skip
    extractors that will almost certainly return {} (saves 800 tokens per
    skipped extractor × 6 extractors × N tickers).

    Universal extractors (always run):
      - dcf_calibration
      - segment_scenarios

    The ticker parameter (optional) provides a last-resort fallback for the
    saas_metrics gate: if profile_name is empty (because strategic_router's
    profile pre-classification block failed silently), we consult
    TICKER_SECTOR_LOOKUP directly. Without this, Tech extractors silently
    skip whenever profile_name doesn't populate — producing empty panels on
    the UI.
    """
    # Normalise inputs — the LLM-driven classifier emits the sector as a free
    # string that may be the canonical form ("Biopharma", "Tech", "Financials",
    # "RealEstate") OR a loose variant ("Biotechnology", "Biotech",
    # "Pharmaceuticals", "Healthcare/Biotech", "Technology", "Software",
    # "Banking", "Real Estate", "REIT", …). Using strict equality dropped
    # extractor runs silently whenever the LLM preferred a variant — observed
    # on MRNA where sector ended up normalised to "Biopharma" in stored data
    # but the pre-storage classification had produced "Biotechnology", so the
    # pipeline_assets extractor was skipped and the frontend rendered an empty
    # Pipeline Assets table. Fix: match on a case-insensitive root-token set
    # for each sector family. Belt-and-suspenders — TICKER_SECTOR_LOOKUP hits
    # already return canonical strings, but unknown tickers fall through to
    # LLM classification where variants are common.
    if extractor in {"dcf_calibration", "segment_scenarios"}:
        return True
    if extractor == "reit_metrics":
        return is_reit_sector(sector) or "REIT" in (profile_name or "")
    if extractor == "bank_metrics":
        # bank_metrics already gates on sector only (profile_name is a
        # tightening filter, not required) — leave as-is.
        return is_bank_sector(sector) and (
            "Bank" in (profile_name or "") or
            profile_name in {"Mortgage/GSE", "Brokerage"}
        )
    if extractor == "pipeline_assets":
        # pipeline_assets gates on sector only — leave as-is.
        return is_biopharma_sector(sector)
    if extractor == "saas_metrics":
        _is_saas_profile = is_tech_sector(sector) and profile_name not in {"", "Levered Subscription"}
        if not _is_saas_profile and ticker:
            # Last-resort fallback when strategic_router's profile
            # pre-classification failed silently: consult the canonical
            # ticker→sector→profile lookup directly.
            try:
                from src.data.sector_profiles import TICKER_SECTOR_LOOKUP
                _entry = TICKER_SECTOR_LOOKUP.get(ticker.upper())
                if _entry and is_tech_sector(_entry[0]) and _entry[1] in {
                    "Hyperscaler / Tech Conglomerate", "Mature SaaS", "Growth SaaS",
                    "Cybersecurity / Mission-Critical SaaS",
                }:
                    _is_saas_profile = True
            except Exception:
                pass
        return _is_saas_profile
    # Unknown extractor — conservative default: run
    return True
