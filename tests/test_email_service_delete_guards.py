import asyncio
from contextlib import contextmanager
from pathlib import Path

from src.database.models import Base, EmailService
from src.database.session import DatabaseSessionManager
from src.web.routes import email as email_routes


def test_delete_email_service_clears_auto_registration_dependency(tmp_path, monkeypatch):
    db_path = tmp_path / "email_delete_guard.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        service = EmailService(service_type="tempmail", name="Auto Mail", config={}, enabled=True, priority=0)
        session.add(service)
        session.flush()
        service_id = service.id

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    class DummySettings:
        registration_auto_email_service_id = service_id

    updates = []
    monkeypatch.setattr(email_routes, "get_db", fake_get_db)
    monkeypatch.setattr(email_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(email_routes, "update_settings", lambda **kwargs: updates.append(kwargs))

    result = asyncio.run(email_routes.delete_email_service(service_id))

    assert result["success"] is True
    assert updates == [{"registration_auto_email_service_id": 0}]


def test_batch_delete_outlook_clears_auto_registration_dependency(tmp_path, monkeypatch):
    db_path = tmp_path / "email_batch_delete_guard.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        service = EmailService(service_type="outlook", name="Outlook Auto", config={}, enabled=True, priority=0)
        session.add(service)
        session.flush()
        service_id = service.id

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    class DummySettings:
        registration_auto_email_service_id = service_id

    updates = []
    monkeypatch.setattr(email_routes, "get_db", fake_get_db)
    monkeypatch.setattr(email_routes, "get_settings", lambda: DummySettings())
    monkeypatch.setattr(email_routes, "update_settings", lambda **kwargs: updates.append(kwargs))

    result = asyncio.run(email_routes.batch_delete_outlook([service_id]))

    assert result["success"] is True
    assert result["deleted"] == 1
    assert updates == [{"registration_auto_email_service_id": 0}]
