import json
import logging
import os
import time
from typing import Any, Dict


logger = logging.getLogger(__name__)


def capture_chatgpt_session_with_playwright(cookies_str: str, session_token: str = "", did: str = "", callback_url: str = "", proxy_url: str = "", timeout_seconds: int = 45) -> Dict[str, Any]:
    """使用 Playwright 复用现有 cookies，从浏览器上下文侧信道提取 /api/auth/session。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return {
            "success": False,
            "error": "playwright not installed",
            "stage": "import",
        }

    from .browser_bind import _add_cookies_resilient, _build_playwright_cookie_items, _find_chrome_binary

    browser = None
    context = None
    page = None
    binary = _find_chrome_binary() or None
    try:
        with sync_playwright() as p:
            headed_env = str(os.environ.get("PLAYWRIGHT_HEADED") or "").strip().lower()
            force_headed = headed_env in {"1", "true", "yes", "on"}
            launch_kwargs = {
                "headless": not force_headed,
            }
            if binary:
                launch_kwargs["executable_path"] = binary
            proxy = str(proxy_url or "").strip()
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            cookies = _build_playwright_cookie_items(cookies_str, session_token, did)
            if cookies:
                _add_cookies_resilient(context, cookies, stage="session_capture")

            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)

            probe = {
                "proxy": proxy,
                "ipify_before": "",
                "chatgpt_title": "",
                "chatgpt_url": "",
                "chatgpt_body_hint": "",
                "page_state": "",
            }

            try:
                ip_page = context.new_page()
                ip_page.set_default_timeout(timeout_seconds * 1000)
                ip_page.goto("https://api.ipify.org?format=json", wait_until="domcontentloaded", timeout=30000)
                probe["ipify_before"] = ip_page.locator("body").inner_text(timeout=3000)
                ip_page.close()
            except Exception:
                pass

            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
            try:
                probe["chatgpt_title"] = page.title()
            except Exception:
                pass
            probe["chatgpt_url"] = page.url
            try:
                probe["chatgpt_body_hint"] = page.locator("body").inner_text(timeout=3000)[:300]
            except Exception:
                probe["chatgpt_body_hint"] = page.content()[:300]
            body_hint_lower = str(probe["chatgpt_body_hint"] or "").lower()
            if "just a moment" in body_hint_lower or "checking your browser" in body_hint_lower:
                probe["page_state"] = "challenge_page"
            elif ("log in" in body_hint_lower or "注册" in body_hint_lower or "登录" in body_hint_lower) and "chatgpt" in body_hint_lower:
                probe["page_state"] = "guest_home"
            elif "history" in body_hint_lower or "历史" in body_hint_lower or "health" in body_hint_lower:
                probe["page_state"] = "app_home"
            else:
                probe["page_state"] = "unknown"
            callback = str(callback_url or "").strip()
            if callback and "/api/auth/callback/openai" in callback:
                try:
                    page.goto(callback, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass

            def _fetch_session_via_page(target_page) -> str:
                payload = None
                try:
                    payload = target_page.evaluate(
                        """
                        async () => {
                          const res = await fetch('/api/auth/session', {
                            method: 'GET',
                            credentials: 'include',
                            headers: { 'accept': 'application/json' },
                          });
                          const text = await res.text();
                          return { status: res.status, text };
                        }
                        """
                    )
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    return str(payload.get("text") or "")
                return ""

            raw_text = ""
            for _ in range(3):
                raw_text = _fetch_session_via_page(page)
                if raw_text.strip().startswith("{"):
                    break
                try:
                    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                time.sleep(1.2)

            if not raw_text.strip().startswith("{"):
                api_page = context.new_page()
                try:
                    api_page.set_default_timeout(timeout_seconds * 1000)
                    api_page.goto("https://chatgpt.com/api/auth/session", wait_until="domcontentloaded", timeout=60000)
                    raw_text = _fetch_session_via_page(api_page)
                    if not raw_text.strip().startswith("{"):
                        try:
                            body = api_page.locator("body")
                            raw_text = body.inner_text(timeout=5000)
                        except Exception:
                            raw_text = api_page.content()
                finally:
                    try:
                        api_page.close()
                    except Exception:
                        pass

            start_idx = raw_text.find("{")
            end_idx = raw_text.rfind("}") + 1
            if start_idx == -1 or end_idx <= start_idx:
                cookies_now = context.cookies()
                return {
                    "success": False,
                    "error": "api/auth/session did not return json body",
                    "stage": "parse",
                    "current_url": page.url,
                    "probe": probe,
                    "cookies": cookies_now,
                }

            data = json.loads(raw_text[start_idx:end_idx])
            cookies_now = context.cookies()
            return {
                "success": True,
                "stage": "captured",
                "current_url": page.url,
                "probe": probe,
                "session": data,
                "cookies": cookies_now,
                "access_token": str(data.get("accessToken") or "").strip(),
                "session_token": str(data.get("sessionToken") or "").strip(),
            }
    except Exception as e:
        logger.warning("browser session capture failed: %s", e)
        return {
            "success": False,
            "error": str(e),
            "stage": "runtime",
            "probe": {"proxy": proxy_url},
        }
    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
