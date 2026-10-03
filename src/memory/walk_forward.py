"""Walk-forward backtest over the outcome ledger (BT2), with lookahead guards (BT4).

A calibration proposal is only worth a shadow slot if it beats live on runs it
never saw. This harness is the only place a proposal is scored, and it builds
the training set itself so a fitter cannot see the future by accident.

Folds are rolling-origin and time-ordered. For cutoffs T1 < T2 < ... :

    train(Ti) = labels whose run AND label both dated on or before Ti
    test(Ti)  = runs dated in (Ti, Ti+1]

Each test run is scored once, in the one fold whose window holds it.

The lookahead that matters for a ledger is subtle: a run made before Ti whose
365-day label matured after Ti is NOT training data at Ti, even though the run
is old. Admitting it hands the fitter a price from the test window. Every fold
asserts this, and that no run sits on both sides (LeakageError).

A second guard catches leaks the builder cannot see -- a fitter that reads the
database directly. If a candidate is implausibly good on a fold (miss under 2%
where live misses by more than 10%), the harness refuses the result rather
than reporting a triumph.

A fitter is `fit(train_rows) -> predict(row) -> Optional[float]`: given the
training rows, return a function giving a revised IV for a row, or None to
leave that row at its live IV.

Scope: this replays re-valuations built from what each run STORED (method
values, weights, IV). A full point-in-time engine replay -- statements cut at
their filing date, restated figures, consensus snapshots -- is a different
harness with its own hazards and is not attempted here.

Pass criteria (all must hold):
  * at least MIN_FOLDS folds evaluated
  * candidate median miss below live in >= 70% of folds
  * pooled median signed error no further from zero than live
  * no (market, profile) cell with >= MIN_CELL_N runs worse by > 2 pp
  * bear-bull band coverage no more than 5 pp below live
  * no named date window with >= MIN_CELL_N runs worse by > 3 pp
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Callable, Iterable, Optional

from src.data import db as _db
from src.memory import valuation_outcomes as vo

Predict = Callable[[dict], Optional[float]]
Fit = Callable[[list[dict]], Predict]

MIN_FOLDS = 2
MIN_TRAIN = 10
MIN_CELL_N = 5
IMPROVED_FOLD_SHARE = 0.70
CELL_TOLERANCE_PP = 2.0
WINDOW_TOLERANCE_PP = 3.0
BAND_TOLERANCE = 0.05
IMPLAUSIBLE_MISS = math.log(1.02)
IMPLAUSIBLE_LIVE_FLOOR = math.log(1.10)


class LeakageError(RuntimeError):
    """A fold's training data reached past its cutoff."""


@dataclass
class Fold:
    cutoff: date
    test_end: date
    train: list[dict]
    test: list[dict]


# ── rows ────────────────────────────────────────────────────────────────────

def _d(v) -> Optional[date]:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def load_rows(horizon: str, *, family: str = "iv") -> list[dict]:
    """Ledger rows for one horizon, joined to the run's stored dcf_range.

    family="pt" (self-learning loops 3 and 4): the rows a target calibration is scored on,
    joined to run_features instead -- the engine's 12-month target, spot, the capture it
    used, the three scenario targets and intrinsic values and the scenario probabilities.
    Price horizons only: a target is never fitted toward the consensus label."""
    if horizon not in vo.HORIZONS:
        raise ValueError(f"unknown horizon {horizon!r}; allowed: {list(vo.HORIZONS)}")
    if family == "pt":
        return _load_pt_rows(horizon)
    from src.memory import valuation_attribution as va
    vo._ensure_tables()
    rows = _db.query(
        "SELECT run_id, ticker, run_date, label_date, label_value, base_iv, "
        "bear_iv, bull_iv, market, profile FROM valuation_outcomes WHERE horizon = ?",
        [horizon])
    dcf = va._load_dcf({(r["run_id"], (r["ticker"] or "").upper()) for r in rows})
    out = []
    for r in rows:
        row = {k: r[k] for k in ("run_id", "ticker", "label_value", "base_iv",
                                 "bear_iv", "bull_iv", "market", "profile")}
        row.update(run_date=_d(r["run_date"]), label_date=_d(r["label_date"]),
                   dcf=dcf.get((r["run_id"], (r["ticker"] or "").upper())) or {})
        if row["run_date"] and row["label_date"] and vo._pos(row["base_iv"]) \
                and vo._pos(row["label_value"]):
            out.append(row)
    return out


PT_ROW_KEYS = ("pt_12m", "spot", "capture", "pt_bear", "pt_base", "pt_bull",
               "iv_bear", "iv_base", "iv_bull", "prob_bear", "prob_base", "prob_bull",
               "pt_calibration_version")


def _load_pt_rows(horizon: str) -> list[dict]:
    if horizon == vo.CONSENSUS:
        raise ValueError("a target calibration is never scored against the consensus label")
    from src.memory import run_features as rf
    vo._ensure_tables()
    rf._ensure_tables()
    rows = _db.query(
        "SELECT o.run_id, o.ticker, o.run_date, o.label_date, o.label_value, o.market, "
        "o.profile, " + ", ".join(f"f.{k}" for k in PT_ROW_KEYS) + " "
        "FROM valuation_outcomes o JOIN run_features f "
        "ON f.run_id = o.run_id AND f.ticker = o.ticker "
        "WHERE o.horizon = ? AND " + rf.AGENT_ONLY_WHERE, [horizon])
    out = []
    for r in rows:
        row = {k: r[k] for k in ("run_id", "ticker", "label_value", "market", "profile")}
        row.update({k: r[k] for k in PT_ROW_KEYS})
        row.update(run_date=_d(r["run_date"]), label_date=_d(r["label_date"]),
                   base_iv=r["iv_base"], bear_iv=r["iv_bear"], bull_iv=r["iv_bull"], dcf={})
        if row["run_date"] and row["label_date"] and vo._pos(row["pt_12m"]) \
                and vo._pos(row["spot"]) and vo._pos(row["label_value"]):
            out.append(row)
    return out


# ── folds ───────────────────────────────────────────────────────────────────

def _train_rows(rows: list[dict], cutoff: date) -> list[dict]:
    return [r for r in rows if r["run_date"] <= cutoff and r["label_date"] <= cutoff]


def _assert_no_lookahead(fold: Fold) -> None:
    late = [r for r in fold.train
            if r["run_date"] > fold.cutoff or r["label_date"] > fold.cutoff]
    if late:
        r = late[0]
        raise LeakageError(
            f"fold at {fold.cutoff}: training row {r['run_id']}/{r['ticker']} "
            f"(run {r['run_date']}, label {r['label_date']}) postdates the cutoff")
    both = ({(r["run_id"], r["ticker"]) for r in fold.train}
            & {(r["run_id"], r["ticker"]) for r in fold.test})
    if both:
        raise LeakageError(f"fold at {fold.cutoff}: {sorted(both)[:3]} in train and test")


def build_folds(rows: list[dict], n_folds: int = 4) -> list[Fold]:
    dates = sorted({r["run_date"] for r in rows})
    if len(dates) < n_folds + 1:
        return []
    last = dates[-1]
    cuts = [dates[int(len(dates) * i / (n_folds + 1))] for i in range(1, n_folds + 1)]
    cuts = sorted(set(cuts))
    folds = []
    for i, cutoff in enumerate(cuts):
        end = cuts[i + 1] if i + 1 < len(cuts) else last
        test = [r for r in rows if cutoff < r["run_date"] <= end]
        folds.append(Fold(cutoff, end, _train_rows(rows, cutoff), test))
    return folds


# ── scoring ─────────────────────────────────────────────────────────────────

def _scored(rows: list[dict], predict: Predict, *, value_key: str = "base_iv",
            band_keys: tuple[str, str] = ("bear_iv", "bull_iv")) -> list[dict]:
    out = []
    for r in rows:
        live = float(r[value_key])
        try:
            revised = predict(r)
        except Exception:                                  # noqa: BLE001
            revised = None
        cand = vo._pos(revised) or live
        label = float(r["label_value"])
        k = cand / live
        bear, bull = vo._pos(r.get(band_keys[0])), vo._pos(r.get(band_keys[1]))
        band = lambda s: (int(min(bear, bull) * s <= label <= max(bear, bull) * s)
                          if bear and bull else None)
        out.append({**r, "live_err": math.log(live / label),
                    "cand_err": math.log(cand / label),
                    "live_band": band(1.0), "cand_band": band(k)})
    return out


def _miss_pct(errs: list[float]) -> Optional[float]:
    return round((math.exp(median(abs(e) for e in errs)) - 1) * 100, 2) if errs else None


def _coverage(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def walk_forward(rows: list[dict], fit: Fit, *, n_folds: int = 4,
                 windows: Iterable[tuple[str, str, str]] = (), value_key: str = "base_iv",
                 band_keys: tuple[str, str] = ("bear_iv", "bull_iv")) -> dict:
    """Score `fit` against live, fold by fold. Raises LeakageError on lookahead.
    `value_key` is the live figure a fitter revises (the IV, or the 12-month target for
    the pt family) and `band_keys` the bear/bull pair whose coverage must not fall."""
    folds = build_folds(rows, n_folds)
    fold_reports, pooled, skipped = [], [], []
    for fold in folds:
        _assert_no_lookahead(fold)
        if len(fold.train) < MIN_TRAIN or not fold.test:
            skipped.append({"cutoff": fold.cutoff.isoformat(), "train": len(fold.train),
                            "test": len(fold.test)})
            continue
        predict = fit(list(fold.train))
        scored = _scored(fold.test, predict, value_key=value_key, band_keys=band_keys)
        live_miss = median(abs(s["live_err"]) for s in scored)
        cand_miss = median(abs(s["cand_err"]) for s in scored)
        if cand_miss < IMPLAUSIBLE_MISS and live_miss > IMPLAUSIBLE_LIVE_FLOOR:
            raise LeakageError(
                f"fold at {fold.cutoff}: candidate misses by {math.exp(cand_miss) - 1:.1%} "
                f"where live misses by {math.exp(live_miss) - 1:.1%} -- not credible "
                f"without information from the test window")
        fold_reports.append({
            "cutoff": fold.cutoff.isoformat(), "test_end": fold.test_end.isoformat(),
            "train": len(fold.train), "test": len(scored),
            "live_miss_pct": _miss_pct([s["live_err"] for s in scored]),
            "cand_miss_pct": _miss_pct([s["cand_err"] for s in scored]),
            "improved": cand_miss < live_miss,
        })
        pooled.extend(scored)

    reasons: list[str] = []
    report: dict = {"folds": fold_reports, "skipped_folds": skipped, "n_test": len(pooled)}
    if len(fold_reports) < MIN_FOLDS:
        reasons.append(f"only {len(fold_reports)} fold(s) evaluated; need {MIN_FOLDS}")
        report["verdict"] = {"passed": False, "reasons": reasons}
        return report

    share = sum(f["improved"] for f in fold_reports) / len(fold_reports)
    if share < IMPROVED_FOLD_SHARE:
        reasons.append(f"improved in {share:.0%} of folds; need {IMPROVED_FOLD_SHARE:.0%}")

    live_bias = median(s["live_err"] for s in pooled)
    cand_bias = median(s["cand_err"] for s in pooled)
    if abs(cand_bias) > abs(live_bias):
        reasons.append(f"signed bias moved away from zero ({vo._pct(live_bias)}% -> "
                       f"{vo._pct(cand_bias)}%)")

    cells: dict[str, list[dict]] = {}
    for s in pooled:
        cells.setdefault(f"{s.get('market') or '?'} / {s.get('profile') or '?'}", []).append(s)
    cell_report = {}
    for name, ss in sorted(cells.items()):
        live_pct = _miss_pct([s["live_err"] for s in ss])
        cand_pct = _miss_pct([s["cand_err"] for s in ss])
        cell_report[name] = {"n": len(ss), "live_miss_pct": live_pct, "cand_miss_pct": cand_pct}
        if len(ss) >= MIN_CELL_N and cand_pct - live_pct > CELL_TOLERANCE_PP:
            reasons.append(f"cell {name} worse by {cand_pct - live_pct:.1f} pp (n={len(ss)})")

    live_cov = _coverage([s["live_band"] for s in pooled])
    cand_cov = _coverage([s["cand_band"] for s in pooled])
    if live_cov is not None and cand_cov is not None and cand_cov < live_cov - BAND_TOLERANCE:
        reasons.append(f"band coverage fell {live_cov:.0%} -> {cand_cov:.0%}")

    window_report = {}
    for name, start, end in windows:
        ss = [s for s in pooled if _d(start) <= s["run_date"] <= _d(end)]
        if not ss:
            continue
        live_pct = _miss_pct([s["live_err"] for s in ss])
        cand_pct = _miss_pct([s["cand_err"] for s in ss])
        window_report[name] = {"n": len(ss), "live_miss_pct": live_pct, "cand_miss_pct": cand_pct}
        if len(ss) >= MIN_CELL_N and cand_pct - live_pct > WINDOW_TOLERANCE_PP:
            reasons.append(f"window {name} worse by {cand_pct - live_pct:.1f} pp (n={len(ss)})")

    report.update({
        "improved_fold_share": round(share, 3),
        "pooled": {"live_bias_pct": vo._pct(live_bias), "cand_bias_pct": vo._pct(cand_bias),
                   "live_miss_pct": _miss_pct([s["live_err"] for s in pooled]),
                   "cand_miss_pct": _miss_pct([s["cand_err"] for s in pooled]),
                   "live_band_coverage": live_cov, "cand_band_coverage": cand_cov},
        "cells": cell_report, "windows": window_report,
        "verdict": {"passed": not reasons, "reasons": reasons},
    })
    return report


# ── reference fitter ────────────────────────────────────────────────────────

def market_bias_fit(train: list[dict], *, min_n: int = MIN_CELL_N,
                    shrink: float = 10.0) -> Predict:
    """Per-market bias correction: scale IV by exp(-b), b = median log error,
    shrunk toward zero by n/(n+shrink). The simplest calibration there is --
    the baseline any real proposal has to beat."""
    by_market: dict[str, list[float]] = {}
    for r in train:
        by_market.setdefault(r.get("market") or "?", []).append(
            math.log(float(r["base_iv"]) / float(r["label_value"])))
    bias = {m: median(e) * len(e) / (len(e) + shrink)
            for m, e in by_market.items() if len(e) >= min_n}

    def predict(row: dict) -> Optional[float]:
        b = bias.get(row.get("market") or "?")
        return None if b is None else float(row["base_iv"]) * math.exp(-b)

    predict.bias = bias  # type: ignore[attr-defined]
    return predict
