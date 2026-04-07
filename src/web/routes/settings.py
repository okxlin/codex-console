"""
设置 API 路由
"""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import text

from ...config.settings import get_settings, update_settings
from ...core.auto_registration import (
    get_auto_registration_state,
    trigger_auto_registration_check,
    update_auto_registration_state,
)
from ...core.account_maintenance import (
    _should_skip_validation,
    compute_next_run_at,
    get_account_maintenance_state,
    get_persisted_account_maintenance_logs,
    get_persisted_account_maintenance_state,
    update_account_maintenance_state,
    trigger_account_maintenance_reconfigure,
    trigger_account_maintenance_run,
)
from ...core.upload.cpa_upload import _match_auth_file_name, list_cpa_auth_files, probe_cpaproxyapi_compatibility
from ...database import crud
from ...database.session import get_db
from ...services import EmailServiceType

logger = logging.getLogger(__name__)
router = APIRouter()


def _cancel_active_auto_registration_batch_if_needed() -> None:
    """禁用自动注册时同步取消正在执行的自动补货批次。"""
    from . import registration as registration_routes

    current_batch_id = str(get_auto_registration_state().get("current_batch_id") or "").strip()
    if not current_batch_id:
        return

    batch = registration_routes.batch_tasks.get(current_batch_id)
    if not batch or batch.get("finished"):
        return

    batch["cancelled"] = True
    registration_routes.task_manager.cancel_batch(current_batch_id)
    registration_routes._cancel_batch_tasks(current_batch_id)


# ============== Pydantic Models ==============

class SettingItem(BaseModel):
    """设置项"""
    key: str
    value: str
    description: Optional[str] = None
    category: str = "general"


class SettingUpdateRequest(BaseModel):
    """设置更新请求"""
    value: str


class ProxySettings(BaseModel):
    """代理设置"""
    enabled: bool = False
    type: str = "http"  # http, socks5
    host: str = "127.0.0.1"
    port: int = 7890
    username: Optional[str] = None
    password: Optional[str] = None


class RegistrationSettings(BaseModel):
    """注册设置"""
    max_retries: int = 3
    timeout: int = 120
    default_password_length: int = 12
    sleep_min: int = 5
    sleep_max: int = 30
    entry_flow: str = "fast"
    refresh_backfill_enabled: bool = False
    playwright_failure_screenshot_enabled: bool = True
    playwright_headed: bool = False
    playwright_artifact_retention_days: int = 7
    playwright_artifact_max_total_size_mb: int = 512
    playwright_artifact_max_total_files: int = 500
    auto_enabled: bool = False
    auto_check_interval: int = 60
    auto_min_ready_auth_files: int = 1
    auto_email_service_type: str = "tempmail"
    auto_email_service_id: int = 0
    auto_proxy: Optional[str] = None
    auto_interval_min: int = 5
    auto_interval_max: int = 30
    auto_concurrency: int = 1
    auto_mode: str = "pipeline"
    auto_cpa_service_id: int = 0
    maintenance_enabled: bool = False
    maintenance_schedule_mode: str = "daily"
    maintenance_schedule_time: str = "03:00"
    maintenance_schedule_cron: str = "0 3 * * *"
    maintenance_validation_proxy: Optional[str] = None
    maintenance_validation_interval_minutes: int = 1440
    maintenance_debug_enabled: bool = False
    maintenance_cleanup_local: bool = False
    maintenance_cleanup_remote_cpa: bool = False
    maintenance_cpa_service_id: int = 0


class WebUISettings(BaseModel):
    """Web UI 设置"""
    host: Optional[str] = None
    port: Optional[int] = None
    debug: Optional[bool] = None
    access_password: Optional[str] = None


class AccountMaintenanceDebugRequest(BaseModel):
    """账号自动维护调试请求"""
    account_id: Optional[int] = None
    email: Optional[str] = None
    inspect_remote: bool = True
    include_accounts_sample: int = 20


class AllSettings(BaseModel):
    """所有设置"""
    proxy: ProxySettings
    registration: RegistrationSettings
    webui: WebUISettings


# ============== API Endpoints ==============

@router.get("")
async def get_all_settings():
    """获取所有设置"""
    settings = get_settings()

    entry_flow_raw = str(settings.registration_entry_flow or "fast").strip().lower()
    if entry_flow_raw == "fast":
        entry_flow = "fast"
    elif entry_flow_raw == "abcard":
        entry_flow = "abcard"
    elif entry_flow_raw == "playwright":
        entry_flow = "playwright"
    elif entry_flow_raw == "native":
        entry_flow = "native"
    else:
        entry_flow = "auto"
    entry_flow_label = {
        "fast": "极速流",
        "auto": "自动推荐",
        "abcard": "方案二 / Session 复用直取",
        "playwright": "Playwright / 浏览器态优先收尾",
        "native": "方案一 / 原生闭环收尾",
    }[entry_flow]

    return {
        "proxy": {
            "enabled": settings.proxy_enabled,
            "type": settings.proxy_type,
            "host": settings.proxy_host,
            "port": settings.proxy_port,
            "username": settings.proxy_username,
            "has_password": bool(settings.proxy_password),
            "dynamic_enabled": settings.proxy_dynamic_enabled,
            "dynamic_api_url": settings.proxy_dynamic_api_url,
            "dynamic_api_key_header": settings.proxy_dynamic_api_key_header,
            "dynamic_result_field": settings.proxy_dynamic_result_field,
            "has_dynamic_api_key": bool(settings.proxy_dynamic_api_key and settings.proxy_dynamic_api_key.get_secret_value()),
        },
        "registration": {
            "max_retries": settings.registration_max_retries,
            "timeout": settings.registration_timeout,
            "default_password_length": settings.registration_default_password_length,
            "sleep_min": settings.registration_sleep_min,
            "sleep_max": settings.registration_sleep_max,
            "entry_flow": entry_flow,
            "refresh_backfill_enabled": settings.registration_refresh_backfill_enabled,
            "playwright_failure_screenshot_enabled": settings.registration_playwright_failure_screenshot_enabled,
            "playwright_headed": settings.registration_playwright_headed,
            "playwright_artifact_retention_days": settings.registration_playwright_artifact_retention_days,
            "playwright_artifact_max_total_size_mb": settings.registration_playwright_artifact_max_total_size_mb,
            "playwright_artifact_max_total_files": settings.registration_playwright_artifact_max_total_files,
            "entry_flow_label": entry_flow_label,
            "auto_enabled": settings.registration_auto_enabled,
            "auto_check_interval": settings.registration_auto_check_interval,
            "auto_min_ready_auth_files": settings.registration_auto_min_ready_auth_files,
            "auto_email_service_type": settings.registration_auto_email_service_type,
            "auto_email_service_id": settings.registration_auto_email_service_id,
            "auto_proxy": settings.registration_auto_proxy,
            "auto_interval_min": settings.registration_auto_interval_min,
            "auto_interval_max": settings.registration_auto_interval_max,
            "auto_concurrency": settings.registration_auto_concurrency,
            "auto_mode": settings.registration_auto_mode,
            "auto_cpa_service_id": settings.registration_auto_cpa_service_id,
            "maintenance_enabled": settings.account_maintenance_enabled,
            "maintenance_schedule_mode": settings.account_maintenance_schedule_mode,
            "maintenance_schedule_time": settings.account_maintenance_schedule_time,
            "maintenance_schedule_cron": settings.account_maintenance_schedule_cron,
            "maintenance_validation_proxy": settings.account_maintenance_validation_proxy,
            "maintenance_validation_interval_minutes": settings.account_maintenance_validation_interval_minutes,
            "maintenance_debug_enabled": settings.account_maintenance_debug_enabled,
            "maintenance_cleanup_local": settings.account_maintenance_cleanup_local,
            "maintenance_cleanup_remote_cpa": settings.account_maintenance_cleanup_remote_cpa,
            "maintenance_cpa_service_id": settings.account_maintenance_cpa_service_id,
            "maintenance_state": get_account_maintenance_state() or get_persisted_account_maintenance_state(),
            "maintenance_logs": get_persisted_account_maintenance_logs(),
        },
        "webui": {
            "host": settings.webui_host,
            "port": settings.webui_port,
            "debug": settings.debug,
            "has_access_password": bool(settings.webui_access_password and settings.webui_access_password.get_secret_value()),
        },
        "tempmail": {
            "enabled": settings.tempmail_enabled,
            "api_url": settings.tempmail_base_url,
            "base_url": settings.tempmail_base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
        },
        "yyds_mail": {
            "enabled": settings.yyds_mail_enabled,
            "api_url": settings.yyds_mail_base_url,
            "base_url": settings.yyds_mail_base_url,
            "default_domain": settings.yyds_mail_default_domain,
            "timeout": settings.yyds_mail_timeout,
            "max_retries": settings.yyds_mail_max_retries,
            "has_api_key": bool(settings.yyds_mail_api_key and settings.yyds_mail_api_key.get_secret_value()),
        },
        "email_code": {
            "timeout": settings.email_code_timeout,
            "poll_interval": settings.email_code_poll_interval,
        },
    }


@router.get("/proxy/dynamic")
async def get_dynamic_proxy_settings():
    """获取动态代理设置"""
    settings = get_settings()
    return {
        "enabled": settings.proxy_dynamic_enabled,
        "api_url": settings.proxy_dynamic_api_url,
        "api_key_header": settings.proxy_dynamic_api_key_header,
        "result_field": settings.proxy_dynamic_result_field,
        "has_api_key": bool(settings.proxy_dynamic_api_key and settings.proxy_dynamic_api_key.get_secret_value()),
    }


class DynamicProxySettings(BaseModel):
    """动态代理设置"""
    enabled: bool = False
    api_url: str = ""
    api_key: Optional[str] = None
    api_key_header: str = "X-API-Key"
    result_field: str = ""


@router.post("/proxy/dynamic")
async def update_dynamic_proxy_settings(request: DynamicProxySettings):
    """更新动态代理设置"""
    update_dict = {
        "proxy_dynamic_enabled": request.enabled,
        "proxy_dynamic_api_url": request.api_url,
        "proxy_dynamic_api_key_header": request.api_key_header,
        "proxy_dynamic_result_field": request.result_field,
    }
    if request.api_key is not None:
        update_dict["proxy_dynamic_api_key"] = request.api_key

    update_settings(**update_dict)
    return {"success": True, "message": "动态代理设置已更新"}


@router.post("/proxy/dynamic/test")
async def test_dynamic_proxy(request: DynamicProxySettings):
    """测试动态代理 API"""
    from ...core.dynamic_proxy import fetch_dynamic_proxy

    if not request.api_url:
        raise HTTPException(status_code=400, detail="请填写动态代理 API 地址")

    # 若未传入 api_key，使用已保存的
    api_key = request.api_key or ""
    if not api_key:
        settings = get_settings()
        if settings.proxy_dynamic_api_key:
            api_key = settings.proxy_dynamic_api_key.get_secret_value()

    proxy_url = fetch_dynamic_proxy(
        api_url=request.api_url,
        api_key=api_key,
        api_key_header=request.api_key_header,
        result_field=request.result_field,
    )

    if not proxy_url:
        return {"success": False, "message": "动态代理 API 返回为空或请求失败"}

    # 用获取到的代理测试连通性
    import time
    from curl_cffi import requests as cffi_requests
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        start = time.time()
        resp = cffi_requests.get(
            "https://api.ipify.org?format=json",
            proxies=proxies,
            timeout=10,
            impersonate="chrome110"
        )
        elapsed = round((time.time() - start) * 1000)
        if resp.status_code == 200:
            ip = resp.json().get("ip", "")
            return {"success": True, "proxy_url": proxy_url, "ip": ip, "response_time": elapsed,
                    "message": f"动态代理可用，出口 IP: {ip}，响应时间: {elapsed}ms"}
        return {"success": False, "proxy_url": proxy_url, "message": f"代理连接失败: HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "proxy_url": proxy_url, "message": f"代理连接失败: {e}"}


@router.get("/registration")
async def get_registration_settings():
    """获取注册设置"""
    settings = get_settings()

    entry_flow_raw = str(settings.registration_entry_flow or "fast").strip().lower()
    if entry_flow_raw == "fast":
        entry_flow = "fast"
    elif entry_flow_raw == "abcard":
        entry_flow = "abcard"
    elif entry_flow_raw == "playwright":
        entry_flow = "playwright"
    elif entry_flow_raw == "native":
        entry_flow = "native"
    else:
        entry_flow = "auto"
    entry_flow_label = {
        "fast": "极速流",
        "auto": "自动推荐",
        "abcard": "方案二 / Session 复用直取",
        "playwright": "Playwright / 浏览器态优先收尾",
        "native": "方案一 / 原生闭环收尾",
    }[entry_flow]

    return {
        "max_retries": settings.registration_max_retries,
        "timeout": settings.registration_timeout,
        "default_password_length": settings.registration_default_password_length,
        "sleep_min": settings.registration_sleep_min,
        "sleep_max": settings.registration_sleep_max,
        "entry_flow": entry_flow,
        "refresh_backfill_enabled": settings.registration_refresh_backfill_enabled,
        "playwright_failure_screenshot_enabled": settings.registration_playwright_failure_screenshot_enabled,
        "playwright_headed": settings.registration_playwright_headed,
        "playwright_artifact_retention_days": settings.registration_playwright_artifact_retention_days,
        "playwright_artifact_max_total_size_mb": settings.registration_playwright_artifact_max_total_size_mb,
        "playwright_artifact_max_total_files": settings.registration_playwright_artifact_max_total_files,
        "entry_flow_label": entry_flow_label,
        "auto_enabled": settings.registration_auto_enabled,
        "auto_check_interval": settings.registration_auto_check_interval,
        "auto_min_ready_auth_files": settings.registration_auto_min_ready_auth_files,
        "auto_email_service_type": settings.registration_auto_email_service_type,
        "auto_email_service_id": settings.registration_auto_email_service_id,
        "auto_proxy": settings.registration_auto_proxy,
        "auto_interval_min": settings.registration_auto_interval_min,
        "auto_interval_max": settings.registration_auto_interval_max,
        "auto_concurrency": settings.registration_auto_concurrency,
        "auto_mode": settings.registration_auto_mode,
        "auto_cpa_service_id": settings.registration_auto_cpa_service_id,
        "maintenance_enabled": settings.account_maintenance_enabled,
        "maintenance_schedule_mode": settings.account_maintenance_schedule_mode,
        "maintenance_schedule_time": settings.account_maintenance_schedule_time,
        "maintenance_schedule_cron": settings.account_maintenance_schedule_cron,
        "maintenance_validation_proxy": settings.account_maintenance_validation_proxy,
        "maintenance_validation_interval_minutes": settings.account_maintenance_validation_interval_minutes,
        "maintenance_debug_enabled": settings.account_maintenance_debug_enabled,
        "maintenance_cleanup_local": settings.account_maintenance_cleanup_local,
        "maintenance_cleanup_remote_cpa": settings.account_maintenance_cleanup_remote_cpa,
        "maintenance_cpa_service_id": settings.account_maintenance_cpa_service_id,
        "maintenance_state": get_account_maintenance_state() or get_persisted_account_maintenance_state(),
        "maintenance_logs": get_persisted_account_maintenance_logs(),
    }


@router.post("/registration")
async def update_registration_settings(request: RegistrationSettings):
    """更新注册设置"""
    if request.timeout < 30 or request.timeout > 600:
        raise HTTPException(status_code=400, detail="注册超时时间必须在 30-600 秒之间")

    if request.default_password_length < 8 or request.default_password_length > 64:
        raise HTTPException(status_code=400, detail="密码长度必须在 8-64 之间")

    if request.sleep_min < 1 or request.sleep_max < request.sleep_min:
        raise HTTPException(status_code=400, detail="注册等待时间参数无效")

    flow_raw = (request.entry_flow or "native").strip().lower()
    # 兼容 register 基线中的方案别名，以及旧前端历史值。
    flow = "native" if flow_raw == "outlook" else flow_raw
    if flow in {"fast", "speed", "rapid", "v23"}:
        flow = "fast"
    elif flow in {"auto", "recommended", "default"}:
        flow = "auto"
    elif flow in {"playwright", "browser", "browser_capture", "pw"}:
        flow = "playwright"
    if flow in {"scheme1", "plan1", "solution1", "v1", "browser_fsm"}:
        flow = "native"
    elif flow in {"scheme2", "plan2", "solution2", "v2", "session_reuse"}:
        flow = "abcard"
    if flow not in {"fast", "auto", "native", "abcard", "playwright"}:
        raise HTTPException(status_code=400, detail="entry_flow 仅支持 fast / auto / native / abcard / playwright")

    if request.auto_check_interval < 5 or request.auto_check_interval > 3600:
        raise HTTPException(status_code=400, detail="自动注册检查间隔必须在 5-3600 秒之间")

    if request.playwright_artifact_retention_days < 1 or request.playwright_artifact_retention_days > 365:
        raise HTTPException(status_code=400, detail="Playwright 截图保留天数必须在 1-365 之间")

    if request.playwright_artifact_max_total_size_mb < 64 or request.playwright_artifact_max_total_size_mb > 10240:
        raise HTTPException(status_code=400, detail="Playwright 截图总容量上限必须在 64-10240 MB 之间")

    if request.playwright_artifact_max_total_files < 10 or request.playwright_artifact_max_total_files > 100000:
        raise HTTPException(status_code=400, detail="Playwright 截图文件数上限必须在 10-100000 之间")

    if request.auto_min_ready_auth_files < 1 or request.auto_min_ready_auth_files > 10000:
        raise HTTPException(status_code=400, detail="自动注册保底数量必须在 1-10000 之间")

    try:
        EmailServiceType(request.auto_email_service_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="自动注册邮箱服务类型无效") from exc

    normalized_auto_email_service_type = (
        "imap_mail" if request.auto_email_service_type == "catchall_imap" else request.auto_email_service_type
    )

    if request.auto_interval_min < 0 or request.auto_interval_max < request.auto_interval_min:
        raise HTTPException(status_code=400, detail="自动注册间隔时间参数无效")

    if request.auto_concurrency < 1 or request.auto_concurrency > 100:
        raise HTTPException(status_code=400, detail="自动注册并发数必须在 1-100 之间")

    if request.auto_mode not in ("parallel", "pipeline"):
        raise HTTPException(status_code=400, detail="自动注册模式必须为 parallel 或 pipeline")

    if request.auto_enabled and request.auto_cpa_service_id <= 0:
        raise HTTPException(status_code=400, detail="启用自动注册时必须选择一个 CPA 服务")

    request.maintenance_schedule_mode = str(request.maintenance_schedule_mode or "daily").strip().lower()
    if request.maintenance_schedule_mode not in {"daily", "cron"}:
        raise HTTPException(status_code=400, detail="自动维护调度模式只支持 daily 或 cron")

    try:
        compute_next_run_at(
            request.maintenance_schedule_time,
            schedule_mode=request.maintenance_schedule_mode,
            schedule_cron=request.maintenance_schedule_cron,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if request.maintenance_validation_interval_minutes < 5 or request.maintenance_validation_interval_minutes > 10080:
        raise HTTPException(status_code=400, detail="账号有效性校验间隔必须在 5-10080 分钟之间")

    with get_db() as db:
        if request.auto_enabled:
            cpa_service = crud.get_cpa_service_by_id(db, request.auto_cpa_service_id)
            if not cpa_service or not cpa_service.enabled:
                raise HTTPException(status_code=400, detail="自动注册选择的 CPA 服务不存在或已禁用")

        if request.auto_email_service_id > 0:
            email_service = crud.get_email_service_by_id(db, request.auto_email_service_id)
            if not email_service or not email_service.enabled:
                raise HTTPException(status_code=400, detail="自动注册选择的邮箱服务不存在或已禁用")
            normalized_service_type = (
                "imap_mail" if email_service.service_type == "catchall_imap" else email_service.service_type
            )
            if normalized_service_type != normalized_auto_email_service_type:
                raise HTTPException(status_code=400, detail="自动注册邮箱服务类型与指定服务不匹配")

        if request.maintenance_cleanup_remote_cpa:
            if request.maintenance_cpa_service_id <= 0:
                raise HTTPException(status_code=400, detail="启用远程 CPA 清理时必须选择一个 CPA 服务")
            maintenance_cpa_service = crud.get_cpa_service_by_id(db, request.maintenance_cpa_service_id)
            if not maintenance_cpa_service or not maintenance_cpa_service.enabled:
                raise HTTPException(status_code=400, detail="自动清理选择的 CPA 服务不存在或已禁用")

    update_settings(
        registration_max_retries=request.max_retries,
        registration_timeout=request.timeout,
        registration_default_password_length=request.default_password_length,
        registration_sleep_min=request.sleep_min,
        registration_sleep_max=request.sleep_max,
        registration_entry_flow=flow,
        registration_refresh_backfill_enabled=request.refresh_backfill_enabled,
        registration_playwright_failure_screenshot_enabled=request.playwright_failure_screenshot_enabled,
        registration_playwright_headed=request.playwright_headed,
        registration_playwright_artifact_retention_days=request.playwright_artifact_retention_days,
        registration_playwright_artifact_max_total_size_mb=request.playwright_artifact_max_total_size_mb,
        registration_playwright_artifact_max_total_files=request.playwright_artifact_max_total_files,
        registration_auto_enabled=request.auto_enabled,
        registration_auto_check_interval=request.auto_check_interval,
        registration_auto_min_ready_auth_files=request.auto_min_ready_auth_files,
        registration_auto_email_service_type=normalized_auto_email_service_type,
        registration_auto_email_service_id=max(0, request.auto_email_service_id),
        registration_auto_proxy=(request.auto_proxy or "").strip(),
        registration_auto_interval_min=request.auto_interval_min,
        registration_auto_interval_max=request.auto_interval_max,
        registration_auto_concurrency=request.auto_concurrency,
        registration_auto_mode=request.auto_mode,
        registration_auto_cpa_service_id=max(0, request.auto_cpa_service_id),
        account_maintenance_enabled=request.maintenance_enabled,
        account_maintenance_schedule_mode=request.maintenance_schedule_mode,
        account_maintenance_schedule_time=request.maintenance_schedule_time.strip(),
        account_maintenance_schedule_cron=request.maintenance_schedule_cron.strip(),
        account_maintenance_validation_proxy=(request.maintenance_validation_proxy or "").strip(),
        account_maintenance_validation_interval_minutes=request.maintenance_validation_interval_minutes,
        account_maintenance_debug_enabled=request.maintenance_debug_enabled,
        account_maintenance_cleanup_local=request.maintenance_cleanup_local,
        account_maintenance_cleanup_remote_cpa=request.maintenance_cleanup_remote_cpa,
        account_maintenance_cpa_service_id=max(0, request.maintenance_cpa_service_id),
    )
    maintenance_next_run_at = None
    maintenance_status = "disabled"
    maintenance_message = "账号自动维护已禁用"
    if request.maintenance_enabled:
        maintenance_next_run_at = compute_next_run_at(
            request.maintenance_schedule_time,
            schedule_mode=request.maintenance_schedule_mode,
            schedule_cron=request.maintenance_schedule_cron,
        ).isoformat()
        maintenance_status = "idle"
        maintenance_message = "等待计划执行"

    update_account_maintenance_state(
        status=maintenance_status,
        message=maintenance_message,
        next_run_at=maintenance_next_run_at,
    )

    if not request.maintenance_enabled:
        update_account_maintenance_state(
            status="disabled",
            message="账号自动维护已禁用",
            next_run_at=None,
        )
    trigger_account_maintenance_reconfigure()

    if request.auto_enabled:
        update_auto_registration_state(
            enabled=True,
            status="checking",
            message="自动注册设置已更新，正在立即检查库存",
            target_ready_count=request.auto_min_ready_auth_files,
        )
        trigger_auto_registration_check()
    else:
        _cancel_active_auto_registration_batch_if_needed()
        update_auto_registration_state(
            enabled=False,
            status="disabled",
            message="自动注册已禁用",
            current_batch_id=None,
            current_ready_count=None,
            target_ready_count=request.auto_min_ready_auth_files,
        )

    return {"success": True, "message": "注册设置已更新"}


@router.post("/registration/maintenance/run")
async def run_account_maintenance_now():
    """立即触发一次账号自动验证与清理。"""
    settings = get_settings()
    if not settings.account_maintenance_enabled:
        raise HTTPException(status_code=400, detail="账号自动维护未启用，请先保存并启用后再执行")
    if not trigger_account_maintenance_run():
        raise HTTPException(status_code=503, detail="账号自动维护协调器未就绪，请稍后重试")
    return {"success": True, "message": "已触发账号自动验证与清理"}


@router.post("/registration/maintenance/debug")
async def debug_account_maintenance(request: AccountMaintenanceDebugRequest):
    """调试账号自动维护在本地/远端清理前的关键依赖。"""
    settings = get_settings()
    if not bool(settings.account_maintenance_debug_enabled):
        raise HTTPException(status_code=403, detail="账号自动维护调试接口未启用")
    email_text = str(request.email or "").strip()
    include_accounts_sample = max(0, min(int(request.include_accounts_sample or 0), 100))

    with get_db() as db:
        account = None
        if request.account_id:
            account = crud.get_account_by_id(db, request.account_id)
        if account is None and email_text:
            account = crud.get_account_by_email(db, email_text)
        maintenance_state = get_account_maintenance_state() or get_persisted_account_maintenance_state()

        cpa_service_id = int(settings.account_maintenance_cpa_service_id or 0)
        cpa_service = crud.get_cpa_service_by_id(db, cpa_service_id) if cpa_service_id > 0 else None
        bind_card_task_columns = [
            row[1]
            for row in db.execute(text("SELECT * FROM pragma_table_info('bind_card_tasks')")).fetchall()
        ]
        bind_card_task_count = None
        if account is not None:
            bind_card_task_count = db.execute(
                text("SELECT COUNT(1) FROM bind_card_tasks WHERE account_id = :account_id"),
                {"account_id": account.id},
            ).scalar() or 0

        total_accounts = db.execute(text("SELECT COUNT(1) FROM accounts")).scalar() or 0
        invalid_accounts = db.execute(
            text("SELECT COUNT(1) FROM accounts WHERE status IN ('failed', 'expired', 'banned')")
        ).scalar() or 0
        invalid_accounts_sample = []
        if include_accounts_sample > 0:
            invalid_accounts_sample = [
                {
                    "id": row[0],
                    "email": row[1],
                    "status": row[2],
                    "has_bind_card_tasks": bool(row[3]),
                }
                for row in db.execute(
                    text(
                        "SELECT a.id, a.email, a.status, "
                        "EXISTS(SELECT 1 FROM bind_card_tasks b WHERE b.account_id = a.id) AS has_bind_card_tasks "
                        "FROM accounts a "
                        "WHERE a.status IN ('failed', 'expired', 'banned') "
                        "ORDER BY a.id ASC LIMIT :limit"
                    ),
                    {"limit": include_accounts_sample},
                ).fetchall()
            ]

    required_columns = ["account_email_snapshot", "account_label_snapshot"]
    missing_columns = [column for column in required_columns if column not in bind_card_task_columns]
    validation_interval_minutes = int(settings.account_maintenance_validation_interval_minutes or 0)
    account_runtime_debug = None
    if account is not None:
        last_checked_at = getattr(account, "last_maintenance_checked_at", None)
        account_runtime_debug = {
            "last_maintenance_checked_at": last_checked_at.isoformat() if last_checked_at else None,
            "validation_interval_minutes": validation_interval_minutes,
            "would_skip_validation_now": _should_skip_validation(account, validation_interval_minutes),
            "maintenance_status": maintenance_state.get("status") if isinstance(maintenance_state, dict) else None,
            "maintenance_message": maintenance_state.get("message") if isinstance(maintenance_state, dict) else None,
            "maintenance_last_run_at": maintenance_state.get("last_run_at") if isinstance(maintenance_state, dict) else None,
            "maintenance_next_run_at": maintenance_state.get("next_run_at") if isinstance(maintenance_state, dict) else None,
            "last_summary": maintenance_state.get("last_summary") if isinstance(maintenance_state, dict) else None,
        }

    remote_debug = {
        "enabled": bool(settings.account_maintenance_cleanup_remote_cpa),
        "inspect_requested": bool(request.inspect_remote),
        "cpa_service_id": cpa_service_id,
        "service_found": cpa_service is not None,
        "service_enabled": bool(getattr(cpa_service, "enabled", False)) if cpa_service is not None else False,
        "api_url": getattr(cpa_service, "api_url", None) if cpa_service is not None else None,
        "proxy_url": getattr(cpa_service, "proxy_url", None) if cpa_service is not None else None,
        "normalized_email": str(account.email or "").strip().lower() if account is not None else None,
        "list_ok": None,
        "match_filename": None,
        "matched_candidates": [],
        "message": None,
        "compatibility_probe": None,
    }

    if not settings.account_maintenance_cleanup_remote_cpa:
        remote_debug["message"] = "当前未启用远端 CPA 清理"
    elif cpa_service is None:
        remote_debug["message"] = "当前配置的 CPA 服务不存在"
    elif not getattr(cpa_service, "enabled", False):
        remote_debug["message"] = "当前配置的 CPA 服务已禁用"
    elif not request.inspect_remote:
        remote_debug["message"] = "已跳过远端接口探测"
    elif account is None:
        remote_debug["message"] = "未指定账号，返回全局远端调试信息"
    elif not account.email:
        remote_debug["message"] = "账号邮箱为空，无法匹配远端 auth-file"
    else:
        success, payload, message = list_cpa_auth_files(
            cpa_service.api_url,
            cpa_service.api_token,
            proxy_url=getattr(cpa_service, "proxy_url", None),
        )
        remote_debug["list_ok"] = success
        remote_debug["message"] = message
        if success:
            remote_debug["match_filename"] = _match_auth_file_name(payload, account.email)
            names = []
            if isinstance(payload, dict):
                files = payload.get("files", [])
            elif isinstance(payload, list):
                files = payload
            else:
                files = []
            target = str(account.email or "").strip().lower()
            for item in files:
                if not isinstance(item, dict):
                    continue
                candidate = str(
                    item.get("name") or item.get("filename") or item.get("file_name") or item.get("path") or ""
                ).strip()
                if candidate and target and target in candidate.lower():
                    names.append(candidate)
            remote_debug["matched_candidates"] = names[:20]
        remote_debug["compatibility_probe"] = probe_cpaproxyapi_compatibility(
            cpa_service.api_url,
            cpa_service.api_token,
            email=account.email if account is not None else None,
            proxy_url=getattr(cpa_service, "proxy_url", None),
        )

    local_debug = {
        "enabled": bool(settings.account_maintenance_cleanup_local),
        "bind_card_task_count": int(bind_card_task_count or 0) if account is not None else None,
        "bind_card_task_columns": bind_card_task_columns,
        "missing_columns": missing_columns,
        "can_delete_locally": not missing_columns,
        "message": None,
    }
    if missing_columns:
        local_debug["message"] = f"bind_card_tasks 缺少列: {', '.join(missing_columns)}"
    else:
        local_debug["message"] = "本地删除所需的 bind_card_tasks 快照列已齐全"

    return {
        "success": True,
        "mode": "account" if account is not None else "global",
        "account": {
            "id": account.id,
            "email": account.email,
            "status": account.status,
        } if account is not None else None,
        "maintenance_settings": {
            "enabled": bool(settings.account_maintenance_enabled),
            "schedule_time": settings.account_maintenance_schedule_time,
            "validation_interval_minutes": validation_interval_minutes,
            "cleanup_local": bool(settings.account_maintenance_cleanup_local),
            "cleanup_remote_cpa": bool(settings.account_maintenance_cleanup_remote_cpa),
            "maintenance_cpa_service_id": cpa_service_id,
        },
        "global_debug": {
            "total_accounts": int(total_accounts),
            "invalid_accounts": int(invalid_accounts),
            "invalid_accounts_sample": invalid_accounts_sample,
        },
        "account_runtime_debug": account_runtime_debug,
        "local_debug": local_debug,
        "remote_debug": remote_debug,
        "would_skip_local_delete_if_remote_fails": bool(
            settings.account_maintenance_cleanup_local and settings.account_maintenance_cleanup_remote_cpa
        ),
    }


@router.post("/webui")
async def update_webui_settings(request: WebUISettings):
    """更新 Web UI 设置"""
    update_dict = {}
    if request.host is not None:
        update_dict["webui_host"] = request.host
    if request.port is not None:
        update_dict["webui_port"] = request.port
    if request.debug is not None:
        update_dict["debug"] = request.debug
    if request.access_password:
        update_dict["webui_access_password"] = request.access_password

    update_settings(**update_dict)
    runtime_changed = any(value is not None for value in (request.host, request.port, request.debug))
    message = "Web UI 设置已更新"
    if runtime_changed:
        message += "，监听地址/端口/调试模式需重启服务后生效"
    return {"success": True, "message": message, "restart_required": runtime_changed}


@router.get("/database")
async def get_database_info():
    """获取数据库信息"""
    settings = get_settings()

    import os
    db_path = settings.database_url
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]

    db_file = Path(db_path) if os.path.isabs(db_path) else Path(db_path)
    db_size = db_file.stat().st_size if db_file.exists() else 0

    with get_db() as db:
        from ...database.models import Account, EmailService, RegistrationTask

        account_count = db.query(Account).count()
        service_count = db.query(EmailService).count()
        task_count = db.query(RegistrationTask).count()

    return {
        "database_url": settings.database_url,
        "database_size_bytes": db_size,
        "database_size_mb": round(db_size / (1024 * 1024), 2),
        "accounts_count": account_count,
        "email_services_count": service_count,
        "tasks_count": task_count,
    }


@router.post("/database/backup")
async def backup_database():
    """备份数据库"""
    import shutil
    from datetime import datetime

    settings = get_settings()

    db_path = settings.database_url
    if db_path.startswith("sqlite:///"):
        db_path = db_path[10:]

    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    # 创建备份目录
    from pathlib import Path as FilePath
    backup_dir = FilePath(db_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"database_backup_{timestamp}.db"

    # 复制数据库文件
    shutil.copy2(db_path, backup_path)

    return {
        "success": True,
        "message": "数据库备份成功",
        "backup_path": str(backup_path)
    }


@router.post("/database/import")
async def import_database(file: UploadFile = File(...)):
    """导入数据库（自动备份后覆盖当前 SQLite 文件）"""
    import shutil
    import tempfile
    from datetime import datetime
    from pathlib import Path as FilePath
    from ...database.session import get_session_manager

    settings = get_settings()

    db_path = settings.database_url
    if not db_path.startswith("sqlite:///"):
        raise HTTPException(status_code=400, detail="当前仅支持 SQLite 数据库导入")

    db_path = db_path[10:]
    db_file = FilePath(db_path)

    # 校验上传扩展名
    filename = (file.filename or "").lower()
    allowed_ext = (".db", ".sqlite", ".sqlite3")
    if filename and not filename.endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="仅支持 .db / .sqlite / .sqlite3 文件")

    if not db_file.exists():
        raise HTTPException(status_code=404, detail="数据库文件不存在")

    # 先落地到临时文件，再校验头，避免脏写
    temp_path = None
    try:
        db_file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="db_import_",
            suffix=".db",
            dir=str(db_file.parent),
            delete=False
        ) as tmp:
            temp_path = FilePath(tmp.name)
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)

        if not temp_path.exists() or temp_path.stat().st_size < 100:
            raise HTTPException(status_code=400, detail="导入文件无效或为空")

        # SQLite 文件头校验
        with temp_path.open("rb") as f:
            header = f.read(16)
        if not header.startswith(b"SQLite format 3\x00"):
            raise HTTPException(status_code=400, detail="文件不是有效的 SQLite 数据库")

        # 先释放数据库连接，避免 Windows 下文件被占用
        session_manager = get_session_manager()
        session_manager.engine.dispose()

        # 导入前自动备份
        backup_dir = db_file.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"database_backup_before_import_{timestamp}.db"
        shutil.copy2(db_file, backup_path)

        # 清理 WAL/SHM，避免替换后出现旧事务残留
        wal_file = FilePath(f"{db_file}-wal")
        shm_file = FilePath(f"{db_file}-shm")
        for sidecar in (wal_file, shm_file):
            try:
                if sidecar.exists():
                    sidecar.unlink()
            except Exception:
                logger.warning("清理 SQLite 附属文件失败: %s", sidecar)

        os.replace(str(temp_path), str(db_file))

        logger.info("数据库导入成功: file=%s backup=%s", file.filename, backup_path)
        return {
            "success": True,
            "message": "数据库导入成功",
            "backup_path": str(backup_path),
        }
    finally:
        await file.close()
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


@router.post("/database/cleanup")
async def cleanup_database(
    days: int = 30,
    keep_failed: bool = True
):
    """清理过期数据"""
    from datetime import timedelta
    from ...core.timezone_utils import utcnow_naive

    cutoff_date = utcnow_naive() - timedelta(days=days)

    with get_db() as db:
        from ...database.models import RegistrationTask
        from sqlalchemy import delete

        # 删除旧任务
        conditions = [RegistrationTask.created_at < cutoff_date]
        if not keep_failed:
            conditions.append(RegistrationTask.status != "failed")
        else:
            conditions.append(RegistrationTask.status.in_(["completed", "cancelled"]))

        result = db.execute(
            delete(RegistrationTask).where(*conditions)
        )
        db.commit()

        deleted_count = result.rowcount

    return {
        "success": True,
        "message": f"已清理 {deleted_count} 条过期任务记录",
        "deleted_count": deleted_count
    }


@router.get("/logs")
async def get_recent_logs(
    lines: int = 100,
    level: str = "INFO"
):
    """获取最近日志"""
    settings = get_settings()

    log_file = settings.log_file
    if not log_file:
        return {"logs": [], "message": "日志文件未配置"}

    from pathlib import Path
    log_path = Path(log_file)

    if not log_path.exists():
        return {"logs": [], "message": "日志文件不存在"}

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]

        return {
            "logs": [line.strip() for line in recent_lines],
            "total_lines": len(all_lines)
        }
    except Exception as e:
        return {"logs": [], "error": str(e)}


# ============== 临时邮箱设置 ==============

class TempmailSettings(BaseModel):
    """临时邮箱设置"""
    api_url: Optional[str] = None
    enabled: Optional[bool] = None
    yyds_api_url: Optional[str] = None
    yyds_api_key: Optional[str] = None
    yyds_default_domain: Optional[str] = None
    yyds_enabled: Optional[bool] = None


class EmailCodeSettings(BaseModel):
    """验证码等待设置"""
    timeout: int = 120  # 验证码等待超时（秒）
    poll_interval: int = 3  # 验证码轮询间隔（秒）


@router.get("/tempmail")
async def get_tempmail_settings():
    """获取临时邮箱设置"""
    settings = get_settings()

    return {
        "tempmail": {
            "api_url": settings.tempmail_base_url,
            "timeout": settings.tempmail_timeout,
            "max_retries": settings.tempmail_max_retries,
            "enabled": settings.tempmail_enabled,
        },
        "yyds_mail": {
            "api_url": settings.yyds_mail_base_url,
            "default_domain": settings.yyds_mail_default_domain,
            "timeout": settings.yyds_mail_timeout,
            "max_retries": settings.yyds_mail_max_retries,
            "enabled": settings.yyds_mail_enabled,
            "has_api_key": bool(settings.yyds_mail_api_key and settings.yyds_mail_api_key.get_secret_value()),
        },
    }


@router.post("/tempmail")
async def update_tempmail_settings(request: TempmailSettings):
    """更新临时邮箱设置"""
    update_dict = {}

    if request.api_url:
        update_dict["tempmail_base_url"] = request.api_url
    if request.enabled is not None:
        update_dict["tempmail_enabled"] = request.enabled
    if request.yyds_api_url is not None:
        update_dict["yyds_mail_base_url"] = request.yyds_api_url
    if request.yyds_api_key is not None:
        update_dict["yyds_mail_api_key"] = request.yyds_api_key
    if request.yyds_default_domain is not None:
        update_dict["yyds_mail_default_domain"] = request.yyds_default_domain
    if request.yyds_enabled is not None:
        update_dict["yyds_mail_enabled"] = request.yyds_enabled

    update_settings(**update_dict)

    return {"success": True, "message": "临时邮箱设置已更新"}


# ============== 验证码等待设置 ==============

@router.get("/email-code")
async def get_email_code_settings():
    """获取验证码等待设置"""
    settings = get_settings()
    return {
        "timeout": settings.email_code_timeout,
        "poll_interval": settings.email_code_poll_interval,
    }


@router.post("/email-code")
async def update_email_code_settings(request: EmailCodeSettings):
    """更新验证码等待设置"""
    # 验证参数范围
    if request.timeout < 30 or request.timeout > 600:
        raise HTTPException(status_code=400, detail="超时时间必须在 30-600 秒之间")
    if request.poll_interval < 1 or request.poll_interval > 30:
        raise HTTPException(status_code=400, detail="轮询间隔必须在 1-30 秒之间")

    update_settings(
        email_code_timeout=request.timeout,
        email_code_poll_interval=request.poll_interval,
    )

    return {"success": True, "message": "验证码等待设置已更新"}


# ============== 代理列表 CRUD ==============

class ProxyCreateRequest(BaseModel):
    """创建代理请求"""
    name: str
    type: str = "http"  # http, socks5
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: bool = True
    priority: int = 0


class ProxyUpdateRequest(BaseModel):
    """更新代理请求"""
    name: Optional[str] = None
    type: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


@router.get("/proxies")
async def get_proxies_list(enabled: Optional[bool] = None):
    """获取代理列表"""
    with get_db() as db:
        proxies = crud.get_proxies(db, enabled=enabled)
        return {
            "proxies": [p.to_dict() for p in proxies],
            "total": len(proxies)
        }


@router.post("/proxies")
async def create_proxy_item(request: ProxyCreateRequest):
    """创建代理"""
    with get_db() as db:
        proxy = crud.create_proxy(
            db,
            name=request.name,
            type=request.type,
            host=request.host,
            port=request.port,
            username=request.username,
            password=request.password,
            enabled=request.enabled,
            priority=request.priority
        )
        return {"success": True, "proxy": proxy.to_dict()}


@router.get("/proxies/{proxy_id}")
async def get_proxy_item(proxy_id: int):
    """获取单个代理"""
    with get_db() as db:
        proxy = crud.get_proxy_by_id(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return proxy.to_dict(include_password=True)


@router.patch("/proxies/{proxy_id}")
async def update_proxy_item(proxy_id: int, request: ProxyUpdateRequest):
    """更新代理"""
    with get_db() as db:
        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.type is not None:
            update_data["type"] = request.type
        if request.host is not None:
            update_data["host"] = request.host
        if request.port is not None:
            update_data["port"] = request.port
        if request.username is not None:
            update_data["username"] = request.username
        if request.password is not None:
            update_data["password"] = request.password
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.priority is not None:
            update_data["priority"] = request.priority

        proxy = crud.update_proxy(db, proxy_id, **update_data)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "proxy": proxy.to_dict()}


@router.delete("/proxies/{proxy_id}")
async def delete_proxy_item(proxy_id: int):
    """删除代理"""
    with get_db() as db:
        success = crud.delete_proxy(db, proxy_id)
        if not success:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已删除"}


@router.post("/proxies/{proxy_id}/set-default")
async def set_proxy_default(proxy_id: int):
    """将指定代理设为默认"""
    with get_db() as db:
        proxy = crud.set_proxy_default(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "proxy": proxy.to_dict()}


@router.post("/proxies/{proxy_id}/test")
async def test_proxy_item(proxy_id: int):
    """测试单个代理"""
    import time
    from curl_cffi import requests as cffi_requests

    with get_db() as db:
        proxy = crud.get_proxy_by_id(db, proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")

        proxy_url = proxy.proxy_url
        test_url = "https://api.ipify.org?format=json"
        start_time = time.time()

        try:
            proxies = {
                "http": proxy_url,
                "https": proxy_url
            }

            response = cffi_requests.get(
                test_url,
                proxies=proxies,
                timeout=3,
                impersonate="chrome110"
            )

            elapsed_time = time.time() - start_time

            if response.status_code == 200:
                ip_info = response.json()
                return {
                    "success": True,
                    "ip": ip_info.get("ip", ""),
                    "response_time": round(elapsed_time * 1000),
                    "message": f"代理连接成功，出口 IP: {ip_info.get('ip', 'unknown')}"
                }
            else:
                return {
                    "success": False,
                    "message": f"代理返回错误状态码: {response.status_code}"
                }

        except Exception as e:
            return {
                "success": False,
                "message": f"代理连接失败: {str(e)}"
            }


@router.post("/proxies/test-all")
async def test_all_proxies():
    """测试所有启用的代理"""
    import time
    from curl_cffi import requests as cffi_requests

    with get_db() as db:
        proxies = crud.get_enabled_proxies(db)

        results = []
        for proxy in proxies:
            proxy_url = proxy.proxy_url
            test_url = "https://api.ipify.org?format=json"
            start_time = time.time()

            try:
                proxies_dict = {
                    "http": proxy_url,
                    "https": proxy_url
                }

                response = cffi_requests.get(
                    test_url,
                    proxies=proxies_dict,
                    timeout=3,
                    impersonate="chrome110"
                )

                elapsed_time = time.time() - start_time

                if response.status_code == 200:
                    ip_info = response.json()
                    results.append({
                        "id": proxy.id,
                        "name": proxy.name,
                        "success": True,
                        "ip": ip_info.get("ip", ""),
                        "response_time": round(elapsed_time * 1000)
                    })
                else:
                    results.append({
                        "id": proxy.id,
                        "name": proxy.name,
                        "success": False,
                        "message": f"状态码: {response.status_code}"
                    })

            except Exception as e:
                results.append({
                    "id": proxy.id,
                    "name": proxy.name,
                    "success": False,
                    "message": str(e)
                })

        success_count = sum(1 for r in results if r["success"])
        return {
            "total": len(proxies),
            "success": success_count,
            "failed": len(proxies) - success_count,
            "results": results
        }


@router.post("/proxies/{proxy_id}/enable")
async def enable_proxy(proxy_id: int):
    """启用代理"""
    with get_db() as db:
        proxy = crud.update_proxy(db, proxy_id, enabled=True)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已启用"}


@router.post("/proxies/{proxy_id}/disable")
async def disable_proxy(proxy_id: int):
    """禁用代理"""
    with get_db() as db:
        proxy = crud.update_proxy(db, proxy_id, enabled=False)
        if not proxy:
            raise HTTPException(status_code=404, detail="代理不存在")
        return {"success": True, "message": "代理已禁用"}


# ============== Outlook 设置 ==============

class OutlookSettings(BaseModel):
    """Outlook 设置"""
    default_client_id: Optional[str] = None


@router.get("/outlook")
async def get_outlook_settings():
    """获取 Outlook 设置"""
    settings = get_settings()

    return {
        "default_client_id": settings.outlook_default_client_id,
        "provider_priority": settings.outlook_provider_priority,
        "health_failure_threshold": settings.outlook_health_failure_threshold,
        "health_disable_duration": settings.outlook_health_disable_duration,
    }


@router.post("/outlook")
async def update_outlook_settings(request: OutlookSettings):
    """更新 Outlook 设置"""
    update_dict = {}

    if request.default_client_id is not None:
        update_dict["outlook_default_client_id"] = request.default_client_id

    if update_dict:
        update_settings(**update_dict)

    return {"success": True, "message": "Outlook 设置已更新"}


# ============== Team Manager 设置 ==============

class TeamManagerSettings(BaseModel):
    """Team Manager 设置"""
    enabled: bool = False
    api_url: str = ""
    api_key: str = ""


class TeamManagerTestRequest(BaseModel):
    """Team Manager 测试请求"""
    api_url: str
    api_key: str


@router.get("/team-manager")
async def get_team_manager_settings():
    """获取 Team Manager 设置"""
    settings = get_settings()
    return {
        "enabled": settings.tm_enabled,
        "api_url": settings.tm_api_url,
        "has_api_key": bool(settings.tm_api_key and settings.tm_api_key.get_secret_value()),
    }


@router.post("/team-manager")
async def update_team_manager_settings(request: TeamManagerSettings):
    """更新 Team Manager 设置"""
    update_dict = {
        "tm_enabled": request.enabled,
        "tm_api_url": request.api_url,
    }
    if request.api_key:
        update_dict["tm_api_key"] = request.api_key
    update_settings(**update_dict)
    return {"success": True, "message": "Team Manager 设置已更新"}


@router.post("/team-manager/test")
async def test_team_manager_connection(request: TeamManagerTestRequest):
    """测试 Team Manager 连接"""
    from ...core.upload.team_manager_upload import test_team_manager_connection as do_test

    settings = get_settings()
    api_key = request.api_key
    if api_key == 'use_saved_key' or not api_key:
        if settings.tm_api_key:
            api_key = settings.tm_api_key.get_secret_value()
        else:
            return {"success": False, "message": "未配置 API Key"}

    success, message = do_test(request.api_url, api_key)
    return {"success": success, "message": message}
