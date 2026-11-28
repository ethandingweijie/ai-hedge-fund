"""PDPA self-service deletion (owner, 2026-11-28): erase personal data, detach shared records, keep billing."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backend.database.connection import Base
from app.backend.database import models as m
from app.backend.services import account_deletion as ad


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUN_ARCHIVE_PATH", str(tmp_path / "a.db"))
    from src.data import db
    from app.backend.services import billing
    monkeypatch.setattr(db, "get_db_path", lambda: str(tmp_path / "a.db"))
    db.close_all_connections()
    monkeypatch.setitem(billing._READY, "v", False)
    for ddl in ("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, user_id INTEGER, ticker TEXT)",
                "CREATE TABLE web_runs (run_id TEXT PRIMARY KEY, user_id INTEGER, ticker TEXT)",
                "CREATE TABLE market_asks (ask_id TEXT PRIMARY KEY, user_id INTEGER, run_ticker TEXT)"):
        db.execute(ddl)
    db.execute("INSERT INTO watchlist (user_id, ticker) VALUES (7, 'AAPL'), (8, 'MSFT')")
    db.execute("INSERT INTO web_runs VALUES ('r7', 7, 'AAPL'), ('r8', 8, 'MSFT')")
    db.execute("INSERT INTO market_asks VALUES ('a7', 7, 'NVDA')")
    billing.ensure_tables()
    db.execute("INSERT INTO usage_ledger (user_id, created_at, kind, credits, ticker, source, ref) "
               "VALUES (7, '2026-11-28T00:00:00', 'run', 1, 'AAPL', 'plan', 'x')")
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for uid, email in ((7, "a@x.test"), (8, "b@x.test")):
        s.add(m.User(id=uid, email=email, provider="google", provider_sub=str(uid)))
    s.flush()
    s.add(m.UserHolding(user_id=7, ticker="AAPL", quantity=1, avg_cost=1))
    mine = m.ChatMessage(ticker="AAPL", user_id=7, content="mine")
    s.add(mine); s.flush()
    reply = m.ChatMessage(ticker="AAPL", user_id=8, content="reply", parent_message_id=mine.id)
    s.add(reply); s.flush()
    s.add(m.ChatReaction(message_id=mine.id, user_id=8))
    s.commit()
    yield db, s, billing
    db.close_all_connections()


def test_erases_detaches_and_keeps(env):
    db, s, _ = env
    out = ad.delete_account(SimpleNamespace(id=7, role="member"), s)
    assert out["deleted"]
    assert db.query("SELECT ticker FROM watchlist") and [r[0] for r in db.query("SELECT user_id FROM watchlist")] == [8]
    assert [r[0] for r in db.query("SELECT run_id FROM web_runs")] == ["r8"]
    assert db.query_one("SELECT user_id FROM market_asks WHERE ask_id='a7'")[0] is None          # detached
    assert db.query_one("SELECT COUNT(*) FROM usage_ledger WHERE user_id = 7")[0] == 1            # billing kept
    assert s.query(m.User).filter(m.User.id == 7).count() == 0
    assert s.query(m.User).filter(m.User.id == 8).count() == 1
    assert s.query(m.UserHolding).count() == 0
    left = s.query(m.ChatMessage).all()
    assert [(c.content, c.parent_message_id) for c in left] == [("reply", None)]                  # reply kept, detached
    assert s.query(m.ChatReaction).count() == 0


def test_refused_for_admin_and_active_subscription(env):
    db, s, billing = env
    with pytest.raises(ad.DeletionRefused) as e:
        ad.delete_account(SimpleNamespace(id=7, role="admin"), s)
    assert e.value.status == 403
    db.execute("INSERT INTO billing_accounts (user_id, plan, status, stripe_subscription_id, updated_at) "
               "VALUES (7, 'starter', 'active', 'sub_1', '2026-11-28')")
    with pytest.raises(ad.DeletionRefused) as e:
        ad.delete_account(SimpleNamespace(id=7, role="member"), s)
    assert e.value.status == 409
    assert s.query(m.User).filter(m.User.id == 7).count() == 1
