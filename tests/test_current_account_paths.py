from pathlib import Path

from src.core import current_account as current_account_module
from src.database import crud as crud_module


def test_remove_current_account_snapshot_uses_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot = data_dir / "current_codex_account.json"
    snapshot.write_text('{"id": 1}', encoding="utf-8")

    monkeypatch.setattr(current_account_module, "get_data_dir", lambda: data_dir)

    current_account_module.remove_current_account_snapshot()

    assert not snapshot.exists()


def test_crud_snapshot_cleanup_uses_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot = data_dir / "current_codex_account.json"
    snapshot.write_text('{"id": 2}', encoding="utf-8")

    monkeypatch.setattr(crud_module, "get_data_dir", lambda: data_dir)

    crud_module._remove_current_account_snapshot_file()

    assert not snapshot.exists()
