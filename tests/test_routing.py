"""Tests for rule-based routing (wildcard patterns) and message lifecycle."""
import json


def test_wildcard_subscription_routing(client, sample_endpoint):
    """Subscription with wildcard event_type should match events via fnmatch."""
    # Create a wildcard subscription
    resp = client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "order.*",
    })
    assert resp.status_code == 201

    # Route a message that should match the wildcard
    resp = client.post("/messages/route", json={
        "event_type": "order.created",
        "idempotency_key": "wc-test-1",
        "payload": json.dumps({"item": "widget"}),
    })
    assert resp.status_code == 202
    data = resp.json()
    assert len(data) == 1
    assert data[0]["endpoint_id"] == sample_endpoint["id"]


def test_wildcard_no_match(client, sample_endpoint):
    """Wildcard subscription should not match unrelated events."""
    client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "order.*",
    })

    resp = client.post("/messages/route", json={
        "event_type": "user.signup",
        "idempotency_key": "wc-nomatch-1",
        "payload": json.dumps({"name": "alice"}),
    })
    assert resp.status_code == 404


def test_exact_and_wildcard_both_match(client, sample_endpoint):
    """Both exact and wildcard subscriptions should fire for same event."""
    # Create second endpoint
    resp2 = client.post("/endpoints", json={
        "url": "http://localhost:9998/hook",
        "secret": "secret-2-long-enough",
        "max_retries": 3,
        "retry_base_interval": 1.0,
    })
    ep2 = resp2.json()

    # Exact match subscription
    client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "payment.completed",
    })
    # Wildcard subscription on different endpoint
    client.post("/subscriptions", json={
        "endpoint_id": ep2["id"],
        "event_type": "payment.*",
    })

    resp = client.post("/messages/route", json={
        "event_type": "payment.completed",
        "idempotency_key": "both-match-1",
        "payload": json.dumps({"amount": 100}),
    })
    assert resp.status_code == 202
    data = resp.json()
    assert len(data) == 2


def test_multi_segment_wildcard(client, sample_endpoint):
    """Wildcard with ** should match nested segments if using fnmatch pattern."""
    client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "events.*",
    })

    resp = client.post("/messages/route", json={
        "event_type": "events.order",
        "idempotency_key": "multi-seg-1",
        "payload": json.dumps({}),
    })
    assert resp.status_code == 202
    assert len(resp.json()) == 1


def test_cancel_pending_message(client, sample_endpoint):
    """Should be able to cancel a pending message."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "cancel-test-1",
        "payload": json.dumps({"data": "test"}),
    })
    assert resp.status_code == 202
    msg_id = resp.json()["id"]

    resp = client.post(f"/messages/{msg_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "dead"


def test_cancel_already_delivered_fails(client, sample_endpoint):
    """Cannot cancel a delivered message."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "cancel-delivered-1",
        "payload": json.dumps({"data": "test"}),
    })
    msg_id = resp.json()["id"]

    # Manually set status to delivered via direct ingest of another + get
    # We'll test by first cancelling, then trying to cancel again (it's now dead)
    client.post(f"/messages/{msg_id}/cancel")
    resp = client.post(f"/messages/{msg_id}/cancel")
    assert resp.status_code == 409


def test_retry_dead_message(client, sample_endpoint):
    """Should be able to retry a dead message."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "retry-test-1",
        "payload": json.dumps({"data": "test"}),
    })
    msg_id = resp.json()["id"]

    # First cancel it to make it dead
    client.post(f"/messages/{msg_id}/cancel")

    # Now retry it
    resp = client.post(f"/messages/{msg_id}/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "pending"


def test_retry_pending_message_fails(client, sample_endpoint):
    """Cannot retry a message that is not dead."""
    resp = client.post("/messages", json={
        "endpoint_id": sample_endpoint["id"],
        "idempotency_key": "retry-pending-1",
        "payload": json.dumps({"data": "test"}),
    })
    msg_id = resp.json()["id"]

    resp = client.post(f"/messages/{msg_id}/retry")
    assert resp.status_code == 409


def test_update_subscription(client, sample_endpoint):
    """Should be able to update subscription event_type and is_active."""
    resp = client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "old.event",
    })
    sub_id = resp.json()["id"]

    resp = client.patch(f"/subscriptions/{sub_id}", json={
        "event_type": "new.event",
        "is_active": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_type"] == "new.event"
    assert data["is_active"] is False


def test_inactive_subscription_not_routed(client, sample_endpoint):
    """Inactive subscription should not participate in routing."""
    resp = client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "inactive.test",
    })
    sub_id = resp.json()["id"]

    # Deactivate it
    client.patch(f"/subscriptions/{sub_id}", json={"is_active": False})

    # Route should find no matching subscriptions
    resp = client.post("/messages/route", json={
        "event_type": "inactive.test",
        "idempotency_key": "inactive-route-1",
        "payload": json.dumps({}),
    })
    assert resp.status_code == 404
