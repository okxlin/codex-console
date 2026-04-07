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
from ..core.current_account import clear_current_account_selection_if_matches
from ..core.timezone_utils import utcnow_naive

logger = logging.getLogger(__name__)
ACCOUNT_MAINTENANCE_CHANNEL = "account-maintenance"
_ACCOUNT_MAINTENANCE_STATE_KEY = "account.maintenance.last_state"
_ACCOUNT_MAINTENANCE_RUNTIME_KEYS = {
    "status",
    "message",
    "last_run_at",
    "next_run_at",
    "last_summary",
}
_account_maintenance_state = {
    "status": "idle",
    "message": "账号自动维护未启动",
    "last_run_at": None,
    "next_run_at": None,
    "last_summary": None,
}
_coordinator_instance = None
_MAINTENANCE_LOGS_SETTING_KEY = "account.maintenance.last_logs"
_MAX_PERSISTED_LOG_LINES = 200
_maintenance_persist_lock = threading.RLock()
_MAINTENANCE_STATUS_PUBLISH_EVERY = 50


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def update_account_maintenance_state(**kwargs) -> dict:
    runtime_updates = {key: value for key, value in kwargs.items() if key in _ACCOUNT_MAINTENANCE_RUNTIME_KEYS}
    _account_maintenance_state.update(runtime_updates)
    state = get_account_maintenance_state()
    _persist_account_maintenance_state(state)
    return state


def get_account_maintenance_state() -> dict:
    state = dict(_account_maintenance_state)
    if state.get("last_run_at") or state.get("next_run_at") or state.get("last_summary"):
        return state
    persisted = get_persisted_account_maintenance_state()
    if persisted:
        state.update({k: v for k, v in persisted.items() if k in _ACCOUNT_MAINTENANCE_RUNTIME_KEYS and v is not None})
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


def _maybe_publish_account_maintenance_progress(
    *,
    force: bool = False,
    current_index: int,
    completed: int,
    success: int,
    failed: int,
    skipped: int,
    total: int,
) -> None:
    if not force and current_index % _MAINTENANCE_STATUS_PUBLISH_EVERY != 0:
        return
    _publish_account_maintenance_status(
        current_index=current_index,
        completed=completed,
        success=success,
        failed=failed,
        skipped=skipped,
        total=total,
    )


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


def _expand_cron_field(field: str, min_value: int, max_value: int) -> set[int]:
    text = str(field or "*").strip()
    values: set[int] = set()
    for part in text.split(","):
        item = part.strip()
        if not item:
            raise ValueError("Cron 表达式存在空字段")
        if "/" in item:
            base, step_text = item.split("/", 1)
            try:
                step = int(step_text)
            except Exception as exc:
                raise ValueError("Cron 步长必须为整数") from exc
            if step <= 0:
                raise ValueError("Cron 步长必须大于 0")
            if base in ("", "*"):
                start, end = min_value, max_value
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = int(base)
                end = max_value
            values.update(v for v in range(start, end + 1) if min_value <= v <= max_value and (v - start) % step == 0)
            continue
        if item == "*":
            values.update(range(min_value, max_value + 1))
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError("Cron 范围必须从小到大")
            values.update(v for v in range(start, end + 1) if min_value <= v <= max_value)
            continue
        value = int(item)
        if not (min_value <= value <= max_value):
            raise ValueError("Cron 字段超出范围")
        values.add(value)
    if not values:
        raise ValueError("Cron 字段不能为空")
    return values


def _matches_cron_day(dt_local: datetime, day_values: set[int], month_values: set[int], weekday_values: set[int]) -> bool:
    cron_weekday = (dt_local.weekday() + 1) % 7
    return dt_local.day in day_values and dt_local.month in month_values and cron_weekday in weekday_values


def _parse_schedule_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = [part.strip() for part in str(expr or "").split() if part.strip()]
    if len(parts) != 5:
        raise ValueError("Cron 表达式必须为 5 段基础格式：分 时 日 月 周")
    minute_values = _expand_cron_field(parts[0], 0, 59)
    hour_values = _expand_cron_field(parts[1], 0, 23)
    day_values = _expand_cron_field(parts[2], 1, 31)
    month_values = _expand_cron_field(parts[3], 1, 12)
    weekday_values = _expand_cron_field(parts[4], 0, 6)
    return minute_values, hour_values, day_values, month_values, weekday_values


def compute_next_run_at(
    schedule_time: str,
    now: Optional[datetime] = None,
    schedule_mode: str = "daily",
    schedule_cron: Optional[str] = None,
) -> datetime:
    current = now or datetime.now(timezone.utc)
    local_now = current.astimezone(timezone(timedelta(hours=8)))
    mode = str(schedule_mode or "daily").strip().lower()
    if mode == "daily":
        hour, minute = _parse_schedule_time(schedule_time)
        target_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_local <= local_now:
            target_local += timedelta(days=1)
        return target_local.astimezone(timezone.utc)
    if mode != "cron":
        raise ValueError("自动维护调度模式只支持 daily 或 cron")

    minute_values, hour_values, day_values, month_values, weekday_values = _parse_schedule_cron(schedule_cron or "")
    candidate = local_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (
            candidate.minute in minute_values
            and candidate.hour in hour_values
            and _matches_cron_day(candidate, day_values, month_values, weekday_values)
        ):
            return candidate.astimezone(timezone.utc)
        candidate += timedelta(minutes=1)
    raise ValueError("Cron 表达式在未来一年内没有可执行时间，请检查是否为受支持的 5 段基础格式")


@dataclass
class AccountMaintenanceResult:
    total_accounts: int
    valid_count: int
    invalid_count: int
    skipped_count: int
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
        "validation_interval_minutes": max(0, int(settings.account_maintenance_validation_interval_minutes or 0)),
        "schedule_mode": str(getattr(settings, "account_maintenance_schedule_mode", "daily") or "daily").strip().lower(),
        "schedule_cron": str(getattr(settings, "account_maintenance_schedule_cron", "0 3 * * *") or "0 3 * * *").strip(),
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

    mode = str(getattr(settings, "account_maintenance_schedule_mode", "daily") or "daily").strip().lower()
    if mode == "cron":
        cron_text = str(getattr(settings, "account_maintenance_schedule_cron", "0 3 * * *") or "0 3 * * *")
        next_after_last = compute_next_run_at(
            str(getattr(settings, "account_maintenance_schedule_time", "03:00") or "03:00"),
            now=last_run_at if last_run_at is not None else current - timedelta(days=1),
            schedule_mode="cron",
            schedule_cron=cron_text,
        )
        return next_after_last <= current

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


def _should_skip_validation(account: Account, interval_minutes: int) -> bool:
    if interval_minutes <= 0:
        return False
    last_checked_at = getattr(account, "last_maintenance_checked_at", None)
    if not last_checked_at:
        return False
    return last_checked_at + timedelta(minutes=interval_minutes) > utcnow_naive()


def _mark_account_maintenance_checked(account_id: int) -> None:
    with get_db() as db:
        crud.update_account(db, account_id, last_maintenance_checked_at=utcnow_naive())


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
    skipped_count = 0
    local_deleted_count = 0
    remote_deleted_count = 0
    errors: list[str] = []
    validation_exception_count = 0
    validation_failed_log_sample_count = 0
    remote_cleanup_failed_count = 0
    local_cleanup_failed_count = 0
    local_deleted_log_sample_count = 0
    status_updated_count = 0

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
            _maybe_publish_account_maintenance_progress(
                force=True,
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                skipped=skipped_count,
                total=total_accounts,
            )
            continue
        account = latest_account
        account_label = str(account.email or f"account_id={account.id}")
        validation_interval_minutes = int(current_snapshot.get("validation_interval_minutes") or 0)
        if _should_skip_validation(account, validation_interval_minutes):
            skipped_count += 1
            _maybe_publish_account_maintenance_progress(
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                skipped=skipped_count,
                total=total_accounts,
            )
            continue
        try:
            is_valid, error = do_validate(account.id, current_snapshot["validation_proxy"])
            _mark_account_maintenance_checked(account.id)
        except Exception as exc:
            validation_exception_count += 1
            _mark_account_maintenance_checked(account.id)
            errors.append(f"验证异常 {account_label}: {exc}")
            if validation_exception_count <= 5:
                add_account_maintenance_log(f"[账号维护] 验证异常: {account_label} -> {exc}")
            _maybe_publish_account_maintenance_progress(
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                skipped=skipped_count,
                total=total_accounts,
            )
            continue
        if is_valid:
            valid_count += 1
            _maybe_publish_account_maintenance_progress(
                current_index=total_accounts,
                completed=total_accounts,
                success=valid_count,
                failed=invalid_count,
                skipped=skipped_count,
                total=total_accounts,
            )
            continue

        invalid_count += 1
        validation_failed_log_sample_count += 1
        if validation_failed_log_sample_count <= 10:
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
            remote_cleanup_failed_count += 1
            errors.append(f"远端清理失败 {account_label}: CPA 服务 {cpa_service_id} 不存在、已禁用或已被删除")

        if cleanup_remote and remote_service and not account.email:
            remote_cleanup_ok = False
            remote_cleanup_failed_count += 1
            errors.append(f"远端清理失败 account_id={account.id}: 缺少邮箱，无法精确删除远端 CPA auth-file")

        if cleanup_remote and remote_service and account.email:
            latest_account = _reload_account_for_action(account.id)
            if latest_account is None:
                remote_cleanup_ok = False
                remote_cleanup_failed_count += 1
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
                    remote_cleanup_failed_count += 1
                    errors.append(f"远端清理失败 {account_label}: {message}")

        if cleanup_remote and cleanup_local and not remote_cleanup_ok:
            if remote_cleanup_failed_count <= 10:
                add_account_maintenance_log(
                    f"[账号维护] 远端 CPA 清理失败，但继续执行本地删除: {account_label}"
                )

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
                        local_deleted_log_sample_count += 1
                        if local_deleted_log_sample_count <= 10:
                            if cleanup_remote and not remote_cleanup_ok:
                                add_account_maintenance_log(
                                    f"[账号维护] 已清理本地账号（远端 CPA 未同步删除）: {account_label}"
                                )
                            else:
                                add_account_maintenance_log(f"[账号维护] 已清理本地账号: {account_label}")
            except Exception as exc:
                local_cleanup_failed_count += 1
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
                    status_updated_count += 1
            except Exception as exc:
                errors.append(f"状态更新失败 {account_label}: {exc}")

        _maybe_publish_account_maintenance_progress(
            force=(total_accounts == 1),
            current_index=total_accounts,
            completed=total_accounts,
            success=valid_count,
            failed=invalid_count,
            skipped=skipped_count,
            total=total_accounts,
        )

    if skipped_count > 0:
        add_account_maintenance_log(
            f"[账号维护] 本轮共跳过校验 {skipped_count} 个账号（原因：未达到最小校验间隔）"
        )
    if invalid_count > validation_failed_log_sample_count:
        add_account_maintenance_log(
            f"[账号维护] 另有 {invalid_count - validation_failed_log_sample_count} 个账号验证失败，已省略逐条日志"
        )
    if validation_exception_count > 5:
        add_account_maintenance_log(
            f"[账号维护] 另有 {validation_exception_count - 5} 个账号验证异常，已省略逐条日志"
        )
    if remote_cleanup_failed_count > 10:
        add_account_maintenance_log(
            f"[账号维护] 另有 {remote_cleanup_failed_count - 10} 次远端 CPA 清理失败，已省略逐条日志"
        )
    if local_deleted_count > local_deleted_log_sample_count:
        add_account_maintenance_log(
            f"[账号维护] 另有 {local_deleted_count - local_deleted_log_sample_count} 个本地账号已清理，已省略逐条日志"
        )
    if local_cleanup_failed_count > 0:
        add_account_maintenance_log(
            f"[账号维护] 本轮本地清理失败 {local_cleanup_failed_count} 次，请查看错误汇总"
        )
    if status_updated_count > 0:
        add_account_maintenance_log(
            f"[账号维护] 本轮共更新 {status_updated_count} 个账号状态（未启用本地删除）"
        )

    return AccountMaintenanceResult(
        total_accounts=total_accounts,
        valid_count=valid_count,
        invalid_count=invalid_count,
        skipped_count=skipped_count,
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
        self._cancel_requested = True
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
                message="等待计划执行",
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
                "skipped_count": result.skipped_count,
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
                skipped=result.skipped_count,
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
                f"总数 {result.total_accounts}, 有效 {result.valid_count}, 无效 {result.invalid_count}, 跳过 {result.skipped_count}, "
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
            schedule_mode = str(getattr(settings, "account_maintenance_schedule_mode", "daily") or "daily")
            schedule_time = str(settings.account_maintenance_schedule_time or "03:00")
            schedule_cron = str(getattr(settings, "account_maintenance_schedule_cron", "0 3 * * *") or "0 3 * * *")
            if enabled and _catchup_due_run(settings):
                self._run_requested = True
                update_account_maintenance_state(
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
                next_run_at = compute_next_run_at(
                    schedule_time,
                    schedule_mode=schedule_mode,
                    schedule_cron=schedule_cron,
                )
            except ValueError as exc:
                logger.warning("账号自动维护计划时间无效: %s", exc)
                update_account_maintenance_state(
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
