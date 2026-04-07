import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "runtime-tests" / "abcard" / "run_real_abcard_registration.py"
DEFAULT_PYTHON = PROJECT_ROOT.parent / "playwright-runtime-tests" / ".venv" / "Scripts" / "python.exe"
RESULTS_DIR = PROJECT_ROOT / "runtime-tests" / "matrix-results"


def choose_python() -> str:
    explicit = str(os.environ.get("RUNTIME_TEST_PYTHON") or "").strip()
    if explicit:
        return explicit
    if DEFAULT_PYTHON.exists():
        return str(DEFAULT_PYTHON)
    return sys.executable


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    flows = ["native", "abcard", "fast", "playwright"]
    rounds = 3
    python_exe = choose_python()
    proxy_url = str(os.environ.get("RUNTIME_TEST_PROXY") or "socks5://127.0.0.1:31156").strip()
    delay_min = int(str(os.environ.get("RUNTIME_TEST_DELAY_MIN_SECONDS") or "15").strip() or "15")
    delay_max = int(str(os.environ.get("RUNTIME_TEST_DELAY_MAX_SECONDS") or "45").strip() or "45")
    if delay_max < delay_min:
        delay_max = delay_min

    print(f"python={python_exe}")
    print(f"proxy={proxy_url}")
    print(f"flows={flows}")
    print(f"rounds={rounds}")
    print(f"delay_range_seconds=({delay_min}, {delay_max})")

    summary = []
    for flow in flows:
        for idx in range(1, rounds + 1):
            run_label = f"{flow}-run{idx}"
            env = os.environ.copy()
            env["RUNTIME_TEST_ENTRY_FLOW"] = flow
            env["RUNTIME_TEST_RUN_LABEL"] = run_label
            env["RUNTIME_TEST_PROXY"] = proxy_url

            print("=" * 72)
            print(f"Starting {run_label}")
            started_at = datetime.now().isoformat()
            proc = subprocess.run(
                [python_exe, str(RUNNER)],
                cwd=str(PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            ended_at = datetime.now().isoformat()
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            marker = "result_file="
            result_file = ""
            for line in stdout.splitlines():
                if line.startswith(marker):
                    result_file = line[len(marker):].strip()

            result_payload = {}
            if result_file and Path(result_file).exists():
                try:
                    result_payload = json.loads(Path(result_file).read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    result_payload = {}

            combined_text = "\n".join([stdout, stderr])
            hits_400 = (
                "提交密码状态: 400" in combined_text
                or "账户创建状态: 400" in combined_text
                or "registration_disallowed" in combined_text
                or "Failed to create account. Please try again." in combined_text
            )

            row = {
                "flow": flow,
                "run": idx,
                "run_label": run_label,
                "started_at": started_at,
                "ended_at": ended_at,
                "exit_code": proc.returncode,
                "result_file": result_file,
                "success": result_payload.get("success"),
                "error_message": result_payload.get("error_message"),
                "has_access_token": result_payload.get("has_access_token"),
                "has_session_token": result_payload.get("has_session_token"),
                "hits_400": hits_400,
            }
            summary.append(row)

            detail_file = RESULTS_DIR / f"{run_label}.json"
            detail_file.write_text(
                json.dumps(
                    {
                        "summary": row,
                        "stdout": stdout,
                        "stderr": stderr,
                        "result_payload": result_payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
                newline="\n",
            )

            is_last_run = flow == flows[-1] and idx == rounds
            if not is_last_run:
                sleep_seconds = random.randint(delay_min, delay_max)
                row["post_run_delay_seconds"] = sleep_seconds
                print(f"Sleeping {sleep_seconds}s before next run...")
                time.sleep(sleep_seconds)
            else:
                row["post_run_delay_seconds"] = 0

    summary_file = RESULTS_DIR / f"registration_flow_matrix_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print("=" * 72)
    print(f"summary_file={summary_file}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
