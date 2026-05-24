"""Quick verification of the sector-medians cache + resolver."""
from src.research_ideas.complacency.scoring import resolve_sector_median
from app.backend.services.sector_medians_storage import (
    list_latest_sector_medians,
    get_latest_refresh_timestamp,
)

print(f"Latest refresh: {get_latest_refresh_timestamp()}\n")

print("=== LIVE EV/Sales medians (S&P 500) ===")
for r in list_latest_sector_medians("ev_sales"):
    print(f"  {r['sector']:25s} median={r['median']:6.2f}  p25={r['p25'] or 0:5.2f}  "
          f"p75={r['p75'] or 0:5.2f}  n={r['sample_size']:3d}")

print("\n=== LIVE FCF Yield medians (S&P 500) ===")
for r in list_latest_sector_medians("fcf_yield"):
    print(f"  {r['sector']:25s} median={r['median']*100:6.2f}%  n={r['sample_size']:3d}")

print("\n=== Resolver tests ===")
for sec in ["Technology", "Healthcare", "Materials", "Basic Materials", "Real Estate", "UnknownXYZ"]:
    val = resolve_sector_median(sec, "ev_sales")
    print(f"  resolve_sector_median({sec!r:25s}, 'ev_sales') = {val}")
