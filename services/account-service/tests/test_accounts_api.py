import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_create_account_for_authenticated_customer(client, auth_headers, customer):
    response = client.post(
        "/accounts", json={"account_type": "CURRENT"}, headers=auth_headers(customer)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["customer_id"] == customer
    assert body["account_type"] == "CURRENT"
    assert body["currency"] == "INR"
    assert body["status"] == "ACTIVE"
    assert body["balance"] == "0.00"
    assert len(body["account_number"]) == 12


def test_customer_id_in_body_is_not_trusted(client, auth_headers, customer):
    response = client.post(
        "/accounts",
        json={"account_type": "SAVINGS", "customer_id": str(uuid.uuid4())},
        headers=auth_headers(customer),
    )
    assert response.status_code == 422


def test_create_account_requires_auth_and_valid_type(client, auth_headers):
    assert client.post("/accounts", json={"account_type": "SAVINGS"}).status_code == 401
    bad_type = client.post("/accounts", json={"account_type": "GOLD"}, headers=auth_headers())
    assert bad_type.status_code == 422
    bad_currency = client.post(
        "/accounts", json={"account_type": "SAVINGS", "currency": "USD"}, headers=auth_headers()
    )
    assert bad_currency.status_code == 422


def test_get_account_and_balance(client, auth_headers, customer, make_account, seed):
    account = make_account(customer)
    seed(account["id"], "1500.50")
    details = client.get(f"/accounts/{account['id']}", headers=auth_headers(customer))
    assert details.status_code == 200
    assert details.json()["balance"] == "1500.50"
    balance = client.get(f"/accounts/{account['id']}/balance", headers=auth_headers(customer))
    assert balance.status_code == 200
    assert balance.json()["balance"] == "1500.50"
    assert balance.json()["currency"] == "INR"


def test_other_customer_cannot_read_account(client, auth_headers, customer, make_account):
    account = make_account(customer)
    intruder = auth_headers(str(uuid.uuid4()))
    for path in (f"/accounts/{account['id']}", f"/accounts/{account['id']}/balance"):
        response = client.get(path, headers=intruder)
        assert response.status_code == 403
        assert "balance" not in response.json()


def test_list_accounts_enforces_ownership_and_paginates(
    client, auth_headers, customer, make_account
):
    for _ in range(3):
        make_account(customer)
    make_account(str(uuid.uuid4()))
    page = client.get(f"/customers/{customer}/accounts?limit=2", headers=auth_headers(customer))
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2
    assert all(item["customer_id"] == customer for item in page.json()["items"])

    other = client.get(f"/customers/{customer}/accounts", headers=auth_headers())
    assert other.status_code == 403


def test_invalid_and_missing_account_ids(client, auth_headers):
    assert client.get("/accounts/not-a-uuid", headers=auth_headers()).status_code == 422
    missing = client.get(f"/accounts/{uuid.uuid4()}", headers=auth_headers())
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_no_public_debit_or_credit_routes(client, auth_headers, customer, make_account):
    account = make_account(customer)
    payload = {"operation_id": "op-12345678", "amount": "10.00", "currency": "INR"}
    # Customers cannot call internal routes with a JWT.
    response = client.post(
        f"/internal/accounts/{account['id']}/credit", json=payload, headers=auth_headers(customer)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INTERNAL_ONLY"
    assert client.post(f"/accounts/{account['id']}/credit", json=payload).status_code in (404, 405)


def test_seed_endpoint_absent_unless_enabled(tmp_path, internal_headers):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=f"sqlite:///{tmp_path}/x.db",
        dev_seed_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            f"/internal/dev/accounts/{uuid.uuid4()}/seed",
            json={"amount": "10"},
            headers=internal_headers,
        )
        assert response.status_code == 404


def test_seed_cannot_be_enabled_in_production():
    with pytest.raises(ValueError, match="DEV_SEED_ENABLED"):
        Settings(
            _env_file=None,
            app_env="production",
            internal_api_token="t" * 48,
            database_url="postgresql://acct:Zq8v1L0p9WmT4r7XyBn2@db/meshbank_account",
            dev_seed_enabled=True,
        )


def test_health_and_ready(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").json()["checks"] == {"database": "ok"}
