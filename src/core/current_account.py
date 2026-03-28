from __future__ import annotations

import logging

from ..database import crud
from ..core.utils import get_data_dir

logger = logging.getLogger(__name__)
CURRENT_ACCOUNT_SETTING_KEY = "codex.current_account_id"


def get_current_account_id(db):
    setting = crud.get_setting(db, CURRENT_ACCOUNT_SETTING_KEY)
    if not setting or not setting.value:
        return None
    try:
        return int(setting.value)
    except (TypeError, ValueError):
        return None


def set_current_account_id(db, account_id: int):
    crud.set_setting(
        db,
        key=CURRENT_ACCOUNT_SETTING_KEY,
        value=str(account_id),
        description="当前切换中的 Codex 账号 ID",
        category="accounts",
    )


def clear_current_account_id(db) -> None:
    setting = crud.get_setting(db, CURRENT_ACCOUNT_SETTING_KEY)
    if setting:
        setting.value = ""
        db.commit()


def remove_current_account_snapshot() -> None:
    try:
        snapshot_path = get_data_dir() / "current_codex_account.json"
        if snapshot_path.exists():
            snapshot_path.unlink()
    except Exception as exc:
        logger.warning(f"删除 current_codex_account.json 失败: {exc}")


def clear_current_account_selection_if_matches(db, account_id: int) -> bool:
    current_id = get_current_account_id(db)
    if current_id != account_id:
        return False
    clear_current_account_id(db)
    remove_current_account_snapshot()
    return True
