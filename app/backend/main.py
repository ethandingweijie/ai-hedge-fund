from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

from app.backend.routes import api_router
from app.backend.database.connection import engine
from app.backend.database.models import Base
from app.backend.services.ollama_service import ollama_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Hedge Fund API", description="Backend API for AI Hedge Fund", version="1.9.0")

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# Configure CORS — local dev ports + any extra origins from ALLOWED_ORIGINS env var
_dev_origins = [
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in range(5173, 5180)
]
_extra_origins = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_dev_origins + ["capacitor://localhost", "http://localhost"] + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include all routes
app.include_router(api_router)

@app.on_event("startup")
async def startup_event():
    """Startup event to check Ollama availability and kick off VGPM backfill."""
    if os.environ.get("DISABLE_OLLAMA", "").lower() in ("1", "true", "yes"):
        logger.info("Ollama disabled via DISABLE_OLLAMA env var — skipping check")
    else:
        await _check_ollama()

    # Start VGPM backfill scheduler in background thread.
    import threading
    t = threading.Thread(target=_backfill_scheduler, daemon=True)
    t.start()


async def _check_ollama():
    try:
        logger.info("Checking Ollama availability...")
        status = await ollama_service.check_ollama_status()

        if status["installed"]:
            if status["running"]:
                logger.info(f"✓ Ollama is installed and running at {status['server_url']}")
                if status["available_models"]:
                    logger.info(f"✓ Available models: {', '.join(status['available_models'])}")
                else:
                    logger.info("ℹ No models are currently downloaded")
            else:
                logger.info("ℹ Ollama is installed but not running")
                logger.info("ℹ You can start it from the Settings page or manually with 'ollama serve'")
        else:
            logger.info("ℹ Ollama is not installed. Install it to use local models.")
            logger.info("ℹ Visit https://ollama.com to download and install Ollama")

    except Exception as e:
        logger.warning(f"Could not check Ollama status: {e}")
        logger.info("ℹ Ollama integration is available if you install it later")


# ── VGPM Backfill Scheduler ──────────────────────────────────────────────────

BACKFILL_HOUR = 9   # 9am local time daily
BACKFILL_MINUTE = 0


def _get_last_backfill_time():
    """Read the most recent cached_at from master_universe table."""
    try:
        from app.backend.services.screener_service import _connect, _ensure_tables
        _ensure_tables()
        conn = _connect()
        row = conn.execute("SELECT cached_at FROM master_universe LIMIT 1").fetchone()
        conn.close()
        if row and row[0]:
            from datetime import datetime, timezone
            return datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _should_backfill_now():
    """Return True if today's scheduled backfill hasn't run yet.

    Logic:
    - If no backfill has ever run → True
    - If last backfill was before today's 9am → True
    - Otherwise → False (already ran today)
    """
    from datetime import datetime, timezone, timedelta
    import time as _time

    last = _get_last_backfill_time()
    if last is None:
        logger.info("VGPM backfill: no previous backfill found — running now")
        return True

    # Compute today's 9am in local time, convert to UTC for comparison
    now_local = datetime.now()
    today_9am_local = now_local.replace(
        hour=BACKFILL_HOUR, minute=BACKFILL_MINUTE, second=0, microsecond=0
    )

    # Convert local 9am to UTC for comparison with cached_at (which is UTC)
    local_offset = datetime.now(timezone.utc).astimezone().utcoffset()
    today_9am_utc = today_9am_local.replace(tzinfo=timezone.utc) - local_offset

    if last < today_9am_utc:
        logger.info(
            "VGPM backfill: last ran %s, before today's 9am — running now",
            last.strftime("%Y-%m-%d %H:%M"),
        )
        return True

    logger.info(
        "VGPM backfill: already ran today at %s — next run at %d:%02d tomorrow",
        last.strftime("%H:%M"), BACKFILL_HOUR, BACKFILL_MINUTE,
    )
    return False


def _run_backfill():
    """Execute the backfill and log results."""
    try:
        from app.backend.services.screener_service import backfill_master_universe
        logger.info("VGPM backfill starting...")
        result = backfill_master_universe(batch_size=50, passes=5, delay=30)
        logger.info(
            "VGPM backfill complete: %d/%d tickers scored",
            result.get("scored", 0), result.get("total", 0),
        )
    except Exception as e:
        logger.warning("VGPM backfill failed (non-fatal): %s", e)


def _seconds_until_next_9am():
    """Return seconds until the next 9am local time."""
    from datetime import datetime, timedelta
    now = datetime.now()
    next_9am = now.replace(hour=BACKFILL_HOUR, minute=BACKFILL_MINUTE, second=0, microsecond=0)
    if now >= next_9am:
        next_9am += timedelta(days=1)
    delta = (next_9am - now).total_seconds()
    return delta


def _backfill_scheduler():
    """Background thread: run backfill on startup if needed, then daily at 9am."""
    import time

    # ── Startup check: run immediately if today's backfill hasn't happened
    if _should_backfill_now():
        _run_backfill()

    # ── Daily loop: sleep until next 9am, then run
    while True:
        wait = _seconds_until_next_9am()
        logger.info(
            "VGPM backfill scheduler: next run in %.1f hours (%d:%02d)",
            wait / 3600, BACKFILL_HOUR, BACKFILL_MINUTE,
        )
        time.sleep(wait)
        _run_backfill()

