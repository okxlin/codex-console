from types import SimpleNamespace

from src.core.openai import sentinel_browser


def test_should_launch_playwright_headed_defaults_false(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_HEADED", raising=False)
    monkeypatch.setattr(sentinel_browser, "get_settings", lambda: SimpleNamespace(registration_playwright_headed=False))

    assert sentinel_browser._should_launch_playwright_headed() is False


def test_should_launch_playwright_headed_uses_persisted_setting(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_HEADED", raising=False)
    monkeypatch.setattr(sentinel_browser, "get_settings", lambda: SimpleNamespace(registration_playwright_headed=True))

    assert sentinel_browser._should_launch_playwright_headed() is True


def test_should_launch_playwright_headed_env_overrides_persisted_setting(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_HEADED", "false")
    monkeypatch.setattr(sentinel_browser, "get_settings", lambda: SimpleNamespace(registration_playwright_headed=True))

    assert sentinel_browser._should_launch_playwright_headed() is False


def test_should_launch_playwright_headed_env_true_overrides_persisted_false(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_HEADED", "true")
    monkeypatch.setattr(sentinel_browser, "get_settings", lambda: SimpleNamespace(registration_playwright_headed=False))

    assert sentinel_browser._should_launch_playwright_headed() is True
