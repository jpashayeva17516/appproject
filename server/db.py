import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

DB_PATH = Path(__file__).parent / "devices.db"

LEVELS = ("debug", "info", "warn", "error")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            region TEXT NOT NULL,
            name TEXT,
            ip TEXT,
            fw_version TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT,
            heartbeat_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            region TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            uptime_ms INTEGER,
            received_at TEXT NOT NULL,
            FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_logs_device ON logs(device_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_logs_region ON logs(region, id DESC);
        CREATE INDEX IF NOT EXISTS idx_logs_level  ON logs(level, id DESC);
        CREATE INDEX IF NOT EXISTS idx_logs_time   ON logs(received_at);
        """
    )
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def register_device(device_id, region, name=None):
    conn = get_conn()
    existing = conn.execute(
        "SELECT device_id FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE devices SET region = ?, name = COALESCE(?, name) WHERE device_id = ?",
            (region, name, device_id),
        )
    else:
        conn.execute(
            "INSERT INTO devices (device_id, region, name, first_seen, heartbeat_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (device_id, region, name, now_iso()),
        )
    conn.commit()
    conn.close()


def record_heartbeat(device_id, ip=None, fw_version=None):
    conn = get_conn()
    row = conn.execute(
        "SELECT device_id FROM devices WHERE device_id = ?", (device_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute(
        """UPDATE devices
           SET last_seen = ?, ip = COALESCE(?, ip), fw_version = COALESCE(?, fw_version),
               heartbeat_count = heartbeat_count + 1
           WHERE device_id = ?""",
        (now_iso(), ip, fw_version, device_id),
    )
    conn.commit()
    conn.close()
    return True


def list_devices(region=None):
    """Устройства + счётчики логов (всего и ошибок за 24 ч)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    conn = get_conn()
    sql = """
        SELECT d.*,
               (SELECT COUNT(*) FROM logs l WHERE l.device_id = d.device_id) AS log_count,
               (SELECT COUNT(*) FROM logs l WHERE l.device_id = d.device_id
                    AND l.level = 'error' AND l.received_at >= ?) AS errors_24h
        FROM devices d
    """
    if region:
        rows = conn.execute(sql + " WHERE d.region = ? ORDER BY d.region, d.device_id", (since, region)).fetchall()
    else:
        rows = conn.execute(sql + " ORDER BY d.region, d.device_id", (since,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- логи ----------

def insert_logs(device_id, entries):
    """entries: list of dict(level, message, uptime_ms). Возвращает число записанных или None, если устройства нет."""
    conn = get_conn()
    dev = conn.execute("SELECT region FROM devices WHERE device_id = ?", (device_id,)).fetchone()
    if not dev:
        conn.close()
        return None
    ts = now_iso()
    conn.executemany(
        "INSERT INTO logs (device_id, region, level, message, uptime_ms, received_at) VALUES (?,?,?,?,?,?)",
        [(device_id, dev["region"], e["level"], e["message"], e.get("uptime_ms"), ts) for e in entries],
    )
    conn.commit()
    conn.close()
    return len(entries)


def query_logs(device_id=None, region=None, level=None, q=None,
               before_id=None, after_id=None, limit=200):
    where, args = [], []
    if device_id:
        where.append("device_id = ?"); args.append(device_id)
    if region:
        where.append("region = ?"); args.append(region)
    if level:
        # level=warn -> warn и выше
        idx = LEVELS.index(level)
        allowed = LEVELS[idx:]
        where.append(f"level IN ({','.join('?' * len(allowed))})"); args += allowed
    if q:
        where.append("message LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if before_id:
        where.append("id < ?"); args.append(before_id)
    if after_id:
        where.append("id > ?"); args.append(after_id)
    sql = "SELECT * FROM logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    conn = get_conn()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def purge_old_logs(days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_conn()
    cur = conn.execute("DELETE FROM logs WHERE received_at < ?", (cutoff,))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n
