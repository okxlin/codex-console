from contextlib import contextmanager
from pathlib import Path

from src.database.models import Account, Base, BindCardTask
from src.database.session import DatabaseSessionManager
from src.web.routes import payment as payment_routes


def test_list_bind_card_tasks_keeps_searchable_deleted_history(tmp_path, monkeypatch):
    db_path = tmp_path / "payment_history.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        account = Account(email="tester@example.com", email_service="tempmail", status="active")
        session.add(account)
        session.flush()
        task = BindCardTask(
            account_id=None,
            account_email_snapshot="tester@example.com",
            account_label_snapshot="tester@example.com",
            plan_type="plus",
            checkout_url="https://chatgpt.com/checkout/openai_llc/test",
            status="account_removed",
            last_error="关联账号已被自动清理",
        )
        session.add(task)

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(payment_routes, "get_db", fake_get_db)

    result = payment_routes.list_bind_card_tasks(page=1, page_size=20, status=None, search="tester@example.com")

    assert result["total"] == 1
    assert result["tasks"][0]["account_email"] == "tester@example.com"
    assert result["tasks"][0]["account_missing"] is True
