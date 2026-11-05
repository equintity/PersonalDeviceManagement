"""SQLite 数据库层:连接管理 + 建表 + 参数化查询约定。

安全约定:
- 所有查询必须使用占位符参数(?,),严禁字符串拼接 SQL
- 启用外键约束,器件删除时映射自动置空
- 数据库存放在 <项目>/data/pdl.db(已加入 .gitignore)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .paths import as_data

_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lcsc_code     TEXT UNIQUE,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT '',
    package       TEXT NOT NULL DEFAULT '',
    manufacturer  TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    qty           INTEGER NOT NULL DEFAULT 0,
    price         REAL,
    notes         TEXT NOT NULL DEFAULT '',
    attrs         TEXT NOT NULL DEFAULT '{}',
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_name ON devices(name);
CREATE INDEX IF NOT EXISTS idx_devices_code ON devices(lcsc_code);

CREATE TABLE IF NOT EXISTS box_config (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    rows           INTEGER NOT NULL DEFAULT 7,
    cols           INTEGER NOT NULL DEFAULT 9,
    led_pin        INTEGER NOT NULL DEFAULT 48,
    led_brightness INTEGER NOT NULL DEFAULT 60,
    led_layout     TEXT NOT NULL DEFAULT 'serpentine',
    device_name    TEXT NOT NULL DEFAULT 'PDL-ComponentBox',
    btn_up         INTEGER NOT NULL DEFAULT 46,
    btn_down       INTEGER NOT NULL DEFAULT 15,
    btn_left       INTEGER NOT NULL DEFAULT 41,
    btn_right      INTEGER NOT NULL DEFAULT 45,
    btn_ok         INTEGER NOT NULL DEFAULT 42,
    oled_sda       INTEGER NOT NULL DEFAULT 40,
    oled_scl       INTEGER NOT NULL DEFAULT 39,
    updated_at     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mappings (
    row       INTEGER NOT NULL,
    col       INTEGER NOT NULL,
    device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    PRIMARY KEY (row, col)
);

CREATE TABLE IF NOT EXISTS import_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    imported   INTEGER NOT NULL DEFAULT 0,
    merged     INTEGER NOT NULL DEFAULT 0,
    skipped    INTEGER NOT NULL DEFAULT 0,
    errors     TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""

_connections: dict[tuple[int, str], sqlite3.Connection] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path(root) -> Path:
    """数据库文件位置:接受 Path 或 AppPaths(取其数据根)。"""
    return as_data(root) / "data" / "pdl.db"


def get_db(root) -> sqlite3.Connection:
    """获取(线程, 数据库路径)维度的连接(WAL 模式,支持多线程)。"""
    path = str(db_path(root))
    key = (threading.get_ident(), path)
    conn = _connections.get(key)
    if conn is not None:
        return conn
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    _connections[key] = conn
    return conn


def init_db(root) -> sqlite3.Connection:
    conn = get_db(root)
    conn.executescript(_SCHEMA)
    # 器件盒配置单行表:确保存在一行默认配置(默认 7x9 格栅)
    row = conn.execute("SELECT id FROM box_config WHERE id = 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO box_config (id) VALUES (1)")
    conn.commit()
    return conn


def row_to_device(row: sqlite3.Row) -> dict:
    """devices 行 → 前端字典(attrs JSON 安全解析)。"""
    d = dict(row)
    try:
        d["attrs"] = json.loads(d.get("attrs") or "{}")
        if not isinstance(d["attrs"], dict):
            d["attrs"] = {}
    except (ValueError, TypeError):
        d["attrs"] = {}
    return d
