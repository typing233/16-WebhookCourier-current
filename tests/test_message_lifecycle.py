"""Tests for full message management lifecycle: delete, batch-cancel, batch-retry."""
import json


def test_delete_dead_message(client, sample_endpoint):
    """Should be able to delete a dead (cancelled) message."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "del-test-1",
        "payload": json.dumps({"data": "test"}),
    })
    msg_id = resp.json()["id"]

    # Cancel it first (makes it dead)
    client.post(f"/messages/{msg_id}/cancel")

    # Now delete it
    resp = client.delete(f"/messages/{msg_id}")
    assert resp.status_code == 204

    # Should be gone
    resp = client.get(f"/messages/{msg_id}")
    assert resp.status_code == 404


def test_delete_pending_message_fails(client, sample_endpoint):
    """Cannot delete a pending message (must cancel first)."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "del-pending-1",
        "payload": json.dumps({"data": "test"}),
    })
    msg_id = resp.json()["id"]

    resp = client.delete(f"/messages/{msg_id}")
    assert resp.status_code == 409


def test_batch_cancel_messages(client, sample_endpoint):
    """Batch cancel multiple pending messages."""
    ids = []
    for i in range(3):
        resp = client.post("/messages", json={
            "endpoint_id": sample_endpoint["id"],
            "idempotency_key": f"batch-cancel-{i}",
            "payload": json.dumps({"n": i}),
        })
        ids.append(resp.json()["id"])

    resp = client.post("/messages/batch/cancel", json={"ids": ids})
    assert resp.status_code == 200
    data = resp.json()
    assert data["affected_count"] == 3
    assert set(data["ids"]) == set(ids)

    # Verify all are dead
    for msg_id in ids:
        resp = client.get(f"/messages/{msg_id}")
        assert resp.json()["status"] == "dead"


def test_batch_cancel_skips_non_pending(client, sample_endpoint):
    """Batch cancel should only affect pending/in-flight messages."""
    # Create and cancel one message first
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "batch-skip-1",
        "payload": json.dumps({}),
    })
    dead_id = resp.json()["id"]
    client.post(f"/messages/{dead_id}/cancel")

    # Create a pending one
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "batch-skip-2",
        "payload": json.dumps({}),
    })
    pending_id = resp.json()["id"]

    resp = client.post("/messages/batch/cancel", json={"ids": [dead_id, pending_id]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["affected_count"] == 1
    assert data["ids"] == [pending_id]


def test_batch_retry_messages(client, sample_endpoint):
    """Batch retry multiple dead messages."""
    ids = []
    for i in range(3):
        resp = client.post("/messages", json={
            "endpoint_id": sample_endpoint["id"],
            "idempotency_key": f"batch-retry-{i}",
            "payload": json.dumps({"n": i}),
        })
        msg_id = resp.json()["id"]
        client.post(f"/messages/{msg_id}/cancel")
        ids.append(msg_id)

    resp = client.post("/messages/batch/retry", json={"ids": ids})
    assert resp.status_code == 200
    data = resp.json()
    assert data["affected_count"] == 3

    # Verify all are pending again
    for msg_id in ids:
        resp = client.get(f"/messages/{msg_id}")
        assert resp.json()["status"] == "pending"


def test_batch_retry_skips_non_dead(client, sample_endpoint):
    """Batch retry should only affect dead messages."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "batch-retry-skip-1",
        "payload": json.dumps({}),
    })
    pending_id = resp.json()["id"]

    resp = client.post("/messages/batch/retry", json={"ids": [pending_id]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["affected_count"] == 0
    assert data["ids"] == []
