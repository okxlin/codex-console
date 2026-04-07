import json
import os
import sys
from datetime import datetime
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def ensure_runtime_dirs() -> tuple[Path, Path]:
    data_dir = Path(os.environ.get("APP_DATA_DIR") or (PROJECT_ROOT / "runtime-tests" / "data"))
    logs_dir = Path(os.environ.get("APP_LOGS_DIR") or (PROJECT_ROOT / "runtime-tests" / "logs"))
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    os.environ["APP_DATA_DIR"] = str(data_dir)
    os.environ["APP_LOGS_DIR"] = str(logs_dir)
    return data_dir, logs_dir


def main() -> int:
    data_dir, logs_dir = ensure_runtime_dirs()

    from webui import setup_application
    from src.services import EmailServiceFactory, EmailServiceType
    from src.core.register import RegistrationEngine
    from src.config.settings import update_settings

    setup_application()
    update_settings(registration_entry_flow="playwright")

    proxy_url = (os.environ.get("RUNTIME_TEST_PROXY") or "").strip() or None
    email_service_name = (os.environ.get("RUNTIME_TEST_EMAIL_SERVICE") or "tempmail").strip().lower()
    service_config = {}
    if proxy_url:
        service_config["proxy_url"] = proxy_url

    if email_service_name == "yyds_mail":
        service_type = EmailServiceType.YYDS_MAIL
        service_config["base_url"] = str(os.environ.get("YYDS_MAIL_BASE_URL") or "https://maliapi.215.im/v1").strip().rstrip("/")
        service_config["api_key"] = str(os.environ.get("YYDS_MAIL_API_KEY") or "").strip()
        service_config["default_domain"] = str(os.environ.get("YYDS_MAIL_DOMAIN") or "").strip()
        service_name = "runtime-test-yyds-mail-playwright"
    else:
        service_type = EmailServiceType.TEMPMAIL
        service_name = "runtime-test-tempmail-playwright"

    email_service = EmailServiceFactory.create(service_type, service_config, name=service_name)
    engine = RegistrationEngine(email_service=email_service, proxy_url=proxy_url)

    print("=" * 72)
    print("Runtime Playwright registration test starting")
    print(f"APP_DATA_DIR={data_dir}")
    print(f"APP_LOGS_DIR={logs_dir}")
    print(f"proxy={proxy_url or '-'}")
    print(f"email_service={email_service_name}")
    print("entry_flow=playwright")
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
    }

    result_file = logs_dir / "real_playwright_registration_result.json"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    history_file = logs_dir / f"real_playwright_registration_result_{timestamp}.json"
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
