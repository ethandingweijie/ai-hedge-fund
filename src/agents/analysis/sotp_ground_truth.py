"""SOTP ground truth -- grade a SOTP table against sell-side reference ranges.

A total can land in range for the wrong reasons. The frozen BABA snapshot
reaches $174/ADS, inside the $152-201 consensus band, while valuing China
commerce at roughly twice its reference range and carrying net DEBT where the
reference carries $25-32/ADS of net cash. So the check is per segment as well
as in total, and the report shows both.

Everything is compared in USD per ADS. Engine tables carry USD values over the
run's share count; an HK line's shares are ordinary shares, so its per-share
USD value is multiplied by the ADS ratio. No FX is involved.

The total band also gates LIVE extraction (see `plausible`): a live SOTP
outside the band widened by PLAUSIBILITY_TOLERANCE is replaced with the
validated snapshot -- how the 25 Aug production run published $61/ADS.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "sotp_ground_truth.json"
PLAUSIBILITY_TOLERANCE = 0.15


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        with open(_DATA_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc.get("tickers") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def ground_truth_for(ticker: str) -> tuple[Optional[dict], float]:
    """(reference entry, per-ADS factor) for a ticker or its HK line."""
    from src.agents.analysis.sotp_snapshot import canonical_sotp_key
    key = canonical_sotp_key(ticker)
    for gt_ticker, entry in _load().items():
        if canonical_sotp_key(gt_ticker) == key:
            return entry, 1.0
        hk = entry.get("hk_line")
        if hk and canonical_sotp_key(hk) == key:
            return entry, float(entry.get("ads_ratio") or 1.0)
    return None, 1.0


def _status(value: float, band: list) -> str:
    lo, hi = float(band[0]), float(band[1])
    return "below" if value < lo else "above" if value > hi else "in_range"


def check_lookthrough(ticker: str, result: Optional[dict], shares: float) -> Optional[dict]:
    """Grade a holdco look-through (holdco_sotp.look_through_value) against a
    broker reference recorded in the listing's own currency per share.

    Parts are matched to reference parts by keyword, first match wins, so a
    listed-trust keyword placed before "infrastructure" keeps Keppel
    Infrastructure Trust out of the operating infrastructure bucket."""
    gt, _ = ground_truth_for(ticker)
    if not gt or gt.get("kind") != "lookthrough" or not result or not shares or shares <= 0:
        return None
    from src.agents.analysis.sotp_multiple_basis import normalize_key

    buckets: dict[str, float] = {p["name"]: 0.0 for p in gt["parts"]}
    unmatched: list[str] = []
    for part in result.get("parts") or []:
        key = normalize_key(str(part.get("division", "")))
        for ref in gt["parts"]:
            if any(k in key for k in ref["keywords"]):
                buckets[ref["name"]] += float(part.get("value") or 0.0)
                break
        else:
            unmatched.append(str(part.get("division", "")))
    parts = []
    for ref in gt["parts"]:
        v = buckets[ref["name"]] / shares
        parts.append({"name": ref["name"], "value_per_share": round(v, 4), "range": ref["per_share"],
                      "status": _status(v, ref["per_share"]) if buckets[ref["name"]] else "missing"})
    total = float(result.get("net_asset_value") or 0.0) / shares
    lo, hi = gt["total_per_share"]
    return {"source": gt["source"], "as_of": gt["as_of"], "unit": gt["unit"],
            "total": {"value_per_share": round(total, 4), "range": gt["total_per_share"],
                      "status": _status(total, gt["total_per_share"])},
            "parts": parts, "unmatched_parts": unmatched,
            "off_range": [p["name"] for p in parts if p["status"] != "in_range"],
            "plausible": lo * (1 - PLAUSIBILITY_TOLERANCE) <= total <= hi * (1 + PLAUSIBILITY_TOLERANCE)}


def check_table(ticker: str, table: Optional[dict]) -> Optional[dict]:
    """Per-segment and total grading of an engine SOTP table, or None when
    there is no reference for this ticker or no usable table."""
    gt, factor = ground_truth_for(ticker)
    if not gt or gt.get("kind") == "lookthrough" or not table or not table.get("shares"):
        return None
    from src.agents.analysis.sotp_multiple_basis import normalize_key

    shares = float(table["shares"])
    per_ads = lambda usd: float(usd or 0.0) / shares * factor   # noqa: E731

    buckets: dict[str, float] = {s["name"]: 0.0 for s in gt["segments"]}
    unmatched: list[str] = []
    for row in table.get("rows") or []:
        key = normalize_key(str(row.get("name", "")))
        for seg in gt["segments"]:                  # first match wins, in order
            if any(k in key for k in seg["keywords"]):
                buckets[seg["name"]] += float(row.get("value") or 0.0)
                break
        else:
            unmatched.append(str(row.get("name", "")))

    segments = []
    for seg in gt["segments"]:
        v = per_ads(buckets[seg["name"]])
        segments.append({"name": seg["name"], "value_per_ads": round(v, 2),
                         "range": seg["per_ads"], "multiple_range": seg["multiple_range"],
                         "status": _status(v, seg["per_ads"]) if buckets[seg["name"]] else "missing"})

    bs = gt["balance_sheet"]
    bs_value = per_ads((table.get("associates") or 0.0) + (table.get("net_cash") or 0.0))
    total = float(table.get("per_share") or 0.0) * factor
    lo, hi = gt["total_per_ads"]
    return {
        "source": gt["source"], "as_of": gt["as_of"], "unit": "USD per ADS",
        "total": {"value_per_ads": round(total, 2), "range": gt["total_per_ads"],
                  "status": _status(total, gt["total_per_ads"])},
        "segments": segments,
        "balance_sheet": {"name": bs["name"], "value_per_ads": round(bs_value, 2),
                          "range": bs["per_ads"], "status": _status(bs_value, bs["per_ads"])},
        "unmatched_rows": unmatched,
        "off_range": [s["name"] for s in segments if s["status"] != "in_range"]
                     + ([bs["name"]] if _status(bs_value, bs["per_ads"]) != "in_range" else []),
        "plausible": lo * (1 - PLAUSIBILITY_TOLERANCE) <= total <= hi * (1 + PLAUSIBILITY_TOLERANCE),
    }
