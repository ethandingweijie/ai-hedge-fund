"""
src/research_ideas/alerts
=========================
Outbound price-vs-fair-value alerting for the Research Ideas cohorts.

v1 ships ONE monitor: the IV15 "price reached fair value" sweep. It compares a
fresh live quote for every name in the latest SW46 + HK50 cohorts against that
name's stored IV15 (the fundamental per-share fair value) and pushes a Slack
alert for any name whose live price has fallen to/below IV15 (live P/IV15 <=
trigger band). Hysteresis (re-arm band) means each name alerts ONCE per
downward cross, not every day it sits cheap.

Fired daily at 08:00 SGT by iv15_scheduler.start_iv15_scheduler() (registered
at FastAPI startup).
"""
