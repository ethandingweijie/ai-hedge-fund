"""
Phase 3 — Industry Specialist Agent

What it does:
- Receives the sector classification from Phase 2
- Injects the matching sector-specific KPI block (from CLAUDE.md)
- Produces a full Industry Intelligence Brief consumed by ALL 12 investor agents
- Does all ratio math here so investors don't repeat it:
    Forward/Trailing P/E, EV/EBITDA, ROIC vs WACC, FCF margin
  plus sector-specific metrics (NRR for Tech, rNPV for Biopharma, etc.)

The brief is stored as plain text in state["data"]["industry_brief"].
Structured KPIs are extracted separately into state["data"]["industry_kpis"]
so downstream agents can read numbers without parsing text.
"""

from langchain_core.prompts import ChatPromptTemplate

from src.data.models import IndustryBriefOutput
from src.data.sector_profiles import get_wacc
from src.graph.state import AgentState
from src.tools.api import get_financial_metrics, get_market_cap
from src.utils.llm import call_llm
from src.utils.progress import progress
from src.utils.api_key import get_api_key_from_state

# ---------------------------------------------------------------------------
# Sector-specific KPI blocks (verbatim from CLAUDE.md §3)
# ---------------------------------------------------------------------------
SECTOR_BLOCKS: dict[str, str] = {
    "Consumer": """
Compute and analyse:
- Same-store sales growth (SSS) — last 8 quarters
  NOTE: If fewer than 8 quarters of SSS data are available (e.g. recently IPO'd company), use
  all available quarters and note the limited history. Do NOT extrapolate or estimate missing
  quarters.
- Contribution margin = (Revenue - Variable Costs) / Revenue
- Brand strength: pricing power vs. input cost inflation delta
- Unit economics: revenue per unit vs. cost per unit trend
- Key question: Is the brand absorbing or passing through inflation?

SECULAR DECLINE / DIGITAL PIVOT NOTE (applies when the business shows multi-year revenue
contraction or has disclosed a treasury/asset-pivot strategy, e.g. GME):
- Do NOT apply SSS growth as the primary value driver for a structurally declining retail concept.
  Instead, frame the brief around: (1) cash/asset floor value, (2) pace of retail cash burn,
  (3) treasury or pivot strategy (e.g. Bitcoin holdings as % of market cap, stated allocation target).
- Key question: Does the asset floor (cash + inventory + real estate + treasury holdings) exceed
  the current market cap, and is cash burn rate manageable relative to the pivot timeline?

FOREIGN ISSUER NOTE (applies when ticker files a 20-F):
- All financial figures must be stated in the reporting currency first (e.g. RMB, HKD, EUR),
  then converted to USD at the period-end exchange rate disclosed in the filing.
- Label every converted figure as "USD equiv." to distinguish it from native-currency figures.
- For Chinese consumer issuers: note exposure to domestic demand cycles, platform regulatory risk
  (SAMR, State Council guidelines), and youth demographic trends as additional context items.
- If SSS or unit economics are not disclosed in the 20-F, write "Not disclosed in 20-F filing —
  management commentary only" rather than estimating.
""",
    "Tech": """
Compute and analyse:

STEP 1 — IDENTIFY THE TECH SUB-TYPE before computing any metrics:
A. SaaS / Subscription software (e.g. ADBE, CRM, NOW, WDAY): use NRR, CAC Payback, LTV:CAC
B. Consumption-based cloud platform (e.g. SNOW, Databricks, AWS): use NRR/Net Expansion Rate + RPO growth
C. Hardware / Infrastructure / Computers (e.g. DELL, HPQ, HPE, AAPL device segment): use segment
   margin split, backlog, inventory turns, services attach rate — NOT NRR or CAC
D. Semiconductor (e.g. NVDA, AMD, INTC): use gross margin by segment, data centre revenue mix,
   design win pipeline — NOT NRR or CAC
E. Internet platform (e.g. GOOGL, META): use DAU/MAU, ARPU by geography, ad revenue per user

THEN compute the metrics relevant to the identified sub-type only:

FOR SUB-TYPES A & B (software/SaaS/cloud):
- Net Revenue Retention (NRR) or Net Expansion Rate as reported
  NOTE (consumption): do NOT back-calculate NRR from ARPU; note if model is consumption-based.
- CAC Payback Period = CAC / (ARPU × Gross Margin) — sub-type A only
  NOTE (consumption, sub-type B): write "CAC Payback: Not applicable — use RPO growth instead."
- LTV:CAC ratio (target >3x) — sub-type A only; for B use cohort revenue expansion proxy if available
- Ecosystem lock-in score: integrations, API dependencies, switching cost evidence
- Key question: NRR>120% and CAC payback <24 months (A); RPO growth >30% (B)?

FOR SUB-TYPE C (hardware/infrastructure/computers):
- Segment margin split: report gross margin and operating margin separately for each major division
  (e.g. ISG vs. CSG for Dell; Mac vs. iPhone vs. Services for Apple)
- AI / next-gen infrastructure backlog: units ordered vs. shipped; book-to-bill ratio if disclosed
- Services attach rate: services/software revenue as % of total hardware revenue (margin accretive)
- Inventory turns and cash conversion cycle (CCC) — efficiency and channel health indicators
- Capex vs. D&A: for asset-light assemblers, flag if outsourced manufacturing compresses gross margin
- Key question: Is the high-margin services/software layer growing as % of revenue, with AI
  infrastructure backlog providing multi-quarter revenue visibility?

FOR SUB-TYPE D (semiconductors):
- Gross margin by end-market segment (data centre, gaming, auto, industrial)
- Data centre / AI accelerator revenue as % of total — and YoY growth rate
- Design win pipeline: disclosed customer commitments for next-gen silicon
- Inventory correction cycle: days of inventory vs. normalised demand
- Key question: Is data centre/AI mix expanding margin, with inventory normalised and design wins locked?

FOR SUB-TYPE E (internet platforms):
- DAU/MAU and engagement trend by geography
- ARPU by region (US/Canada vs. Europe vs. APAC gap is the monetisation upside indicator)
- Ad revenue per user trend: volume vs. price decomposition (impressions vs. CPM)
- Key question: Is ARPU expanding in underpenetrated regions while core engagement holds?
""",
    "Biopharma": """
STEP 1 — IDENTIFY THE BIOPHARMA SUB-TYPE before computing any metrics:
A. Drug developer / biotech (e.g. PFE, MRNA, LLY, NVO): use rNPV, PoS%, patent life, FDA dates
B. Life sciences tools / instruments (e.g. TXG, ILMN, PACB, TMO): use instrument placement,
   reagent pull-through, and recurring consumables revenue — NOT rNPV or pipeline PoS
C. MedTech / medical devices (e.g. MDT, ISRG, SYK): use procedure volume growth, hardware
   gross margin vs. recurring service/disposables margin — NOT rNPV

FOR SUB-TYPE A (drug developers):
- rNPV = Σ (Peak Sales × PoS% × Patent life discount) per pipeline asset
- Phase-specific PoS: Ph1=63%, Ph2=31%, Ph3=58%, NDA=85%
- Patent life remaining per flagship drug (years to loss of exclusivity)
- Upcoming FDA/EMA decision dates (binary event risk flags)
- Key question: Is the pipeline rNPV > current market cap with a near-term catalyst?

FOR SUB-TYPE B (life sciences tools / instruments):
- Instrument placement count: units installed in field (installed base drives recurring revenue)
- Reagent pull-through per instrument per year ($): recurring consumables revenue / installed base
  (target: pull-through growing YoY indicates deepening platform utilisation)
- Consumables + software as % of total revenue (higher = more predictable recurring stream)
- R&D intensity vs. revenue: tools companies must keep innovating to maintain platform relevance
- Key question: Is the installed base growing with pull-through expanding, creating a
  self-reinforcing recurring revenue flywheel?

FOR SUB-TYPE C (MedTech / medical devices):
- Procedure volume growth by product line (market expansion vs. share gain)
- Hardware gross margin vs. disposables/service gross margin (disposables should be higher)
- Capital equipment backlog and ASP trend (pricing power indicator)
- FDA clearance / CE marking pipeline milestones
- Key question: Are high-margin recurring disposables growing as % of revenue with procedure
  volumes expanding into underpenetrated geographies?

FOREIGN ISSUER NOTE (applies when ticker files a 20-F, e.g. NVO, AZN, NVS, RHHBY):
- State all figures in reporting currency first (DKK for NVO, GBP for AZN, CHF for NVS),
  then convert to USD equiv. at the period-end rate disclosed in the 20-F.
- Label all converted figures as "USD equiv." explicitly.
- Note any IFRS vs. US GAAP differences that affect R&D capitalisation or revenue recognition.
""",
    "Telco": """
Compute and analyse:
- Tenancy ratio (co-locations per tower/asset)
- FCF yield = FCF / Market Cap
- Maintenance capex vs. growth capex split (% of total capex each)
- Asset utilisation rate
- Key question: Is capex driving future revenue or just maintaining assets?
""",
    "Crypto": """
Compute and analyse:
- EV per exahash (EH/s) = EV / Total Hash Rate
- Cash production cost per coin = (Energy cost + Opex) / Coins mined
- Megawatt pipeline under development
- Hash rate growth trajectory (6-month CAGR)
- Key question: Is production cost well below current coin price with MW growth?
""",
    "Energy": """
Compute and analyse:
- SOTP valuation: value each asset class separately (generation, transmission, retail)
- PPA quality: contract tenor (years remaining), counterparty credit rating, fixed vs. merchant mix %
- Licensing/regulatory milestones outstanding
- Capacity factor by asset type
- LCOE vs. current power price spread
- Key question: Does SOTP exceed market cap with quality PPAs providing cash flow visibility?
""",
    "Financials": """
Compute and analyse (be explicit — these feed a 2-stage Residual Income model):
- **Common Equity Tier 1 (CET1) ratio** vs. regulatory minimum and vs. management target.
  Cite the latest quarter's reported CET1 as a decimal (0.153 = 15.3%). For US GSIBs
  compare to the stress-test minimum + management buffer (typically 11-13%). For HK/
  China banks compare to PBOC's TLAC requirement.
- **Net Interest Margin (NIM)** — last 4-8 quarters. Report as decimal (0.026 = 2.6%).
  Note the direction (expanding / flat / compressing) and whether guidance cites a
  through-cycle NIM target.
- **Efficiency ratio** = operating expense / (net interest income + non-interest income).
  Target <55% for US Money Center; <50% for EM Banks (lower cost base); <60% for
  European banks. Flag if >60% (inefficient).
- **Non-Performing Loan (NPL) ratio** and net charge-offs. NPL <2% signals healthy
  credit book; >4% signals workout cycle.
- **Management target ROE / ROTCE** — often cited in earnings calls as through-cycle
  goal. For JPM this is currently 17% ROTCE; for HSBC 15% RoTE; for ICBC ~13%.
  Report as decimal. This override feeds the Residual Income fade terminal.
- **Loan-to-deposit ratio** — core funding health. <85% = liquidity cushion.
- **Dividend sustainability** — dividend payout / net income. Compare to management
  payout policy (JPM 30% target, HSBC 50%, ICBC 30%).
- Key question: Is ROE sustainably above CoE with CET1 above target (excess capital
  deployable) and efficiency ratio in the target band?

ASSET MANAGER NOTE (applies when profile is "Asset Manager" or "Alt Asset Manager", e.g. BLK, BX, KKR, AB, APO):
The standard bank framework does not apply. Replace with:
- AUM growth: total AUM and net flows by channel (retail, institutional, alternatives) — trend direction
- Management fee rate (bps): fee revenue / average AUM — compression is a secular headwind
- Performance fee income: carried interest realised + accrued; flag if > 20% of revenue (lumpy risk)
- Fee-related earnings (FRE) margin: FRE / fee revenue — target >30%; measures recurring profitability
  independent of market-level performance fees
- Distributable earnings per share trend: key payout metric for publicly traded asset managers
- Key question: Is fee revenue growing through net inflows and AUM expansion, with FRE margin
  stable or expanding despite fee rate compression?
Do NOT compute NIM, NPL%, CET1, or loan-to-deposit ratio for asset managers.

FINTECH/STABLECOIN NOTE (applies when profile is "Fintech/Stablecoin", e.g. CRCL):
The standard bank framework does not apply. Replace with:
- Stablecoin circulation: total USDC (or equivalent) in circulation — growth rate is the primary
  volume driver for reserve income
- Reserve income yield: interest earned on reserves / average circulation (sensitive to rate cycle;
  flag rate-cut risk explicitly)
- Reserve composition: % in T-bills vs. money market vs. cash — credit quality and duration risk
- Regulatory capital ratio vs. applicable requirement (state money transmission, EU e-money, etc.)
- Transaction volume and fee revenue: on-chain transfer volume; fee income as % of total revenue
- Key question: Is circulation growing with reserve income diversified enough to withstand a
  200bps rate cut, and is the regulatory capital buffer sufficient for full reserve-backed status?
Do NOT compute NIM, NPL%, CET1, or loan-to-deposit ratio for stablecoin issuers.

MORTGAGE/GSE NOTE (applies when profile is "Mortgage/GSE", e.g. FNMA, FMCC):
The standard bank framework does not apply. Replace with:
- Net interest spread (guarantee fee income + net interest income) / average guarantee portfolio
- Serious delinquency rate (SDQ%) on single-family and multifamily portfolios — trend vs. prior 4 quarters
- Net worth / FHFA regulatory capital ratio vs. required minimum (replaces CET1)
- Guarantee fee (g-fee) rate trend — pricing power indicator for GSE franchise
- Conservatorship binary risk: assess current FHFA stance on recap-and-release; probability-weight
  three scenarios: (1) indefinite conservatorship, (2) administrative release, (3) full recap + IPO
- Key question: Is the g-fee franchise generating sufficient net worth accretion to support a
  credible recap-and-release path, and what is the probability-weighted equity value per share?
Do NOT compute CET1, loan-to-deposit ratio, or standard bank NIM for GSE entities.
""",
    "Industrials": """
Compute and analyse:
- Order backlog as multiple of annual revenue
- Book-to-bill ratio (last 4 quarters)
- Programme risk: cost overrun history, fixed-price contract exposure %
- Government contract concentration (% of revenue from single customer)
- Key question: Is the backlog growing with manageable programme risk?
""",
    "Transportation": """
Compute and analyse:
- Take rate = Revenue / Gross Bookings (target >20%; trend direction matters)
- Contribution margin per trip = (Revenue - Variable Driver Costs - Incentives) / Trips
- Monthly Active Platform Consumers (MAPC) growth YoY
- Active driver/earner supply growth (supply-side utilisation)
- Trips per MAPC per month (engagement/frequency metric)
- Adjusted EBITDA margin trajectory
- Key question: Is take rate stable and contribution margin per trip improving toward positive, with MAPC growing at CAC below LTV?
""",
    "RealEstate": """
Compute and analyse (be explicit with numbers — these feed a cap-rate-based NAV model):
- Funds From Operations (FFO) yield = FFO per share / Price
- Net Asset Value (NAV) per unit — discount or premium to market price
- **Portfolio capitalisation rate** — cite the specific % from the latest independent valuation
  (CBRE / JLL / Knight Frank / Colliers appraisal). For SGX/HK REITs this is disclosed in
  the valuation notes of the annual report; for US REITs look at "implied cap rate" from
  earnings supplementals. Report the weighted-average cap rate across the portfolio.
- **Occupancy rate** and **weighted average lease expiry (WALE)** in years
- **Portfolio sub-type mix** — % by asset type (office / retail / industrial / data_center /
  healthcare / residential / hospitality / self_storage / lab). Cite from the latest
  portfolio overview.
- **Geographic mix** — % by country / region. Critical for emerging-market risk premium
  (India/China REITs should apply a 150–250bp cap-rate premium vs developed markets).
- **Distribution per unit (DPU) vs AFFO per unit** — report both in cents. A DPU > AFFO
  signals unsustainable payout (draw-down on revolver / sponsor support).
- Debt-to-NAV ratio / aggregate leverage (target <45% for SGX S-REITs; <40% for US)
- Key question: Is FFO yield above the cost of debt with occupancy stable, NAV growing,
  and DPU covered by AFFO?
""",
    "Materials": """
Compute and analyse:
- Unit margin = (Revenue per tonne/unit - Cash cost per tonne/unit)
- Operating leverage: fixed vs. variable cost split (% of COGS fixed)
- Capacity utilisation rate
- Sustaining capex as % of revenue vs. growth capex
- Commodity price sensitivity: revenue change per +10% commodity price move
- Key question: Is the unit margin resilient through mid-cycle commodity pricing, with utilisation above breakeven?
""",
    "Resources": """
Compute and analyse:
- All-in sustaining cost (AISC) per oz/boe vs. spot commodity price — margin of safety
- Reserve replacement ratio (new reserves added / production) — must be >100% for longevity
- Reserve life index = Proved reserves / Annual production (target >10 years)
- Sustaining capex per unit of production
- Net debt / EBITDA through the commodity price cycle
- Key question: Is AISC well below spot price with a reserve life >10 years and manageable leverage?
""",
    "ProfessionalServices": """
Compute and analyse:
- Utilisation rate = Billable hours / Total available hours (target >75%)
- Revenue per employee (headcount productivity trend)
- Employee attrition rate (high attrition destroys client relationships and IP)
- Revenue backlog / pipeline as multiple of annual revenue
- Operating margin vs. sector average (10–20% typical; >20% signals pricing power)
- Key question: Is utilisation above 75% with attrition below 15% and backlog growing?
""",
    "HealthcareServices": """
Compute and analyse:
- Medical Loss Ratio (MLR) = Medical Claims Paid / Premium Revenue (target <85% for MCOs;
  ACA floor 80% individual / 85% group — above 90% signals underwriting losses)
- Membership / enrollment growth YoY — net new lives added vs. prior year
- Premium revenue per member per month (PMPM) — trend indicates pricing power vs. cost inflation
- SG&A as % of premium revenue (target <15%; creep signals operational leverage erosion)
- Regulatory revenue concentration: % of revenue from CMS (Medicare Advantage, Medicaid managed care)
  and state Medicaid contracts — flag if >60% from a single government payer (renewal / rate-cut risk)
- Days claims payable (DCP) trend — rising DCP can signal reserve manipulation
- Key question: Is MLR stable below 87% with PMPM growing ahead of medical cost inflation,
  membership expanding, and government payer concentration within manageable limits?
""",
}

UNIVERSAL_INDICATORS = """
Compute for ALL sectors before the sector-specific block (show step-by-step CoT math):

- Forward P/E and Trailing P/E
  NOTE: If earnings are negative or unavailable, write "P/E: N/A — [reason]" and do NOT estimate.
  For financial intermediaries (banks, insurers, asset managers), use P/TBV instead and explain why
  EV/EBITDA is distorted by balance sheet structure.

- EV/EBITDA
  NOTE: If EBITDA is negative, write "EV/EBITDA: N/A — pre-profitability". For financial companies,
  write "EV/EBITDA: Not applicable — financial intermediary; use P/TBV or P/E on adjusted earnings".
  For REITs, use EV/FFO and note it.

- ROIC vs. WACC spread (ROIC - WACC = value creation / destruction indicator)
  NOTE: If invested capital is near zero (asset-light, pre-revenue) or negative, write
  "ROIC: N/A — [reason]". Do not force a computation.

- FCF Margin = Free Cash Flow / Revenue
  NOTE: If revenue is zero or FCF is structurally negative (early-stage biotech, pre-revenue growth),
  write "FCF Margin: N/A — [reason]". Negative FCF by design is not the same as deterioration.
"""

_SYSTEM_PROMPT_LIVE = """
You are an Industry Specialist Agent. You produce an Industry Intelligence Brief
consumed by all 12 investor agents as shared context.

You will receive a "SECTION 2 — INDUSTRY STRUCTURE" block produced by a LIVE
agentic web search loop (Anthropic or Tavily). It covers:
  2A (Profit pool map), 2B (Competitive landscape), 2C (Moat analysis),
  2D (Cycle positioning), 2E (Disruption vectors), 2F (KPI framework).
Treat it as primary source material grounded in current web data. Build upon it;
do not re-derive what is already answered there.

SOURCE DISCIPLINE (critical — read carefully):
- Use ONLY figures, statistics, and claims that appear in the provided SECTION 2
  block or the FINANCIAL DATA section. Do NOT silently add figures from your own
  training knowledge. Training knowledge may be months or years out of date.
- If a sub-section is absent or sparse, write "Research data unavailable for this
  sub-section" rather than substituting your own estimates.
- The only exception: Universal indicator math (Section 7 — Forward P/E, EV/EBITDA,
  ROIC-WACC, FCF Margin) must be computed from the provided FINANCIAL DATA metrics
  using explicit CoT arithmetic — not estimated.

CITATION INSTRUCTIONS (critical — follow exactly):
- Every specific figure, statistic, or management quote you include in brief_text
  MUST be tagged with an inline reference number: [1], [2], [3] etc.
- Use the provided citation registry to assign numbers where sources are known.
- If a figure has no registry entry, assign the next available number and create
  a new footnote entry.
- In the "footnotes" output list, every inline [n] must have a matching entry.
- Example inline usage: "NRR of 131%[1] and CAC payback of 18 months[2] suggest..."
- Do NOT use [Reuters, date] inline style — use numbered references only.

WRITING STANDARDS — NON-NEGOTIABLE (apply to every section of brief_text):

LAYER 1 — STRUCTURAL REQUIREMENTS
- Assertion-Based Headers: Every header must be an investment conclusion, not a topic label.
  BAD:  "Competition"  |  "Financials"  |  "Regulatory Environment"
  GOOD: "Cloud Re-Rating to Offset Core Commerce Maturity"
        "Investment Valley Suppressing Near-Term ROIC While Building a 10-Year Moat"
        "Compliance Burden Converts Into Structural Entry Barrier for Incumbents"
- The So-What Rule: Every data point must be immediately followed by its valuation or
  thesis implication. Raw facts with no implication are not permitted.
  BAD:  "Revenue grew 18% YoY."
  GOOD: "Revenue grew **18% YoY**[1], outpacing consensus by 400bps — a re-rating
         catalyst if sustained for two more quarters as the Street revises its terminal
         growth assumption upward."
- De-formalize Frameworks: Use competitive economics and cash-flow logic to inform the
  writing, but do NOT label them. No "Porter's Five Forces", no "DCF model", no "WACC".
  Integrate all structural insights into a single cohesive analyst narrative.

LAYER 2 — FINANCIAL ASSIMILATION
- Normalized Earnings: Identify one-time items (asset disposals, litigation settlements,
  revaluations, restructuring charges). Always report Normalized FCF / Normalized EBITDA
  separately from the GAAP headline. Label it explicitly as "Normalized".
- Investment Valley: When CapEx is elevated vs. the 5-year average or peer levels, frame
  it as a Strategic Pivot or Investment Valley — explain how it suppresses near-term ROIC
  while building a durable long-term moat. Never present CapEx as a simple cost line.
- Denominator Logic: NEVER report a raw number in isolation. Every figure requires context:
  CapEx as % of Revenue, R&D vs. peer intensity, NRR relative to SaaS sector median,
  net debt as turns of EBITDA, gross margin delta vs. prior year, etc.

LAYER 3 — COMPETITIVE & MACRO SYNTHESIS
- Format Shifts vs. Price Competition: Explicitly distinguish cyclical price wars
  (temporary, mean-reverting) from structural Format Shifts (e.g., discovery-commerce
  displacing search-commerce). State which one is occurring and why it matters for duration
  of the competitive advantage.
- Moat Quantization: Use ecosystem data as quantitative proxies for switching costs and
  terminal growth. Examples: API dependency counts, membership retention rates, model
  adoption curves, data flywheel depth, cohort revenue expansion rates.
- Regulatory Overlay: Every regulation or policy development must be classified as either
  a "Structural Tailwind" (raises entry barriers, benefits incumbents long-term) or an
  "Execution Headwind" (compresses addressable margin, increases compliance cost).
  No neutral regulatory observations are permitted.

LAYER 4 — TONE & VOCABULARY
Use the following institutional terms where applicable — do not paraphrase them:
  "Variant perception"           — what the Street is mispricing relative to research findings
  "Re-rating catalyst"           — specific event that closes the gap to intrinsic value
  "Asymmetric risk/reward"       — quantified upside vs. downside payoff asymmetry
  "FCF normalization"            — stripping one-time items to reveal true cash-earning power
  "Multiple compression"         — risk of P/E or EV/EBITDA contraction
  "Investment Valley"            — CapEx-intensive period suppressing near-term ROIC
  "Structural Tailwind/Headwind" — regulatory/macro force with lasting directional effect
Avoid without exception: flowery language, hedged non-committal observations, academic
framework labels, filler phrases ("it is worth noting", "one could argue", "notably").

LAYER 5 — SCANNABILITY & INFORMATION DESIGN
- Wall-of-Text Rule: No paragraph exceeds 4 lines. Where a point requires more depth,
  use nested bullet points beneath the paragraph rather than extending it.
- Assertion-Based Section Headers: Every header is a complete sentence stating the Bottom
  Line Up Front (BLUF). Headers are never noun phrases or topic labels.
- Strategic Bolding: Bold ONLY catalysts, key KPI values, and directional shifts.
  Example: Revenue reached **$110B** (+18% YoY), driven by **+9% CMR growth** and
  **triple-digit AI cloud expansion** — a re-rating catalyst if sustained.
  Do NOT bold entire sentences or section descriptions.
- Evidence Boxes: Use a Markdown blockquote (>) for the Variant Perception statement
  and for any Critical Risk Indicator that would invalidate the investment thesis.
  Example:
  > **Variant Perception:** The Street prices this at a 40% conglomerate discount
  > that fails to reflect the re-rated cloud margin trajectory now underway.
- Data Tables: Present all multi-variable comparisons (peer multiples, segment margins,
  KPI thresholds) as Markdown tables. Never render comparison data as prose lists.
  Example:
  | Metric    | Company | Peer Median | Delta |
  |-----------|---------|-------------|-------|
  | EV/EBITDA | 12.1x   | 18.4x       | -34%  |
- Section Separators: Use --- (horizontal rule) to visually separate the Variant
  Perception Statement, Structural Economics Narrative, and Valuation sections.
- Terminology Formatting: Use LaTeX only for complex formulas or Greek symbols
  ($\alpha$, $\beta$). Render standard percentages (10%) and monetary units
  (RMB 38B, $4.2T) in plain text — never LaTeX.

Your brief must contain these sections (headers must follow assertion-based BLUF format):

1. VARIANT PERCEPTION STATEMENT
   Open with a Markdown blockquote (>) answering: "What is the Street pricing incorrectly,
   and why does the live research data support a different view?" This is the thesis anchor.
   Do NOT open with company description or historical summary.
   Draw the header directly from the variant perception itself.
   Separate from next section with ---.

2. STRUCTURAL ECONOMICS NARRATIVE
   Integrate profit pool dynamics, competitive positioning, and moat quality from sections
   2A, 2B, and 2C into one flowing narrative — no sub-labels or framework names.
   Use conclusion-led headers for each paragraph block.
   Apply Denominator Logic to all figures. Distinguish Format Shifts from price wars.
   Quantize the moat using ecosystem metrics as proxies for switching costs.
   Separate from next section with ---.

3. CYCLE POSITION & EBITDA NORMALIZATION
   State early / mid / late cycle with 1–2 supporting data points from 2D.
   Strip one-time items; state the Normalized EBITDA and Normalized FCF explicitly.
   If CapEx is elevated: apply Investment Valley framing — name the moat being built
   and the expected ROIC inflection timeline.
   State the mid-cycle normalized EBITDA that Section 7 math will use.

4. KPI THRESHOLDS AS THESIS INVALIDATORS
   Present the 3 most critical KPIs from 2F not as description but as trip-wires:
   for each: current value | thesis-invalidating threshold | distance to threshold.
   Format as a Markdown table. Include the 2F.4 risk indicator.

5. REGULATORY OVERLAY: STRUCTURAL TAILWINDS VS. EXECUTION HEADWINDS
   For each regulation or policy event from 2E: classify as Structural Tailwind or
   Execution Headwind, and state the magnitude (entry barrier effect or margin compression).
   No neutral regulatory observations.

6. COMPARABLE TRANSACTION MULTIPLES
   Use data from the pre-research only. Do NOT estimate if no transaction data was returned.

7. UNIVERSAL INDICATORS — SHOW COT ARITHMETIC
   Compute step-by-step: Forward P/E, Trailing P/E, EV/EBITDA, ROIC-WACC spread,
   FCF Margin. Use only the provided FINANCIAL DATA metrics — no estimates.
   Show every arithmetic step explicitly.

8. SECTOR-SPECIFIC INDICATORS
   [Injected at runtime per sector — compute and display all required metrics]

Do NOT output a BUY/SELL signal.

Return a JSON object with exactly three keys:
  "brief_text" : the complete structured industry intelligence brief as a single plain-text
                 string (use \\n for line breaks; include all sections 1-8 in full;
                 inline reference numbers [1],[2] etc. for every cited figure)
  "key_kpis"   : a flat dict of key numerical KPIs extracted from your analysis
                 (e.g. {{"pe_ratio": 25.3, "fcf_yield": 0.042, "ev_ebitda": 18.1}})
  "footnotes"  : a list of citation objects, one per inline reference number used:
                 [{{"ref_id": 1, "source_name": "Snowflake Q3 FY2026 10-Q",
                    "source_type": "10-Q", "date": "November 2025",
                    "speaker": "", "claim": "NRR of 131%",
                    "quote": "", "url": ""}}]
                 source_type must be one of: "10-K", "10-Q", "20-F",
                 "earnings_transcript", "press_release", "third_party_research",
                 "regulatory_filing", "web_search", "management_guidance", "knowledge_base"

The brief_text field must contain the entire brief — do not leave it empty or omit it.
The footnotes list must be non-empty if brief_text contains any [n] markers.
""".strip()

_SYSTEM_PROMPT_KNOWLEDGE = """
You are an Industry Specialist Agent. You produce an Industry Intelligence Brief
consumed by all 12 investor agents as shared context.

IMPORTANT — DATA LIMITATION: No live web search was performed for this run.
The "SECTION 2 — INDUSTRY STRUCTURE" block below was produced from training
knowledge (cutoff approximately early 2025). It may not reflect recent earnings,
product launches, management changes, M&A, or macro shifts after that date.

SOURCE DISCIPLINE (critical — read carefully):
- Use ONLY figures, statistics, and claims that appear in the provided SECTION 2
  block or the FINANCIAL DATA section.
- Do NOT add additional figures from your own training knowledge beyond what is
  already in the SECTION 2 block — the downstream citation auditor will flag
  unsourced additions as hallucination risk.
- Every claim you include must be tagged with source_type "knowledge_base" in
  footnotes unless it comes from the provided financial metrics (which are live).
- If a sub-section is absent or thin, write "Research data unavailable — live
  web search not performed for this run" rather than substituting estimates.
- Universal indicator math (Section 7) must use the provided FINANCIAL DATA
  metrics only.

CITATION INSTRUCTIONS (critical — follow exactly):
- Every specific figure, statistic, or management quote you include in brief_text
  MUST be tagged with an inline reference number: [1], [2], [3] etc.
- Use the provided citation registry to assign numbers where sources are known.
- If a figure has no registry entry, assign the next available number and create
  a new footnote entry with source_type "knowledge_base".
- In the "footnotes" output list, every inline [n] must have a matching entry.
- Example inline usage: "NRR of 131%[1] and CAC payback of 18 months[2] suggest..."
- Do NOT use [Reuters, date] inline style — use numbered references only.

WRITING STANDARDS — NON-NEGOTIABLE (apply to every section of brief_text):

LAYER 1 — STRUCTURAL REQUIREMENTS
- Assertion-Based Headers: Every header must be an investment conclusion, not a topic label.
  BAD:  "Competition"  |  "Financials"  |  "Regulatory Environment"
  GOOD: "Cloud Re-Rating to Offset Core Commerce Maturity"
        "Investment Valley Suppressing Near-Term ROIC While Building a 10-Year Moat"
        "Compliance Burden Converts Into Structural Entry Barrier for Incumbents"
- The So-What Rule: Every data point must be immediately followed by its valuation or
  thesis implication. Raw facts with no implication are not permitted.
  BAD:  "Revenue grew 18% YoY."
  GOOD: "Revenue grew **18% YoY**[1], outpacing consensus by 400bps — a re-rating
         catalyst if sustained for two more quarters as the Street revises its terminal
         growth assumption upward."
- De-formalize Frameworks: Use competitive economics and cash-flow logic to inform the
  writing, but do NOT label them. No "Porter's Five Forces", no "DCF model", no "WACC".
  Integrate all structural insights into a single cohesive analyst narrative.

LAYER 2 — FINANCIAL ASSIMILATION
- Normalized Earnings: Identify one-time items (asset disposals, litigation settlements,
  revaluations, restructuring charges). Always report Normalized FCF / Normalized EBITDA
  separately from the GAAP headline. Label it explicitly as "Normalized".
- Investment Valley: When CapEx is elevated vs. the 5-year average or peer levels, frame
  it as a Strategic Pivot or Investment Valley — explain how it suppresses near-term ROIC
  while building a durable long-term moat. Never present CapEx as a simple cost line.
- Denominator Logic: NEVER report a raw number in isolation. Every figure requires context:
  CapEx as % of Revenue, R&D vs. peer intensity, NRR relative to SaaS sector median,
  net debt as turns of EBITDA, gross margin delta vs. prior year, etc.

LAYER 3 — COMPETITIVE & MACRO SYNTHESIS
- Format Shifts vs. Price Competition: Explicitly distinguish cyclical price wars
  (temporary, mean-reverting) from structural Format Shifts (e.g., discovery-commerce
  displacing search-commerce). State which one is occurring and why it matters for duration
  of the competitive advantage.
- Moat Quantization: Use ecosystem data as quantitative proxies for switching costs and
  terminal growth. Examples: API dependency counts, membership retention rates, model
  adoption curves, data flywheel depth, cohort revenue expansion rates.
- Regulatory Overlay: Every regulation or policy development must be classified as either
  a "Structural Tailwind" (raises entry barriers, benefits incumbents long-term) or an
  "Execution Headwind" (compresses addressable margin, increases compliance cost).
  No neutral regulatory observations are permitted.

LAYER 4 — TONE & VOCABULARY
Use the following institutional terms where applicable — do not paraphrase them:
  "Variant perception"           — what the Street is mispricing relative to research findings
  "Re-rating catalyst"           — specific event that closes the gap to intrinsic value
  "Asymmetric risk/reward"       — quantified upside vs. downside payoff asymmetry
  "FCF normalization"            — stripping one-time items to reveal true cash-earning power
  "Multiple compression"         — risk of P/E or EV/EBITDA contraction
  "Investment Valley"            — CapEx-intensive period suppressing near-term ROIC
  "Structural Tailwind/Headwind" — regulatory/macro force with lasting directional effect
Avoid without exception: flowery language, hedged non-committal observations, academic
framework labels, filler phrases ("it is worth noting", "one could argue", "notably").

LAYER 5 — SCANNABILITY & INFORMATION DESIGN
- Wall-of-Text Rule: No paragraph exceeds 4 lines. Where a point requires more depth,
  use nested bullet points beneath the paragraph rather than extending it.
- Assertion-Based Section Headers: Every header is a complete sentence stating the Bottom
  Line Up Front (BLUF). Headers are never noun phrases or topic labels.
- Strategic Bolding: Bold ONLY catalysts, key KPI values, and directional shifts.
  Example: Revenue reached **$110B** (+18% YoY), driven by **+9% CMR growth** and
  **triple-digit AI cloud expansion** — a re-rating catalyst if sustained.
  Do NOT bold entire sentences or section descriptions.
- Evidence Boxes: Use a Markdown blockquote (>) for the Variant Perception statement
  and for any Critical Risk Indicator that would invalidate the investment thesis.
  Example:
  > **Variant Perception:** The Street prices this at a 40% conglomerate discount
  > that fails to reflect the re-rated cloud margin trajectory now underway.
- Data Tables: Present all multi-variable comparisons (peer multiples, segment margins,
  KPI thresholds) as Markdown tables. Never render comparison data as prose lists.
  Example:
  | Metric    | Company | Peer Median | Delta |
  |-----------|---------|-------------|-------|
  | EV/EBITDA | 12.1x   | 18.4x       | -34%  |
- Section Separators: Use --- (horizontal rule) to visually separate the Variant
  Perception Statement, Structural Economics Narrative, and Valuation sections.
- Terminology Formatting: Use LaTeX only for complex formulas or Greek symbols
  ($\alpha$, $\beta$). Render standard percentages (10%) and monetary units
  (RMB 38B, $4.2T) in plain text — never LaTeX.

Your brief must contain these sections (headers must follow assertion-based BLUF format):

1. VARIANT PERCEPTION STATEMENT
   Open with a Markdown blockquote (>) answering: "What is the Street pricing incorrectly,
   and what does the available research data (noting its training-knowledge vintage) suggest
   as a different view?" This is the thesis anchor.
   Do NOT open with company description or historical summary.
   Draw the header from the variant perception itself.
   Mark any figure whose date is unknown or pre-2025 with [as of training cutoff].
   Separate from next section with ---.

2. STRUCTURAL ECONOMICS NARRATIVE
   Integrate profit pool dynamics, competitive positioning, and moat quality from sections
   2A, 2B, and 2C into one flowing narrative — no sub-labels or framework names.
   Use conclusion-led headers for each paragraph block.
   Apply Denominator Logic to all figures. Distinguish Format Shifts from price wars.
   Quantize the moat using ecosystem metrics as proxies for switching costs.
   Separate from next section with ---.

3. CYCLE POSITION & EBITDA NORMALIZATION
   State early / mid / late cycle with 1–2 supporting data points from 2D.
   Strip one-time items; state the Normalized EBITDA and Normalized FCF explicitly.
   If CapEx is elevated: apply Investment Valley framing — name the moat being built
   and the expected ROIC inflection timeline.
   State the mid-cycle normalized EBITDA that Section 7 math will use.

4. KPI THRESHOLDS AS THESIS INVALIDATORS
   Present the 3 most critical KPIs from 2F not as description but as trip-wires:
   for each: current value | thesis-invalidating threshold | distance to threshold.
   Format as a Markdown table. Include the 2F.4 risk indicator.

5. REGULATORY OVERLAY: STRUCTURAL TAILWINDS VS. EXECUTION HEADWINDS
   For each regulation or policy event from 2E: classify as Structural Tailwind or
   Execution Headwind, and state the magnitude (entry barrier effect or margin compression).
   No neutral regulatory observations.
   If live search was not performed, state "Regulatory data limited to training knowledge
   (cutoff ~early 2025)" and note any major known regulatory vectors from that period.

6. COMPARABLE TRANSACTION MULTIPLES
   Use data from the pre-research only. If none available, state:
   "No M&A transaction data available (live search not performed)."

7. UNIVERSAL INDICATORS — SHOW COT ARITHMETIC
   Compute step-by-step: Forward P/E, Trailing P/E, EV/EBITDA, ROIC-WACC spread,
   FCF Margin. Use only the provided FINANCIAL DATA metrics — no estimates.
   Show every arithmetic step explicitly.

8. SECTOR-SPECIFIC INDICATORS
   [Injected at runtime per sector — compute and display all required metrics]

Do NOT output a BUY/SELL signal.

Return a JSON object with exactly three keys:
  "brief_text" : the complete structured industry intelligence brief as a single plain-text
                 string (use \\n for line breaks; include all sections 1-8 in full;
                 inline reference numbers [1],[2] etc. for every cited figure)
  "key_kpis"   : a flat dict of key numerical KPIs extracted from your analysis
                 (e.g. {{"pe_ratio": 25.3, "fcf_yield": 0.042, "ev_ebitda": 18.1}})
  "footnotes"  : a list of citation objects, one per inline reference number used:
                 [{{"ref_id": 1, "source_name": "knowledge_base",
                    "source_type": "knowledge_base", "date": "pre-2025",
                    "speaker": "", "claim": "NRR of 131%",
                    "quote": "", "url": ""}}]
                 source_type must be one of: "10-K", "10-Q", "20-F",
                 "earnings_transcript", "press_release", "third_party_research",
                 "regulatory_filing", "web_search", "management_guidance", "knowledge_base"

The brief_text field must contain the entire brief — do not leave it empty or omit it.
The footnotes list must be non-empty if brief_text contains any [n] markers.
""".strip()


_LIVE_RESEARCH_TIERS = frozenset({
    "anthropic_web",          # fresh Tier 1 run (Anthropic web search)
    "qwen_web",               # fresh Tier 1 run (Qwen native web search)
    "tavily",                 # fresh Tavily run
    "anthropic_web_cached",   # reused from archive, age < 2 days
    "anthropic_web_delta",    # reused base + 4-search delta top-up
    "qwen_web_cached",        # reused Qwen result from archive
})


def _get_system_prompt(research_tier: str) -> str:
    """Return the appropriate system prompt based on the research data tier."""
    if research_tier in _LIVE_RESEARCH_TIERS:
        return _SYSTEM_PROMPT_LIVE
    # knowledge_only, none, unknown — all treated as training-knowledge baseline
    return _SYSTEM_PROMPT_KNOWLEDGE


# Keep SYSTEM_PROMPT as alias for backward compatibility (retry path uses it)
SYSTEM_PROMPT = _SYSTEM_PROMPT_LIVE


# ---------------------------------------------------------------------------
# Typed sector KPI extraction — runs after the LLM call, never inside Pydantic.
# Using a registry + post-processing avoids fragile union-type validation of
# LLM output. Each parser returns {} on any miss; _safe_float never raises.
# ---------------------------------------------------------------------------

def _safe_float(d: dict, *keys) -> float | None:
    """Return the first numeric value found under any of the given keys, or None."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


_SECTOR_KPI_PARSERS: dict[str, callable] = {}


def _register(sector: str):
    def decorator(fn):
        _SECTOR_KPI_PARSERS[sector] = fn
        return fn
    return decorator


@_register("Tech")
def _parse_tech_kpis(raw: dict) -> dict:
    return {
        "ai_stack_layer":           raw.get("ai_stack_layer"),
        "rule_of_40":               _safe_float(raw, "rule_of_40"),
        "nrr":                      _safe_float(raw, "nrr", "net_revenue_retention"),
        "cac_payback_months":       _safe_float(raw, "cac_payback_months", "cac_payback"),
        "ltv_cac_ratio":            _safe_float(raw, "ltv_cac_ratio", "ltv_cac"),
        "arr_growth_yoy":           _safe_float(raw, "arr_growth_yoy", "arr_growth"),
        "burn_multiple":            _safe_float(raw, "burn_multiple"),
        "rd_intensity":             _safe_float(raw, "rd_intensity", "r_and_d_intensity"),
        "ai_sku_live":              raw.get("ai_sku_live"),
        "ai_revenue_disclosed":     raw.get("ai_revenue_disclosed"),
        "ai_earnings_signal_tier":  raw.get("ai_earnings_signal_tier", "Tier3"),
        "token_cost_trend":         raw.get("token_cost_trend"),
        "china_revenue_pct":        _safe_float(raw, "china_revenue_pct"),
    }


@_register("Financials")
def _parse_financials_kpis(raw: dict) -> dict:
    return {
        "nim_trend":             raw.get("nim_trend"),
        "npl_ratio":             _safe_float(raw, "npl_ratio", "non_performing_loan_ratio"),
        "cet1_ratio":            _safe_float(raw, "cet1_ratio", "cet1"),
        "roe_vs_coe_spread":     _safe_float(raw, "roe_vs_coe_spread"),
        "loan_to_deposit_ratio": _safe_float(raw, "loan_to_deposit_ratio", "ltd_ratio"),
    }


@_register("Biopharma")
def _parse_biopharma_kpis(raw: dict) -> dict:
    return {
        "pipeline_rnpv":                   _safe_float(raw, "pipeline_rnpv", "rnpv"),
        "flagship_patent_years_remaining": _safe_float(raw, "flagship_patent_years_remaining"),
        "fda_decision_within_90d":         bool(raw.get("fda_decision_within_90d", False)),
    }


@_register("Energy")
def _parse_energy_kpis(raw: dict) -> dict:
    return {
        "sotp_vs_market_cap":  _safe_float(raw, "sotp_vs_market_cap"),
        "ppa_avg_tenor_years": _safe_float(raw, "ppa_avg_tenor_years"),
        "lcoe_spread":         _safe_float(raw, "lcoe_spread"),
        "capacity_factor":     _safe_float(raw, "capacity_factor"),
    }


@_register("Industrials")
def _parse_industrials_kpis(raw: dict) -> dict:
    return {
        "backlog_revenue_multiple": _safe_float(raw, "backlog_revenue_multiple", "backlog_multiple"),
        "book_to_bill":             _safe_float(raw, "book_to_bill"),
        "fixed_price_exposure_pct": _safe_float(raw, "fixed_price_exposure_pct"),
    }


@_register("Transportation")
def _parse_transportation_kpis(raw: dict) -> dict:
    return {
        "take_rate":                _safe_float(raw, "take_rate"),
        "gross_bookings_growth":    _safe_float(raw, "gross_bookings_growth", "bookings_growth"),
        "contribution_margin_pct":  _safe_float(raw, "contribution_margin_pct", "contribution_margin"),
        "mapc_growth":              _safe_float(raw, "mapc_growth", "active_user_growth"),
        "trips_growth_yoy":         _safe_float(raw, "trips_growth_yoy", "trips_growth"),
        "adjusted_ebitda_margin":   _safe_float(raw, "adjusted_ebitda_margin", "ebitda_margin"),
    }


@_register("RealEstate")
def _parse_realestate_kpis(raw: dict) -> dict:
    return {
        "ffo_yield":            _safe_float(raw, "ffo_yield", "funds_from_operations_yield"),
        "nav_premium_discount": _safe_float(raw, "nav_premium_discount", "nav_discount"),
        "occupancy_rate":       _safe_float(raw, "occupancy_rate"),
        "cap_rate":             _safe_float(raw, "cap_rate"),
        "debt_to_nav":          _safe_float(raw, "debt_to_nav"),
    }


@_register("Materials")
def _parse_materials_kpis(raw: dict) -> dict:
    return {
        "unit_margin":          _safe_float(raw, "unit_margin"),
        "utilisation_rate":     _safe_float(raw, "utilisation_rate", "capacity_utilisation"),
        "sustaining_capex_pct": _safe_float(raw, "sustaining_capex_pct", "sustaining_capex"),
    }


@_register("Resources")
def _parse_resources_kpis(raw: dict) -> dict:
    return {
        "aisc_per_unit":            _safe_float(raw, "aisc_per_unit", "aisc"),
        "reserve_replacement_ratio": _safe_float(raw, "reserve_replacement_ratio"),
        "reserve_life_index":        _safe_float(raw, "reserve_life_index", "rli"),
    }


@_register("ProfessionalServices")
def _parse_professionalservices_kpis(raw: dict) -> dict:
    return {
        "utilisation_rate":      _safe_float(raw, "utilisation_rate"),
        "revenue_per_employee":  _safe_float(raw, "revenue_per_employee"),
        "attrition_rate":        _safe_float(raw, "attrition_rate"),
        "backlog_revenue_multiple": _safe_float(raw, "backlog_revenue_multiple", "backlog_multiple"),
    }


@_register("HealthcareServices")
def _parse_healthcareservices_kpis(raw: dict) -> dict:
    return {
        "mlr":                      _safe_float(raw, "mlr", "medical_loss_ratio"),
        "membership_growth_yoy":    _safe_float(raw, "membership_growth_yoy", "enrollment_growth"),
        "premium_pmpm":             _safe_float(raw, "premium_pmpm", "premium_per_member"),
        "sga_pct_premiums":         _safe_float(raw, "sga_pct_premiums", "sga_ratio"),
        "govt_payer_concentration": _safe_float(raw, "govt_payer_concentration", "cms_revenue_pct"),
        "days_claims_payable":      _safe_float(raw, "days_claims_payable", "dcp"),
    }


@_register("Consumer")
def _parse_consumer_kpis(raw: dict) -> dict:
    return {
        "sss_growth_8q":         raw.get("sss_growth_8q"),
        "contribution_margin":   _safe_float(raw, "contribution_margin"),
        "pricing_power_delta":   _safe_float(raw, "pricing_power_delta"),
        "revenue_per_unit":      _safe_float(raw, "revenue_per_unit"),
        "cost_per_unit":         _safe_float(raw, "cost_per_unit"),
    }


@_register("Telco")
def _parse_telco_kpis(raw: dict) -> dict:
    return {
        "tenancy_ratio":          _safe_float(raw, "tenancy_ratio"),
        "fcf_yield":              _safe_float(raw, "fcf_yield"),
        "maintenance_capex_pct":  _safe_float(raw, "maintenance_capex_pct"),
        "growth_capex_pct":       _safe_float(raw, "growth_capex_pct"),
        "asset_utilisation_rate": _safe_float(raw, "asset_utilisation_rate"),
    }


@_register("Crypto")
def _parse_crypto_kpis(raw: dict) -> dict:
    return {
        "ev_per_exahash":           _safe_float(raw, "ev_per_exahash"),
        "production_cost_per_coin": _safe_float(raw, "production_cost_per_coin", "cash_cost_per_coin"),
        "mw_pipeline":              _safe_float(raw, "mw_pipeline", "megawatt_pipeline"),
        "hash_rate_cagr_6m":        _safe_float(raw, "hash_rate_cagr_6m", "hash_rate_growth"),
    }


def _parse_sector_kpis(sector: str, raw: dict) -> dict:
    """
    Extract typed numeric KPIs from the LLM's freeform key_kpis dict.
    Returns {} when no parser exists for this sector or raw is empty.
    Never raises — all key lookups are guarded by _safe_float / .get().
    """
    parser = _SECTOR_KPI_PARSERS.get(sector)
    if parser is None or not raw:
        return {}
    try:
        return {k: v for k, v in parser(raw).items() if v is not None}
    except Exception:
        return {}


def run_industry_specialist(state: AgentState) -> AgentState:
    """Phase 3: produce the shared Industry Intelligence Brief."""
    agent_id = "industry_specialist"
    ticker = state["data"].get("primary_ticker", state["data"]["tickers"][0])
    sector = state["data"].get("sector", "Tech")

    progress.update_status(agent_id, ticker, f"Building industry brief for sector: {sector}")

    api_key = get_api_key_from_state(state, "FINANCIAL_DATASETS_API_KEY")
    end_date = state["data"]["end_date"]

    metrics_list = get_financial_metrics(ticker, end_date, period="ttm", limit=1, api_key=api_key)
    market_cap = get_market_cap(ticker, end_date, api_key=api_key)

    metrics_summary = ""
    if metrics_list:
        m = metrics_list[0]
        metrics_summary = (
            f"Trailing P/E: {m.price_to_earnings_ratio}  |  "
            f"EV/EBITDA: {m.enterprise_value_to_ebitda_ratio}  |  "
            f"Gross margin: {m.gross_margin}  |  "
            f"Net margin: {m.net_margin}  |  "
            f"ROIC: {m.return_on_invested_capital}  |  "
            f"FCF yield: {m.free_cash_flow_yield}  |  "
            f"Revenue growth: {m.revenue_growth}  |  "
            f"Market cap: {market_cap}"
        )

    _UNKNOWN_SECTOR_BLOCK = (
        "Sector classification could not be confirmed for this ticker.\n"
        "Apply UNIVERSAL INDICATORS only (Section 7). Do NOT compute sector-specific KPIs — "
        "there is insufficient sector context to select a meaningful framework.\n"
        "In the brief, note: \"Sector-specific analysis omitted — ticker not mapped to a recognised "
        "sector. Classify manually and re-run for full brief.\""
    )
    sector_block = SECTOR_BLOCKS.get(sector, _UNKNOWN_SECTOR_BLOCK)
    raw_financials = state["data"].get("raw_financials", {})
    insider_summary = state["data"].get("insider_summary", "")

    # Read the research tier so the prompt can be calibrated accordingly.
    # Live tiers: anthropic_web / tavily (fresh) and their cached/delta variants.
    # Training-data tiers: knowledge_only / none / unknown.
    research_tier     = state["data"].get("research_tier", "unknown")
    _is_live_research = research_tier in _LIVE_RESEARCH_TIERS
    _cache_age        = state["data"].get("research_cache_age_days")   # float or None

    # Select the system prompt variant based on whether research is live or training-based
    _active_system_prompt = _get_system_prompt(research_tier)

    # Prefer deep research report (Phase 3.5) over simple 4-query pre-fetch.
    # Strategy: use structured deep_research_sections (2A–2F, each capped at
    # _SEC_CAP chars) so every section contributes to the brief.  This gives
    # ~15 000 chars of structured context vs. the old single-blob 4 000-char cap
    # that truncated to roughly the first half of section 2A.
    _SEC_CAP = 2500   # chars per section — 6 × 2 500 = 15 000 total
    _FALLBACK_CAP = 10000  # chars when sections dict is absent
    deep_research          = state["data"].get("deep_research", "")
    deep_research_sections = state["data"].get("deep_research_sections", {}) or {}
    web_intel              = state["data"].get("web_intelligence", {})
    web_intel_block        = ""

    # Build the tier provenance header so the specialist LLM sees exactly what
    # data quality it is working with — prevents it from treating training-knowledge
    # output as live and avoids silent supplementation with its own stale data.
    if _is_live_research:
        if research_tier == "anthropic_web_cached":
            _age_str = f"{_cache_age:.1f}d" if _cache_age is not None else "recent"
            _tier_header = (
                f"[Research tier: {research_tier} — LIVE web data "
                f"(retrieved from archive, {_age_str} old — no new searches run)]"
            )
        elif research_tier == "anthropic_web_delta":
            _age_str = f"{_cache_age:.1f}d" if _cache_age is not None else "recent"
            _tier_header = (
                f"[Research tier: {research_tier} — LIVE web data "
                f"(base from archive {_age_str} old + delta top-up searches for new material events)]"
            )
        else:
            _tier_header = f"[Research tier: {research_tier} — LIVE web data]"
    else:
        _tier_header = (
            f"[Research tier: {research_tier} — TRAINING DATA ONLY "
            f"(cutoff approximately early 2025). Do NOT treat these figures as current. "
            f"Do NOT supplement with additional figures from your own training knowledge.]"
        )

    if deep_research_sections:
        # Pass all 6 sections independently, each capped — far richer than a
        # single truncated blob.  Empty sections are skipped silently.
        _SECTION_LABELS = {
            "2a": "2A — Profit Pool Map",
            "2b": "2B — Competitive Landscape",
            "2c": "2C — Moat Analysis",
            "2d": "2D — Cycle Positioning",
            "2e": "2E — Disruption Vectors",
            "2f": "2F — KPI Framework",
            "recent_news": "Recent Market Developments",
        }
        parts = []
        for key, label in _SECTION_LABELS.items():
            content = (deep_research_sections.get(key) or "").strip()
            if content:
                capped = content[:_SEC_CAP]
                if len(content) > _SEC_CAP:
                    capped += "\n[...section truncated]"
                parts.append(f"[{label}]\n{capped}")
        if parts:
            web_intel_block = _tier_header + "\n\n" + "\n\n".join(parts)
            progress.update_status(
                agent_id, ticker,
                f"Deep research sections loaded ({research_tier}): {len(parts)}/6 sections "
                f"({sum(len(p) for p in parts):,} chars)"
            )
        elif deep_research:
            # Sections dict exists but all keys empty — fall back to raw text
            web_intel_block = _tier_header + "\n\n" + deep_research[:_FALLBACK_CAP]
            progress.update_status(agent_id, ticker, f"Deep research ({research_tier}): sections empty, using raw text fallback")
    elif deep_research:
        # No sections dict — use raw text with a larger cap than the old 4 000
        web_intel_block = _tier_header + "\n\n" + deep_research[:_FALLBACK_CAP]
        progress.update_status(
            agent_id, ticker,
            f"Deep research ({research_tier}): no sections dict, using raw text "
            f"({min(len(deep_research), _FALLBACK_CAP):,}/{len(deep_research):,} chars)"
        )
    elif web_intel:
        sections = {
            "company_news":  "Company News & Recent Developments",
            "ma_activity":   "M&A Activity & Transaction Multiples",
            "regulatory":    "Regulatory & Policy Developments",
            "competitive":   "Competitive Landscape Shifts",
        }
        parts = []
        for key, label in sections.items():
            content = web_intel.get(key, "")
            if content and content != "Search unavailable.":
                parts.append(f"[{label}]\n{content}")
        web_intel_block = _tier_header + "\n\n" + (
            "\n\n".join(parts) if parts else "No real-time data available."
        )
    else:
        # No research at all — still show the tier header so the LLM knows the situation
        web_intel_block = _tier_header + "\n\nNo Section 2 research data available for this run."

    # Pull citation registry for footnote mapping (from deep_research.py)
    citation_registry = state["data"].get("citation_registry", []) or []
    _registry_block = ""
    if citation_registry:
        lines = []
        for e in citation_registry[:30]:
            src  = e.get("source_name", "?")
            date = e.get("date", "")
            lines.append(
                f"[{e.get('ref_id',0)}] {e.get('claim','')[:80]} "
                f"— {src}" + (f" ({date})" if date else "")
            )
        _registry_block = "\n".join(lines)
    else:
        _registry_block = "No citation registry available — assign new reference numbers starting at [1]."

    # Inject sector-calibrated WACC anchor so the LLM does not self-compute
    # a CAPM estimate (~14–15% for high-beta stocks) that conflicts with the
    # DCF agent's sector-based value (which runs at step 4.5, AFTER this agent).
    # Fix 3d: pass valuation_profile so Energy sub-types (IPP, Regulated Utility,
    # Merchant Power) use the correct Damodaran base rate rather than the flat 9%.
    _val_profile = state["data"].get("valuation_profile", "") or ""
    _prelim_wacc = get_wacc(sector, leverage=0.0, profile=_val_profile)
    _wacc_anchor_note = (
        f"\n\nWACC ANCHOR (mandatory): Use WACC = {_prelim_wacc * 100:.1f}% "
        f"(Damodaran sector-calibrated rate for {sector}"
        + (f" / {_val_profile}" if _val_profile else "")
        + ") for ALL ROIC-WACC spread "
        f"calculations in this brief. Do NOT substitute your own CAPM estimate."
    )

    # Dynamic section header reflects actual data provenance
    _section2_header = (
        "=== SECTION 2 — INDUSTRY STRUCTURE (live web research — use as primary source) ==="
        if _is_live_research else
        "=== SECTION 2 — INDUSTRY STRUCTURE (training knowledge baseline — cutoff ~early 2025) ==="
    )

    template = ChatPromptTemplate.from_messages([
        ("system", _active_system_prompt),
        ("human", (
            "Ticker: {ticker}  |  Sector: {sector}  |  Analysis date: {analysis_date}\n\n"
            "=== CITATION REGISTRY (use these ref_ids for inline [n] markers) ===\n"
            "{registry}\n\n"
            "{section2_header}\n"
            "{web_intel}\n\n"
            "=== FINANCIAL DATA (live — from financial data API) ===\n"
            "Financial metrics snapshot:\n{metrics}\n\n"
            "5-year raw financials:\n{raw_financials}\n\n"
            "Insider activity:\n{insider}\n\n"
            "Universal indicators to compute (show CoT):\n{universal}\n\n"
            "Sector-specific indicators to compute:\n{sector_block}"
        )),
    ])
    prompt = template.invoke({
        "ticker":          ticker,
        "sector":          sector,
        "ticker":          ticker,
        "sector":          sector,
        "analysis_date":   end_date,
        "registry":        _registry_block,
        "section2_header": _section2_header,
        "web_intel":       web_intel_block,
        "metrics":         metrics_summary,
        "raw_financials":  str(raw_financials)[:2000],
        "insider":         insider_summary,
        "universal":       UNIVERSAL_INDICATORS + _wacc_anchor_note,
        "sector_block":    sector_block,
    })

    progress.update_status(agent_id, ticker, "Generating industry intelligence brief")

    result: IndustryBriefOutput = call_llm(
        prompt=prompt,
        pydantic_model=IndustryBriefOutput,
        agent_name=agent_id,
        state=state,
        default_factory=lambda: IndustryBriefOutput(
            brief_text=f"Industry brief unavailable for {ticker} ({sector}).",
            key_kpis={},
        ),
    )

    progress.update_status(agent_id, ticker, "Industry brief complete")

    # Guard: brief_text empty means the LLM hit its output token budget before
    # writing the brief_text field (key_kpis is shorter so it often survives).
    # Retry once with a stripped-down prompt that only asks for the brief text.
    if not result.brief_text:
        progress.update_status(agent_id, ticker, "brief_text empty — retrying with compact prompt")
        _retry_template = ChatPromptTemplate.from_messages([
            ("system", (
                "You are an industry analyst. Write a concise Industry Intelligence Brief "
                "for the ticker below. Return JSON with exactly two keys: "
                "'brief_text' (the full brief as a plain-text string, \\n for line breaks) "
                "and 'key_kpis' (flat dict of numerical KPIs). "
                "brief_text MUST be non-empty."
            )),
            ("human", (
                "Ticker: {ticker}  |  Sector: {sector}  |  Analysis date: {analysis_date}\n\n"
                "Metrics: {metrics}\n\n"
                "Sector indicators to cover:\n{sector_block}\n\n"
                "Universal indicators: Forward P/E, Trailing P/E, EV/EBITDA, ROIC-WACC, FCF Margin."
            )),
        ])
        _retry_prompt = _retry_template.invoke({
            "ticker": ticker,
            "sector": sector,
            "analysis_date": end_date,
            "metrics": metrics_summary,
            "sector_block": sector_block,
        })
        result = call_llm(
            prompt=_retry_prompt,
            pydantic_model=IndustryBriefOutput,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: IndustryBriefOutput(
                brief_text=f"Industry brief unavailable for {ticker} ({sector}) after retry.",
                key_kpis={},
            ),
        )
        if not result.brief_text:
            result = IndustryBriefOutput(
                brief_text=f"Industry brief unavailable for {ticker} ({sector}).",
                key_kpis=result.key_kpis,
            )

    state["data"]["industry_brief"]     = result.brief_text
    state["data"]["industry_kpis"]      = result.key_kpis
    # Typed extraction: never raises, skips any key the LLM didn't populate
    state["data"]["sector_kpis"]        = _parse_sector_kpis(sector, result.key_kpis)

    # Merge specialist footnotes with deep_research citation_registry
    # Specialist may assign new ref_ids for claims not in the original registry.
    # Deduplicate by ref_id (specialist footnote takes precedence for same id).
    existing_registry: list[dict] = state["data"].get("citation_registry", []) or []
    specialist_footnotes: list[dict] = result.footnotes or []
    merged: dict[int, dict] = {e.get("ref_id", 0): e for e in existing_registry}
    for fn in specialist_footnotes:
        rid = fn.get("ref_id", 0)
        if rid:
            merged[rid] = fn   # specialist footnote wins on conflict
    state["data"]["citation_registry"] = sorted(
        merged.values(),
        key=lambda x: int(x.get("ref_id", 0)) if str(x.get("ref_id", "0")).lstrip("-").isdigit() else 0,
    )
    state["data"]["industry_footnotes"] = specialist_footnotes

    n_fn = len(specialist_footnotes)
    progress.update_status(agent_id, ticker, f"Industry brief complete | {n_fn} footnote(s) tagged")

    return state
