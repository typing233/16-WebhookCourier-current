def test_delivery_logs_empty(client):
    resp = client.get("/delivery-logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_delivery_logs_stats_empty(client):
    resp = client.get("/delivery-logs/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_attempts"] == 0
    assert data["success_rate"] == 0.0


def test_delivery_logs_export_csv(client):
    resp = client.get("/delivery-logs/export?format=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]


def test_delivery_logs_export_json(client):
    resp = client.get("/delivery-logs/export?format=json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
