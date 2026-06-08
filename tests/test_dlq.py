import json


def test_list_dlq_empty(client):
    resp = client.get("/dlq")
    assert resp.status_code == 200
    assert resp.json() == []


def test_dlq_stats_empty(client):
    resp = client.get("/dlq/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


def test_replay_not_found(client):
    resp = client.post("/dlq/nonexistent/replay")
    assert resp.status_code == 404


def test_batch_replay_empty(client):
    resp = client.post("/dlq/batch-replay", json={"ids": []})
    assert resp.status_code == 200
    assert resp.json()["replayed_count"] == 0


def test_purge_requires_criteria(client):
    resp = client.post("/dlq/purge", json={})
    assert resp.status_code == 400
