import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from .utils import get_data_dir
from ..config.settings import get_settings


logger = logging.getLogger(__name__)


def get_playwright_artifacts_dir() -> Path:
    base_dir = get_data_dir() / "playwright-artifacts"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def build_failure_screenshot_path(task_uuid: str = "", stage: str = "failed") -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_task = "".join(ch for ch in str(task_uuid or "adhoc") if ch.isalnum() or ch in {"-", "_"})[:40] or "adhoc"
    safe_stage = "".join(ch for ch in str(stage or "failed") if ch.isalnum() or ch in {"-", "_"})[:30] or "failed"
    return get_playwright_artifacts_dir() / f"{ts}-{safe_task}-{safe_stage}.png"


def artifact_to_metadata(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    try:
        relative = str(path.relative_to(get_data_dir()))
    except Exception:
        relative = str(path)
    return {
        "type": "screenshot",
        "path": relative,
        "size_bytes": int(stat.st_size),
        "created_at": int(stat.st_mtime),
    }


def cleanup_playwright_artifacts() -> Dict[str, int]:
    settings = get_settings()
    artifacts_dir = get_playwright_artifacts_dir()
    retention_days = max(1, int(getattr(settings, "registration_playwright_artifact_retention_days", 7) or 7))
    max_total_size_mb = max(64, int(getattr(settings, "registration_playwright_artifact_max_total_size_mb", 512) or 512))
    max_total_files = max(10, int(getattr(settings, "registration_playwright_artifact_max_total_files", 500) or 500))
    now = time.time()
    max_age_seconds = retention_days * 86400

    files: List[Path] = [path for path in artifacts_dir.glob("*.png") if path.is_file()]
    deleted_total = 0
    deleted_expired = 0
    deleted_limited = 0

    for path in files:
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
                deleted_total += 1
                deleted_expired += 1
        except Exception as exc:
            logger.warning("清理过期 Playwright artifact 失败: %s", exc)

    files = [path for path in artifacts_dir.glob("*.png") if path.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)

    def _current_size(paths: List[Path]) -> int:
        total = 0
        for item in paths:
            try:
                total += int(item.stat().st_size)
            except Exception:
                continue
        return total

    size_limit_bytes = max_total_size_mb * 1024 * 1024
    total_size = _current_size(files)
    while files and (len(files) > max_total_files or total_size > size_limit_bytes):
        victim = files.pop(0)
        try:
            size = int(victim.stat().st_size)
        except Exception:
            size = 0
        try:
            victim.unlink(missing_ok=True)
            deleted_total += 1
            deleted_limited += 1
            total_size = max(0, total_size - size)
        except Exception as exc:
            logger.warning("按配额清理 Playwright artifact 失败: %s", exc)

    return {
        "deleted_total": deleted_total,
        "deleted_expired": deleted_expired,
        "deleted_limited": deleted_limited,
        "remaining": len([path for path in artifacts_dir.glob("*.png") if path.is_file()]),
    }
