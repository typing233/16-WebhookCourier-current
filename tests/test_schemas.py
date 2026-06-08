import json


def test_create_schema(client):
    schema = {"type": "object", "properties": {"event": {"type": "string"}}}
    resp = client.post("/schemas", json={
        "event_type": "order.created",
        "version": 1,
        "schema_definition": json.dumps(schema),
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["event_type"] == "order.created"
    assert data["version"] == 1


def test_create_schema_invalid_json(client):
    resp = client.post("/schemas", json={
        "event_type": "test",
        "version": 1,
        "schema_definition": "not json{",
    })
    assert resp.status_code == 422


def test_duplicate_schema_version(client):
    schema = json.dumps({"type": "object"})
    client.post("/schemas", json={"event_type": "dup.test", "version": 1, "schema_definition": schema})
    resp = client.post("/schemas", json={"event_type": "dup.test", "version": 1, "schema_definition": schema})
    assert resp.status_code == 409


def test_list_schemas(client):
    schema = json.dumps({"type": "object"})
    client.post("/schemas", json={"event_type": "list.test", "version": 1, "schema_definition": schema})
    resp = client.get("/schemas")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_filter_schemas_by_event_type(client):
    schema = json.dumps({"type": "object"})
    client.post("/schemas", json={"event_type": "filter.test", "version": 1, "schema_definition": schema})
    resp = client.get("/schemas?event_type=filter.test")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_delete_schema(client):
    schema = json.dumps({"type": "object"})
    resp = client.post("/schemas", json={"event_type": "del.test", "version": 1, "schema_definition": schema})
    schema_id = resp.json()["id"]
    resp = client.delete(f"/schemas/{schema_id}")
    assert resp.status_code == 204
