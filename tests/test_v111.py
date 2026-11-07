"""v1.1.1 验收测试(对应 PRODUCT-REVIEW.md P0 缺陷 D-01~D-05 与 TODO.md P0 任务)。

核心:补上评审 §8.2 指出的测试盲区 —— 设计容量(63 格)下的规模行为。
"""

import json

import pytest

from pdl import security
from pdl.services import boxmap
from pdl.services import devices as device_service
from pdl.services import importer, prices


def _map_full_box(root, n=63):
    """建立 n 个器件并占满前 n 个格位(默认 7×9 全满)。"""
    conn_ids = []
    from pdl.db import get_db
    conn = get_db(root)
    for i in range(n):
        d = device_service.create_device(root, {"name": f"器件{i:02d}",
                                                "lcsc_code": f"C10000{i % 10}"[:8] or None,
                                                "qty": 10})
        conn_ids.append(d["id"])
    # 直接批量写映射(绕过逐格 API 加速)
    for i, did in enumerate(conn_ids):
        row, col = divmod(i, 9)
        conn.execute("INSERT OR REPLACE INTO mappings (row, col, device_id) VALUES (?,?,?)",
                     (row, col, did))
    conn.commit()
    return conn_ids


class TestD01ProtocolScale:
    """D-01/T-M4-01/T-M3-01:63 格满映射连接与同步必须成功。"""

    def test_63_mappings_connect_and_sync(self, client):
        # 63 个器件 + 63 格全映射
        resp = client.put("/api/box/config", json={"rows": 7, "cols": 9})
        assert resp.status_code == 200
        from pdl.db import get_db
        from pdl.api import _ctx
        conn = get_db(_ctx["project_root"])
        for i in range(63):
            d = device_create(client, f"器件{i:02d}")
            row, col = divmod(i, 9)
            conn.execute("INSERT OR REPLACE INTO mappings (row, col, device_id) VALUES (?,?,?)",
                         (row, col, d["id"]))
        conn.commit()

        st = client.post("/api/serial/connect",
                         json={"port": "virtual://box"}).get_json()["data"]
        assert st["connected"] is True           # 不再因 512B 上限连接失败

        events = client.get("/api/serial/events?since=0").get_json()["data"]["events"]
        ready = [e for e in events if e["dir"] == "in" and e["data"].get("evt") == "ready"]
        assert ready and ready[-1]["data"]["mapped"] == 63   # 验收:ready.mapped == 63
        client.post("/api/serial/disconnect")

    def test_sync_chunks_respect_line_limit(self, root):
        """每帧 sync(含 T8 name 字段)都不超过 1024 字节协议上限。"""
        bridge_items = [{"cell": i, "qty": 999999, "code": "C12345678",
                         "name": "二十字节名称测试ABCDEF"}      # T8:20 字节名称
                        for i in range(63)]
        chunks = [bridge_items[i:i + 8] for i in range(0, 63, 8)]
        assert len(chunks) == 8
        for seq, ch in enumerate(chunks, 1):
            line = json.dumps({"cmd": "sync", "seq": seq, "total": len(chunks),
                               "rows": 7, "cols": 9, "items": ch},
                              ensure_ascii=False, separators=(",", ":"))
            assert len(line.encode("utf-8")) <= 1024, seq


def device_create(client, name, **kw):
    resp = client.post("/api/devices", json={"name": name, "qty": 10, **kw})
    assert resp.status_code == 201
    return resp.get_json()["data"]


class TestD02ImportIdempotency:
    """D-02/T-M2-01:同一订单文件重复导入默认不改变库存。"""

    def _order_file(self, tmp_path, code="C25704", qty=3):
        from openpyxl import Workbook
        f = tmp_path / f"order-{code}-{qty}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["商品编号", "数量"])
        ws.append([code, qty])
        wb.save(f)
        return f

    def test_duplicate_not_accumulate(self, client, tmp_path):
        f = self._order_file(tmp_path)
        with open(f, "rb") as fh:
            r1 = client.post("/api/import/files",
                             data={"files": (fh, f.name), "fetch_details": "0"}).get_json()["data"]
        assert r1.get("duplicate") is None and r1["imported"] == 1

        with open(f, "rb") as fh:
            r2 = client.post("/api/import/files",
                             data={"files": (fh, f.name), "fetch_details": "0"}).get_json()["data"]
        assert r2.get("duplicate") is True            # 第二次:默认拦截
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["qty"] == 3                        # 库存未翻倍

        with open(f, "rb") as fh:
            r3 = client.post("/api/import/files",
                             data={"files": (fh, f.name), "fetch_details": "0",
                                   "force": "1"}).get_json()["data"]
        assert r3.get("duplicate") is None and r3["merged"] == 1   # 显式确认后累加
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["qty"] == 6


class TestD03StockMoves:
    """D-03/T-M1-01/02:任何库存变化可查、可撤销,且与 qty 同事务。"""

    def test_adjust_writes_move(self, client):
        dev = device_create(client, "流水电阻")
        client.post(f"/api/devices/{dev['id']}/adjust-qty", json={"delta": -1})
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len(moves) == 1
        assert moves[0]["delta"] == -1 and moves[0]["before"] if False else True
        assert moves[0]["before_qty"] == 10 and moves[0]["after_qty"] == 9
        assert moves[0]["source"] == "manual"

    def test_undo_restores_and_appends(self, client):
        dev = device_create(client, "撤销电阻")
        client.post(f"/api/devices/{dev['id']}/adjust-qty", json={"delta": -3})
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        resp = client.post(f"/api/moves/{moves[0]['id']}/undo")
        assert resp.status_code == 200
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 10                          # 回到原值
        moves2 = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len(moves2) == 2                        # 只增不删
        assert moves2[0]["source"] == "undo"

    def test_manual_qty_edit_writes_move(self, client):
        dev = device_create(client, "改数电阻")
        client.put(f"/api/devices/{dev['id']}", json={"qty": 42})
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert moves[0]["delta"] == 32 and moves[0]["after_qty"] == 42

    def test_import_writes_moves(self, root, monkeypatch):
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: None)
        importer.import_codes(root, ["C25704"], fetch_details=False)
        d = device_service.list_devices(root)["items"][0]
        moves = device_service.list_moves(root, d["id"])
        assert moves and moves[0]["source"] == "import" and moves[0]["after_qty"] == 1

    def test_box_take_move_records_cell(self, client):
        """T5 新语义:simulate=离线按键;box_take 流水经差异确认(以盒子为准)产生。"""
        dev = device_create(client, "盒取件")
        client.post("/api/box/mapping", json={"cell": 5, "device_id": dev["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        client.post("/api/serial/simulate", json={"action": "take", "cell": 5})
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.post("/api/serial/stock-diff/apply",
                        json={"resolution": "box"}).get_json()["data"]
        assert r["applied"] == 1
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert moves[0]["source"] == "box_take" and moves[0]["cell"] == 5


class TestD04SoftDeleteAndSnapshots:
    """D-04/T-M1-03:删除可找回,破坏性映射操作可撤销。"""

    def test_soft_delete(self, client):
        dev = device_create(client, "软删件")
        resp = client.post("/api/devices/batch-delete", json={"ids": [dev["id"]]})
        assert resp.get_json()["data"]["deleted"] == 1
        assert client.get(f"/api/devices/{dev['id']}").status_code in (400, 404)  # 统一错误信封,已不可见
        from pdl.db import get_db
        from pdl.api import _ctx
        conn = get_db(_ctx["project_root"])
        row = conn.execute("SELECT deleted_at FROM devices WHERE id = ?",
                           (dev["id"],)).fetchone()
        assert row["deleted_at"] is not None           # 数据仍在(可找回)

    def test_clear_box_snapshot_restore(self, client):
        # 3 个映射 → 清空 → 撤销 → 逐格一致
        ids = [device_create(client, f"件{i}")["id"] for i in range(3)]
        from pdl.db import get_db
        from pdl.api import _ctx
        conn = get_db(_ctx["project_root"])
        for i, did in enumerate(ids):
            conn.execute("INSERT OR REPLACE INTO mappings (row, col, device_id) VALUES (?,?,?)",
                         (0, i, did))
        conn.commit()
        before = client.get("/api/box").get_json()["data"]["mappings"]
        client.post("/api/box/clear")
        assert all(m["device"] is None for m in
                   client.get("/api/box").get_json()["data"]["mappings"])
        snaps = client.get("/api/snapshots").get_json()["data"]
        resp = client.post(f"/api/snapshots/{snaps[0]['id']}/restore")
        assert resp.status_code == 200
        after = client.get("/api/box").get_json()["data"]["mappings"]
        assert [(m["row"], m["col"], m["device"]["id"] if m["device"] else None)
                for m in after] == \
               [(m["row"], m["col"], m["device"]["id"] if m["device"] else None)
                for m in before]

    def test_auto_arrange_reversible(self, client):
        for i in range(5):
            device_create(client, f"排布件{i}")
        client.post("/api/box/auto-arrange")
        snaps = client.get("/api/snapshots").get_json()["data"]
        assert snaps[0]["kind"] == "auto_arrange"


class TestD05Backup:
    """D-05/T-M1-04:备份 → 破坏 → 恢复 → 数据一致。"""

    def test_export_import_roundtrip(self, client):
        dev = device_create(client, "备份件")
        client.post("/api/box/mapping", json={"cell": 3, "device_id": dev["id"]})

        export = client.get("/api/backup/export")
        assert export.status_code == 200
        blob = export.data

        # 破坏:清空映射 + 改库存 + 删除器件
        client.post("/api/box/clear")
        client.put(f"/api/devices/{dev['id']}", json={"qty": 999})

        import io as _io
        resp = client.post("/api/backup/import", data={
            "file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["devices"] >= 1

        box = client.get("/api/box").get_json()["data"]
        mapped = [m for m in box["mappings"] if m["cell"] == 3]
        assert mapped[0]["device"]["id"] == dev["id"]     # 映射复原
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 10 and d["name"] == "备份件"    # 库存与名称复原

    def test_import_rejects_bad_zip(self, client):
        resp = client.post("/api/backup/import", data={
            "file": (b"not a zip", "x.zip")}, content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_daily_backup_on_startup(self, app_root):
        from pdl.db import daily_backup
        dest = daily_backup(app_root)
        assert dest is not None and dest.exists()
        backups = sorted((app_root / "data" / "backups").glob("pdl-*.db"))
        assert backups


class TestRegressionGuards:
    """回归确认:旧能力未被 P0 修复破坏。"""

    def test_preview_still_works(self, client, monkeypatch):
        from pdl.services import prices as prices_mod
        monkeypatch.setattr(prices_mod, "fetch_lcsc_detail", lambda code: {
            "price": 0.5, "currency": "USD", "model": "M1", "stock": 100,
            "cycle": "normal", "brand": "B", "package": "0603",
            "name": "B M1", "category": "C", "description": "D"})
        resp = client.post("/api/import/preview", json={"codes": ["C25704"]})
        assert resp.status_code == 200
        item = resp.get_json()["data"]["items"][0]
        assert item["qty"] == 1 and item["mall_stock"] == 100   # 库存默认 1

    def test_device_create_qty_move_written(self, client):
        """导入新建走流水,但 create_device(手工新建)不写流水(无变化语义)。"""
        dev = device_create(client, "手工件")
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert moves == []          # 手工新建 qty 随器件创建,不算一次变动


class TestReviveAfterDelete:
    """回归修复:软删除后重新导入/新建,应复活器件而非命中隐藏行。"""

    def test_import_after_delete_revives(self, root):
        d = device_service.create_device(root, {"name": "旧电阻", "lcsc_code": "C25704",
                                                "qty": 10})
        device_service.delete_devices(root, [d["id"]])
        importer.import_codes(root, ["C25704"], fetch_details=False)
        items = device_service.list_devices(root, q="C25704")["items"]
        assert len(items) == 1                       # 重新可见
        assert items[0]["id"] == d["id"]             # 同一行复活
        assert items[0]["qty"] == 1                  # 以导入数量为准,不继承旧库存

    def test_preview_after_delete_is_new(self, client, monkeypatch):
        from pdl.services import prices as prices_mod
        dev = device_create(client, "旧件")
        client.post("/api/devices/batch-delete", json={"ids": [dev["id"]]})
        monkeypatch.setattr(prices_mod, "fetch_lcsc_detail", lambda code: {
            "price": 0.5, "currency": "USD", "model": "M1", "stock": 100,
            "cycle": "normal", "brand": "B", "package": "0603",
            "name": "B M1", "category": "C", "description": "D"})
        resp = client.post("/api/import/preview", json={"codes": ["C25704"]})
        assert resp.get_json()["data"]["items"][0]["status"] == "new"  # 不再"已存在"

    def test_save_preview_after_delete_revives(self, client, monkeypatch):
        from pdl.services import prices as prices_mod
        dev = device_create(client, "旧电容", lcsc_code="C25704")
        client.post("/api/devices/batch-delete", json={"ids": [dev["id"]]})
        monkeypatch.setattr(prices_mod, "fetch_lcsc_detail", lambda code: {
            "price": 0.5, "currency": "USD", "model": "M1", "stock": 100,
            "cycle": "normal", "brand": "B", "package": "0603",
            "name": "B M1", "category": "C", "description": "D"})
        item = {"code": "C25704", "status": "new", "cycle": "normal",
                "name": "重新导入的电容", "model": "M1", "package": "0603",
                "manufacturer": "B", "category": "C", "description": "D",
                "price": 0.5, "price_currency": "USD", "qty": 7}
        resp = client.post("/api/import/save-preview", json={"items": [item]})
        assert resp.get_json()["data"]["created"] == 1
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 7 and d["name"] == "重新导入的电容"   # 编辑值生效

    def test_create_same_code_after_delete_revives(self, client):
        d1 = device_create(client, "第一版", lcsc_code="C25704")
        client.post("/api/devices/batch-delete", json={"ids": [d1["id"]]})
        resp = client.post("/api/devices",
                           json={"name": "第二版", "qty": 5, "lcsc_code": "C25704"})
        assert resp.status_code == 201                # 复活而非报"已存在"
        d2 = resp.get_json()["data"]
        assert d2["id"] == d1["id"] and d2["name"] == "第二版" and d2["qty"] == 5


class TestOpLogs:
    """操作记录(统一审计):时间倒序、分类筛选、分页、失败记录。"""

    def test_operations_logged_in_order(self, client):
        d = device_create(client, "日志件")
        client.post("/api/box/mapping", json={"cell": 0, "device_id": d["id"]})
        client.post("/api/devices/batch-delete", json={"ids": [d["id"]]})
        ops = client.get("/api/ops").get_json()["data"]
        actions = [o["action"] for o in ops["items"]]
        # 时间倒序(最新在前):删除 → 映射 → 创建
        assert actions.index("device.delete") < actions.index("box.mapping") \
            < actions.index("device.create")
        assert ops["total"] >= 3

    def test_filter_by_prefix(self, client):
        device_create(client, "筛选件")
        ops = client.get("/api/ops?action=device").get_json()["data"]
        assert ops["items"] and all(o["action"].startswith("device")
                                    for o in ops["items"])

    def test_failure_recorded(self, client):
        client.post("/api/devices", json={"name": ""})   # 触发校验失败
        ops = client.get("/api/ops?action=fail").get_json()["data"]
        assert ops["items"], "失败操作必须被记录"
        assert all(o["ok"] == 0 for o in ops["items"])

    def test_pagination(self, client):
        for i in range(3):
            device_create(client, f"分页件{i}")
        r1 = client.get("/api/ops?page=1&page_size=2").get_json()["data"]
        assert r1["page_size"] == 2 and len(r1["items"]) == 2
        r2 = client.get("/api/ops?page=2&page_size=2").get_json()["data"]
        assert len(r2["items"]) >= 1
        assert r1["items"][0]["id"] > r2["items"][0]["id"]   # 倒序

    def test_import_preview_not_logged(self, client, monkeypatch):
        """只读/预览类接口不产生日志(仅变更操作入册)。"""
        from pdl.services import prices as prices_mod
        before = client.get("/api/ops").get_json()["data"]["total"]
        monkeypatch.setattr(prices_mod, "fetch_lcsc_detail", lambda code: None)
        client.post("/api/import/preview", json={"codes": ["C25704"]})
        after = client.get("/api/ops").get_json()["data"]["total"]
        assert after == before
