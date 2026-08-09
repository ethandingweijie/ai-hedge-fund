from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import List

from app.backend.database import get_db
from app.backend.repositories.api_key_repository import ApiKeyRepository
from app.backend.routes.deps import require_admin
from app.backend.models.schemas import (
    ApiKeyCreateRequest,
    ApiKeyUpdateRequest,
    ApiKeyResponse,
    ApiKeySummaryResponse,
    ApiKeyBulkUpdateRequest,
    ErrorResponse
)

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _to_response(api_key) -> ApiKeyResponse:
    """Serialise an ApiKey row without its secret.

    Never use ApiKeyResponse.from_orm() here: the ORM row carries `key_value`,
    and the whole point of this layer is that the value never crosses the wire.
    """
    raw = api_key.key_value or ""
    return ApiKeyResponse(
        id=api_key.id,
        provider=api_key.provider,
        key_preview=f"…{raw[-4:]}" if len(raw) >= 4 else ("set" if raw else None),
        is_active=api_key.is_active,
        description=api_key.description,
        created_at=api_key.created_at,
        updated_at=api_key.updated_at,
        last_used=api_key.last_used,
        user_id=api_key.user_id,
    )


@router.post(
    "/",
    response_model=ApiKeyResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_or_update_api_key(request: ApiKeyCreateRequest, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Create a new API key or update existing one (admin only).

    user_id omitted/NULL targets the GLOBAL key; a user_id assigns a
    per-user override (Phase 3e).
    """
    try:
        repo = ApiKeyRepository(db)
        api_key = repo.create_or_update_api_key(
            provider=request.provider,
            key_value=request.key_value,
            description=request.description,
            is_active=request.is_active,
            user_id=request.user_id
        )
        return _to_response(api_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create/update API key: {str(e)}")


@router.get(
    "/",
    response_model=List[ApiKeySummaryResponse],
    responses={
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_api_keys(include_inactive: bool = False, db: Session = Depends(get_db)):
    """Get all API keys (without actual key values for security)"""
    try:
        repo = ApiKeyRepository(db)
        api_keys = repo.get_all_api_keys(include_inactive=include_inactive)
        return [ApiKeySummaryResponse.from_orm(key) for key in api_keys]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve API keys: {str(e)}")


@router.get(
    "/{provider}",
    response_model=ApiKeyResponse,
    responses={
        404: {"model": ErrorResponse, "description": "API key not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_api_key(provider: str, user_id: int = None, db: Session = Depends(get_db)):
    """Get a specific API key by provider. user_id selects a per-user
    row (Phase 3e); omitted = the global key."""
    try:
        repo = ApiKeyRepository(db)
        api_key = repo.get_api_key_by_provider(provider, user_id=user_id)
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        return _to_response(api_key)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve API key: {str(e)}")


@router.put(
    "/{provider}",
    response_model=ApiKeyResponse,
    responses={
        404: {"model": ErrorResponse, "description": "API key not found"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_api_key(provider: str, request: ApiKeyUpdateRequest, user_id: int = None, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Update an existing API key (admin only). user_id selects a
    per-user row (Phase 3e); omitted = the global key."""
    try:
        repo = ApiKeyRepository(db)
        api_key = repo.update_api_key(
            provider=provider,
            key_value=request.key_value,
            description=request.description,
            is_active=request.is_active,
            user_id=user_id
        )
        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")
        return _to_response(api_key)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update API key: {str(e)}")


@router.delete(
    "/{provider}",
    responses={
        204: {"description": "API key deleted successfully"},
        404: {"model": ErrorResponse, "description": "API key not found"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_api_key(provider: str, user_id: int = None, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Delete an API key (admin only). user_id selects a per-user row
    (Phase 3e); omitted = the global key."""
    try:
        repo = ApiKeyRepository(db)
        success = repo.delete_api_key(provider, user_id=user_id)
        if not success:
            raise HTTPException(status_code=404, detail="API key not found")
        return {"message": "API key deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")


@router.patch(
    "/{provider}/deactivate",
    response_model=ApiKeySummaryResponse,
    responses={
        404: {"model": ErrorResponse, "description": "API key not found"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def deactivate_api_key(provider: str, user_id: int = None, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Deactivate an API key without deleting it (admin only). user_id
    selects a per-user row (Phase 3e); omitted = the global key."""
    try:
        repo = ApiKeyRepository(db)
        success = repo.deactivate_api_key(provider, user_id=user_id)
        if not success:
            raise HTTPException(status_code=404, detail="API key not found")

        # Return the updated key (include_inactive: it was JUST deactivated,
        # so an active-only re-fetch would return None and 500 the route)
        api_key = repo.get_api_key_by_provider(
            provider, user_id=user_id, include_inactive=True)
        return ApiKeySummaryResponse.from_orm(api_key)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to deactivate API key: {str(e)}")


@router.post(
    "/bulk",
    response_model=List[ApiKeyResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def bulk_update_api_keys(request: ApiKeyBulkUpdateRequest, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Bulk create or update multiple API keys (admin only)"""
    try:
        repo = ApiKeyRepository(db)
        api_keys_data = [
            {
                'provider': key.provider,
                'key_value': key.key_value,
                'description': key.description,
                'is_active': key.is_active
            }
            for key in request.api_keys
        ]
        api_keys = repo.bulk_create_or_update(api_keys_data)
        return [_to_response(key) for key in api_keys]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to bulk update API keys: {str(e)}")


@router.patch(
    "/{provider}/last-used",
    responses={
        200: {"description": "Last used timestamp updated"},
        404: {"model": ErrorResponse, "description": "API key not found"},
        403: {"model": ErrorResponse, "description": "Admin access required"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_last_used(provider: str, user_id: int = None, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """Update the last used timestamp for an API key (admin only).
    user_id selects a per-user row (Phase 3e); omitted = the global key."""
    try:
        repo = ApiKeyRepository(db)
        success = repo.update_last_used(provider, user_id=user_id)
        if not success:
            raise HTTPException(status_code=404, detail="API key not found")
        return {"message": "Last used timestamp updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update last used timestamp: {str(e)}") 