"""
src/tools/hk/currency.py
─────────────────────────
Reporting currency lookup and CNY→HKD FX conversion for HKEX-listed stocks.

Many HKEX-listed companies are incorporated in mainland China and report their
financial statements in CNY (Renminbi).  However, their share prices are quoted
in HKD, and market-level figures (market cap, P/E, P/B) are derived from
those HKD prices.

Without explicit FX handling:
  • enterprise_value = market_cap (HKD) + net_debt (CNY) → mixed-currency error
  • LineItem currency label "HKD" is wrong for CNY-reporting companies

This module:
  1. Maps known stock codes to their reporting currency ("CNY" or "HKD")
  2. Provides a live or cached CNY→HKD FX rate (falls back to a hardcoded
     approximate if yfinance is unavailable)
  3. Exposes helper functions used by line_items.py and financial_metrics.py
"""
from __future__ import annotations

import logging
import time

_log = logging.getLogger(__name__)

# ── Approximate fallback rate (updated 2026-04) ─────────────────────────────
# 1 CNY ≈ 1.073 HKD  (HKMA peg means USD/HKD ≈ 7.78; USD/CNY ≈ 7.25)
_CNY_HKD_FALLBACK = 1.073

# Cache: (rate, timestamp)
_fx_cache: tuple[float, float] | None = None
_FX_TTL = 3600.0  # refresh once per hour


def cny_to_hkd_rate() -> float:
    """
    Return the current CNY/HKD rate (1 CNY in HKD).

    Tries yfinance first; falls back to the hardcoded approximate if
    yfinance is unavailable or returns an invalid value.
    """
    global _fx_cache
    now = time.time()
    if _fx_cache is not None and (now - _fx_cache[1]) < _FX_TTL:
        return _fx_cache[0]

    rate = _fetch_live_rate()
    _fx_cache = (rate, now)
    return rate


def _fetch_live_rate() -> float:
    try:
        import yfinance as yf
        ticker = yf.Ticker("CNYHKD=X")
        info = ticker.info
        rate = (
            info.get("regularMarketPrice")
            or info.get("previousClose")
            or info.get("ask")
        )
        if rate and 0.8 <= float(rate) <= 1.5:   # sanity check
            _log.debug("Live CNY/HKD rate: %.4f", rate)
            return float(rate)
    except Exception as exc:
        _log.debug("CNY/HKD live fetch failed: %s", exc)
    _log.debug("Using fallback CNY/HKD rate: %.4f", _CNY_HKD_FALLBACK)
    return _CNY_HKD_FALLBACK


# ── Per-company reporting currency ───────────────────────────────────────────
# Key: 5-digit AKShare code  (e.g. "00700")
# Value: ISO currency code   ("CNY" | "HKD" | "USD")
#
# Rules used to build this table:
#   • Mainland Chinese companies incorporated under PRC law → CNY
#   • HK-incorporated companies whose primary operations are HK/global → HKD
#   • HSBC, Standard Chartered, etc. → USD  (international banks)
#   • Default for unlisted companies → CNY  (most HK-listed companies are PRC)

_REPORTING_CURRENCY: dict[str, str] = {
    # Tech / Internet
    "00700": "CNY",   # Tencent
    "09988": "CNY",   # Alibaba HK
    "03690": "CNY",   # Meituan
    "09618": "CNY",   # JD.com
    "09999": "CNY",   # NetEase
    "01810": "CNY",   # Xiaomi
    "01024": "CNY",   # Kuaishou
    "00020": "CNY",   # SenseTime
    "01357": "CNY",   # Meitu
    "00981": "CNY",   # SMIC
    "09660": "CNY",   # Horizon Robotics
    "00100": "CNY",   # MiniMax
    "02513": "CNY",   # Knowledge Atlas
    "03896": "CNY",   # Kingsoft Cloud
    # Telco
    "00941": "CNY",   # China Mobile
    "00762": "CNY",   # China Unicom
    # Energy
    "00883": "USD",   # CNOOC (reports in USD)
    "00857": "CNY",   # PetroChina
    "00386": "CNY",   # Sinopec
    # Financials
    "00005": "USD",   # HSBC (reports in USD)
    "01299": "USD",   # AIA Group (reports in USD)
    "02318": "CNY",   # Ping An
    "03988": "CNY",   # Bank of China
    "01398": "CNY",   # ICBC
    "00939": "CNY",   # CCB
    # Industrials
    "00001": "HKD",   # CK Hutchison (HK-incorporated)
    "03750": "CNY",   # CATL
    "01211": "CNY",   # BYD
    # Real Estate
    "00016": "HKD",   # Sun Hung Kai
    "00012": "HKD",   # Henderson Land
    "00688": "CNY",   # China Overseas Land
    "01113": "HKD",   # CK Asset
    # Biopharma
    "01177": "CNY",   # Sino Biopharmaceutical
    "02269": "CNY",   # Wuxi Biologics (reports in CNY)
    "02268": "CNY",   # Wuxi XDC
    # Consumer
    "09992": "CNY",   # Pop Mart
    "06862": "CNY",   # Haidilao
    "00669": "HKD",   # Techtronic Industries (HK-incorporated)
    "00322": "CNY",   # Tingyi
    "00151": "CNY",   # Want Want China
}

# Default for unlisted stocks (most HK-listed are PRC = CNY)
_DEFAULT_CURRENCY = "CNY"


def get_reporting_currency(ak_code: str) -> str:
    """
    Return the ISO currency code for a stock's financial statements.

    Parameters
    ----------
    ak_code : 5-digit AKShare code, e.g. "00700"

    Returns
    -------
    "CNY" | "HKD" | "USD"
    """
    return _REPORTING_CURRENCY.get(ak_code.zfill(5), _DEFAULT_CURRENCY)


def statement_to_hkd(value: float | None, ak_code: str) -> float | None:
    """
    Convert a financial statement value to HKD if the company reports in CNY.

    Used when combining statement-level figures (revenue, net_debt) with
    market-level figures (market_cap) that are denominated in HKD.

    Returns the value unchanged if the reporting currency is already HKD or USD
    (USD is left to the caller to convert via a separate USD/HKD rate).
    """
    if value is None:
        return None
    ccy = get_reporting_currency(ak_code)
    if ccy == "CNY":
        return value * cny_to_hkd_rate()
    return value   # HKD already correct; USD left as-is (rare edge case)
