import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import NotificationService
from tests.conftest import make_settings


def test_create_persists_and_delivers(client, internal_headers, payload, settings):
    response = client.post("/notifications", json=payload(), headers=internal_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["delivery_status"] == "DELIVERED_SIMULATED"
    assert body["delivery_attempts"] == 1
    assert body["delivered_at"] is not None
    assert body["channel"] == "SIMULATED_LOG"
    lines = Path(settings.notification_delivery_log).read_text().splitlines()
    assert json.loads(lines[0])["notification_id"] == body["id"]


def test_create_requires_internal_token(client, payload, auth_headers, customer):
    assert client.post("/notifications", json=payload()).status_code == 403
    response = client.post("/notifications", json=payload(), headers=auth_headers(customer))
    assert response.status_code == 403


def test_duplicate_event_is_deduplicated(client, internal_headers, payload, settings):
    body = payload()
    first = client.post("/notifications", json=body, headers=internal_headers)
    second = client.post("/notifications", json=body, headers=internal_headers)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["replayed"] is True
    assert len(Path(settings.notification_delivery_log).read_text().splitlines()) == 1


def test_event_key_reuse_with_different_payload_conflicts(client, internal_headers, payload):
    body = payload()
    client.post("/notifications", json=body, headers=internal_headers)
    response = client.post(
        "/notifications", json={**body, "message": "different"}, headers=internal_headers
    )
    assert response.status_code == 409


def test_simulated_delivery_failure_is_persisted_and_retryable(tmp_path, db_url, payload):
    failing = make_settings(
        tmp_path, db_url, notification_simulated_failure_types="TRANSFER_SUCCEEDED"
    )
    headers = {"X-Internal-Token": failing.internal_token_value()}
    with TestClient(create_app(failing)) as client:
        response = client.post("/notifications", json=payload(), headers=headers)
        assert response.status_code == 201
        assert response.json()["delivery_status"] == "FAILED_SIMULATED"
        assert response.json()["delivered_at"] is None

    healthy = make_settings(tmp_path, db_url)
    with TestClient(create_app(healthy)) as client, client.app.state.db.session() as session:
        service = NotificationService(session, client.app.state.delivery_adapter)
        assert service.redeliver_pending() == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"type": "SPAM"},
        {"message": ""},
        {"event_key": "short"},
        {"customer_id": ""},
        {"message": "x" * 501},
    ],
)
def test_invalid_payloads(client, internal_headers, payload, mutation):
    response = client.post("/notifications", json=payload(**mutation), headers=internal_headers)
    assert response.status_code == 422


def test_customers_only_see_their_own(client, internal_headers, payload, auth_headers, customer):
    mine = client.post("/notifications", json=payload(), headers=internal_headers).json()
    other_customer = str(uuid.uuid4())
    client.post(
        "/notifications", json=payload(customer_id=other_customer), headers=internal_headers
    )

    own = client.get(f"/notifications/{mine['id']}", headers=auth_headers(customer))
    assert own.status_code == 200
    stolen = client.get(f"/notifications/{mine['id']}", headers=auth_headers(other_customer))
    assert stolen.status_code == 403

    listing = client.get(f"/customers/{customer}/notifications", headers=auth_headers(customer))
    assert listing.json()["total"] == 1
    forbidden = client.get(
        f"/customers/{customer}/notifications", headers=auth_headers(other_customer)
    )
    assert forbidden.status_code == 403
    assert client.get(f"/customers/{customer}/notifications").status_code == 401


def test_missing_notification(client, auth_headers, customer):
    response = client.get(f"/notifications/{uuid.uuid4()}", headers=auth_headers(customer))
    assert response.status_code == 404
