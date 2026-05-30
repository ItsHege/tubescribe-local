from __future__ import annotations

import ctypes
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765
APP_URL = f"http://{HOST}:{PORT}/"
HEALTH_URL = f"http://{HOST}:{PORT}/api/health"
PID_FILE = BASE_DIR / "server.pid"


def find_pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return str(exe)


def health_ok(timeout: float = 1.2) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def start_server() -> int:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [find_pythonw(), str(BASE_DIR / "app.py")],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def wait_until_ready(timeout_seconds: float = 15.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if health_ok():
            return True
        time.sleep(0.35)
    return False


def show_message(title: str, body: str):
    ctypes.windll.user32.MessageBoxW(0, body, title, 0x10)


def main():
    already_running = health_ok()
    if not already_running:
        start_server()
    elif PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)

    if not wait_until_ready():
        show_message(
            "YouTube transkribavimo tool",
            "Nepavyko paleisti lokalaus serverio. Patikrink ar irankio aplanke yra Python ir pabandyk dar karta.",
        )
        return

    webbrowser.open(APP_URL)


if __name__ == "__main__":
    main()
