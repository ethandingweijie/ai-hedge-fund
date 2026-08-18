from __future__ import annotations

"""Learned multiple/margin basis for GS-style SOTP segments (v1).

Characteristic models fitted offline (``.stage7_fit_calibration.py``) over a
broad pure-play + coverage comp universe so segment multiples and EBIT
margins rest on market data instead of any single research note:

    ln(multiple) = a + Σ_arch b_arch·1[arch] + b_g·g_fwd + b_s·ln(scale)
                     + c·1[china]            (targets: pe, ev_rev)
    margin       = a + Σ_arch b_arch·1[arch] + b_g·g_fwd + b_s·ln(scale)

The jurisdiction haircut and growth adjustment that used to be POLICY
CONSTANTS (``sotp_multiple_basis.DEFAULT_CN_HAIRCUT`` / ``GROWTH_ELASTICITY``)
become fitted, reported coefficients (c and b_g).

Containment — a fitted sector never applies outside its taxonomy:
``predict`` refuses archetypes absent from the fit or carrying fewer than
MIN_ARCH_OBS observations; callers then fall back to the median heuristic /
LLM behavior that predates this module.

Determinism: closed-form ridge via ``np.linalg.lstsq``, sorted archetype
vocabulary, no RNG — the same panel always produces the same coefficients.
"""

import json
import math
from pathlib import Path

import numpy as np

# ── Policy ────────────────────────────────────────────────────────────────────
ARTIFACT_VERSION = 1
MULTIPLE_TARGETS = ("pe", "ev_rev")
MARGIN_TARGET = "margin"
_TARGETS = MULTIPLE_TARGETS + (MARGIN_TARGET,)
MIN_ARCH_OBS = 3          # containment gate at inference (per archetype)
MIN_TARGET_OBS = 8        # minimum rows to fit a target at all
MARGIN_CLAMP = (0.0, 0.60)
_RIDGE_LAMBDA = 1e-2
_SIGMA_FALLBACK = {"pe": 0.30, "ev_rev": 0.30, "margin": 0.06}

DEFAULT_ARTIFACT_PATH = (Path(__file__).resolve().parents[2] / "data"
                         / "segment_calibration_v1.json")


# ── Fit ───────────────────────────────────────────────────────────────────────

def _feature_names(vocab: list[str]) -> list[str]:
    # Baseline-drop encoding: the first archetype (sorted vocab) is absorbed
    # into the intercept. Full one-hot + intercept is rank-deficient and
    # ridge splits confounded effects arbitrarily (double-counting observed
    # in the 2026-08-18 fit); baseline-drop makes every coefficient — in
    # particular ``china`` — interpretable relative to the baseline arch.
    return (["intercept"] + [f"arch:{a}" for a in vocab[1:]]
            + ["g_fwd_pct", "log_revenue_scale", "china"])


def _ridge(X: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """Closed-form Tikhonov-regularized least squares (deterministic)."""
    A = np.vstack([X, math.sqrt(_RIDGE_LAMBDA) * np.eye(k)])
    b = np.concatenate([y, np.zeros(k)])
    coeffs, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    return coeffs


def fit_model(panel: list[dict], fit_end_date: str) -> dict:
    """Fit per-target characteristic models over panel rows shaped
    ``{ticker, archetype, china, pe, ev_rev, margin, g_fwd_pct,
    log_revenue_scale}``. Raises ``ValueError`` when nothing is fittable.
    """
    rows = [r for r in panel if r.get("archetype")]
    if len(rows) < MIN_TARGET_OBS:
        raise ValueError(f"panel too small: {len(rows)} usable rows")
    vocab = sorted({r["archetype"] for r in rows})
    names = _feature_names(vocab)
    idx = {n: i for i, n in enumerate(names)}
    k = len(names)

    def x_of(r: dict) -> np.ndarray:
        x = np.zeros(k)
        x[idx["intercept"]] = 1.0
        arch_feat = f"arch:{r['archetype']}"
        if arch_feat in idx:  # baseline archetype carries no dummy
            x[idx[arch_feat]] = 1.0
        x[idx["g_fwd_pct"]] = float(r.get("g_fwd_pct") or 0.0)
        x[idx["log_revenue_scale"]] = float(r.get("log_revenue_scale") or 0.0)
        x[idx["china"]] = 1.0 if r.get("china") else 0.0
        return x

    archetype_n: dict[str, int] = {}
    for r in rows:
        archetype_n[r["archetype"]] = archetype_n.get(r["archetype"], 0) + 1

    targets: dict[str, dict] = {}
    for t in _TARGETS:
        if t in MULTIPLE_TARGETS:
            obs = [(r, math.log(float(r[t]))) for r in rows
                   if r.get(t) is not None and float(r[t]) > 0]
        else:
            obs = [(r, float(r[t])) for r in rows if r.get(t) is not None]
        if len(obs) < MIN_TARGET_OBS:
            continue
        X = np.vstack([x_of(r) for r, _ in obs])
        y = np.array([v for _, v in obs])
        coeffs = _ridge(X, y, k)
        resid = y - X @ coeffs
        n = len(obs)
        dof = max(n - k, 1)
        ssr = float(resid @ resid)
        sigma = math.sqrt(ssr / dof) if ssr > 0 else _SIGMA_FALLBACK[t]
        sst = float(((y - y.mean()) ** 2).sum())
        targets[t] = {
            "feature_names": names,
            "coeffs": [float(c) for c in coeffs],
            "n_obs": n,
            "residual_std": round(sigma, 4),
            "r2": round(1.0 - ssr / sst, 4) if sst > 0 else 0.0,
        }
    if not targets:
        raise ValueError("no target had enough observations to fit")
    return {
        "version": ARTIFACT_VERSION,
        "fit_end_date": fit_end_date,
        "n_obs": len(rows),
        "archetypes": vocab,
        "archetype_n": archetype_n,
        "targets": targets,
        "observations": [],
        "outcome_corrections": {},
    }


# ── Inference ─────────────────────────────────────────────────────────────────

def predict(artifact: dict | None, target: str, chars: dict) -> dict | None:
    """Point + ±1σ CI for one target at the given characteristics.

    Returns None (caller falls back) when the artifact is invalid, the
    target wasn't fitted, or the archetype is unseen/thin — the containment
    gate that keeps a fitted sector inside its taxonomy.
    """
    if not _valid(artifact) or target not in artifact["targets"]:
        return None
    arch = chars.get("archetype")
    if not arch or artifact["archetype_n"].get(arch, 0) < MIN_ARCH_OBS:
        return None
    blk = artifact["targets"][target]
    names = blk["feature_names"]
    idx = {n: i for i, n in enumerate(names)}
    x = np.zeros(len(names))
    x[idx["intercept"]] = 1.0
    arch_feat = f"arch:{arch}"
    if arch_feat in idx:
        x[idx[arch_feat]] = 1.0
    x[idx["g_fwd_pct"]] = float(chars.get("g_fwd_pct") or 0.0)
    x[idx["log_revenue_scale"]] = float(chars.get("log_revenue_scale") or 0.0)
    x[idx["china"]] = 1.0 if chars.get("china") else 0.0
    dot = float(x @ np.asarray(blk["coeffs"], dtype=float))
    sigma = float(blk["residual_std"])
    if target in MULTIPLE_TARGETS:
        point = math.exp(dot)
        lo, hi = math.exp(dot - sigma), math.exp(dot + sigma)
    else:
        lo = min(max(dot - sigma, MARGIN_CLAMP[0]), MARGIN_CLAMP[1])
        hi = min(max(dot + sigma, MARGIN_CLAMP[0]), MARGIN_CLAMP[1])
        point = min(max(dot, MARGIN_CLAMP[0]), MARGIN_CLAMP[1])
    return {"point": round(point, 4), "ci": [round(lo, 4), round(hi, 4)],
            "sigma": sigma, "archetype_n": artifact["archetype_n"][arch]}


def coefficient(artifact: dict | None, target: str, name: str) -> float | None:
    """Named coefficient (H-report: learned constants vs policy)."""
    blk = (artifact or {}).get("targets", {}).get(target)
    if not blk or name not in blk.get("feature_names", []):
        return None
    return blk["coeffs"][blk["feature_names"].index(name)]


# ── Artifact load / validation ────────────────────────────────────────────────

def _valid(a) -> bool:
    try:
        if not isinstance(a, dict) or a.get("version") != ARTIFACT_VERSION:
            return False
        tg = a.get("targets")
        if not isinstance(tg, dict) or not tg:
            return False
        for blk in tg.values():
            names, coeffs = blk["feature_names"], blk["coeffs"]
            if len(names) != len(coeffs):
                return False
            if not all(math.isfinite(c) for c in coeffs):
                return False
        return isinstance(a.get("archetype_n"), dict)
    except Exception:
        return False


def load_artifact(path: str | Path | None = None) -> dict | None:
    """Load + validate the calibration artifact; None when missing/corrupt
    (callers silently keep the pre-learning behavior)."""
    p = Path(path) if path else DEFAULT_ARTIFACT_PATH
    try:
        with open(p, encoding="utf-8") as fh:
            a = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return a if _valid(a) else None
