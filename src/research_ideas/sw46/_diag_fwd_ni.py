"""Diagnostic: dump forward NI estimates for screenshot tickers."""
import datetime as dt
from src.tools.api import get_analyst_estimates


for t in ['CRM', 'NOW', 'HUBS', 'WDAY', 'MNDY', 'PAYC', 'FRSH', 'PCTY']:
    est = get_analyst_estimates(t, end_date=dt.date.today().isoformat(), period='annual', limit=4)
    if not est:
        print(f"{t:5s} no estimates")
        continue
    print(f"{t:5s}")
    for e in est:
        rev = (e.revenue_avg or 0) / 1e9
        ni = (e.net_income_avg or 0) / 1e9
        print(f"  {e.period_end}: rev_avg=${rev:.2f}B  ni_avg=${ni:.2f}B  n_eps={e.analyst_count_eps}")
