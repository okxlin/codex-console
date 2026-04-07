import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
SCRIPT = PROJECT_ROOT / "runtime-tests" / "run_real_playwright_registration.py"
LOGS_DIR = PROJECT_ROOT / "runtime-tests" / "logs"


def load_last_result() -> dict:
    result_file = LOGS_DIR / "real_playwright_registration_result.json"
    if not result_file.exists():
        return {}
    try:
        return json.loads(result_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def detect_stage(result: dict) -> str:
    metadata = result.get("metadata") or {}
    if metadata.get("playwright_diagnostics"):
        return str(((metadata.get("playwright_diagnostics") or {}).get("stage") or "playwright")).strip() or "playwright"
    if metadata.get("create_account_payload") and not result.get("success"):
        return "create_account"
    last_stage = str(result.get("last_stage") or "").lower()
    if "password" in last_stage:
        return "password"
    if "otp" in last_stage or "验证码" in last_stage:
        return "otp"
    return "unknown"


def run_case(name: str, proxy: str | None, attempts: int = 1, sleep_seconds: int = 5) -> dict:
    summary = []
    stage_counter = Counter()
    success_count = 0

    for index in range(1, attempts + 1):
        env = os.environ.copy()
        env["APP_DATA_DIR"] = str(PROJECT_ROOT / "runtime-tests" / "data" / name)
        env["APP_LOGS_DIR"] = str(PROJECT_ROOT / "runtime-tests" / "logs" / name)
        Path(env["APP_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
        Path(env["APP_LOGS_DIR"]).mkdir(parents=True, exist_ok=True)
        if proxy:
            env["RUNTIME_TEST_PROXY"] = proxy
        else:
            env.pop("RUNTIME_TEST_PROXY", None)

        completed = subprocess.run([str(PYTHON), str(SCRIPT)], cwd=str(PROJECT_ROOT), env=env, check=False)
        result_file = Path(env["APP_LOGS_DIR"]) / "real_playwright_registration_result.json"
        result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {}
        stage = detect_stage(result)
        stage_counter[stage] += 1
        success = bool(result.get("success"))
        if success:
            success_count += 1
        summary.append(
            {
                "attempt": index,
                "returncode": completed.returncode,
                "success": success,
                "stage": stage,
                "error_message": result.get("error_message"),
                "email": result.get("email"),
            }
        )
        if index < attempts:
            time.sleep(sleep_seconds)

    return {
        "name": name,
        "proxy": proxy or "direct",
        "attempts": attempts,
        "success_count": success_count,
        "failure_count": attempts - success_count,
        "stage_counts": dict(stage_counter),
        "runs": summary,
    }


def main() -> int:
    attempts = int(os.environ.get("RUNTIME_COMPARE_ATTEMPTS") or "1")
    sleep_seconds = int(os.environ.get("RUNTIME_COMPARE_SLEEP_SECONDS") or "5")
    proxy_cases_raw = str(os.environ.get("RUNTIME_COMPARE_CASES") or "").strip()
    if proxy_cases_raw:
        cases = []
        for raw in proxy_cases_raw.split(","):
            item = raw.strip()
            if not item:
                continue
            if item == "direct":
                cases.append(("direct", None))
            else:
                port = item.replace("proxy_", "").strip()
                cases.append((f"proxy_{port}", f"socks5://127.0.0.1:{port}"))
    else:
        cases = [
            ("direct", None),
            ("proxy_31156", "socks5://127.0.0.1:31156"),
        ]

    report = {
        "attempts_per_case": attempts,
        "sleep_seconds": sleep_seconds,
        "cases": [run_case(name, proxy, attempts=attempts, sleep_seconds=sleep_seconds) for name, proxy in cases],
    }
    report_file = LOGS_DIR / "compare_playwright_environments_summary.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report_file={report_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
