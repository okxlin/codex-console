from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config.constants import AccountStatus
from ..config.settings import Settings, get_settings
from ..core.openai.token_refresh import validate_account_token as do_validate
from ..core.upload.cpa_upload import delete_cpa_auth_file
from ..database import crud
from ..database.models import Account
from ..database.crud import set_setting
from ..database.session import get_db
from ..web.routes.accounts import clear_current_account_selection_if_matches

logger = logging.getLogger(__name__)
ACCOUNT_MAINTENANCE_CHANNEL = "account-maintenance"
_ACCOUNT_MAINTENANCE_STATE_KEY = "account.maintenance.last_state"
_account_maintenance_state = {
    "enabled": False,
    "status": "idle",
    "message": "账号自动维护未启动",
    "schedule_time": None,
    "last_run_at": None,
    "next_run_at": None,
    "last_summary": None,
}
_coordinator_instance = None
_MAINTENANCE_LOGS_SETTING_KEY = "account.maintenance.last_logs"
_MAX_PERSISTED_LOG_LINES = 200
_maintenance_persist_lock = threading.RLock()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_account_maintenance_state(**kwargs) -> dict:
    _account_maintenance_state.update(kwargs)
    state = get_account_maintenance_state()
    _persist_account_maintenance_state(state)
    return state


def get_account_maintenance_state() -> dict:
    state = dict(_account_maintenance_state)
    if state.get("last_run_at") or state.get("next_run_at") or state.get("last_summary"):
        return state
    persisted = get_persisted_account_maintenance_state()
    if persisted:
        state.update({k: v for k, v in persisted.items() if v is not None})
    return state


def register_account_maintenance_coordinator(coordinator: Optional["AccountMaintenanceCoordinator"]) -> None:
    global _coordinator_instance
    _coordinator_instance = coordinator


def trigger_account_maintenance_run() -> bool:
    coordinator = _coordinator_instance
    if coordinator is not None:
        coordinator.request_immediate_run()
        return True
    return False


def trigger_account_maintenance_reconfigure() -> bool:
    coordinator = _coordinator_instance
    if coordinator is not None:
        coordinator.request_reconfigure()
        return True
    return False


def add_account_maintenance_log(message: str) -> None:
    from ..web.task_manager import task_manager

    task_manager.add_batch_log(ACCOUNT_MAINTENANCE_CHANNEL, message)
    _persist_account_maintenance_log(message)


def _persist_account_maintenance_log(message: str) -> None:
    try:
        with _maintenance_persist_lock:
            with get_db() as db:
                current = crud.get_setting(db, _MAINTENANCE_LOGS_SETTING_KEY)
                lines = []
                if current and current.value:
                    lines = [line for line in str(current.value).splitlines() if line.strip()]
                lines.append(message)
                lines = lines[-_MAX_PERSISTED_LOG_LINES:]
                set_setting(
                    db,
                    _MAINTENANCE_LOGS_SETTING_KEY,
                    "\n".join(lines),
                    description="账号自动维护最近日志",
                    category="registration",
                )
    except Exception:
        logger.exception("持久化账号维护日志失败")


def _persist_account_maintenance_state(state: dict) -> None:
    try:
        with _maintenance_persist_lock:
            with get_db() as db:
                set_setting(
                    db,
                    _ACCOUNT_MAINTENANCE_STATE_KEY,
                    json.dumps(state, ensure_ascii=False),
                    description="账号自动维护最近状态",
                    category="registration",
                )
    except Exception:
        logger.exception("持久化账号维护状态失败")


def get_persisted_account_maintenance_state() -> dict:
    try:
        with get_db() as db:
            setting = crud.get_setting(db, _ACCOUNT_MAINTENANCE_STATE_KEY)
            if not setting or not setting.value:
                return {}
            payload = json.loads(str(setting.value))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.exception("读取账号维护持久化状态失败")
        return {}


def _publish_account_maintenance_status(**kwargs) -> None:
    from ..web.task_manager import task_manager

    if not task_manager.get_batch_status(ACCOUNT_MAINTENANCE_CHANNEL):
        task_manager.init_batch(ACCOUNT_MAINTENANCE_CHANNEL, 0)
    task_manager.update_batch_status(ACCOUNT_MAINTENANCE_CHANNEL, **kwargs)


def get_persisted_account_maintenance_logs() -> list[str]:
    try:
        with get_db() as db:
            setting = crud.get_setting(db, _MAINTENANCE_LOGS_SETTING_KEY)
            if not setting or not setting.value:
                return []
            return [line for line in str(setting.value).splitlines() if line.strip()]
    except Exception:
        logger.exception("读取账号维护持久化日志失败")
        return []


def _parse_schedule_time(value: str) -> tuple[int, int]:
    text = str(value or "03:00").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except Exception as exc:
        raise ValueError("计划时间格式必须为 HH:MM") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("计划时间超出范围")
    return hour, minute


def compute_next_run_at(schedule_time: str, now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    hour, minute = _parse_schedule_time(schedule_time)
    local_now = current.astimezone(timezone(timedelta(hours=8)))
    target_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_local <= local_now:
        target_local += timedelta(days=1)
    return target_local.astimezone(timezone.utc)


@dataclass
class AccountMaintenanceResult:
    total_accounts: int
    valid_count: int
    invalid_count: int
    local_deleted_count: int
    remote_deleted_count: int
    errors: list[str]


def _resolve_remote_proxy(settings: Settings, cpa_service_id: int) -> Optional[str]:
    if cpa_service_id <= 0:
        return None
    with get_db() as db:
        service = crud.get_cpa_service_by_id(db, cpa_service_id)
        if not service:
            return None
        return str(getattr(service, "proxy_url", "") or "").strip() or None


def _load_remote_cpa_service(cpa_service_id: int):
    if cpa_service_id <= 0:
        return None
    with get_db() as db:
        service = crud.get_cpa_service_by_id(db, cpa_service_id)
        if not service or not getattr(service, "enabled", False):
            return None
        return service


def _iter_all_accounts(page_size: int = 1000):
    last_seen_id = None
    while True:
        with get_db() as db:
            query = db.query(Account).order_by(Account.id.asc())
            if last_seen_id is not None:
                query = query.filter(Account.id > last_seen_id)
            batch = query.limit(page_size).all()
        if not batch:
            break
        for account in batch:
            yield account
        if len(batch) < page_size:
            break
        last_seen_id = batch[-1].id


def _current_maintenance_settings() -> dict:
    settings = get_settings()
    cpa_service_id = int(settings.account_maintenance_cpa_service_id or 0)
    remote_service = None
    remote_proxy = None
    if bool(settings.account_maintenance_cleanup_remote_cpa) and cpa_service_id > 0:
        remote_service = _load_remote_cpa_service(cpa_service_id)
        remote_proxy = _resolve_remote_proxy(settings, cpa_service_id)
    return {
        "validation_proxy": str(settings.account_maintenance_validation_proxy or "").strip() or None,
        "cleanup_local": bool(settings.account_maintenance_cleanup_local),
        "cleanup_remote": bool(settings.account_maintenance_cleanup_remote_cpa),
        "cpa_service_id": cpa_service_id,
        "remote_service_id": getattr(remote_service, "id", None) if remote_service is not None else None,
        "remote_proxy": remote_proxy,
        "enabled": bool(settings.account_maintenance_enabled),
    }


def _catchup_due_run(settings: Settings, now: Optional[datetime] = None) -> bool:
    current = now or datetime.now(timezone.utc)
    last_state = get_persisted_account_maintenance_state()
    last_run_text = last_state.get("last_run_at") if isinstance(last_state, dict) else None
    try:
        last_run_at = datetime.fromisoformat(last_run_text) if last_run_text else None
    except Exception:
        last_run_at = None

    hour, minute = _parse_schedule_time(str(settings.account_maintenance_schedule_time or "03:00"))
    local_now = current.astimezone(timezone(timedelta(hours=8)))
    today_due_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < today_due_local:
        return False
    if last_run_at is None:
        return True
    return last_run_at.astimezone(timezone(timedelta(hours=8))) < today_due_local


def _reload_account_for_action(account_id: int):
    with get_db() as db:
        return crud.get_account_by_id(db, account_id)


def _is_account_maintenance_cancelled() -> bool:
    coordinator = _coordinator_instance
    if coordinator is None:
        return False
    return coordinator.is_cancellation_requested()


class AccountMaintenanceCancelled(RuntimeError):
    pass


def run_account_maintenance_once(settings: Settings) -> AccountMaintenanceResult:
    initial_snapshot = _current_maintenance_settings()

    total_accounts = 0
    valid_count = 0
    invalid_count = 0
    local_deleted_count = 0
    remote_deleted_count = 0
    errors: list[str] = []

    for account in _iter_all_accounts():
        total_accounts += 1
        if _is_account_maintenance_cancelled():
            add_account_maintenance_log("[账号维护] 检测到停止信号，提前结束本轮执行")
            raise AccountMaintenanceCancelled("账号自动维护已收到停止信号")
        current_snapshot = _current_maintenance_settings()
        if current_snapshot != initial_snapshot:
            add_account_maintenance_log("[账号维护] 检测到维护配置已变更，提前结束当前轮次，等待使用新配置重新执行")
            raise AccountMaintenanceCancelled("账号自动维护配置已变更，当前轮次已安全终止")
        latest_account = _reload_account_for_action(account.id)
        if latest_account is None:
            errors.append(f"账号已不存在 account_id={account.id}: 跳过本轮维护")
            add_account_maintenance_log(f"[账号维护] 账号已不存在，跳过: account_id={account.id}")
            _publish_account_maintenance_status(
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                total=total_accounts,
            )
            continue
        account = latest_account
        account_label = str(account.email or f"account_id={account.id}")
        try:
            is_valid, error = do_validate(account.id, current_snapshot["validation_proxy"])
        except Exception as exc:
            errors.append(f"验证异常 {account_label}: {exc}")
            add_account_maintenance_log(f"[账号维护] 验证异常: {account_label} -> {exc}")
            _publish_account_maintenance_status(
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                total=total_accounts,
            )
            continue
        if is_valid:
            valid_count += 1
            _publish_account_maintenance_status(
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                total=total_accounts,
            )
            continue

        invalid_count += 1
        add_account_maintenance_log(
            f"[账号维护] 验证失败: {account_label} -> {error or '未知错误'}"
        )

        remote_cleanup_ok = True
        cleanup_remote = current_snapshot["cleanup_remote"]
        cleanup_local = current_snapshot["cleanup_local"]
        cpa_service_id = current_snapshot["cpa_service_id"]
        remote_proxy = current_snapshot["remote_proxy"]
        remote_service = _load_remote_cpa_service(cpa_service_id) if cleanup_remote and cpa_service_id > 0 else None

        if _is_account_maintenance_cancelled():
            add_account_maintenance_log("[账号维护] 检测到停止信号，终止当前账号后续清理")
            raise AccountMaintenanceCancelled("账号自动维护已收到停止信号")

        if cleanup_remote and not remote_service:
            remote_cleanup_ok = False
            errors.append(f"远端清理失败 {account_label}: CPA 服务 {cpa_service_id} 不存在、已禁用或已被删除")

        if cleanup_remote and remote_service and not account.email:
            remote_cleanup_ok = False
            errors.append(f"远端清理失败 account_id={account.id}: 缺少邮箱，无法精确删除远端 CPA auth-file")

        if cleanup_remote and remote_service and account.email:
            latest_account = _reload_account_for_action(account.id)
            if latest_account is None:
                remote_cleanup_ok = False
                errors.append(f"远端清理前账号已不存在 account_id={account.id}: 跳过远端删除")
            else:
                account = latest_account
                account_label = str(account.email or f"account_id={account.id}")
                try:
                    success, message = delete_cpa_auth_file(
                        remote_service.api_url,
                        remote_service.api_token,
                        account.email,
                        proxy_url=remote_proxy,
                    )
                except Exception as exc:
                    success = False
                    message = f"删除异常: {exc}"
                if success:
                    remote_deleted_count += 1
                    add_account_maintenance_log(f"[账号维护] 已清理远端 CPA: {account.email} ({message})")
                else:
                    remote_cleanup_ok = False
                    errors.append(f"远端清理失败 {account_label}: {message}")

        if cleanup_remote and cleanup_local and not remote_cleanup_ok:
            add_account_maintenance_log(
                f"[账号维护] 跳过本地删除: {account_label}，原因是远端 CPA 清理失败"
            )
            continue

        if cleanup_local:
            try:
                latest_account = _reload_account_for_action(account.id)
                if latest_account is None:
                    errors.append(f"本地清理前账号已不存在 {account_label}: 跳过删除")
                    continue
                with get_db() as db:
                    clear_current_account_selection_if_matches(db, account.id)
                    if crud.delete_account(db, account.id):
                        local_deleted_count += 1
                        add_account_maintenance_log(f"[账号维护] 已清理本地账号: {account_label}")
            except Exception as exc:
                errors.append(f"本地清理失败 {account_label}: {exc}")
        else:
            try:
                next_status = AccountStatus.FAILED.value
                error_text = str(error or "").lower()
                if any(token in error_text for token in ("过期", "expired", "401", "invalid")):
                    next_status = AccountStatus.EXPIRED.value
                elif any(token in error_text for token in ("封禁", "banned", "forbidden", "403")):
                    next_status = AccountStatus.BANNED.value
                with get_db() as db:
                    crud.update_account(db, account.id, status=next_status)
            except Exception as exc:
                errors.append(f"状态更新失败 {account_label}: {exc}")

        _publish_account_maintenance_status(
            current_index=total_accounts,
            completed=total_accounts,
            success=valid_count,
            failed=invalid_count,
            total=total_accounts,
        )

    return AccountMaintenanceResult(
        total_accounts=total_accounts,
        valid_count=valid_count,
        invalid_count=invalid_count,
        local_deleted_count=local_deleted_count,
        remote_deleted_count=remote_deleted_count,
        errors=errors,
    )


class AccountMaintenanceCoordinator:
    def __init__(self, settings_getter=get_settings):
        self._settings_getter = settings_getter
        self._task: Optional[asyncio.Task] = None
        self._wake_event = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._run_requested = False
        self._cancel_requested = False
        self._stop_requested = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_requested = False
        self._task = asyncio.create_task(self._run_forever(), name="account-maintenance-loop")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_requested = True
        self._cancel_requested = True
        self._wake_event.set()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._cancel_requested = False
            self._stop_requested = False

    def request_immediate_run(self) -> None:
        self._cancel_requested = False
        self._run_requested = True
        self._wake_event.set()

    def request_reconfigure(self) -> None:
        self._wake_event.set()

    def is_cancellation_requested(self) -> bool:
        return self._cancel_requested

    async def run_once(self) -> Optional[AccountMaintenanceResult]:
        if self._run_lock.locked():
            add_account_maintenance_log("[账号维护] 上一轮仍在执行，跳过重入")
            return None

        async with self._run_lock:
            self._cancel_requested = False
            settings = self._settings_getter()
            _publish_account_maintenance_status(
                status="running",
                finished=False,
                cancelled=False,
                total=0,
                completed=0,
                success=0,
                failed=0,
                skipped=0,
                current_index=0,
            )
            update_account_maintenance_state(
                enabled=bool(settings.account_maintenance_enabled),
                schedule_time=settings.account_maintenance_schedule_time,
            )
            if not settings.account_maintenance_enabled:
                self._cancel_requested = True
                _publish_account_maintenance_status(status="disabled", finished=True)
                update_account_maintenance_state(status="disabled", message="账号自动维护已禁用")
                return None

            update_account_maintenance_state(status="running", message="正在执行账号自动验证与清理")
            add_account_maintenance_log("[账号维护] 开始执行自动验证与清理")
            try:
                result = await asyncio.shield(asyncio.to_thread(run_account_maintenance_once, settings))
            except AccountMaintenanceCancelled as exc:
                _publish_account_maintenance_status(status="cancelled", finished=True, cancelled=True)
                update_account_maintenance_state(
                    status="cancelled",
                    message=str(exc),
                    last_run_at=_timestamp(),
                )
                add_account_maintenance_log(f"[账号维护] {exc}")
                return None
            summary = {
                "total_accounts": result.total_accounts,
                "valid_count": result.valid_count,
                "invalid_count": result.invalid_count,
                "local_deleted_count": result.local_deleted_count,
                "remote_deleted_count": result.remote_deleted_count,
                "error_count": len(result.errors),
            }
            _publish_account_maintenance_status(
                status="completed",
                finished=True,
                cancelled=False,
                total=result.total_accounts,
                completed=result.total_accounts,
                success=result.valid_count,
                failed=result.invalid_count,
                skipped=0,
                current_index=result.total_accounts,
            )
            update_account_maintenance_state(
                status="idle",
                message="账号自动维护执行完成",
                last_run_at=_timestamp(),
                last_summary=summary,
            )
            add_account_maintenance_log(
                "[账号维护] 执行完成: "
                f"总数 {result.total_accounts}, 有效 {result.valid_count}, 无效 {result.invalid_count}, "
                f"本地清理 {result.local_deleted_count}, 远端清理 {result.remote_deleted_count}"
            )
            for error in result.errors:
                add_account_maintenance_log(f"[账号维护] {error}")
            return result

    async def _run_forever(self) -> None:
        while True:
            if self._stop_requested:
                break
            settings = self._settings_getter()
            enabled = bool(settings.account_maintenance_enabled)
            schedule_time = str(settings.account_maintenance_schedule_time or "03:00")
            if enabled and _catchup_due_run(settings):
                self._run_requested = True
                update_account_maintenance_state(
                    enabled=enabled,
                    schedule_time=schedule_time,
                    status="catching_up",
                    message="检测到错过计划时间，正在补执行账号自动维护",
                    next_run_at=None,
                )
                add_account_maintenance_log("[账号维护] 检测到错过计划时间，立即补执行一次")
                try:
                    await self.run_once()
                    self._run_requested = False
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("账号自动维护补执行失败")
                    self._run_requested = False
                    update_account_maintenance_state(
                        status="error",
                        message="账号自动维护补执行失败，请查看服务端日志",
                        last_run_at=_timestamp(),
                    )
                    add_account_maintenance_log("[账号维护] 补执行失败，请检查服务端日志")
                continue
            try:
                next_run_at = compute_next_run_at(schedule_time)
            except ValueError as exc:
                logger.warning("账号自动维护计划时间无效: %s", exc)
                update_account_maintenance_state(
                    enabled=enabled,
                    schedule_time=schedule_time,
                    status="error",
                    message=f"计划时间无效: {exc}",
                    next_run_at=None,
                )
                add_account_maintenance_log(f"[账号维护] 计划时间无效，已回退等待修正: {exc}")
                self._run_requested = False
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=60)
                    self._wake_event.clear()
                except asyncio.TimeoutError:
                    pass
                if self._stop_requested:
                    break
                continue
            update_account_maintenance_state(
                enabled=enabled,
                schedule_time=schedule_time,
                next_run_at=None if not enabled else next_run_at.isoformat(),
                status="disabled" if not enabled else _account_maintenance_state.get("status", "idle"),
                message="账号自动维护已禁用" if not enabled else _account_maintenance_state.get("message", "等待计划执行"),
            )

            wait_seconds = max(1.0, (next_run_at - datetime.now(timezone.utc)).total_seconds())
            woke_early = False
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=wait_seconds)
                woke_early = True
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

            if self._stop_requested:
                break

            if woke_early and not self._run_requested:
                continue

            try:
                await self.run_once()
                self._run_requested = False
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("账号自动维护执行失败")
                self._run_requested = False
                update_account_maintenance_state(
                    status="error",
                    message="账号自动维护执行失败，请查看服务端日志",
                    last_run_at=_timestamp(),
                )
                add_account_maintenance_log("[账号维护] 自动维护执行失败，请检查服务端日志")
