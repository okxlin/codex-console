"""
Codex OTP 邮箱服务实现。

该服务面向专用 OTP Worker，尽量减少对现有邮箱系统的侵入。
"""

import logging
import time
from typing import Any, Dict, List, Optional

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from ..config.constants import OTP_CODE_PATTERN
from ..core.http_client import HTTPClient, RequestConfig


logger = logging.getLogger(__name__)


class CodexOtpMailService(BaseEmailService):
    """Codex OTP 专用邮箱后端适配器。"""

    def __init__(self, config: Dict[str, Any] = None, name: str = None):
        super().__init__(EmailServiceType.CODEX_OTP, name)

        required_keys = ["base_url", "admin_token"]
        missing_keys = [key for key in required_keys if not (config or {}).get(key)]
        if missing_keys:
            raise ValueError(f"缺少必需配置: {missing_keys}")

        default_config = {
            "timeout": 30,
            "max_retries": 3,
            "poll_interval": 3,
            "ttl_seconds": 1800,
            "domain": "",
            "proxy_url": None,
            "address_tags": ["codex", "register"],
        }
        self.config = {**default_config, **(config or {})}
        self.config["base_url"] = str(self.config["base_url"] or "").rstrip("/")

        http_config = RequestConfig(
            timeout=int(self.config.get("timeout") or 30),
            max_retries=int(self.config.get("max_retries") or 3),
        )
        self.http_client = HTTPClient(
            proxy_url=self.config.get("proxy_url"),
            config=http_config,
        )

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-admin-auth": str(self.config["admin_token"]),
        }
        custom_auth = str(self.config.get("custom_auth") or "").strip()
        if custom_auth:
            headers["x-custom-auth"] = custom_auth
        return headers

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.config['base_url']}{path}"
        headers = kwargs.pop("headers", {}) or {}
        merged_headers = self._headers()
        merged_headers.update(headers)

        try:
            response = self.http_client.request(method, url, headers=merged_headers, **kwargs)
        except Exception as exc:
            self.update_status(False, exc)
            raise EmailServiceError(f"请求失败: {method} {path} - {exc}") from exc

        if response.status_code >= 400:
            message = f"请求失败: {response.status_code}"
            try:
                payload = response.json()
            except Exception:
                payload = response.text[:200]
            self.update_status(False, EmailServiceError(f"{message} - {payload}"))
            raise EmailServiceError(f"{message} - {payload}")

        try:
            return response.json()
        except Exception:
            return {"raw_response": response.text}

    def create_email(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        stage = str((config or {}).get("stage") or "register").strip()
        payload = {
            "domain": str((config or {}).get("domain") or self.config.get("domain") or "").strip(),
            "ttl_seconds": int((config or {}).get("ttl_seconds") or self.config.get("ttl_seconds") or 1800),
            "tags": (config or {}).get("tags") or self.config.get("address_tags") or ["codex", "register"],
        }
        response = self._request("POST", "/admin/v1/new_address", json=payload)
        email = str(response.get("email") or "").strip()
        if not email:
            raise EmailServiceError(f"创建邮箱失败，返回数据不完整: {response}")

        email_info = {
            "email": email,
            "service_id": f"{email}:{stage}",
            "id": email,
            "created_at": response.get("created_at") or time.time(),
            "expires_at": response.get("expires_at"),
            "domain": response.get("domain") or payload["domain"],
        }
        self.update_status(True)
        return email_info

    def get_verification_code(
        self,
        email: str,
        email_id: str = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        start_time = time.time()
        poll_interval = max(1, int(self.config.get("poll_interval") or 3))
        attempted_codes: List[str] = []
        stage = None

        if email_id and isinstance(email_id, str) and ":" in email_id:
            _, stage = email_id.split(":", 1)

        if stage is None:
            stage = "register"
            if attempted_codes:
                stage = "login"

        while time.time() - start_time < timeout:
            payload = {
                "email": email,
                "stage": stage,
                "ignore_codes": attempted_codes,
                "otp_sent_at": otp_sent_at,
                "pattern": pattern,
            }
            try:
                result = self._request("POST", "/admin/v1/code/latest", json=payload)
            except EmailServiceError as exc:
                logger.debug("Codex OTP 取码失败，等待后重试: %s", exc)
                time.sleep(poll_interval)
                continue

            if result.get("found"):
                code = str(result.get("code") or "").strip()
                code_id = result.get("id")
                if code:
                    if code_id:
                        try:
                            self._request("POST", "/admin/v1/code/consume", json={"id": code_id})
                        except Exception as exc:
                            logger.debug("Codex OTP 标记验证码已消费失败: %s", exc)
                    self.update_status(True)
                    return code

            latest_attempt = str(result.get("latest_code") or "").strip()
            if latest_attempt and latest_attempt not in attempted_codes:
                attempted_codes.append(latest_attempt)

            time.sleep(poll_interval)

        return None

    def list_emails(self, **kwargs) -> List[Dict[str, Any]]:
        return []

    def delete_email(self, email_id: str) -> bool:
        try:
            response = self._request("POST", "/admin/v1/address/deactivate", json={"email": email_id})
            self.update_status(True)
            return bool(response.get("success", True))
        except Exception as exc:
            self.update_status(False, exc)
            logger.debug("Codex OTP 删除邮箱失败: %s", exc)
            return False

    def check_health(self) -> bool:
        try:
            response = self._request("GET", "/admin/v1/health")
            healthy = bool(response.get("ok") or response.get("success") or response.get("status") == "ok")
            self.update_status(healthy, None if healthy else EmailServiceError("健康检查失败"))
            return healthy
        except Exception as exc:
            self.update_status(False, exc)
            return False

    def get_service_info(self) -> Dict[str, Any]:
        return {
            "base_url": self.config.get("base_url"),
            "domain": self.config.get("domain"),
            "poll_interval": self.config.get("poll_interval"),
            "has_admin_token": bool(self.config.get("admin_token")),
            "has_custom_auth": bool(self.config.get("custom_auth")),
        }

    def build_stage_service_id(self, email: str, stage: str) -> str:
        return f"{email}:{stage}"
