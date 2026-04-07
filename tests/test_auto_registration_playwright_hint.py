from contextlib import contextmanager

from src.core import auto_registration
from src.database.models import Base, RegistrationTask
from src.database.session import DatabaseSessionManager


def test_build_playwright_alert_hint_uses_unified_stats(tmp_path, monkeypatch):
    db_path = tmp_path / "auto_registration_hint.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        for idx in range(5):
            session.add(RegistrationTask(
                task_uuid=f"task-{idx}",
                status="failed",
                result={
                    "success": False,
                    "metadata": {
                        "playwright_diagnostics": {
                            "stage": "failed",
                            "failure_reason": "access_token_missing",
                            "browser_probe": {"page_state": "challenge_page"},
                        },
                        "playwright_post_failure_strategy": {
                            "should_rotate_proxy": True,
                            "needs_manual_review": True,
                            "next_run_policy": {
                                "prefer_fresh_fingerprint": True,
                                "rotate_proxy_before_retry": True,
                            },
                        },
                    },
                },
            ))

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(auto_registration, "get_db", fake_get_db)

    hint = auto_registration._build_playwright_alert_hint()

    assert hint
