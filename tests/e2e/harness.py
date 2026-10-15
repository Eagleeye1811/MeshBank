"""End-to-end harness used by conftest.py and the tests.

Runs all six services as real uvicorn processes on free local ports, with
isolated temporary databases, and talks to them over HTTP. Nothing touches the
developer's persistent data."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from common.keys import SigningKey, generate_key_pair, load_signing_key

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ["auth", "account", "transaction", "payment", "fraud", "notification"]
READY_TIMEOUT_SECONDS = 30
E2E_KEY_ID = "e2e-key-1"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServiceProcess:
    def __init__(self, name: str, port: int, env: dict[str, str], log_dir: Path) -> None:
        self.name = name
        self.port = port
        self.env = env
        self.log_path = log_dir / f"{name}.log"
        self.proc: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        log = self.log_path.open("ab")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--factory",
                "app.main:create_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT / "services" / f"{self.name}-service",
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.wait_ready()

    def wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} exited; see {self.log_path}")
            try:
                if httpx.get(f"{self.url}/ready", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)  # polling interval while waiting for readiness
        raise RuntimeError(f"{self.name} not ready; see {self.log_path}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def make_secrets_env(tmp: Path) -> dict[str, str]:
    """Fresh per-run credentials and RS256 key pair (never the development ones).
    Keys go in a sibling temp directory so ``tmp`` itself is not created here."""
    keys = Path(tempfile.mkdtemp(prefix=f"{tmp.name}-jwt-", dir=tmp.parent))
    generate_key_pair(keys, E2E_KEY_ID, bits=2048)
    return {
        "JWT_PUBLIC_KEYS_DIR": str(keys / "public"),
        "JWT_PRIVATE_KEY_FILE": str(keys / "private" / f"{E2E_KEY_ID}.pem"),
        "JWT_SIGNING_KEY_ID": E2E_KEY_ID,
        "INTERNAL_API_TOKEN": secrets.token_urlsafe(32),
    }


class Stack:
    def __init__(
        self,
        tmp: Path,
        *,
        database_urls: dict[str, str] | None = None,
        secrets_env: dict[str, str] | None = None,
    ) -> None:
        ports = {name: _free_port() for name in SERVICES}
        self.tmp = tmp
        self.secrets_env = secrets_env or make_secrets_env(tmp)
        self.internal_token = self.secrets_env["INTERNAL_API_TOKEN"]
        # Lets tests mint tokens exactly as the auth service would.
        self.signing_key: SigningKey = load_signing_key(
            self.secrets_env["JWT_PRIVATE_KEY_FILE"],
            self.secrets_env["JWT_SIGNING_KEY_ID"],
            strict_permissions=True,
        )
        self.database_urls = database_urls or {
            name: f"sqlite:///{tmp}/{name}.db" for name in SERVICES
        }
        common = {
            "PATH": os.environ.get("PATH", ""),
            "APP_ENV": "test",
            "LOG_LEVEL": "INFO",
            **self.secrets_env,
            "HTTP_TIMEOUT_SECONDS": "2",
            "RECOVERY_INTERVAL_SECONDS": "0",
            "DEV_SEED_ENABLED": "true",
            "ARGON2_TIME_COST": "1",
            "ARGON2_MEMORY_COST_KIB": "1024",
            "ARGON2_PARALLELISM": "1",
            "FRAUD_REVIEW_THRESHOLD": "50000.00",
            "FRAUD_HARD_LIMIT": "200000.00",
            "PAYMENT_PROVIDER_DECLINE_PREFIX": "DECLINE",
            "NOTIFICATION_DELIVERY_LOG": str(tmp / "deliveries.log"),
            "ACCOUNT_SERVICE_URL": f"http://127.0.0.1:{ports['account']}",
            "TRANSACTION_SERVICE_URL": f"http://127.0.0.1:{ports['transaction']}",
            "FRAUD_SERVICE_URL": f"http://127.0.0.1:{ports['fraud']}",
            "NOTIFICATION_SERVICE_URL": f"http://127.0.0.1:{ports['notification']}",
        }
        self.services = {
            name: ServiceProcess(
                name,
                ports[name],
                {**common, "DATABASE_URL": self.database_urls[name]},
                tmp,
            )
            for name in SERVICES
        }

    def __getitem__(self, name: str) -> ServiceProcess:
        return self.services[name]

    def start_all(self) -> None:
        for service in self.services.values():
            if not service.running:
                service.start()

    def stop_all(self) -> None:
        for service in self.services.values():
            service.stop()


class Api:
    """Tiny helper around HTTPX for the e2e tests."""

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.http = httpx.Client(timeout=15)

    def url(self, service: str, path: str) -> str:
        return f"{self.stack[service].url}{path}"

    def call(self, method: str, service: str, path: str, token: str | None = None, **kw):
        headers = kw.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return self.http.request(method, self.url(service, path), headers=headers, **kw)

    def internal(self, method: str, service: str, path: str, **kw):
        return self.http.request(
            method,
            self.url(service, path),
            headers={"X-Internal-Token": self.stack.internal_token},
            **kw,
        )

    def new_customer(self) -> tuple[str, str]:
        email = f"user-{uuid.uuid4().hex[:10]}@example.com"
        password = "Sup3rSecretPassw0rd"
        registered = self.call(
            "POST",
            "auth",
            "/auth/register",
            json={"email": email, "password": password, "full_name": "E2E User"},
        )
        assert registered.status_code == 201, registered.text
        login = self.call(
            "POST", "auth", "/auth/login", json={"email": email, "password": password}
        )
        assert login.status_code == 200, login.text
        return registered.json()["id"], login.json()["access_token"]

    def new_account(self, token: str, seed: str | None = None) -> str:
        response = self.call(
            "POST", "account", "/accounts", token, json={"account_type": "SAVINGS"}
        )
        assert response.status_code == 201, response.text
        account_id = response.json()["id"]
        if seed:
            seeded = self.internal(
                "POST",
                "account",
                f"/internal/dev/accounts/{account_id}/seed",
                json={"amount": seed},
            )
            assert seeded.status_code == 200, seeded.text
        return account_id

    def balance(self, token: str, account_id: str) -> str:
        response = self.call("GET", "account", f"/accounts/{account_id}/balance", token)
        assert response.status_code == 200, response.text
        return response.json()["balance"]
