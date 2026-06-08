def test_create_subscription(client, sample_endpoint):
    resp = client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "order.created",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["event_type"] == "order.created"
    assert data["endpoint_id"] == sample_endpoint["id"]


def test_duplicate_subscription(client, sample_endpoint):
    client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "order.created",
    })
    resp = client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "order.created",
    })
    assert resp.status_code == 409


def test_list_subscriptions(client, sample_endpoint):
    client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "user.signup",
    })
    resp = client.get("/subscriptions")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_filter_subscriptions_by_event_type(client, sample_endpoint):
    client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "specific.event",
    })
    resp = client.get("/subscriptions?event_type=specific.event")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_delete_subscription(client, sample_endpoint):
    resp = client.post("/subscriptions", json={
        "endpoint_id": sample_endpoint["id"],
        "event_type": "delete.me",
    })
    sub_id = resp.json()["id"]
    resp = client.delete(f"/subscriptions/{sub_id}")
    assert resp.status_code == 204


def test_subscription_invalid_endpoint(client):
    resp = client.post("/subscriptions", json={
        "endpoint_id": "nonexistent",
        "event_type": "test",
    })
    assert resp.status_code == 404
