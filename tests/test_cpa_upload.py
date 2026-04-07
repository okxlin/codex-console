from src.core.upload import cpa_upload


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeMime:
    def __init__(self):
        self.parts = []

    def addpart(self, **kwargs):
        self.parts.append(kwargs)


def test_upload_to_cpa_accepts_management_root_url(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=201)

    monkeypatch.setattr(cpa_upload, "CurlMime", FakeMime)
    monkeypatch.setattr(cpa_upload.cffi_requests, "post", fake_post)

    success, message = cpa_upload.upload_to_cpa(
        {"email": "tester@example.com"},
        api_url="https://cpa.example.com/v0/management",
        api_token="token-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["url"] == "https://cpa.example.com/v0/management/auth-files"


def test_upload_to_cpa_does_not_double_append_full_endpoint(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=201)

    monkeypatch.setattr(cpa_upload, "CurlMime", FakeMime)
    monkeypatch.setattr(cpa_upload.cffi_requests, "post", fake_post)

    success, _ = cpa_upload.upload_to_cpa(
        {"email": "tester@example.com"},
        api_url="https://cpa.example.com/v0/management/auth-files",
        api_token="token-123",
    )

    assert success is True
    assert calls[0]["url"] == "https://cpa.example.com/v0/management/auth-files"


def test_upload_to_cpa_falls_back_to_raw_json_when_multipart_returns_404(monkeypatch):
    calls = []
    responses = [
        FakeResponse(status_code=404, text="404 page not found"),
        FakeResponse(status_code=200, payload={"status": "ok"}),
    ]

    def fake_post(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr(cpa_upload, "CurlMime", FakeMime)
    monkeypatch.setattr(cpa_upload.cffi_requests, "post", fake_post)

    success, message = cpa_upload.upload_to_cpa(
        {"email": "tester@example.com", "type": "codex"},
        api_url="https://cpa.example.com",
        api_token="token-123",
    )

    assert success is True
    assert message == "上传成功"
    assert calls[0]["kwargs"]["multipart"] is not None
    assert calls[1]["url"] == "https://cpa.example.com/v0/management/auth-files?name=tester%40example.com.json"
    assert calls[1]["kwargs"]["headers"]["Content-Type"] == "application/json"
    assert calls[1]["kwargs"]["data"].startswith(b"{")


def test_test_cpa_connection_uses_get_and_normalized_url(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200, payload={"files": []})

    monkeypatch.setattr(cpa_upload.cffi_requests, "get", fake_get)

    success, message = cpa_upload.test_cpa_connection(
        "https://cpa.example.com/v0/management",
        "token-123",
    )

    assert success is True
    assert message == "CPA 连接测试成功"
    assert calls[0]["url"] == "https://cpa.example.com/v0/management/auth-files"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer token-123"


def test_test_cpa_connection_passes_proxy(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=200, payload={"files": []})

    monkeypatch.setattr(cpa_upload.cffi_requests, "get", fake_get)

    success, message = cpa_upload.test_cpa_connection(
        "https://cpa.example.com/v0/management",
        "token-123",
        "http://127.0.0.1:7890",
    )

    assert success is True
    assert message == "CPA 连接测试成功"
    assert calls[0]["kwargs"]["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_delete_cpa_auth_file_uses_matching_remote_name(monkeypatch):
    monkeypatch.setattr(
        cpa_upload,
        "list_cpa_auth_files",
        lambda api_url, api_token, proxy_url=None: (
            True,
            {"files": [{"name": "tester@example.com.json"}]},
            "ok",
        ),
    )

    calls = []

    def fake_delete(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return FakeResponse(status_code=204)

    monkeypatch.setattr(cpa_upload.cffi_requests, "delete", fake_delete)

    success, message = cpa_upload.delete_cpa_auth_file(
        "https://cpa.example.com",
        "token-123",
        "tester@example.com",
        proxy_url="http://127.0.0.1:7890",
    )

    assert success is True
    assert "tester@example.com.json" in message
    assert calls[0]["url"] == "https://cpa.example.com/v0/management/auth-files?name=tester%40example.com.json"
    assert calls[0]["kwargs"]["proxies"] == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_delete_cpa_auth_file_fails_when_remote_name_missing(monkeypatch):
    monkeypatch.setattr(
        cpa_upload,
        "list_cpa_auth_files",
        lambda api_url, api_token, proxy_url=None: (True, {"files": [{"name": "other@example.com.json"}]}, "ok"),
    )

    success, message = cpa_upload.delete_cpa_auth_file(
        "https://cpa.example.com",
        "token-123",
        "tester@example.com",
    )

    assert success is False
    assert "未在远端 auth-files 中匹配到" in message


def test_delete_cpa_auth_file_retries_path_style_when_query_delete_returns_404(monkeypatch):
    monkeypatch.setattr(
        cpa_upload,
        "list_cpa_auth_files",
        lambda api_url, api_token, proxy_url=None: (
            True,
            {"files": [{"name": "tester@example.com.json"}]},
            "ok",
        ),
    )

    calls = []
    responses = [
        FakeResponse(status_code=404, text="not found"),
        FakeResponse(status_code=204),
    ]

    def fake_delete(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        return responses.pop(0)

    monkeypatch.setattr(cpa_upload.cffi_requests, "delete", fake_delete)

    success, message = cpa_upload.delete_cpa_auth_file(
        "https://cpa.example.com",
        "token-123",
        "tester@example.com",
    )

    assert success is True
    assert "tester@example.com.json" in message
    assert calls[0]["url"] == "https://cpa.example.com/v0/management/auth-files?name=tester%40example.com.json"
    assert calls[1]["url"] == "https://cpa.example.com/v0/management/auth-files/tester%40example.com.json"


def test_match_auth_file_name_requires_exact_email_json_match():
    payload = {
        "files": [
            {"name": "tester@example.com.bak.json"},
            {"name": "other-tester@example.com.json"},
            {"name": "tester@example.com.json"},
        ]
    }

    assert cpa_upload._match_auth_file_name(payload, "tester@example.com") == "tester@example.com.json"


def test_probe_cpaproxyapi_compatibility_collects_delete_strategies(monkeypatch):
    monkeypatch.setattr(
        cpa_upload,
        "list_cpa_auth_files",
        lambda api_url, api_token, proxy_url=None: (
            True,
            {"files": [{"filename": "tester@example.com.json"}]},
            "ok",
        ),
    )

    responses = [
        FakeResponse(status_code=404, text="missing"),
        FakeResponse(status_code=204),
        FakeResponse(status_code=404, text="missing"),
        FakeResponse(status_code=404, text="missing"),
    ]
    calls = []

    def fake_delete(url, **kwargs):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(cpa_upload.cffi_requests, "delete", fake_delete)

    result = cpa_upload.probe_cpaproxyapi_compatibility(
        "https://cpa.example.com",
        "token-123",
        email="tester@example.com",
    )

    assert result["list_probe"]["ok"] is True
    assert result["list_probe"]["name_fields_seen"] == ["filename"]
    assert result["delete_probe"]["filename"] == "tester@example.com.json"
    assert result["delete_probe"]["recommended_strategy"] == "path_segment"
    assert result["delete_probe"]["strategies"][0]["strategy"] == "query_name"
    assert result["delete_probe"]["strategies"][0]["status_code"] == 404
    assert result["delete_probe"]["strategies"][1]["strategy"] == "path_segment"
    assert result["delete_probe"]["strategies"][1]["ok"] is True
    assert calls[0] == "https://cpa.example.com/v0/management/auth-files?name=tester%40example.com.json"
    assert calls[1] == "https://cpa.example.com/v0/management/auth-files/tester%40example.com.json"
