from src.config.settings import Settings
from src.core.auto_registration import AutoRegistrationPlan
from src.web.routes import registration


def test_settings_exposes_auto_registration_fields():
    settings = Settings()

    assert settings.registration_auto_enabled is False
    assert settings.registration_auto_check_interval == 60
    assert settings.registration_auto_email_service_type == "tempmail"
    assert settings.registration_auto_mode == "pipeline"


def test_run_auto_registration_batch_exists():
    assert callable(registration.run_auto_registration_batch)


def test_run_auto_registration_batch_rejects_invalid_email_type():
    plan = AutoRegistrationPlan(
        deficit=1,
        ready_count=0,
        min_ready_auth_files=1,
        cpa_service_id=123,
    )
    settings = Settings(registration_auto_email_service_type="invalid-service")

    try:
        import asyncio

        asyncio.run(registration.run_auto_registration_batch(plan, settings))
    except ValueError as exc:
        assert "邮箱服务类型无效" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid email service type")
