from sqlalchemy import select

from app.models import User
from tests.conftest import PASSWORD


def test_register_returns_safe_user_and_hashes_password(client, register):
    response = register()
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "asha@example.com"
    assert body["full_name"] == "Asha Rao"
    assert "password" not in body and "password_hash" not in body

    with client.app.state.db.session() as session:
        user = session.scalar(select(User))
        assert user.password_hash != PASSWORD
        assert user.password_hash.startswith("$argon2id$")


def test_register_normalizes_email_case(register):
    assert register(email="Asha@Example.COM").json()["email"] == "asha@example.com"


def test_duplicate_registration_is_conflict(register):
    assert register().status_code == 201
    response = register(email="ASHA@example.com")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "USER_EXISTS"


def test_register_rejects_invalid_email_and_weak_password(register):
    bad_email = register(email="not-an-email")
    assert bad_email.status_code == 422
    assert bad_email.json()["error"]["code"] == "VALIDATION_ERROR"

    weak = register(password="short1")
    assert weak.status_code == 422
    # The rejected password must never be echoed back.
    assert "short1" not in weak.text

    assert register(password="onlyletterslongenough").status_code == 422


def test_register_rejects_unknown_fields(client):
    response = client.post(
        "/auth/register",
        json={"email": "a@example.com", "password": PASSWORD, "full_name": "A", "admin": True},
    )
    assert response.status_code == 422


def test_login_wrong_password_and_unknown_email_are_indistinguishable(client, register):
    register()
    wrong = client.post(
        "/auth/login", json={"email": "asha@example.com", "password": "Wrong12345678"}
    )
    unknown = client.post("/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_me_requires_authentication(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_me_returns_current_user(client, token):
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "asha@example.com"


def test_verify_endpoint(client, token):
    ok = client.post("/auth/verify", json={"token": token})
    assert ok.status_code == 200
    assert ok.json()["valid"] is True
    assert ok.json()["email"] == "asha@example.com"

    bad = client.post("/auth/verify", json={"token": "nope"})
    assert bad.status_code == 401


def test_errors_carry_request_id(client):
    response = client.get("/auth/me", headers={"X-Request-ID": "req-123"})
    assert response.headers["X-Request-ID"] == "req-123"
    assert response.json()["error"]["request_id"] == "req-123"


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok", "service": "auth-service"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"
    assert "sqlite" not in ready.text
