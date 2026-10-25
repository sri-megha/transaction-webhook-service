import os
import time

# Point the app at a throwaway database and a short delay before importing it.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_transactions.db"
os.environ["PROCESSING_DELAY_SECONDS"] = "1"
os.environ["SWEEP_INTERVAL_SECONDS"] = "0.5"

import pytest
from fastapi.testclient import TestClient

from app.main import app

PAYLOAD = {
    "transaction_id": "txn_abc123def456",
    "source_account": "acc_user_789",
    "destination_account": "acc_merchant_456",
    "amount": 1500,
    "currency": "INR",
}


@pytest.fixture
def client():
    if os.path.exists("test_transactions.db"):
        os.remove("test_transactions.db")
    with TestClient(app) as c:
        yield c
    os.remove("test_transactions.db")


def test_health(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.json()["status"] == "HEALTHY"
    assert res.json()["current_time"].endswith("Z")


def test_transaction_is_processed_after_delay(client):
    started = time.perf_counter()
    res = client.post("/v1/webhooks/transactions", json=PAYLOAD)
    assert res.status_code == 202
    assert time.perf_counter() - started < 0.5

    [txn] = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()
    assert txn["status"] == "PROCESSING"
    assert txn["processed_at"] is None

    time.sleep(1.5)

    [txn] = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()
    assert txn["status"] == "PROCESSED"
    assert txn["processed_at"] is not None
    assert txn["amount"] == 1500


def test_duplicate_webhooks_are_processed_once(client):
    responses = [client.post("/v1/webhooks/transactions", json=PAYLOAD) for _ in range(5)]
    assert all(r.status_code == 202 for r in responses)
    assert [r.json()["message"] for r in responses].count("accepted") == 1

    # A retry with a different body must not overwrite the original.
    client.post("/v1/webhooks/transactions", json={**PAYLOAD, "amount": 99})

    time.sleep(1.5)
    body = client.get(f"/v1/transactions/{PAYLOAD['transaction_id']}").json()
    assert len(body) == 1
    assert body[0]["status"] == "PROCESSED"
    assert body[0]["amount"] == 1500


def test_unknown_transaction_returns_404(client):
    assert client.get("/v1/transactions/txn_missing").status_code == 404


def test_invalid_payload_is_rejected(client):
    res = client.post("/v1/webhooks/transactions", json={**PAYLOAD, "amount": -5})
    assert res.status_code == 422
