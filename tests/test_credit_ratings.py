"""Agency credit ratings as the default cost-of-debt basis for US tickers (owner, 2026-10-08)."""
import json

import pytest

from src.data import credit_ratings as cr

_TENK = ("As of December 31, 2025, S&P, Moody’s and Fitch assigned credit ratings to our outstanding senior notes "
         "of BBB+, Baa1 and BBB+, respectively, which are considered investment grade.")


def _searcher(first: dict, confirm: dict | None = None):
    calls = []

    def s(prompt):
        calls.append(prompt)
        if prompt.startswith("Find the CURRENT"):
            return "Here you go: " + json.dumps(first)
        return json.dumps(confirm or {})
    s.calls = calls
    return s


def _rows(**kw):
    return {"ratings": [{"agency": a, "rating": r, "outlook": "Stable", "action_date": "2026-02-10", "source_url": None}
                        for a, r in kw.items()]}


def test_the_scale_and_the_composite():
    assert cr.notch("S&P", "BBB+") == 8 and cr.notch("Moody's", "baa1") == 8 and cr.notch("Fitch", "A–") == 7
    assert cr.normalize("Moody's", "BAA2") == "Baa2" and cr.normalize("S&P", "Baa2") is None
    assert cr.composite({"S&P": "A-", "Moody's": "Baa1", "Fitch": "A"}) == "A-"          # middle of three
    assert cr.composite({"S&P": "A-", "Moody's": "Baa1"}) == "BBB+"                       # lower of two


def test_two_agencies_confirmed_by_the_10k_price_automatically():
    s = _searcher({"ratings": [{"agency": "S&P", "rating": "BBB+"}, {"agency": "Moody's", "rating": "Baa1"},
                               {"agency": "Fitch", "rating": "BBB+"}]})
    rec = cr.research("AMGN", "Amgen", search=s, filings=lambda t: [{"form": "10-K", "text": _TENK}])
    assert rec["status"] == "VERIFIED" and rec["rating"] == "BBB+"
    assert all(f["confirmed_by"][0]["kind"] == "10-K" for f in rec["agencies"].values())
    assert len(s.calls) == 1                                       # the filing confirmed all three: no second search


def test_the_second_search_confirms_what_the_filings_do_not():
    s = _searcher({"ratings": [{"agency": "S&P", "rating": "BBB"}, {"agency": "Moody's", "rating": "Baa2"}]},
                  {"S&P": "BBB", "Moody's": "Baa3"})
    rec = cr.research("KMI", search=s, filings=lambda t: [])
    assert rec["agencies"]["S&P"]["confirmed_by"] == [{"kind": "second search"}]
    assert rec["agencies"]["Moody's"]["confirmed_by"] == []      # the second search disagreed
    assert rec["status"] == "PROPOSED" and "1 of 2" in rec["basis"]


def test_a_split_rating_or_a_single_agency_waits_for_the_owner():
    split = cr.research("X", search=_searcher({"ratings": [{"agency": "S&P", "rating": "BBB"}, {"agency": "Moody's", "rating": "Ba2"}]},
                                              {"S&P": "BBB", "Moody's": "Ba2"}), filings=lambda t: [])
    assert split["status"] == "PROPOSED" and "more than one notch" in split["basis"] and split["rating"] == "BB"
    one = cr.research("Y", search=_searcher({"ratings": [{"agency": "S&P", "rating": "BB+"}]}, {"S&P": "BB+"}), filings=lambda t: [])
    assert one["status"] == "PROPOSED" and "one agency" in one["basis"]


def test_unrated_and_failed():
    assert cr.research("NTLA", search=_searcher({"ratings": [{"agency": "S&P", "rating": None}]}), filings=lambda t: [])["status"] == "UNRATED"
    assert cr.research("Z", search=lambda p: "no idea", filings=lambda t: [])["status"] == "FAILED"


def test_a_filing_mention_must_sit_beside_the_agency():
    assert cr.filing_states(_TENK, "Moody's", "Baa1")
    assert not cr.filing_states("Fitch rates the bank counterparties. ... " + "x" * 400 + " BBB+", "Fitch", "BBB+")


def test_storage_precedence_and_the_owners_decision(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from src.data import db
    monkeypatch.setattr(db, "get_db_path", lambda: str(tmp_path / "t.db"), raising=False)
    monkeypatch.setattr(cr, "_ready_key", None)
    monkeypatch.setattr(cr, "_owner_registry", lambda t: None)
    cr.reset_cache()
    rec = cr.research("AMGN", search=_searcher({"ratings": [{"agency": "S&P", "rating": "BBB+"}, {"agency": "Moody's", "rating": "Baa1"}]}),
                      filings=lambda t: [{"form": "10-K", "text": _TENK}])
    cr.save(rec)
    lk = cr.lookup("AMGN", wait_s=0)
    assert lk["rating"] == "BBB+" and lk["status"] == "VERIFIED" and "Cost of debt on the agency rating BBB+" in cr.describe(lk)
    cr.set_status("AMGN", "REVOKED", "owner")
    cr.reset_cache()
    assert cr.lookup("AMGN", wait_s=0)["rating"] is None
    cr.set_status("AMGN", "ACCEPTED", "owner", rating="A-")
    cr.reset_cache()
    assert cr.lookup("AMGN", wait_s=0)["rating"] == "A-"
    # the owner registry wins over research
    monkeypatch.setattr(cr, "_owner_registry", lambda t: {"status": "ACCEPTED", "rating": "AA"})
    cr.reset_cache()
    assert cr.lookup("AMGN", wait_s=0)["rating"] == "AA"
    cr.reset_cache()


def test_no_research_under_pytest_and_only_for_us_tickers(monkeypatch):
    assert not cr.research_enabled()
    assert cr.is_us_ticker("AMGN") and cr.is_us_ticker("BRK-B") and not cr.is_us_ticker("0700.HK") and not cr.is_us_ticker("D05.SI")
    started = []
    monkeypatch.setattr(cr.threading, "Thread", lambda *a, **k: started.append(1))
    cr.prefetch("AMGN")
    assert started == []
