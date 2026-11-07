"""器件盒映射服务(需求 2、3)。

- 默认 7 行 × 9 列 = 63 格格栅(工程重构后的新布局)
- 行列数可动态调整,适配不同尺寸的物理器件盒
- 映射按 (row, col) 存储,调整尺寸后仍能落在正确位置,
  超出新边界的映射会被移除并在结果中报告
- 调整尺寸/修改配置时,同步向已连接的硬件推送 sync 指令
"""

from __future__ import annotations

import json
import re

from .. import security
from ..db import get_db, utc_now
from . import devices as device_service

_DEVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

DEFAULT_CONFIG = {
    "rows": 7,
    "cols": 9,
    "led_pin": 48,
    "led_brightness": 60,
    "led_layout": "serpentine",
    "device_name": "PDL-ComponentBox",
    "btn_up": 46, "btn_down": 15, "btn_left": 41, "btn_right": 45, "btn_ok": 42,
    "oled_sda": 40, "oled_scl": 39,
}

_BTN_FIELDS = {"btn_up": "上键", "btn_down": "下键", "btn_left": "左键",
               "btn_right": "右键", "btn_ok": "确认键"}

# 变更配置后需要通知硬件的回调(由 app 层注入,避免循环依赖)
_sync_hooks: list = []


def register_sync_hook(fn) -> None:
    _sync_hooks.append(fn)


def _notify_sync(project_root) -> dict:
    """T6/QA-19:同步失败可见 —— 任一盒子同步成功即成功;
    全部失败(或无已连接盒子)时返回第一个失败原因并留痕。不阻断配置保存。"""
    first_failure = ""
    any_success = False
    for fn in _sync_hooks:
        try:
            r = fn(project_root) or {}
            if r.get("sent") is True:
                any_success = True
            elif not first_failure:
                first_failure = r.get("reason") or "同步失败"
        except Exception as exc:  # 留痕而非静默(T6:消灭 except pass)
            from ..db import log_op
            log_op(project_root, "fail", "box.sync-notify", str(exc), ok=False)
            if not first_failure:
                first_failure = f"同步异常:{exc}"
    if any_success:
        return {"sent": True, "reason": ""}
    return {"sent": False, "reason": first_failure or "器件盒未连接"}


def get_config(project_root) -> dict:
    row = get_db(project_root).execute("SELECT * FROM box_config WHERE id = 1").fetchone()
    c = dict(row)
    c["cells"] = c["rows"] * c["cols"]
    c.pop("id", None)
    return c


def update_config(project_root, data: dict) -> dict:
    conn = get_db(project_root)
    current = get_config(project_root)
    updates, params = [], []

    new_rows = security.validate_int(
        data.get("rows", current["rows"]), field="行数",
        min_val=1, max_val=security.MAX_ROWS)
    new_cols = security.validate_int(
        data.get("cols", current["cols"]), field="列数",
        min_val=1, max_val=security.MAX_COLS)
    if new_rows * new_cols > security.MAX_CELLS:
        raise security.ValidationError(
            f"格栅总数不能超过 {security.MAX_CELLS} 格(当前 {new_rows}×{new_cols})")

    for key, label in _BTN_FIELDS.items():
        if key in data:
            updates.append(f"{key} = ?")
            params.append(security.validate_gpio(data[key], field=label, allow_risky=False))
    if "led_pin" in data:
        updates.append("led_pin = ?")
        params.append(security.validate_gpio(data["led_pin"], field="LED数据引脚"))
    if "led_brightness" in data:
        updates.append("led_brightness = ?")
        params.append(security.validate_int(data["led_brightness"], field="LED亮度",
                                            min_val=0, max_val=255))
    if "led_layout" in data:
        if data["led_layout"] not in ("row", "serpentine"):
            raise security.ValidationError("LED排列方式只支持 row(逐行)或 serpentine(蛇形)")
        updates.append("led_layout = ?")
        params.append(data["led_layout"])
    if "device_name" in data:
        name = security.validate_str(data["device_name"], field="设备名",
                                     required=True, max_len=30,
                                     pattern=_DEVICE_NAME_RE, pattern_hint="字母数字-_")
        updates.append("device_name = ?")
        params.append(name)
    if "oled_sda" in data:
        updates.append("oled_sda = ?")
        params.append(security.validate_gpio(data["oled_sda"], field="OLED SDA"))
    if "oled_scl" in data:
        updates.append("oled_scl = ?")
        params.append(security.validate_gpio(data["oled_scl"], field="OLED SCL"))

    # 引脚唯一性检查:同一 GPIO 不允许分配给多个功能
    fields_now = dict(current)
    fields_now.update({k: data[k] for k in
                       ("led_pin", "btn_up", "btn_down", "btn_left", "btn_right",
                        "btn_ok", "oled_sda", "oled_scl") if k in data})
    pins_used: dict[int, str] = {}
    for key in ("led_pin", "btn_up", "btn_down", "btn_left", "btn_right",
                "btn_ok", "oled_sda", "oled_scl"):
        pin = fields_now[key]
        if pin in pins_used:
            raise security.ValidationError(
                f"引脚冲突:GPIO{pin} 同时分配给了 {pins_used[pin]} 与 {key}")
        pins_used[pin] = key

    removed: list[dict] = []
    resized = new_rows != current["rows"] or new_cols != current["cols"]
    if resized:
        # 先改尺寸再清理越界映射,保留仍在边界内的器件(T-M1-03:可撤销)
        if conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]:
            _write_snapshot(conn, "resize")
        removed_rows = conn.execute(
            "SELECT m.row, m.col, d.name FROM mappings m"
            " LEFT JOIN devices d ON d.id = m.device_id"
            " WHERE m.row >= ? OR m.col >= ?",
            (new_rows, new_cols)).fetchall()
        removed = [{"row": r["row"], "col": r["col"], "name": r["name"] or ""} for r in removed_rows]
        conn.execute("DELETE FROM mappings WHERE row >= ? OR col >= ?", (new_rows, new_cols))
        updates.extend(["rows = ?", "cols = ?"])
        params.extend([new_rows, new_cols])

    if updates:
        updates.append("updated_at = ?")
        params.append(utc_now())
        params.append(1)
        conn.execute(f"UPDATE box_config SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    sync = _notify_sync(project_root)
    result = get_config(project_root)
    result["resized"] = resized
    result["removed_mappings"] = removed
    result["sync"] = sync
    return result


def get_box(project_root) -> dict:
    """返回配置 + 全部映射(带器件信息),供前端一次拉取。"""
    cfg = get_config(project_root)
    rows = get_db(project_root).execute(
        "SELECT m.row, m.col, d.id AS device_id, d.lcsc_code, d.name, d.qty,"
        " d.package, d.description"
        " FROM mappings m LEFT JOIN devices d ON d.id = m.device_id"
        " ORDER BY m.row, m.col").fetchall()
    mappings = []
    for r in rows:
        mappings.append({
            "row": r["row"], "col": r["col"],
            "cell": r["row"] * cfg["cols"] + r["col"],
            "device": None if r["device_id"] is None else {
                "id": r["device_id"], "lcsc_code": r["lcsc_code"], "name": r["name"],
                "qty": r["qty"], "package": r["package"], "description": r["description"],
            },
        })
    return {"config": cfg, "mappings": mappings}


def set_mapping(project_root, cell: int, device_id: int | None) -> dict:
    cfg = get_config(project_root)
    cell = security.validate_int(cell, field="格位编号", min_val=0, max_val=cfg["cells"] - 1)
    row, col = divmod(cell, cfg["cols"])
    conn = get_db(project_root)
    if device_id is None:
        conn.execute("DELETE FROM mappings WHERE row = ? AND col = ?", (row, col))
        conn.commit()
        return {"row": row, "col": col, "cell": cell, "device": None,
                "sync": _notify_sync(project_root)}
    device_id = security.validate_int(device_id, field="器件ID", min_val=1, max_val=2**31 - 1)
    device = device_service.get_device(project_root, device_id)  # 确认存在
    conn.execute(
        "INSERT INTO mappings (row, col, device_id) VALUES (?,?,?)"
        " ON CONFLICT(row, col) DO UPDATE SET device_id = excluded.device_id",
        (row, col, device_id))
    conn.commit()
    sync = _notify_sync(project_root)
    return {"row": row, "col": col, "cell": cell, "sync": sync,
            "device": {k: device[k] for k in ("id", "lcsc_code", "name", "qty", "package")}}


def _write_snapshot(conn, kind: str) -> int:
    """T-M1-03:破坏性映射操作前保存快照,保留最近 10 条。"""
    rows = conn.execute(
        "SELECT row, col, device_id FROM mappings ORDER BY row, col").fetchall()
    payload = json.dumps([dict(r) for r in rows], ensure_ascii=False)
    conn.execute(
        "INSERT INTO snapshots (kind, payload_json, created_at) VALUES (?,?,?)",
        (kind, payload, utc_now()))
    conn.execute(
        "DELETE FROM snapshots WHERE id NOT IN"
        " (SELECT id FROM snapshots ORDER BY id DESC LIMIT 10)")
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def latest_snapshot(project_root) -> dict | None:
    row = get_db(project_root).execute(
        "SELECT id, kind, created_at FROM snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def restore_snapshot(project_root, snapshot_id: int) -> dict:
    """T-M1-03:恢复映射快照(恢复前先对当前状态再做一次快照,可撤销恢复)。"""
    snapshot_id = security.validate_int(snapshot_id, field="快照ID",
                                        min_val=1, max_val=2**31 - 1)
    conn = get_db(project_root)
    row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if row is None:
        raise security.ValidationError(f"快照不存在:id={snapshot_id}")
    try:
        payload = json.loads(row["payload_json"])
    except ValueError as exc:
        raise security.ValidationError("快照数据损坏") from exc
    if not isinstance(payload, list):
        raise security.ValidationError("快照数据损坏")
    # 恢复前快照当前状态(撤销恢复 = 再恢复这条 pre_restore)
    pre_id = _write_snapshot(conn, "pre_restore")
    conn.execute("DELETE FROM mappings")
    restored = 0
    for m in payload:
        if not isinstance(m, dict):
            continue
        try:
            r = security.validate_int(m.get("row"), field="行", min_val=0,
                                      max_val=security.MAX_ROWS)
            c = security.validate_int(m.get("col"), field="列", min_val=0,
                                      max_val=security.MAX_COLS)
        except security.ValidationError:
            continue
        device_id = m.get("device_id")
        conn.execute(
            "INSERT OR REPLACE INTO mappings (row, col, device_id) VALUES (?,?,?)",
            (r, c, device_id if isinstance(device_id, int) else None))
        restored += 1
    conn.commit()
    sync = _notify_sync(project_root)
    return {"restored": restored, "from_kind": row["kind"],
            "safety_snapshot": pre_id, "sync": sync}


def clear_box(project_root) -> int:
    conn = get_db(project_root)
    n = conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]
    if n:
        _write_snapshot(conn, "clear_box")   # T-M1-03:可撤销
    conn.execute("DELETE FROM mappings")
    conn.commit()
    sync = _notify_sync(project_root)
    return {"cleared": n, "sync": sync}


def auto_arrange(project_root) -> dict:
    """一键排布:按 更新时间倒序 把器件依次填入格位(需求 3 的“映射关系动态配置”)。"""
    cfg = get_config(project_root)
    conn = get_db(project_root)
    _write_snapshot(conn, "auto_arrange")    # T-M1-03:可撤销
    devices = conn.execute(
        "SELECT id FROM devices WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
        (cfg["cells"],)).fetchall()
    conn.execute("DELETE FROM mappings")
    for i, d in enumerate(devices):
        row, col = divmod(i, cfg["cols"])
        conn.execute("INSERT OR REPLACE INTO mappings (row, col, device_id) VALUES (?,?,?)",
                     (row, col, d["id"]))
    conn.commit()
    sync = _notify_sync(project_root)
    return {"assigned": len(devices), "cells": cfg["cells"], "sync": sync}
