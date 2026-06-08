def test_create_alert_config(client, sample_endpoint):
    resp = client.post("/alert-configs", json={
        "endpoint_id": sample_endpoint["id"],
        "channel": "webhook",
        "destination": "http://alerts.example.com/hook",
        "failure_threshold": 5,
        "cooldown_seconds": 600,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["channel"] == "webhook"
    assert data["failure_threshold"] == 5


def test_create_alert_config_email(client):
    resp = client.post("/alert-configs", json={
        "channel": "email",
        "destination": "ops@example.com",
    })
    assert resp.status_code == 201


def test_list_alert_configs(client, sample_endpoint):
    client.post("/alert-configs", json={
        "endpoint_id": sample_endpoint["id"],
        "channel": "webhook",
        "destination": "http://test.com",
    })
    resp = client.get("/alert-configs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_delete_alert_config(client):
    resp = client.post("/alert-configs", json={
        "channel": "webhook",
        "destination": "http://del.test.com",
    })
    config_id = resp.json()["id"]
    resp = client.delete(f"/alert-configs/{config_id}")
    assert resp.status_code == 204


def test_invalid_channel(client):
    resp = client.post("/alert-configs", json={
        "channel": "sms",
        "destination": "+1234567890",
    })
    assert resp.status_code == 422
