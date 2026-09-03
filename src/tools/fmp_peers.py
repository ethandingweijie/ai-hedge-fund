"""Company-level peer sets from FMP, cached so a run is reproducible.

FMP's `stable/stock-peers` returns roughly ten similar listed companies, and
it covers all three markets this project values -- verified live for AMD,
0700.HK and D05.SI, where FMP's segmentation endpoints return nothing at all.
That makes it the one mechanical peer source that survives the move to HKEX
and SGX.

What it is NOT
--------------
These are COMPANY peers chosen on sector and market cap, not pure-plays for a
business segment. AMD's list comes back as AMAT, ARM and ASML -- semiconductor
equipment and IP, none of which is a comp for AMD's Client or Gaming segment.
Valuing a segment against them would repeat, with a machine-generated peer
list, exactly the mistake that made `AMD Gaming -> games_media` dangerous.

So this is a company-level tier: a group cross-check, a deterministic fallback
for a segment no pinned archetype covers, and a starting point for a human to
promote into `segment_peer_sets.json`. It is always labelled as a company
proxy so a report can never present it as a segment comp.

Determinism
-----------
Peer lists move as market caps move, so the response is cached on disk by
(ticker, end_date). Two runs of the same valuation see the same peers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

_CACHE_DIR = Path(os.environ.get(
    "FMP_PEERS_CACHE_DIR",
    Path(__file__).resolve().parents[2] / ".cache" / "fmp_peers"))

MAX_PEERS = 10


def _cache_path(ticker: str, end_date: str) -> Path:
    key = hashlib.sha1(f"{ticker}|{end_date}".encode()).hexdigest()[:16]
    safe = "".join(c if c.isalnum() else "_" for c in ticker)
    return _CACHE_DIR / f"{safe}_{key}.json"


def _load(ticker: str, end_date: str) -> Optional[list]:
    p = _cache_path(ticker, end_date)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("peers")
    except Exception:                                  # noqa: BLE001
        return None


def _store(ticker: str, end_date: str, peers: list) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(ticker, end_date).write_text(
            json.dumps({"ticker": ticker, "end_date": end_date,
                        "peers": peers}, indent=1), encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        pass


def get_fmp_peers(ticker: str, end_date: str,
                  api_key: str | None = None) -> list[str]:
    """Company peers for `ticker`, never including the ticker itself."""
    # FMP's own symbol form, NOT the repo canonical: FMP rejects the 5-digit
    # HK canonical (00700.HK) and wants 0700.HK, and writes US class shares
    # with a hyphen. Passing the canonical form returns an empty list rather
    # than an error, so the miss is silent.
    from src.tools.fmp_transcripts import to_fmp_symbol
    t = to_fmp_symbol(ticker)
    if not t:
        return []
    cached = _load(t, end_date)
    if cached is not None:
        return cached

    from src.tools.api import _STABLE, _fmp_get
    try:
        rows = _fmp_get(f"{_STABLE}/stock-peers", {"symbol": t},
                        api_key or os.getenv("FMP_API_KEY"))
    except Exception as exc:                           # noqa: BLE001
        print(f"  [fmp-peers] {t}: {type(exc).__name__}")
        return []
    if not isinstance(rows, list):
        return []

    drop = {t.upper(), (ticker or "").strip().upper()}
    peers, seen = [], set()
    for row in rows:
        sym = (row or {}).get("symbol") if isinstance(row, dict) else None
        if not sym:
            continue
        sym = str(sym).strip().upper()
        if sym in drop or sym in seen:
            continue
        seen.add(sym)
        peers.append(sym)
        if len(peers) >= MAX_PEERS:
            break
    # Never cache an empty list: an outage would otherwise freeze this ticker
    # as peerless for the whole end_date.
    if peers:
        _store(t, end_date, peers)
    return peers
