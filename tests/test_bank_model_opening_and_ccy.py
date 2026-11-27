"""HSBC, 2026-11-27: the bank model opened on a total income the statements table withholds,
and its forecast columns were in the listing currency (HKD) beside USD reported years."""
import inspect

from src.tools.financial_statements import attach_forecast, _capital_model_fx


def _fs(eq=177.8e9, ni=22.8e9):
    return {"periods": ["FY2024", "FY2025"], "statements": {
        "income": {"rows": [{"key": "net_income", "values": {"FY2024": 24.0e9, "FY2025": ni}}]},
        "balance": {"rows": [{"key": "shareholders_equity", "values": {"FY2024": 185e9, "FY2025": eq}}]},
        "cashflow": {"rows": []}}}


def _model(fx, tagged=True):
    m = {"kind": "bank", "fy_labels": ["FY+1E"], "rows": {"net_income": [24.8e9 * fx], "equity": [190e9 * fx], "eps": [1.44 * fx]},
         "opening": {"equity": 177.8e9 * fx, "net_income": 22.8e9 * fx}}
    if tagged:
        m.update(currency="valuation", fx_to_valuation=fx)
    return m


def _ni(out):
    return out["statements"]["income"]["rows"][0]["values"]["FY+1E"]


def test_tagged_model_restated_to_statement_currency():
    assert _ni(attach_forecast(_fs(), _model(7.83))) == __import__("pytest").approx(24.8e9)


def test_untagged_stored_run_inferred_from_opening():
    assert _capital_model_fx(_model(7.83, tagged=False), _fs()) == __import__("pytest").approx(7.83)
    assert _ni(attach_forecast(_fs(), _model(7.83, tagged=False))) == __import__("pytest").approx(24.8e9)


def test_same_currency_and_definitional_gaps_untouched():
    assert _capital_model_fx(_model(1.0, tagged=False), _fs()) is None
    # equity differs (minorities) but net income does not: not a conversion
    m = _model(1.0, tagged=False); m["opening"]["equity"] = 200e9
    assert _capital_model_fx(m, _fs()) is None


def test_bank_opening_uses_series_rule_and_discloses():
    from src.agents.analysis import dcf_agent
    src = inspect.getsource(dcf_agent.run_dcf_agent)
    assert "bank_total_income_series" in src
    assert "Bank earnings-and-capital model not built: latest-year total income is withheld" in src
    assert '_bm_out["fx_to_valuation"] = fx_rate' in src
