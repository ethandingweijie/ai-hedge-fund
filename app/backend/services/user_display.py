"""
app/backend/services/user_display.py
=====================================
Shared helper for rendering a User as a display label. Small and
standalone (not tied to the chat feature) so any future feature needing a
user-facing name can reuse it instead of re-deriving the fallback.
"""


def display_name(user) -> str:
    """User.name (set via OAuth provider) if present, else the email
    local-part (e.g. "ethandingweijie" for "ethandingweijie@gmail.com").
    Never returns None/empty for a valid User row."""
    if user.name:
        return user.name
    return user.email.split("@")[0]
