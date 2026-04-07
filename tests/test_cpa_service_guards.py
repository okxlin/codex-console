import asyncio
from contextlib import contextmanager

from src.web.routes.upload import cpa_services as cpa_routes


class DummyService:
    def __init__(self, service_id=1, enabled=True):
        self.id = service_id
        self.name = "CPA Main"
        self.api_url = "https://cpa.example.com"
        self.api_token = "token-123"
        self.proxy_url = None
        self.enabled = enabled
        self.priority = 0
        self.created_at = None
        self.updated_at = None


class DummySettings:
    registration_auto_cpa_service_id = 1
    account_maintenance_cleanup_remote_cpa = True
    account_maintenance_cpa_service_id = 1


def test_update_cpa_service_rejects_disabling_in_use_service(monkeypatch):
    @contextmanager
    def fake_get_db():
        yield object()

    monkeypatch.setattr(cpa_routes, "get_db", fake_get_db)
    monkeypatch.setattr(cpa_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(cpa_routes.crud, "get_cpa_service_by_id", lambda db, service_id: DummyService(service_id=service_id, enabled=True))

    request = cpa_routes.CpaServiceUpdate(enabled=False)

    try:
        asyncio.run(cpa_routes.update_cpa_service(1, request))
    except cpa_routes.HTTPException as exc:
        assert exc.status_code == 400
        assert "请先在设置中解绑后再禁用" in exc.detail
    else:
        raise AssertionError("expected HTTPException when disabling in-use CPA service")
