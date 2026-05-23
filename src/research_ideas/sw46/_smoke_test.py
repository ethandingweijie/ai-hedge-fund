"""
Self-contained synthetic-data smoke test. Runs the whole SW46 math chain
on a Microsoft-shaped fake company. No FMP API key required.

Invoke:   .\.venv\Scripts\python.exe -m src.research_ideas.sw46._smoke_test
"""
from src.research_ideas.sw46.data_fetch import TickerBundle, YearlyStatement
from src.research_ideas.sw46.tragic_algebra import compute_tragic_algebra
from src.research_ideas.sw46.roic import compute_roic
from src.research_ideas.sw46.aict import classify_aict
from src.research_ideas.sw46.iv15 import compute_iv15
from src.research_ideas.sw46.composite_score import compute_composite


def main() -> None:
    ni_seq    = [60_000e6, 65_000e6, 72_000e6, 80_000e6, 88_000e6]
    sbc_seq   = [ 6_000e6,  7_000e6,  8_000e6,  9_000e6, 10_000e6]
    rev_seq   = [180_000e6, 200_000e6, 220_000e6, 245_000e6, 275_000e6]
    shr_seq   = [ 7_580e6,  7_510e6,  7_460e6,  7_430e6,  7_420e6]
    price_seq = [240.0, 280.0, 320.0, 370.0, 420.0]
    buyback   = [25_000e6, 27_000e6, 22_000e6, 18_000e6, 17_000e6]
    rsu_tax   = [ 3_000e6,  3_500e6,  4_000e6,  4_500e6,  5_000e6]

    years = []
    for i, fy in enumerate([2020, 2021, 2022, 2023, 2024]):
        years.append(
            YearlyStatement(
                fiscal_year=fy,
                report_date=f"{fy}-06-30",
                net_income=ni_seq[i],
                sbc_expense=sbc_seq[i],
                common_stock_repurchased=buyback[i],
                rsu_tax_withholding=rsu_tax[i],
                interest_income=2_000e6,
                capital_lease_payments=4_000e6,
                revenue=rev_seq[i],
                cash_and_equivalents=80_000e6,
                short_term_investments=40_000e6,
                long_term_investments=15_000e6,
                total_debt=60_000e6,
                long_term_operating_leases=12_000e6,
                shareholders_equity=230_000e6,
                diluted_shares=shr_seq[i],
                avg_share_price=price_seq[i],
            )
        )

    bundle = TickerBundle(
        ticker="MSFT",
        years=years,
        market_cap=420.0 * 7_420e6,
        current_price=420.0,
        shares_outstanding=7_420e6,
        forward_revenue_growth=0.15,
        reported_currency="USD",
    )

    ta = compute_tragic_algebra(bundle)
    roic = compute_roic(bundle, ta)
    aict = classify_aict("MSFT")
    iv = compute_iv15(bundle, ta, roic, aict)
    comp = compute_composite(bundle, ta, roic, aict, iv)

    print("--- Tragic Algebra ---")
    print(f"  pooled dE       = {ta.pooled_delta_e:.3f}")
    print(f"  ta_tier         = {ta.ta_tier}")
    print(f"  latest OE       = {ta.latest_owner_earnings / 1e9:.1f}B")
    print(f"  sbc_trend slope = {ta.sbc_trend}")
    print("--- ROIC ---")
    print(f"  numerator       = {roic.numerator / 1e9:.1f}B")
    print(f"  denominator     = {roic.denominator / 1e9:.1f}B")
    print(f"  ROIC            = {roic.roic * 100:.1f}%")
    print("--- AICT ---")
    print(f"  tier            = {aict.tier}")
    print(f"  growth_haircut  = {aict.growth_haircut * 100:.0f}%")
    print(f"  multiple        = {aict.terminal_multiple:.1f}x")
    print("--- IV15 ---")
    print(f"  IV15 total      = {iv.iv15_total / 1e9:.1f}B")
    print(f"  IV15 / share    = ${iv.iv15_per_share:.2f}")
    print(f"  growth Y1-5     = {iv.growth_year1_5 * 100:.1f}%")
    print(f"  growth Y6-10    = {iv.growth_year6_10 * 100:.1f}%")
    print(f"  growth Y11-15   = {iv.growth_year11_15 * 100:.1f}%")
    print("--- Composite ---")
    print(f"  P/IV15          = {comp.p_iv15:.2f}x")
    print(f"  Shareholder     = {comp.shareholder_bucket:.1f} / 30")
    print(f"  Quality         = {comp.quality_bucket:.1f} / 35")
    print(f"  Valuation       = {comp.valuation_bucket:.1f} / 35")
    print(f"  TOTAL           = {comp.total:.1f} / 100")


if __name__ == "__main__":
    main()
