def test_create_application(client):
    resp = client.post("/applications", json={"name": "my-app"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "my-app"
    assert data["is_active"] is True


def test_duplicate_application_name(client, sample_application):
    resp = client.post("/applications", json={"name": sample_application["name"]})
    assert resp.status_code == 409


def test_list_applications(client, sample_application):
    resp = client.get("/applications")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_update_application(client, sample_application):
    resp = client.patch(f"/applications/{sample_application['id']}", json={"name": "renamed"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "renamed"


def test_delete_application(client, sample_application):
    resp = client.delete(f"/applications/{sample_application['id']}")
    assert resp.status_code == 204


def test_create_api_key(client, sample_application):
    resp = client.post(f"/applications/{sample_application['id']}/keys", json={"label": "dev"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["key"] is not None
    assert data["key"].startswith("whc_")
    assert data["label"] == "dev"


def test_list_api_keys(client, sample_application):
    client.post(f"/applications/{sample_application['id']}/keys", json={"label": "k1"})
    resp = client.get(f"/applications/{sample_application['id']}/keys")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_delete_api_key(client, sample_application):
    key_resp = client.post(f"/applications/{sample_application['id']}/keys", json={"label": "del"})
    key_id = key_resp.json()["id"]
    resp = client.delete(f"/applications/{sample_application['id']}/keys/{key_id}")
    assert resp.status_code == 204
