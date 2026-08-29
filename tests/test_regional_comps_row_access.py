"""Comps rows must be read by column name, not by position.

`src.data.db.query` returns plain **dicts** on Postgres and `sqlite3.Row` on
SQLite. `sqlite3.Row` supports BOTH `row[0]` and `row["field"]`; a dict
supports only the latter, and `row[0]` on one raises `KeyError: 0` rather
than `IndexError`.

`load_comps` indexed positionally with a comment asserting that worked for
both. It did not. In production every lookup raised, the exception escaped
`load_comps`, and the caller's broad `except Exception: return {}` turned it
into "no comps available" — so the entire regional_comps table (2,503 rows
across US/HKSE/SES, refreshed weekly) was never read and every valuation
silently fell back to the static multiples. Locally on SQLite it worked
perfectly, which is why it survived.

Measured immediately after the fix, against production data:

    US   10/10 tickers resolved, 6.0/6 fields, 9 at industry level
    HK    5/5              "     6.0/6         4 at industry level
    SG    7/8              "     5.2/6         2 at industry level

Before it: 0 of 23.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.data import regional_comps as rc

COLUMNS = ("field", "value", "peer_count", "min_market_cap", "computed_at")
_FRESH = "2999-01-01T00:00:00+00:00"   # never stale


class FakeSqliteRow:
    """Mimics sqlite3.Row: indexable by position AND by column name."""

    def __init__(self, values):
        self._v = dict(zip(COLUMNS, values))

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._v[COLUMNS[k]]
        return self._v[k]

    def keys(self):
        return list(COLUMNS)


def _pg_rows():
    """What psycopg hands back — a plain dict, keyed by column name."""
    return [dict(zip(COLUMNS, ("pe", 12.4, 14, 1.0e9, _FRESH)))]


def _sqlite_rows():
    return [FakeSqliteRow(("pe", 12.4, 14, 1.0e9, _FRESH))]


def test_a_dict_row_rejects_positional_access():
    """The precondition the old code got wrong — stated explicitly so the
    reason for name-based access cannot be optimised away later."""
    row = _pg_rows()[0]
    with pytest.raises(KeyError):
        _ = row[0]
    assert row["field"] == "pe"


@pytest.mark.parametrize("rows_fn, label",
                         [(_pg_rows, "postgres-dict"),
                          (_sqlite_rows, "sqlite-row")])
def test_load_comps_reads_both_row_shapes(rows_fn, label):
    with patch.object(rc, "_ensure_table", return_value=None), \
         patch.object(rc._db, "query", return_value=rows_fn()):
        out = rc.load_comps("US", "industry", "Semiconductors", "all")
    assert "pe" in out, f"{label}: no field parsed"
    assert out["pe"]["value"] == pytest.approx(12.4)
    assert out["pe"]["peer_count"] == 14


def test_postgres_lookup_is_not_silently_empty():
    """The production symptom: a populated table reading as no comps at all."""
    with patch.object(rc, "_ensure_table", return_value=None), \
         patch.object(rc._db, "query", return_value=_pg_rows()):
        out = rc.load_comps("US", "industry", "Semiconductors", "all")
    assert out != {}, (
        "a populated comps table returned nothing — the caller treats this as "
        "'no comps available' and falls back to static multiples"
    )


def test_stale_rows_are_still_dropped():
    """The age filter must survive the row-access change."""
    stale = [dict(zip(COLUMNS,
                      ("pe", 12.4, 14, 1.0e9, "2000-01-01T00:00:00+00:00")))]
    with patch.object(rc, "_ensure_table", return_value=None), \
         patch.object(rc._db, "query", return_value=stale):
        out = rc.load_comps("US", "industry", "Semiconductors", "all",
                            max_age_days=14)
    assert out == {}


def test_latest_refresh_age_handles_a_dict_row():
    """Same trap: an unaliased MAX() comes back under a driver-chosen key."""
    with patch.object(rc, "_ensure_table", return_value=None), \
         patch.object(rc._db, "query_one", return_value={"newest": _FRESH}):
        age = rc.latest_refresh_age_days("US")
    assert age is not None
