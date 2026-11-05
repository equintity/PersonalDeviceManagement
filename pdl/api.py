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

from . import security
from .db import db_path
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
        "version": codegen.APP_VERSION,
        "db_path": str(db_file),
        "db_size_kb": round(db_file.stat().st_size / 1024, 1) if db_file.exists() else 0,
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
    return _ok(device_service.create_device(_root(), _body()), 201)


@bp.put("/devices/<int:device_id>")
def devices_update(device_id: int):
    return _ok(device_service.update_device(_root(), device_id, _body()))


@bp.get("/devices/<int:device_id>")
def devices_get(device_id: int):
    return _ok(device_service.get_device(_root(), device_id))


@bp.post("/devices/batch-delete")
def devices_batch_delete():
    n = device_service.delete_devices(_root(), _body().get("ids"))
    return _ok({"deleted": n})


@bp.post("/devices/<int:device_id>/adjust-qty")
def devices_adjust(device_id: int):
    return _ok(device_service.adjust_qty(_root(), device_id, _body().get("delta", 0)))


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
    order_files = [s for s in saved if not (s["path"].lower().endswith(".csv")
                                            and _is_full_field_csv(s["path"]))]
    csv_files = [s for s in saved if s not in order_files]
    total = {"imported": 0, "merged": 0, "skipped": 0, "errors": []}
    if order_files:
        stats = importer.import_files(_root(), order_files, fetch_details=fetch)
        for k in ("imported", "merged", "skipped"):
            total[k] += stats.get(k, 0)
        total["errors"].extend(stats.get("errors", []))
    if csv_files:
        stats = importer.import_csv(_root(), csv_files)
        for k in ("imported", "merged", "skipped"):
            total[k] += stats.get(k, 0)
        total["errors"].extend(stats.get("errors", []))
    if total["imported"] + total["merged"] == 0 and not total["errors"]:
        raise security.ValidationError("所有文件都没有识别到有效数据")
    return _ok(total)


@bp.post("/import/codes")
def import_codes():
    body = _body()
    return _ok(importer.import_codes(
        _root(), body.get("codes"),
        fetch_details=security.validate_bool(body.get("fetch_details", True),
                                             field="fetch_details")))


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
    return _ok(boxmap.update_config(_root(), _body()))


@bp.post("/box/mapping")
def box_set_mapping():
    body = _body()
    return _ok(boxmap.set_mapping(_root(), body.get("cell"), body.get("device_id")))


@bp.delete("/box/mapping/<int:cell>")
def box_clear_mapping(cell: int):
    return _ok(boxmap.set_mapping(_root(), cell, None))


@bp.post("/box/clear")
def box_clear():
    return _ok({"cleared": boxmap.clear_box(_root())})


@bp.post("/box/auto-arrange")
def box_auto_arrange():
    return _ok(boxmap.auto_arrange(_root()))


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
    return _ok(prices.refresh_prices(_root(), ids=ids,
                                     scope=scope.lower() if scope else "all"))


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
        return _ok(_bridge().connect(body.get("port", ""),
                                     body.get("baud", 115200) if isinstance(
                                         body.get("baud", 115200), int) else 115200))
    except BridgeError as exc:
        return _fail(str(exc), 409)


@bp.post("/serial/disconnect")
def serial_disconnect():
    return _ok(_bridge().disconnect())


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
    return _ok({"sent": _bridge().light_cell(cell, on)})


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
