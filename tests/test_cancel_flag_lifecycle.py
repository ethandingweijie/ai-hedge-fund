"""A ticker-level cancel must not outlive the run it was aimed at (2026-10-07, REGN purge)."""
import inspect

from app.backend.services import analysis_service as a


def test_a_new_run_clears_a_stale_ticker_cancel():
    a.request_cancel(ticker="ZZTEST")
    assert a.is_cancelled(ticker="ZZTEST")
    src = inspect.getsource(a.run_analysis_pipeline)
    i_run = src.index("run_id = run_id or str(uuid.uuid4())")
    i_clear = src.index("clear_cancel(ticker=ticker)")
    i_ckpt = src.index("def _on_checkpoint")
    assert i_run < i_clear < i_ckpt                      # cleared at start, before any checkpoint can raise
    a.clear_cancel(ticker="zztest")
    assert not a.is_cancelled(ticker="ZZTEST")
