import asyncio
from datetime import timedelta
from contextlib import contextmanager
from pathlib import Path

from src.config.settings import Settings
from src.core.timezone_utils import utcnow_naive
from src.database.models import Account, Base, CpaService, EmailService
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
    registration_refresh_backfill_enabled = True
    registration_playwright_failure_screenshot_enabled = True
    registration_playwright_headed = True
    registration_playwright_artifact_retention_days = 7
    registration_playwright_artifact_max_total_size_mb = 512
    registration_playwright_artifact_max_total_files = 500
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
    account_maintenance_schedule_mode = "cron"
    account_maintenance_schedule_time = "04:30"
    account_maintenance_schedule_cron = "*/15 * * * *"
    account_maintenance_validation_proxy = "http://127.0.0.1:7899"
    account_maintenance_validation_interval_minutes = 240
    account_maintenance_debug_enabled = True
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
    assert result["refresh_backfill_enabled"] is True
    assert result["playwright_failure_screenshot_enabled"] is True
    assert result["playwright_headed"] is True
    assert result["playwright_artifact_retention_days"] == 7
    assert result["playwright_artifact_max_total_size_mb"] == 512
    assert result["playwright_artifact_max_total_files"] == 500
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
    assert result["maintenance_schedule_mode"] == "cron"
    assert result["maintenance_schedule_cron"] == "*/15 * * * *"
    assert result["maintenance_validation_proxy"] == "http://127.0.0.1:7899"
    assert result["maintenance_validation_interval_minutes"] == 240
    assert result["maintenance_debug_enabled"] is True
    assert result["maintenance_cleanup_local"] is True
    assert result["maintenance_cleanup_remote_cpa"] is True
    assert result["maintenance_cpa_service_id"] == 11
    assert "maintenance_state" in result
    assert result["maintenance_logs"] == ["[账号维护] 测试日志"]


def test_get_registration_settings_supports_playwright_entry_flow(monkeypatch):
    class PlaywrightSettings(DummySettings):
        registration_entry_flow = "playwright"

    monkeypatch.setattr(settings_routes, "get_settings", lambda: PlaywrightSettings())
    monkeypatch.setattr(settings_routes, "get_account_maintenance_state", lambda: get_account_maintenance_state())
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_state", lambda: {"status": "idle"})
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_logs", lambda: [])

    result = asyncio.run(settings_routes.get_registration_settings())

    assert result["entry_flow"] == "playwright"
    assert result["entry_flow_label"] == "Playwright / 浏览器态优先收尾"


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
    monkeypatch.setattr(settings_routes, "update_auto_registration_state", lambda **kwargs: state_calls.append(("auto", kwargs)))
    monkeypatch.setattr(settings_routes, "update_account_maintenance_state", lambda **kwargs: state_calls.append(("maintenance", kwargs)))
    monkeypatch.setattr(settings_routes, "trigger_auto_registration_check", lambda: trigger_calls.append(True))
    monkeypatch.setattr(settings_routes, "trigger_account_maintenance_reconfigure", lambda: reconfigure_calls.append(True))

    request = settings_routes.RegistrationSettings(
        max_retries=4,
        timeout=180,
        default_password_length=16,
        sleep_min=7,
        sleep_max=15,
        entry_flow="abcard",
        refresh_backfill_enabled=True,
        playwright_headed=True,
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
        maintenance_schedule_mode="cron",
        maintenance_schedule_time="05:45",
        maintenance_schedule_cron="*/20 * * * *",
        maintenance_validation_proxy=" http://maintenance.proxy:8080 ",
        maintenance_validation_interval_minutes=180,
        maintenance_debug_enabled=True,
        maintenance_cleanup_local=True,
        maintenance_cleanup_remote_cpa=True,
        maintenance_cpa_service_id=cpa_service_id,
    )

    result = asyncio.run(settings_routes.update_registration_settings(request))

    assert result["success"] is True
    assert len(update_calls) == 1
    payload = update_calls[0]
    assert payload["registration_entry_flow"] == "abcard"
    assert payload["registration_refresh_backfill_enabled"] is True
    assert payload["registration_playwright_headed"] is True
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
    assert payload["account_maintenance_schedule_mode"] == "cron"
    assert payload["account_maintenance_schedule_time"] == "05:45"
    assert payload["account_maintenance_schedule_cron"] == "*/20 * * * *"
    assert payload["account_maintenance_validation_proxy"] == "http://maintenance.proxy:8080"
    assert payload["account_maintenance_validation_interval_minutes"] == 180
    assert payload["account_maintenance_debug_enabled"] is True
    assert payload["account_maintenance_cleanup_local"] is True
    assert payload["account_maintenance_cleanup_remote_cpa"] is True
    assert payload["account_maintenance_cpa_service_id"] == cpa_service_id
    maintenance_state_calls = [payload for kind, payload in state_calls if kind == "maintenance"]
    auto_state_calls = [payload for kind, payload in state_calls if kind == "auto"]
    assert maintenance_state_calls[-1]["status"] == "idle"
    assert maintenance_state_calls[-1]["next_run_at"] is not None
    assert auto_state_calls[-1]["status"] == "checking"
    assert trigger_calls == [True]
    assert reconfigure_calls == [True]


def test_update_registration_settings_accepts_playwright_entry_flow(monkeypatch):
    runtime_dir = Path("tests_runtime")
    runtime_dir.mkdir(exist_ok=True)
    db_path = runtime_dir / "settings_registration_playwright.db"
    if db_path.exists():
        db_path.unlink()

    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        cpa_service = CpaService(
            name="CPA PW",
            api_url="https://cpa.example.com",
            api_token="token",
            enabled=True,
        )
        session.add(cpa_service)
        email_service = EmailService(
            service_type="tempmail",
            name="Tempmail PW",
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
    monkeypatch.setattr(settings_routes, "get_db", fake_get_db)
    monkeypatch.setattr(settings_routes, "update_settings", lambda **kwargs: update_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "update_auto_registration_state", lambda **kwargs: None)
    monkeypatch.setattr(settings_routes, "update_account_maintenance_state", lambda **kwargs: None)
    monkeypatch.setattr(settings_routes, "trigger_auto_registration_check", lambda: None)
    monkeypatch.setattr(settings_routes, "trigger_account_maintenance_reconfigure", lambda: None)

    request = settings_routes.RegistrationSettings(
        max_retries=3,
        timeout=120,
        default_password_length=12,
        sleep_min=5,
        sleep_max=10,
        entry_flow="playwright",
        refresh_backfill_enabled=False,
        playwright_failure_screenshot_enabled=True,
        playwright_artifact_retention_days=14,
        playwright_artifact_max_total_size_mb=768,
        playwright_artifact_max_total_files=600,
        auto_enabled=False,
        auto_check_interval=60,
        auto_min_ready_auth_files=1,
        auto_email_service_type="tempmail",
        auto_email_service_id=email_service_id,
        auto_proxy="",
        auto_interval_min=5,
        auto_interval_max=10,
        auto_concurrency=1,
        auto_mode="pipeline",
        auto_cpa_service_id=cpa_service_id,
        maintenance_enabled=False,
        maintenance_schedule_mode="daily",
        maintenance_schedule_time="03:00",
        maintenance_schedule_cron="0 3 * * *",
        maintenance_validation_proxy="",
        maintenance_validation_interval_minutes=1440,
        maintenance_debug_enabled=False,
        maintenance_cleanup_local=False,
        maintenance_cleanup_remote_cpa=False,
        maintenance_cpa_service_id=0,
    )

    result = asyncio.run(settings_routes.update_registration_settings(request))

    assert result["success"] is True
    assert update_calls[0]["registration_entry_flow"] == "playwright"
    assert update_calls[0]["registration_playwright_failure_screenshot_enabled"] is True
    assert update_calls[0]["registration_playwright_artifact_retention_days"] == 14
    assert update_calls[0]["registration_playwright_artifact_max_total_size_mb"] == 768
    assert update_calls[0]["registration_playwright_artifact_max_total_files"] == 600


def test_debug_account_maintenance_reports_missing_local_columns_and_remote_match(monkeypatch, tmp_path):
    db_path = tmp_path / "settings_debug_maintenance.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        account = Account(email="tester@example.com", email_service="tempmail", status="failed")
        session.add(account)
        cpa_service = CpaService(
            name="CPA Debug",
            api_url="https://cpa.example.com",
            api_token="token-debug",
            enabled=True,
            proxy_url="http://proxy.local:7890",
        )
        session.add(cpa_service)
        session.flush()
        account_id = account.id
        cpa_service_id = cpa_service.id

    with manager.engine.begin() as conn:
        conn.execute(settings_routes.text("ALTER TABLE bind_card_tasks DROP COLUMN account_email_snapshot"))
        conn.execute(settings_routes.text("ALTER TABLE bind_card_tasks DROP COLUMN account_label_snapshot"))

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    class DebugSettings(DummySettings):
        account_maintenance_cpa_service_id = cpa_service_id
        account_maintenance_validation_interval_minutes = 180
        account_maintenance_debug_enabled = True

    monkeypatch.setattr(settings_routes, "get_db", fake_get_db)
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DebugSettings())
    monkeypatch.setattr(
        settings_routes,
        "get_account_maintenance_state",
        lambda: {"status": "running", "message": "processing", "last_run_at": "2026-03-28T10:00:00+00:00", "next_run_at": "2026-03-28T19:00:00+00:00", "last_summary": {"total_accounts": 10}},
    )
    monkeypatch.setattr(
        settings_routes,
        "list_cpa_auth_files",
        lambda api_url, api_token, proxy_url=None: (
            True,
            {"files": [{"name": "tester@example.com.json"}, {"name": "other@example.com.json"}]},
            "ok",
        ),
    )
    monkeypatch.setattr(
        settings_routes,
        "probe_cpaproxyapi_compatibility",
        lambda api_url, api_token, email=None, proxy_url=None: {
            "normalized_auth_files_url": "https://cpa.example.com/v0/management/auth-files",
            "email": email,
            "list_probe": {"ok": True, "message": "ok", "payload_kind": "dict", "file_count": 2, "sample_names": ["tester@example.com.json"], "name_fields_seen": ["name"]},
            "delete_probe": {"filename": "tester@example.com.json", "strategies": [{"strategy": "query_name", "ok": False}, {"strategy": "path_segment", "ok": True}], "recommended_strategy": "path_segment"},
        },
    )

    result = asyncio.run(
        settings_routes.debug_account_maintenance(
            settings_routes.AccountMaintenanceDebugRequest(account_id=account_id)
        )
    )

    assert result["success"] is True
    assert result["account"]["email"] == "tester@example.com"
    assert result["maintenance_settings"]["validation_interval_minutes"] == 180
    assert result["account_runtime_debug"]["maintenance_status"] == "running"
    assert result["account_runtime_debug"]["would_skip_validation_now"] is False
    assert result["local_debug"]["can_delete_locally"] is False
    assert "account_email_snapshot" in result["local_debug"]["missing_columns"]
    assert result["remote_debug"]["list_ok"] is True
    assert result["remote_debug"]["match_filename"] == "tester@example.com.json"
    assert result["remote_debug"]["matched_candidates"] == ["tester@example.com.json"]
    assert result["remote_debug"]["compatibility_probe"]["delete_probe"]["recommended_strategy"] == "path_segment"


def test_debug_account_maintenance_can_skip_remote_probe(monkeypatch):
    class DebugSettings(DummySettings):
        account_maintenance_cleanup_remote_cpa = True
        account_maintenance_debug_enabled = True

    class DummyAccount:
        id = 7
        email = "skip@example.com"
        status = "failed"

    class DummyService:
        id = 3
        enabled = True
        api_url = "https://cpa.example.com"
        api_token = "token"
        proxy_url = None

    class DummyDb:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "COUNT(1) FROM bind_card_tasks" in sql:
                return type("R", (), {"scalar": lambda self: 0})()
            if "pragma_table_info('bind_card_tasks')" in sql:
                return type(
                    "R",
                    (),
                    {"fetchall": lambda self: [(0, "id"), (1, "account_email_snapshot"), (2, "account_label_snapshot")]},
                )()
            if "SELECT COUNT(1) FROM accounts WHERE status IN ('failed', 'expired', 'banned')" in sql:
                return type("R", (), {"scalar": lambda self: 1})()
            if "SELECT COUNT(1) FROM accounts" in sql:
                return type("R", (), {"scalar": lambda self: 1})()
            if "FROM accounts a" in sql:
                return type("R", (), {"fetchall": lambda self: [(7, "skip@example.com", "failed", 0)]})()
            raise AssertionError(f"unexpected sql: {sql}")

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(settings_routes, "get_db", lambda: DummyContext())
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DebugSettings())
    monkeypatch.setattr(settings_routes, "get_account_maintenance_state", lambda: {"status": "idle", "message": "ok"})
    monkeypatch.setattr(settings_routes.crud, "get_account_by_id", lambda db, account_id: DummyAccount())
    monkeypatch.setattr(settings_routes.crud, "get_account_by_email", lambda db, email: None)
    monkeypatch.setattr(settings_routes.crud, "get_cpa_service_by_id", lambda db, service_id: DummyService())


def test_settings_exposes_refresh_backfill_default_flag():
    settings = Settings()

    assert settings.registration_refresh_backfill_enabled is False


def test_get_all_settings_includes_refresh_backfill_flag(monkeypatch):
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(settings_routes, "get_account_maintenance_state", lambda: None)
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_state", lambda: {})
    monkeypatch.setattr(settings_routes, "get_persisted_account_maintenance_logs", lambda: [])

    payload = asyncio.run(settings_routes.get_all_settings())

    assert payload["registration"]["refresh_backfill_enabled"] is True


def test_debug_account_maintenance_reports_validation_skip_window(monkeypatch):
    checked_at = utcnow_naive() - timedelta(minutes=10)

    class DebugSettings(DummySettings):
        account_maintenance_validation_interval_minutes = 60
        account_maintenance_debug_enabled = True

    class DummyAccount:
        id = 9
        email = "recent@example.com"
        status = "expired"
        last_maintenance_checked_at = checked_at

    class DummyDb:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "COUNT(1) FROM bind_card_tasks" in sql:
                return type("R", (), {"scalar": lambda self: 0})()
            if "pragma_table_info('bind_card_tasks')" in sql:
                return type("R", (), {"fetchall": lambda self: [(0, "id"), (1, "account_email_snapshot"), (2, "account_label_snapshot")]})()
            if "SELECT COUNT(1) FROM accounts WHERE status IN ('failed', 'expired', 'banned')" in sql:
                return type("R", (), {"scalar": lambda self: 1})()
            if "SELECT COUNT(1) FROM accounts" in sql:
                return type("R", (), {"scalar": lambda self: 1})()
            if "FROM accounts a" in sql:
                return type("R", (), {"fetchall": lambda self: [(9, "recent@example.com", "expired", 0)]})()
            raise AssertionError(f"unexpected sql: {sql}")

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(settings_routes, "get_db", lambda: DummyContext())
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DebugSettings())
    monkeypatch.setattr(settings_routes, "get_account_maintenance_state", lambda: {"status": "running", "message": "processing"})
    monkeypatch.setattr(settings_routes.crud, "get_account_by_id", lambda db, account_id: DummyAccount())
    monkeypatch.setattr(settings_routes.crud, "get_account_by_email", lambda db, email: None)
    monkeypatch.setattr(settings_routes.crud, "get_cpa_service_by_id", lambda db, service_id: None)

    result = asyncio.run(
        settings_routes.debug_account_maintenance(
            settings_routes.AccountMaintenanceDebugRequest(account_id=9, inspect_remote=False)
        )
    )

    assert result["account_runtime_debug"]["validation_interval_minutes"] == 60
    assert result["account_runtime_debug"]["would_skip_validation_now"] is True
    assert result["account_runtime_debug"]["last_maintenance_checked_at"] == checked_at.isoformat()


def test_debug_account_maintenance_supports_global_mode(monkeypatch):
    class DebugSettings(DummySettings):
        account_maintenance_cleanup_remote_cpa = True
        account_maintenance_cpa_service_id = 5
        account_maintenance_debug_enabled = True

    class DummyService:
        id = 5
        enabled = True
        api_url = "https://cpa.example.com"
        api_token = "token"
        proxy_url = "http://proxy.local:7890"

    class DummyResult:
        def __init__(self, scalar_value=None, rows=None):
            self._scalar_value = scalar_value
            self._rows = rows or []

        def scalar(self):
            return self._scalar_value

        def fetchall(self):
            return self._rows

    class DummyDb:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "pragma_table_info('bind_card_tasks')" in sql:
                return DummyResult(rows=[(0, "id"), (1, "account_email_snapshot"), (2, "account_label_snapshot")])
            if "SELECT COUNT(1) FROM accounts WHERE status IN ('failed', 'expired', 'banned')" in sql:
                return DummyResult(scalar_value=2)
            if "SELECT COUNT(1) FROM accounts" in sql:
                return DummyResult(scalar_value=9)
            if "FROM accounts a" in sql:
                return DummyResult(rows=[(1, "a@example.com", "failed", 1), (2, "b@example.com", "expired", 0)])
            raise AssertionError(f"unexpected sql: {sql}")

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(settings_routes, "get_db", lambda: DummyContext())
    monkeypatch.setattr(settings_routes, "get_settings", lambda: DebugSettings())
    monkeypatch.setattr(settings_routes.crud, "get_account_by_id", lambda db, account_id: None)
    monkeypatch.setattr(settings_routes.crud, "get_account_by_email", lambda db, email: None)
    monkeypatch.setattr(settings_routes.crud, "get_cpa_service_by_id", lambda db, service_id: DummyService())
    monkeypatch.setattr(
        settings_routes,
        "probe_cpaproxyapi_compatibility",
        lambda api_url, api_token, email=None, proxy_url=None: {"delete_probe": {"recommended_strategy": None}},
    )

    result = asyncio.run(
        settings_routes.debug_account_maintenance(
            settings_routes.AccountMaintenanceDebugRequest()
        )
    )

    assert result["success"] is True
    assert result["mode"] == "global"
    assert result["account"] is None
    assert result["global_debug"]["total_accounts"] == 9
    assert result["global_debug"]["invalid_accounts"] == 2
    assert result["global_debug"]["invalid_accounts_sample"][0]["email"] == "a@example.com"
    assert result["remote_debug"]["message"] == "未指定账号，返回全局远端调试信息"


def test_debug_account_maintenance_rejects_when_debug_disabled(monkeypatch):
    class DebugDisabledSettings(DummySettings):
        account_maintenance_debug_enabled = False

    monkeypatch.setattr(settings_routes, "get_settings", lambda: DebugDisabledSettings())
    monkeypatch.setattr(settings_routes, "get_db", lambda: (_ for _ in ()).throw(AssertionError("should not touch db when debug disabled")))

    try:
        asyncio.run(settings_routes.debug_account_maintenance(settings_routes.AccountMaintenanceDebugRequest()))
    except settings_routes.HTTPException as exc:
        assert exc.status_code == 403
        assert "未启用" in str(exc.detail)
    else:
        raise AssertionError("expected HTTPException when maintenance debug is disabled")


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
