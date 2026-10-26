"""Isolated, real Redis for integration tests; never connect to production."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError
from redis.retry import Retry

from requeststoken.observatory_store import RedisSnapshotStore


def pytest_addoption(parser):
    parser.addoption("--require-redis", action="store_true", help="Fail if redis-server is unavailable")


@pytest.fixture(autouse=True)
def isolate_observatory_environment(monkeypatch):
    for name in ("REDIS_URL", "REQUESTS_OBSERVATORY_REDIS_PREFIX", "REQUESTS_OBSERVATORY_REFRESH"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def offline_writing_feed(monkeypatch):
    """Never fetch the real blog feed from tests."""
    from requeststoken import writing

    def unavailable():
        raise OSError("offline in tests")

    monkeypatch.setattr(writing.Writing, "_fetch", staticmethod(unavailable))


class IsolatedRedis:
    def __init__(self, binary, directory):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        password = uuid4().hex
        self.command = [
            binary, "--bind", "127.0.0.1", "--port", str(port), "--requirepass", password,
            "--dir", directory, "--save", "", "--appendonly", "yes",
            "--appendfsync", "always", "--maxmemory-policy", "noeviction",
        ]
        self.client = Redis(host="127.0.0.1", port=port, password=password,
                            decode_responses=True, socket_timeout=1, socket_connect_timeout=1,
                            retry=Retry(NoBackoff(), 0))
        self.log = open(Path(directory) / "redis.log", "w+")
        self.process = None

    def start(self):
        self.process = subprocess.Popen(self.command, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 5
        while True:
            try:
                if self.client.ping():
                    return
            except ConnectionError:
                if self.process.poll() is not None or time.monotonic() >= deadline:
                    self.log.seek(0)
                    pytest.fail("Test Redis failed to start: " + self.log.read())
                time.sleep(.02)

    def stop(self, *, crash=False):
        self.client.connection_pool.disconnect()
        if self.process is None:
            return
        if crash:
            self.process.kill()
        else:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)

    def restart(self, *, crash=False):
        self.stop(crash=crash)
        self.start()


@pytest.fixture(scope="session")
def redis_instance(request):
    binary = os.environ.get("REDIS_SERVER_BINARY") or shutil.which("redis-server")
    if not binary:
        if request.config.getoption("--require-redis"):
            pytest.fail("Install redis-server or set REDIS_SERVER_BINARY to run Redis integration tests")
        pytest.skip("redis-server unavailable; use --require-redis in CI")
    with tempfile.TemporaryDirectory(prefix="rt-redis-") as directory:
        server = IsolatedRedis(binary, directory)
        try:
            server.start()
            yield server
        finally:
            server.stop()
            server.client.close()
            server.log.close()


@pytest.fixture(scope="session")
def redis_server(redis_instance):
    return redis_instance.client


@pytest.fixture
def redis_store(redis_server):
    return RedisSnapshotStore(redis_server, prefix="test:" + uuid4().hex)
