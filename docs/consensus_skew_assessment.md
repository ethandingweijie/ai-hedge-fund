# Where the engine sits against consensus, and against spot (all waves, 2026-09-26)

Owner question: across energy, power, A&D and staples the within-30%-of-consensus rate is close to
50-50. Is there a structural concern? Measured over every wave baseline that carries a consensus target
(45 names; Wave 1 energy has no stored baseline file and is excluded), against the consensus target the
scorecards use AND against spot, with spot read the same day.

| Wave (file) | n | IV below consensus | within ±30% | median IV/consensus | median consensus/spot | median IV/spot | IV below spot |
|---|---|---|---|---|---|---|---|
| W2 power (after) | 14 | 14 of 14 | 3 | −48% | +36% | −25% | 10 of 14 |
| W3 A&D (after rules) | 13 | 13 of 13 | 6 | −47% | +31% | −3% | 8 of 13 |
| W4 staples (Stage 0) | 15 | 14 of 15 | 6 | −39% | +16% | −28% | 12 of 15 |
| China platform (w=0.35) | 3 | 1 of 3 | 1 | +81% | +35% | +145% | 0 of 3 |
| **All** | **45** | **42 of 45** | **16** | **−39%** | **+29%** | **−23%** | **30 of 45** |

## Three findings

**1. The yardstick carries a built-in miss.** Sell-side consensus targets sit a median +29% above spot
across these names (+31% to +36% in power and A&D, +16% in staples). A 12-month target is a bullish
instrument by construction: it is published by houses that mostly rate Buy, and it embeds a year of
expected return. An intrinsic value that lands ON spot reads as −22% against such a target and fails
the ±30% band on any name where the premium exceeds 30%. In power, where the consensus premium is +36%,
even a perfect spot-tracking IV fails. Part of the 50-50 is therefore the metric, not the engine: against
spot, the W3 A&D blend is at −3% median with 8 of 13 within 30%, which is a different picture from
"13 of 13 below consensus".

**2. There is a genuine conservative tilt of about −20% against spot, and it is concentrated.** After
netting the consensus premium, the engine still sits a median −23% below the market with 30 of 45 names
below spot. The tail is not random:

- *Re-rated growth regimes* (CCJ −82%, RKLB −78%, BE −84%, GEV −67%, LEU −56%, SMR −54%, CEG −53%
  against spot). The engine prices trailing or normalised economics; the market prices a re-rated path
  (AI power, space, nuclear fuel). Recorded as the AI-power regime deviation in Wave 2; the same
  mechanism in Wave 3's space names. A basket multiple on today's earnings cannot reach a price that
  discounts 2030.
- *Misrouting* (Wave 4 Stage 0: EL, CL, WMT, COST, USFD, KR, MDLZ, TSN at −24% to −63% against spot).
  Fixed by Stage 4 routing: after the fix 11 of 15 are within 30% of consensus and the median gap
  against consensus halves. This component was plumbing, and it is gone for staples.
- *Where routing is right, the tilt is small.* Defense Primes (NOC −1%, GD +7%, LMT +7%, LHX +23%
  against spot), Regulated Utility (DUK +5%, SO −19%), Household-type names (PG −8%, MO −11%, BUD +8%,
  ADM +2%). An intrinsic-value engine sitting a few points under a market at elevated multiples is the
  expected sign, not a defect.

**3. The one structural over-valuation is China.** PDD +224% and JD +145% against spot on the China
Internet Platform profile: the DCF anchor projects domestic margins at a US-style cost of capital, and
the market applies a China discount the multiples legs carry (`cn_adr_haircut`) but the DCF does not.
That is a cost-of-capital gap, owner constant: a country risk premium for CNY reporters in the DCF, or
the discount applied to the DCF leg as it is to the multiples.

## What this argues for

1. **Score against spot as well as consensus.** Add "within ±30% of spot" and "sign agreement with the
   consensus direction" to the wave scorecard beside the consensus band, and record the consensus premium
   per wave. The consensus band stays because the owner set it; the spot band tells the engine's own
   story. This is a scorecard change, not an engine change.
2. **Treat the growth-regime tail as a named state, not a miss to fix.** Wave 2 already does this with
   the regime deviation record; Wave 3's space names and any name whose consensus premium exceeds 50%
   (KTOS +115%, NRG +102%, NXT +82%, BABA +65%) belong in the same accounting. The engine's job on those
   is to say what today's economics support and how far the market is from that.
3. **Close the China cost-of-capital gap** (owner constant). It is the only place the engine is
   structurally high rather than low.
4. **Do not lower the engine's conservatism to chase the band.** A −5% to −10% tilt against spot on
   correctly routed names is what an intrinsic value should show in a market at 2026 multiples. Moving
   WACC or terminal growth to close it would be fitting the market, which is what the two-tier design
   was built to avoid.

## Correction and decisions (owner, 2026-09-26)

Finding 3 said the DCF carried no China discount. It carried 180bps (an inline Damodaran table) while the
multiples legs carried the `cn_adr_haircut` only on the Consumer static row. The owner's decision: the
country premium for China and Hong Kong is ZERO ("do not include risk premium for me"), recorded in
`valuation_constants.country_risk_premium`; the engine removes the 150bps the HK sector WACC table embeds
so the total is zero. PDD and JD therefore sit further above spot on their DCF anchor, and that gap is
the owner's basis, scored as a genuine stance and flagged, not tuned.

Applied: dual score (`iv_vs_spot`, `consensus_spread` covariate on the calibration record),
`Growth_Inflection_Speculative` regime flag above a 50% spread (`GATE_GROWTH_INFLECTION`, observation),
and the conservatism principle (no WACC or terminal-growth tuning to close the band).
