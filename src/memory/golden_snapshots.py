"""Golden snapshot store and diff (Phase 0, item 7).

``tests/golden/snapshots.json`` is the single pinned baseline for the
valuation engine's end-to-end output. It is compared against, never edited by
hand; the only writer is ``--update-snapshots`` with a ``GOLDEN_UPDATE_REASON``.

Two files, deliberately separate:

  ``tests/fixtures/golden/<T>/``   recorded INPUTS (raw FMP responses, frozen
                                   DB reads). Re-recorded when the engine's
                                   input surface changes.
  ``tests/golden/snapshots.json``  pinned OUTPUTS. Moving this file is the
                                   thing that needs a written reason, because
                                   it is the only artefact that says "the
                                   answer changed".

Tolerance is ±5% relative on numeric leaves and exact on everything else. The
tolerance exists because a golden suite that fails on the sixth decimal would
be switched off; it is not a licence to drift, since every move inside
tolerance is still reported in the diff table on a failure.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "tests" / "golden" / "snapshots.json"
CHANGELOG_PATH = ROOT / "tests" / "golden" / "CHANGELOG.md"

#: Relative tolerance on numeric leaves.
TOLERANCE = 0.05

#: Below this magnitude a relative test is meaningless (a field pinned at 0.0
#: would permit anything), so zero-ish leaves are compared absolutely.
_ZERO_FLOOR = 1e-12
_ABS_TOL = 1e-9


# ── Numeric comparison ──────────────────────────────────────────────────────

def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def numeric_close(expected: Any, actual: Any, tol: float = TOLERANCE) -> bool:
    """True when two leaves agree within tolerance (or are exactly equal)."""
    if expected == actual:
        return True
    if not (_is_number(expected) and _is_number(actual)):
        return False
    e, a = float(expected), float(actual)
    if abs(e) < _ZERO_FLOOR:
        return abs(a - e) <= _ABS_TOL
    return abs(a - e) / abs(e) <= tol


def _pct(expected: Any, actual: Any) -> str:
    if not (_is_number(expected) and _is_number(actual)):
        return ""
    e = float(expected)
    if abs(e) < _ZERO_FLOOR:
        return "n/a"
    return f"{(float(actual) - e) / e * 100.0:+.2f}%"


# ── Diff ────────────────────────────────────────────────────────────────────

def diff_fields(expected: dict, actual: dict,
                tol: float = TOLERANCE) -> list[dict]:
    """Field-by-field delta between two flat projections.

    Returns one row per disagreement, each with ``path``, ``kind``,
    ``expected``, ``actual`` and (for numerics) ``delta``. Empty list means
    the replay reproduces the baseline.
    """
    rows: list[dict] = []
    for path in sorted(set(expected) | set(actual)):
        in_e, in_a = path in expected, path in actual
        if in_e and not in_a:
            rows.append({"path": path, "kind": "gone_from_replay",
                         "expected": expected[path], "actual": None, "delta": ""})
            continue
        if in_a and not in_e:
            rows.append({"path": path, "kind": "new_in_replay",
                         "expected": None, "actual": actual[path], "delta": ""})
            continue
        e, a = expected[path], actual[path]
        if isinstance(e, bool) or isinstance(a, bool):
            if e != a:
                rows.append({"path": path, "kind": "flag_changed",
                             "expected": e, "actual": a, "delta": ""})
            continue
        if _is_number(e) and _is_number(a):
            if not numeric_close(e, a, tol):
                rows.append({"path": path, "kind": "out_of_tolerance",
                             "expected": e, "actual": a, "delta": _pct(e, a)})
            continue
        if isinstance(e, list) or isinstance(a, list):
            if e != a:
                rows.append({"path": path, "kind": "list_changed",
                             "expected": e, "actual": a, "delta": ""})
            continue
        if e != a:
            rows.append({"path": path, "kind": "value_changed",
                         "expected": e, "actual": a, "delta": _pct(e, a)})
    return rows


def format_diff(rows: list[dict], limit: int = 60) -> str:
    """Render diff rows as an aligned table for a pytest failure message."""
    if not rows:
        return "  (no differences)"
    shown = rows[:limit]
    cols = ("path", "kind", "expected", "actual", "delta")
    table = []
    for r in shown:
        table.append([r["path"], r["kind"],
                      _short(r["expected"]), _short(r["actual"]), r["delta"]])
    widths = [max(len(str(c)) for c in [h] + [row[i] for row in table])
              for i, h in enumerate(cols)]
    widths = [min(w, 46) for w in widths]

    def line(cells: list[str]) -> str:
        return "  " + "  ".join(str(c)[:46].ljust(widths[i])
                               for i, c in enumerate(cells)).rstrip()

    out = [line(list(cols)), "  " + "-".join("-" * w for w in widths)]
    out += [line(row) for row in table]
    if len(rows) > limit:
        out.append(f"  ... {len(rows) - limit} more row(s) suppressed")
    return "\n".join(out)


def _short(v: Any) -> str:
    if v is None:
        return "-"
    s = json.dumps(v, default=str) if isinstance(v, (list, dict)) else str(v)
    return s if len(s) <= 46 else s[:43] + "..."


# ── Store IO ────────────────────────────────────────────────────────────────

def load() -> dict:
    if not SNAPSHOT_PATH.exists():
        raise FileNotFoundError(str(SNAPSHOT_PATH))
    with open(SNAPSHOT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def exists() -> bool:
    return SNAPSHOT_PATH.exists()


def tickers_in(doc: dict) -> list[str]:
    return sorted(k for k in doc if not k.startswith("_"))


def build_doc(replays: dict[str, dict], *, reason: str,
              commit: str = "unknown") -> dict:
    """Assemble a snapshot document from ``{fixture: replay_result}``."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc: dict[str, Any] = {
        "_meta": {
            "generated_at": now,
            "commit": commit,
            "reason": reason,
            "tolerance": TOLERANCE,
            "tickers": len(replays),
            "note": "Written by --update-snapshots. Do not edit by hand.",
        },
    }
    for name in sorted(replays):
        r = replays[name]
        doc[name] = {
            "ticker": r.get("ticker"),
            "base_iv": r.get("base_iv"),
            "recorded_commit": (r.get("meta") or {}).get("commit"),
            "captured_at": (r.get("meta") or {}).get("captured_at"),
            "state_source": (r.get("meta") or {}).get("state_source"),
            "projection": r.get("projection") or {},
        }
    return doc


def save(doc: dict, *, reason: str) -> Path:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, default=str, sort_keys=False)
        fh.write("\n")
    append_changelog(doc, reason=reason)
    return SNAPSHOT_PATH


def append_changelog(doc: dict, *, reason: str) -> None:
    """One entry per snapshot update — the audit trail the plan asks for."""
    meta = doc.get("_meta", {})
    CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new = not CHANGELOG_PATH.exists()
    with open(CHANGELOG_PATH, "a", encoding="utf-8") as fh:
        if new:
            fh.write("# Golden valuation snapshots\n\n"
                     "Every entry is one `--update-snapshots` run. An entry\n"
                     "exists because a pinned end-to-end valuation moved, and\n"
                     "says why.\n\n")
        fh.write(f"## {meta.get('generated_at', '?')}\n\n"
                 f"- commit: `{meta.get('commit', '?')}`\n"
                 f"- tickers: {meta.get('tickers', '?')}\n"
                 f"- tolerance: ±{TOLERANCE:.0%} on numeric leaves\n"
                 f"- reason: {reason}\n\n")
