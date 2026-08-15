# Redis + arq Worker Deployment (Phase 2g)

The scalability upgrade runs long jobs (deep-research pipeline, /research/*
jobs, hedge-fund graph runs) in a separate **arq worker** process instead of
the web process. Redis carries the task queue and the cross-process progress
stream (pub/sub + replay buffer).

All of this is **dormant until `REDIS_URL` exists** in the environment:
without it, every route runs its original in-process path, so the web code
can be deployed at any time with zero behaviour change.

## Moving parts

| Piece | Image | Command | Needs |
|---|---|---|---|
| web (existing service) | `docker/Dockerfile.web` | `uvicorn app.backend.main:app` (START_CMD unset) | `REDIS_URL` to enable queue mode |
| worker (new service) | `docker/Dockerfile.web` | `python -m app.backend.worker_main` (via `START_CMD` Variable) | `REDIS_URL`, `DATABASE_URL`, `JWT_SECRET_KEY`, same API-key env vars as web |
| scheduler (Phase 4) | `docker/Dockerfile.web` | `python -m app.backend.scheduler_service` (via `START_CMD` Variable) | `REDIS_URL`, `DATABASE_URL` (predeploy schema sync) |
| Redis (Railway plugin) | — | — | nothing |

One `railway.toml` applies to EVERY service and code config overrides the
dashboard, so all services inherit `docker/Dockerfile.web`, the
`sync_schema` predeploy, and the `healthcheckPath = "/"` probe. Per-service
divergence happens ONLY through the `START_CMD` env var (the image CMD
expands `${START_CMD:-uvicorn ...}`). Non-HTTP processes run
`app/backend/health_responder.py` on `$PORT` so the inherited healthcheck
passes. The dashboard Builder / Start Command fields are NOT reliable here —
do not use them.

Worker limits (see `WorkerSettings` in `app/backend/worker.py`):
`max_jobs=10` concurrent, `job_timeout=3600s` (a full VGPM backfill can
exceed 30 min; the timeout is a cap, not a delay), results kept 1 h,
no automatic retries (failures are surfaced to the client stream).

## Rollout order (important)

If the web service sees `REDIS_URL` but no worker is consuming the queue,
runs will be enqueued and sit there until the stream deadline. Follow this
order:

1. **Deploy the web code** (already queue-aware) with **no** `REDIS_URL`.
   Production behaviour is unchanged.
2. **Add the Redis plugin** to the Railway project.
3. **Create the worker service**:
   - New service → GitHub repo → same repository, same `main` branch.
   - Build settings are inherited from `railway.toml` (Dockerfile.web).
     Set Variables: `START_CMD=python -m app.backend.worker_main`, a
     reference to the Redis plugin's `REDIS_URL`, a reference to the
     Postgres plugin's `DATABASE_URL` (the inherited predeploy runs
     `scripts/sync_schema.py`), `JWT_SECRET_KEY`, and the same API-key
     variables as web.
   - No public domain needed.
4. **Verify the worker started** — its logs should show:
   `health responder listening on :<PORT>` followed by
   `arq worker started: queue=arq:queue max_jobs=10 job_timeout=3600s`.
5. **Only now add `REDIS_URL` to the WEB service** (reference the same
   plugin variable) and redeploy. Queue mode activates.

## Verification after step 5

- Web logs: no `queue mode unavailable` warnings on `POST /analysis/run`.
- Worker logs: `run_analysis_pipeline_task` job lines while a run streams.
- Frontend: run an analysis — progress bar advances exactly as before.
- `GET /admin/diag` (X-Admin-Secret) shows `REDIS_URL: true`.
- Second concurrent request for the same ticker shows the waiter message
  ("Analysis already in progress … awaiting result") and receives the cached
  result when the runner finishes.

## Rollback

Remove `REDIS_URL` from the web service and redeploy. Web immediately
returns to in-process execution. The worker can then be paused/deleted.
In-flight queued runs are abandoned — re-run them from the UI.

## Scaling later

- More throughput: add worker replicas (same service). Dedup is distributed
  (Redis SETNX for analysis, job-store in-flight checks for research), so
  replicas don't double-run.
- Redis is single-point for queue + progress; a Redis outage degrades web to
  in-process mode automatically (`redis_ready()` fails → fallback path).

## Phase 4 — Scheduler service

The 7 fire schedules (VGPM backfill, idea-of-the-day, IV15 sweep, weekly
fund-flow brief, and the three 100-Q schedulers) run in a dedicated
`scheduler` service (`app/backend/scheduler_service.py`) instead of web
daemon threads. It owns fire TIMES only: each fire acquires a Redis slot
lock (`sched_lock:{name}:{slot}`, SET NX EX) and enqueues the matching
`run_*_task` for the worker. Every task keeps the legacy DB-timestamp
idempotency gate, so even a lock bypass (Redis down) can't double-run a
cycle.

**Rollout order (no gap at fire times):**

1. Deploy the additive commit (worker tasks + scheduler service exist; web
   still runs its in-process schedulers). Overlap is safe: worker-side
   idempotency makes any double fire a no-op.
2. **Create the `scheduler` service**:
   - Same repo/branch; build settings inherited from `railway.toml`.
   - Variables: `START_CMD=python -m app.backend.scheduler_service`,
     `REDIS_URL` (Redis plugin reference), `DATABASE_URL` (Postgres plugin
     reference — the inherited predeploy runs `sync_schema`). No API keys
     needed: the scheduler never calls LLM/market APIs itself, it only
     enqueues.
   - No public domain needed.
3. **Verify boot logs**: `health responder listening on :<PORT>`, then
   `scheduler service starting: 7 schedules (...)`, then one
   `schedule '<name>' started` line per schedule, plus catch-up enqueue
   lines for `vgpm_backfill` / `hundred_q_daily_sweep` (the tasks skip
   worker-side if today's window is already filled).
4. Remove the scheduler block from the web service (second commit) and
   redeploy web.

**Kill switches** (Variables on the scheduler service, hot-re-read):
`SCHEDULER_SERVICE_DISABLED=true` (whole service), `VGPM_BACKFILL_DISABLED`,
and the legacy `IDEA_SCHEDULER_DISABLED` / `IV15_ALERT_DISABLED` /
`FUNDFLOW_SCHEDULER_DISABLED` / `HUNDRED_Q_*_DISABLED` names.

**Ops:** `GET /admin/diag?secret=...` section 8 shows queue depth and the
worker health key; scheduled job ids are `sched:{name}:{slot}` and slot
locks are `sched_lock:{name}:{slot}` in Redis.

## Phase 5 — Multi-replica web

The queue-mode web process is stateless: JWT auth is stateless, run dedup
and rate limiting live in Redis, progress streams over Redis pub/sub, and
all persistent state is in Postgres. Two (or more) replicas can serve
behind Railway's load balancer with no sticky sessions required.

**Fixes made for replica safety:**

- `/screener/admin/backfill-universe` used an in-process `_backfill_running`
  flag — only one replica was protected. It now takes the Redis lock
  `lock:vgpm_backfill` (SET NX EX, TTL 2h, released early on completion)
  via `app/backend/services/redis_locks.py`. The worker's scheduled
  `run_vgpm_backfill_task` takes the SAME lock, so an admin trigger and
  the daily 09:00 UTC job can never overlap on any replica (they share
  one FMP token bucket). Fails open to the old per-process flag when
  Redis is unavailable.
- Dead `POST /storage/save-json` route removed (wrote files to the web
  container's local disk — per-replica and unreachable by the other
  replica; the frontend never called it). `routes/storage.py` deleted,
  router deregistered, frontend `saveJsonFile` removed.

**Audit findings left as-is (deliberately):**

- `routes/analysis.py` in-process `_in_flight`/`_live_phases` dicts —
  only used when queue mode is OFF (Redis down); queue-mode path is
  Redis-backed. Degraded single-replica semantics are acceptable.
- `routes/research.py` `_BACKGROUND_TASKS` + heartbeat threads —
  fallback path only; job state lives in Postgres (`job_store`).
- `routes/dd_alerts.py` agent threads — request-scoped; results persist
  to Postgres via `_upsert_dd_report`/`alert_dedup`.
- `routes/hedge_fund.py` backtest SSE — request-scoped stream.
- `sqlite_migration` `_busy` flag — one-shot admin op, already run.

**Rollout:**

1. Check the web service for a mounted volume (diag lists `/data/*.db`).
   SQLite is dormant in PG mode; detach any volume BEFORE adding replicas
   (Railway volumes are single-instance — a second replica cannot mount
   the same volume, and nothing reads those files in PG mode).
2. Deploy this commit, verify diag + a normal run on 1 replica.
3. Bump the web service to 2 replicas (Settings → Networking/Replicas).
4. Verify: hit `/admin/diag` repeatedly — both replicas should answer
   (log the instance id / boot time per response if exposed); start a
   run and confirm the SSE stream survives whichever replica answers.
5. Rollback: set replicas back to 1. No schema or env changes are
   involved, so rollback is instant.
