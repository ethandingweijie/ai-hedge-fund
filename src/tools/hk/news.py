"""
HK company news via AKShare stock_news_em.
Returns list[CompanyNews] — same type as the US path.
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.data.models import CompanyNews
from src.tools.hk.ticker import to_akshare_code, to_canonical

_log = logging.getLogger(__name__)


def get_hk_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[CompanyNews]:
    """
    Fetch up to 100 recent news articles for an HK-listed stock.

    AKShare's stock_news_em returns at most 100 articles per call (no pagination).
    The effective limit is therefore min(limit, 100).

    Parameters
    ----------
    ticker     : any valid HK ticker format
    end_date   : "YYYY-MM-DD" — exclude articles after this date
    start_date : "YYYY-MM-DD" — exclude articles before this date (optional)
    limit      : max articles to return

    Returns
    -------
    list[CompanyNews] — empty on any error
    """
    try:
        import akshare as ak
    except ImportError:
        _log.error("akshare not installed — cannot fetch HK news")
        return []

    symbol = to_akshare_code(ticker)
    canonical = to_canonical(ticker)

    try:
        df = ak.stock_news_em(symbol=symbol)
    except Exception as exc:
        _log.warning("AKShare stock_news_em failed for %s: %s", symbol, exc)
        return []

    if df is None or df.empty:
        return []

    news: list[CompanyNews] = []
    for _, row in df.iterrows():
        try:
            # Parse date — AKShare returns strings like "2024-11-07 08:30:00"
            raw_date = str(row.get("发布时间") or row.get("时间") or "").strip()
            if not raw_date:
                continue
            # Normalise to YYYY-MM-DD
            article_date = raw_date[:10]

            # Date filters
            if article_date > end_date:
                continue
            if start_date and article_date < start_date:
                continue

            title = str(row.get("新闻标题") or row.get("标题") or "").strip()
            source = str(row.get("文章来源") or row.get("来源") or "").strip()
            url = str(row.get("新闻链接") or row.get("链接") or "").strip()

            if not title:
                continue

            news.append(
                CompanyNews(
                    ticker=canonical,
                    title=title,
                    author=source or "East Money",
                    source=source or "East Money",
                    date=article_date,
                    url=url,
                    sentiment=None,  # classified downstream by news_sentiment_agent
                )
            )
        except Exception as exc:
            _log.debug("Skipping news row for %s: %s", symbol, exc)
            continue

        if len(news) >= limit:
            break

    # Sort newest first
    news.sort(key=lambda n: n.date, reverse=True)
    return news[:limit]
