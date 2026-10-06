"""HK / SG screener day change: FMP whole-exchange quote overlay.

The weekly HK/SG builds carry no day change and the frontend tick only
re-quotes the top 50 rows, so most HK/SG rows had none. One FMP
batch-exchange-quote per exchange fills every row.
"""
from app.backend.services import screener_service as ss


class _Resp:
    ok = True

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


def _patch(monkeypatch, rows, calls):
    def _get(url, params=None, timeout=None):
        calls.append(params["exchange"])
        return _Resp(rows)
    monkeypatch.setattr(ss.requests, "get", _get)
    monkeypatch.setattr(ss, "_get_fmp_key", lambda: "k")
    monkeypatch.setattr("src.tools.api.acquire_fmp_token", lambda: None)
    ss._EXCHANGE_QUOTES.clear()


def test_hk_symbols_keyed_canonical(monkeypatch):
    calls = []
    _patch(monkeypatch, [
        {"symbol": "0700.HK", "price": 428.2, "changePercentage": 1.23, "volume": 10},
        {"symbol": "9988.HK", "price": 108.2, "changePercentage": -2.5, "volume": 20},
    ], calls)
    q = ss.get_exchange_quotes("HKSE")
    assert q["00700.HK"]["change_pct"] == 1.23
    assert q["09988.HK"]["price"] == 108.2
    # cached: a second read in the TTL makes no second call
    ss.get_exchange_quotes("HKSE")
    assert calls == ["HKSE"]


def test_overlay_fills_every_row(monkeypatch):
    _patch(monkeypatch, [
        {"symbol": "D05.SI", "price": 78.56, "changePercentage": 0.94, "volume": 5},
    ], [])
    result = {"items": [{"symbol": "D05.SI", "price": 70.0, "change_pct": None},
                        {"symbol": "ZZZ.SI", "price": 1.0, "change_pct": None}]}
    ss.overlay_exchange_quotes(result, "SES")
    d05, zzz = result["items"]
    assert d05["price"] == 78.56 and d05["change_pct"] == 0.94
    assert zzz["change_pct"] is None   # unquoted row untouched, never zero-filled


def test_overlay_never_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ss.requests, "get", _boom)
    monkeypatch.setattr(ss, "_get_fmp_key", lambda: "k")
    ss._EXCHANGE_QUOTES.clear()
    result = {"items": [{"symbol": "D05.SI", "change_pct": None}]}
    assert ss.overlay_exchange_quotes(result, "SES") is result
