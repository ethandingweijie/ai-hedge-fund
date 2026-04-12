"""
Temporary endpoint to upload a local run_archive.db to the cloud.
DELETE THIS ROUTE after migration is complete.
"""
import os
import shutil
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

# One-time secret to prevent unauthorized uploads
UPLOAD_SECRET = os.environ.get("DB_UPLOAD_SECRET", "")


@router.post("/admin/upload-db")
async def upload_db(file: UploadFile = File(...), secret: str = ""):
    """Replace the cloud run_archive.db with an uploaded file."""
    if not UPLOAD_SECRET or secret != UPLOAD_SECRET:
        raise HTTPException(status_code=403, detail="Invalid or missing secret")

    db_path = os.environ.get("RUN_ARCHIVE_PATH", "/data/run_archive.db")

    # Write uploaded file to a temp location first
    tmp_path = db_path + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                f.write(chunk)

        # Replace the existing DB
        shutil.move(tmp_path, db_path)
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        logger.info(f"Database uploaded successfully: {size_mb:.1f} MB")
        return {"status": "ok", "size_mb": round(size_mb, 1), "path": db_path}
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=str(e))
