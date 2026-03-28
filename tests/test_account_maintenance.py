import asyncio
from datetime import datetime, timedelta, timezone

from src.core.account_maintenance import AccountMaintenanceCancelled, AccountMaintenanceCoordinator, run_account_maintenance_once


class DummySettings:
    account_maintenance_enabled = True
    account_maintenance_validation_proxy = "http://proxy.example:7890"
    account_maintenance_cleanup_local = True
    account_maintenance_cleanup_remote_cpa = True
    account_maintenance_cpa_service_id = 1


class DummyAccount:
    def __init__(self, account_id, email):
        self.id = account_id
        self.email = email


class DummyCpaService:
    api_url = "https://cpa.example.com"
    api_token = "token-123"
    proxy_url = "http://cpa-proxy.example:7890"
    enabled = True


def _patch_single_pass_accounts(monkeypatch, accounts):
    monkeypatch.setattr(
        "src.core.account_maintenance._iter_all_accounts",
        lambda page_size=1000: iter(accounts),
    )
    account_map = {account.id: account for account in accounts}
    monkeypatch.setattr(
        "src.core.account_maintenance._reload_account_for_action",
        lambda account_id: account_map.get(account_id),
    )


def test_remote_cleanup_failure_blocks_local_delete(monkeypatch):
    deleted = []
    updated = []
    logs = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance.do_validate", lambda account_id, proxy: (False, "Token 无效或已过期"))
    monkeypatch.setattr("src.core.account_maintenance.delete_cpa_auth_file", lambda *args, **kwargs: (False, "remote delete failed"))
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.crud.get_cpa_service_by_id", lambda db, service_id: DummyCpaService())
    _patch_single_pass_accounts(monkeypatch, [DummyAccount(1, "tester@example.com")])
    monkeypatch.setattr("src.core.account_maintenance.crud.delete_account", lambda db, account_id: deleted.append(account_id) or True)
    monkeypatch.setattr("src.core.account_maintenance.crud.update_account", lambda db, account_id, **kwargs: updated.append((account_id, kwargs)) or True)

    result = run_account_maintenance_once(DummySettings())

    assert result.invalid_count == 1
    assert result.local_deleted_count == 0
    assert result.remote_deleted_count == 0
    assert deleted == []
    assert updated == []
    assert any("跳过本地删除" in message for message in logs)


def test_missing_remote_service_blocks_local_delete(monkeypatch):
    deleted = []
    logs = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance.do_validate", lambda account_id, proxy: (False, "Token 无效或已过期"))
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.crud.get_cpa_service_by_id", lambda db, service_id: None)
    _patch_single_pass_accounts(monkeypatch, [DummyAccount(1, "tester@example.com")])
    monkeypatch.setattr("src.core.account_maintenance.crud.delete_account", lambda db, account_id: deleted.append(account_id) or True)

    result = run_account_maintenance_once(DummySettings())

    assert result.local_deleted_count == 0
    assert deleted == []
    assert any("CPA 服务 1 不存在、已禁用或已被删除" in error for error in result.errors)
    assert any("跳过本地删除" in message for message in logs)


def test_disabled_remote_service_blocks_local_delete(monkeypatch):
    deleted = []
    logs = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    class DisabledCpaService:
        enabled = False
        api_url = "https://cpa.example.com"
        api_token = "token-123"
        proxy_url = "http://cpa-proxy.example:7890"

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance.do_validate", lambda account_id, proxy: (False, "Token 无效或已过期"))
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.crud.get_cpa_service_by_id", lambda db, service_id: DisabledCpaService())
    _patch_single_pass_accounts(monkeypatch, [DummyAccount(1, "tester@example.com")])
    monkeypatch.setattr("src.core.account_maintenance.crud.delete_account", lambda db, account_id: deleted.append(account_id) or True)

    result = run_account_maintenance_once(DummySettings())

    assert result.local_deleted_count == 0
    assert deleted == []
    assert any("CPA 服务 1 不存在、已禁用或已被删除" in error for error in result.errors)
    assert any("跳过本地删除" in message for message in logs)


def test_missing_email_blocks_local_delete_when_remote_cleanup_enabled(monkeypatch):
    deleted = []
    logs = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance.do_validate", lambda account_id, proxy: (False, "Token 无效或已过期"))
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.crud.get_cpa_service_by_id", lambda db, service_id: DummyCpaService())
    _patch_single_pass_accounts(monkeypatch, [DummyAccount(1, "")])
    monkeypatch.setattr("src.core.account_maintenance.crud.delete_account", lambda db, account_id: deleted.append(account_id) or True)

    result = run_account_maintenance_once(DummySettings())

    assert result.local_deleted_count == 0
    assert deleted == []
    assert any("account_id=1" in error and "auth-file" in error for error in result.errors)
    assert any("跳过本地删除" in message for message in logs)


def test_validation_exception_does_not_abort_whole_pass(monkeypatch):
    logs = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    accounts = [DummyAccount(1, "boom@example.com"), DummyAccount(2, "ok@example.com")]

    def fake_validate(account_id, proxy):
        if account_id == 1:
            raise RuntimeError("network error")
        return True, None

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance.do_validate", fake_validate)
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.crud.get_cpa_service_by_id", lambda db, service_id: DummyCpaService())
    _patch_single_pass_accounts(monkeypatch, accounts)

    result = run_account_maintenance_once(DummySettings())

    assert result.valid_count == 1
    assert any("验证异常 boom@example.com" in error for error in result.errors)
    assert any("验证异常: boom@example.com" in message for message in logs)


def test_coordinator_survives_invalid_schedule(monkeypatch):
    logs = []
    states = []

    class BadScheduleSettings:
        account_maintenance_enabled = True
        account_maintenance_schedule_time = "99:99"

    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.update_account_maintenance_state", lambda **kwargs: states.append(kwargs) or kwargs)
    monkeypatch.setattr("src.core.account_maintenance._catchup_due_run", lambda settings: False)

    coordinator = AccountMaintenanceCoordinator(settings_getter=lambda: BadScheduleSettings())

    async def runner():
        coordinator.start()
        await asyncio.sleep(0.05)
        await coordinator.stop()

    asyncio.run(runner())

    assert any(state.get("status") == "error" for state in states)
    assert any("计划时间无效" in message for message in logs)


def test_cancel_signal_stops_current_pass_before_cleanup(monkeypatch):
    logs = []
    deleted = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    class CancelCoordinator:
        def is_cancellation_requested(self):
            return True

    monkeypatch.setattr("src.core.account_maintenance._coordinator_instance", CancelCoordinator())
    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))
    monkeypatch.setattr("src.core.account_maintenance.crud.get_cpa_service_by_id", lambda db, service_id: DummyCpaService())
    _patch_single_pass_accounts(monkeypatch, [DummyAccount(1, "tester@example.com")])
    monkeypatch.setattr("src.core.account_maintenance.crud.delete_account", lambda db, account_id: deleted.append(account_id) or True)

    try:
        try:
            run_account_maintenance_once(DummySettings())
        except AccountMaintenanceCancelled:
            pass
    finally:
        monkeypatch.setattr("src.core.account_maintenance._coordinator_instance", None)

    assert deleted == []
    assert any("提前结束本轮执行" in message for message in logs)


def test_reconfigure_during_run_stops_current_pass(monkeypatch):
    logs = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    snapshots = [
        {
            "validation_proxy": "http://proxy.example:7890",
            "cleanup_local": True,
            "cleanup_remote": True,
            "cpa_service_id": 1,
            "remote_service_id": None,
            "remote_proxy": "http://cpa-proxy.example:7890",
            "enabled": True,
        },
        {
            "validation_proxy": None,
            "cleanup_local": False,
            "cleanup_remote": False,
            "cpa_service_id": 0,
            "remote_service_id": None,
            "remote_proxy": None,
            "enabled": True,
        },
    ]

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance._iter_all_accounts", lambda page_size=1000: iter([DummyAccount(1, "tester@example.com")]))
    monkeypatch.setattr("src.core.account_maintenance._current_maintenance_settings", lambda: snapshots.pop(0) if snapshots else snapshots[-1])
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: logs.append(message))

    try:
        run_account_maintenance_once(DummySettings())
    except AccountMaintenanceCancelled as exc:
        assert "配置已变更" in str(exc)
    else:
        raise AssertionError("expected AccountMaintenanceCancelled on reconfigure")

    assert any("配置已变更" in message for message in logs)


def test_account_maintenance_updates_batch_status(monkeypatch):
    batch_updates = []

    class DummyDb:
        pass

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())
    monkeypatch.setattr("src.core.account_maintenance.get_settings", lambda: DummySettings())
    monkeypatch.setattr("src.core.account_maintenance._current_maintenance_settings", lambda: {
        "validation_proxy": None,
        "cleanup_local": False,
        "cleanup_remote": False,
        "cpa_service_id": 0,
        "remote_service_id": None,
        "remote_proxy": None,
        "enabled": True,
    })
    monkeypatch.setattr("src.core.account_maintenance._iter_all_accounts", lambda page_size=1000: iter([DummyAccount(1, "ok@example.com")]))
    monkeypatch.setattr("src.core.account_maintenance._reload_account_for_action", lambda account_id: DummyAccount(account_id, "ok@example.com"))
    monkeypatch.setattr("src.core.account_maintenance.do_validate", lambda account_id, proxy: (True, None))
    monkeypatch.setattr("src.core.account_maintenance.add_account_maintenance_log", lambda message: None)
    monkeypatch.setattr(
        "src.core.account_maintenance._publish_account_maintenance_status",
        lambda **kwargs: batch_updates.append(kwargs),
    )

    result = run_account_maintenance_once(DummySettings())

    assert result.valid_count == 1
    assert any(update.get("completed") == 1 for update in batch_updates)


def test_iter_all_accounts_survives_deletions_without_skipping(monkeypatch):
    class DummyAccountWithId:
        def __init__(self, account_id):
            self.id = account_id
            self.email = f"user{account_id}@example.com"

    data = [DummyAccountWithId(1), DummyAccountWithId(2), DummyAccountWithId(3)]

    class DummyQuery:
        def __init__(self, rows):
            self.rows = rows
            self._last_seen_id = None
            self._limit = None

        def order_by(self, *_args, **_kwargs):
            return self

        def filter(self, criterion):
            value = criterion.right.value
            self._last_seen_id = value
            return self

        def limit(self, value):
            self._limit = value
            return self

        def all(self):
            rows = [row for row in self.rows if self._last_seen_id is None or row.id > self._last_seen_id]
            return rows[:self._limit]

    class DummyDb:
        def query(self, _model):
            return DummyQuery(data)

    class DummyContext:
        def __enter__(self):
            return DummyDb()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("src.core.account_maintenance.get_db", lambda: DummyContext())

    from src.core.account_maintenance import _iter_all_accounts

    seen_ids = []
    for account in _iter_all_accounts(page_size=2):
        seen_ids.append(account.id)
        if account.id == 1:
            data.pop(0)

    assert seen_ids == [1, 2, 3]


def test_catchup_due_run_detects_missed_schedule():
    from src.core.account_maintenance import _catchup_due_run

    class CatchupSettings(DummySettings):
        account_maintenance_schedule_time = "03:00"

    state = {"last_run_at": "2026-03-27T00:00:00+00:00"}

    now = datetime(2026, 3, 28, 4, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    import src.core.account_maintenance as maintenance_module
    original = maintenance_module.get_persisted_account_maintenance_state
    maintenance_module.get_persisted_account_maintenance_state = lambda: state
    try:
        assert _catchup_due_run(CatchupSettings(), now=now.astimezone(timezone.utc)) is True
    finally:
        maintenance_module.get_persisted_account_maintenance_state = original
