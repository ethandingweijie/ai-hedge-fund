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
| web (existing service) | `docker/Dockerfile.web` | `uvicorn app.backend.main:app` | `REDIS_URL` to enable queue mode |
| worker (new service) | `docker/Dockerfile.worker` | `arq app.backend.worker.WorkerSettings` | `REDIS_URL`, `DATABASE_URL`, same API-key env vars as web |
| Redis (Railway plugin) | — | — | nothing |

Worker limits (see `WorkerSettings` in `app/backend/worker.py`):
`max_jobs=10` concurrent, `job_timeout=1800s`, results kept 1 h,
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
   - Settings → Build → Dockerfile path: `docker/Dockerfile.worker`.
   - No predeploy command (schema sync stays on the web service only).
   - No public domain needed.
   - Variables: reference the Redis plugin's `REDIS_URL`, and copy the web
     service's `DATABASE_URL`, `JWT_SECRET_KEY`, API-key variables, and
     anything else the pipeline reads. **Do not set `PORT`-dependent vars.**
4. **Verify the worker started** — its logs should show:
   `arq worker started: queue=arq:queue max_jobs=10 job_timeout=1800s`.
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
