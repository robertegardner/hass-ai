from fastapi.testclient import TestClient

from pae.api.app import create_app


def test_healthz_and_metrics():
    client = TestClient(create_app())

    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = client.get("/readyz")
    assert resp.status_code == 200

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "pae_build_info" in resp.text
