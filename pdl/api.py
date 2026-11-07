"""HTTP API 路由(全部 JSON,统一 {ok, data|error} 信封)。

安全约定:
- 所有输入先过 pdl.security 校验,非法输入一律 400
- 上传文件:扩展名白名单 + 大小上限 + 随机落盘文件名(防路径注入)
- 安全响应头(CSP 等)在应用工厂的 after_request 统一添加
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

from . import APP_VERSION
from . import security
from .db import db_path, log_op, schema_version, utc_now
from .paths import as_data
from .services import boxmap, codegen, devices as device_service, importer, prices
from .services.serial_bridge import BridgeError, SerialBridge

bp = Blueprint("api", __name__, url_prefix="/api")

# 运行期由 create_app 注入
_ctx: dict = {}


def _ok(data=None, status: int = 200) -> Response:
    return jsonify({"ok": True, "data": data}), status


def _fail(message: str, status: int = 400) -> Response:
    return jsonify({"ok": False, "error": str(message)}), status


def _body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise security.ValidationError("请求体必须是 JSON 对象")
    return data


def _root() -> Path:
    return _ctx["project_root"]


def _int_arg(name: str, default: int) -> int:
    raw = request.args.get(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise security.ValidationError(f"参数 {name} 必须是整数") from None


def _bridge() -> SerialBridge:
    return _ctx["bridge"]


def _op(action: str, target: str = "", detail: str = "", ok: bool = True) -> None:
    """写统一操作日志(时间/指令/对象/结果);日志失败绝不影响业务。"""
    try:
        log_op(_root(), action, target, detail, ok)
    except Exception:  # noqa: BLE001
        pass


def _op_fail(path: str, detail: str) -> None:
    """失败操作:由应用工厂的全局错误处理器调用。"""
    _op("fail", path, detail, ok=False)


# ---------------------------------------------------------------------------
# 系统
# ---------------------------------------------------------------------------
def _shutdown_token() -> str | None:
    return _ctx.get("shutdown_token")


@bp.get("/system/info")
def system_info():
    db_file = db_path(_root())
    return _ok({
        "app": "PersonalDeviceManagement",
        "version": APP_VERSION,
        "db_path": str(db_file),
        "db_size_kb": round(db_file.stat().st_size / 1024, 1) if db_file.exists() else 0,
        "schema_version": schema_version(_ctx["get_db"]()),
        "limits": {
            "max_rows": security.MAX_ROWS, "max_cols": security.MAX_COLS,
            "max_cells": security.MAX_CELLS, "max_name_len": security.MAX_NAME_LEN,
        },
    })


@bp.post("/system/heartbeat")
def system_heartbeat():
    """桌面外壳保活心跳:令牌匹配才有效,用于“所有页面都关闭则退出”。"""
    expected = _shutdown_token()
    token = request.headers.get("X-PDL-Token", "")
    if not expected or token != expected:
        return _fail("令牌无效", 403)
    _ctx["last_heartbeat"] = time.time()
    return _ok({"alive": True})


@bp.post("/system/shutdown")
def system_shutdown():
    """桌面外壳主动退出:需要启动器注入的随机令牌,防止任何网页恶意关闭/操控。"""
    expected = _shutdown_token()
    if not expected:
        return _fail("该运行模式不支持远程关闭", 403)
    token = request.headers.get("X-PDL-Token", "")
    if token != expected:
        return _fail("令牌无效", 403)
    evt = _ctx.get("shutdown_event")
    if evt is not None:
        evt.set()
    return _ok({"bye": True})


# ---------------------------------------------------------------------------
# 器件库
# ---------------------------------------------------------------------------
@bp.get("/devices")
def devices_list():
    return _ok(device_service.list_devices(
        _root(),
        q=request.args.get("q", ""),
        page=_int_arg("page", 1),
        page_size=_int_arg("page_size", 50),
        source=request.args.get("source", ""),
        sort=request.args.get("sort", "updated_desc"),
    ))


@bp.post("/devices")
def devices_create():
    d = device_service.create_device(_root(), _body())
    _op("device.create", d["name"], f"编号 {d['lcsc_code'] or '—'} · 库存 {d['qty']}")
    return _ok(d, 201)


@bp.put("/devices/<int:device_id>")
def devices_update(device_id: int):
    d = device_service.update_device(_root(), device_id, _body())
    _op("device.update", d["name"], "编辑器件信息")
    return _ok(d)


@bp.get("/devices/<int:device_id>")
def devices_get(device_id: int):
    return _ok(device_service.get_device(_root(), device_id))


@bp.post("/devices/batch-delete")
def devices_batch_delete():
    n = device_service.delete_devices(_root(), _body().get("ids"))
    _op("device.delete", f"{n} 件", "软删除(数据保留,可经备份找回)")
    return _ok({"deleted": n})


@bp.post("/devices/<int:device_id>/adjust-qty")
def devices_adjust(device_id: int):
    body = _body()
    delta = security.validate_int(body.get("delta", 0), field="变动量",
                                  min_val=-1000, max_val=1000)
    d = device_service.adjust_qty(_root(), device_id, delta, source="manual")
    _op("device.adjust", d["name"], f"{delta:+d} → 结余 {d['qty']}")
    return _ok(d)


# ---------------------------------------------------------------------------
# 库存流水与撤销(T-M1-01/02)
# ---------------------------------------------------------------------------
@bp.get("/devices/<int:device_id>/moves")
def moves_list(device_id: int):
    limit = _int_arg("limit", 50)
    return _ok(device_service.list_moves(_root(), device_id, limit=limit))


@bp.post("/moves/<int:move_id>/undo")
def moves_undo(move_id: int):
    r = device_service.undo_move(_root(), move_id)
    _op("stock.undo", r["device"]["name"],
        f"撤销流水#{move_id} → 结余 {r['device']['qty']}")
    return _ok(r)


# ---------------------------------------------------------------------------
# 映射快照与恢复(T-M1-03)
# ---------------------------------------------------------------------------
@bp.get("/snapshots")
def snapshots_list():
    limit = _int_arg("limit", 10)
    rows = _ctx["get_db"]().execute(
        "SELECT id, kind, created_at FROM snapshots ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    return _ok([dict(r) for r in rows])


@bp.post("/snapshots/<int:snapshot_id>/restore")
def snapshots_restore(snapshot_id: int):
    r = boxmap.restore_snapshot(_root(), snapshot_id)
    _op("box.snapshot-restore", f"快照#{snapshot_id}",
        f"恢复 {r['restored']} 个格位映射")
    return _ok(r)


# ---------------------------------------------------------------------------
# 备份与恢复(T-M1-04)
# ---------------------------------------------------------------------------
@bp.get("/backup/export")
def backup_export():
    import io
    import zipfile
    from datetime import datetime, timezone

    from .db import SCHEMA_VERSION, backup_db, migrate_db_file, prune_backups

    root = _root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tmp_db = as_data(root) / "data" / "backups" / f"export-{stamp}.db"
    backup_db(root, tmp_db)
    conn = _ctx["get_db"]()
    counts = {
        "devices": conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0],
        "mappings": conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[0],
        "moves": conn.execute("SELECT COUNT(*) FROM stock_moves").fetchone()[0],
        "snapshots": conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
        "ops": conn.execute("SELECT COUNT(*) FROM op_logs").fetchone()[0],
    }
    manifest = {
        "app": "PersonalDeviceManagement",
        "version": APP_VERSION,            # 旧字段,兼容旧读取方
        "app_version": APP_VERSION,
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "counts": counts,
        # 旧字段(兼容 v1.1 备份读取方)
        "devices": counts["devices"], "mappings": counts["mappings"],
        "moves": counts["moves"],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp_db, "pdl.db")
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    tmp_db.unlink(missing_ok=True)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"pdl-backup-{stamp}.zip")


@bp.post("/backup/import")
def backup_import():
    import io
    import sqlite3
    import zipfile

    from .db import SCHEMA_VERSION, backup_db, migrate_db_file, prune_backups

    root = _root()
    fs = request.files.get("file")
    if not fs or not fs.filename:
        raise security.ValidationError("请选择备份 zip 文件")
    if not fs.filename.lower().endswith(".zip"):
        raise security.ValidationError("备份文件必须是 .zip")
    blob = fs.read()
    if len(blob) > 100 * 1024 * 1024:
        raise security.ValidationError("备份文件超过 100MB 上限")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise security.ValidationError("无效的 zip 文件") from exc
    names = zf.namelist()
    if "pdl.db" not in names or "manifest.json" not in names:
        raise security.ValidationError("备份缺少 pdl.db 或 manifest.json")
    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except ValueError as exc:
        raise security.ValidationError("manifest.json 损坏") from exc
    if manifest.get("app") != "PersonalDeviceManagement":
        raise security.ValidationError("这不是本工具的备份文件")
    # T1:缺 schema_version 的旧备份视为 1;高于当前版本 → 拒绝并提示升级
    from_schema = int(manifest.get("schema_version", 1) or 1)
    if from_schema > SCHEMA_VERSION:
        _op("backup.import", "schema 校验",
            f"备份来自 schema {from_schema}", ok=False)
        return _fail(f"备份来自更高版本(schema {from_schema}),请先升级程序", 400)
    counts = manifest.get("counts") or {}
    exp_devices = counts.get("devices", manifest.get("devices"))
    exp_mappings = counts.get("mappings", manifest.get("mappings"))

    backup_dir = as_data(root) / "data" / "backups"
    tmp = backup_dir / "restore-tmp.db"
    tmp.write_bytes(zf.read("pdl.db"))
    src = sqlite3.connect(str(tmp))
    try:
        step = "表结构检查"
        tables = {r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"devices", "mappings", "box_config"} <= tables:
            raise security.ValidationError("备份数据库结构不完整")
        step = "schema 迁移"
        from_v, to_v = migrate_db_file(tmp)
        step = "关键列检查"
        cols = {r[1] for r in src.execute("PRAGMA table_info(devices)")}
        if not {"name", "qty", "deleted_at"} <= cols:
            raise security.ValidationError("迁移后仍缺少关键列(deleted_at)")
        step = "计数校验"
        actual_devices = src.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        actual_mappings = src.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]
        if exp_devices is not None and actual_devices != exp_devices:
            raise security.ValidationError(
                f"计数校验失败:devices 实际 {actual_devices} 与清单 {exp_devices} 不符")
        if exp_mappings is not None and actual_mappings != exp_mappings:
            raise security.ValidationError(
                f"计数校验失败:mappings 实际 {actual_mappings} 与清单 {exp_mappings} 不符")
        step = "恢复前备份"
        safety = backup_db(root, backup_dir /
                           f"pre-restore-{utc_now().replace(':', '')}.db")
        prune_backups(root, "pre-restore-*.db", keep=3)
        step = "覆盖当前库"
        src.backup(_ctx["get_db"]())       # 原子覆盖(全部校验通过后才执行)
    except security.ValidationError:
        src.close()
        tmp.unlink(missing_ok=True)
        _op("backup.import", step, "恢复失败,当前库未被改动", ok=False)
        raise
    except sqlite3.Error as exc:
        src.close()
        tmp.unlink(missing_ok=True)
        _op("backup.import", step, f"恢复失败:{exc}", ok=False)
        raise security.ValidationError(f"{step}失败:{exc}") from None
    src.close()
    tmp.unlink(missing_ok=True)
    # 覆盖后探针:确认恢复后的库真正可用
    devices_n = _ctx["get_db"]().execute(
        "SELECT COUNT(*) FROM devices WHERE deleted_at IS NULL").fetchone()[0]
    _op("backup.import", f"在库 {devices_n} 件",
        f"恢复完成,schema {from_v} → {to_v}")
    return _ok({"restored": True, "from_schema": from_v, "to_schema": to_v,
                "migrated": from_v != to_v, "devices": devices_n,
                "safety_backup": str(safety)})


# ---------------------------------------------------------------------------
# 导入 / 导出
# ---------------------------------------------------------------------------
def _save_uploads(files) -> list[dict]:
    """保存上传文件到数据目录 uploads/(随机名),返回 [{path, filename}]。"""
    upload_dir = as_data(_root()) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for fs in files:
        if not fs or not fs.filename:
            continue
        suffix = Path(fs.filename).suffix.lower()
        if suffix not in importer.ALLOWED_EXTENSIONS:
            raise security.ValidationError(
                f"不支持的文件类型:{fs.filename}(允许 {'/'.join(sorted(importer.ALLOWED_EXTENSIONS))})")
        blob = fs.read()
        if len(blob) > importer.MAX_FILE_SIZE:
            raise security.ValidationError(f"{fs.filename} 超过 20MB 上限")
        if not blob:
            raise security.ValidationError(f"{fs.filename} 是空文件")
        dest = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        dest.write_bytes(blob)
        # 显示名仅保留清理后的短文本用于日志
        display = security.sanitize_text(Path(fs.filename).name, field="文件名", max_len=120) or "upload"
        saved.append({"path": str(dest), "filename": display})
    if not saved:
        raise security.ValidationError("没有收到有效文件")
    return saved


def _is_full_field_csv(path: str) -> bool:
    """判断是否为本工具导出的全字段 CSV。"""
    try:
        with open(path, "rb") as f:
            first = f.readline().decode("utf-8", "ignore").lower()
        return "lcsc_code" in first and ("name" in first.split(",") or ",name" in first)
    except OSError:
        return False


@bp.post("/import/files")
def import_files():
    saved = _save_uploads(request.files.getlist("files"))
    fetch = request.form.get("fetch_details", "0") in ("1", "true", "yes")
    force = request.form.get("force", "0") in ("1", "true", "yes")
    order_files = [s for s in saved if not (s["path"].lower().endswith(".csv")
                                            and _is_full_field_csv(s["path"]))]
    csv_files = [s for s in saved if s not in order_files]
    total = {"imported": 0, "merged": 0, "skipped": 0, "errors": []}
    duplicates: list[dict] = []
    if order_files:
        stats = importer.import_files(_root(), order_files, fetch_details=fetch,
                                      force=force)
        if stats.get("duplicate"):
            duplicates.extend(stats.get("previous", []))   # T3:汇总后再确认
        else:
            for k in ("imported", "merged", "skipped"):
                total[k] += stats.get(k, 0)
            total["errors"].extend(stats.get("errors", []))
    if csv_files:
        stats = importer.import_csv(_root(), csv_files, force=force)
        if stats.get("duplicate"):
            duplicates.extend(stats.get("previous", []))
        else:
            for k in ("imported", "merged", "skipped"):
                total[k] += stats.get(k, 0)
            total["errors"].extend(stats.get("errors", []))
    if duplicates and not force:
        # T3:任一分支判重 → 整批交由前端二次确认(未执行的分支不会写库)
        return _ok({"duplicate": True, "previous": duplicates,
                    "last": duplicates[0] if duplicates else None,
                    "imported": 0, "merged": 0, "skipped": 0,
                    "errors": total["errors"]})
    if total["imported"] + total["merged"] == 0 and not total["errors"]:
        raise security.ValidationError("所有文件都没有识别到有效数据")
    _op("import.files", ", ".join(s["filename"] for s in saved)[:100],
        f"新增 {total['imported']} / 合并 {total['merged']}"
        + (f" · {len(total['errors'])} 个警告" if total["errors"] else ""))
    return _ok(total)


@bp.post("/import/codes")
def import_codes():
    body = _body()
    r = importer.import_codes(
        _root(), body.get("codes"),
        fetch_details=security.validate_bool(body.get("fetch_details", True),
                                             field="fetch_details"))
    _op("import.codes", f"{len(body.get('codes') or [])} 个编号",
        f"新增 {r['imported']} / 合并 {r['merged']}")
    return _ok(r)


@bp.post("/import/preview")
def import_preview():
    """C 编号一键加载·阶段一:校验 + 按接口补全属性,返回可编辑预览。"""
    body = _body()
    return _ok(importer.preview_codes(_root(), body.get("codes")))


@bp.post("/import/save-preview")
def import_save_preview():
    """C 编号一键加载·阶段二:保存用户确认/编辑后的预览行。"""
    body = _body()
    r = importer.save_preview(_root(), body.get("items"),
                              force=security.validate_bool(
                                  body.get("force", False), field="force"))
    if r.get("duplicate"):
        _op("import.preview-save", "C 编号一键加载", "重复保存,默认拦截")
    else:
        _op("import.preview-save", "C 编号一键加载",
            f"新增 {r['created']} / 合并 {r['merged']} / 失败 {len(r['failed'])}")
    return _ok(r)


@bp.get("/import/logs")
def import_logs():
    rows = _ctx["get_db"]().execute(
        "SELECT * FROM import_logs ORDER BY id DESC LIMIT 50").fetchall()
    return _ok([dict(r) for r in rows])


@bp.get("/export/devices.csv")
def export_csv():
    text = importer.export_csv(_root())
    return Response("\ufeff" + text, mimetype="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=devices_export.csv"})


# ---------------------------------------------------------------------------
# 器件盒(需求 2、3)
# ---------------------------------------------------------------------------
@bp.get("/box")
def box_get():
    return _ok(boxmap.get_box(_root()))


@bp.put("/box/config")
def box_config():
    r = boxmap.update_config(_root(), _body())
    _op("box.config", f"{r['rows']}×{r['cols']}",
        "调整格栅尺寸"
        + (f",移除越界映射 {len(r['removed_mappings'])} 条" if r.get("removed_mappings") else ""))
    return _ok(r)


@bp.post("/box/mapping")
def box_set_mapping():
    body = _body()
    r = boxmap.set_mapping(_root(), body.get("cell"), body.get("device_id"))
    _op("box.mapping", f"格位 {body.get('cell')}",
        f"→ {r['device']['name']}" if r.get("device") else "解除映射")
    return _ok(r)


@bp.delete("/box/mapping/<int:cell>")
def box_clear_mapping(cell: int):
    r = boxmap.set_mapping(_root(), cell, None)
    _op("box.mapping", f"格位 {cell}", "解除映射")
    return _ok(r)


@bp.post("/box/clear")
def box_clear():
    n = boxmap.clear_box(_root())
    _op("box.clear", f"{n} 格", "清空映射(已生成快照,可撤销)")
    return _ok({"cleared": n})


@bp.post("/box/auto-arrange")
def box_auto_arrange():
    r = boxmap.auto_arrange(_root())
    _op("box.arrange", f"{r['assigned']} 件",
        f"一键排布(盒 {r['cells']} 格,已生成快照,可撤销)")
    return _ok(r)


# ---------------------------------------------------------------------------
# 代码生成(需求 4、5)
# ---------------------------------------------------------------------------
@bp.get("/codegen/preview")
def codegen_preview():
    return _ok(codegen.preview(_root()))


@bp.post("/codegen/generate")
def codegen_generate():
    body = _body()
    result = codegen.generate(_root(), body.get("output_dir", ""))
    _op("firmware.generate", result["output_dir"], f"生成 {result['count']} 个文件")
    return _ok(result)


# ---------------------------------------------------------------------------
# 价格更新(需求:按型号/C编号批量刷新,标记无匹配与已下架)
# ---------------------------------------------------------------------------
@bp.post("/prices/refresh")
def prices_refresh():
    body = _body()
    ids = body.get("ids")
    if ids is not None and not isinstance(ids, list):
        raise security.ValidationError("ids 必须是数组")
    scope = body.get("scope", "selected" if ids else "all")
    if not isinstance(scope, str):
        raise security.ValidationError("scope 必须是字符串")
    r = prices.refresh_prices(_root(), ids=ids,
                              scope=scope.lower() if scope else "all")
    _op("price.refresh", r["scope"],
        f"更新 {len(r['updated'])} / 无匹配 {len(r['no_match'])}"
        f" / 下架 {len(r['discontinued'])} / 失败 {len(r['failed'])}")
    return _ok(r)


# ---------------------------------------------------------------------------
# 操作记录(统一审计入口;顺带关闭 D-11 导入历史无界面)
# ---------------------------------------------------------------------------
@bp.get("/ops")
def ops_list():
    """操作记录:时间倒序 + 分页 + 按指令类别前缀筛选。"""
    page = _int_arg("page", 1)
    page_size = _int_arg("page_size", 50)
    action = (request.args.get("action", "") or "").strip()
    where, params = ["1=1"], []
    if action:
        where.append("(action LIKE ? OR action = ?)")
        params.extend([action + ".%", action])
    clause = " AND ".join(where)
    conn = _ctx["get_db"]()
    total = conn.execute(
        f"SELECT COUNT(*) FROM op_logs WHERE {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM op_logs WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, page_size, (page - 1) * page_size]).fetchall()
    return _ok({"total": total, "page": page, "page_size": page_size,
                "items": [dict(r) for r in rows]})


# ---------------------------------------------------------------------------
# 串口联动(需求 2)
# ---------------------------------------------------------------------------
@bp.get("/serial/ports")
def serial_ports():
    return _ok(SerialBridge.list_ports())


@bp.post("/serial/connect")
def serial_connect():
    body = _body()
    try:
        st = _bridge().connect(body.get("port", ""),
                               body.get("baud", 115200) if isinstance(
                                   body.get("baud", 115200), int) else 115200)
        _op("box.connect", st.get("port") or body.get("port", ""), "连接成功,映射已同步")
        return _ok(st)
    except BridgeError as exc:
        _op("box.connect", body.get("port", ""), f"连接失败:{exc}", ok=False)
        return _fail(str(exc), 409)


@bp.post("/serial/disconnect")
def serial_disconnect():
    st = _bridge().disconnect()
    _op("box.disconnect", st.get("port") or "", "断开器件盒")
    return _ok(st)


@bp.get("/serial/status")
def serial_status():
    return _ok(_bridge().status())


@bp.get("/serial/events")
def serial_events():
    since = request.args.get("since", "0")
    since = int(since) if since.isdigit() else 0
    return _ok({"events": _bridge().events_since(since),
                "status": _bridge().status()})


@bp.post("/serial/sync")
def serial_sync():
    if not _bridge().connected:
        return _fail("器件盒未连接", 409)
    return _ok({"sent": _bridge().push_sync()})


@bp.post("/serial/light")
def serial_light():
    body = _body()
    cell = security.validate_int(body.get("cell", -1), field="格位编号",
                                 min_val=0, max_val=security.MAX_CELLS)
    on = security.validate_bool(body.get("on", True), field="on")
    if not _bridge().connected:
        return _fail("器件盒未连接", 409)
    r = _bridge().light_cell(cell, on)
    _op("box.light", f"格位 {cell}", "点亮" if on else "熄灭")
    return _ok({"sent": r})


@bp.post("/serial/simulate")
def serial_simulate():
    body = _body()
    try:
        cell = body.get("cell")
        return _ok(_bridge().simulate(
            str(body.get("action", "")),
            security.validate_int(cell, field="格位编号", min_val=0,
                                  max_val=security.MAX_CELLS) if isinstance(cell, int) else None))
    except BridgeError as exc:
        return _fail(str(exc), 409)
