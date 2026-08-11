from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

from app.backend.auth_gate import AuthGateMiddleware, verify_startup_config
from app.backend.routes import api_router
from app.backend.database.connection import engine
from app.backend.database.models import Base
from app.backend.services.ollama_service import ollama_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Hedge Fund API", description="Backend API for AI Hedge Fund", version="1.9.0")

# Refuse to boot with a forgeable JWT signing key (see auth_gate.py).
verify_startup_config()

# Initialize database tables (this is safe to run multiple times)
Base.metadata.create_all(bind=engine)

# create_all cannot ALTER tables that already exist — the schema guard
# backfills columns added by later model changes (idempotent, dialect-aware).
from app.backend.database.schema_guard import ensure_all as _ensure_schema
_ensure_schema(engine)

# Configure CORS — local dev ports + any extra origins from ALLOWED_ORIGINS env var
_dev_origins = [
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in range(5173, 5181)
]
_extra_origins = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
]
# Middleware order: Starlette makes the LAST-added middleware the outermost, so
# AuthGate is registered first and CORS second. That way CORS wraps the gate and
# a 401 still carries Access-Control-Allow-Origin — otherwise the browser
# reports an opaque CORS failure instead of the actual 401.
app.add_middleware(AuthGateMiddleware)

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
    """Startup event — checks Ollama availability.

    Scheduled jobs (VGPM backfill, idea-of-the-day, IV15 sweep, fund-flow
    brief, the three 100-Q schedules) do NOT run here: as of Phase 4 the
    dedicated scheduler service (app/backend/scheduler_service.py) owns the
    fire times and enqueues them on the arq worker. This keeps web restarts
    from re-triggering catch-up work and makes the web tier horizontally
    scalable.
    """
    if os.environ.get("DISABLE_OLLAMA", "").lower() in ("1", "true", "yes"):
        logger.info("Ollama disabled via DISABLE_OLLAMA env var — skipping check")
    else:
        await _check_ollama()


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

