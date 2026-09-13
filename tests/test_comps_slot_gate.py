"""The weekly refresh must be gated on the SLOT, not on age.

The gate used to accept any refresh younger than six days. A run that
completed late therefore suppressed the NEXT scheduled slot: production's
week of 2026-09-01 finished on Tuesday the 8th (the recheck loop retrying
after a failed Saturday), so when Saturday the 12th arrived the data was 3.7
days old, the gate reported "already ran this week", and the refresh was
skipped entirely. Cadence halves to fortnightly and the comps peak around 11
days against a 14-day usability limit -- on a mechanism whose failure is
silent.
"""
import datetime as dt

import src.data.regional_comps as rc


def _age_of(when: dt.datetime) -> float:
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 86400


class TestSlotBoundary:
    def test_slot_start_is_the_most_recent_fire_time(self):
        start = rc.current_slot_start()
        now = dt.datetime.now(dt.timezone.utc)
        assert start <= now
        assert start.weekday() == rc._FIRE_WEEKDAY
        assert start.hour == rc._FIRE_HOUR_UTC
        assert (now - start) < dt.timedelta(days=7)

    def test_slot_and_next_fire_are_one_week_apart(self):
        start = rc.current_slot_start()
        nxt = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=rc.seconds_until_next_fire())
        assert abs((nxt - start) - dt.timedelta(days=7)) < dt.timedelta(seconds=2)


class TestGateIsSlotBased:
    def test_a_late_previous_run_does_not_suppress_this_slot(self, monkeypatch):
        """The exact production failure: work finished 3.7 days ago, but in
        the PREVIOUS slot."""
        before_slot = rc.current_slot_start() - dt.timedelta(hours=3)
        monkeypatch.setattr(rc, "latest_refresh_age_days",
                            lambda ex: _age_of(before_slot))
        assert rc.already_ran_this_week() is False

    def test_work_inside_this_slot_satisfies_the_gate(self, monkeypatch):
        inside = rc.current_slot_start() + dt.timedelta(minutes=30)
        if inside > dt.datetime.now(dt.timezone.utc):
            inside = dt.datetime.now(dt.timezone.utc)
        monkeypatch.setattr(rc, "latest_refresh_age_days",
                            lambda ex: _age_of(inside))
        assert rc.already_ran_this_week() is True

    def test_a_market_that_never_ran_is_not_done(self, monkeypatch):
        monkeypatch.setattr(rc, "latest_refresh_age_days", lambda ex: None)
        assert rc.already_ran_this_week() is False

    def test_partial_completion_is_not_done(self, monkeypatch):
        """A run that died after HKSE but before US must be retried, not
        recorded as a successful week."""
        inside = _age_of(min(rc.current_slot_start() + dt.timedelta(minutes=30),
                             dt.datetime.now(dt.timezone.utc)))
        stale = _age_of(rc.current_slot_start() - dt.timedelta(days=2))
        monkeypatch.setattr(rc, "latest_refresh_age_days",
                            lambda ex: inside if ex == "HKSE" else stale)
        assert rc.already_ran_this_week() is False

    def test_every_market_is_checked(self):
        """US, JPX, KSC, SHH and SHZ are covered too -- the gate must not be
        satisfied by Hong Kong and Singapore alone."""
        assert set(rc.EXCHANGES) >= {"HKSE", "SES", "US", "JPX", "KSC",
                                     "SHH", "SHZ"}
