"""Codex OTP D1 read-only mode service."""

from __future__ import annotations

import logging
import random
import string
import time
from typing import Any, Dict, List, Optional

from .base import BaseEmailService, EmailServiceError, EmailServiceType
from ..config.constants import OTP_CODE_PATTERN
from ..core.codex_otp_d1_reader import CodexOtpD1ReadError, CodexOtpD1Reader


logger = logging.getLogger(__name__)


def canonicalize_email(email: str) -> str:
    return str(email or "").strip().lower()


class CodexOtpD1MailService(BaseEmailService):
    def __init__(self, config: Dict[str, Any] = None, name: str = None):
        super().__init__(EmailServiceType.CODEX_OTP_D1, name)
        cfg = config or {}
        required_keys = ["domain", "cf_account_id", "cf_database_id", "cf_runtime_api_token"]
        missing = [key for key in required_keys if not cfg.get(key)]
        if missing:
            raise ValueError(f"缺少必需配置: {missing}")

        self.config = {
            "domain": "",
            "timeout": 30,
            "poll_interval": 3,
            "address_length": 12,
            **cfg,
        }
        self.reader = CodexOtpD1Reader(
            account_id=self.config["cf_account_id"],
            database_id=self.config["cf_database_id"],
            api_token=self.config["cf_runtime_api_token"],
            timeout=int(self.config.get("timeout") or 30),
        )

    def _generate_local_part(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        length = max(8, int(self.config.get("address_length") or 12))
        return "".join(random.choice(alphabet) for _ in range(length))

    def create_email(self, config: Dict[str, Any] = None) -> Dict[str, Any]:
        stage = str((config or {}).get("stage") or "register").strip()
        domain = canonicalize_email(f"x@{(config or {}).get('domain') or self.config.get('domain') or ''}").split("@", 1)[1]
        if not domain:
            raise EmailServiceError("缺少邮箱域名")
        email = canonicalize_email(f"{self._generate_local_part()}@{domain}")
        logger.info("Codex OTP D1 generated email: %s", email)
        self.update_status(True)
        return {
            "email": email,
            "service_id": f"{email}:{stage}",
            "id": email,
            "created_at": time.time(),
            "expires_at": None,
            "domain": domain,
        }

    def get_verification_code(
        self,
        email: str,
        email_id: str = None,
        timeout: int = 120,
        pattern: str = OTP_CODE_PATTERN,
        otp_sent_at: Optional[float] = None,
    ) -> Optional[str]:
        del pattern, otp_sent_at
        stage = None
        if email_id and isinstance(email_id, str) and ":" in email_id:
            _, stage = email_id.split(":", 1)

        normalized_email = canonicalize_email(email)
        logger.info("Codex OTP D1 polling code for email=%s stage=%s", normalized_email, stage or "register")

        start_time = time.time()
        poll_interval = max(1, int(self.config.get("poll_interval") or 3))
        while time.time() - start_time < timeout:
            try:
                row = self.reader.get_latest_code(email=normalized_email, stage=stage)
            except CodexOtpD1ReadError as exc:
                self.update_status(False, exc)
                logger.debug("Codex OTP D1 取码失败: %s", exc)
                time.sleep(poll_interval)
                continue

            if row and row.get("code"):
                self.update_status(True)
                return str(row["code"]).strip()
            time.sleep(poll_interval)
        return None

    def list_emails(self, **kwargs) -> List[Dict[str, Any]]:
        return []

    def delete_email(self, email_id: str) -> bool:
        return True

    def check_health(self) -> bool:
        try:
            self.reader.get_latest_code(email="healthcheck@example.invalid", stage=None)
            self.update_status(True)
            return True
        except Exception as exc:
            self.update_status(False, exc)
            return False

    def get_service_info(self) -> Dict[str, Any]:
        return {
            "domain": self.config.get("domain"),
            "poll_interval": self.config.get("poll_interval"),
            "cf_account_id": self.config.get("cf_account_id"),
            "cf_database_id": self.config.get("cf_database_id"),
            "has_runtime_api_token": bool(self.config.get("cf_runtime_api_token")),
        }
