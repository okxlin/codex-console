from src.core.codex_otp_provisioner import D1_WORKER_TEMPLATE_VERSION, provision_codex_otp_d1


class DummyProvisionerD1:
    def __init__(self, account_id: str, api_token: str, zone_id: str = ""):
        self.account_id = account_id
        self.api_token = api_token
        self.zone_id = zone_id
        self.calls = []

    def list_d1_databases(self, name: str = ""):
        return []

    def list_worker_scripts(self):
        return []

    def list_routes(self):
        return []

    def create_d1_database(self, name: str, location_hint: str = ""):
        self.calls.append(("create_d1_database", name, location_hint))
        return {"uuid": "db-d1", "name": name}

    def execute_sql(self, database_id: str, sql: str):
        self.calls.append(("execute_sql", database_id, sql))

    def deploy_email_only_worker(self, script_name: str, database_id: str):
        self.calls.append(("deploy_email_only_worker", script_name, database_id))
        return {"id": script_name}


def test_provision_codex_otp_d1_returns_expected_steps(monkeypatch):
    dummy = DummyProvisionerD1("acc", "tok", "")
    monkeypatch.setattr(
        "src.core.codex_otp_provisioner.CloudflareProvisioner",
        lambda account_id, api_token, zone_id="": dummy,
    )

    result = provision_codex_otp_d1(
        account_id="acc",
        api_token="tok",
        script_name="codex-otp-d1-main",
        database_name="codex-otp-d1-db",
        email_domain="mail.example.com",
        service_name="Codex OTP D1 Main",
        allow_override=False,
    )

    assert result.cloudflare["database_id"] == "db-d1"
    assert result.cloudflare["template_version"] == D1_WORKER_TEMPLATE_VERSION
    assert result.steps["route"]["status"] == "skipped"
    assert "运行期 D1 只读 Token" in " ".join(result.next_steps)
    execute_calls = [call for call in dummy.calls if call[0] == "execute_sql"]
    assert execute_calls
    assert "mail_events" in execute_calls[0][2]
