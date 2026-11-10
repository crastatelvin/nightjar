"""Shared fixtures for the Nightjar test suite.

The suite is self-contained: it starts its own CornerShop lab on a free port,
builds the schema in a database whose name must end in ``_test``, and gives every
test its own tenant so incidents never leak between cases.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from nightjar.api import tables as _tables  # noqa: F401 - registers the metadata
from nightjar.api.db import Base
from nightjar.api.main import create_app
from nightjar.api.settings import Settings

DEFAULT_DATABASE_URL = "postgresql+asyncpg://nightjar@127.0.0.1:55432/nightjar_test"
ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _assert_test_database(database_url: str) -> None:
    name = make_url(database_url).database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to reset schema in {name!r}: test database names must end with '_test'"
        )


def _reset_schema(database_url: str) -> None:
    _assert_test_database(database_url)

    async def run() -> None:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
                await connection.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(run())


def _wait_for_lab(base_url: str, process: subprocess.Popen[bytes], log: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"lab exited with {process.returncode}:\n{log.read_text(errors='replace')[-4000:]}"
            )
        try:
            response = httpx.get(f"{base_url}/healthz", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("lab did not become healthy within 30 seconds")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("NIGHTJAR_TEST_DATABASE_URL", DEFAULT_DATABASE_URL)
    _reset_schema(url)
    return url


@pytest.fixture(scope="session")
def lab_url() -> Iterator[str]:
    """Run the synthetic CornerShop environment in its own process."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.NamedTemporaryFile(
        prefix="nightjar-lab-", suffix=".log", dir="/tmp/opencode", delete=False
    ) as handle:
        log_path = Path(handle.name)
    log = log_path.open("wb")
    process = subprocess.Popen(  # noqa: S603 - fixed arguments, local test binary
        [
            sys.executable,
            "-m",
            "uvicorn",
            "nightjar.lab.cornershop.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        _wait_for_lab(base_url, process, log_path)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        log_path.unlink(missing_ok=True)


@pytest.fixture
def settings(database_url: str, lab_url: str) -> Settings:
    return Settings(
        environment="test",
        log_level="WARNING",
        tenant_id=str(uuid.uuid4()),
        tenant_name=f"test-{uuid.uuid4().hex[:12]}",
        database_url=database_url,
        lab_base_url=lab_url,
        lab_enabled=True,
        model_provider="mock",
        critic_provider="mock",
        stability_seconds=0,
        approval_ttl_seconds=300,
        allow_private_probes=True,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def lab(lab_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=lab_url, timeout=10.0) as lab_client:
        lab_client.post("/lab/reset")
        yield lab_client
        lab_client.post("/lab/reset")
