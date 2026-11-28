"""D8 (owner, 2026-11-28): names outside the tuned coverage are analysed, labelled 'limited' and cost 1 credit."""
from src.data import coverage as cv


def test_tuned_vs_limited():
    for t in ("AAPL", "NVDA", "D05.SI", "9988.HK", "00700.HK"):
        assert cv.is_tuned(t), t
    for t in ("APP", "SHOP", "RDDT", ""):
        assert not cv.is_tuned(t), t
    assert cv.coverage("APP") == {"level": "limited", "note": cv.LIMITED_NOTE}
    assert cv.coverage("AAPL") == {"level": "tuned", "note": None}


def test_limited_first_analysis_costs_one_credit():
    from app.backend.services import billing as b
    assert b.run_type("APP", "qwen3.8-flash", covered=False) == "limited"
    assert b.run_credits("APP", "qwen3.8-flash", covered=False) == 1
    assert b.run_type("AAPL", "qwen3.8-flash", covered=False) == "new"
    assert b.run_credits("AAPL", "qwen3.8-flash", covered=False) == 2
    assert b.run_type("APP", "qwen3.8-flash", covered=True) == "covered"
