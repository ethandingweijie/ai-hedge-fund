"""Aged-out comps must not fail silently.

Every field simply goes missing, the caller keeps its static table, and an HK
stock is quietly valued on US multiples -- the regression regional_comps.py
was written to prevent, reappearing through the back door. It happened on
2026-09-13: the weekly refresh last ran 17.3 days earlier against a 14-day
limit, so Chinese autos took EV/Revenue at the static 2.5x instead of the
HKSE industry median of 0.68x and Geely blended to +112% over consensus.
"""
import logging

import src.data.regional_comps as rc


class TestStalenessIsLoud:
    def test_warns_when_everything_aged_out(self, monkeypatch, caplog):
        monkeypatch.setattr(rc, "load_comps",
                            lambda *a, **k: {})
        monkeypatch.setattr(rc, "latest_refresh_age_days", lambda ex: 17.3)
        with caplog.at_level(logging.WARNING, logger=rc.logger.name):
            out = rc.get_regional_multiples("HKSE", industry="Auto - Manufacturers",
                                            sector="Consumer")
        assert out == {}
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "aged out" in msg and "17.3" in msg
        assert "STATIC" in msg

    def test_silent_when_nothing_was_ever_stored(self, monkeypatch, caplog):
        """A market with no comps at all is a different condition -- it is not
        a refresh that stopped, and warning on it would train people to ignore
        the warning that matters."""
        monkeypatch.setattr(rc, "load_comps", lambda *a, **k: {})
        monkeypatch.setattr(rc, "latest_refresh_age_days", lambda ex: None)
        with caplog.at_level(logging.WARNING, logger=rc.logger.name):
            rc.get_regional_multiples("XETRA", industry="Auto", sector="Consumer")
        assert not [r for r in caplog.records if "aged out" in r.getMessage()]

    def test_silent_when_fresh_rows_resolve(self, monkeypatch, caplog):
        monkeypatch.setattr(rc, "load_comps", lambda ex, lvl, key, coh, age: (
            {"ev_ebitda": {"value": 10.36, "peer_count": 99,
                           "min_market_cap": 0.0}} if lvl == "industry" else {}))
        monkeypatch.setattr(rc, "latest_refresh_age_days", lambda ex: 0.2)
        with caplog.at_level(logging.WARNING, logger=rc.logger.name):
            out = rc.get_regional_multiples("HKSE", industry="Auto - Manufacturers",
                                            sector="Consumer")
        assert out["ev_ebitda"]["value"] == 10.36
        assert not [r for r in caplog.records if "aged out" in r.getMessage()]

    def test_a_weekly_job_has_only_one_week_of_slack(self):
        """Documents WHY this is fragile: the limit is 14 days and the refresh
        is weekly, so a single missed run silently degrades a whole market."""
        assert rc.MAX_AGE_DAYS == 14
