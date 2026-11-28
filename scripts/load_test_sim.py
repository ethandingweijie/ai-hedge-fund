"""Capacity load test with a simulated Qwen (owner, 2026-11-28, item 3). No real LLM calls, no cost.

    python scripts/load_test_sim.py                       # 10, 25, 50 concurrent analyses
    python scripts/load_test_sim.py --runs 25 --processes 3 --capacity-rps 6

What it simulates
  * N analyses spread over P "processes" (each with its OWN Redis client, like separate worker replicas), all
    sharing one Redis (fakeredis in-process; set REDIS_URL to use a real one).
  * Each analysis makes CALLS Qwen calls of LATENCY seconds each, two at a time (the pipeline's parallel legs).
  * The fake Qwen has a HIDDEN capacity (requests/s, sliding 1 s window) the limiter is never told -- like
    DashScope's dynamic RPM. Over it, the call gets a 429 and is retried after the Retry-After.
  * Admission caps analyses at --cap at once; the rest wait in line.

What it reports, per scenario: completed / failed analyses, Qwen 429 rate, the rate the limiter learned, p50/p90
analysis time and queue wait, and the same run WITHOUT the shared limiter (old per-process buckets) for contrast.
Pass (item 3): 0 failed, 429 rate < 1%, waits predictable.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import threading
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeQwen:
    """Hidden-capacity provider: more than `rps` requests in any 1 s window -> 429."""

    def __init__(self, rps: float):
        self.rps = rps
        self.lock = threading.Lock()
        self.window: deque = deque()
        self.ok = 0
        self.rejected = 0

    def call(self) -> int:
        now = time.monotonic()
        with self.lock:
            while self.window and now - self.window[0] > 1.0:
                self.window.popleft()
            if len(self.window) >= self.rps:
                self.rejected += 1
                return 429
            self.window.append(now)
            self.ok += 1
            return 200


def _pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def scenario(runs: int, processes: int, capacity_rps: float, cap: int, calls: int, latency: float,
             shared: bool) -> dict:
    import fakeredis
    from fakeredis import aioredis
    from src.llm import global_limiter as gl
    from app.backend.services import admission as adm
    from src.research_ideas.complacency import qwen_throttle as qt

    server = fakeredis.FakeServer()
    os.environ.update({"GLOBAL_LLM_LIMITER": "on" if shared else "off", "ANALYSIS_MAX_CONCURRENT": str(cap),
                       "LLM_RATE_START": "2", "LLM_RATE_STEP": "0.25", "LLM_RATE_STEP_EVERY": "10",
                       "LLM_COOLDOWN_S": "1.0", "LLM_RATE_MAX": "50"})
    qwen = FakeQwen(capacity_rps)
    clients = [fakeredis.FakeRedis(server=server) for _ in range(processes)]
    # old behaviour: one local bucket per process (0.5 req/s, burst 3 by default)
    local = [qt._QwenThrottle() for _ in range(processes)]
    glock = threading.Lock()
    def acquire(proc):
        if shared:
            with glock:
                gl._CLIENT["c"], gl._CLIENT["down_until"] = clients[proc], 0.0
            if gl.try_acquire("sim", max_wait_s=600) is not None:
                return
        local[proc].acquire(max_wait_s=600)

    def feedback(proc, status):
        if shared:
            gl.feedback("sim", status, 0, retry_after_s=1.0)
        elif status == 429:
            local[proc].report_429(1.0)

    def one_call(proc):
        for _ in range(50):
            acquire(proc)
            time.sleep(latency)
            st = qwen.call()
            feedback(proc, st)
            if st == 200:
                return True
        return False

    def analysis(i: int) -> tuple[bool, float]:
        proc = i % processes
        t0 = time.monotonic()
        ok = True
        for _ in range(calls // 2):                         # two parallel legs per step
            res = []
            th = [threading.Thread(target=lambda: res.append(one_call(proc))) for _ in range(2)]
            for t in th:
                t.start()
            for t in th:
                t.join()
            ok = ok and all(res)
        return ok, time.monotonic() - t0

    results, waits = [], []
    ar = aioredis.FakeRedis(server=server)

    async def runner(i):
        q0 = time.monotonic()
        slot = await adm.acquire(f"run{i}", redis_client=ar, poll_s=0.05)
        waits.append(time.monotonic() - q0)
        try:
            ok, dur = await asyncio.to_thread(analysis, i)
            results.append((ok, dur))
        finally:
            if slot:
                await adm.release(f"run{i}", redis_client=ar)

    async def main():
        await asyncio.gather(*(runner(i) for i in range(runs)))

    t0 = time.monotonic()
    asyncio.run(main())
    wall = time.monotonic() - t0
    learned = None
    if shared:
        gl._CLIENT["c"] = clients[0]
        learned = gl.snapshot("sim").get("rate_rps")
    total = qwen.ok + qwen.rejected
    return {"runs": runs, "shared": shared, "completed": sum(1 for ok, _ in results if ok),
            "failed": sum(1 for ok, _ in results if not ok), "qwen_calls": qwen.ok,
            "rate_429": qwen.rejected / total if total else 0.0, "learned_rps": learned,
            "run_p50_s": statistics.median([d for _, d in results]), "run_p90_s": _pct([d for _, d in results], 0.9),
            "wait_p50_s": statistics.median(waits), "wait_p90_s": _pct(waits, 0.9), "wall_s": wall}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, nargs="*", default=[10, 25, 50])
    ap.add_argument("--processes", type=int, default=3)
    ap.add_argument("--capacity-rps", type=float, default=8.0, help="hidden Qwen capacity (req/s)")
    ap.add_argument("--cap", type=int, default=25, help="global admission cap")
    ap.add_argument("--calls", type=int, default=20, help="Qwen calls per analysis")
    ap.add_argument("--latency", type=float, default=0.3, help="seconds per call")
    ap.add_argument("--no-contrast", action="store_true", help="skip the old per-process limiter comparison")
    a = ap.parse_args(argv)
    print(f"hidden Qwen capacity {a.capacity_rps} req/s; {a.processes} processes; cap {a.cap}; "
          f"{a.calls} calls x {a.latency}s per analysis\n")
    print(f"{'runs':>4} {'limiter':8} {'done':>5} {'fail':>4} {'429 rate':>8} {'learned':>8} "
          f"{'run p50':>8} {'run p90':>8} {'wait p50':>8} {'wait p90':>8} {'wall':>6}")
    for n in a.runs:
        for shared in ([True] if a.no_contrast else [True, False]):
            r = scenario(n, a.processes, a.capacity_rps, a.cap, a.calls, a.latency, shared)
            print(f"{r['runs']:>4} {'shared' if shared else 'local':8} {r['completed']:>5} {r['failed']:>4} "
                  f"{r['rate_429']:>8.1%} {str(r['learned_rps'] or '-')[:6]:>8} {r['run_p50_s']:>7.1f}s "
                  f"{r['run_p90_s']:>7.1f}s {r['wait_p50_s']:>7.1f}s {r['wait_p90_s']:>7.1f}s {r['wall_s']:>5.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
