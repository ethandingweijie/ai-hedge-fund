"""Item 6 (owner, 2026-11-28): the admin secret is header-only by default, and the API docs are off."""
from types import SimpleNamespace

from app.backend import auth_gate as ag


def _req(header="", query=""):
    return SimpleNamespace(headers={"X-Admin-Secret": header} if header else {},
                           query_params={"secret": query} if query else {})


def test_query_secret_refused_by_default(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "s3cret")
    monkeypatch.delenv("ADMIN_QUERY_SECRET", raising=False)
    assert not ag._valid_admin_secret(_req(query="s3cret"))
    assert ag._valid_admin_secret(_req(header="s3cret"))
    assert ag.presented_admin_secret(_req(query="s3cret"), "s3cret") == ""


def test_query_secret_can_be_reenabled_for_the_dashboard(monkeypatch):
    monkeypatch.setenv("DB_UPLOAD_SECRET", "s3cret")
    monkeypatch.setenv("ADMIN_QUERY_SECRET", "on")
    assert ag._valid_admin_secret(_req(query="s3cret"))


def test_admin_route_helper_ignores_query_secret(monkeypatch):
    monkeypatch.delenv("ADMIN_QUERY_SECRET", raising=False)
    from app.backend.routes import admin
    monkeypatch.setattr(admin, "ADMIN_SECRET", "s3cret")
    assert not admin._secret_ok(_req(), "s3cret")
    assert admin._secret_ok(_req(header="s3cret"), "")


def test_api_docs_off_by_default():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "app" / "backend" / "main.py").read_text(encoding="utf-8")
    assert 'os.environ.get("ENABLE_API_DOCS", "off")' in src
    assert 'docs_url="/docs" if _API_DOCS else None' in src and 'openapi_url="/openapi.json" if _API_DOCS else None' in src
