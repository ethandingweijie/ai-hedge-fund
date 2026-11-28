"""Per-market rules that are not shared by every market (one module per market).

Each module owns its ticker forms, listing currency, research hints and the
constants it PROPOSES to the owner. Shared code calls into a module through a
small, explicit hook (``is_korea_ticker`` and friends) so a market's logic does
not spread across the engine. Korea: ``src.markets.korea``.
"""
