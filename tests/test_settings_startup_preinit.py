from src.config import settings as settings_module


class DummySettings(settings_module.Settings):
    pass


def test_update_settings_tolerates_preinit_database(monkeypatch):
    original_settings = settings_module._settings
    original_pending = dict(settings_module._pending_db_settings)
    try:
        settings_module._settings = DummySettings()
        settings_module._pending_db_settings.clear()

        result = settings_module.update_settings(webui_host="127.0.0.1", webui_port=9001)

        assert result.webui_host == "127.0.0.1"
        assert result.webui_port == 9001
        assert settings_module._settings.webui_host == "127.0.0.1"
        assert settings_module._pending_db_settings["webui_host"] == "127.0.0.1"
    finally:
        settings_module._settings = original_settings
        settings_module._pending_db_settings.clear()
        settings_module._pending_db_settings.update(original_pending)


def test_save_settings_to_db_ignores_preinit_runtime_error(monkeypatch):
    original_pending = dict(settings_module._pending_db_settings)

    def fake_get_db():
        raise RuntimeError("数据库未初始化，请先调用 init_database()")

    monkeypatch.setattr("src.database.session.get_db", fake_get_db)
    settings_module._pending_db_settings.clear()
    settings_module._save_settings_to_db(webui_host="0.0.0.0")
    assert settings_module._pending_db_settings["webui_host"] == "0.0.0.0"
    settings_module._pending_db_settings.clear()
    settings_module._pending_db_settings.update(original_pending)


def test_get_data_dir_prefers_app_data_dir(monkeypatch, tmp_path):
    from src.core import utils as utils_module

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "app-data"))

    data_dir = utils_module.get_data_dir()

    assert data_dir == tmp_path / "app-data"
