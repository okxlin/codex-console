"""
CPA (Codex Protocol API) 上传功能
"""

import json
import logging
import base64
import time
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from urllib.parse import quote

from curl_cffi import requests as cffi_requests
from curl_cffi import CurlMime

from ...database.session import get_db
from ...database.models import Account
from ...config.settings import get_settings
from ..timezone_utils import utcnow_naive

logger = logging.getLogger(__name__)


def _extract_account_id_from_access_token(access_token: str) -> str:
    raw = str(access_token or "").strip()
    if raw.count(".") < 2:
        return ""
    try:
        payload = raw.split(".")[1]
        payload += "=" * ((4 - (len(payload) % 4)) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        claims = json.loads(decoded.decode("utf-8"))
        auth_claims = claims.get("https://api.openai.com/auth") or {}
        return str(auth_claims.get("chatgpt_account_id") or claims.get("chatgpt_account_id") or "").strip()
    except Exception:
        return ""


def _build_mock_id_token(email: str, account_id: str, exp_ts: int) -> str:
    now_ts = int(time.time())
    fake_id_payload = {
        "email": email,
        "email_verified": True,
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": "free",
        },
        "exp": exp_ts,
        "iat": now_ts,
        "sub": "auth0|mocked_sub_" + (account_id[:8] if account_id else "unknown"),
    }
    hdr_b64 = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode("ascii").rstrip("=")
    pyld_b64 = base64.urlsafe_b64encode(json.dumps(fake_id_payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    fake_sig = base64.urlsafe_b64encode(b'mocked_signature_for_pool_compatibility').decode("ascii").rstrip("=")
    return f"{hdr_b64}.{pyld_b64}.{fake_sig}"


def _build_cpa_proxies(proxy_url: Optional[str]) -> Optional[dict[str, str]]:
    normalized = str(proxy_url or "").strip()
    if not normalized:
        return None
    return {"http": normalized, "https": normalized}


def _extract_auth_file_names(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        files = payload.get("files", [])
    elif isinstance(payload, list):
        files = payload
    else:
        return []

    names: list[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        for key in ("name", "filename", "file_name", "path"):
            value = str(item.get(key) or "").strip()
            if value:
                names.append(value)
                break
    return names


def _match_auth_file_name(payload: Any, email: str) -> Optional[str]:
    target_email = str(email or "").strip().lower()
    if not target_email:
        return None

    expected_name = f"{target_email}.json"
    for raw_name in _extract_auth_file_names(payload):
        normalized_name = raw_name.split("/")[-1].strip()
        if normalized_name.lower() == expected_name:
            return normalized_name
    return None


def _normalize_cpa_auth_files_url(api_url: str) -> str:
    """将用户填写的 CPA 地址规范化为 auth-files 接口地址。"""
    normalized = (api_url or "").strip().rstrip("/")
    lower_url = normalized.lower()

    if not normalized:
        return ""

    if lower_url.endswith("/auth-files"):
        return normalized

    if lower_url.endswith("/v0/management") or lower_url.endswith("/management"):
        return f"{normalized}/auth-files"

    if lower_url.endswith("/v0"):
        return f"{normalized}/management/auth-files"

    return f"{normalized}/v0/management/auth-files"


def _build_cpa_headers(api_token: str, content_type: Optional[str] = None) -> dict:
    headers = {
        "Authorization": f"Bearer {api_token}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _extract_cpa_error(response) -> str:
    error_msg = f"上传失败: HTTP {response.status_code}"
    try:
        error_detail = response.json()
        if isinstance(error_detail, dict):
            error_msg = error_detail.get("message", error_msg)
    except Exception:
        error_msg = f"{error_msg} - {response.text[:200]}"
    return error_msg


def _post_cpa_auth_file_multipart(upload_url: str, filename: str, file_content: bytes, api_token: str, proxy_url: Optional[str] = None):
    mime = CurlMime()
    mime.addpart(
        name="file",
        data=file_content,
        filename=filename,
        content_type="application/json",
    )

    return cffi_requests.post(
        upload_url,
        multipart=mime,
        headers=_build_cpa_headers(api_token),
        proxies=_build_cpa_proxies(proxy_url),
        timeout=30,
        impersonate="chrome110",
    )


def _post_cpa_auth_file_raw_json(upload_url: str, filename: str, file_content: bytes, api_token: str, proxy_url: Optional[str] = None):
    raw_upload_url = f"{upload_url}?name={quote(filename)}"
    return cffi_requests.post(
        raw_upload_url,
        data=file_content,
        headers=_build_cpa_headers(api_token, content_type="application/json"),
        proxies=_build_cpa_proxies(proxy_url),
        timeout=30,
        impersonate="chrome110",
    )


def _delete_cpa_auth_file_by_name(upload_url: str, filename: str, api_token: str, proxy_url: Optional[str] = None):
    encoded_name = quote(filename)
    candidates = [
        f"{upload_url}?name={encoded_name}",
        f"{upload_url}/{encoded_name}",
    ]
    last_response = None
    proxies = _build_cpa_proxies(proxy_url)
    for candidate in candidates:
        response = cffi_requests.delete(
            candidate,
            headers=_build_cpa_headers(api_token),
            proxies=proxies,
            timeout=15,
            impersonate="chrome110",
        )
        last_response = response
        if response.status_code in (200, 202, 204):
            return response
        if response.status_code == 404 and candidate != candidates[-1]:
            continue
        if response.status_code == 404:
            return response
    return last_response


def probe_cpaproxyapi_compatibility(
    api_url: str,
    api_token: str,
    email: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> dict:
    """探测 CLIProxyAPI auth-files 列表/删除接口兼容性。"""
    normalized_url = _normalize_cpa_auth_files_url(api_url)
    result = {
        "normalized_auth_files_url": normalized_url,
        "email": str(email or "").strip().lower() or None,
        "list_probe": {
            "ok": False,
            "message": None,
            "payload_kind": None,
            "file_count": 0,
            "sample_names": [],
            "name_fields_seen": [],
        },
        "delete_probe": {
            "filename": None,
            "strategies": [],
            "recommended_strategy": None,
        },
    }

    success, payload, message = list_cpa_auth_files(api_url, api_token, proxy_url=proxy_url)
    result["list_probe"]["ok"] = bool(success)
    result["list_probe"]["message"] = message
    if not success:
        return result

    if isinstance(payload, dict):
        files = payload.get("files", [])
        result["list_probe"]["payload_kind"] = "dict"
    elif isinstance(payload, list):
        files = payload
        result["list_probe"]["payload_kind"] = "list"
    else:
        files = []
        result["list_probe"]["payload_kind"] = type(payload).__name__

    sample_names: list[str] = []
    name_fields_seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            continue
        for key in ("name", "filename", "file_name", "path"):
            value = str(item.get(key) or "").strip()
            if value:
                name_fields_seen.add(key)
                if len(sample_names) < 10:
                    sample_names.append(value)
                break

    result["list_probe"]["file_count"] = len(files) if isinstance(files, list) else 0
    result["list_probe"]["sample_names"] = sample_names
    result["list_probe"]["name_fields_seen"] = sorted(name_fields_seen)

    if not result["email"]:
        return result

    filename = _match_auth_file_name(payload, result["email"])
    result["delete_probe"]["filename"] = filename
    if not filename:
        return result

    encoded_name = quote(filename)
    strategies = [
        ("query_name", f"{normalized_url}?name={encoded_name}"),
        ("path_segment", f"{normalized_url}/{encoded_name}"),
        ("query_filename", f"{normalized_url}?filename={encoded_name}"),
        ("query_path", f"{normalized_url}?path={encoded_name}"),
    ]
    proxies = _build_cpa_proxies(proxy_url)
    for strategy_name, candidate_url in strategies:
        probe_item = {
            "strategy": strategy_name,
            "url": candidate_url,
            "status_code": None,
            "ok": False,
            "response_hint": None,
        }
        try:
            response = cffi_requests.delete(
                candidate_url,
                headers=_build_cpa_headers(api_token),
                proxies=proxies,
                timeout=15,
                impersonate="chrome110",
            )
            probe_item["status_code"] = response.status_code
            probe_item["ok"] = response.status_code in (200, 202, 204)
            if response.status_code >= 400:
                probe_item["response_hint"] = _extract_cpa_error(response)
            elif response.status_code not in (200, 202, 204):
                probe_item["response_hint"] = f"HTTP {response.status_code}"
        except Exception as exc:
            probe_item["response_hint"] = str(exc)
        result["delete_probe"]["strategies"].append(probe_item)

    for item in result["delete_probe"]["strategies"]:
        if item["ok"]:
            result["delete_probe"]["recommended_strategy"] = item["strategy"]
            break

    return result


def generate_token_json(account: Account) -> dict:
    """
    生成 CPA 格式的 Token JSON

    Args:
        account: 账号模型实例

    Returns:
        CPA 格式的 Token 字典
    """
    access_token = account.access_token or ""
    account_id = account.account_id or _extract_account_id_from_access_token(access_token)
    if not account_id and access_token:
        account_id = access_token

    exp_ts = int(time.time()) + 2592000
    if access_token and access_token.count(".") >= 2:
        try:
            payload = access_token.split(".")[1]
            payload += "=" * ((4 - (len(payload) % 4)) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
            exp_ts = int(claims.get("exp") or exp_ts)
        except Exception:
            pass

    id_token = account.id_token or _build_mock_id_token(account.email, account_id, exp_ts)

    return {
        "type": "codex",
        "email": account.email,
        "expired": account.expires_at.strftime("%Y-%m-%dT%H:%M:%S+08:00") if account.expires_at else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp_ts)),
        "id_token": id_token,
        "account_id": account_id,
        "access_token": access_token,
        "last_refresh": account.last_refresh.strftime("%Y-%m-%dT%H:%M:%S+08:00") if account.last_refresh else "",
        "refresh_token": account.refresh_token or account.session_token or "",
    }


def upload_to_cpa(
    token_data: dict,
    proxy: str = None,
    api_url: str = None,
    api_token: str = None,
) -> Tuple[bool, str]:
    """
    上传单个账号到 CPA 管理平台

    Args:
        token_data: Token JSON 数据
        proxy: 可选代理 URL
        api_url: 指定 CPA API URL（优先于全局配置）
        api_token: 指定 CPA API Token（优先于全局配置）

    Returns:
        (成功标志, 消息或错误信息)
    """
    settings = get_settings()

    # 优先使用传入的参数，否则退回全局配置
    effective_url = api_url or settings.cpa_api_url
    effective_token = api_token or (settings.cpa_api_token.get_secret_value() if settings.cpa_api_token else "")

    # 仅当未指定服务时才检查全局启用开关
    if not api_url and not settings.cpa_enabled:
        return False, "CPA 上传未启用"

    if not effective_url:
        return False, "CPA API URL 未配置"

    if not effective_token:
        return False, "CPA API Token 未配置"

    upload_url = _normalize_cpa_auth_files_url(effective_url)

    filename = f"{token_data['email']}.json"
    file_content = json.dumps(token_data, ensure_ascii=False, indent=2).encode("utf-8")

    try:
        response = _post_cpa_auth_file_multipart(
            upload_url,
            filename,
            file_content,
            effective_token,
            proxy,
        )

        if response.status_code in (200, 201):
            return True, "上传成功"

        if response.status_code in (404, 405, 415):
            logger.warning("CPA multipart 上传失败，尝试原始 JSON 回退: %s", response.status_code)
            fallback_response = _post_cpa_auth_file_raw_json(
                upload_url,
                filename,
                file_content,
                effective_token,
                proxy,
            )
            if fallback_response.status_code in (200, 201):
                return True, "上传成功"
            response = fallback_response

        return False, _extract_cpa_error(response)

    except Exception as e:
        logger.error(f"CPA 上传异常: {e}")
        return False, f"上传异常: {str(e)}"


def batch_upload_to_cpa(
    account_ids: List[int],
    proxy: str = None,
    api_url: str = None,
    api_token: str = None,
) -> dict:
    """
    批量上传账号到 CPA 管理平台

    Args:
        account_ids: 账号 ID 列表
        proxy: 可选的代理 URL
        api_url: 指定 CPA API URL（优先于全局配置）
        api_token: 指定 CPA API Token（优先于全局配置）

    Returns:
        包含成功/失败统计和详情的字典
    """
    results = {
        "success_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "details": []
    }

    with get_db() as db:
        for account_id in account_ids:
            account = db.query(Account).filter(Account.id == account_id).first()

            if not account:
                results["failed_count"] += 1
                results["details"].append({
                    "id": account_id,
                    "email": None,
                    "success": False,
                    "error": "账号不存在"
                })
                continue

            # 检查是否已有 Token
            if not account.access_token:
                results["skipped_count"] += 1
                results["details"].append({
                    "id": account_id,
                    "email": account.email,
                    "success": False,
                    "error": "缺少 Token"
                })
                continue

            # 生成 Token JSON
            token_data = generate_token_json(account)

            # 上传
            success, message = upload_to_cpa(token_data, proxy, api_url=api_url, api_token=api_token)

            if success:
                # 更新数据库状态
                account.cpa_uploaded = True
                account.cpa_uploaded_at = utcnow_naive()
                db.commit()

                results["success_count"] += 1
                results["details"].append({
                    "id": account_id,
                    "email": account.email,
                    "success": True,
                    "message": message
                })
            else:
                results["failed_count"] += 1
                results["details"].append({
                    "id": account_id,
                    "email": account.email,
                    "success": False,
                    "error": message
                })

    return results


def list_cpa_auth_files(api_url: str, api_token: str, proxy_url: Optional[str] = None) -> Tuple[bool, Any, str]:
    """列出远端 CPA auth-files 清单。"""
    if not api_url:
        return False, None, "API URL 不能为空"

    if not api_token:
        return False, None, "API Token 不能为空"

    list_url = _normalize_cpa_auth_files_url(api_url)
    headers = _build_cpa_headers(api_token)

    try:
        response = cffi_requests.get(
            list_url,
            headers=headers,
            proxies=_build_cpa_proxies(proxy_url),
            timeout=10,
            impersonate="chrome110",
        )
        if response.status_code != 200:
            return False, None, _extract_cpa_error(response)
        return True, response.json(), "ok"
    except cffi_requests.exceptions.ConnectionError as e:
        return False, None, f"无法连接到服务器: {str(e)}"
    except cffi_requests.exceptions.Timeout:
        return False, None, "连接超时，请检查网络配置"
    except Exception as e:
        logger.error("获取 CPA auth-files 清单异常: %s", e)
        return False, None, f"获取 auth-files 失败: {str(e)}"


def count_ready_cpa_auth_files(payload: Any) -> int:
    """统计可用于补货判断的认证文件数量。"""
    if isinstance(payload, dict):
        files = payload.get("files", [])
    elif isinstance(payload, list):
        files = payload
    else:
        return 0

    ready_count = 0
    for item in files:
        if not isinstance(item, dict):
            continue

        status = str(item.get("status", "")).strip().lower()
        provider = str(item.get("provider") or item.get("type") or "").strip().lower()
        disabled = bool(item.get("disabled", False))
        unavailable = bool(item.get("unavailable", False))

        if disabled or unavailable:
            continue

        if provider != "codex":
            continue

        if status and status not in {"ready", "active"}:
            continue

        ready_count += 1

    return ready_count


def delete_cpa_auth_file(api_url: str, api_token: str, email: str, proxy_url: Optional[str] = None) -> Tuple[bool, str]:
    """按邮箱删除远端 CPA auth-files 中对应的认证文件。"""
    if not api_url:
        return False, "API URL 不能为空"
    if not api_token:
        return False, "API Token 不能为空"
    if not email:
        return False, "邮箱不能为空"

    upload_url = _normalize_cpa_auth_files_url(api_url)
    success, payload, message = list_cpa_auth_files(api_url, api_token, proxy_url=proxy_url)
    if not success:
        return False, message

    filename = _match_auth_file_name(payload, email)
    if not filename:
        return False, f"未在远端 auth-files 中匹配到 {email} 对应文件"

    try:
        response = _delete_cpa_auth_file_by_name(upload_url, filename, api_token, proxy_url=proxy_url)
        if response is None:
            return False, "删除失败：未收到响应"
        if response.status_code in (200, 202, 204):
            return True, f"已删除远端 CPA 文件 {filename}"
        if response.status_code == 404:
            return False, f"远端删除接口未找到文件 {filename}"
        return False, _extract_cpa_error(response)
    except Exception as e:
        logger.error("删除 CPA auth-file 异常: %s", e)
        return False, f"删除 auth-file 失败: {str(e)}"


def test_cpa_connection(api_url: str, api_token: str, proxy: str = None) -> Tuple[bool, str]:
    """
    测试 CPA 连接

    Args:
        api_url: CPA API URL
        api_token: CPA API Token
        proxy: 可选代理 URL

    Returns:
        (成功标志, 消息)
    """
    if not api_url:
        return False, "API URL 不能为空"

    if not api_token:
        return False, "API Token 不能为空"

    test_url = _normalize_cpa_auth_files_url(api_url)
    headers = _build_cpa_headers(api_token)

    try:
        proxies = _build_cpa_proxies(proxy)
        response = cffi_requests.get(
            test_url,
            headers=headers,
            proxies=proxies,
            timeout=10,
            impersonate="chrome110",
        )

        if response.status_code == 200:
            return True, "CPA 连接测试成功"
        if response.status_code == 401:
            return False, "连接成功，但 API Token 无效"
        if response.status_code == 403:
            return False, "连接成功，但服务端未启用远程管理或当前 Token 无权限"
        if response.status_code == 404:
            return False, "未找到 CPA auth-files 接口，请检查 API URL 是否填写为根地址、/v0/management 或完整 auth-files 地址"
        if response.status_code == 503:
            return False, "连接成功，但服务端认证管理器不可用"

        return False, f"服务器返回异常状态码: {response.status_code}"

    except cffi_requests.exceptions.ConnectionError as e:
        return False, f"无法连接到服务器: {str(e)}"
    except cffi_requests.exceptions.Timeout:
        return False, "连接超时，请检查网络配置"
    except Exception as e:
        return False, f"连接测试失败: {str(e)}"
