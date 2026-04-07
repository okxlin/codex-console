"""
邮箱服务配置 API 路由
"""

import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func

from ...database import crud
from ...database.session import get_db
from ...database.models import EmailService as EmailServiceModel
from ...database.models import Account as AccountModel
from ...config.settings import get_settings, update_settings
from ...core.codex_otp_provisioner import CodexOtpProvisionError, provision_codex_otp_d1_idempotent, provision_codex_otp_idempotent
from ...services import EmailServiceFactory, EmailServiceType

logger = logging.getLogger(__name__)
router = APIRouter()


# ============== Pydantic Models ==============

class EmailServiceCreate(BaseModel):
    """创建邮箱服务请求"""
    service_type: str
    name: str
    config: Dict[str, Any]
    enabled: bool = True
    priority: int = 0


class EmailServiceUpdate(BaseModel):
    """更新邮箱服务请求"""
    name: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None


class EmailServiceResponse(BaseModel):
    """??????"""
    id: int
    service_type: str
    name: str
    enabled: bool
    priority: int
    config: Optional[Dict[str, Any]] = None  # ??????????
    registration_status: Optional[str] = None
    registered_account_id: Optional[int] = None
    last_used: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EmailServiceListResponse(BaseModel):
    """邮箱服务列表响应"""
    total: int
    services: List[EmailServiceResponse]


class ServiceTestResult(BaseModel):
    """服务测试结果"""
    success: bool
    message: str
    details: Optional[Dict[str, Any]] = None


class OutlookBatchImportRequest(BaseModel):
    """Outlook 批量导入请求"""
    data: str  # 多行数据，每行格式: 邮箱----密码 或 邮箱----密码----client_id----refresh_token
    enabled: bool = True
    priority: int = 0


class OutlookBatchImportResponse(BaseModel):
    """Outlook 批量导入响应"""
    total: int
    success: int
    failed: int
    accounts: List[Dict[str, Any]]
    errors: List[str]


# ============== Helper Functions ==============

# 敏感字段列表，返回响应时需要过滤
SENSITIVE_FIELDS = {
    'password',
    'api_key',
    'refresh_token',
    'access_token',
    'admin_token',
    'admin_password',
    'custom_auth',
    'cf_api_token',
    'admin_token',
    'cf_runtime_api_token',
}

def filter_sensitive_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """过滤敏感配置信息"""
    if not config:
        return {}

    filtered = {}
    for key, value in config.items():
        if key in SENSITIVE_FIELDS:
            # 敏感字段不返回，但标记是否存在
            filtered[f"has_{key}"] = bool(value)
        else:
            filtered[key] = value

    # 为 Outlook 计算是否有 OAuth
    if config.get('client_id') and config.get('refresh_token'):
        filtered['has_oauth'] = True

    if config.get('cf_runtime_api_token'):
        filtered['has_runtime_api_token'] = True

    if isinstance(config.get('cloudflare'), dict):
        cloudflare = dict(config['cloudflare'])
        filtered['cloudflare'] = {
            'database_id': cloudflare.get('database_id'),
            'database_name': cloudflare.get('database_name'),
            'worker_id': cloudflare.get('worker_id'),
            'route': cloudflare.get('route'),
            'script_name': cloudflare.get('script_name'),
        }

    return filtered


def service_to_response(service: EmailServiceModel) -> EmailServiceResponse:
    """?????????"""
    registration_status = None
    registered_account_id = None
    if service.service_type == "outlook":
        email = str((service.config or {}).get("email") or service.name or "").strip()
        normalized_email = email.lower()
        if email:
            with get_db() as db:
                account = (
                    db.query(AccountModel)
                    .filter(func.lower(AccountModel.email) == normalized_email)
                    .first()
                )
            if account:
                registration_status = "registered"
                registered_account_id = account.id
            else:
                registration_status = "unregistered"

    return EmailServiceResponse(
        id=service.id,
        service_type=service.service_type,
        name=service.name,
        enabled=service.enabled,
        priority=service.priority,
        config=filter_sensitive_config(service.config),
        registration_status=registration_status,
        registered_account_id=registered_account_id,
        last_used=service.last_used.isoformat() if service.last_used else None,
        created_at=service.created_at.isoformat() if service.created_at else None,
        updated_at=service.updated_at.isoformat() if service.updated_at else None,
    )


# ============== API Endpoints ==============

@router.get("/stats")
async def get_email_services_stats():
    """获取邮箱服务统计信息"""
    with get_db() as db:
        # 按类型统计
        type_stats = db.query(
            EmailServiceModel.service_type,
            func.count(EmailServiceModel.id)
        ).group_by(EmailServiceModel.service_type).all()

        # 启用数量
        enabled_count = db.query(func.count(EmailServiceModel.id)).filter(
            EmailServiceModel.enabled == True
        ).scalar()

        settings = get_settings()
        tempmail_enabled = bool(settings.tempmail_enabled)
        yyds_enabled = bool(
            settings.yyds_mail_enabled
            and settings.yyds_mail_api_key
            and settings.yyds_mail_api_key.get_secret_value()
        )

        stats = {
            'outlook_count': 0,
            'custom_count': 0,
            'yyds_mail_count': 0,
            'temp_mail_count': 0,
            'duck_mail_count': 0,
            'freemail_count': 0,
            'imap_mail_count': 0,
            'cloudmail_count': 0,
            'codex_otp_count': 0,
            'codex_otp_d1_count': 0,
            'tempmail_available': tempmail_enabled or yyds_enabled,
            'yyds_mail_available': yyds_enabled,
            'enabled_count': enabled_count
        }

        for service_type, count in type_stats:
            if service_type == 'outlook':
                stats['outlook_count'] = count
            elif service_type == 'moe_mail':
                stats['custom_count'] = count
            elif service_type == 'yyds_mail':
                stats['yyds_mail_count'] = count
            elif service_type == 'temp_mail':
                stats['temp_mail_count'] = count
            elif service_type == 'duck_mail':
                stats['duck_mail_count'] = count
            elif service_type == 'freemail':
                stats['freemail_count'] = count
            elif service_type == 'imap_mail':
                stats['imap_mail_count'] = count
            elif service_type == 'cloudmail':
                stats['cloudmail_count'] = count
            elif service_type == 'codex_otp':
                stats['codex_otp_count'] = count
            elif service_type == 'codex_otp_d1':
                stats['codex_otp_d1_count'] = count

        return stats


@router.get("/types")
async def get_service_types():
    """获取支持的邮箱服务类型"""
    return {
        "types": [
            {
                "value": "tempmail",
                "label": "Tempmail.lol",
                "description": "官方内置临时邮箱渠道，通过全局配置使用",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "default": "https://api.tempmail.lol/v2", "required": False},
                    {"name": "timeout", "label": "超时时间", "default": 30, "required": False},
                ]
            },
            {
                "value": "yyds_mail",
                "label": "YYDS Mail",
                "description": "官方内置临时邮箱渠道，使用 X-API-Key 创建邮箱并轮询消息",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "default": "https://maliapi.215.im/v1", "required": False},
                    {"name": "api_key", "label": "API Key", "required": True, "secret": True},
                    {"name": "default_domain", "label": "默认域名", "required": False, "placeholder": "public.example.com"},
                    {"name": "timeout", "label": "超时时间", "default": 30, "required": False},
                ]
            },
            {
                "value": "outlook",
                "label": "Outlook",
                "description": "Outlook 邮箱，需要配置账户信息",
                "config_fields": [
                    {"name": "email", "label": "邮箱地址", "required": True},
                    {"name": "password", "label": "密码", "required": True},
                    {"name": "client_id", "label": "OAuth Client ID", "required": False},
                    {"name": "refresh_token", "label": "OAuth Refresh Token", "required": False},
                ]
            },
            {
                "value": "moe_mail",
                "label": "MoeMail",
                "description": "自定义域名邮箱服务",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True},
                    {"name": "api_key", "label": "API Key", "required": True},
                    {"name": "default_domain", "label": "默认域名", "required": False},
                ]
            },
            {
                "value": "temp_mail",
                "label": "Temp-Mail（自部署）",
                "description": "自部署 Cloudflare Worker 临时邮箱，admin 模式管理",
                "config_fields": [
                    {"name": "base_url", "label": "Worker 地址", "required": True, "placeholder": "https://mail.example.com"},
                    {"name": "admin_password", "label": "Admin 密码", "required": True, "secret": True},
                    {"name": "custom_auth", "label": "Custom Auth（可选）", "required": False, "secret": True},
                    {"name": "domain", "label": "邮箱域名", "required": True, "placeholder": "example.com"},
                    {"name": "enable_prefix", "label": "启用前缀", "required": False, "default": True},
                ]
            },
            {
                "value": "duck_mail",
                "label": "DuckMail",
                "description": "DuckMail 接口邮箱服务，支持 API Key 私有域名访问",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "https://api.duckmail.sbs"},
                    {"name": "default_domain", "label": "默认域名", "required": True, "placeholder": "duckmail.sbs"},
                    {"name": "api_key", "label": "API Key", "required": False, "secret": True},
                    {"name": "password_length", "label": "随机密码长度", "required": False, "default": 12},
                ]
            },
            {
                "value": "freemail",
                "label": "Freemail",
                "description": "Freemail 自部署 Cloudflare Worker 临时邮箱服务",
                "config_fields": [
                    {"name": "base_url", "label": "API 地址", "required": True, "placeholder": "https://freemail.example.com"},
                    {"name": "admin_token", "label": "Admin Token", "required": True, "secret": True},
                    {"name": "domain", "label": "邮箱域名", "required": False, "placeholder": "example.com"},
                ]
            },
            {
                "value": "imap_mail",
                "label": "IMAP 邮箱",
                "description": "标准 IMAP 协议邮箱（Gmail/QQ/163等），仅用于接收验证码，强制直连",
                "config_fields": [
                    {"name": "host", "label": "IMAP 服务器", "required": True, "placeholder": "imap.gmail.com"},
                    {"name": "port", "label": "端口", "required": False, "default": 993},
                    {"name": "use_ssl", "label": "使用 SSL", "required": False, "default": True},
                    {"name": "email", "label": "邮箱地址", "required": True},
                    {"name": "password", "label": "密码/授权码", "required": True, "secret": True},
                ]
            },
            {
                "value": "codex_otp",
                "label": "Codex OTP",
                "description": "专用 OTP Worker 邮箱后端，可配合一键初始化使用",
                "config_fields": [
                    {"name": "base_url", "label": "Worker 地址", "required": True, "placeholder": "https://otp.example.com"},
                    {"name": "admin_token", "label": "Admin Token", "required": True, "secret": True},
                    {"name": "custom_auth", "label": "Custom Auth（可选）", "required": False, "secret": True},
                    {"name": "domain", "label": "邮箱域名", "required": True, "placeholder": "mail.example.com"},
                    {"name": "ttl_seconds", "label": "地址 TTL（秒）", "required": False, "default": 1800},
                    {"name": "poll_interval", "label": "轮询间隔（秒）", "required": False, "default": 3},
                ]
            },
            {
                "value": "codex_otp_d1",
                "label": "Codex OTP D1",
                "description": "只读 D1 取码模式，Worker 仅负责收信提码",
                "config_fields": [
                    {"name": "domain", "label": "邮箱域名", "required": True, "placeholder": "mail.example.com"},
                    {"name": "cf_account_id", "label": "Cloudflare Account ID", "required": True},
                    {"name": "cf_database_id", "label": "D1 Database ID", "required": True},
                    {"name": "cf_runtime_api_token", "label": "D1 只读 Token", "required": True, "secret": True},
                    {"name": "poll_interval", "label": "轮询间隔（秒）", "required": False, "default": 3},
                ]
            }
        ]
    }


@router.get("", response_model=EmailServiceListResponse)
async def list_email_services(
    service_type: Optional[str] = Query(None, description="服务类型筛选"),
    enabled_only: bool = Query(False, description="只显示启用的服务"),
):
    """获取邮箱服务列表"""
    with get_db() as db:
        query = db.query(EmailServiceModel)

        if service_type:
            query = query.filter(EmailServiceModel.service_type == service_type)

        if enabled_only:
            query = query.filter(EmailServiceModel.enabled == True)

        services = query.order_by(EmailServiceModel.priority.asc(), EmailServiceModel.id.asc()).all()

        return EmailServiceListResponse(
            total=len(services),
            services=[service_to_response(s) for s in services]
        )


@router.get("/{service_id}", response_model=EmailServiceResponse)
async def get_email_service(service_id: int):
    """获取单个邮箱服务详情"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")
        return service_to_response(service)


@router.get("/{service_id}/full")
async def get_email_service_full(service_id: int):
    """获取单个邮箱服务完整详情（包含敏感字段，用于编辑）"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        return {
            "id": service.id,
            "service_type": service.service_type,
            "name": service.name,
            "enabled": service.enabled,
            "priority": service.priority,
            "config": service.config or {},  # 返回完整配置
            "last_used": service.last_used.isoformat() if service.last_used else None,
            "created_at": service.created_at.isoformat() if service.created_at else None,
            "updated_at": service.updated_at.isoformat() if service.updated_at else None,
        }


@router.post("", response_model=EmailServiceResponse)
async def create_email_service(request: EmailServiceCreate):
    """创建邮箱服务配置"""
    # 验证服务类型
    try:
        EmailServiceType(request.service_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的服务类型: {request.service_type}")

    with get_db() as db:
        # 检查名称是否重复
        existing = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        service = EmailServiceModel(
            service_type=request.service_type,
            name=request.name,
            config=request.config,
            enabled=request.enabled,
            priority=request.priority
        )
        db.add(service)
        db.commit()
        db.refresh(service)

        return service_to_response(service)


@router.patch("/{service_id}", response_model=EmailServiceResponse)
async def update_email_service(service_id: int, request: EmailServiceUpdate):
    """更新邮箱服务配置"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        update_data = {}
        if request.name is not None:
            update_data["name"] = request.name
        if request.config is not None:
            # 合并配置而不是替换
            current_config = service.config or {}
            merged_config = {**current_config, **request.config}
            # 移除空值
            merged_config = {k: v for k, v in merged_config.items() if v}
            update_data["config"] = merged_config
        if request.enabled is not None:
            update_data["enabled"] = request.enabled
        if request.priority is not None:
            update_data["priority"] = request.priority

        for key, value in update_data.items():
            setattr(service, key, value)

        db.commit()
        db.refresh(service)

        return service_to_response(service)


@router.delete("/{service_id}")
async def delete_email_service(service_id: int):
    """删除邮箱服务配置"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        settings = get_settings()
        if int(settings.registration_auto_email_service_id or 0) == service_id:
            update_settings(registration_auto_email_service_id=0)

        db.delete(service)
        db.commit()

        return {"success": True, "message": f"服务 {service.name} 已删除"}


@router.post("/{service_id}/test", response_model=ServiceTestResult)
async def test_email_service(service_id: int):
    """测试邮箱服务是否可用"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        try:
            service_type = EmailServiceType(service.service_type)
            email_service = EmailServiceFactory.create(service_type, service.config, name=service.name)

            health = email_service.check_health()

            if health:
                return ServiceTestResult(
                    success=True,
                    message="服务连接正常",
                    details=email_service.get_service_info() if hasattr(email_service, 'get_service_info') else None
                )
            else:
                return ServiceTestResult(
                    success=False,
                    message="服务连接失败"
                )

        except Exception as e:
            logger.error(f"测试邮箱服务失败: {e}")
            return ServiceTestResult(
                success=False,
                message=f"测试失败: {str(e)}"
            )


@router.post("/{service_id}/enable")
async def enable_email_service(service_id: int):
    """启用邮箱服务"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        service.enabled = True
        db.commit()

        return {"success": True, "message": f"服务 {service.name} 已启用"}


@router.post("/{service_id}/disable")
async def disable_email_service(service_id: int):
    """禁用邮箱服务"""
    with get_db() as db:
        service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
        if not service:
            raise HTTPException(status_code=404, detail="服务不存在")

        service.enabled = False
        db.commit()

        return {"success": True, "message": f"服务 {service.name} 已禁用"}


@router.post("/reorder")
async def reorder_services(service_ids: List[int]):
    """重新排序邮箱服务优先级"""
    with get_db() as db:
        for index, service_id in enumerate(service_ids):
            service = db.query(EmailServiceModel).filter(EmailServiceModel.id == service_id).first()
            if service:
                service.priority = index

        db.commit()

        return {"success": True, "message": "优先级已更新"}


@router.post("/outlook/batch-import", response_model=OutlookBatchImportResponse)
async def batch_import_outlook(request: OutlookBatchImportRequest):
    """
    批量导入 Outlook 邮箱账户

    支持两种格式：
    - 格式一（密码认证）：邮箱----密码
    - 格式二（XOAUTH2 认证）：邮箱----密码----client_id----refresh_token

    每行一个账户，使用四个连字符（----）分隔字段
    """
    lines = request.data.strip().split("\n")
    total = len(lines)
    success = 0
    failed = 0
    accounts = []
    errors = []

    with get_db() as db:
        for i, line in enumerate(lines):
            line = line.strip()

            # 跳过空行和注释
            if not line or line.startswith("#"):
                continue

            parts = line.split("----")

            # 验证格式
            if len(parts) < 2:
                failed += 1
                errors.append(f"行 {i+1}: 格式错误，至少需要邮箱和密码")
                continue

            email = parts[0].strip()
            password = parts[1].strip()

            # 验证邮箱格式
            if "@" not in email:
                failed += 1
                errors.append(f"行 {i+1}: 无效的邮箱地址: {email}")
                continue

            # 检查是否已存在
            existing = db.query(EmailServiceModel).filter(
                EmailServiceModel.service_type == "outlook",
                EmailServiceModel.name == email
            ).first()

            if existing:
                failed += 1
                errors.append(f"行 {i+1}: 邮箱已存在: {email}")
                continue

            # 构建配置
            config = {
                "email": email,
                "password": password
            }

            # 检查是否有 OAuth 信息（格式二）
            if len(parts) >= 4:
                client_id = parts[2].strip()
                refresh_token = parts[3].strip()
                if client_id and refresh_token:
                    config["client_id"] = client_id
                    config["refresh_token"] = refresh_token

            # 创建服务记录
            try:
                service = EmailServiceModel(
                    service_type="outlook",
                    name=email,
                    config=config,
                    enabled=request.enabled,
                    priority=request.priority
                )
                db.add(service)
                db.commit()
                db.refresh(service)

                accounts.append({
                    "id": service.id,
                    "email": email,
                    "has_oauth": bool(config.get("client_id")),
                    "name": email
                })
                success += 1

            except Exception as e:
                failed += 1
                errors.append(f"行 {i+1}: 创建失败: {str(e)}")
                db.rollback()

    return OutlookBatchImportResponse(
        total=total,
        success=success,
        failed=failed,
        accounts=accounts,
        errors=errors
    )


@router.delete("/outlook/batch")
async def batch_delete_outlook(service_ids: List[int]):
    """批量删除 Outlook 邮箱服务"""
    deleted = 0
    settings = get_settings()
    auto_service_id = int(settings.registration_auto_email_service_id or 0)
    cleared_auto_binding = False
    with get_db() as db:
        for service_id in service_ids:
            service = db.query(EmailServiceModel).filter(
                EmailServiceModel.id == service_id,
                EmailServiceModel.service_type == "outlook"
            ).first()
            if service:
                if service.id == auto_service_id:
                    cleared_auto_binding = True
                db.delete(service)
                deleted += 1
        db.commit()

    if cleared_auto_binding:
        update_settings(registration_auto_email_service_id=0)

    return {"success": True, "deleted": deleted, "message": f"已删除 {deleted} 个服务"}


# ============== 临时邮箱测试 ==============

class TempmailTestRequest(BaseModel):
    """临时邮箱测试请求"""
    provider: str = "tempmail"
    api_url: Optional[str] = None
    api_key: Optional[str] = None


class CodexOtpProvisionRequest(BaseModel):
    service_name: str
    script_name: str
    database_name: str
    route_pattern: str
    email_domain: str
    account_id: str
    api_token: str
    zone_id: str = ""
    custom_auth: str = ""
    admin_token: str = ""
    ttl_seconds: int = 1800
    code_retention_days: int = 2
    location_hint: str = ""
    enabled: bool = True
    priority: int = 0
    allow_override: bool = False

    model_config = ConfigDict(str_strip_whitespace=True)


class CodexOtpD1ProvisionRequest(BaseModel):
    service_name: str
    script_name: str
    database_name: str
    email_domain: str
    account_id: str
    api_token: str
    runtime_api_token: str = ""
    location_hint: str = ""
    enabled: bool = True
    priority: int = 0
    allow_override: bool = False

    model_config = ConfigDict(str_strip_whitespace=True)


@router.post("/codex-otp/provision")
async def provision_codex_otp_service(request: CodexOtpProvisionRequest):
    """一键初始化 Codex OTP Worker、D1 以及本地服务配置。"""
    with get_db() as db:
        existing = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.service_name).first()
        if existing:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        try:
            result = provision_codex_otp_idempotent(
                account_id=request.account_id,
                api_token=request.api_token,
                zone_id=request.zone_id,
                script_name=request.script_name,
                database_name=request.database_name,
                route_pattern=request.route_pattern,
                email_domain=request.email_domain,
                service_name=request.service_name,
                custom_auth=request.custom_auth,
                admin_token=request.admin_token,
                ttl_seconds=request.ttl_seconds,
                code_retention_days=request.code_retention_days,
                location_hint=request.location_hint,
                allow_override=request.allow_override,
            )
        except CodexOtpProvisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Codex OTP 初始化未捕获异常")
            raise HTTPException(status_code=500, detail=f"Codex OTP 初始化内部错误: {type(exc).__name__}") from exc

        config = {
            **result.service_config,
            "cloudflare": result.cloudflare,
        }


@router.post("/codex-otp-d1/provision")
async def provision_codex_otp_d1_service(request: CodexOtpD1ProvisionRequest):
    """一键初始化 Codex OTP D1 只读模式。"""
    with get_db() as db:
        existing = db.query(EmailServiceModel).filter(EmailServiceModel.name == request.service_name).first()
        if existing:
            raise HTTPException(status_code=400, detail="服务名称已存在")

        try:
            result = provision_codex_otp_d1_idempotent(
                account_id=request.account_id,
                api_token=request.api_token,
                script_name=request.script_name,
                database_name=request.database_name,
                email_domain=request.email_domain,
                service_name=request.service_name,
                location_hint=request.location_hint,
                allow_override=request.allow_override,
            )
        except CodexOtpProvisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Codex OTP D1 初始化未捕获异常")
            raise HTTPException(status_code=500, detail=f"Codex OTP D1 初始化内部错误: {type(exc).__name__}") from exc

        config = {
            **result.service_config,
            "cf_runtime_api_token": request.runtime_api_token,
            "cloudflare": result.cloudflare,
        }
        service = EmailServiceModel(
            service_type="codex_otp_d1",
            name=request.service_name,
            config=config,
            enabled=request.enabled,
            priority=request.priority,
        )
        db.add(service)
        db.commit()
        db.refresh(service)

        return {
            "success": True,
            "service": service_to_response(service),
            "cloudflare": result.cloudflare,
            "next_steps": result.next_steps,
            "steps": result.steps,
        }
        service = EmailServiceModel(
            service_type="codex_otp",
            name=request.service_name,
            config=config,
            enabled=request.enabled,
            priority=request.priority,
        )
        db.add(service)
        db.commit()
        db.refresh(service)

        return {
            "success": True,
            "service": service_to_response(service),
            "cloudflare": result.cloudflare,
            "next_steps": result.next_steps,
            "base_url": result.service_config.get("base_url") or None,
            "steps": result.steps,
        }


@router.post("/test-tempmail")
async def test_tempmail_service(request: TempmailTestRequest):
    """测试临时邮箱服务是否可用"""
    try:
        settings = get_settings()
        provider = str(request.provider or "tempmail").strip().lower()

        if provider == "yyds_mail":
            base_url = request.api_url or settings.yyds_mail_base_url
            api_key = request.api_key
            if api_key is None and settings.yyds_mail_api_key:
                api_key = settings.yyds_mail_api_key.get_secret_value()

            config = {
                "base_url": base_url,
                "api_key": api_key or "",
                "default_domain": settings.yyds_mail_default_domain,
                "timeout": settings.yyds_mail_timeout,
                "max_retries": settings.yyds_mail_max_retries,
            }
            service = EmailServiceFactory.create(EmailServiceType.YYDS_MAIL, config)
            success_message = "YYDS Mail 连接正常"
            fail_message = "YYDS Mail 连接失败"
        else:
            base_url = request.api_url or settings.tempmail_base_url
            config = {
                "base_url": base_url,
                "timeout": settings.tempmail_timeout,
                "max_retries": settings.tempmail_max_retries,
            }
            service = EmailServiceFactory.create(EmailServiceType.TEMPMAIL, config)
            success_message = "临时邮箱连接正常"
            fail_message = "临时邮箱连接失败"

        # 检查服务健康状态
        health = service.check_health()

        if health:
            return {"success": True, "message": success_message}
        else:
            return {"success": False, "message": fail_message}

    except Exception as e:
        logger.error(f"测试临时邮箱失败: {e}")
        return {"success": False, "message": f"测试失败: {str(e)}"}
