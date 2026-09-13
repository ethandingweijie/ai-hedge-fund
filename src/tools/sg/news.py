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

            # Convert Unix timestamp if needed. Keep the minute alongside:
            # `date` is date-only by contract (the sentiment agent parses it
            # with "%Y-%m-%d"), but a feed cannot order one day's items on a
            # date alone.
            from datetime import datetime, timezone
            published_at = None
            if isinstance(pub_date, (int, float)):
                _dt = datetime.fromtimestamp(pub_date, tz=timezone.utc)
                published_at = _dt.strftime("%Y-%m-%d %H:%M:%S")
                pub_date = _dt.strftime("%Y-%m-%d")
            elif isinstance(pub_date, str) and len(pub_date) > 10:
                try:
                    published_at = (
                        datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        .strftime("%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    published_at = None
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
                "published_at": published_at,
            })

        return articles

    except Exception:
        return []
