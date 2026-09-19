"""One KPI vector per company, across its listings.

2026-09-16: Alibaba's GAAP operating margin was extracted as +10.0% for
09988.HK and -0.3% for BABA seven minutes apart. That one field moved the
composite from 1.266 to 1.0363 and opened an 18.7% gap between the two
listings' blended IVs ($209.03 vs $176.05 per ADS) on a price that agreed to
0.6%. The venue is not a property of the income statement.
"""
import inspect
import json

import pytest

import src.data.db as _db
from src.data import company_metrics as cm

BABA_HK = {"operating_margin_pct": 0.10, "revenue_growth_pct": 0.09,
           "cloud_revenue_growth_pct": 0.45, "fcf_margin_pct": -0.05,
           "_completeness_score": 1.0, "_z_scores": {"x": {"z": 3.2}}}
BABA_US = {"operating_margin_pct": -0.003, "revenue_growth_pct": 0.09,
           "cloud_revenue_growth_pct": 0.45, "fcf_margin_pct": -0.05}


@pytest.fixture
def archive(monkeypatch):
    """Stand in for web_runs; rows are (ticker, run_at, payload)."""
    rows: list = []

    def _query(sql, params=None):
        params = list(params or [])
        cutoff = params[-1]
        wanted = {str(p).upper() for p in params[:-1]}
        hits = [r for r in rows
                if r[0].upper() in wanted and str(r[1]) >= str(cutoff)]
        hits.sort(key=lambda r: str(r[1]), reverse=True)
        return [{"ticker": t, "run_at": a, "full_result_json": j}
                for t, a, j in hits[:5]]

    monkeypatch.setattr(_db, "is_postgres", lambda: True)
    monkeypatch.setattr(_db, "query", _query)
    return rows


def _payload(ticker, metrics):
    return json.dumps({"data": {"framework_metrics_all": {ticker: metrics}}})


def _recent(days_ago=1):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestBothListingsScoreTheSameCompany:
    def test_the_hk_line_adopts_the_adr_vector(self, archive):
        archive.append(("BABA", _recent(), _payload("BABA", BABA_US)))
        out, flag = cm.resolve_company_metrics("09988.HK", dict(BABA_HK))
        assert out["operating_margin_pct"] == pytest.approx(-0.003)
        assert out["_metrics_company"] == "BABA"
        assert "BABA" in out["_metrics_source"]
        assert "operating_margin_pct" in flag

    def test_the_adr_adopts_the_hk_vector(self, archive):
        archive.append(("09988.HK", _recent(), _payload("09988.HK", BABA_HK)))
        out, _ = cm.resolve_company_metrics("BABA", dict(BABA_US))
        assert out["operating_margin_pct"] == pytest.approx(0.10)

    def test_the_two_listings_end_up_identical(self, archive):
        archive.append(("BABA", _recent(), _payload("BABA", BABA_US)))
        hk, _ = cm.resolve_company_metrics("09988.HK", dict(BABA_HK))
        us, _ = cm.resolve_company_metrics("BABA", dict(BABA_US))
        shared = lambda d: {k: v for k, v in d.items() if not k.startswith("_")}
        assert shared(hk) == shared(us)

    def test_per_run_private_keys_are_not_inherited(self, archive):
        """_z_scores and completeness belong to the run that computed them."""
        archive.append(("09988.HK", _recent(), _payload("09988.HK", BABA_HK)))
        out, _ = cm.resolve_company_metrics("BABA", dict(BABA_US))
        assert "_z_scores" not in out and "_completeness_score" not in out

    def test_no_flag_when_the_vectors_already_agree(self, archive):
        archive.append(("BABA", _recent(), _payload("BABA", BABA_US)))
        out, flag = cm.resolve_company_metrics("09988.HK", dict(BABA_US))
        assert flag is None and out["operating_margin_pct"] == pytest.approx(-0.003)


class TestScopeIsNarrow:
    def test_a_single_listed_company_keeps_its_own_extraction(self, archive):
        archive.append(("MSFT", _recent(), _payload("MSFT", {"operating_margin_pct": 0.9})))
        out, flag = cm.resolve_company_metrics("MSFT", {"operating_margin_pct": 0.45})
        assert out == {"operating_margin_pct": 0.45} and flag is None

    def test_a_stale_sibling_run_is_ignored(self, archive):
        archive.append(("BABA", _recent(days_ago=60), _payload("BABA", BABA_US)))
        out, flag = cm.resolve_company_metrics("09988.HK", dict(BABA_HK))
        assert out["operating_margin_pct"] == pytest.approx(0.10) and flag is None

    def test_an_empty_archive_changes_nothing(self, archive):
        out, flag = cm.resolve_company_metrics("09988.HK", dict(BABA_HK))
        assert out["operating_margin_pct"] == pytest.approx(0.10) and flag is None

    def test_a_sibling_run_with_no_metrics_is_skipped(self, archive):
        archive.append(("BABA", _recent(), json.dumps({"data": {}})))
        out, flag = cm.resolve_company_metrics("09988.HK", dict(BABA_HK))
        assert out["operating_margin_pct"] == pytest.approx(0.10) and flag is None

    def test_a_database_failure_is_not_fatal(self, monkeypatch):
        monkeypatch.setattr(_db, "is_postgres", lambda: True)
        def boom(*a, **k):
            raise RuntimeError("db down")
        monkeypatch.setattr(_db, "query", boom)
        out, flag = cm.resolve_company_metrics("09988.HK", dict(BABA_HK))
        assert out["operating_margin_pct"] == pytest.approx(0.10) and flag is None

    def test_every_dual_listing_is_covered(self):
        from src.data.dual_listings import DUAL_LISTINGS
        for hk, (adr, _ratio) in DUAL_LISTINGS.items():
            assert cm._siblings(hk) == [adr]
            assert cm._siblings(adr) == [hk]


def test_the_pipeline_resolves_metrics_before_it_scores_them():
    import src.pipeline as pipeline
    src = inspect.getsource(pipeline)
    assert src.index('_timed("4_44_company_metrics")') < \
        src.index('_timed("4_5_dcf_engine")')
    assert "zscore" not in src
