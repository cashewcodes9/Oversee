from fastapi.testclient import TestClient

from oversee.api.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_openapi_lists_endpoints():
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/ports", "/ports/{port_id}/activity", "/vessels", "/kpis/overview"} <= set(paths)
