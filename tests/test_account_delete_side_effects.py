from contextlib import contextmanager
from pathlib import Path

from src.database.models import Account, Base, BindCardTask, Setting
from src.database.session import DatabaseSessionManager
from src.web.routes import accounts as accounts_routes


def test_clear_current_account_selection_removes_snapshot(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    data_dir = runtime_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot = data_dir / "current_codex_account.json"
    snapshot.write_text('{"id": 1}', encoding="utf-8")

    db_path = runtime_dir / "accounts.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        session.add(Setting(key=accounts_routes.CURRENT_ACCOUNT_SETTING_KEY, value="1", category="accounts"))

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.chdir(runtime_dir)

    with fake_get_db() as db:
        cleared = accounts_routes.clear_current_account_selection_if_matches(db, 1)
        assert cleared is True

    with fake_get_db() as db:
        setting = db.query(Setting).filter(Setting.key == accounts_routes.CURRENT_ACCOUNT_SETTING_KEY).first()
        assert setting is not None
        assert setting.value == ""

    assert not snapshot.exists()


def test_delete_account_preserves_bind_card_history(tmp_path):
    db_path = tmp_path / "delete_side_effects.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        account = Account(email="tester@example.com", email_service="tempmail", status="active")
        session.add(account)
        session.flush()
        task = BindCardTask(
            account_id=account.id,
            plan_type="plus",
            checkout_url="https://chatgpt.com/checkout/openai_llc/test",
            status="verifying",
            last_error="old error",
        )
        session.add(task)
        session.flush()
        account_id = account.id
        task_id = task.id

    with manager.session_scope() as session:
        from src.database import crud

        assert crud.delete_account(session, account_id) is True

    with manager.session_scope() as session:
        task = session.query(BindCardTask).filter(BindCardTask.id == task_id).first()
        assert task is not None
        assert task.account_id is None
        assert task.status == "account_removed"
        assert "关联账号已被自动清理" in (task.last_error or "")
        assert task.account_email_snapshot == "tester@example.com"


def test_delete_account_clears_current_account_setting_at_crud_layer(tmp_path):
    db_path = tmp_path / "delete_current_account.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        account = Account(email="selected@example.com", email_service="tempmail", status="active")
        session.add(account)
        session.flush()
        session.add(Setting(key=accounts_routes.CURRENT_ACCOUNT_SETTING_KEY, value=str(account.id), category="accounts"))
        account_id = account.id

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot = data_dir / "current_codex_account.json"
    snapshot.write_text('{"id": 1}', encoding="utf-8")

    original_cwd = Path.cwd()
    try:
        import os
        os.chdir(tmp_path)
        with manager.session_scope() as session:
            from src.database import crud

            assert crud.delete_account(session, account_id) is True
    finally:
        import os
        os.chdir(original_cwd)

    with manager.session_scope() as session:
        setting = session.query(Setting).filter(Setting.key == accounts_routes.CURRENT_ACCOUNT_SETTING_KEY).first()
        assert setting is not None
        assert setting.value == ""
    assert not snapshot.exists()


def test_delete_accounts_batch_clears_current_account_setting(tmp_path):
    db_path = tmp_path / "delete_batch_current_account.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        account_a = Account(email="a@example.com", email_service="tempmail", status="active")
        account_b = Account(email="b@example.com", email_service="tempmail", status="active")
        session.add_all([account_a, account_b])
        session.flush()
        session.add(Setting(key=accounts_routes.CURRENT_ACCOUNT_SETTING_KEY, value=str(account_b.id), category="accounts"))
        ids = [account_a.id, account_b.id]

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot = data_dir / "current_codex_account.json"
    snapshot.write_text('{"id": 2}', encoding="utf-8")

    original_cwd = Path.cwd()
    try:
        import os
        os.chdir(tmp_path)
        with manager.session_scope() as session:
            from src.database import crud

            assert crud.delete_accounts_batch(session, ids) == 2
    finally:
        import os
        os.chdir(original_cwd)

    with manager.session_scope() as session:
        setting = session.query(Setting).filter(Setting.key == accounts_routes.CURRENT_ACCOUNT_SETTING_KEY).first()
        assert setting is not None
        assert setting.value == ""
    assert not snapshot.exists()
