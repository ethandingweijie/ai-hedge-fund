"""The watchlist must not open a database FILE.

Reported twice from mobile as HTTP 500 {"detail":"unable to open database
file"}. watchlist_service was the last module still using raw sqlite3 against
a path -- RUN_ARCHIVE_PATH, else src/data/run_archive.db. That works on a
laptop and fails on a multi-replica container whose volume is not mounted.
screener_service.py carries the same note for the same incident on 2026-08-16.

Production already had a `watchlist` table in Postgres holding four rows; the
service had been reading a file that does not exist there, so the data was
present and unreachable the whole time.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app" / "backend" / "services" / "watchlist_service.py"


def _code() -> str:
    """The module's CODE, with comments and docstrings removed.

    Asserting on raw source keeps catching the explanations rather than the
    behaviour: this file documents why sqlite3.connect, RUN_ARCHIVE_PATH,
    INSERT OR IGNORE and AUTOINCREMENT are forbidden, and a naive search finds
    every one of those words in the prose that forbids them.

    untokenize (rather than joining tokens) so the result is real source --
    `_db.query` stays `_db.query` instead of becoming `_db . query`.
    """
    import io as _io
    import tokenize
    kept = []
    prev_type = tokenize.INDENT
    with _io.open(SERVICE, encoding="utf-8") as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev_type in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.NL,
                    tokenize.DEDENT):
                continue          # a bare string statement is a docstring
            kept.append((tok.type, tok.string))
            if tok.type != tokenize.NL:
                prev_type = tok.type
    return tokenize.untokenize(kept)


class TestNoRawSqlite:
    def test_it_never_opens_a_file(self):
        src = _code()
        assert "sqlite3.connect" not in src
        assert "RUN_ARCHIVE_PATH" not in src

    def test_every_query_goes_through_the_dual_mode_layer(self):
        # The import is asserted on raw source: tokenising splits the dotted
        # path. The proximity check runs on whitespace-stripped code, because
        # untokenize re-emits `_db .query` and a spacing quirk is not the
        # thing under test.
        assert "from src.data import db as _db" in SERVICE.read_text(
            encoding="utf-8")
        flat = "".join(_code().split())
        for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            for m in re.finditer(rf'"{verb}', flat):
                window = flat[max(0, m.start() - 300):m.start()]
                assert "_db." in window, (
                    f"{verb} not issued through the dual-mode layer: "
                    f"...{window[-70:]}")


class TestPortableSql:
    def test_insert_uses_on_conflict_not_the_sqlite_spelling(self):
        """INSERT OR IGNORE is SQLite-only and is a syntax error on Postgres."""
        src = _code()
        assert "INSERT OR IGNORE" not in src
        assert "ON CONFLICT DO NOTHING" in src

    def test_the_ddl_does_not_use_autoincrement(self):
        """AUTOINCREMENT is SQLite-only. Production's id is a Postgres
        IDENTITY column, supplied automatically when the insert omits it --
        which is why the insert must keep omitting it."""
        src = _code()
        assert "AUTOINCREMENT" not in src
        insert = src[src.index("INSERT INTO watchlist"):][:400]
        assert "id," not in insert, "the insert must not supply the identity column"

    def test_the_unique_index_is_created_if_missing(self):
        """Production had only watchlist_pkey; ON CONFLICT needs the
        (user_id, ticker) index to actually dedupe."""
        src = _code()
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlist_user_ticker" in src


class TestBehaviour:
    def test_crud_round_trips_on_sqlite(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "wl.db"))
        from app.backend.services import watchlist_service as ws
        monkeypatch.setattr(ws, "_fetch_profile", lambda t: {"companyName": "Test Co"})
        monkeypatch.setattr(ws, "_fetch_vgpm_and_price",
                            lambda t: {"vgpm": None, "price": 1.0, "source": "fast"})
        monkeypatch.setattr(ws, "_batch_fetch_prices", lambda t: {})
        monkeypatch.setattr(ws, "_get_pipeline_vgpm", lambda t: {})

        ws.add_ticker("TEST", user_id=7)
        assert ws.is_in_watchlist("TEST", user_id=7) is True
        ws.add_ticker("TEST", user_id=7)                    # idempotent
        assert len([r for r in ws.get_watchlist(user_id=7)
                    if r["ticker"] == "TEST"]) == 1
        assert ws.remove_ticker("TEST", user_id=7) is True
        assert ws.remove_ticker("TEST", user_id=7) is False

    def test_a_users_rows_are_scoped(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "wl2.db"))
        from app.backend.services import watchlist_service as ws
        monkeypatch.setattr(ws, "_fetch_profile", lambda t: {"companyName": "X"})
        monkeypatch.setattr(ws, "_fetch_vgpm_and_price",
                            lambda t: {"vgpm": None, "price": 1.0, "source": "fast"})
        monkeypatch.setattr(ws, "_batch_fetch_prices", lambda t: {})
        monkeypatch.setattr(ws, "_get_pipeline_vgpm", lambda t: {})

        ws.add_ticker("AAA", user_id=1)
        ws.add_ticker("BBB", user_id=2)
        assert [r["ticker"] for r in ws.get_watchlist(user_id=1)] == ["AAA"]
        assert [r["ticker"] for r in ws.get_watchlist(user_id=2)] == ["BBB"]
