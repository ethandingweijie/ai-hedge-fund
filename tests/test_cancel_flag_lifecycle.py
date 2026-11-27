"""Cancel flags are per run id (2026-10-07 REGN purge; 2026-11-27 chaos audit).

A ticker-level flag first outlived its run (every later run of the ticker
stopped at its first checkpoint); the fix -- clearing the ticker flag when a
new run starts -- then let a resubmitted run B wipe run A's flag so both
completed. Keying by run id removes both failure modes.
"""
import inspect

from app.backend.services import analysis_service as a


def test_cancel_is_per_run_id():
    a.request_cancel(run_id="run-A")
    try:
        assert a.is_cancelled("run-A")
        assert not a.is_cancelled("run-B")      # a resubmitted run is unaffected
    finally:
        a.clear_cancel("run-A")
    assert not a.is_cancelled("run-A")


def test_a_new_run_does_not_clear_another_runs_cancel():
    src = inspect.getsource(a.run_analysis_pipeline)
    assert "clear_cancel(ticker" not in src
    assert "is_cancelled(run_id)" in src
    # the run's own flag is dropped once its thread has stopped
    assert "clear_cancel(run_id)" in src


def test_request_cancel_no_longer_accepts_a_ticker():
    assert "ticker" not in inspect.signature(a.request_cancel).parameters
    assert "ticker" not in inspect.signature(a.is_cancelled).parameters
