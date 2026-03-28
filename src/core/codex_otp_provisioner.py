"""
Codex OTP Worker 自动初始化器。

注意：Cloudflare Email Routing 指向 Email Worker 的步骤暂不完全开放 API，
因此初始化器负责创建 D1、初始化 schema、部署 Worker 与可选 HTTP Route，
并返回剩余的手工操作提示。
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from curl_cffi import requests as cffi_requests


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS addresses (
    email TEXT PRIMARY KEY,
    local_part TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME,
    last_code_at DATETIME,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_addresses_status ON addresses(status);
CREATE INDEX IF NOT EXISTS idx_addresses_expires_at ON addresses(expires_at);

CREATE TABLE IF NOT EXISTS codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code TEXT NOT NULL,
    stage TEXT,
    source TEXT,
    subject TEXT,
    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed INTEGER NOT NULL DEFAULT 0,
    consumed_at DATETIME,
    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_codes_email_consumed_received
ON codes(email, consumed, received_at DESC);
""".strip()


TEMPLATE_DIR = Path(__file__).with_name("templates")
WORKER_TEMPLATE = (TEMPLATE_DIR / "codex_otp_worker.js").read_text(encoding="utf-8")
WORKER_TEMPLATE_VERSION = "2026-03-28.1"


class CodexOtpProvisionError(Exception):
    """Codex OTP 初始化异常。"""


def _sanitize_error_detail(detail: Any) -> str:
    text = str(detail or "").strip()
    if not text:
        return "unknown_error"
    return text[:240]


@dataclass
class ProvisionResult:
    service_config: Dict[str, Any]
    cloudflare: Dict[str, Any]
    next_steps: List[str]


@dataclass
class ProvisionConflicts:
    database: Optional[Dict[str, Any]] = None
    worker: Optional[Dict[str, Any]] = None
    route: Optional[Dict[str, Any]] = None

    def has_conflicts(self) -> bool:
        return any([self.database, self.worker, self.route])


class CloudflareProvisioner:
    def __init__(self, account_id: str, api_token: str, zone_id: str = ""):
        self.account_id = str(account_id or "").strip()
        self.api_token = str(api_token or "").strip()
        self.zone_id = str(zone_id or "").strip()
        if not self.account_id or not self.api_token:
            raise CodexOtpProvisionError("缺少 Cloudflare account_id 或 api_token")

        self.session = cffi_requests.Session(headers={
            "Authorization": f"Bearer {self.api_token}",
        })

    def _request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        response = self.session.request(method, url, timeout=60, **kwargs)
        if response.status_code >= 400:
            try:
                payload = response.json()
                detail = payload.get("errors") or payload.get("messages") or payload
            except Exception:
                detail = response.text[:240]
            raise CodexOtpProvisionError(
                f"Cloudflare API 请求失败: HTTP {response.status_code} - {_sanitize_error_detail(detail)}"
            )
        payload = response.json()
        if not payload.get("success", False):
            raise CodexOtpProvisionError(
                f"Cloudflare API 返回失败: {_sanitize_error_detail(payload.get('errors') or payload)}"
            )
        return payload

    def create_d1_database(self, name: str, location_hint: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name}
        if location_hint:
            body["primary_location_hint"] = location_hint
        payload = self._request(
            "POST",
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/d1/database",
            json=body,
        )
        result = payload.get("result")
        if not isinstance(result, dict) or not result.get("uuid"):
            raise CodexOtpProvisionError("Cloudflare D1 返回结果异常")
        return result

    def list_d1_databases(self, name: str = "") -> List[Dict[str, Any]]:
        params = {"name": name} if name else None
        payload = self._request(
            "GET",
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/d1/database",
            params=params,
        )
        result = payload.get("result") or []
        return result if isinstance(result, list) else []

    def list_worker_scripts(self) -> List[Dict[str, Any]]:
        payload = self._request(
            "GET",
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/workers/scripts",
        )
        result = payload.get("result") or []
        return result if isinstance(result, list) else []

    def list_routes(self) -> List[Dict[str, Any]]:
        if not self.zone_id:
            return []
        payload = self._request(
            "GET",
            f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}/workers/routes",
        )
        result = payload.get("result") or []
        return result if isinstance(result, list) else []

    def execute_sql(self, database_id: str, sql: str) -> None:
        self._request(
            "POST",
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/d1/database/{database_id}/query",
            json={"sql": sql},
        )

    def deploy_worker(
        self,
        script_name: str,
        database_id: str,
        admin_token: str,
        default_email_domain: str,
        custom_auth: str = "",
        ttl_seconds: int = 1800,
        code_retention_days: int = 2,
    ) -> Dict[str, Any]:
        metadata = {
            "main_module": "index.js",
            "bindings": [
                {
                    "type": "d1",
                    "name": "DB",
                    "id": database_id,
                },
                {
                    "type": "plain_text",
                    "name": "ADMIN_TOKEN",
                    "text": admin_token,
                },
                {
                    "type": "plain_text",
                    "name": "DEFAULT_EMAIL_DOMAIN",
                    "text": default_email_domain,
                },
                {
                    "type": "plain_text",
                    "name": "DEFAULT_TTL_SECONDS",
                    "text": str(ttl_seconds),
                },
                {
                    "type": "plain_text",
                    "name": "CODE_RETENTION_DAYS",
                    "text": str(code_retention_days),
                },
            ],
            "compatibility_date": "2026-03-28",
        }
        if custom_auth:
            metadata["bindings"].append({
                "type": "plain_text",
                "name": "CUSTOM_AUTH",
                "text": custom_auth,
            })

        files = {
            "metadata": (None, json.dumps(metadata), "application/json"),
            "index.js": ("index.js", WORKER_TEMPLATE.encode("utf-8"), "application/javascript+module"),
        }
        payload = self._request(
            "PUT",
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/workers/scripts/{script_name}",
            files=files,
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CodexOtpProvisionError("Cloudflare Worker 返回结果异常")
        return result

    def create_route(self, route_pattern: str, script_name: str) -> Optional[Dict[str, Any]]:
        if not self.zone_id or not route_pattern:
            return None
        payload = self._request(
            "POST",
            f"https://api.cloudflare.com/client/v4/zones/{self.zone_id}/workers/routes",
            json={"pattern": route_pattern, "script": script_name},
        )
        return payload.get("result")


def build_public_base_url(route_pattern: str) -> str:
    route = str(route_pattern or "").strip()
    if not route:
        return ""
    if route.endswith("/*"):
        route = route[:-2]
    route = route.lstrip("*")
    if route.startswith("http://") or route.startswith("https://"):
        return route.rstrip("/")
    return f"https://{route.rstrip('/')}"


def _normalize_provision_inputs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(kwargs)
    for key in (
        "account_id",
        "api_token",
        "zone_id",
        "script_name",
        "database_name",
        "route_pattern",
        "email_domain",
        "service_name",
        "custom_auth",
        "admin_token",
        "location_hint",
    ):
        normalized[key] = str(normalized.get(key) or "").strip()

    normalized["email_domain"] = normalized["email_domain"].lower()
    normalized["route_pattern"] = normalized["route_pattern"].rstrip("/")
    normalized["ttl_seconds"] = int(normalized.get("ttl_seconds") or 1800)
    normalized["code_retention_days"] = int(normalized.get("code_retention_days") or 2)
    return normalized


def _validate_provision_inputs(
    *,
    script_name: str,
    database_name: str,
    route_pattern: str,
    email_domain: str,
) -> None:
    if not script_name.strip():
        raise CodexOtpProvisionError("缺少 Worker Script 名称")
    if not database_name.strip():
        raise CodexOtpProvisionError("缺少 D1 数据库名称")
    if not route_pattern.strip():
        raise CodexOtpProvisionError("缺少 HTTP Route")
    if not email_domain.strip():
        raise CodexOtpProvisionError("缺少邮箱域名")


def _validate_database_result(database: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(database, dict) or not database.get("uuid"):
        raise CodexOtpProvisionError("Cloudflare D1 返回结果异常")
    return database


def _validate_worker_result(worker: Dict[str, Any], script_name: str) -> Dict[str, Any]:
    if not isinstance(worker, dict):
        raise CodexOtpProvisionError("Cloudflare Worker 返回结果异常")
    if not worker.get("id"):
        worker = {**worker, "id": script_name}
    return worker


def _collect_conflicts(
    provisioner: CloudflareProvisioner,
    *,
    database_name: str,
    script_name: str,
    route_pattern: str,
) -> ProvisionConflicts:
    database = next(
        (item for item in provisioner.list_d1_databases(database_name) if str(item.get("name") or "") == database_name),
        None,
    )
    worker = next(
        (item for item in provisioner.list_worker_scripts() if str(item.get("id") or "") == script_name),
        None,
    )
    route = next(
        (item for item in provisioner.list_routes() if str(item.get("pattern") or "") == route_pattern),
        None,
    )
    return ProvisionConflicts(database=database, worker=worker, route=route)


def _raise_if_conflicts(conflicts: ProvisionConflicts) -> None:
    messages = []
    if conflicts.database:
        messages.append(f"D1 名称已存在: {conflicts.database.get('name')}")
    if conflicts.worker:
        messages.append(f"Worker Script 已存在: {conflicts.worker.get('id')}")
    if conflicts.route:
        messages.append(f"Route 已存在: {conflicts.route.get('pattern')}")
    if messages:
        raise CodexOtpProvisionError("资源冲突，默认已阻断: " + "；".join(messages))


def provision_codex_otp(
    *,
    account_id: str,
    api_token: str,
    zone_id: str,
    script_name: str,
    database_name: str,
    route_pattern: str,
    email_domain: str,
    service_name: str,
    custom_auth: str = "",
    admin_token: str = "",
    ttl_seconds: int = 1800,
    code_retention_days: int = 2,
    location_hint: str = "",
    allow_override: bool = False,
) -> ProvisionResult:
    route_pattern = str(route_pattern or "").strip().rstrip("/")
    email_domain = str(email_domain or "").strip().lower()
    script_name = str(script_name or "").strip()
    database_name = str(database_name or "").strip()
    service_name = str(service_name or "").strip()
    custom_auth = str(custom_auth or "").strip()
    admin_token = str(admin_token or "").strip()
    location_hint = str(location_hint or "").strip()

    _validate_provision_inputs(
        script_name=script_name,
        database_name=database_name,
        route_pattern=route_pattern,
        email_domain=email_domain,
    )

    token = admin_token or secrets.token_urlsafe(24)
    provisioner = CloudflareProvisioner(account_id=account_id, api_token=api_token, zone_id=zone_id)
    conflicts = _collect_conflicts(
        provisioner,
        database_name=database_name,
        script_name=script_name,
        route_pattern=route_pattern,
    )
    if conflicts.has_conflicts() and not allow_override:
        _raise_if_conflicts(conflicts)

    if conflicts.database and allow_override:
        database = _validate_database_result(conflicts.database)
    else:
        database = _validate_database_result(
            provisioner.create_d1_database(database_name, location_hint=location_hint)
        )
    provisioner.execute_sql(database["uuid"], SCHEMA_SQL)
    worker = _validate_worker_result(
        provisioner.deploy_worker(
            script_name=script_name,
            database_id=database["uuid"],
            admin_token=token,
            default_email_domain=email_domain,
            custom_auth=custom_auth,
            ttl_seconds=ttl_seconds,
            code_retention_days=code_retention_days,
        ),
        script_name=script_name,
    )
    route = provisioner.create_route(route_pattern=route_pattern, script_name=script_name)

    next_steps = []
    if not route:
        next_steps.append("需要手动为 Worker 绑定一个 HTTP Route 或自定义域名，随后再把该地址填回服务配置。")
    next_steps.append("需要在 Cloudflare Email Routing 中把目标邮箱域名或 catch-all 路由绑定到该 Email Worker。")
    next_steps.append("建议初始化完成后立即测试 /admin/v1/health，并用测试地址验证 Email Worker 是否能写入验证码。")

    service_config = {
        "base_url": build_public_base_url(route_pattern) if route_pattern else "",
        "admin_token": token,
        "custom_auth": custom_auth,
        "domain": email_domain,
        "ttl_seconds": ttl_seconds,
        "poll_interval": 3,
        "max_retries": 3,
        "timeout": 30,
        }
    cloudflare_meta = {
        "service_name": service_name,
        "script_name": script_name,
        "database_id": database["uuid"],
        "database_name": database.get("name") or database_name,
        "worker_id": worker.get("id") or script_name,
        "route": route,
        "template_version": WORKER_TEMPLATE_VERSION,
        "allow_override": bool(allow_override),
    }
    return ProvisionResult(service_config=service_config, cloudflare=cloudflare_meta, next_steps=next_steps)


def provision_codex_otp_idempotent(**kwargs: Any) -> ProvisionResult:
    try:
        return provision_codex_otp(**_normalize_provision_inputs(kwargs))
    except CodexOtpProvisionError as exc:
        raise CodexOtpProvisionError(_sanitize_error_detail(exc)) from exc
