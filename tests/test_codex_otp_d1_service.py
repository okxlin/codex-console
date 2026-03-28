from src.services.codex_otp_d1_mail import CodexOtpD1MailService


def test_codex_otp_d1_generates_local_email():
    service = CodexOtpD1MailService(
        {
            "domain": "mail.example.com",
            "cf_account_id": "acc",
            "cf_database_id": "db",
            "cf_runtime_api_token": "token",
        }
    )

    info = service.create_email({"stage": "register"})

    assert info["email"].endswith("@mail.example.com")
    assert info["service_id"].endswith(":register")


def test_codex_otp_d1_reads_latest_code(monkeypatch):
    service = CodexOtpD1MailService(
        {
            "domain": "mail.example.com",
            "cf_account_id": "acc",
            "cf_database_id": "db",
            "cf_runtime_api_token": "token",
            "poll_interval": 1,
        }
    )

    monkeypatch.setattr(
        service.reader,
        "get_latest_code",
        lambda email, stage=None: {"id": 1, "code": "654321", "stage": stage or "register"},
    )

    code = service.get_verification_code("abc@mail.example.com", email_id="abc@mail.example.com:register", timeout=2)
    assert code == "654321"
