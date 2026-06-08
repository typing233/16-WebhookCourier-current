import json


def test_ingest_message(client, sample_endpoint):
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "test-key-001",
        "payload": json.dumps({"event": "test"}),
    })
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "pending"
    assert data["endpoint_id"] == sample_endpoint["id"]


def test_idempotent_dedup(client, sample_endpoint):
    payload = json.dumps({"event": "test"})
    resp1 = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "dedup-key",
        "payload": payload,
    })
    resp2 = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "dedup-key",
        "payload": payload,
    })
    assert resp1.status_code == 202
    assert resp2.status_code == 202
    assert resp1.json()["id"] == resp2.json()["id"]


def test_ingest_invalid_endpoint(client):
    resp = client.post("/messages", json={
        "endpoint_id": "nonexistent",
        "idempotency_key": "key",
        "payload": "{}",
    })
    assert resp.status_code == 404


def test_ingest_inactive_endpoint(client, sample_endpoint):
    client.patch(f"/endpoints/{sample_endpoint['id']}", json={"is_active": False})
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "key",
        "payload": "{}",
    })
    assert resp.status_code == 400


def test_list_messages(client, sample_endpoint):
    client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "list-key-1",
        "payload": json.dumps({"n": 1}),
    })
    client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "list-key-2",
        "payload": json.dumps({"n": 2}),
    })
    resp = client.get("/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_list_messages_filter_status(client, sample_endpoint):
    client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "filter-key",
        "payload": "{}",
    })
    resp = client.get("/messages?status=pending")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


def test_get_message(client, sample_endpoint):
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "get-key",
        "payload": "{}",
    })
    msg_id = resp.json()["id"]
    resp = client.get(f"/messages/{msg_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == msg_id


def test_ingest_with_event_type(client, sample_endpoint):
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "event-key",
        "payload": json.dumps({"type": "order.created"}),
        "event_type": "order.created",
    })
    assert resp.status_code == 202
    assert resp.json()["event_type"] == "order.created"
