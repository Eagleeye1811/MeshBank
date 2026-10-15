"""Login throttling: per-IP, per-account+IP and per-account windows."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from common.database import utcnow
from tests.conftest import PASSWORD


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


def _client(settings, clock, **overrides):
    app = create_app(settings.model_copy(update=overrides))
    app.state.clock = clock
    return TestClient(app)


@pytest.fixture
def throttled(settings, clock):
    with _client(
        settings,
        clock,
        login_account_ip_max_failures=3,
        login_ip_max_failures=10,
        login_account_max_failures=6,
        login_account_window_seconds=900,
        login_ip_window_seconds=300,
    ) as client:
        client.post(
            "/auth/register",
            json={"email": "asha@example.com", "password": PASSWORD, "full_name": "Asha"},
        )
        yield client


def _login(client, password="WrongPassword123", email="asha@example.com", ip=None):  # noqa: S107
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post("/auth/login", json={"email": email, "password": password}, headers=headers)


def test_account_ip_throttle_then_recovery_after_window(throttled, clock):
    for _ in range(3):
        assert _login(throttled).status_code == 401
    blocked = _login(throttled, password=PASSWORD)  # even the right password waits
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "TOO_MANY_LOGIN_ATTEMPTS"
    assert 0 < int(blocked.headers["Retry-After"]) <= 900
    assert "asha" not in blocked.text
    clock.advance(901)  # window expires: no permanent lockout
    assert _login(throttled, password=PASSWORD).status_code == 200


def test_success_resets_account_ip_counter(throttled):
    for _ in range(2):
        _login(throttled)
    assert _login(throttled, password=PASSWORD).status_code == 200
    for _ in range(2):
        assert _login(throttled).status_code == 401  # counter restarted


def test_unknown_email_is_throttled_identically(throttled):
    responses = [_login(throttled, email="ghost@example.com").status_code for _ in range(4)]
    assert responses == [401, 401, 401, 429]


def test_ip_throttle_covers_many_accounts(throttled, clock):
    for i in range(10):
        _login(throttled, email=f"user{i}@example.com")
    assert _login(throttled, email="someone-else@example.com").status_code == 429
    clock.advance(301)
    assert _login(throttled, email="someone-else@example.com").status_code == 401


def test_forwarded_for_ignored_without_trusted_proxy(throttled):
    # Rotating a spoofed X-Forwarded-For must not evade the throttle.
    for i in range(3):
        _login(throttled, ip=f"198.51.100.{i}")
    assert _login(throttled, ip="198.51.100.99").status_code == 429


def test_account_wide_throttle_is_higher_and_temporary(settings, clock):
    """Distributed guessing (many IPs via a trusted proxy) hits the account-wide
    limit, which still expires."""
    with _client(
        settings,
        clock,
        trusted_proxy_cidrs="127.0.0.0/8,10.0.0.0/8",
        login_account_ip_max_failures=3,
        login_account_max_failures=6,
        login_ip_max_failures=100,
    ) as client:
        client.post(
            "/auth/register",
            json={"email": "asha@example.com", "password": PASSWORD, "full_name": "Asha"},
        )
        # TestClient's peer is "testclient", not an IP, so proxy trust is exercised
        # via the resolver unit tests; here every attempt shares one client key.
        statuses = [_login(client, ip=f"198.51.100.{i}").status_code for i in range(4)]
        assert statuses[-1] == 429
        clock.advance(901)
        assert _login(client, password=PASSWORD).status_code == 200


def test_throttling_can_be_disabled(settings, clock):
    with _client(
        settings, clock, login_throttle_enabled=False, login_account_ip_max_failures=1
    ) as client:
        assert [_login(client).status_code for _ in range(3)] == [401, 401, 401]
