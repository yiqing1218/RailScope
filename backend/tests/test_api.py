from fastapi.testclient import TestClient
from railscope.main import app

client = TestClient(app)

def test_health_and_operations_api():
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/blocks").status_code == 200
    assert client.get("/api/v1/conflicts").json()
    corridors = client.get("/api/v1/corridors").json()
    assert len(corridors) == 2
    assert client.get(f"/api/v1/corridors/{corridors[0]['id']}").status_code == 200


def test_edge_bbox_rejects_reversed_or_out_of_range_coordinates():
    assert client.get("/api/v1/network/edges?bbox=120,30,119,31").status_code == 400
    assert client.get("/api/v1/network/edges?bbox=0,0,181,1").status_code == 400

def test_dispatch_api_preserves_scheduled_endpoint():
    response = client.post("/api/v1/dispatch/delay", json={"train_run_id":"run-102","seconds":600})
    assert response.status_code == 200
    assert client.get("/api/v1/train-runs/run-102/stops").status_code == 200
    assert client.post("/api/v1/dispatch/reset").status_code == 200
