"""Run all isolated IPC regressions; never connects to a real Codex socket."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import os
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        parser.error("executable does not exist or is not executable: " + str(executable))
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="wink-monitor-", dir="/private/tmp") as scratch:
        manual = Path(scratch) / "ManualClear"
        subprocess.run([
            "xcrun", "swiftc", "-O", "-module-cache-path", "/private/tmp/wink-module-cache",
            str(root / "Sources/Core.swift"), str(root / "Sources/DesktopMonitor.swift"),
            str(root / "Tests/Monitor/main.swift"), "-o", str(manual)
        ], check=True)
        names = ["ipc_check", "input_state_check", "read_state_check", "idle_refresh_check",
                 "reconciliation_check", "read_reconnect_check", "robustness_check", "optimization_check"]
        cases = [(name, [sys.executable, str(root / "Tests" / (name + ".py")), str(executable)]) for name in names]
        cases += [
            ("manual_reconnect", [sys.executable, str(root / "Tests/read_reconnect_check.py"), str(manual), "--manual"]),
            ("manual_owner", [sys.executable, str(root / "Tests/read_reconnect_check.py"), str(manual), "--manual", "--owner"])
        ]
        failures = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = {pool.submit(subprocess.run, command, capture_output=True, text=True, timeout=90): name for name, command in cases}
            for job in as_completed(jobs):
                name = jobs[job]
                try:
                    result = job.result()
                    if result.returncode:
                        failures.append(name)
                        print("FAIL " + name + "\n" + result.stdout + result.stderr, flush=True)
                    else:
                        print("PASS " + name, flush=True)
                except Exception as error:
                    failures.append(name)
                    print("FAIL " + name + ": " + str(error), flush=True)
        print(str(len(cases) - len(failures)) + "/" + str(len(cases)) + " IPC regression groups passed", flush=True)
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
