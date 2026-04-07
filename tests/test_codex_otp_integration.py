import pytest

from src.core.codex_otp_provisioner import (
    CodexOtpProvisionError,
    WORKER_TEMPLATE_VERSION,
    build_public_base_url,
    provision_codex_otp,
    provision_codex_otp_idempotent,
)
from src.services.codex_otp_mail import CodexOtpMailService


class DummyProvisioner:
    def __init__(self, account_id: str, api_token: str, zone_id: str = ""):
        self.account_id = account_id
        self.api_token = api_token
        self.zone_id = zone_id
        self.calls = []

    def create_d1_database(self, name: str, location_hint: str = ""):
        self.calls.append(("create_d1_database", name, location_hint))
        return {"uuid": "db-123", "name": name}

    def execute_sql(self, database_id: str, sql: str) -> None:
        self.calls.append(("execute_sql", database_id, sql))

    def deploy_worker(self, **kwargs):
        self.calls.append(("deploy_worker", kwargs))
        return {"id": kwargs["script_name"]}

    def create_route(self, route_pattern: str, script_name: str):
        self.calls.append(("create_route", route_pattern, script_name))
        return {"pattern": route_pattern, "script": script_name}

    def list_d1_databases(self, name: str = ""):
        return []

    def list_worker_scripts(self):
        return []

    def list_routes(self):
        return []


class DummyProvisionerNoRoute(DummyProvisioner):
    def create_route(self, route_pattern: str, script_name: str):
        self.calls.append(("create_route", route_pattern, script_name))
        return None


class DummyProvisionerError(DummyProvisioner):
    def create_d1_database(self, name: str, location_hint: str = ""):
        raise CodexOtpProvisionError("Cloudflare API 请求失败: HTTP 400 - token secret-value-exposed")


class DummyProvisionerBadResult(DummyProvisioner):
    def create_d1_database(self, name: str, location_hint: str = ""):
        return {"name": name}


class DummyProvisionerConflicts(DummyProvisioner):
    def list_d1_databases(self, name: str = ""):
        return [{"uuid": "db-existing", "name": name or "codex-otp-db"}]

    def list_worker_scripts(self):
        return [{"id": "codex-otp-main"}]

    def list_routes(self):
        return [{"id": "route-1", "pattern": "otp.example.com/*", "script": "other-worker"}]


class DummyProvisionerCreateConflictThenReuse(DummyProvisioner):
    def create_d1_database(self, name: str, location_hint: str = ""):
        raise CodexOtpProvisionError("Cloudflare API 请求失败: HTTP 400 - [{'code': 7502, 'message': \"Database with name already exists\"}]")

    def list_d1_databases(self, name: str = ""):
        return [{"uuid": "db-reused", "name": name or "codex-otp-db"}]


def test_build_public_base_url_trims_wildcard():
    assert build_public_base_url("otp.example.com/*") == "https://otp.example.com"


def test_provision_codex_otp_returns_runtime_config(monkeypatch):
    dummy = DummyProvisioner("acc", "tok", "zone")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp(
        account_id="acc",
        api_token="tok",
        zone_id="zone",
        script_name="codex-otp-main",
        database_name="codex-otp-db",
        route_pattern="otp.example.com/*",
        email_domain="mail.example.com",
        service_name="Codex OTP Main",
        custom_auth="extra-secret",
        admin_token="admin-secret",
        ttl_seconds=2400,
        code_retention_days=3,
        location_hint="weur",
    )

    assert result.service_config["base_url"] == "https://otp.example.com"
    assert result.service_config["admin_token"] == "admin-secret"
    assert result.service_config["custom_auth"] == "extra-secret"
    assert result.service_config["domain"] == "mail.example.com"
    assert result.cloudflare["database_id"] == "db-123"
    assert result.cloudflare["worker_id"] == "codex-otp-main"
    assert result.cloudflare["template_version"] == WORKER_TEMPLATE_VERSION
    assert result.steps["d1"]["status"] in {"created", "reused"}
    assert result.steps["worker"]["status"] in {"created", "updated"}
    assert result.next_steps


def test_codex_otp_mail_service_create_email_uses_stage_service_id(monkeypatch):
    service = CodexOtpMailService(
        {
            "base_url": "https://otp.example.com",
            "admin_token": "secret",
            "domain": "mail.example.com",
        }
    )

    monkeypatch.setattr(
        service,
        "_request",
        lambda method, path, **kwargs: {
            "email": "test@mail.example.com",
            "created_at": "2026-03-28T00:00:00Z",
            "expires_at": "2026-03-28T00:30:00Z",
            "domain": "mail.example.com",
        },
    )

    email_info = service.create_email({"stage": "login"})

    assert email_info["email"] == "test@mail.example.com"
    assert email_info["service_id"] == "test@mail.example.com:login"


def test_codex_otp_mail_service_marks_consumed_when_code_found(monkeypatch):
    service = CodexOtpMailService(
        {
            "base_url": "https://otp.example.com",
            "admin_token": "secret",
            "domain": "mail.example.com",
            "poll_interval": 1,
        }
    )

    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/admin/v1/code/latest":
            return {"found": True, "id": 11, "code": "123456"}
        if path == "/admin/v1/code/consume":
            return {"success": True}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(service, "_request", fake_request)

    code = service.get_verification_code("test@mail.example.com", email_id="test@mail.example.com:login", timeout=2)

    assert code == "123456"
    assert any(path == "/admin/v1/code/consume" for _, path, _ in calls)


def test_provision_codex_otp_handles_missing_route(monkeypatch):
    dummy = DummyProvisionerNoRoute("acc", "tok", "")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp(
        account_id="acc",
        api_token="tok",
        zone_id="zone-1",
        script_name="codex-otp-main",
        database_name="codex-otp-db",
        route_pattern="otp.example.com/*",
        email_domain="mail.example.com",
        service_name="Codex OTP Main",
    )

    assert result.cloudflare["route"] is None
    assert any("确认 Workers Route" in step for step in result.next_steps)


def test_provision_codex_otp_idempotent_sanitizes_error(monkeypatch):
    dummy = DummyProvisionerError("acc", "tok", "")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    with pytest.raises(CodexOtpProvisionError) as exc_info:
        provision_codex_otp_idempotent(
            account_id="acc",
            api_token="tok",
            zone_id="zone-1",
            script_name="codex-otp-main",
            database_name="codex-otp-db",
            route_pattern="otp.example.com/*",
            email_domain="mail.example.com",
            service_name="Codex OTP Main",
        )

    assert "Cloudflare API 请求失败" in str(exc_info.value)
    assert len(str(exc_info.value)) <= 240


def test_provision_codex_otp_idempotent_normalizes_inputs(monkeypatch):
    dummy = DummyProvisioner("acc", "tok", "zone")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp_idempotent(
        account_id=" acc ",
        api_token=" tok ",
        zone_id=" zone ",
        script_name=" codex-otp-main ",
        database_name=" codex-otp-db ",
        route_pattern=" otp.example.com/*/ ",
        email_domain=" MAIL.EXAMPLE.COM ",
        service_name=" Codex OTP Main ",
        custom_auth=" extra-secret ",
        admin_token=" admin-secret ",
        ttl_seconds="2400",
        code_retention_days="3",
    )

    assert result.service_config["base_url"] == "https://otp.example.com"
    assert result.service_config["domain"] == "mail.example.com"
    assert result.service_config["ttl_seconds"] == 2400


def test_provision_codex_otp_rejects_bad_d1_result(monkeypatch):
    dummy = DummyProvisionerBadResult("acc", "tok", "zone")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    with pytest.raises(CodexOtpProvisionError) as exc_info:
        provision_codex_otp(
            account_id="acc",
            api_token="tok",
            zone_id="zone",
            script_name="codex-otp-main",
            database_name="codex-otp-db",
            route_pattern="otp.example.com/*",
            email_domain="mail.example.com",
            service_name="Codex OTP Main",
        )

    assert "D1 返回结果异常" in str(exc_info.value)


def test_provision_codex_otp_requires_zone_id_when_route_is_set(monkeypatch):
    dummy = DummyProvisioner("acc", "tok", "")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    with pytest.raises(CodexOtpProvisionError) as exc_info:
        provision_codex_otp(
            account_id="acc",
            api_token="tok",
            zone_id="",
            script_name="codex-otp-main",
            database_name="codex-otp-db",
            route_pattern="otp.example.com/*",
            email_domain="mail.example.com",
            service_name="Codex OTP Main",
        )

    assert "必须同时填写 Zone ID" in str(exc_info.value)


def test_provision_codex_otp_allows_manual_route_mode_without_route_pattern(monkeypatch):
    dummy = DummyProvisioner("acc", "tok", "")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp(
        account_id="acc",
        api_token="tok",
        zone_id="",
        script_name="codex-otp-main",
        database_name="codex-otp-db",
        route_pattern="",
        email_domain="mail.example.com",
        service_name="Codex OTP Main",
    )

    assert result.steps["route"]["status"] == "skipped"
    assert "未填写 HTTP Route" in result.steps["route"]["message"]


def test_provision_codex_otp_blocks_conflicts_by_default(monkeypatch):
    dummy = DummyProvisionerConflicts("acc", "tok", "zone")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    with pytest.raises(CodexOtpProvisionError) as exc_info:
        provision_codex_otp(
            account_id="acc",
            api_token="tok",
            zone_id="zone",
            script_name="codex-otp-main",
            database_name="codex-otp-db",
            route_pattern="otp.example.com/*",
            email_domain="mail.example.com",
            service_name="Codex OTP Main",
        )

    message = str(exc_info.value)
    assert "资源冲突" in message
    assert "D1 名称已存在" in message
    assert "Worker Script 已存在" in message
    assert "Route 已存在" in message


def test_provision_codex_otp_allows_override_and_reuses_database(monkeypatch):
    dummy = DummyProvisionerConflicts("acc", "tok", "zone")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp(
        account_id="acc",
        api_token="tok",
        zone_id="zone",
        script_name="codex-otp-main",
        database_name="codex-otp-db",
        route_pattern="otp.example.com/*",
        email_domain="mail.example.com",
        service_name="Codex OTP Main",
        allow_override=True,
    )

    assert result.cloudflare["database_id"] == "db-existing"
    assert result.cloudflare["allow_override"] is True
    assert not any(call[0] == "create_d1_database" for call in dummy.calls)


def test_provision_codex_otp_reuses_existing_database_after_create_conflict(monkeypatch):
    dummy = DummyProvisionerCreateConflictThenReuse("acc", "tok", "zone")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp(
        account_id="acc",
        api_token="tok",
        zone_id="zone",
        script_name="codex-otp-main",
        database_name="codex-otp-db",
        route_pattern="otp.example.com/*",
        email_domain="mail.example.com",
        service_name="Codex OTP Main",
        allow_override=True,
    )

    assert result.cloudflare["database_id"] == "db-reused"
