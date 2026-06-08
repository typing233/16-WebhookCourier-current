def test_create_endpoint(client):
    resp = client.post("/endpoints", json={
        "url": "http://example.com/hook",
        "secret": "supersecretkey123456",
        "description": "My endpoint",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "http://example.com/hook"
    assert data["is_active"] is True
    assert data["jitter_strategy"] == "full"
    assert data["circuit_state"] == "closed"


def test_list_endpoints(client, sample_endpoint):
    resp = client.get("/endpoints")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["id"] == sample_endpoint["id"]


def test_get_endpoint(client, sample_endpoint):
    resp = client.get(f"/endpoints/{sample_endpoint['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == sample_endpoint["id"]


def test_get_endpoint_not_found(client):
    resp = client.get("/endpoints/nonexistent")
    assert resp.status_code == 404


def test_update_endpoint(client, sample_endpoint):
    resp = client.patch(f"/endpoints/{sample_endpoint['id']}", json={
        "description": "Updated",
        "max_retries": 10,
        "jitter_strategy": "equal",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "Updated"
    assert data["max_retries"] == 10
    assert data["jitter_strategy"] == "equal"


def test_delete_endpoint(client, sample_endpoint):
    resp = client.delete(f"/endpoints/{sample_endpoint['id']}")
    assert resp.status_code == 204
    resp = client.get(f"/endpoints/{sample_endpoint['id']}")
    assert resp.status_code == 404


def test_filter_active_endpoints(client, sample_endpoint):
    client.patch(f"/endpoints/{sample_endpoint['id']}", json={"is_active": False})
    resp = client.get("/endpoints?is_active=true")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_create_endpoint_validation(client):
    resp = client.post("/endpoints", json={
        "url": "http://example.com/hook",
        "secret": "short",
    })
    assert resp.status_code == 422
