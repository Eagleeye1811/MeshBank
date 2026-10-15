"""Real HTTP clients against httpx.MockTransport: timeouts, outages, retries."""

from decimal import Decimal

import httpx
import pytest

from app.clients import HttpAccountClient, HttpFraudClient, HttpNotificationClient
from common.errors import DownstreamError
from common.http_client import InternalClient
from common.logging_setup import set_request_id


def make(handler, max_attempts=2) -> InternalClient:
    return InternalClient(
        target="account-service",
        base_url="http://account",
        caller="transaction-service",
        internal_token="tok",
        timeout_seconds=0.5,
        max_attempts=max_attempts,
        backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )


def test_headers_and_successful_debit():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(request.headers)
        return httpx.Response(200, json={"operation_id": "op", "status": "APPLIED"})

    set_request_id("req-abc")
    outcome = HttpAccountClient(make(handler)).debit("a1", "tx:debit", Decimal("1.00"), "INR", "tx")
    assert outcome.applied
    assert seen["x-internal-token"] == "tok"
    assert seen["x-caller-service"] == "transaction-service"
    assert seen["x-request-id"] == "req-abc"
    set_request_id(None)


def test_connect_error_is_definitely_not_processed():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(DownstreamError) as exc:
        HttpAccountClient(make(handler)).debit("a1", "op-1", Decimal("1"), "INR", "r")
    assert exc.value.maybe_processed is False
    assert exc.value.code == "ACCOUNT_SERVICE_UNAVAILABLE"
    assert len(calls) == 2  # idempotent call retried


def test_read_timeout_may_have_been_processed():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(DownstreamError) as exc:
        HttpAccountClient(make(handler)).credit("a1", "op-1", Decimal("1"), "INR", "r")
    assert exc.value.maybe_processed is True
    assert exc.value.status_code == 504


def test_timeout_then_refused_is_still_unknown():
    responses = [httpx.ReadTimeout("slow"), httpx.ConnectError("down")]

    def handler(request):
        raise responses.pop(0)

    with pytest.raises(DownstreamError) as exc:
        HttpAccountClient(make(handler)).debit("a1", "op-1", Decimal("1"), "INR", "r")
    assert exc.value.maybe_processed is True


def test_non_retry_safe_requests_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(DownstreamError):
        make(handler).request("POST", "/x", json={})
    assert len(calls) == 1


def test_404_maps_to_missing_account_and_rejection():
    client = HttpAccountClient(make(lambda r: httpx.Response(404, json={"error": {"code": "X"}})))
    assert client.get_account("nope") is None
    outcome = client.debit("nope", "op-1", Decimal("1"), "INR", "r")
    assert outcome.status == "REJECTED" and outcome.rejection_code == "ACCOUNT_NOT_FOUND"


def test_server_error_and_invalid_response():
    client = HttpAccountClient(make(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(DownstreamError) as exc:
        client.get_account("a")
    assert exc.value.status_code == 502

    garbage = HttpAccountClient(make(lambda r: httpx.Response(200, json={"unexpected": True})))
    with pytest.raises(DownstreamError) as exc:
        garbage.get_account("a")
    assert exc.value.code.endswith("INVALID_RESPONSE")


def test_fraud_and_notification_clients():
    fraud = HttpFraudClient(
        make(
            lambda r: httpx.Response(
                200, json={"decision": "REVIEW", "reason_codes": ["R"], "explanation": "e"}
            )
        )
    )
    assert fraud.evaluate({}).decision == "REVIEW"
    with pytest.raises(DownstreamError):
        HttpFraudClient(make(lambda r: httpx.Response(409, json={}))).evaluate({})

    HttpNotificationClient(make(lambda r: httpx.Response(200, json={}))).notify(
        event_key="k", customer_id="c", type="T", message="m"
    )
    with pytest.raises(DownstreamError):
        HttpNotificationClient(make(lambda r: httpx.Response(422, json={}))).notify(
            event_key="k", customer_id="c", type="T", message="m"
        )
