"""
注册任务 API 路由
"""

import asyncio
import logging
import uuid
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, desc

from ...database import crud
from ...database.session import get_db
from ...database.models import RegistrationTask, Proxy
from ...core.register import RegistrationEngine, RegistrationResult
from ...services import EmailServiceFactory, EmailServiceType
from ...config.settings import get_settings, Settings
from ...core.auto_registration import (
    add_auto_registration_log,
    get_auto_registration_inventory,
    get_auto_registration_logs,
    get_auto_registration_state,
    update_auto_registration_state,
)
from ...core.timezone_utils import utcnow_naive
from ...core.playwright_insights import get_cached_playwright_stats, invalidate_playwright_stats_cache
from ...core.utils import get_data_dir
from ..task_manager import task_manager

logger = logging.getLogger(__name__)
router = APIRouter()

# 任务存储（简单的内存存储，生产环境应使用 Redis）
running_tasks: dict = {}
# 批量任务存储
batch_tasks: Dict[str, dict] = {}


def _cancel_batch_tasks(batch_id: str) -> None:
    batch = batch_tasks.get(batch_id)
    if not batch:
        return

    for task_uuid in batch.get("task_uuids", []):
        task_manager.cancel_task(task_uuid)

    auto_state = get_auto_registration_state()
    if auto_state.get("current_batch_id") == batch_id:
        update_auto_registration_state(
            status="cancelling",
            message=f"自动补货取消中: {batch_id}",
        )
        add_auto_registration_log(f"[自动注册] 已提交补货批量任务取消请求: {batch_id}")


# ============== Proxy Helper Functions ==============

def get_proxy_for_registration(db) -> Tuple[Optional[str], Optional[int]]:
    """
    获取用于注册的代理

    策略：
    1. 优先从代理列表中随机选择一个启用的代理
    2. 如果代理列表为空且启用了动态代理，调用动态代理 API 获取
    3. 否则使用系统设置中的静态默认代理

    Returns:
        Tuple[proxy_url, proxy_id]: 代理 URL 和代理 ID（如果来自代理列表）
    """
    # 先尝试从代理列表中获取
    proxy = crud.get_random_proxy(db)
    if proxy:
        return proxy.proxy_url, proxy.id

    # 代理列表为空，尝试动态代理或静态代理
    from ...core.dynamic_proxy import get_proxy_url_for_task
    proxy_url = get_proxy_url_for_task()
    if proxy_url:
        return proxy_url, None

    return None, None


def get_proxy_for_registration_with_exclusions(
    db,
    *,
    excluded_proxy_urls: Optional[List[str]] = None,
) -> Tuple[Optional[str], Optional[int]]:
    excluded = {str(item or "").strip() for item in (excluded_proxy_urls or []) if str(item or "").strip()}
    if not excluded:
        return get_proxy_for_registration(db)

    enabled_proxies = [proxy for proxy in crud.get_enabled_proxies(db) if str(proxy.proxy_url or "").strip() not in excluded]
    if enabled_proxies:
        import random

        chosen = random.choice(enabled_proxies)
        return chosen.proxy_url, chosen.id

    return get_proxy_for_registration(db)


def update_proxy_usage(db, proxy_id: Optional[int]):
    """更新代理的使用时间"""
    if proxy_id:
        crud.update_proxy_last_used(db, proxy_id)


# ============== Pydantic Models ==============

class RegistrationTaskCreate(BaseModel):
    """创建注册任务请求"""
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []  # 指定 CPA 服务 ID 列表，空则取第一个启用的
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []  # 指定 Sub2API 服务 ID 列表
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []  # 指定 TM 服务 ID 列表


class BatchRegistrationRequest(BaseModel):
    """批量注册请求"""
    count: int = 1
    email_service_type: str = "tempmail"
    proxy: Optional[str] = None
    email_service_config: Optional[dict] = None
    email_service_id: Optional[int] = None
    interval_min: int = 5
    interval_max: int = 30
    concurrency: int = 1
    mode: str = "pipeline"
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []


class RegistrationTaskResponse(BaseModel):
    """注册任务响应"""
    id: int
    task_uuid: str
    status: str
    email_service_id: Optional[int] = None
    proxy: Optional[str] = None
    logs: Optional[str] = None
    result: Optional[dict] = None
    error_message: Optional[str] = None
    effective_scheme: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class BatchRegistrationResponse(BaseModel):
    """批量注册响应"""
    batch_id: str
    count: int
    tasks: List[RegistrationTaskResponse]


class TaskListResponse(BaseModel):
    """任务列表响应"""
    total: int
    tasks: List[RegistrationTaskResponse]


# ============== Outlook 批量注册模型 ==============

class OutlookAccountForRegistration(BaseModel):
    """可用于注册的 Outlook 账户"""
    id: int                      # EmailService 表的 ID
    email: str
    name: str
    has_oauth: bool              # 是否有 OAuth 配置
    is_registered: bool          # 是否已注册
    registered_account_id: Optional[int] = None


class OutlookAccountsListResponse(BaseModel):
    """Outlook 账户列表响应"""
    total: int
    registered_count: int        # 已注册数量
    unregistered_count: int      # 未注册数量
    accounts: List[OutlookAccountForRegistration]


class OutlookBatchRegistrationRequest(BaseModel):
    """Outlook 批量注册请求"""
    service_ids: List[int]
    skip_registered: bool = True
    proxy: Optional[str] = None
    interval_min: int = 5
    interval_max: int = 30
    concurrency: int = 1
    mode: str = "pipeline"
    auto_upload_cpa: bool = False
    cpa_service_ids: List[int] = []
    auto_upload_sub2api: bool = False
    sub2api_service_ids: List[int] = []
    auto_upload_tm: bool = False
    tm_service_ids: List[int] = []


class OutlookBatchRegistrationResponse(BaseModel):
    """Outlook 批量注册响应"""
    batch_id: str
    total: int                   # 总数
    skipped: int                 # 跳过数（已注册）
    to_register: int             # 待注册数
    service_ids: List[int]       # 实际要注册的服务 ID


# ============== Helper Functions ==============

def task_to_response(task: RegistrationTask) -> RegistrationTaskResponse:
    """转换任务模型为响应"""
    result = task.result if isinstance(task.result, dict) else {}
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    effective_scheme = None
    if isinstance(metadata, dict):
        effective_scheme = metadata.get("registration_scheme_label_effective") or metadata.get("registration_scheme_label")

    return RegistrationTaskResponse(
        id=task.id,
        task_uuid=task.task_uuid,
        status=task.status,
        email_service_id=task.email_service_id,
        proxy=task.proxy,
        logs=task.logs,
        result=task.result,
        error_message=task.error_message,
        effective_scheme=effective_scheme,
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


def _playwright_diagnosis_label(category: str) -> str:
    mapping = {
        "risk_challenge": "风控挑战",
        "not_signed_in": "未进入登录态",
        "access_token_missing": "Access 缺失",
        "session_token_missing": "Session 缺失",
        "browser_capture_miss": "浏览器侧未命中",
        "browser_ok_token_gap": "已进应用但取 Token 失败",
        "unknown": "待进一步排查",
    }
    return mapping.get(str(category or "unknown"), "待进一步排查")


def _playwright_recommended_action(category: str) -> tuple[str, str]:
    mapping = {
        "risk_challenge": ("rotate_proxy_and_retry", "建议更换代理/出口环境后再重试，暂不建议原环境立即连跑"),
        "not_signed_in": ("check_session_bootstrap", "建议优先检查 cookie/session 注入与 callback 落地，再决定是否重跑整条注册"),
        "access_token_missing": ("retry_token_capture_only", "建议优先只重试 token 提取链路，不必立即重跑完整注册"),
        "session_token_missing": ("retry_session_backfill", "建议优先重试 session 补齐链路，重点看 browser retry 和 signin bridge"),
        "browser_capture_miss": ("inspect_screenshot_then_retry", "建议先看失败截图确认最终页面，再决定换代理还是整条重跑"),
        "browser_ok_token_gap": ("retry_token_capture_only", "页面已进应用态，建议优先只重试 token/session 提取链路"),
        "unknown": ("manual_review", "建议结合截图、页面状态和日志做人工复核后再决定动作"),
    }
    return mapping.get(str(category or "unknown"), ("manual_review", "建议结合截图、页面状态和日志做人工复核后再决定动作"))


def _playwright_strategy_flags(recommended_action: str) -> dict:
    action = str(recommended_action or "manual_review").strip()
    return {
        "safe_retry_same_env": action in {"retry_token_capture_only", "retry_session_backfill"},
        "should_rotate_proxy": action == "rotate_proxy_and_retry",
        "prefer_token_only_retry": action == "retry_token_capture_only",
        "prefer_session_only_retry": action == "retry_session_backfill",
        "needs_manual_review": action in {"inspect_screenshot_then_retry", "manual_review", "check_session_bootstrap"},
    }


def _classify_playwright_diagnosis(diagnostics: dict, browser_probe_summary: Optional[dict]) -> tuple[str, str, str]:
    diagnosis_category = "unknown"
    diagnosis_hint = "需要结合阶段、页面状态和截图继续排查"
    page_state = str((browser_probe_summary or {}).get("page_state") or "").strip().lower()
    failure_reason = str(diagnostics.get("failure_reason") or "").strip().lower()
    stage = str(diagnostics.get("stage") or "").strip().lower()
    has_access_token = bool(diagnostics.get("has_access_token"))
    has_session_token = bool(diagnostics.get("has_session_token"))

    if page_state in {"challenge_page", "challenge", "arkose", "captcha"}:
        diagnosis_category = "risk_challenge"
        diagnosis_hint = "疑似命中风控挑战页，优先检查代理质量、浏览器指纹和注册频率"
    elif page_state in {"guest_home", "logged_out_home"}:
        diagnosis_category = "not_signed_in"
        diagnosis_hint = "疑似未真正进入登录态，优先检查 session cookie 注入和 callback 落地情况"
    elif failure_reason == "access_token_missing":
        diagnosis_category = "access_token_missing"
        diagnosis_hint = "浏览器已执行收尾，但 access_token 仍未拿到，重点看 session 接口和侧信道提取"
    elif failure_reason == "session_token_missing":
        diagnosis_category = "session_token_missing"
        diagnosis_hint = "access_token 已有但 session_token 未补齐，重点看 browser retry 和 signin bridge"
    elif stage in {"native_backfill_miss", "browser_side_channel_miss", "browser_session_retry_miss"}:
        diagnosis_category = "browser_capture_miss"
        diagnosis_hint = "浏览器态和回退链路都未稳定命中，可优先结合截图确认页面最终落点"
    elif page_state in {"app_home", "workspace_home"} and (not has_access_token or not has_session_token):
        diagnosis_category = "browser_ok_token_gap"
        diagnosis_hint = "页面已经进入应用态，更可能是 token/session 提取链路问题而不是页面登录问题"

    return diagnosis_category, _playwright_diagnosis_label(diagnosis_category), diagnosis_hint


def _extract_playwright_summary(result: dict) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        return None
    diagnostics = metadata.get("playwright_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    post_failure_strategy = metadata.get("playwright_post_failure_strategy") if isinstance(metadata.get("playwright_post_failure_strategy"), dict) else None
    browser_probe = diagnostics.get("browser_probe") if isinstance(diagnostics.get("browser_probe"), dict) else None
    browser_probe_summary = None
    if browser_probe:
        browser_probe_summary = {
            "proxy": browser_probe.get("proxy"),
            "ipify_before": browser_probe.get("ipify_before"),
            "chatgpt_title": browser_probe.get("chatgpt_title"),
            "chatgpt_url": browser_probe.get("chatgpt_url"),
            "page_state": browser_probe.get("page_state"),
            "chatgpt_body_hint": browser_probe.get("chatgpt_body_hint"),
            "fingerprint_profile_id": browser_probe.get("fingerprint_profile_id"),
            "method": browser_probe.get("method"),
            "hit": browser_probe.get("hit"),
            "source": browser_probe.get("source"),
        }
    diagnosis_category, diagnosis_label, diagnosis_hint = _classify_playwright_diagnosis(diagnostics, browser_probe_summary)
    recommended_action, recommended_action_hint = _playwright_recommended_action(diagnosis_category)
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    proxy_used = str((metadata or {}).get("proxy_used") or "").strip() if isinstance(metadata, dict) else ""
    if not proxy_used and recommended_action == "rotate_proxy_and_retry":
        recommended_action = "manual_review"
        recommended_action_hint = "当前为直连环境，无法通过换代理规避，建议降频并优先人工复核"
    strategy_flags = _playwright_strategy_flags(recommended_action)
    return {
        "stage": diagnostics.get("stage"),
        "strategy": diagnostics.get("strategy"),
        "failure_reason": diagnostics.get("failure_reason"),
        "diagnosis_category": diagnosis_category,
        "diagnosis_label": diagnosis_label,
        "diagnosis_hint": diagnosis_hint,
        "recommended_action": recommended_action,
        "recommended_action_hint": recommended_action_hint,
        "strategy_flags": strategy_flags,
        "post_failure_strategy": post_failure_strategy,
        "next_run_policy": (post_failure_strategy or {}).get("next_run_policy") if isinstance(post_failure_strategy, dict) else None,
        "callback_url": diagnostics.get("callback_url"),
        "current_url": diagnostics.get("current_url"),
        "callback_candidate": diagnostics.get("callback_candidate"),
        "has_session_token": bool(diagnostics.get("has_session_token")),
        "has_access_token": bool(diagnostics.get("has_access_token")),
        "has_refresh_token": bool(diagnostics.get("has_refresh_token")),
        "used_native_backfill": bool(diagnostics.get("used_native_backfill")),
        "used_browser_retry": bool(diagnostics.get("used_browser_retry")),
        "used_signin_bridge": bool(diagnostics.get("used_signin_bridge")),
        "artifact": diagnostics.get("artifact") if isinstance(diagnostics.get("artifact"), dict) else None,
        "browser_probe": browser_probe_summary,
    }


def _build_playwright_stats(tasks: List[RegistrationTask], limit: int = 50) -> dict:
    diagnosis_counts: Dict[str, int] = {}
    rotate_proxy_count = 0
    fresh_fingerprint_count = 0
    throttle_count = 0
    samples_total = 0
    samples_failed = 0

    for task in tasks[:limit]:
        result = task.result if isinstance(getattr(task, "result", None), dict) else None
        if not result:
            continue
        summary = _extract_playwright_summary(result)
        if not summary:
            continue
        samples_total += 1
        task_status = str(getattr(task, "status", "") or "").strip().lower()
        result_success = result.get("success") if isinstance(result, dict) else None
        if task_status != "failed" and result_success is not False:
            continue
        samples_failed += 1
        diagnosis = str(summary.get("diagnosis_label") or summary.get("diagnosis_category") or "未知")
        diagnosis_counts[diagnosis] = diagnosis_counts.get(diagnosis, 0) + 1
        strategy = summary.get("post_failure_strategy") if isinstance(summary.get("post_failure_strategy"), dict) else {}
        next_run_policy = summary.get("next_run_policy") if isinstance(summary.get("next_run_policy"), dict) else {}
        if bool(strategy.get("should_rotate_proxy")) or bool(next_run_policy.get("rotate_proxy_before_retry")):
            rotate_proxy_count += 1
        if bool(next_run_policy.get("prefer_fresh_fingerprint")):
            fresh_fingerprint_count += 1
        if bool(strategy.get("needs_manual_review")):
            throttle_count += 1

    top_diagnosis = sorted(diagnosis_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return {
        "samples": samples_failed,
        "samples_total": samples_total,
        "samples_failed": samples_failed,
        "top_diagnosis": [{"label": label, "count": count} for label, count in top_diagnosis],
        "rotate_proxy_count": rotate_proxy_count,
        "fresh_fingerprint_count": fresh_fingerprint_count,
        "throttle_count": throttle_count,
    }


def _build_playwright_alerts(stats: dict) -> dict:
    samples = int(stats.get("samples") or 0)
    top = list(stats.get("top_diagnosis") or [])
    challenge_count = 0
    for item in top:
        if str(item.get("label") or "") == "风控挑战":
            challenge_count = int(item.get("count") or 0)
            break

    alerts = []
    if samples >= 5 and challenge_count * 100 >= samples * 40:
        alerts.append("最近 Playwright 风控挑战占比较高，建议降低频率并优先更换代理/指纹")
    if samples >= 5 and int(stats.get("throttle_count") or 0) * 100 >= samples * 30:
        alerts.append("最近节流触发较多，说明高风险或需人工复核场景增多")
    if samples >= 5 and int(stats.get("rotate_proxy_count") or 0) == 0 and int(stats.get("fresh_fingerprint_count") or 0) > 0:
        alerts.append("近期 Playwright 更偏直连/无代理环境，请重点关注直连风险与人工复核")

    return {
        "active": bool(alerts),
        "messages": alerts,
    }


def _append_playwright_diagnosis_log(result_dict: dict, log_callback) -> dict:
    summary = _extract_playwright_summary(result_dict)
    if not summary:
        return result_dict
    diagnosis_label = str(summary.get("diagnosis_label") or "待进一步排查").strip()
    diagnosis_hint = str(summary.get("diagnosis_hint") or "").strip()
    recommended_action = str(summary.get("recommended_action") or "manual_review").strip()
    recommended_action_hint = str(summary.get("recommended_action_hint") or "").strip()
    stage = str(summary.get("stage") or "-").strip()
    final_line = f"[Playwright 诊断] {diagnosis_label} | stage={stage}"
    if diagnosis_hint:
        final_line = f"{final_line} | {diagnosis_hint}"
    if recommended_action_hint:
        final_line = f"{final_line} | action={recommended_action} | {recommended_action_hint}"
    try:
        log_callback(final_line)
    except Exception:
        pass
    metadata = dict((result_dict or {}).get("metadata") or {})
    metadata["playwright_diagnosis_summary"] = {
        "label": diagnosis_label,
        "category": summary.get("diagnosis_category"),
        "hint": diagnosis_hint,
        "stage": stage,
        "recommended_action": recommended_action,
        "recommended_action_hint": recommended_action_hint,
        "strategy_flags": dict(summary.get("strategy_flags") or {}),
    }
    updated = dict(result_dict or {})
    updated["metadata"] = metadata
    return updated


def _build_playwright_post_failure_strategy_summary(result_dict: dict) -> Optional[dict]:
    summary = _extract_playwright_summary(result_dict)
    if not summary:
        return None
    flags = dict(summary.get("strategy_flags") or {})
    if not flags:
        return None

    strategy = {
        "retry_scope": "manual_review",
        "should_rotate_proxy": bool(flags.get("should_rotate_proxy")),
        "safe_retry_same_env": bool(flags.get("safe_retry_same_env")),
        "needs_manual_review": bool(flags.get("needs_manual_review")),
        "note": str(summary.get("recommended_action_hint") or "").strip(),
        "next_run_policy": {
            "fresh_browser_context": True,
            "reuse_browser_storage": False,
            "isolate_task_cookies": True,
            "prefer_fresh_fingerprint": True,
            "rotate_proxy_before_retry": bool(flags.get("should_rotate_proxy")),
        },
    }
    metadata = dict((result_dict or {}).get("metadata") or {})
    proxy_used = str(metadata.get("proxy_used") or "").strip()
    if not proxy_used:
        strategy["should_rotate_proxy"] = False
        strategy["next_run_policy"]["rotate_proxy_before_retry"] = False
        strategy["note"] = (strategy["note"] + "；当前为直连环境，无法通过换代理规避，建议降频并优先人工复核").strip("；")
    if bool(flags.get("prefer_token_only_retry")):
        strategy["retry_scope"] = "token_only"
    elif bool(flags.get("prefer_session_only_retry")):
        strategy["retry_scope"] = "session_only"
    elif bool(flags.get("should_rotate_proxy")):
        strategy["retry_scope"] = "full_retry_with_new_proxy"
    elif bool(flags.get("safe_retry_same_env")):
        strategy["retry_scope"] = "light_retry_same_env"

    return strategy


def _log_playwright_post_failure_strategy(
    result_dict: dict,
    *,
    log_callback,
    batch_id: str = "",
) -> dict:
    strategy = _build_playwright_post_failure_strategy_summary(result_dict)
    if not strategy:
        return result_dict

    retry_scope = str(strategy.get("retry_scope") or "manual_review")
    note = str(strategy.get("note") or "").strip()
    line = f"[Playwright 后续动作] retry_scope={retry_scope}"
    if strategy.get("should_rotate_proxy"):
        line += " | should_rotate_proxy=true"
    if strategy.get("safe_retry_same_env"):
        line += " | safe_retry_same_env=true"
    if strategy.get("needs_manual_review"):
        line += " | needs_manual_review=true"
    if note:
        line += f" | {note}"
    next_run_policy = dict(strategy.get("next_run_policy") or {})
    if next_run_policy:
        line += " | next=fresh_context"
        if next_run_policy.get("rotate_proxy_before_retry"):
            line += ",rotate_proxy"
        if next_run_policy.get("prefer_fresh_fingerprint"):
            line += ",fresh_fingerprint"
        if next_run_policy.get("isolate_task_cookies"):
            line += ",isolated_cookies"
    try:
        log_callback(line)
    except Exception:
        pass
    if batch_id:
        try:
            add_auto_registration_log(f"[自动注册策略] {line}")
        except Exception:
            pass

    metadata = dict((result_dict or {}).get("metadata") or {})
    metadata["playwright_post_failure_strategy"] = strategy
    updated = dict(result_dict or {})
    updated["metadata"] = metadata
    return updated


def _resolve_playwright_artifact_path(relative_path: str) -> Path:
    requested = str(relative_path or "").strip().replace("\\", "/")
    if not requested:
        raise HTTPException(status_code=400, detail="artifact_path 不能为空")
    if not requested.startswith("playwright-artifacts/"):
        raise HTTPException(status_code=400, detail="artifact_path 非法")

    base_dir = (get_data_dir() / "playwright-artifacts").resolve()
    target = (get_data_dir() / requested).resolve()
    if base_dir not in target.parents and target != base_dir:
        raise HTTPException(status_code=400, detail="artifact_path 非法")
    if target.suffix.lower() != ".png":
        raise HTTPException(status_code=400, detail="仅支持下载 PNG artifact")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="artifact 不存在")
    return target


def _normalize_email_service_config(
    service_type: EmailServiceType,
    config: Optional[dict],
    proxy_url: Optional[str] = None
) -> dict:
    """按服务类型兼容旧字段名，避免不同服务的配置键互相污染。"""
    normalized = config.copy() if config else {}

    if 'api_url' in normalized and 'base_url' not in normalized:
        normalized['base_url'] = normalized.pop('api_url')

    if service_type == EmailServiceType.MOE_MAIL:
        if 'domain' in normalized and 'default_domain' not in normalized:
            normalized['default_domain'] = normalized.pop('domain')
    elif service_type == EmailServiceType.YYDS_MAIL:
        if 'domain' in normalized and 'default_domain' not in normalized:
            normalized['default_domain'] = normalized.pop('domain')
    elif service_type in (EmailServiceType.TEMP_MAIL, EmailServiceType.FREEMAIL, EmailServiceType.CODEX_OTP, EmailServiceType.CODEX_OTP_D1):
        if 'default_domain' in normalized and 'domain' not in normalized:
            normalized['domain'] = normalized.pop('default_domain')
    elif service_type == EmailServiceType.DUCK_MAIL:
        if 'domain' in normalized and 'default_domain' not in normalized:
            normalized['default_domain'] = normalized.pop('domain')

    if proxy_url and 'proxy_url' not in normalized:
        normalized['proxy_url'] = proxy_url

    return normalized


def _run_sync_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None, log_prefix: str = "", batch_id: str = "", auto_upload_cpa: bool = False, cpa_service_ids: List[int] = None, auto_upload_sub2api: bool = False, sub2api_service_ids: List[int] = None, auto_upload_tm: bool = False, tm_service_ids: List[int] = None):
    """
    在线程池中执行的同步注册任务

    这个函数会被 run_in_executor 调用，运行在独立线程中
    """
    with get_db() as db:
        try:
            # 检查是否已取消
            if task_manager.is_cancelled(task_uuid):
                logger.info(f"任务 {task_uuid} 已取消，跳过执行")
                return

            # 更新任务状态为运行中
            task = crud.update_registration_task(
                db, task_uuid,
                status="running",
                started_at=utcnow_naive()
            )

            if not task:
                logger.error(f"任务不存在: {task_uuid}")
                return

            # 更新 TaskManager 状态
            task_manager.update_status(task_uuid, "running")

            # 确定使用的代理
            # 如果前端传入了代理参数，使用传入的
            # 否则从代理列表或系统设置中获取
            actual_proxy_url = proxy
            proxy_id = None
            execution_overrides = _load_task_execution_overrides(task)

            if not actual_proxy_url:
                actual_proxy_url, proxy_id = get_proxy_for_registration_with_exclusions(
                    db,
                    excluded_proxy_urls=execution_overrides.get("excluded_proxy_urls") or [],
                )
                if actual_proxy_url:
                    logger.info(f"任务 {task_uuid} 使用代理: {actual_proxy_url[:50]}...")
                    if execution_overrides.get("excluded_proxy_urls"):
                        logger.info("任务 %s 已避开最近失败代理，改用新代理", task_uuid)

            # 更新任务的代理记录
            crud.update_registration_task(db, task_uuid, proxy=actual_proxy_url)

            # 创建邮箱服务
            service_type = EmailServiceType(email_service_type)
            settings = get_settings()

            # 优先使用数据库中配置的邮箱服务
            if email_service_id:
                from ...database.models import EmailService as EmailServiceModel
                db_service = db.query(EmailServiceModel).filter(
                    EmailServiceModel.id == email_service_id,
                    EmailServiceModel.enabled == True
                ).first()

                if db_service:
                    service_type = EmailServiceType(db_service.service_type)
                    config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                    # 更新任务关联的邮箱服务
                    crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                    logger.info(f"使用数据库邮箱服务: {db_service.name} (ID: {db_service.id}, 类型: {service_type.value})")
                else:
                    raise ValueError(f"邮箱服务不存在或已禁用: {email_service_id}")
            else:
                # 使用默认配置或传入的配置
                if email_service_config:
                    config = _normalize_email_service_config(service_type, email_service_config, actual_proxy_url)
                elif service_type == EmailServiceType.TEMPMAIL:
                    if not settings.tempmail_enabled:
                        raise ValueError("Tempmail.lol 渠道已禁用，请先在邮箱服务页面启用")
                    config = {
                        "base_url": settings.tempmail_base_url,
                        "timeout": settings.tempmail_timeout,
                        "max_retries": settings.tempmail_max_retries,
                        "proxy_url": actual_proxy_url,
                    }
                elif service_type == EmailServiceType.YYDS_MAIL:
                    api_key = settings.yyds_mail_api_key.get_secret_value() if settings.yyds_mail_api_key else ""
                    if not settings.yyds_mail_enabled or not api_key:
                        raise ValueError("YYDS Mail 渠道未启用或未配置 API Key，请先在邮箱服务页面配置")
                    config = {
                        "base_url": settings.yyds_mail_base_url,
                        "api_key": api_key,
                        "default_domain": settings.yyds_mail_default_domain,
                        "timeout": settings.yyds_mail_timeout,
                        "max_retries": settings.yyds_mail_max_retries,
                        "proxy_url": actual_proxy_url,
                    }
                elif service_type == EmailServiceType.MOE_MAIL:
                    # 检查数据库中是否有可用的自定义域名服务
                    from ...database.models import EmailService as EmailServiceModel
                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "moe_mail",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库自定义域名服务: {db_service.name}")
                    elif settings.custom_domain_base_url and settings.custom_domain_api_key:
                        config = {
                            "base_url": settings.custom_domain_base_url,
                            "api_key": settings.custom_domain_api_key.get_secret_value() if settings.custom_domain_api_key else "",
                            "proxy_url": actual_proxy_url,
                        }
                    else:
                        raise ValueError("没有可用的自定义域名邮箱服务，请先在设置中配置")
                elif service_type == EmailServiceType.OUTLOOK:
                    # 检查数据库中是否有可用的 Outlook 账户
                    from ...database.models import EmailService as EmailServiceModel, Account
                    # 获取所有启用的 Outlook 服务
                    outlook_services = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "outlook",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).all()

                    if not outlook_services:
                        raise ValueError("没有可用的 Outlook 账户，请先在设置中导入账户")

                    # 找到一个未注册的 Outlook 账户
                    selected_service = None
                    for svc in outlook_services:
                        email = svc.config.get("email") if svc.config else None
                        if not email:
                            continue
                        # 检查是否已在 accounts 表中注册
                        existing = db.query(Account).filter(Account.email == email).first()
                        if not existing:
                            selected_service = svc
                            logger.info(f"选择未注册的 Outlook 账户: {email}")
                            break
                        else:
                            logger.info(f"跳过已注册的 Outlook 账户: {email}")

                    if selected_service and selected_service.config:
                        config = selected_service.config.copy()
                        crud.update_registration_task(db, task_uuid, email_service_id=selected_service.id)
                        logger.info(f"使用数据库 Outlook 账户: {selected_service.name}")
                    else:
                        raise ValueError("所有 Outlook 账户都已注册过 OpenAI 账号，请添加新的 Outlook 账户")
                elif service_type == EmailServiceType.DUCK_MAIL:
                    from ...database.models import EmailService as EmailServiceModel

                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "duck_mail",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库 DuckMail 服务: {db_service.name}")
                    else:
                        raise ValueError("没有可用的 DuckMail 邮箱服务，请先在邮箱服务页面添加服务")
                elif service_type == EmailServiceType.FREEMAIL:
                    from ...database.models import EmailService as EmailServiceModel

                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "freemail",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库 Freemail 服务: {db_service.name}")
                    else:
                        raise ValueError("没有可用的 Freemail 邮箱服务，请先在邮箱服务页面添加服务")
                elif service_type == EmailServiceType.IMAP_MAIL:
                    from ...database.models import EmailService as EmailServiceModel

                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "imap_mail",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库 IMAP 邮箱服务: {db_service.name}")
                    else:
                        raise ValueError("没有可用的 IMAP 邮箱服务，请先在邮箱服务中添加")
                elif service_type == EmailServiceType.CODEX_OTP:
                    from ...database.models import EmailService as EmailServiceModel

                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "codex_otp",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库 Codex OTP 服务: {db_service.name}")
                    elif settings.codex_otp_enabled and settings.codex_otp_base_url and settings.codex_otp_admin_token:
                        config = {
                            "base_url": settings.codex_otp_base_url,
                            "admin_token": settings.codex_otp_admin_token.get_secret_value() if settings.codex_otp_admin_token else "",
                            "custom_auth": settings.codex_otp_custom_auth.get_secret_value() if settings.codex_otp_custom_auth else "",
                            "domain": settings.codex_otp_domain,
                            "timeout": settings.codex_otp_timeout,
                            "max_retries": settings.codex_otp_max_retries,
                            "poll_interval": settings.codex_otp_poll_interval,
                            "ttl_seconds": settings.codex_otp_ttl_seconds,
                            "proxy_url": actual_proxy_url,
                        }
                    else:
                        raise ValueError("没有可用的 Codex OTP 服务，请先在邮箱服务中添加或完成初始化")
                elif service_type == EmailServiceType.CODEX_OTP_D1:
                    from ...database.models import EmailService as EmailServiceModel

                    db_service = db.query(EmailServiceModel).filter(
                        EmailServiceModel.service_type == "codex_otp_d1",
                        EmailServiceModel.enabled == True
                    ).order_by(EmailServiceModel.priority.asc()).first()

                    if db_service and db_service.config:
                        config = _normalize_email_service_config(service_type, db_service.config, actual_proxy_url)
                        crud.update_registration_task(db, task_uuid, email_service_id=db_service.id)
                        logger.info(f"使用数据库 Codex OTP D1 服务: {db_service.name}")
                    elif (
                        settings.codex_otp_d1_enabled
                        and settings.codex_otp_d1_domain
                        and settings.codex_otp_d1_cf_account_id
                        and settings.codex_otp_d1_cf_database_id
                        and settings.codex_otp_d1_cf_runtime_api_token
                    ):
                        config = {
                            "domain": settings.codex_otp_d1_domain,
                            "cf_account_id": settings.codex_otp_d1_cf_account_id,
                            "cf_database_id": settings.codex_otp_d1_cf_database_id,
                            "cf_runtime_api_token": settings.codex_otp_d1_cf_runtime_api_token.get_secret_value() if settings.codex_otp_d1_cf_runtime_api_token else "",
                            "timeout": settings.codex_otp_d1_timeout,
                            "poll_interval": settings.codex_otp_d1_poll_interval,
                            "proxy_url": actual_proxy_url,
                        }
                    else:
                        raise ValueError("没有可用的 Codex OTP D1 服务，请先在邮箱服务中添加或完成配置")
                else:
                    config = email_service_config or {}

            email_service = EmailServiceFactory.create(service_type, config)

            # 创建注册引擎 - 使用 TaskManager 的日志回调
            log_callback = task_manager.create_log_callback(task_uuid, prefix=log_prefix, batch_id=batch_id)
            if execution_overrides.get("prefer_fresh_fingerprint"):
                log_callback("[策略] 本轮任务已标记为 fresh_fingerprint，建议使用新的浏览器指纹配置")
            if execution_overrides.get("excluded_proxy_urls"):
                log_callback("[策略] 本轮任务已启用 rotate_proxy_before_retry，避免沿用最近失败代理")

            engine = RegistrationEngine(
                email_service=email_service,
                proxy_url=actual_proxy_url,
                callback_logger=log_callback,
                task_uuid=task_uuid,
                cancel_requested=lambda: task_manager.is_cancelled(task_uuid),
                fingerprint_profile_exclude_id=(execution_overrides.get("previous_fingerprint_profile_id") if execution_overrides.get("prefer_fresh_fingerprint") else None),
            )

            # 执行注册
            result = engine.run()

            if task_manager.is_cancelled(task_uuid):
                cancellation_message = result.error_message or "任务已取消"
                crud.update_registration_task(
                    db,
                    task_uuid,
                    status="cancelled",
                    completed_at=utcnow_naive(),
                    error_message=cancellation_message,
                )
                task_manager.update_status(task_uuid, "cancelled", error=cancellation_message)
                logger.info(f"注册任务已取消: {task_uuid}")
                return

            if result.success:
                # 更新代理使用时间
                update_proxy_usage(db, proxy_id)

                # 保存到数据库
                saved_ok = engine.save_to_database(result)
                if not saved_ok:
                    save_error = "注册成功但保存账号到数据库失败"
                    crud.update_registration_task(
                        db,
                        task_uuid,
                        status="failed",
                        completed_at=utcnow_naive(),
                        error_message=save_error,
                    )
                    task_manager.update_status(task_uuid, "failed", error=save_error)
                    logger.error(f"注册任务入库失败: {task_uuid}, 邮箱: {result.email}")
                    return

                # 自动上传到 CPA（可多服务）
                if auto_upload_cpa:
                    try:
                        from ...core.upload.cpa_upload import upload_to_cpa, generate_token_json
                        from ...database.models import Account as AccountModel
                        saved_account = db.query(AccountModel).filter_by(email=result.email).first()
                        if saved_account and saved_account.access_token:
                            token_data = generate_token_json(saved_account)
                            _cpa_ids = cpa_service_ids or []
                            if not _cpa_ids:
                                # 未指定则取所有启用的服务
                                _cpa_ids = [s.id for s in crud.get_cpa_services(db, enabled=True)]
                            if not _cpa_ids:
                                log_callback("[CPA] 无可用 CPA 服务，跳过上传")
                            for _sid in _cpa_ids:
                                try:
                                    _svc = crud.get_cpa_service_by_id(db, _sid)
                                    if not _svc:
                                        continue
                                    log_callback(f"[CPA] 正在把账号打包发往服务站: {_svc.name}")
                                    _ok, _msg = upload_to_cpa(token_data, api_url=_svc.api_url, api_token=_svc.api_token)
                                    if _ok:
                                        saved_account.cpa_uploaded = True
                                        saved_account.cpa_uploaded_at = utcnow_naive()
                                        db.commit()
                                        log_callback(f"[CPA] 投递成功，服务站已签收: {_svc.name}")
                                    else:
                                        log_callback(f"[CPA] 上传失败({_svc.name}): {_msg}")
                                except Exception as _e:
                                    log_callback(f"[CPA] 异常({_sid}): {_e}")
                    except Exception as cpa_err:
                        log_callback(f"[CPA] 上传异常: {cpa_err}")

                # 自动上传到 Sub2API（可多服务）
                if auto_upload_sub2api:
                    try:
                        from ...core.upload.sub2api_upload import upload_to_sub2api
                        from ...database.models import Account as AccountModel
                        saved_account = db.query(AccountModel).filter_by(email=result.email).first()
                        if saved_account and saved_account.access_token:
                            _s2a_ids = sub2api_service_ids or []
                            if not _s2a_ids:
                                _s2a_ids = [s.id for s in crud.get_sub2api_services(db, enabled=True)]
                            if not _s2a_ids:
                                log_callback("[Sub2API] 无可用 Sub2API 服务，跳过上传")
                            for _sid in _s2a_ids:
                                try:
                                    _svc = crud.get_sub2api_service_by_id(db, _sid)
                                    if not _svc:
                                        continue
                                    log_callback(f"[Sub2API] 正在把账号发往服务站: {_svc.name}")
                                    _ok, _msg = upload_to_sub2api([saved_account], _svc.api_url, _svc.api_key)
                                    log_callback(f"[Sub2API] {'成功' if _ok else '失败'}({_svc.name}): {_msg}")
                                except Exception as _e:
                                    log_callback(f"[Sub2API] 异常({_sid}): {_e}")
                    except Exception as s2a_err:
                        log_callback(f"[Sub2API] 上传异常: {s2a_err}")

                # 自动上传到 Team Manager（可多服务）
                if auto_upload_tm:
                    try:
                        from ...core.upload.team_manager_upload import upload_to_team_manager
                        from ...database.models import Account as AccountModel
                        saved_account = db.query(AccountModel).filter_by(email=result.email).first()
                        if saved_account and saved_account.access_token:
                            _tm_ids = tm_service_ids or []
                            if not _tm_ids:
                                _tm_ids = [s.id for s in crud.get_tm_services(db, enabled=True)]
                            if not _tm_ids:
                                log_callback("[TM] 无可用 Team Manager 服务，跳过上传")
                            for _sid in _tm_ids:
                                try:
                                    _svc = crud.get_tm_service_by_id(db, _sid)
                                    if not _svc:
                                        continue
                                    log_callback(f"[TM] 正在把账号发往服务站: {_svc.name}")
                                    _ok, _msg = upload_to_team_manager(saved_account, _svc.api_url, _svc.api_key)
                                    log_callback(f"[TM] {'成功' if _ok else '失败'}({_svc.name}): {_msg}")
                                except Exception as _e:
                                    log_callback(f"[TM] 异常({_sid}): {_e}")
                    except Exception as tm_err:
                        log_callback(f"[TM] 上传异常: {tm_err}")

                # 更新任务状态
                result_payload = _append_playwright_diagnosis_log(result.to_dict(), log_callback)
                crud.update_registration_task(
                    db, task_uuid,
                    status="completed",
                    completed_at=utcnow_naive(),
                    result=result_payload
                )
                invalidate_playwright_stats_cache()

                # 更新 TaskManager 状态
                effective_scheme = None
                if isinstance(result.metadata, dict):
                    effective_scheme = result.metadata.get("registration_scheme_label_effective") or result.metadata.get("registration_scheme_label")
                task_manager.update_status(task_uuid, "completed", email=result.email, effective_scheme=effective_scheme)

                logger.info(f"注册任务完成: {task_uuid}, 邮箱: {result.email}")
            else:
                result_payload = _append_playwright_diagnosis_log(result.to_dict(), log_callback)
                result_payload = _log_playwright_post_failure_strategy(
                    result_payload,
                    log_callback=log_callback,
                    batch_id=batch_id,
                )
                # 更新任务状态为失败
                crud.update_registration_task(
                    db, task_uuid,
                    status="failed",
                    completed_at=utcnow_naive(),
                    error_message=result.error_message,
                    result=result_payload,
                )
                invalidate_playwright_stats_cache()

                # 更新 TaskManager 状态
                task_manager.update_status(
                    task_uuid,
                    "failed",
                    error=result.error_message,
                    **_extract_followup_status_payload(result_payload),
                )

                logger.warning(f"注册任务失败: {task_uuid}, 原因: {result.error_message}")

        except Exception as e:
            logger.error(f"注册任务异常: {task_uuid}, 错误: {e}")

            try:
                with get_db() as db:
                    if task_manager.is_cancelled(task_uuid):
                        crud.update_registration_task(
                            db,
                            task_uuid,
                            status="cancelled",
                            completed_at=utcnow_naive(),
                            error_message=str(e) or "任务已取消",
                        )
                        task_manager.update_status(task_uuid, "cancelled", error=str(e) or "任务已取消")
                        return

                    crud.update_registration_task(
                        db, task_uuid,
                        status="failed",
                        completed_at=utcnow_naive(),
                        error_message=str(e)
                    )
                    invalidate_playwright_stats_cache()

                # 更新 TaskManager 状态
                task_manager.update_status(task_uuid, "failed", error=str(e))
            except:
                pass


async def run_registration_task(task_uuid: str, email_service_type: str, proxy: Optional[str], email_service_config: Optional[dict], email_service_id: Optional[int] = None, log_prefix: str = "", batch_id: str = "", auto_upload_cpa: bool = False, cpa_service_ids: List[int] = None, auto_upload_sub2api: bool = False, sub2api_service_ids: List[int] = None, auto_upload_tm: bool = False, tm_service_ids: List[int] = None):
    """
    异步执行注册任务

    使用 run_in_executor 将同步任务放入线程池执行，避免阻塞主事件循环
    """
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    # 初始化 TaskManager 状态
    task_manager.update_status(task_uuid, "pending")
    task_manager.add_log(task_uuid, f"{log_prefix} [系统] 任务 {task_uuid[:8]} 已加入队列" if log_prefix else f"[系统] 任务 {task_uuid[:8]} 已加入队列")

    try:
        # 在线程池中执行同步任务（传入 log_prefix 和 batch_id 供回调使用）
        await loop.run_in_executor(
            task_manager.executor,
            _run_sync_registration_task,
            task_uuid,
            email_service_type,
            proxy,
            email_service_config,
            email_service_id,
            log_prefix,
            batch_id,
            auto_upload_cpa,
            cpa_service_ids or [],
            auto_upload_sub2api,
            sub2api_service_ids or [],
            auto_upload_tm,
            tm_service_ids or [],
        )
    except Exception as e:
        logger.error(f"线程池执行异常: {task_uuid}, 错误: {e}")
        task_manager.add_log(task_uuid, f"[错误] 线程池执行异常: {str(e)}")
        task_manager.update_status(task_uuid, "failed", error=str(e))


def _init_batch_state(batch_id: str, task_uuids: List[str]):
    """初始化批量任务内存状态"""
    task_manager.init_batch(batch_id, len(task_uuids))
    batch_tasks[batch_id] = {
        "total": len(task_uuids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "cancelled": False,
        "task_uuids": task_uuids,
        "current_index": 0,
        "logs": [],
        "finished": False,
        "last_failure_strategy": None,
        "throttle_until": 0.0,
    }


def _make_batch_helpers(batch_id: str):
    """返回 add_batch_log 和 update_batch_status 辅助函数"""
    def add_batch_log(msg: str):
        batch_tasks[batch_id]["logs"].append(msg)
        task_manager.add_batch_log(batch_id, msg)

    def update_batch_status(**kwargs):
        for key, value in kwargs.items():
            if key in batch_tasks[batch_id]:
                batch_tasks[batch_id][key] = value
        task_manager.update_batch_status(batch_id, **kwargs)

    return add_batch_log, update_batch_status


def _summarize_next_run_policy(next_run_policy: Optional[dict]) -> str:
    if not isinstance(next_run_policy, dict):
        return ""
    labels = []
    if next_run_policy.get("fresh_browser_context"):
        labels.append("fresh_context")
    if next_run_policy.get("rotate_proxy_before_retry"):
        labels.append("rotate_proxy")
    if next_run_policy.get("prefer_fresh_fingerprint"):
        labels.append("fresh_fingerprint")
    if next_run_policy.get("isolate_task_cookies"):
        labels.append("isolated_cookies")
    if next_run_policy.get("reuse_browser_storage") is False:
        labels.append("no_storage_reuse")
    return " / ".join(labels)


def _capture_task_followup_strategy(task) -> Optional[dict]:
    result = task.result if isinstance(getattr(task, "result", None), dict) else {}
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    if not isinstance(metadata, dict):
        return None
    strategy = metadata.get("playwright_post_failure_strategy")
    if not isinstance(strategy, dict):
        return None
    return strategy


def _compute_followup_throttle_seconds(followup: Optional[dict]) -> int:
    if not isinstance(followup, dict):
        return 0
    if bool(followup.get("needs_manual_review")):
        return 45
    if bool(followup.get("should_rotate_proxy")):
        return 20
    return 0


def _apply_batch_throttle_window(batch_id: str, seconds: int) -> None:
    if seconds <= 0:
        return
    batch_tasks[batch_id]["throttle_until"] = max(
        float(batch_tasks[batch_id].get("throttle_until") or 0.0),
        time.time() + float(seconds),
    )


def _remaining_batch_throttle_seconds(batch_id: str) -> int:
    until = float(batch_tasks[batch_id].get("throttle_until") or 0.0)
    return max(0, int(round(until - time.time())))


def _extract_followup_status_payload(result_dict: dict) -> dict:
    metadata = dict((result_dict or {}).get("metadata") or {})
    strategy = metadata.get("playwright_post_failure_strategy")
    if not isinstance(strategy, dict):
        return {}
    next_run_policy = strategy.get("next_run_policy") if isinstance(strategy.get("next_run_policy"), dict) else {}
    return {
        "playwright_retry_scope": strategy.get("retry_scope"),
        "playwright_should_rotate_proxy": bool(strategy.get("should_rotate_proxy")),
        "playwright_safe_retry_same_env": bool(strategy.get("safe_retry_same_env")),
        "playwright_needs_manual_review": bool(strategy.get("needs_manual_review")),
        "playwright_next_run_policy": dict(next_run_policy),
    }


def _load_task_execution_overrides(task) -> dict:
    result = task.result if isinstance(getattr(task, "result", None), dict) else {}
    metadata = result.get("metadata") if isinstance(result, dict) else {}
    strategy = metadata.get("playwright_post_failure_strategy") if isinstance(metadata, dict) else None
    next_run_policy = strategy.get("next_run_policy") if isinstance(strategy, dict) else {}
    previous_fingerprint_profile_id = str(((metadata.get("playwright_diagnostics") or {}) if isinstance(metadata, dict) else {}).get("browser_probe", {}).get("fingerprint_profile_id") or "").strip()
    return {
        "rotate_proxy_before_retry": bool((next_run_policy or {}).get("rotate_proxy_before_retry")),
        "prefer_fresh_fingerprint": bool((next_run_policy or {}).get("prefer_fresh_fingerprint")),
        "excluded_proxy_urls": [str(task.proxy or "").strip()] if bool((next_run_policy or {}).get("rotate_proxy_before_retry")) and str(task.proxy or "").strip() else [],
        "previous_fingerprint_profile_id": previous_fingerprint_profile_id,
    }


async def _wait_for_batch_delay(batch_id: str, seconds: int) -> bool:
    remaining = max(0, int(seconds))
    while remaining > 0:
        if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id]["cancelled"]:
            return False
        await asyncio.sleep(min(0.5, remaining))
        remaining -= 0.5
    return True


def _mark_batch_tasks_cancelled(batch_id: str, task_uuids: List[str]) -> None:
    if not task_uuids:
        return

    terminal_statuses = {"completed", "failed", "cancelled"}
    task_statuses = {}
    with get_db() as db:
        for task_uuid in task_uuids:
            task = crud.get_registration_task(db, task_uuid)
            current_status = getattr(task, "status", None)
            task_statuses[task_uuid] = current_status
            if current_status not in terminal_statuses:
                crud.update_registration_task(db, task_uuid, status="cancelled")

    current_completed = batch_tasks[batch_id]["completed"]
    update_count = 0
    for task_uuid in task_uuids:
        if not task_manager.is_cancelled(task_uuid):
            task_manager.cancel_task(task_uuid)
        if task_statuses.get(task_uuid) not in terminal_statuses:
            update_count += 1

    batch_tasks[batch_id]["completed"] = current_completed + update_count
    task_manager.update_batch_status(batch_id, completed=batch_tasks[batch_id]["completed"])


async def run_batch_parallel(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    concurrency: int,
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """
    并行模式：所有任务同时提交，Semaphore 控制最大并发数
    """
    _init_batch_state(batch_id, task_uuids)
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    add_batch_log(f"[系统] 并行模式启动，并发数: {concurrency}，总任务: {len(task_uuids)}")

    async def _run_one(idx: int, uuid: str):
        prefix = f"[任务{idx + 1}]"
        async with semaphore:
            if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id]["cancelled"]:
                _mark_batch_tasks_cancelled(batch_id, [uuid])
                return
            extra_wait = _remaining_batch_throttle_seconds(batch_id)
            if extra_wait > 0:
                add_batch_log(f"{prefix} [节流] 并行模式检测到高风险场景，启动前额外等待 {extra_wait} 秒")
                if not await _wait_for_batch_delay(batch_id, extra_wait):
                    _mark_batch_tasks_cancelled(batch_id, [uuid])
                    return
            await run_registration_task(
                uuid, email_service_type, proxy, email_service_config, email_service_id,
                log_prefix=prefix, batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids or [],
            )
        with get_db() as db:
            t = crud.get_registration_task(db, uuid)
            if t:
                async with counter_lock:
                    new_completed = batch_tasks[batch_id]["completed"] + 1
                    new_success = batch_tasks[batch_id]["success"]
                    new_failed = batch_tasks[batch_id]["failed"]
                    if t.status == "completed":
                        new_success += 1
                        add_batch_log(f"{prefix} [成功] 注册成功")
                    elif t.status == "cancelled":
                        add_batch_log(f"{prefix} [取消] 注册已取消")
                    elif t.status == "failed":
                        new_failed += 1
                        add_batch_log(f"{prefix} [失败] 注册失败: {t.error_message}")
                        followup = _capture_task_followup_strategy(t)
                        if followup:
                            batch_tasks[batch_id]["last_failure_strategy"] = dict(followup)
                            _apply_batch_throttle_window(batch_id, _compute_followup_throttle_seconds(followup))
                            next_run = _summarize_next_run_policy(followup.get("next_run_policy"))
                            add_batch_log(
                                f"{prefix} [策略] 下一轮建议: retry_scope={followup.get('retry_scope') or 'manual_review'}"
                                + (f" | {next_run}" if next_run else "")
                            )
                    update_batch_status(completed=new_completed, success=new_success, failed=new_failed)

    try:
        await asyncio.gather(*[_run_one(i, u) for i, u in enumerate(task_uuids)], return_exceptions=True)
        if not task_manager.is_batch_cancelled(batch_id):
            add_batch_log(f"[完成] 批量任务完成！成功: {batch_tasks[batch_id]['success']}, 失败: {batch_tasks[batch_id]['failed']}")
            update_batch_status(finished=True, status="completed")
        else:
            add_batch_log("[取消] 批量任务已取消")
            update_batch_status(finished=True, status="cancelled")
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_pipeline(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int,
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """
    流水线模式：每隔 interval 秒启动一个新任务，Semaphore 限制最大并发数
    """
    _init_batch_state(batch_id, task_uuids)
    add_batch_log, update_batch_status = _make_batch_helpers(batch_id)
    semaphore = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    running_tasks_list = []
    add_batch_log(f"[系统] 流水线模式启动，并发数: {concurrency}，总任务: {len(task_uuids)}")

    async def _run_and_release(idx: int, uuid: str, pfx: str):
        try:
            await run_registration_task(
                uuid, email_service_type, proxy, email_service_config, email_service_id,
                log_prefix=pfx, batch_id=batch_id,
                auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids or [],
                auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids or [],
                auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids or [],
            )
            with get_db() as db:
                t = crud.get_registration_task(db, uuid)
                if t:
                    async with counter_lock:
                        new_completed = batch_tasks[batch_id]["completed"] + 1
                        new_success = batch_tasks[batch_id]["success"]
                        new_failed = batch_tasks[batch_id]["failed"]
                        if t.status == "completed":
                            new_success += 1
                            add_batch_log(f"{pfx} [成功] 注册成功")
                        elif t.status == "cancelled":
                            add_batch_log(f"{pfx} [取消] 注册已取消")
                        elif t.status == "failed":
                            new_failed += 1
                            add_batch_log(f"{pfx} [失败] 注册失败: {t.error_message}")
                            followup = _capture_task_followup_strategy(t)
                            if followup:
                                batch_tasks[batch_id]["last_failure_strategy"] = dict(followup)
                                next_run = _summarize_next_run_policy(followup.get("next_run_policy"))
                                add_batch_log(
                                    f"{pfx} [策略] 下一轮建议: retry_scope={followup.get('retry_scope') or 'manual_review'}"
                                    + (f" | {next_run}" if next_run else "")
                                )
                                _apply_batch_throttle_window(batch_id, _compute_followup_throttle_seconds(followup))
                        update_batch_status(completed=new_completed, success=new_success, failed=new_failed)
        finally:
            semaphore.release()

    try:
        for i, task_uuid in enumerate(task_uuids):
            if task_manager.is_batch_cancelled(batch_id) or batch_tasks[batch_id]["cancelled"]:
                _mark_batch_tasks_cancelled(batch_id, task_uuids[i:])
                add_batch_log("[取消] 批量任务已取消")
                update_batch_status(status="cancelled")
                break

            update_batch_status(current_index=i)
            await semaphore.acquire()
            prefix = f"[任务{i + 1}]"
            add_batch_log(f"{prefix} 开始注册...")
            t = asyncio.create_task(_run_and_release(i, task_uuid, prefix))
            running_tasks_list.append(t)

            if i < len(task_uuids) - 1 and not task_manager.is_batch_cancelled(batch_id):
                wait_time = random.randint(interval_min, interval_max)
                extra_wait = _remaining_batch_throttle_seconds(batch_id)
                if extra_wait > 0:
                    add_batch_log(f"{prefix} [节流] 检测到需要人工复核/高风险场景，下一任务额外延迟 {extra_wait} 秒")
                    wait_time += extra_wait
                logger.info(f"批量任务 {batch_id}: 等待 {wait_time} 秒后启动下一个任务")
                if not await _wait_for_batch_delay(batch_id, wait_time):
                    _mark_batch_tasks_cancelled(batch_id, task_uuids[i + 1:])
                    add_batch_log("[取消] 批量任务在等待下一个任务期间已取消")
                    update_batch_status(status="cancelled")
                    break

        if running_tasks_list:
            await asyncio.gather(*running_tasks_list, return_exceptions=True)

        if not task_manager.is_batch_cancelled(batch_id):
            add_batch_log(f"[完成] 批量任务完成！成功: {batch_tasks[batch_id]['success']}, 失败: {batch_tasks[batch_id]['failed']}")
            update_batch_status(finished=True, status="completed")
        else:
            update_batch_status(finished=True, status="cancelled")
    except Exception as e:
        logger.error(f"批量任务 {batch_id} 异常: {e}")
        add_batch_log(f"[错误] 批量任务异常: {str(e)}")
        update_batch_status(finished=True, status="failed")
    finally:
        batch_tasks[batch_id]["finished"] = True


async def run_batch_registration(
    batch_id: str,
    task_uuids: List[str],
    email_service_type: str,
    proxy: Optional[str],
    email_service_config: Optional[dict],
    email_service_id: Optional[int],
    interval_min: int,
    interval_max: int,
    concurrency: int = 1,
    mode: str = "pipeline",
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """根据 mode 分发到并行或流水线执行"""
    if mode == "parallel":
        await run_batch_parallel(
            batch_id, task_uuids, email_service_type, proxy,
            email_service_config, email_service_id, concurrency,
            auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids,
        )
    else:
        await run_batch_pipeline(
            batch_id, task_uuids, email_service_type, proxy,
            email_service_config, email_service_id,
            interval_min, interval_max, concurrency,
            auto_upload_cpa=auto_upload_cpa, cpa_service_ids=cpa_service_ids,
            auto_upload_sub2api=auto_upload_sub2api, sub2api_service_ids=sub2api_service_ids,
            auto_upload_tm=auto_upload_tm, tm_service_ids=tm_service_ids,
        )


async def run_auto_registration_batch(plan, settings: Settings) -> str:
    email_service_type = settings.registration_auto_email_service_type
    try:
        EmailServiceType(email_service_type)
    except ValueError as exc:
        raise ValueError(f"自动注册邮箱服务类型无效: {email_service_type}") from exc

    mode = settings.registration_auto_mode or "pipeline"
    if mode not in ("parallel", "pipeline"):
        raise ValueError(f"自动注册模式无效: {mode}")

    interval_min = max(0, int(settings.registration_auto_interval_min))
    interval_max = max(interval_min, int(settings.registration_auto_interval_max))
    concurrency = max(1, int(settings.registration_auto_concurrency))
    email_service_id = int(settings.registration_auto_email_service_id or 0) or None
    proxy = settings.registration_auto_proxy.strip() or None

    batch_id = str(uuid.uuid4())
    task_uuids = []

    with get_db() as db:
        for _ in range(plan.deficit):
            task_uuid = str(uuid.uuid4())
            crud.create_registration_task(
                db,
                task_uuid=task_uuid,
                proxy=proxy,
                email_service_id=email_service_id,
            )
            task_uuids.append(task_uuid)

    update_auto_registration_state(
        status="running",
        message=f"自动补货任务运行中: {batch_id}",
        current_batch_id=batch_id,
    )
    add_auto_registration_log(
        f"[自动注册] 已创建补货批量任务 {batch_id}，计划注册 {len(task_uuids)} 个账号"
    )
    add_auto_registration_log(
        "[自动注册] Playwright 失败后将优先遵循 fresh_context / fresh_fingerprint / isolated_cookies 策略，必要时提示换代理"
    )
    logger.info(
        "自动注册批量任务已创建: batch=%s, count=%s, cpa_service_id=%s",
        batch_id,
        len(task_uuids),
        plan.cpa_service_id,
    )

    await run_batch_registration(
        batch_id=batch_id,
        task_uuids=task_uuids,
        email_service_type=email_service_type,
        proxy=proxy,
        email_service_config=None,
        email_service_id=email_service_id,
        interval_min=interval_min,
        interval_max=interval_max,
        concurrency=concurrency,
        mode=mode,
        auto_upload_cpa=True,
        cpa_service_ids=[plan.cpa_service_id],
        auto_upload_sub2api=False,
        sub2api_service_ids=[],
        auto_upload_tm=False,
        tm_service_ids=[],
    )

    batch = batch_tasks.get(batch_id)
    if batch:
        batch_cancelled = bool(batch.get("cancelled"))
        current_auto_state = get_auto_registration_state()
        refreshed_inventory = await asyncio.to_thread(
            get_auto_registration_inventory, settings
        )
        refreshed_ready_count = (
            refreshed_inventory[0]
            if refreshed_inventory
            else current_auto_state.get("current_ready_count")
        )
        refreshed_target_count = (
            refreshed_inventory[1]
            if refreshed_inventory
            else max(1, int(settings.registration_auto_min_ready_auth_files or 1))
        )
        final_status = "cancelled" if batch_cancelled else "idle"
        final_message = (
            f"自动补货批量任务已取消: {batch_id}"
            if batch_cancelled
            else f"自动补货批量任务已完成: {batch_id}"
        )
        final_log_message = (
            f"[自动注册] 补货批量任务已取消：成功 {batch.get('success', 0)}，失败 {batch.get('failed', 0)}"
            if batch_cancelled
            else f"[自动注册] 补货批量任务已完成：成功 {batch.get('success', 0)}，失败 {batch.get('failed', 0)}"
        )
        update_auto_registration_state(
            status=final_status,
            message=final_message,
            current_batch_id=None,
            current_ready_count=refreshed_ready_count,
            target_ready_count=refreshed_target_count,
            last_checked_at=datetime.now(timezone.utc).isoformat(),
        )
        add_auto_registration_log(final_log_message)

    return batch_id


# ============== API Endpoints ==============

@router.post("/start", response_model=RegistrationTaskResponse)
async def start_registration(
    request: RegistrationTaskCreate,
    background_tasks: BackgroundTasks
):
    """
    启动注册任务

    - email_service_type: 邮箱服务类型 (tempmail, outlook, moe_mail)
    - proxy: 代理地址
    - email_service_config: 邮箱服务配置（outlook 需要提供账户信息）
    """
    # 验证邮箱服务类型
    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    # 创建任务
    task_uuid = str(uuid.uuid4())

    with get_db() as db:
        task = crud.create_registration_task(
            db,
            task_uuid=task_uuid,
            proxy=request.proxy
        )

    # 在后台运行注册任务
    background_tasks.add_task(
        run_registration_task,
        task_uuid,
        request.email_service_type,
        request.proxy,
        request.email_service_config,
        request.email_service_id,
        "",
        "",
        request.auto_upload_cpa,
        request.cpa_service_ids,
        request.auto_upload_sub2api,
        request.sub2api_service_ids,
        request.auto_upload_tm,
        request.tm_service_ids,
    )

    return task_to_response(task)


@router.post("/batch", response_model=BatchRegistrationResponse)
async def start_batch_registration(
    request: BatchRegistrationRequest,
    background_tasks: BackgroundTasks
):
    """
    启动批量注册任务

    - count: 注册数量 (1-1000)
    - email_service_type: 邮箱服务类型
    - proxy: 代理地址
    - interval_min: 最小间隔秒数
    - interval_max: 最大间隔秒数
    """
    # 验证参数
    if request.count < 1 or request.count > 1000:
        raise HTTPException(status_code=400, detail="注册数量必须在 1-1000 之间")

    try:
        EmailServiceType(request.email_service_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"无效的邮箱服务类型: {request.email_service_type}"
        )

    if request.interval_min < 0 or request.interval_max < request.interval_min:
        raise HTTPException(status_code=400, detail="间隔时间参数无效")

    if not 1 <= request.concurrency <= 50:
        raise HTTPException(status_code=400, detail="并发数必须在 1-50 之间")

    if request.mode not in ("parallel", "pipeline"):
        raise HTTPException(status_code=400, detail="模式必须为 parallel 或 pipeline")

    # 创建批量任务
    batch_id = str(uuid.uuid4())
    task_uuids = []

    with get_db() as db:
        for _ in range(request.count):
            task_uuid = str(uuid.uuid4())
            task = crud.create_registration_task(
                db,
                task_uuid=task_uuid,
                proxy=request.proxy
            )
            task_uuids.append(task_uuid)

    # 获取所有任务
    with get_db() as db:
        tasks = [crud.get_registration_task(db, uuid) for uuid in task_uuids]

    # 在后台运行批量注册
    background_tasks.add_task(
        run_batch_registration,
        batch_id,
        task_uuids,
        request.email_service_type,
        request.proxy,
        request.email_service_config,
        request.email_service_id,
        request.interval_min,
        request.interval_max,
        request.concurrency,
        request.mode,
        request.auto_upload_cpa,
        request.cpa_service_ids,
        request.auto_upload_sub2api,
        request.sub2api_service_ids,
        request.auto_upload_tm,
        request.tm_service_ids,
    )

    return BatchRegistrationResponse(
        batch_id=batch_id,
        count=request.count,
        tasks=[task_to_response(t) for t in tasks if t]
    )


@router.get("/batch/{batch_id}")
async def get_batch_status(batch_id: str):
    """获取批量任务状态"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "current_index": batch["current_index"],
        "cancelled": batch["cancelled"],
        "finished": batch.get("finished", False),
        "progress": f"{batch['completed']}/{batch['total']}"
    }


@router.get("/auto-monitor")
async def get_auto_registration_monitor():
    auto_state = get_auto_registration_state()
    current_batch_id = auto_state.get("current_batch_id")
    batch = batch_tasks.get(current_batch_id) if current_batch_id else None
    logs = get_auto_registration_logs().copy()
    def _tasks_provider():
        with get_db() as db:
            return db.query(RegistrationTask).order_by(desc(RegistrationTask.created_at)).limit(50).all()

    playwright_stats, playwright_alerts = get_cached_playwright_stats(
        _tasks_provider,
        stats_builder=_build_playwright_stats,
        alerts_builder=_build_playwright_alerts,
        ttl_seconds=15,
    )
    if batch and current_batch_id:
        logs.extend(task_manager.get_batch_logs(current_batch_id))

    return {
        **auto_state,
        "playwright": playwright_stats,
        "playwright_alerts": playwright_alerts,
        "logs": logs,
        "batch": {
            "batch_id": current_batch_id,
            "total": batch["total"],
            "completed": batch["completed"],
            "success": batch["success"],
            "failed": batch["failed"],
            "current_index": batch["current_index"],
            "cancelled": batch["cancelled"],
            "finished": batch.get("finished", False),
            "progress": f"{batch['completed']}/{batch['total']}",
        } if batch else None,
    }


@router.post("/batch/{batch_id}/cancel")
async def cancel_batch(batch_id: str):
    """取消批量任务"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    if batch.get("finished"):
        raise HTTPException(status_code=400, detail="批量任务已完成")

    batch["cancelled"] = True
    task_manager.cancel_batch(batch_id)
    _cancel_batch_tasks(batch_id)
    return {"success": True, "message": "批量任务取消请求已提交，正在让它们有序收工"}


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    """获取任务列表"""
    with get_db() as db:
        query = db.query(RegistrationTask)

        if status:
            query = query.filter(RegistrationTask.status == status)

        total = query.count()
        offset = (page - 1) * page_size
        tasks = query.order_by(RegistrationTask.created_at.desc()).offset(offset).limit(page_size).all()

        return TaskListResponse(
            total=total,
            tasks=[task_to_response(t) for t in tasks]
        )


@router.get("/tasks/{task_uuid}", response_model=RegistrationTaskResponse)
async def get_task(task_uuid: str):
    """获取任务详情"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task_to_response(task)


@router.get("/tasks/{task_uuid}/logs")
async def get_task_logs(task_uuid: str):
    """获取任务日志"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        logs = task.logs or ""
        result = task.result if isinstance(task.result, dict) else {}
        metadata = result.get("metadata") if isinstance(result, dict) and isinstance(result.get("metadata"), dict) else {}
        email = result.get("email")
        service_type = task.email_service.service_type if task.email_service else None
        return {
            "task_uuid": task_uuid,
            "status": task.status,
            "email": email,
            "email_service": service_type,
            "effective_scheme": metadata.get("registration_scheme_label_effective") or metadata.get("registration_scheme_label"),
            "playwright": _extract_playwright_summary(result),
            "logs": logs.split("\n") if logs else []
        }


@router.get("/artifacts/playwright")
async def download_playwright_artifact(path: str = Query(..., description="相对 data 目录的 artifact 路径")):
    target = _resolve_playwright_artifact_path(path)
    return FileResponse(path=str(target), media_type="image/png", filename=target.name)


@router.post("/tasks/{task_uuid}/cancel")
async def cancel_task(task_uuid: str):
    """取消任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status not in ["pending", "running"]:
            raise HTTPException(status_code=400, detail="任务已完成或已取消")

        task = crud.update_registration_task(db, task_uuid, status="cancelled")
        task_manager.cancel_task(task_uuid)

        return {"success": True, "message": "任务已取消"}


@router.delete("/tasks/{task_uuid}")
async def delete_task(task_uuid: str):
    """删除任务"""
    with get_db() as db:
        task = crud.get_registration_task(db, task_uuid)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task.status == "running":
            raise HTTPException(status_code=400, detail="无法删除运行中的任务")

        crud.delete_registration_task(db, task_uuid)

        return {"success": True, "message": "任务已删除"}


@router.get("/stats")
async def get_registration_stats():
    """获取注册统计信息"""
    with get_db() as db:
        # 按状态统计
        status_stats = db.query(
            RegistrationTask.status,
            func.count(RegistrationTask.id)
        ).group_by(RegistrationTask.status).all()

        # 今日统计
        today = utcnow_naive().date()
        today_status_stats = db.query(
            RegistrationTask.status,
            func.count(RegistrationTask.id)
        ).filter(
            func.date(RegistrationTask.created_at) == today
        ).group_by(RegistrationTask.status).all()

        today_count = db.query(func.count(RegistrationTask.id)).filter(
            func.date(RegistrationTask.created_at) == today
        ).scalar()

        today_by_status = {status: count for status, count in today_status_stats}
        today_success = int(today_by_status.get("completed", 0))
        today_failed = int(today_by_status.get("failed", 0))
        today_total = int(today_count or 0)
        today_success_rate = round((today_success / today_total) * 100, 1) if today_total > 0 else 0.0

        def _tasks_provider():
            return db.query(RegistrationTask).order_by(desc(RegistrationTask.created_at)).limit(100).all()

        playwright_stats, playwright_alerts = get_cached_playwright_stats(
            _tasks_provider,
            stats_builder=_build_playwright_stats,
            alerts_builder=_build_playwright_alerts,
            ttl_seconds=15,
        )

        return {
            "by_status": {status: count for status, count in status_stats},
            "today_count": today_total,
            "today_total": today_total,
            "today_success": today_success,
            "today_failed": today_failed,
            "today_success_rate": today_success_rate,
            "today_by_status": today_by_status,
            "playwright": playwright_stats,
            "playwright_alerts": playwright_alerts,
        }


@router.get("/available-services")
async def get_available_email_services():
    """
    获取可用于注册的邮箱服务列表

    返回所有已启用的邮箱服务，包括：
    - tempmail: 临时邮箱（无需配置）
    - yyds_mail: YYDS Mail 临时邮箱（需 API Key）
    - outlook: 已导入的 Outlook 账户
    - moe_mail: 已配置的自定义域名服务
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...config.settings import get_settings

    settings = get_settings()
    result = {
        "tempmail": {
            "available": bool(settings.tempmail_enabled),
            "count": 1 if settings.tempmail_enabled else 0,
            "services": ([{
                "id": None,
                "name": "Tempmail.lol",
                "type": "tempmail",
                "description": "临时邮箱，自动创建"
            }] if settings.tempmail_enabled else [])
        },
        "yyds_mail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "outlook": {
            "available": False,
            "count": 0,
            "services": []
        },
        "moe_mail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "temp_mail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "duck_mail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "freemail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "imap_mail": {
            "available": False,
            "count": 0,
            "services": []
        },
        "codex_otp": {
            "available": False,
            "count": 0,
            "services": []
        },
        "codex_otp_d1": {
            "available": False,
            "count": 0,
            "services": []
        }
    }

    if settings.codex_otp_enabled and settings.codex_otp_base_url and settings.codex_otp_admin_token:
        result["codex_otp"]["available"] = True
        result["codex_otp"]["count"] = 1
        result["codex_otp"]["services"].append({
            "id": None,
            "name": "Codex OTP",
            "type": "codex_otp",
            "domain": settings.codex_otp_domain or None,
            "description": "Codex OTP 专用邮箱后端",
        })

    if (
        settings.codex_otp_d1_enabled
        and settings.codex_otp_d1_domain
        and settings.codex_otp_d1_cf_account_id
        and settings.codex_otp_d1_cf_database_id
        and settings.codex_otp_d1_cf_runtime_api_token
    ):
        result["codex_otp_d1"]["available"] = True
        result["codex_otp_d1"]["count"] = 1
        result["codex_otp_d1"]["services"].append({
            "id": None,
            "name": "Codex OTP D1",
            "type": "codex_otp_d1",
            "domain": settings.codex_otp_d1_domain or None,
            "description": "Codex OTP D1 只读模式",
        })

    yyds_api_key = settings.yyds_mail_api_key.get_secret_value() if settings.yyds_mail_api_key else ""
    if settings.yyds_mail_enabled and yyds_api_key:
        result["yyds_mail"]["available"] = True
        result["yyds_mail"]["count"] = 1
        result["yyds_mail"]["services"].append({
            "id": None,
            "name": "YYDS Mail",
            "type": "yyds_mail",
            "default_domain": settings.yyds_mail_default_domain or None,
            "description": "YYDS Mail API 临时邮箱",
        })

    with get_db() as db:
        yyds_mail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "yyds_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in yyds_mail_services:
            config = service.config or {}
            result["yyds_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "yyds_mail",
                "default_domain": config.get("default_domain"),
                "priority": service.priority
            })

        if yyds_mail_services:
            result["yyds_mail"]["count"] = len(result["yyds_mail"]["services"])
            result["yyds_mail"]["available"] = True
        # 获取 Outlook 账户
        outlook_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "outlook",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in outlook_services:
            config = service.config or {}
            result["outlook"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "outlook",
                "has_oauth": bool(config.get("client_id") and config.get("refresh_token")),
                "priority": service.priority
            })

        result["outlook"]["count"] = len(outlook_services)
        result["outlook"]["available"] = len(outlook_services) > 0

        # 获取自定义域名服务
        custom_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "moe_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in custom_services:
            config = service.config or {}
            result["moe_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "moe_mail",
                "default_domain": config.get("default_domain"),
                "priority": service.priority
            })

        result["moe_mail"]["count"] = len(custom_services)
        result["moe_mail"]["available"] = len(custom_services) > 0

        # 如果数据库中没有自定义域名服务，检查 settings
        if not result["moe_mail"]["available"]:
            if settings.custom_domain_base_url and settings.custom_domain_api_key:
                result["moe_mail"]["available"] = True
                result["moe_mail"]["count"] = 1
                result["moe_mail"]["services"].append({
                    "id": None,
                    "name": "默认自定义域名服务",
                    "type": "moe_mail",
                    "from_settings": True
                })

        # 获取 TempMail 服务（自部署 Cloudflare Worker 临时邮箱）
        temp_mail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "temp_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in temp_mail_services:
            config = service.config or {}
            result["temp_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "temp_mail",
                "domain": config.get("domain"),
                "priority": service.priority
            })

        result["temp_mail"]["count"] = len(temp_mail_services)
        result["temp_mail"]["available"] = len(temp_mail_services) > 0

        duck_mail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "duck_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in duck_mail_services:
            config = service.config or {}
            result["duck_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "duck_mail",
                "default_domain": config.get("default_domain"),
                "priority": service.priority
            })

        result["duck_mail"]["count"] = len(duck_mail_services)
        result["duck_mail"]["available"] = len(duck_mail_services) > 0

        freemail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "freemail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in freemail_services:
            config = service.config or {}
            result["freemail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "freemail",
                "domain": config.get("domain"),
                "priority": service.priority
            })

        result["freemail"]["count"] = len(freemail_services)
        result["freemail"]["available"] = len(freemail_services) > 0

        imap_mail_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "imap_mail",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in imap_mail_services:
            config = service.config or {}
            result["imap_mail"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "imap_mail",
                "email": config.get("email"),
                "host": config.get("host"),
                "priority": service.priority
            })

        result["imap_mail"]["count"] = len(imap_mail_services)
        result["imap_mail"]["available"] = len(imap_mail_services) > 0

        codex_otp_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "codex_otp",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in codex_otp_services:
            config = service.config or {}
            result["codex_otp"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "codex_otp",
                "domain": config.get("domain"),
                "priority": service.priority,
            })

        if codex_otp_services:
            result["codex_otp"]["count"] = len(result["codex_otp"]["services"])
            result["codex_otp"]["available"] = True

        codex_otp_d1_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "codex_otp_d1",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        for service in codex_otp_d1_services:
            config = service.config or {}
            result["codex_otp_d1"]["services"].append({
                "id": service.id,
                "name": service.name,
                "type": "codex_otp_d1",
                "domain": config.get("domain"),
                "priority": service.priority,
            })

        if codex_otp_d1_services:
            result["codex_otp_d1"]["count"] = len(result["codex_otp_d1"]["services"])
            result["codex_otp_d1"]["available"] = True

    return result


# ============== Outlook 批量注册 API ==============

@router.get("/outlook-accounts", response_model=OutlookAccountsListResponse)
async def get_outlook_accounts_for_registration():
    """
    获取可用于注册的 Outlook 账户列表

    返回所有已启用的 Outlook 服务，并检查每个邮箱是否已在 accounts 表中注册
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...database.models import Account

    with get_db() as db:
        # 获取所有启用的 Outlook 服务
        outlook_services = db.query(EmailServiceModel).filter(
            EmailServiceModel.service_type == "outlook",
            EmailServiceModel.enabled == True
        ).order_by(EmailServiceModel.priority.asc()).all()

        accounts = []
        registered_count = 0
        unregistered_count = 0

        for service in outlook_services:
            config = service.config or {}
            email = config.get("email") or service.name

            # 检查是否已注册（查询 accounts 表）
            existing_account = db.query(Account).filter(
                Account.email == email
            ).first()

            is_registered = existing_account is not None
            if is_registered:
                registered_count += 1
            else:
                unregistered_count += 1

            accounts.append(OutlookAccountForRegistration(
                id=service.id,
                email=email,
                name=service.name,
                has_oauth=bool(config.get("client_id") and config.get("refresh_token")),
                is_registered=is_registered,
                registered_account_id=existing_account.id if existing_account else None
            ))

        return OutlookAccountsListResponse(
            total=len(accounts),
            registered_count=registered_count,
            unregistered_count=unregistered_count,
            accounts=accounts
        )


async def run_outlook_batch_registration(
    batch_id: str,
    service_ids: List[int],
    skip_registered: bool,
    proxy: Optional[str],
    interval_min: int,
    interval_max: int,
    concurrency: int = 1,
    mode: str = "pipeline",
    auto_upload_cpa: bool = False,
    cpa_service_ids: List[int] = None,
    auto_upload_sub2api: bool = False,
    sub2api_service_ids: List[int] = None,
    auto_upload_tm: bool = False,
    tm_service_ids: List[int] = None,
):
    """
    异步执行 Outlook 批量注册任务，复用通用并发逻辑

    将每个 service_id 映射为一个独立的 task_uuid，然后调用
    run_batch_registration 的并发逻辑
    """
    loop = task_manager.get_loop()
    if loop is None:
        loop = asyncio.get_event_loop()
        task_manager.set_loop(loop)

    # 预先为每个 service_id 创建注册任务记录
    task_uuids = []
    with get_db() as db:
        for service_id in service_ids:
            task_uuid = str(uuid.uuid4())
            crud.create_registration_task(
                db,
                task_uuid=task_uuid,
                proxy=proxy,
                email_service_id=service_id
            )
            task_uuids.append(task_uuid)

    # 复用通用并发逻辑（outlook 服务类型，每个任务通过 email_service_id 定位账户）
    await run_batch_registration(
        batch_id=batch_id,
        task_uuids=task_uuids,
        email_service_type="outlook",
        proxy=proxy,
        email_service_config=None,
        email_service_id=None,   # 每个任务已绑定了独立的 email_service_id
        interval_min=interval_min,
        interval_max=interval_max,
        concurrency=concurrency,
        mode=mode,
        auto_upload_cpa=auto_upload_cpa,
        cpa_service_ids=cpa_service_ids,
        auto_upload_sub2api=auto_upload_sub2api,
        sub2api_service_ids=sub2api_service_ids,
        auto_upload_tm=auto_upload_tm,
        tm_service_ids=tm_service_ids,
    )


@router.post("/outlook-batch", response_model=OutlookBatchRegistrationResponse)
async def start_outlook_batch_registration(
    request: OutlookBatchRegistrationRequest,
    background_tasks: BackgroundTasks
):
    """
    启动 Outlook 批量注册任务

    - service_ids: 选中的 EmailService ID 列表
    - skip_registered: 是否自动跳过已注册邮箱（默认 True）
    - proxy: 代理地址
    - interval_min: 最小间隔秒数
    - interval_max: 最大间隔秒数
    """
    from ...database.models import EmailService as EmailServiceModel
    from ...database.models import Account

    # 验证参数
    if not request.service_ids:
        raise HTTPException(status_code=400, detail="请选择至少一个 Outlook 账户")

    if request.interval_min < 0 or request.interval_max < request.interval_min:
        raise HTTPException(status_code=400, detail="间隔时间参数无效")

    if not 1 <= request.concurrency <= 50:
        raise HTTPException(status_code=400, detail="并发数必须在 1-50 之间")

    if request.mode not in ("parallel", "pipeline"):
        raise HTTPException(status_code=400, detail="模式必须为 parallel 或 pipeline")

    # 过滤掉已注册的邮箱
    actual_service_ids = request.service_ids
    skipped_count = 0

    if request.skip_registered:
        actual_service_ids = []
        with get_db() as db:
            for service_id in request.service_ids:
                service = db.query(EmailServiceModel).filter(
                    EmailServiceModel.id == service_id
                ).first()

                if not service:
                    continue

                config = service.config or {}
                email = config.get("email") or service.name

                # 检查是否已注册
                existing_account = db.query(Account).filter(
                    Account.email == email
                ).first()

                if existing_account:
                    skipped_count += 1
                else:
                    actual_service_ids.append(service_id)

    if not actual_service_ids:
        return OutlookBatchRegistrationResponse(
            batch_id="",
            total=len(request.service_ids),
            skipped=skipped_count,
            to_register=0,
            service_ids=[]
        )

    # 创建批量任务
    batch_id = str(uuid.uuid4())

    # 初始化批量任务状态
    batch_tasks[batch_id] = {
        "total": len(actual_service_ids),
        "completed": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "cancelled": False,
        "service_ids": actual_service_ids,
        "current_index": 0,
        "logs": [],
        "finished": False
    }

    # 在后台运行批量注册
    background_tasks.add_task(
        run_outlook_batch_registration,
        batch_id,
        actual_service_ids,
        request.skip_registered,
        request.proxy,
        request.interval_min,
        request.interval_max,
        request.concurrency,
        request.mode,
        request.auto_upload_cpa,
        request.cpa_service_ids,
        request.auto_upload_sub2api,
        request.sub2api_service_ids,
        request.auto_upload_tm,
        request.tm_service_ids,
    )

    return OutlookBatchRegistrationResponse(
        batch_id=batch_id,
        total=len(request.service_ids),
        skipped=skipped_count,
        to_register=len(actual_service_ids),
        service_ids=actual_service_ids
    )


@router.get("/outlook-batch/{batch_id}")
async def get_outlook_batch_status(batch_id: str):
    """获取 Outlook 批量任务状态"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    return {
        "batch_id": batch_id,
        "total": batch["total"],
        "completed": batch["completed"],
        "success": batch["success"],
        "failed": batch["failed"],
        "skipped": batch.get("skipped", 0),
        "current_index": batch["current_index"],
        "cancelled": batch["cancelled"],
        "finished": batch.get("finished", False),
        "logs": batch.get("logs", []),
        "progress": f"{batch['completed']}/{batch['total']}"
    }


@router.post("/outlook-batch/{batch_id}/cancel")
async def cancel_outlook_batch(batch_id: str):
    """取消 Outlook 批量任务"""
    if batch_id not in batch_tasks:
        raise HTTPException(status_code=404, detail="批量任务不存在")

    batch = batch_tasks[batch_id]
    if batch.get("finished"):
        raise HTTPException(status_code=400, detail="批量任务已完成")

    # 同时更新两个系统的取消状态
    batch["cancelled"] = True
    task_manager.cancel_batch(batch_id)
    _cancel_batch_tasks(batch_id)

    return {"success": True, "message": "批量任务取消请求已提交，正在让它们有序收工"}
