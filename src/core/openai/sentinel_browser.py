from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from ...config.settings import get_settings
from .browser_bind import _find_chrome_binary


logger = logging.getLogger(__name__)


_FLOW_PAGE_URLS = {
    "username_password_create": "https://auth.openai.com/create-account/password",
    "password_verify": "https://auth.openai.com/log-in/password",
    "email_otp_validate": "https://auth.openai.com/email-verification",
    "oauth_create_account": "https://auth.openai.com/about-you",
    "authorize_continue": "https://auth.openai.com/",
}


def _should_launch_playwright_headed() -> bool:
    headed_env = str(os.environ.get("PLAYWRIGHT_HEADED") or "").strip().lower()
    if headed_env:
        return headed_env in {"1", "true", "yes", "on"}

    try:
        return bool(get_settings().registration_playwright_headed)
    except Exception as exc:
        logger.debug("load persisted playwright headed setting failed: %s", exc)
        return False


def get_sentinel_token_via_browser(
    flow: str,
    *,
    proxy_url: str = "",
    page_url: str = "",
    timeout_seconds: int = 30,
) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    target_flow = str(flow or "authorize_continue").strip() or "authorize_continue"
    target_url = str(page_url or _FLOW_PAGE_URLS.get(target_flow) or _FLOW_PAGE_URLS["authorize_continue"]).strip()
    binary = _find_chrome_binary() or None

    browser = None
    context = None
    page = None
    try:
        with sync_playwright() as p:
            force_headed = _should_launch_playwright_headed()
            launch_kwargs: Dict[str, Any] = {"headless": not force_headed}
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
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = context.new_page()
            page.set_default_timeout(timeout_seconds * 1000)
            page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            try:
                page.wait_for_function(
                    "() => Boolean(window.SentinelSDK && typeof window.SentinelSDK.token === 'function')",
                    timeout=10000,
                )
            except Exception:
                pass
            token = page.evaluate(
                """
                async (flowName) => {
                  if (!window.SentinelSDK || typeof window.SentinelSDK.token !== 'function') {
                    return '';
                  }
                  try {
                    const value = await window.SentinelSDK.token(flowName);
                    return typeof value === 'string' ? value : '';
                  } catch (error) {
                    return '';
                  }
                }
                """,
                target_flow,
            )
            value = str(token or "").strip()
            return value or None
    except Exception as exc:
        logger.warning("browser sentinel token failed for flow=%s: %s", target_flow, exc)
        return None
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
