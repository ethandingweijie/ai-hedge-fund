from sqlalchemy.orm import Session
from typing import Dict, Optional
from app.backend.repositories.api_key_repository import ApiKeyRepository


class ApiKeyService:
    """Loads API keys for pipeline requests.

    Phase 3e resolution chain: user_id=None hydrates from GLOBAL
    (admin-managed) keys only; user_id=N merges globals with that user's
    own keys, the user's rows overriding per provider. User rows are
    never visible to other users or to unauthenticated runs.
    """

    def __init__(self, db: Session):
        self.repository = ApiKeyRepository(db)

    def get_api_keys_dict(self, user_id: Optional[int] = None) -> Dict[str, str]:
        """Load active API keys as a provider → key_value dict suitable
        for injecting into requests.

        user_id=None → global keys only (service calls / unauthenticated
        runs — identical to pre-3e behaviour).
        user_id=N    → global keys overlaid with user N's own keys.
        """
        merged = {key.provider: key.key_value
                  for key in self.repository.get_global_api_keys(
                      include_inactive=False)}
        if user_id is not None:
            for key in self.repository.get_user_api_keys(
                    user_id, include_inactive=False):
                merged[key.provider] = key.key_value
        return merged

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get a specific GLOBAL API key by provider"""
        api_key = self.repository.get_api_key_by_provider(provider)
        return api_key.key_value if api_key else None
