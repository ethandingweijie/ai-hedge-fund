from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime

from app.backend.database.models import ApiKey


class ApiKeyRepository:
    """Repository for API key database operations.

    Phase 3e: rows carry a nullable user_id owner (NULL = global
    admin-managed key). The provider-keyed CRUD methods below operate on
    GLOBAL rows only — per-user rows are managed through the explicit
    user_id parameter of create_or_update_api_key, and resolved through
    get_global_api_keys / get_user_api_keys.
    """

    def __init__(self, db: Session):
        self.db = db

    def create_or_update_api_key(
        self,
        provider: str,
        key_value: str,
        description: str = None,
        is_active: bool = True,
        user_id: Optional[int] = None
    ) -> ApiKey:
        """Create a new API key or update the existing one for this
        (provider, owner) pair. user_id=None targets the global key."""
        # Check if an API key already exists for this provider + owner
        if user_id is None:
            owner_filter = ApiKey.user_id.is_(None)
        else:
            owner_filter = ApiKey.user_id == user_id
        existing_key = self.db.query(ApiKey).filter(
            ApiKey.provider == provider, owner_filter).first()

        if existing_key:
            # Update existing key
            existing_key.key_value = key_value
            existing_key.description = description
            existing_key.is_active = is_active
            existing_key.updated_at = func.now()
            self.db.commit()
            self.db.refresh(existing_key)
            return existing_key
        else:
            # Create new key
            api_key = ApiKey(
                provider=provider,
                key_value=key_value,
                description=description,
                is_active=is_active,
                user_id=user_id
            )
            self.db.add(api_key)
            self.db.commit()
            self.db.refresh(api_key)
            return api_key

    def get_api_key_by_provider(self, provider: str) -> Optional[ApiKey]:
        """Get the GLOBAL API key by provider name"""
        return self.db.query(ApiKey).filter(
            ApiKey.provider == provider,
            ApiKey.user_id.is_(None),
            ApiKey.is_active == True
        ).first()

    def get_global_api_keys(self, include_inactive: bool = False) -> List[ApiKey]:
        """Global (admin-managed, user_id NULL) keys — the fallback layer
        for every run."""
        query = self.db.query(ApiKey).filter(ApiKey.user_id.is_(None))
        if not include_inactive:
            query = query.filter(ApiKey.is_active == True)
        return query.order_by(ApiKey.provider).all()

    def get_user_api_keys(self, user_id: int,
                          include_inactive: bool = False) -> List[ApiKey]:
        """A single user's own keys (per-provider overrides of globals)."""
        query = self.db.query(ApiKey).filter(ApiKey.user_id == user_id)
        if not include_inactive:
            query = query.filter(ApiKey.is_active == True)
        return query.order_by(ApiKey.provider).all()

    def get_all_api_keys(self, include_inactive: bool = False) -> List[ApiKey]:
        """Get all API keys (every owner — admin listing)"""
        query = self.db.query(ApiKey)
        if not include_inactive:
            query = query.filter(ApiKey.is_active == True)
        return query.order_by(ApiKey.provider, ApiKey.user_id).all()

    def update_api_key(
        self,
        provider: str,
        key_value: str = None,
        description: str = None,
        is_active: bool = None
    ) -> Optional[ApiKey]:
        """Update an existing GLOBAL API key"""
        api_key = self.db.query(ApiKey).filter(
            ApiKey.provider == provider,
            ApiKey.user_id.is_(None)).first()
        if not api_key:
            return None

        if key_value is not None:
            api_key.key_value = key_value
        if description is not None:
            api_key.description = description
        if is_active is not None:
            api_key.is_active = is_active

        api_key.updated_at = func.now()
        self.db.commit()
        self.db.refresh(api_key)
        return api_key

    def delete_api_key(self, provider: str) -> bool:
        """Delete a GLOBAL API key by provider"""
        api_key = self.db.query(ApiKey).filter(
            ApiKey.provider == provider,
            ApiKey.user_id.is_(None)).first()
        if not api_key:
            return False

        self.db.delete(api_key)
        self.db.commit()
        return True

    def deactivate_api_key(self, provider: str) -> bool:
        """Deactivate a GLOBAL API key instead of deleting it"""
        api_key = self.db.query(ApiKey).filter(
            ApiKey.provider == provider,
            ApiKey.user_id.is_(None)).first()
        if not api_key:
            return False

        api_key.is_active = False
        api_key.updated_at = func.now()
        self.db.commit()
        return True

    def update_last_used(self, provider: str) -> bool:
        """Update the last_used timestamp for the GLOBAL API key"""
        api_key = self.db.query(ApiKey).filter(
            ApiKey.provider == provider,
            ApiKey.user_id.is_(None),
            ApiKey.is_active == True
        ).first()
        if not api_key:
            return False

        api_key.last_used = func.now()
        self.db.commit()
        return True

    def bulk_create_or_update(self, api_keys_data: List[dict]) -> List[ApiKey]:
        """Bulk create or update multiple API keys (global scope)"""
        results = []
        for data in api_keys_data:
            api_key = self.create_or_update_api_key(
                provider=data['provider'],
                key_value=data['key_value'],
                description=data.get('description'),
                is_active=data.get('is_active', True)
            )
            results.append(api_key)
        return results 