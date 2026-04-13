"""
SGX company news — yfinance .news as primary source.
"""

from src.tools.sg.ticker import to_yfinance_code


def get_sg_company_news(
    ticker: str,
    start_date: str = "",
    end_date: str = "",
    limit: int = 20,
) -> list[dict]:
    """Fetch recent news for an SGX ticker."""
    import yfinance as yf

    yf_code = to_yfinance_code(ticker)

    try:
        t = yf.Ticker(yf_code)
        raw_news = t.news or []

        articles = []
        for item in raw_news[:limit]:
            content = item.get("content", {}) if isinstance(item, dict) else {}
            title = content.get("title") or item.get("title", "")
            url = content.get("canonicalUrl", {}).get("url", "") or item.get("link", "")
            source = content.get("provider", {}).get("displayName", "") or item.get("publisher", "")
            pub_date = content.get("pubDate", "") or item.get("providerPublishTime", "")

            # Convert Unix timestamp if needed
            if isinstance(pub_date, (int, float)):
                from datetime import datetime
                pub_date = datetime.fromtimestamp(pub_date).strftime("%Y-%m-%d")
            elif isinstance(pub_date, str) and len(pub_date) > 10:
                pub_date = pub_date[:10]

            # Date filtering
            if start_date and pub_date < start_date:
                continue
            if end_date and pub_date > end_date:
                continue

            articles.append({
                "ticker": ticker,
                "title": title,
                "author": "",
                "source": source,
                "date": pub_date,
                "url": url,
                "sentiment": None,
            })

        return articles

    except Exception:
        return []
