"""Per-market support modules (one file per market; shared code stays in src/tools and src/data).

Each module owns the rules that only make sense for its market -- ticker forms, fiscal-year conventions, filing
sources, research hints -- so the shared engine files carry a one-line branch instead of the market's detail.
"""
