"""
HK stock data layer — AKShare-powered equivalents to the Financial Datasets
functions in src/tools/api.py.

All functions return the same Pydantic model types as the US path so agents
receive identical object structures regardless of ticker origin.
"""

from src.tools.hk.prices import get_hk_prices
from src.tools.hk.financial_metrics import get_hk_financial_metrics
from src.tools.hk.line_items import search_hk_line_items
from src.tools.hk.insider_trades import get_hk_insider_trades
from src.tools.hk.news import get_hk_company_news
from src.tools.hk.market_cap import get_hk_market_cap

__all__ = [
    "get_hk_prices",
    "get_hk_financial_metrics",
    "search_hk_line_items",
    "get_hk_insider_trades",
    "get_hk_company_news",
    "get_hk_market_cap",
]
