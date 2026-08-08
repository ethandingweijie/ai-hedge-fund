from fastapi import APIRouter, HTTPException
import json
import re
from pathlib import Path
from pydantic import BaseModel

from app.backend.models.schemas import ErrorResponse

router = APIRouter(prefix="/storage")

# Allowed filename pattern: alphanumeric, dash, underscore, dot, forward slash for subdirs
# Disallow path traversal sequences and absolute paths
_SAFE_FILENAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._/\-]*$')

class SaveJsonRequest(BaseModel):
    filename: str
    data: dict

def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal attacks.

    Rejects filenames containing '..' or absolute paths.
    """
    # Reject path traversal
    if '..' in filename:
        raise ValueError("Path traversal not allowed")
    # Reject absolute paths
    if filename.startswith('/') or filename.startswith('\\'):
        raise ValueError("Absolute paths not allowed")
    # Reject Windows drive letters
    if len(filename) >= 2 and filename[1] == ':':
        raise ValueError("Drive letter paths not allowed")
    # Verify against allowed pattern
    if not _SAFE_FILENAME_RE.match(filename):
        raise ValueError("Filename contains disallowed characters")
    return filename

@router.post(
    path="/save-json",
    responses={
        200: {"description": "File saved successfully"},
        400: {"model": ErrorResponse, "description": "Invalid request parameters"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def save_json_file(request: SaveJsonRequest):
    """Save JSON data to the project's /outputs directory."""
    try:
        # Sanitize filename to prevent path traversal
        safe_filename = _sanitize_filename(request.filename)

        # Create outputs directory if it doesn't exist
        project_root = Path(__file__).parent.parent.parent.parent  # Navigate to project root
        outputs_dir = project_root / "outputs"
        outputs_dir.mkdir(exist_ok=True)

        # Construct file path (now safe after sanitization)
        file_path = outputs_dir / safe_filename

        # Ensure the resolved path is still within outputs_dir
        resolved_path = file_path.resolve()
        if not str(resolved_path).startswith(str(outputs_dir.resolve())):
            raise HTTPException(status_code=400, detail="Path traversal not allowed")

        # Create parent directories if needed (for subdirectories like "reports/foo.json")
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Save JSON data to file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(request.data, f, indent=2, ensure_ascii=False)

        return {
            "success": True,
            "message": f"File saved successfully to {file_path}",
            "filename": safe_filename
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}") 