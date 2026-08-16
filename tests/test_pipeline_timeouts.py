"""
tests/test_pipeline_timeouts.py
===============================
R1 reliability batch — bounded parallel-block joins (src/pipeline.py).

Before: the front block and phase-7 joins blocked without a timeout, so a
hung dependency (FMP/yfinance/Tavily never returning) hung the whole run
forever while the SSE stream heartbeated indefinitely. Now _bounded_join
enforces ONE wall-clock deadline per block and fails fast with a
RuntimeError naming the block.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import src.pipeline as P


def test_bounded_join_returns_results_in_order():
    ex = ThreadPoolExecutor(max_workers=2)
    futures = [ex.submit(lambda: "a"), ex.submit(lambda: "b")]
    assert P._bounded_join(ex, futures, 30.0, "test block") == ["a", "b"]


def test_bounded_join_hang_raises_runtime_error():
    """The stub-hang case: a never-returning dependency must fail the run
    at the deadline, not block forever."""
    def _hang():
        time.sleep(2)  # outlives the deadline; ends soon so no suite-exit lag

    ex = ThreadPoolExecutor(max_workers=2)
    futures = [ex.submit(lambda: "fast"), ex.submit(_hang)]

    t0 = time.perf_counter()
    with pytest.raises(RuntimeError, match=r"test block timed out after \d+s"):
        P._bounded_join(ex, futures, 0.5, "test block")
    elapsed = time.perf_counter() - t0
    assert elapsed < 5  # actually bounded — no infinite block


def test_bounded_join_propagates_phase_exception_unchanged():
    """Fail semantics identical to the sequential pipeline: a phase error
    re-raises as-is (not converted into a timeout)."""
    def _boom():
        raise ValueError("phase exploded")

    ex = ThreadPoolExecutor(max_workers=1)
    with pytest.raises(ValueError, match="phase exploded"):
        P._bounded_join(ex, [ex.submit(_boom)], 30.0, "test block")


def test_timeout_defaults_and_env_tuning(monkeypatch):
    assert P._FRONT_BLOCK_TIMEOUT_S == 1500.0
    assert P._PHASE7_TIMEOUT_S == 900.0

    monkeypatch.setenv("PIPELINE_FRONT_BLOCK_TIMEOUT_S", "123")
    monkeypatch.setenv("PIPELINE_PHASE7_TIMEOUT_S", "bad-value")
    assert P._env_seconds("PIPELINE_FRONT_BLOCK_TIMEOUT_S", 1500.0) == 123.0
    # invalid env value falls back to the default
    assert P._env_seconds("PIPELINE_PHASE7_TIMEOUT_S", 900.0) == 900.0
    assert P._env_seconds("PIPELINE_NOT_SET_ANYWHERE", 42.0) == 42.0
