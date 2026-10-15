import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_requires_internal_token(client, payload):
    response = client.post("/evaluate", json=payload())
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("amount", "decision"),
    [("100.00", "APPROVE"), ("50000.01", "REVIEW"), ("200000.01", "REJECT"), ("-1", "REJECT")],
)
def test_decisions(client, headers, payload, amount, decision):
    response = client.post("/evaluate", json=payload(amount=amount), headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == decision
    assert body["reason_codes"]
    assert body["explanation"]
    assert body["rules_version"]


def test_repeated_evaluation_returns_stored_decision(client, headers, payload):
    first = client.post("/evaluate", json=payload(amount="60000"), headers=headers).json()
    second = client.post("/evaluate", json=payload(amount="60000"), headers=headers).json()
    assert first["decision"] == second["decision"] == "REVIEW"
    assert first["replayed"] is False and second["replayed"] is True
    assert first["evaluated_at"] == second["evaluated_at"]


def test_decision_survives_threshold_change(tmp_path, headers, payload):
    """A stored decision is not silently changed by later configuration changes."""
    db_url = f"sqlite:///{tmp_path}/shared.db"
    strict = Settings(
        _env_file=None,
        app_env="test",
        database_url=db_url,
        fraud_review_threshold="10",
        fraud_hard_limit="20",
    )
    with TestClient(create_app(strict)) as client:
        assert (
            client.post("/evaluate", json=payload(amount="15"), headers=headers).json()["decision"]
            == "REVIEW"
        )
    relaxed = Settings(_env_file=None, app_env="test", database_url=db_url)
    with TestClient(create_app(relaxed)) as client:
        again = client.post("/evaluate", json=payload(amount="15"), headers=headers).json()
        assert again["decision"] == "REVIEW" and again["replayed"] is True
        fresh = client.post(
            "/evaluate", json=payload(operation_id="op-00000002", amount="15"), headers=headers
        ).json()
        assert fresh["decision"] == "APPROVE"


def test_operation_id_reuse_with_different_payload_conflicts(client, headers, payload):
    client.post("/evaluate", json=payload(), headers=headers)
    response = client.post("/evaluate", json=payload(amount="101.00"), headers=headers)
    assert response.status_code == 409


@pytest.mark.parametrize(
    "mutation",
    [
        {"amount": "abc"},
        {"amount": None},
        {"operation_type": "WITHDRAWAL"},
        {"operation_id": "x"},
        {"unexpected": 1},
    ],
)
def test_malformed_requests(client, headers, payload, mutation):
    response = client.post("/evaluate", json=payload(**mutation), headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_required_field(client, headers, payload):
    body = payload()
    del body["customer_id"]
    assert client.post("/evaluate", json=body, headers=headers).status_code == 422


def test_invalid_threshold_configuration_fails_fast():
    with pytest.raises(ValueError):
        Settings(
            _env_file=None, app_env="test", fraud_review_threshold="500", fraud_hard_limit="100"
        )
