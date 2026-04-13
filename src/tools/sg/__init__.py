"""
SG stock data layer — yfinance-powered equivalents to the Financial Datasets
functions in src/tools/api.py.

All functions return the same Pydantic model types as the US path so agents
receive identical object structures regardless of ticker origin.
"""

from src.tools.sg.prices import get_sg_prices
from src.tools.sg.financial_metrics import get_sg_financial_metrics
from src.tools.sg.line_items import search_sg_line_items
from src.tools.sg.insider_trades import get_sg_insider_trades
from src.tools.sg.news import get_sg_company_news
from src.tools.sg.market_cap import get_sg_market_cap

__all__ = [
    "get_sg_prices",
    "get_sg_financial_metrics",
    "search_sg_line_items",
    "get_sg_insider_trades",
    "get_sg_company_news",
    "get_sg_market_cap",
]
