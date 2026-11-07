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

# T1/QA-01:schema 版本唯一真相(1=v1.0,2=价格列,3=deleted_at,4=冲销列)
SCHEMA_VERSION = 4


def schema_version(conn: sqlite3.Connection) -> int:
    """读 PRAGMA user_version;旧库 0 视为 1。"""
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    return v if v else 1

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

-- T-M1-01:库存流水(只增不删,审计与撤销依据)
CREATE TABLE IF NOT EXISTS stock_moves (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  INTEGER NOT NULL,
    delta      INTEGER NOT NULL,
    before_qty INTEGER NOT NULL,
    after_qty  INTEGER NOT NULL,
    source     TEXT NOT NULL,          -- manual/box_take/box_return/import/adjust/undo
    ref        TEXT NOT NULL DEFAULT '',
    cell       INTEGER,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    reversed_move_id INTEGER           -- T2:冲销指向(NULL=未被冲销)
);
CREATE INDEX IF NOT EXISTS idx_moves_device ON stock_moves(device_id, id);

-- T-M1-03:操作快照(破坏性映射操作前保存,保留最近 10 条)
CREATE TABLE IF NOT EXISTS snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,        -- clear_box/auto_arrange/resize/pre_restore
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- 统一操作日志:时间/指令/对象/结果(界面「操作记录」页数据源)
CREATE TABLE IF NOT EXISTS op_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    action     TEXT NOT NULL,      -- 指令:device.create / box.clear / import.files …
    target     TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    ok         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ops_id ON op_logs(id);

-- T-M2-01:导入批次幂等登记(同一文件默认不重复入库)
CREATE TABLE IF NOT EXISTS import_batches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL,
    kind            TEXT NOT NULL,
    source_name     TEXT NOT NULL DEFAULT '',
    stats_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batches_key ON import_batches(idempotency_key);
"""

# 增量迁移:旧库补列(幂等)
_MIGRATION_COLUMNS = {
    "devices": {
        "model": "TEXT NOT NULL DEFAULT ''",            # 型号(MPN),用于无C编号时的价格匹配
        "price_currency": "TEXT NOT NULL DEFAULT ''",   # 价格币种:''=用户手填(未标注),USD/CNY=接口来源
        "price_status": "TEXT NOT NULL DEFAULT ''",     # ''未刷新 / ok / no_match / discontinued / error
        "price_updated_at": "TEXT",                     # 最近一次价格刷新时间(UTC)
        "price_source": "TEXT NOT NULL DEFAULT ''",     # lcsc / jlcpcb
        "deleted_at": "TEXT",                           # T-M1-03:软删除时间(NULL=在库)
    },
    "stock_moves": {
        "reversed_move_id": "INTEGER",                  # T2:冲销指向(NULL=未被冲销)
    },
}

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
    _migrate(conn)
    # 器件盒配置单行表:确保存在一行默认配置(默认 7x9 格栅)
    row = conn.execute("SELECT id FROM box_config WHERE id = 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO box_config (id) VALUES (1)")
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> tuple[int, int]:
    """T1:幂等增量列迁移 + user_version 版本化,返回 (from, to)。"""
    from_version = schema_version(conn)
    for table, columns in _MIGRATION_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, ddl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_moves_reversed"
        " ON stock_moves(reversed_move_id) WHERE reversed_move_id IS NOT NULL")
    to_version = SCHEMA_VERSION
    if from_version >= 1 and from_version != to_version:
        try:
            conn.execute(
                "INSERT INTO import_logs (kind, detail, imported, merged, skipped,"
                " errors, created_at) VALUES ('migrate', ?, 0, 0, 0, '[]', ?)",
                (f"schema {from_version} -> {to_version}", utc_now()))
            conn.commit()
        except sqlite3.Error:
            pass
    return from_version, to_version


def migrate_db_file(path: Path) -> tuple[int, int]:
    """T1:对磁盘上的独立数据库文件执行 schema+_migrate(备份恢复用)。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        versions = _migrate(conn)
        conn.commit()
        return versions
    finally:
        conn.close()


def prune_backups(root, pattern: str, keep: int) -> None:
    """按通配符清理备份目录,保留最近 keep 份。"""
    bdir = as_data(root) / "data" / "backups"
    files = sorted(bdir.glob(pattern))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 备份(T-M1-04):基于 sqlite backup API 的一致性快照
# ---------------------------------------------------------------------------
def log_op(root, action: str, target: str = "", detail: str = "",
           ok: bool = True) -> None:
    """写一条操作日志。日志失败绝不影响业务操作本身;超过 2 万条时裁剪至 1.5 万。"""
    try:
        conn = get_db(root)
        conn.execute(
            "INSERT INTO op_logs (created_at, action, target, detail, ok)"
            " VALUES (?,?,?,?,?)",
            (utc_now(), action[:60], (target or "")[:120],
             (detail or "")[:500], 1 if ok else 0))
        if conn.execute("SELECT COUNT(*) FROM op_logs").fetchone()[0] > 20000:
            conn.execute(
                "DELETE FROM op_logs WHERE id NOT IN"
                " (SELECT id FROM op_logs ORDER BY id DESC LIMIT 15000)")
        conn.commit()
    except sqlite3.Error:
        pass


def backup_db(root, dest: Path) -> Path:
    """把当前数据库做一致性快照到 dest(在线备份,不阻塞写入)。"""
    src = get_db(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
    return dest


def daily_backup(root, *, keep: int = 7) -> Path | None:
    """启动时自动日备份到 <数据根>/backups/,保留最近 keep 份。"""
    if not db_path(root).exists():
        return None
    bdir = as_data(root) / "data" / "backups"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = bdir / f"pdl-{stamp}.db"
    try:
        if not dest.exists():
            backup_db(root, dest)
        files = sorted(bdir.glob("pdl-*.db"))
        for old in files[:-keep]:
            old.unlink(missing_ok=True)
        return dest
    except (OSError, sqlite3.Error):
        return None


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
