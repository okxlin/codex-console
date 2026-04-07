import time
from typing import Callable, Dict, List, Optional, Tuple


_playwright_stats_cache: Dict[str, object] = {
    "timestamp": 0.0,
    "stats": None,
    "alerts": None,
}


def invalidate_playwright_stats_cache() -> None:
    _playwright_stats_cache.update({"timestamp": 0.0, "stats": None, "alerts": None})


def classify_lightweight_playwright_alert_hint(tasks: List[object]) -> str:
    samples = 0
    challenge_count = 0
    throttle_like_count = 0
    for task in tasks:
        result = task.result if isinstance(getattr(task, "result", None), dict) else {}
        metadata = result.get("metadata") if isinstance(result, dict) else {}
        if not isinstance(metadata, dict):
            continue
        diagnostics = metadata.get("playwright_diagnostics") if isinstance(metadata.get("playwright_diagnostics"), dict) else None
        if not diagnostics:
            continue
        samples += 1
        page_state = str(((diagnostics.get("browser_probe") or {}) if isinstance(diagnostics.get("browser_probe"), dict) else {}).get("page_state") or "")
        if page_state == "challenge_page":
            challenge_count += 1
        strategy = metadata.get("playwright_post_failure_strategy") if isinstance(metadata.get("playwright_post_failure_strategy"), dict) else {}
        if bool(strategy.get("needs_manual_review")):
            throttle_like_count += 1

    if samples >= 5 and challenge_count * 100 >= samples * 40:
        return "Playwright 风控挑战偏高"
    if samples >= 5 and throttle_like_count * 100 >= samples * 30:
        return "Playwright 人工复核/节流触发偏高"
    return ""


def summarize_playwright_alert_messages(alerts: dict) -> str:
    messages = list(alerts.get("messages") or []) if isinstance(alerts, dict) else []
    return "；".join(str(item).strip() for item in messages if str(item).strip())


def get_cached_playwright_stats(
    tasks_provider: Optional[Callable[[], List[object]]] = None,
    *,
    stats_builder: Callable[[List[object], int], dict],
    alerts_builder: Callable[[dict], dict],
    ttl_seconds: int = 15,
) -> Tuple[dict, dict]:
    now = time.time()
    cached_at = float(_playwright_stats_cache.get("timestamp") or 0.0)
    cached_stats = _playwright_stats_cache.get("stats")
    cached_alerts = _playwright_stats_cache.get("alerts")
    if cached_stats is not None and cached_alerts is not None and (now - cached_at) < max(1, ttl_seconds):
        return dict(cached_stats), dict(cached_alerts)

    tasks = tasks_provider() if callable(tasks_provider) else []
    stats = stats_builder(tasks, 50)
    alerts = alerts_builder(stats)
    _playwright_stats_cache["timestamp"] = now
    _playwright_stats_cache["stats"] = dict(stats)
    _playwright_stats_cache["alerts"] = dict(alerts)
    return stats, alerts
