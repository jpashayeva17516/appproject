"""Быстрая проверка API: python test_smoke.py (нужен httpx: pip install httpx)"""
import os, tempfile
import db
db.DB_PATH = __import__("pathlib").Path(tempfile.mkdtemp()) / "t.db"
from fastapi.testclient import TestClient
import app as appmod

with TestClient(appmod.app) as c:
    assert c.post("/api/v1/devices/X/logs", json={"logs": [{"message": "a"}]}).status_code == 404
    c.post("/api/v1/devices/register", json={"device_id": "D1", "region": "baku", "name": "n"})
    c.post("/api/v1/devices/register", json={"device_id": "D2", "region": "ganja"})
    r = c.post("/api/v1/devices/D1/logs", json={"logs": [
        {"level": "info", "message": "hello 50%_x", "uptime_ms": 10},
        {"level": "ERROR", "message": "boom \"q\""},
        {"level": "weird", "message": "x"}]})
    assert r.json()["stored"] == 3
    c.post("/api/v1/devices/D2/logs", json={"logs": [{"level": "warn", "message": "w"}]})
    assert len(c.get("/api/v1/logs").json()) == 4
    assert len(c.get("/api/v1/logs?region=baku").json()) == 3
    assert [l["level"] for l in c.get("/api/v1/logs?level=warn").json()] == ["warn", "error"]
    assert len(c.get("/api/v1/logs?q=50%25_").json()) == 1      # % и _ экранируются
    assert len(c.get("/api/v1/logs?level=bad").json()["detail"]) >= 1 if c.get("/api/v1/logs?level=bad").status_code == 422 else False
    top = c.get("/api/v1/logs?limit=1").json()[0]["id"]
    assert len(c.get(f"/api/v1/logs?before_id={top}").json()) == 3
    assert c.get(f"/api/v1/logs?after_id={top}").json() == []
    d = {x["device_id"]: x for x in c.get("/api/v1/devices").json()}
    assert d["D1"]["log_count"] == 3 and d["D1"]["errors_24h"] == 1
    assert "boom" in c.get("/api/v1/logs/export.csv").text
    assert c.post("/api/v1/devices/D1/logs", json={"logs": []}).status_code == 422
    appmod.API_KEY = "k"
    assert c.post("/api/v1/devices/D1/logs", json={"logs": [{"message": "a"}]}).status_code == 401
    assert c.post("/api/v1/devices/D1/logs", headers={"X-API-Key": "k"}, json={"logs": [{"message": "a"}]}).status_code == 200
    assert c.get("/").status_code == 200
print("OK")
