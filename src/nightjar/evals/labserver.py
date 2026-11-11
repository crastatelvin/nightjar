"""Own-process CornerShop lab for evaluation runs.

The harness never mutates or depends on a developer's lab instance: it starts a
fresh process on a free port, waits for health, and tears it down deterministically.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
MODULE = "nightjar.lab.cornershop.app:app"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LabProcess:
    def __init__(self) -> None:
        self.base_url = ""
        self._process: subprocess.Popen[bytes] | None = None
        self._log_path: Path | None = None
        self._log_handle: object | None = None

    def start(self, *, log_level: str = "warning") -> str:
        port = free_port()
        self.base_url = f"http://127.0.0.1:{port}"
        with tempfile.NamedTemporaryFile(
            prefix="nightjar-eval-lab-", suffix=".log", delete=False
        ) as handle:
            self._log_path = Path(handle.name)
        log = self._log_path.open("wb")
        self._log_handle = log
        self._process = subprocess.Popen(  # noqa: S603 - fixed local arguments
            [
                sys.executable,
                "-m",
                "uvicorn",
                MODULE,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                log_level,
            ],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self._wait_for_health()
        return self.base_url

    def _wait_for_health(self, timeout: float = 30.0) -> None:
        assert self._process is not None and self._log_path is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"evaluation lab exited with {self._process.returncode}:\n"
                    f"{self._log_path.read_text(errors='replace')[-4000:]}"
                )
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=1.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("evaluation lab did not become healthy within 30 seconds")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            self._process = None
        if self._log_handle is not None:
            self._log_handle.close()  # type: ignore[attr-defined]
            self._log_handle = None
        if self._log_path is not None:
            self._log_path.unlink(missing_ok=True)
            self._log_path = None


@contextmanager
def managed_lab() -> Iterator[str]:
    lab = LabProcess()
    lab.start()
    try:
        yield lab.base_url
    finally:
        lab.stop()


__all__ = ["LabProcess", "free_port", "managed_lab"]
