"""
src/research_ideas/complacency/
================================
Bill Ackman "Complacency Detection" equity screener — detects when retail
enthusiasm has decoupled prices from fundamentals + insider behaviour +
quality scores. Mirrors the structural setup Ackman's 2020 CDS trade
exploited at the credit-spread level, applied to equities.

v1 = equity layer only (4-pillar scoring on the ~60-ticker complacency
universe). Sector layer (ETF holdings + concentration + flows) is designed
in the spec but unbuilt — add in v2.

Four pillars (max 2 pts each → composite 0-8, gate ≥6 AND all pillars ≥1):
  Valuation    — EV/Sales vs sector + absolute backstop + FCF yield
  Behavioral   — insider A/D, EPS revision, range-position
  Technical    — % above 200DMA, weekly RSI
  Quality      — Altman Z (dual tail), Piotroski

Outputs a ranked "complacency watchlist" — names where multiple pillars
are flashing red simultaneously.
"""
