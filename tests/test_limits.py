import os, tempfile
from fastapi.testclient import TestClient

def client(monkeypatch):
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=".db"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    from app.main import app
    return TestClient(app)

def test_limits(monkeypatch):
    c = client(monkeypatch)
    for body in [{"name": "A", "stock_kg": 1e30}, {"name": "B", "stock_kg": -5}, {"name": "x" * 400, "stock_kg": 1},
                 {"name": "<img onerror=x>", "stock_kg": 1}, {"name": "C", "stock_kg": 1, "reorder_level_kg": 1e12}]:
        r = c.post("/api/brands", json=body)
        assert r.status_code == 422 and "message" in r.json() and "fix" in r.json(), body
    assert c.post("/api/brands", json={"name": "QA Ünï çödé 米 🍚 & Co", "stock_kg": 5}).status_code == 200
    assert c.get("/api/state").status_code == 200
    b = c.get("/api/state").json()["brands"][0]["id"]
    assert c.post("/api/movements", json={"brand_id": b, "kind": "in", "kg": 1e30}).status_code == 422
    assert c.delete(f"/api/brands/{b}").status_code == 200
