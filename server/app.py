import asyncio
import csv
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import db

HEARTBEAT_TIMEOUT_S = 90                                         # offline, если молчит дольше
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "30"))  # 0 = хранить вечно
API_KEY = os.getenv("REGISTRY_API_KEY", "")                      # пусто = без авторизации устройств
MAX_LOG_BATCH = 100
MAX_MESSAGE_LEN = 2000

STATIC_DIR = Path(__file__).parent / "static"


async def _purge_loop():
    while True:
        try:
            if LOG_RETENTION_DAYS > 0:
                await asyncio.to_thread(db.purge_old_logs, LOG_RETENTION_DAYS)
        except Exception as e:  # не роняем сервер из-за очистки
            print("[purge] error:", e)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    task = asyncio.create_task(_purge_loop())
    yield
    task.cancel()


app = FastAPI(title="ESP32 Device Registry", lifespan=lifespan)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    """Проверяется только на эндпоинтах, которые вызывают платы."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")


class RegisterPayload(BaseModel):
    device_id: str
    region: str
    name: Optional[str] = None


class HeartbeatPayload(BaseModel):
    ip: Optional[str] = None
    fw_version: Optional[str] = None


class LogEntry(BaseModel):
    level: str = "info"
    message: str
    uptime_ms: Optional[int] = None


class LogsPayload(BaseModel):
    logs: List[LogEntry] = Field(min_length=1, max_length=MAX_LOG_BATCH)


def _status(last_seen_iso):
    if not last_seen_iso:
        return "never_seen"
    last_seen = datetime.fromisoformat(last_seen_iso)
    age = (datetime.now(timezone.utc) - last_seen).total_seconds()
    return "online" if age <= HEARTBEAT_TIMEOUT_S else "offline"


# ---------- устройства ----------

@app.post("/api/v1/devices/register", dependencies=[Depends(require_key)])
def register(payload: RegisterPayload):
    db.register_device(payload.device_id, payload.region, payload.name)
    return {"ok": True, "device_id": payload.device_id, "region": payload.region}


@app.post("/api/v1/devices/{device_id}/heartbeat", dependencies=[Depends(require_key)])
def heartbeat(device_id: str, payload: HeartbeatPayload, request: Request):
    ip = payload.ip or (request.client.host if request.client else None)
    ok = db.record_heartbeat(device_id, ip=ip, fw_version=payload.fw_version)
    if not ok:
        raise HTTPException(status_code=404, detail="device not registered — call /register first")
    return {"ok": True}


@app.get("/api/v1/devices")
def devices(region: Optional[str] = None):
    rows = db.list_devices(region)
    for r in rows:
        r["status"] = _status(r["last_seen"])
    return rows


@app.get("/api/v1/regions")
def regions():
    out = {}
    for d in db.list_devices():
        r = d["region"]
        out.setdefault(r, {"region": r, "total": 0, "online": 0})
        out[r]["total"] += 1
        if _status(d["last_seen"]) == "online":
            out[r]["online"] += 1
    return list(out.values())


# ---------- логи ----------

@app.post("/api/v1/devices/{device_id}/logs", dependencies=[Depends(require_key)])
def post_logs(device_id: str, payload: LogsPayload):
    entries = []
    for e in payload.logs:
        level = e.level.lower()
        if level == "warning":
            level = "warn"
        if level not in db.LEVELS:
            level = "info"
        entries.append({"level": level, "message": e.message[:MAX_MESSAGE_LEN], "uptime_ms": e.uptime_ms})
    n = db.insert_logs(device_id, entries)
    if n is None:
        raise HTTPException(status_code=404, detail="device not registered — call /register first")
    return {"ok": True, "stored": n}


def _log_filters(
    device_id: Optional[str] = None,
    region: Optional[str] = None,
    level: Optional[str] = Query(default=None, pattern="^(debug|info|warn|error)$"),
    q: Optional[str] = None,
):
    return dict(device_id=device_id, region=region, level=level, q=q)


@app.get("/api/v1/logs")
def get_logs(
    f: dict = Depends(_log_filters),
    before_id: Optional[int] = None,
    after_id: Optional[int] = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    return db.query_logs(**f, before_id=before_id, after_id=after_id, limit=limit)


@app.get("/api/v1/logs/export.csv")
def export_logs(f: dict = Depends(_log_filters), limit: int = Query(default=10000, ge=1, le=100000)):
    rows = db.query_logs(**f, limit=limit)

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "received_at", "region", "device_id", "level", "uptime_ms", "message"])
        yield buf.getvalue(); buf.seek(0); buf.truncate()
        for r in rows:
            w.writerow([r["id"], r["received_at"], r["region"], r["device_id"], r["level"], r["uptime_ms"], r["message"]])
            yield buf.getvalue(); buf.seek(0); buf.truncate()

    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=device_logs.csv"})


@app.get("/")
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")
