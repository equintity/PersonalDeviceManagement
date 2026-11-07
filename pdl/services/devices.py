"""器件库服务:增删改查、模糊搜索、分页。

安全:所有输入先经 pdl.security 校验;SQL 全部参数化。
"""

from __future__ import annotations

import json
import sqlite3

from .. import security
from ..db import get_db, row_to_device, utc_now

_DEVICE_FIELDS = ("category", "package", "manufacturer", "description", "notes")


def _validate_attrs(attrs) -> dict:
    """自定义属性:dict,键为 1~30 字符白名单文本,值为标量,最多 32 项。"""
    if attrs is None or attrs == "":
        return {}
    if not isinstance(attrs, dict):
        raise security.ValidationError("自定义属性必须是对象")
    if len(attrs) > 32:
        raise security.ValidationError("自定义属性数量不能超过 32 项")
    out = {}
    for k, v in attrs.items():
        key = security.validate_str(k, field="属性名", max_len=30)
        if not key:
            raise security.ValidationError("属性名不能为空")
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, (int, float)):
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                raise security.ValidationError(f"属性 {key} 数值非法")
            if abs(v) > 1e15:
                raise security.ValidationError(f"属性 {key} 数值过大")
            out[key] = v
        elif isinstance(v, str):
            out[key] = security.validate_str(v, field=f"属性 {key}", max_len=200)
        else:
            raise security.ValidationError(f"属性 {key} 只支持文本/数字/布尔值")
    return out


def _validate_price(price):
    if price is None or price == "":
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        raise security.ValidationError("单价必须是数字")
    price = float(price)
    if price < 0 or price > 1e7:
        raise security.ValidationError("单价超出合理范围")
    return round(price, 4)


def list_devices(
    project_root, *, q: str = "", page: int = 1, page_size: int = 50,
    source: str = "", sort: str = "updated_desc",
) -> dict:
    page = security.validate_int(page, field="页码", min_val=1, max_val=100_000)
    page_size = security.validate_int(page_size, field="每页数量", min_val=1, max_val=200)
    sort_map = {
        "updated_desc": "updated_at DESC, id DESC",
        "name_asc": "name COLLATE NOCASE ASC",
        "qty_desc": "qty DESC",
        "code_asc": "lcsc_code ASC",
    }
    order = sort_map.get(sort, sort_map["updated_desc"])

    where, params = ["deleted_at IS NULL"], []
    if q:
        q = security.sanitize_text(q, field="搜索词", max_len=80)
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append(
                "(name LIKE ? ESCAPE '\\' OR lcsc_code LIKE ? ESCAPE '\\' "
                "OR description LIKE ? ESCAPE '\\' OR package LIKE ? ESCAPE '\\' "
                "OR manufacturer LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 5)
    if source in ("lcsc", "manual"):
        where.append("source = ?")
        params.append(source)

    conn = get_db(project_root)
    total = conn.execute(
        f"SELECT COUNT(*) FROM devices WHERE {' AND '.join(where)}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM devices WHERE {' AND '.join(where)} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, page_size, (page - 1) * page_size],
    ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [row_to_device(r) for r in rows],
    }


def create_device(project_root, data: dict) -> dict:
    name = security.validate_device_name(data.get("name"))
    code = security.validate_lcsc_code(data.get("lcsc_code"))
    model = security.validate_model(data.get("model"))
    qty = security.validate_int(data.get("qty", 0), field="库存数量", min_val=0, max_val=security.MAX_QTY)
    attrs = _validate_attrs(data.get("attrs"))
    price = _validate_price(data.get("price"))
    fields = {f: security.validate_str(data.get(f), field=f, max_len=security.MAX_TEXT_LEN)
              for f in _DEVICE_FIELDS}
    now = utc_now()
    conn = get_db(project_root)
    try:
        cur = conn.execute(
            "INSERT INTO devices (lcsc_code, model, name, category, package, manufacturer,"
            " description, qty, price, notes, attrs, source, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, model, name, fields["category"], fields["package"], fields["manufacturer"],
             fields["description"], qty, price, fields["notes"],
             json.dumps(attrs, ensure_ascii=False), "manual", now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise security.ValidationError(f"创建失败:C编号已存在({exc})") from None
    return get_device(project_root, cur.lastrowid)


def update_device(project_root, device_id: int, data: dict) -> dict:
    device_id = security.validate_int(device_id, field="器件ID", min_val=1, max_val=2**31 - 1)
    conn = get_db(project_root)
    row = conn.execute("SELECT id FROM devices WHERE id = ?", (device_id,)).fetchone()
    if row is None:
        raise security.ValidationError(f"器件不存在:id={device_id}")

    updates, params = [], []
    old_qty = None
    if "name" in data:
        updates.append("name = ?")
        params.append(security.validate_device_name(data.get("name")))
    if "lcsc_code" in data:
        updates.append("lcsc_code = ?")
        params.append(security.validate_lcsc_code(data.get("lcsc_code")))
    if "model" in data:
        updates.append("model = ?")
        params.append(security.validate_model(data.get("model")))
    if "qty" in data:
        old_qty = conn.execute("SELECT qty FROM devices WHERE id = ?",
                               (device_id,)).fetchone()["qty"]
        updates.append("qty = ?")
        params.append(security.validate_int(data.get("qty", 0), field="库存数量",
                                            min_val=0, max_val=security.MAX_QTY))
    if "price" in data:
        updates.append("price = ?")
        params.append(_validate_price(data.get("price")))
    if "attrs" in data:
        updates.append("attrs = ?")
        params.append(json.dumps(_validate_attrs(data.get("attrs")), ensure_ascii=False))
    for f in _DEVICE_FIELDS:
        if f in data:
            updates.append(f"{f} = ?")
            params.append(security.validate_str(data.get(f), field=f,
                                                max_len=security.MAX_TEXT_LEN))
    if not updates:
        raise security.ValidationError("没有需要更新的字段")
    updates.append("updated_at = ?")
    params.append(utc_now())
    params.append(device_id)
    try:
        conn.execute(f"UPDATE devices SET {', '.join(updates)} WHERE id = ?", params)
        # T-M1-01:手工改数量与 qty 更新同事务写流水
        if old_qty is not None:
            new_qty = conn.execute("SELECT qty FROM devices WHERE id = ?",
                                   (device_id,)).fetchone()["qty"]
            delta = new_qty - old_qty
            if delta != 0:
                _write_move(conn, device_id, delta, old_qty, new_qty,
                            source="manual", note="手工修改数量")
        conn.commit()
    except sqlite3.IntegrityError:
        raise security.ValidationError("更新失败:C编号已存在") from None
    return get_device(project_root, device_id)


def get_device(project_root, device_id: int) -> dict:
    device_id = security.validate_int(device_id, field="器件ID", min_val=1, max_val=2**31 - 1)
    row = get_db(project_root).execute(
        "SELECT * FROM devices WHERE id = ? AND deleted_at IS NULL", (device_id,)
    ).fetchone()
    if row is None:
        raise security.ValidationError(f"器件不存在:id={device_id}")
    return row_to_device(row)


def delete_devices(project_root, ids: list[int]) -> int:
    """T-M1-03 软删除:标记 deleted_at 并解除关联映射,可找回、编号保留。"""
    ids = security.validate_id_list(ids, field="器件ID列表")
    conn = get_db(project_root)
    marks = ",".join("?" * len(ids))
    now = utc_now()
    cur = conn.execute(
        f"UPDATE devices SET deleted_at = ? WHERE id IN ({marks}) AND deleted_at IS NULL",
        (now, *ids))
    conn.execute(
        f"UPDATE mappings SET device_id = NULL WHERE device_id IN ({marks})", ids)
    conn.commit()
    return cur.rowcount


def list_moves(project_root, device_id: int, limit: int = 50) -> list[dict]:
    """T-M1-02:库存流水(时间倒序,只增不删)。"""
    device_id = security.validate_int(device_id, field="器件ID", min_val=1, max_val=2**31 - 1)
    limit = security.validate_int(limit, field="条数", min_val=1, max_val=200)
    rows = get_db(project_root).execute(
        "SELECT * FROM stock_moves WHERE device_id = ? ORDER BY id DESC LIMIT ?",
        (device_id, limit)).fetchall()
    return [dict(r) for r in rows]


def undo_move(project_root, move_id: int) -> dict:
    """T-M1-02:按反向 delta 撤销一条流水(写 undo 记录,不删除原记录)。"""
    move_id = security.validate_int(move_id, field="流水ID", min_val=1, max_val=2**31 - 1)
    conn = get_db(project_root)
    move = conn.execute("SELECT * FROM stock_moves WHERE id = ?", (move_id,)).fetchone()
    if move is None:
        raise security.ValidationError(f"流水不存在:id={move_id}")
    updated = adjust_qty(project_root, move["device_id"], -move["delta"],
                         source="undo", cell=move["cell"],
                         note=f"撤销流水#{move['id']}")
    return {"move_id": move_id, "device": updated}


def _write_move(conn, device_id: int, delta: int, before: int, after: int, *,
                source: str, ref: str = "", cell: int | None = None,
                note: str = "") -> None:
    """T-M1-01:任何库存变化必须与 qty 更新同事务写流水(只增不删)。"""
    conn.execute(
        "INSERT INTO stock_moves (device_id, delta, before_qty, after_qty,"
        " source, ref, cell, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (device_id, delta, before, after, source, ref, cell, note, utc_now()))


def adjust_qty(project_root, device_id: int, delta: int, *,
               source: str = "manual", cell: int | None = None,
               note: str = "", ref: str = "") -> dict:
    """取料/退料等库存增减:T-M1-01 要求与流水同事务写入。

    source ∈ {manual, box_take, box_return, import, adjust, undo}。
    结果量夹在 [0, MAX_QTY],流水记录实际发生的 before/after。
    """
    device_id = security.validate_int(device_id, field="器件ID", min_val=1, max_val=2**31 - 1)
    delta = security.validate_int(delta, field="变动量", min_val=-security.MAX_QTY,
                                  max_val=security.MAX_QTY)
    if source not in ("manual", "box_take", "box_return", "import", "adjust", "undo"):
        raise security.ValidationError(f"未知的流水来源:{source!r}")
    conn = get_db(project_root)
    row = conn.execute("SELECT qty FROM devices WHERE id = ? AND deleted_at IS NULL",
                       (device_id,)).fetchone()
    if row is None:
        raise security.ValidationError(f"器件不存在:id={device_id}")
    before = row["qty"]
    after = max(0, min(security.MAX_QTY, before + delta))
    actual = after - before
    if actual != delta:
        note = (note + ";") if note else ""
        note += f"目标 {delta} 因边界截断为 {actual}"
    conn.execute(
        "UPDATE devices SET qty = ?, updated_at = ? WHERE id = ?",
        (after, utc_now(), device_id))
    _write_move(conn, device_id, actual, before, after, source=source,
                ref=ref, cell=cell, note=note)
    conn.commit()
    return get_device(project_root, device_id)
