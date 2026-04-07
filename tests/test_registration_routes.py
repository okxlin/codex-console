import asyncio
from contextlib import contextmanager
from pathlib import Path

from src.database.models import Base, EmailService, RegistrationTask
from src.database.session import DatabaseSessionManager
from src.web.routes import registration as registration_routes
from src.core.auto_registration import update_auto_registration_state


def test_get_task_logs_exposes_playwright_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "registration_routes.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        email_service = EmailService(
            service_type="tempmail",
            name="Tempmail",
            config={},
            enabled=True,
            priority=0,
        )
        session.add(email_service)
        session.flush()

        task = RegistrationTask(
            task_uuid="task-playwright-1",
            status="running",
            email_service_id=email_service.id,
            logs="line-1\nline-2",
            result={
                "email": "tester@example.com",
                "metadata": {
                    "registration_scheme_label_effective": "Playwright / 浏览器态优先收尾",
                    "playwright_diagnostics": {
                        "stage": "browser_side_channel_ok",
                        "strategy": "browser_first",
                        "failure_reason": "",
                        "callback_url": "https://chatgpt.com/api/auth/callback/openai?code=1&state=2",
                        "current_url": "https://chatgpt.com/",
                        "callback_candidate": "https://auth.openai.com/continue",
                        "has_session_token": True,
                        "has_access_token": True,
                        "has_refresh_token": False,
                        "used_native_backfill": True,
                        "used_browser_retry": False,
                        "used_signin_bridge": False,
                        "artifact": {
                            "type": "screenshot",
                            "path": "playwright-artifacts/failure.png",
                            "size_bytes": 4096,
                            "created_at": 1234567890,
                        },
                        "browser_probe": {
                            "proxy": "http://127.0.0.1:7890",
                            "ipify_before": "{\"ip\":\"1.2.3.4\"}",
                            "chatgpt_title": "ChatGPT",
                            "chatgpt_url": "https://chatgpt.com/",
                            "page_state": "app_home",
                            "chatgpt_body_hint": "history",
                            "fingerprint_profile_id": "win_chrome_a",
                            "method": "side_channel",
                            "hit": True,
                            "source": "playwright",
                        },
                    },
                },
            },
        )
        session.add(task)

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(registration_routes, "get_db", fake_get_db)

    payload = asyncio.run(registration_routes.get_task_logs("task-playwright-1"))

    assert payload["effective_scheme"] == "Playwright / 浏览器态优先收尾"
    assert payload["playwright"]["stage"] == "browser_side_channel_ok"
    assert payload["playwright"]["strategy"] == "browser_first"
    assert payload["playwright"]["diagnosis_category"] == "unknown"
    assert payload["playwright"]["diagnosis_label"] == "待进一步排查"
    assert "继续排查" in payload["playwright"]["diagnosis_hint"]
    assert payload["playwright"]["recommended_action"] == "manual_review"
    assert "人工复核" in payload["playwright"]["recommended_action_hint"]
    assert payload["playwright"]["strategy_flags"]["safe_retry_same_env"] is False
    assert payload["playwright"]["strategy_flags"]["prefer_token_only_retry"] is False
    assert payload["playwright"]["strategy_flags"]["should_rotate_proxy"] is False
    assert payload["playwright"]["post_failure_strategy"] is None
    assert payload["playwright"]["next_run_policy"] is None
    assert payload["playwright"]["has_session_token"] is True
    assert payload["playwright"]["has_access_token"] is True
    assert payload["playwright"]["has_refresh_token"] is False
    assert payload["playwright"]["callback_url"] == "https://chatgpt.com/api/auth/callback/openai?code=1&state=2"
    assert payload["playwright"]["current_url"] == "https://chatgpt.com/"
    assert payload["playwright"]["callback_candidate"] == "https://auth.openai.com/continue"
    assert payload["playwright"]["used_native_backfill"] is True
    assert payload["playwright"]["used_browser_retry"] is False
    assert payload["playwright"]["used_signin_bridge"] is False
    assert payload["playwright"]["artifact"]["path"] == "playwright-artifacts/failure.png"
    assert payload["playwright"]["browser_probe"]["page_state"] == "app_home"
    assert payload["playwright"]["browser_probe"]["chatgpt_title"] == "ChatGPT"
    assert payload["playwright"]["browser_probe"]["ipify_before"] == "{\"ip\":\"1.2.3.4\"}"
    assert payload["playwright"]["browser_probe"]["method"] == "side_channel"
    assert payload["logs"] == ["line-1", "line-2"]


def test_download_playwright_artifact_returns_png(tmp_path, monkeypatch):
    artifact = tmp_path / "playwright-artifacts" / "failure.png"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"png")

    monkeypatch.setattr(registration_routes, "get_data_dir", lambda: tmp_path)

    response = asyncio.run(registration_routes.download_playwright_artifact("playwright-artifacts/failure.png"))

    assert response.path == str(artifact)
    assert response.media_type == "image/png"


def test_download_playwright_artifact_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(registration_routes, "get_data_dir", lambda: tmp_path)

    try:
        asyncio.run(registration_routes.download_playwright_artifact("../secrets.txt"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "非法" in str(getattr(exc, "detail", ""))
    else:
        raise AssertionError("expected HTTPException for invalid path")


def test_append_playwright_diagnosis_log_writes_summary_line():
    lines = []
    payload = {
        "metadata": {
            "playwright_diagnostics": {
                "stage": "failed",
                "failure_reason": "session_token_missing",
                "browser_probe": {
                    "page_state": "app_home",
                },
            }
        }
    }

    result = registration_routes._append_playwright_diagnosis_log(payload, lines.append)

    assert lines
    assert "[Playwright 诊断]" in lines[0]
    assert "Session 缺失" in lines[0]
    assert "action=retry_session_backfill" in lines[0]
    assert result["metadata"]["playwright_diagnosis_summary"]["label"] == "Session 缺失"
    assert result["metadata"]["playwright_diagnosis_summary"]["recommended_action"] == "retry_session_backfill"
    assert result["metadata"]["playwright_diagnosis_summary"]["strategy_flags"]["prefer_session_only_retry"] is True


def test_log_playwright_post_failure_strategy_writes_strategy_summary():
    lines = []
    payload = {
        "metadata": {
            "playwright_diagnostics": {
                "stage": "failed",
                "failure_reason": "access_token_missing",
                "browser_probe": {
                    "page_state": "app_home",
                },
            }
        }
    }

    result = registration_routes._log_playwright_post_failure_strategy(payload, log_callback=lines.append)

    assert lines
    assert "[Playwright 后续动作]" in lines[0]
    assert "retry_scope=token_only" in lines[0]
    assert "next=fresh_context" in lines[0]
    assert result["metadata"]["playwright_post_failure_strategy"]["retry_scope"] == "token_only"
    assert result["metadata"]["playwright_post_failure_strategy"]["safe_retry_same_env"] is True
    assert result["metadata"]["playwright_post_failure_strategy"]["next_run_policy"]["fresh_browser_context"] is True
    assert result["metadata"]["playwright_post_failure_strategy"]["next_run_policy"]["reuse_browser_storage"] is False


def test_summarize_next_run_policy_formats_safe_browser_hints():
    text = registration_routes._summarize_next_run_policy(
        {
            "fresh_browser_context": True,
            "reuse_browser_storage": False,
            "isolate_task_cookies": True,
            "prefer_fresh_fingerprint": True,
            "rotate_proxy_before_retry": True,
        }
    )

    assert text == "fresh_context / rotate_proxy / fresh_fingerprint / isolated_cookies / no_storage_reuse"


def test_extract_followup_status_payload_returns_strategy_fields():
    payload = {
        "metadata": {
            "playwright_post_failure_strategy": {
                "retry_scope": "full_retry_with_new_proxy",
                "should_rotate_proxy": True,
                "safe_retry_same_env": False,
                "needs_manual_review": True,
                "next_run_policy": {
                    "fresh_browser_context": True,
                    "reuse_browser_storage": False,
                    "isolate_task_cookies": True,
                    "prefer_fresh_fingerprint": True,
                    "rotate_proxy_before_retry": True,
                },
            }
        }
    }

    result = registration_routes._extract_followup_status_payload(payload)

    assert result["playwright_retry_scope"] == "full_retry_with_new_proxy"
    assert result["playwright_should_rotate_proxy"] is True
    assert result["playwright_safe_retry_same_env"] is False
    assert result["playwright_needs_manual_review"] is True
    assert result["playwright_next_run_policy"]["prefer_fresh_fingerprint"] is True


def test_load_task_execution_overrides_reads_proxy_and_fingerprint_policy():
    class DummyTask:
        proxy = "http://old-proxy:8080"
        result = {
            "metadata": {
                "playwright_diagnostics": {
                    "browser_probe": {
                        "fingerprint_profile_id": "win_chrome_a",
                    }
                },
                "playwright_post_failure_strategy": {
                    "next_run_policy": {
                        "rotate_proxy_before_retry": True,
                        "prefer_fresh_fingerprint": True,
                    }
                }
            }
        }

    result = registration_routes._load_task_execution_overrides(DummyTask())

    assert result["rotate_proxy_before_retry"] is True
    assert result["prefer_fresh_fingerprint"] is True
    assert result["excluded_proxy_urls"] == ["http://old-proxy:8080"]
    assert result["previous_fingerprint_profile_id"] == "win_chrome_a"


def test_get_proxy_for_registration_with_exclusions_skips_recent_failed_proxy(monkeypatch):
    class DummyProxy:
        def __init__(self, proxy_url, proxy_id):
            self.proxy_url = proxy_url
            self.id = proxy_id

    monkeypatch.setattr(registration_routes.crud, "get_enabled_proxies", lambda db: [
        DummyProxy("http://old-proxy:8080", 1),
        DummyProxy("http://new-proxy:8080", 2),
    ])

    proxy_url, proxy_id = registration_routes.get_proxy_for_registration_with_exclusions(
        db=object(),
        excluded_proxy_urls=["http://old-proxy:8080"],
    )

    assert proxy_url == "http://new-proxy:8080"
    assert proxy_id == 2


def test_build_playwright_post_failure_strategy_summary_handles_direct_connection():
    payload = {
        "metadata": {
            "proxy_used": "",
            "playwright_diagnostics": {
                "stage": "failed",
                "failure_reason": "access_token_missing",
                "browser_probe": {
                    "page_state": "challenge_page",
                },
            },
        }
    }

    result = registration_routes._build_playwright_post_failure_strategy_summary(payload)

    assert result is not None
    assert result["should_rotate_proxy"] is False
    assert result["next_run_policy"]["rotate_proxy_before_retry"] is False
    assert result["next_run_policy"]["prefer_fresh_fingerprint"] is True
    assert "直连环境" in result["note"]

    summary = registration_routes._extract_playwright_summary(payload)
    assert summary["recommended_action"] == "manual_review"


def test_compute_followup_throttle_seconds_prefers_manual_review_delay():
    assert registration_routes._compute_followup_throttle_seconds({"needs_manual_review": True}) == 45
    assert registration_routes._compute_followup_throttle_seconds({"should_rotate_proxy": True}) == 20
    assert registration_routes._compute_followup_throttle_seconds({}) == 0


def test_build_playwright_stats_aggregates_recent_strategy_counts():
    class DummyTask:
        def __init__(self, result, status="failed"):
            self.result = result
            self.status = status

    tasks = [
        DummyTask({
            "metadata": {
                "playwright_diagnostics": {
                    "stage": "failed",
                    "failure_reason": "access_token_missing",
                    "browser_probe": {"page_state": "app_home"},
                },
                "playwright_post_failure_strategy": {
                    "should_rotate_proxy": False,
                    "needs_manual_review": False,
                    "next_run_policy": {"prefer_fresh_fingerprint": True},
                },
            }
        }),
        DummyTask({
            "metadata": {
                "playwright_diagnostics": {
                    "stage": "failed",
                    "failure_reason": "access_token_missing",
                    "browser_probe": {"page_state": "challenge_page"},
                },
                "playwright_post_failure_strategy": {
                    "should_rotate_proxy": True,
                    "needs_manual_review": True,
                    "next_run_policy": {
                        "prefer_fresh_fingerprint": True,
                        "rotate_proxy_before_retry": True,
                    },
                },
            }
        }),
    ]

    stats = registration_routes._build_playwright_stats(tasks, limit=50)

    assert stats["samples"] == 2
    assert stats["samples_total"] == 2
    assert stats["rotate_proxy_count"] == 1
    assert stats["fresh_fingerprint_count"] == 2
    assert stats["throttle_count"] == 1
    assert stats["top_diagnosis"]


def test_build_playwright_stats_excludes_successful_samples_from_failure_metrics():
    class DummyTask:
        def __init__(self, result, status="completed"):
            self.result = result
            self.status = status

    tasks = [
        DummyTask({
            "success": True,
            "metadata": {
                "playwright_diagnostics": {
                    "stage": "completed",
                    "failure_reason": "",
                    "browser_probe": {"page_state": "app_home"},
                }
            }
        }, status="completed"),
        DummyTask({
            "success": False,
            "metadata": {
                "playwright_diagnostics": {
                    "stage": "failed",
                    "failure_reason": "access_token_missing",
                    "browser_probe": {"page_state": "challenge_page"},
                },
                "playwright_post_failure_strategy": {
                    "should_rotate_proxy": True,
                    "needs_manual_review": True,
                    "next_run_policy": {"prefer_fresh_fingerprint": True, "rotate_proxy_before_retry": True},
                },
            }
        }, status="failed"),
    ]

    stats = registration_routes._build_playwright_stats(tasks, limit=50)

    assert stats["samples_total"] == 2
    assert stats["samples_failed"] == 1
    assert stats["samples"] == 1
    assert stats["rotate_proxy_count"] == 1


def test_build_playwright_alerts_flags_risk_and_throttle_patterns():
    alerts = registration_routes._build_playwright_alerts(
        {
            "samples": 10,
            "top_diagnosis": [{"label": "风控挑战", "count": 5}],
            "rotate_proxy_count": 0,
            "fresh_fingerprint_count": 8,
            "throttle_count": 4,
        }
    )

    assert alerts["active"] is True
    assert len(alerts["messages"]) >= 2


def test_get_cached_playwright_stats_reuses_recent_cache():
    calls = {"count": 0}

    def provider():
        calls["count"] += 1
        return []

    from src.core.playwright_insights import invalidate_playwright_stats_cache, get_cached_playwright_stats

    invalidate_playwright_stats_cache()
    stats1, alerts1 = get_cached_playwright_stats(
        provider,
        stats_builder=registration_routes._build_playwright_stats,
        alerts_builder=registration_routes._build_playwright_alerts,
        ttl_seconds=60,
    )
    stats2, alerts2 = get_cached_playwright_stats(
        provider,
        stats_builder=registration_routes._build_playwright_stats,
        alerts_builder=registration_routes._build_playwright_alerts,
        ttl_seconds=60,
    )

    assert calls["count"] == 1
    assert stats1 == stats2
    assert alerts1 == alerts2


def test_apply_batch_throttle_window_extends_until(monkeypatch):
    from src.core import playwright_insights

    registration_routes.batch_tasks["batch-test"] = {"throttle_until": 0.0}
    monkeypatch.setattr(playwright_insights.time, "time", lambda: 100.0)

    registration_routes._apply_batch_throttle_window("batch-test", 20)

    assert registration_routes.batch_tasks["batch-test"]["throttle_until"] == 120.0
    assert registration_routes._remaining_batch_throttle_seconds("batch-test") == 20


def test_get_auto_registration_monitor_includes_playwright_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "auto_monitor_stats.db"
    manager = DatabaseSessionManager(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=manager.engine)

    with manager.session_scope() as session:
        session.add(RegistrationTask(
            task_uuid="task-auto-1",
            status="failed",
            result={
                "metadata": {
                    "playwright_diagnostics": {
                        "stage": "failed",
                        "failure_reason": "access_token_missing",
                        "browser_probe": {"page_state": "challenge_page"},
                    },
                    "playwright_post_failure_strategy": {
                        "should_rotate_proxy": True,
                        "needs_manual_review": True,
                        "next_run_policy": {"prefer_fresh_fingerprint": True, "rotate_proxy_before_retry": True},
                    },
                }
            },
        ))

    @contextmanager
    def fake_get_db():
        session = manager.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(registration_routes, "get_db", fake_get_db)
    from src.core.playwright_insights import invalidate_playwright_stats_cache

    invalidate_playwright_stats_cache()
    update_auto_registration_state(status="checking", message="正在检查库存")

    payload = asyncio.run(registration_routes.get_auto_registration_monitor())

    assert "playwright" in payload
    assert "playwright_alerts" in payload
    assert payload["playwright"]["samples"] >= 1
    assert isinstance(payload["playwright_alerts"]["messages"], list)
