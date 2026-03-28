import asyncio
from contextlib import contextmanager
from pathlib import Path

from src.database.models import Base, CpaService, EmailService
from src.database.session import DatabaseSessionManager
from src.core.account_maintenance import get_account_maintenance_state
from src.web.routes import settings as settings_routes


class DummySettings:
    proxy_enabled = False
    proxy_type = "http"
    proxy_host = "127.0.0.1"
    proxy_port = 7890
    proxy_username = None
    proxy_password = None
    proxy_dynamic_enabled = False
    proxy_dynamic_api_url = ""
    proxy_dynamic_api_key_header = "X-API-Key"
    proxy_dynamic_result_field = ""
    proxy_dynamic_api_key = None
    registration_max_retries = 3
    registration_timeout = 120
    registration_default_password_length = 12
    registration_sleep_min = 5
    registration_sleep_max = 30
    registration_entry_flow = "abcard"
    registration_auto_enabled = True
    registration_auto_check_interval = 90
    registration_auto_min_ready_auth_files = 3
    registration_auto_email_service_type = "tempmail"
    registration_auto_email_service_id = 7
    registration_auto_proxy = "http://127.0.0.1:7890"
    registration_auto_interval_min = 8
    registration_auto_interval_max = 18
    registration_auto_concurrency = 2
    registration_auto_mode = "parallel"
    registration_auto_cpa_service_id = 9
    account_maintenance_enabled = True
    account_maintenance_schedule_time = "04:30"
    account_maintenance_validation_proxy = "http://127.0.0.1:7899"
    account_maintenance_cleanup_local = True
    account_maintenance_cleanup_remote_cpa = True
    account_maintenance_cpa_service_id = 11
    webui_host = "127.0.0.1"
    webui_port = 3000
    debug = False
    webui_access_password = None
    tempmail_enabled = True
    tempmail_base_url = "https://tempmail.example.com"
    tempmail_timeout = 120
    tempmail_max_retries = 3
    yyds_mail_enabled = False
    yyds_mail_base_url = "https://yyds.example.com"
    yyds_mail_default_domain = "example.com"
    yyds_mail_timeout = 120
    yyds_mail_max_retries = 3
    yyds_mail_api_key = None
    email_code_timeout = 120
    email_code_poll_interval = 3


def test_get_registration_settings_includes_auto_fields(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(settings_routes, "get_account_maintenance_state", lambda: get_account_maintenance_state())
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_state", lambda: {"status": "idle"})
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_logs", lambda: ["[账号维护] 测试日志"])

    result = asyncio.run(settings_routes.get_registration_settings())

    assert result["entry_flow"] == "abcard"
    assert result["auto_enabled"] is True
    assert result["auto_check_interval"] == 90
    assert result["auto_min_ready_auth_files"] == 3
    assert result["auto_email_service_type"] == "tempmail"
    assert result["auto_email_service_id"] == 7
    assert result["auto_proxy"] == "http://127.0.0.1:7890"
    assert result["auto_interval_min"] == 8
    assert result["auto_interval_max"] == 18
    assert result["auto_concurrency"] == 2
    assert result["auto_mode"] == "parallel"
    assert result["auto_cpa_service_id"] == 9
    assert result["maintenance_enabled"] is True
    assert result["maintenance_schedule_time"] == "04:30"
    assert result["maintenance_validation_proxy"] == "http://127.0.0.1:7899"
    assert result["maintenance_cleanup_local"] is True
    assert result["maintenance_cleanup_remote_cpa"] is True
    assert result["maintenance_cpa_service_id"] == 11
    assert "maintenance_state" in result
    assert result["maintenance_logs"] == ["[账号维护] 测试日志"]


def test_get_all_settings_includes_webui_runtime_fields(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(settings_routes, "get_account_maintenance_state", lambda: {"status": "idle"})
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_state", lambda: {"status": "idle"})
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_logs", lambda: [])

    result = asyncio.run(settings_routes.get_all_settings())

    assert result["webui"]["host"] == DummySettings.webui_host
    assert result["webui"]["port"] == DummySettings.webui_port
    assert result["webui"]["debug"] == DummySettings.debug


def test_update_webui_settings_marks_restart_required_for_runtime_fields(monkeypatch):
    update_calls = []
    monkeypatch.setattr(settings_routes, "update_settings", lambda **kwargs: update_calls.append(kwargs))

    request = settings_routes.WebUISettings(host="0.0.0.0", port=9000, debug=True, access_password=None)
    result = asyncio.run(settings_routes.update_webui_settings(request))

    assert result["success"] is True
    assert result["restart_required"] is True
    assert "需重启服务后生效" in result["message"]
    assert update_calls[0]["webui_host"] == "0.0.0.0"
    assert update_calls[0]["webui_port"] == 9000
    assert update_calls[0]["debug"] is True


def test_update_registration_settings_persists_auto_fields(monkeypatch):
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "settings_registration_auto.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        cpa_service = CpaService(
            name="CPA Auto",
            api_url="https://cpa.example.com",
            api_token="token",
            enabled=True,
        )
        session.add(cpa_service)
        email_service = EmailService(
            service_type="tempmail",
            name="Tempmail Auto",
            config={},
            enabled=True,
            priority=0,
        )
        session.add(email_service)
        session.flush()
        cpa_service_id = cpa_service.id
        email_service_id = email_service.id

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    update_calls = []
    state_calls = []
    trigger_calls = []
    reconfigure_calls = []

    monkeypatch.setattr(settings_routes, "get_db", fake_get_db)
    monkeypatch.setattr(settings_routes, "update_settings", lambda **kwargs: update_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "update_auto_registration_state", lambda **kwargs: state_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "trigger_auto_registration_check", lambda: trigger_calls.append(True))
    monkeypatch.setattr(settings_routes, "trigger_account_maintenance_reconfigure", lambda: reconfigure_calls.append(True))

    request = settings_routes.RegistrationSettings(
        max_retries=4,
        timeout=180,
        default_password_length=16,
        sleep_min=7,
        sleep_max=15,
        entry_flow="abcard",
        auto_enabled=True,
        auto_check_interval=120,
        auto_min_ready_auth_files=5,
        auto_email_service_type="tempmail",
        auto_email_service_id=email_service_id,
        auto_proxy=" http://proxy.local:8080 ",
        auto_interval_min=9,
        auto_interval_max=21,
        auto_concurrency=3,
        auto_mode="pipeline",
        auto_cpa_service_id=cpa_service_id,
        maintenance_enabled=True,
        maintenance_schedule_time="05:45",
        maintenance_validation_proxy=" http://maintenance.proxy:8080 ",
        maintenance_cleanup_local=True,
        maintenance_cleanup_remote_cpa=True,
        maintenance_cpa_service_id=cpa_service_id,
    )

    result = asyncio.run(settings_routes.update_registration_settings(request))

    assert result["success"] is True
    assert len(update_calls) == 1
    payload = update_calls[0]
    assert payload["registration_entry_flow"] == "abcard"
    assert payload["registration_auto_enabled"] is True
    assert payload["registration_auto_check_interval"] == 120
    assert payload["registration_auto_min_ready_auth_files"] == 5
    assert payload["registration_auto_email_service_type"] == "tempmail"
    assert payload["registration_auto_email_service_id"] == email_service_id
    assert payload["registration_auto_proxy"] == "http://proxy.local:8080"
    assert payload["registration_auto_interval_min"] == 9
    assert payload["registration_auto_interval_max"] == 21
    assert payload["registration_auto_concurrency"] == 3
    assert payload["registration_auto_mode"] == "pipeline"
    assert payload["registration_auto_cpa_service_id"] == cpa_service_id
    assert payload["account_maintenance_enabled"] is True
    assert payload["account_maintenance_schedule_time"] == "05:45"
    assert payload["account_maintenance_validation_proxy"] == "http://maintenance.proxy:8080"
    assert payload["account_maintenance_cleanup_local"] is True
    assert payload["account_maintenance_cleanup_remote_cpa"] is True
    assert payload["account_maintenance_cpa_service_id"] == cpa_service_id
    assert state_calls[-1]["status"] == "checking"
    assert trigger_calls == [True]
    assert reconfigure_calls == [True]


def test_run_account_maintenance_now_rejects_when_disabled(monkeypatch):
    class DisabledSettings(DummySettings):
        account_maintenance_enabled = False

    monkeypatch.setattr(settings_routes, "get_settings", lambda: DisabledSettings())

    try:
        asyncio.run(settings_routes.run_account_maintenance_now())
    except settings_routes.HTTPException as exc:
        assert exc.status_code == 400
        assert "未启用" in exc.detail
    else:
        raise AssertionError("expected HTTPException when maintenance disabled")


def test_run_account_maintenance_now_rejects_when_coordinator_missing(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(settings_routes, "trigger_account_maintenance_run", lambda: False)

    try:
        asyncio.run(settings_routes.run_account_maintenance_now())
    except settings_routes.HTTPException as exc:
        assert exc.status_code == 503
        assert "未就绪" in exc.detail
    else:
        raise AssertionError("expected HTTPException when coordinator missing")


def test_update_registration_settings_rejects_missing_cpa_when_enabled():
    request = settings_routes.RegistrationSettings(
        auto_enabled=True,
        auto_cpa_service_id=0,
    )

    try:
        asyncio.run(settings_routes.update_registration_settings(request))
    except settings_routes.HTTPException as exc:
        assert exc.status_code == 400
        assert "必须选择一个 CPA 服务" in exc.detail
    else:
        raise AssertionError("expected HTTPException for missing CPA service")
