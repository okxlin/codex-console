import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


try:
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if callable(stdout_reconfigure):
        stdout_reconfigure(encoding="utf-8", errors="replace")
    if callable(stderr_reconfigure):
        stderr_reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEST_CONFIG_DIR = PROJECT_ROOT.parent / "test-config"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def ensure_runtime_dirs() -> tuple[Path, Path]:
    data_dir = Path(os.environ.get("APP_DATA_DIR") or (PROJECT_ROOT / "runtime-tests" / "abcard" / "data"))
    logs_dir = Path(os.environ.get("APP_LOGS_DIR") or (PROJECT_ROOT / "runtime-tests" / "abcard" / "logs"))
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    os.environ["APP_DATA_DIR"] = str(data_dir)
    os.environ["APP_LOGS_DIR"] = str(logs_dir)
    return data_dir, logs_dir


def choose_proxy() -> Optional[str]:
    explicit = str(os.environ.get("RUNTIME_TEST_PROXY") or "").strip()
    if explicit:
        return explicit

    preferred_ports = ("31156", "31152")
    proxy_file = TEST_CONFIG_DIR / "SmartProxy-Servers.txt"
    if proxy_file.exists():
        text = proxy_file.read_text(encoding="utf-8", errors="replace")
        for port in preferred_ports:
            needle = f"127.0.0.1:{port}"
            if needle in text:
                return f"socks5://{needle}"
    return None


def resolve_yyds_api_key() -> str:
    explicit = str(os.environ.get("YYDS_MAIL_API_KEY") or "").strip()
    if explicit:
        return explicit

    guide_file = TEST_CONFIG_DIR / "yyds-mail-use.md"
    if guide_file.exists():
        text = guide_file.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            candidate = line.strip()
            if candidate.startswith("AC-"):
                return candidate
    return ""


def build_email_service_config(email_service_name: str, proxy_url: Optional[str]):
    from src.services import EmailServiceType

    service_config: dict[str, str] = {}
    if proxy_url:
        service_config["proxy_url"] = proxy_url

    if email_service_name == "yyds_mail":
        service_type = EmailServiceType.YYDS_MAIL
        service_config["base_url"] = str(os.environ.get("YYDS_MAIL_BASE_URL") or "https://maliapi.215.im/v1").strip().rstrip("/")
        service_config["api_key"] = resolve_yyds_api_key()
        service_config["default_domain"] = str(os.environ.get("YYDS_MAIL_DOMAIN") or "").strip()
        service_name = "runtime-test-yyds-mail-abcard"
    else:
        service_type = EmailServiceType.TEMPMAIL
        service_name = "runtime-test-tempmail-abcard"

    return service_type, service_config, service_name


def main() -> int:
    data_dir, logs_dir = ensure_runtime_dirs()

    from webui import setup_application
    from src.services import EmailServiceFactory
    from src.core.register import RegistrationEngine
    from src.config.settings import update_settings

    setup_application()
    entry_flow = str(os.environ.get("RUNTIME_TEST_ENTRY_FLOW") or "abcard").strip().lower() or "abcard"
    run_label = str(os.environ.get("RUNTIME_TEST_RUN_LABEL") or entry_flow).strip() or entry_flow
    browser_sentinel_default = "1" if entry_flow in {"abcard", "native", "fast", "playwright"} else "0"
    os.environ.setdefault("OPENAI_BROWSER_SENTINEL_ENABLED", browser_sentinel_default)
    update_settings(registration_entry_flow=entry_flow)

    proxy_url = choose_proxy()
    email_service_name = (os.environ.get("RUNTIME_TEST_EMAIL_SERVICE") or "yyds_mail").strip().lower()
    service_type, service_config, service_name = build_email_service_config(email_service_name, proxy_url)

    email_service = EmailServiceFactory.create(service_type, service_config, name=service_name)
    engine = RegistrationEngine(email_service=email_service, proxy_url=proxy_url)

    print("=" * 72)
    print("Runtime ABCard registration test starting")
    print(f"APP_DATA_DIR={data_dir}")
    print(f"APP_LOGS_DIR={logs_dir}")
    print(f"proxy={proxy_url or '-'}")
    print(f"email_service={email_service_name}")
    print(f"test_config_dir={TEST_CONFIG_DIR}")
    print(f"entry_flow={entry_flow}")
    print(f"run_label={run_label}")
    print("=" * 72)

    result = engine.run()
    db_saved = False
    saved_account_id = None
    if result.success and result.access_token:
        db_saved = engine.save_to_database(result)
        if db_saved:
            from src.database.session import get_db
            from src.database.models import Account

            with get_db() as db:
                account = db.query(Account).filter_by(email=result.email).first()
                if account:
                    saved_account_id = account.id

    payload = {
        "success": result.success,
        "email": result.email,
        "password": result.password,
        "account_id": result.account_id,
        "workspace_id": result.workspace_id,
        "has_access_token": bool(result.access_token),
        "has_refresh_token": bool(result.refresh_token),
        "has_session_token": bool(result.session_token),
        "error_message": result.error_message,
        "source": result.source,
        "last_stage": (result.logs or [""])[-1] if result.logs else "",
        "db_saved": db_saved,
        "saved_account_id": saved_account_id,
        "metadata": result.metadata,
        "proxy_used": proxy_url,
        "email_service": email_service_name,
        "entry_flow": entry_flow,
        "run_label": run_label,
    }

    safe_label = re.sub(r"[^a-zA-Z0-9._-]+", "-", run_label)
    result_file = logs_dir / f"real_{safe_label}_registration_result.json"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    history_file = logs_dir / f"real_{safe_label}_registration_result_{timestamp}.json"
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    result_file.write_text(encoded, encoding="utf-8", newline="\n")
    history_file.write_text(encoded, encoding="utf-8", newline="\n")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("-" * 72)
    print(f"result_file={result_file}")
    print(f"history_file={history_file}")
    print(f"log_lines={len(result.logs or [])}")

    if result.logs:
        print("-" * 72)
        print("last_logs:")
        for line in result.logs[-20:]:
            print(line)

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
