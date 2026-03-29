"""Cloudflare D1 read-only code reader for Codex OTP D1 mode."""

from __future__ import annotations

from typing import Any, Dict, Optional

from curl_cffi import requests as cffi_requests


class CodexOtpD1ReadError(Exception):
    """D1 query failed."""


class CodexOtpD1Reader:
    def __init__(self, *, account_id: str, database_id: str, api_token: str, timeout: int = 30):
        self.account_id = str(account_id or "").strip()
        self.database_id = str(database_id or "").strip()
        self.api_token = str(api_token or "").strip()
        self.timeout = int(timeout or 30)
        if not self.account_id or not self.database_id or not self.api_token:
            raise CodexOtpD1ReadError("缺少 D1 读取所需配置")

        self.session = cffi_requests.Session(headers={
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        })

    def _query(self, sql: str, params: list[Any]) -> list[Dict[str, Any]]:
        response = self.session.post(
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/d1/database/{self.database_id}/query",
            json={"sql": sql, "params": params},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = response.text[:240]
            raise CodexOtpD1ReadError(f"D1 查询失败: HTTP {response.status_code} - {payload}")

        payload = response.json()
        if not payload.get("success", False):
            raise CodexOtpD1ReadError(f"D1 查询失败: {payload.get('errors') or payload}")

        result = payload.get("result") or []
        if not result:
            return []
        first = result[0] if isinstance(result, list) else result
        rows = first.get("results") if isinstance(first, dict) else None
        return rows or []

    def get_latest_code(self, *, email: str, stage: str | None = None) -> Optional[Dict[str, Any]]:
        normalized_email = str(email or "").strip().lower()
        sql = (
            "SELECT id, code, stage, received_at FROM codes "
            "WHERE email = ? AND consumed = 0 "
        )
        params: list[Any] = [normalized_email]
        if stage:
            sql += "AND stage = ? "
            params.append(stage)
        sql += "ORDER BY received_at DESC LIMIT 1"
        rows = self._query(sql, params)
        if not rows and normalized_email and "@" in normalized_email:
            local_part, domain = normalized_email.split("@", 1)
            fallback_sql = (
                "SELECT id, code, stage, received_at FROM codes "
                "WHERE lower(trim(email)) = ? AND consumed = 0 "
            )
            fallback_params: list[Any] = [f"{local_part}@{domain}".lower()]
            if stage:
                fallback_sql += "AND stage = ? "
                fallback_params.append(stage)
            fallback_sql += "ORDER BY received_at DESC LIMIT 1"
            rows = self._query(fallback_sql, fallback_params)
        return rows[0] if rows else None
