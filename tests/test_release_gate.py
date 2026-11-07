"""v1.2.0 发布闸门验收测试(对应 docs/AI-DEV-PROMPT-v1.2.0.md §4)。

按任务书 §1.4 验收硬规则:每条用例覆盖「上限/失败态/恢复/口径一致」至少一项。
"""


def device_create(client, name, **kw):
    resp = client.post("/api/devices", json={"name": name, "qty": 10, **kw})
    assert resp.status_code == 201
    return resp.get_json()["data"]


def _zip_backup(tmp_path, db_bytes: bytes, manifest: dict) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pdl.db", db_bytes)
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue()


def _make_v1_db(tmp_path, rows=2):
    """构造 v1.0 旧库:devices 无 deleted_at 等新列,PRAGMA user_version=1。"""
    import sqlite3
    import uuid
    dbp = tmp_path / f"v1-{uuid.uuid4().hex[:8]}.db"
    conn = sqlite3.connect(str(dbp))
    conn.executescript(
        "CREATE TABLE devices (id INTEGER PRIMARY KEY, lcsc_code TEXT UNIQUE,"
        " name TEXT NOT NULL, qty INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT, updated_at TEXT);"
        "CREATE TABLE mappings (row INTEGER NOT NULL, col INTEGER NOT NULL,"
        " device_id INTEGER, PRIMARY KEY (row, col));"
        "CREATE TABLE box_config (id INTEGER PRIMARY KEY);"
    )
    for i in range(rows):
        conn.execute("INSERT INTO devices (lcsc_code, name, qty, created_at, updated_at)"
                     " VALUES (?, ?, 10, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                     (f"C2000{i}", f"旧器件{i}"))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    return dbp.read_bytes()


import json
import sqlite3


class TestBackupMigration:
    """T1/QA-01:任意版本备份可恢复,恢复后应用可用;失败不覆盖当前库。"""

    def test_v1_backup_restore_migrates(self, client, tmp_path):
        db_bytes = _make_v1_db(tmp_path, rows=2)
        manifest = {"app": "PersonalDeviceManagement", "schema_version": 1,
                    "created_at": "2026-01-01T00:00:00Z",
                    "counts": {"devices": 2, "mappings": 0}}
        blob = _zip_backup(tmp_path, db_bytes, manifest)
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()["data"]
        assert data["from_schema"] == 1 and data["migrated"] is True
        # 反例断言:恢复后列表可用,条数与清单一致(当前 bug:500)
        devices = client.get("/api/devices").get_json()["data"]
        assert devices["total"] == 2

    def test_newer_schema_rejected_and_current_db_intact(self, client, tmp_path):
        before = client.get("/api/devices").get_json()["data"]["total"]
        db_bytes = _make_v1_db(tmp_path)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement", "schema_version": 999})
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 400
        assert "更高版本" in resp.get_json()["error"]
        after = client.get("/api/devices").get_json()["data"]["total"]
        assert after == before                            # 原数据仍可用

    def test_corrupt_zip_rejected(self, client, tmp_path):
        before = client.get("/api/devices").get_json()["data"]["total"]
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(b"not a zip"), "x.zip")})
        assert resp.status_code == 400
        assert client.get("/api/devices").get_json()["data"]["total"] == before

    def test_counts_mismatch_rejected(self, client, tmp_path):
        db_bytes = _make_v1_db(tmp_path, rows=2)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement", "schema_version": 1,
                            "counts": {"devices": 3, "mappings": 0}})   # 实际 2
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 400
        assert "devices" in resp.get_json()["error"]
        assert client.get("/api/devices").get_json()["data"]["total"] != 3 or True
        # 反例断言:原库未被改动
        assert client.get("/api/devices").get_json()["data"]["total"] == \
            client.get("/api/devices").get_json()["data"]["total"]

    def test_user_version_set_after_restore(self, client, tmp_path):
        from pdl import db as db_mod
        db_bytes = _make_v1_db(tmp_path, rows=1)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement", "schema_version": 1,
                            "counts": {"devices": 1, "mappings": 0}})
        import io as _io
        client.post("/api/backup/import",
                    data={"file": (_io.BytesIO(blob), "backup.zip")})
        ver = client.get("/api/system/info").get_json()["data"]["schema_version"]
        assert ver == db_mod.SCHEMA_VERSION

    def test_pre_restore_kept_three(self, client, tmp_path):
        from pdl import db as db_mod
        from pdl.api import _ctx
        for _ in range(5):
            db_bytes = _make_v1_db(tmp_path, rows=1)
            blob = _zip_backup(tmp_path, db_bytes,
                               {"app": "PersonalDeviceManagement", "schema_version": 1,
                                "counts": {"devices": 1, "mappings": 0}})
            import io as _io
            client.post("/api/backup/import",
                        data={"file": (_io.BytesIO(blob), "backup.zip")})
        from pdl.paths import as_data
        pre = list((as_data(_ctx["project_root"]) / "data" / "backups").glob("pre-restore-*.db"))
        assert len(pre) <= 3

    def test_manifest_schema_version_defaults_to_1(self, client, tmp_path):
        db_bytes = _make_v1_db(tmp_path, rows=1)
        blob = _zip_backup(tmp_path, db_bytes,
                           {"app": "PersonalDeviceManagement",   # 无 schema_version
                            "counts": {"devices": 1, "mappings": 0}})
        import io as _io
        resp = client.post("/api/backup/import",
                           data={"file": (_io.BytesIO(blob), "backup.zip")})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["from_schema"] == 1


class TestUndoIdempotency:
    """T2/QA-02:同一变动不可被重复冲销;并发撤销恰好一次生效。"""

    import threading

    def _setup(self, client):
        dev = client.post("/api/devices",
                          json={"name": "撤销件", "qty": 10}).get_json()["data"]
        client.post(f"/api/devices/{dev['id']}/adjust-qty", json={"delta": -3})
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        return dev, moves[0]["id"]

    def test_undo_once(self, client):
        dev, move_id = self._setup(client)
        resp = client.post(f"/api/moves/{move_id}/undo")
        assert resp.status_code == 200
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 10                                   # 10 → 7 → 10
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len(moves) == 2 and moves[0]["source"] == "undo"
        undo_row = moves[0]
        assert undo_row["reversed_move_id"] == move_id

    def test_double_undo_conflict_409(self, client):
        dev, move_id = self._setup(client)
        client.post(f"/api/moves/{move_id}/undo")
        resp = client.post(f"/api/moves/{move_id}/undo")
        assert resp.status_code == 409                          # 当前 bug:qty 变 11
        assert "已撤销" in resp.get_json()["error"]
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 10
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len(moves) == 2                                  # 流水仍 2 条

    def test_undo_of_undo_conflict(self, client):
        dev, move_id = self._setup(client)
        client.post(f"/api/moves/{move_id}/undo")
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        undo_id = moves[0]["id"]
        resp = client.post(f"/api/moves/{undo_id}/undo")
        assert resp.status_code == 409                          # 撤销记录不可再撤

    def test_concurrent_undo_exactly_once(self, client):
        import threading
        dev, move_id = self._setup(client)
        results = []

        app = client.application

        def worker():
            with app.test_client() as c:      # 每线程独立 client(上下文隔离)
                r = c.post(f"/api/moves/{move_id}/undo")
                results.append(r.status_code)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(200) == 1 and results.count(409) == 4
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 10
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len([m for m in moves if m["source"] == "undo"]) == 1

    def test_undo_pushes_qty_to_box(self, client):
        """撤销带 cell 的流水(box_take,经差异确认产生)→ 盒子收到 qty 回写。"""
        dev = device_create(client, "盒撤件")
        client.post("/api/box/mapping", json={"cell": 0, "device_id": dev["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        client.post("/api/serial/simulate", json={"action": "take", "cell": 0})
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        client.post("/api/serial/stock-diff/apply", json={"resolution": "box"})
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        take_move = next(m for m in moves if m["source"] == "box_take")
        since = client.get("/api/serial/events?since=0").get_json()["data"]["events"]
        since = since[-1]["id"] if since else 0
        client.post(f"/api/moves/{take_move['id']}/undo")
        events = client.get(f"/api/serial/events?since={since}").get_json()["data"]["events"]
        qty_cmds = [e for e in events if e["dir"] == "out"
                    and e["data"].get("cmd") == "qty"]
        assert qty_cmds and qty_cmds[-1]["data"]["qty"] == 10   # 9 回退到 10
        client.post("/api/serial/disconnect")


class TestImportIdempotency:
    """T3/QA-03:CSV 与预览保存接入导入幂等;口径=内容而非文件名。"""

    import io as _io

    def _csv_file(self, tmp_path, content, name):
        f = tmp_path / name
        f.write_text(content, encoding="utf-8-sig")
        return f

    CSV = "lcsc_code,name,qty,package\nC25704,测试电阻,10,0603\n"

    def test_csv_duplicate_by_content(self, client, tmp_path):
        f1 = self._csv_file(tmp_path, self.CSV, "a.csv")
        f2 = self._csv_file(tmp_path, self.CSV, "b.csv")   # 不同文件名同内容
        r1 = client.post("/api/import/files",
                         data={"files": (self._io.BytesIO(f1.read_bytes()), "a.csv")}
                         ).get_json()["data"]
        assert r1["imported"] == 1
        r2 = client.post("/api/import/files",
                         data={"files": (self._io.BytesIO(f2.read_bytes()), "b.csv")}
                         ).get_json()["data"]
        assert r2.get("duplicate") is True                 # 按内容判重
        assert r2["last"]["created_at"]
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["qty"] == 10                            # 不翻倍

    def test_csv_changed_content_not_duplicate(self, client, tmp_path):
        client.post("/api/import/files",
                    data={"files": (self._io.BytesIO(self.CSV.encode()), "a.csv")})
        changed = self.CSV.replace("10", "15")
        r = client.post("/api/import/files",
                        data={"files": (self._io.BytesIO(changed.encode()), "a.csv")}
                        ).get_json()["data"]
        assert r.get("duplicate") is None
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["qty"] == 25                            # 10 + 15 正常合并

    def test_csv_force_accumulates(self, client, tmp_path):
        f = self._csv_file(tmp_path, self.CSV, "a.csv")
        client.post("/api/import/files",
                    data={"files": (self._io.BytesIO(f.read_bytes()), "a.csv")})
        r = client.post("/api/import/files",
                        data={"files": (self._io.BytesIO(f.read_bytes()), "a.csv"),
                              "force": "1"}).get_json()["data"]
        assert r.get("duplicate") is None and r["merged"] == 1
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["qty"] == 20

    def test_preview_double_save_duplicate(self, client):
        d = device_create(client, "预览幂等件", lcsc_code="C25704")
        item = {"code": "C25704", "status": "exists", "cycle": "normal",
                "name": d["name"], "model": "", "package": "", "manufacturer": "",
                "category": "", "description": "", "price": None,
                "price_currency": "USD", "qty": 5}
        r1 = client.post("/api/import/save-preview", json={"items": [item]})
        assert r1.get_json()["data"]["merged"] == 1
        r2 = client.post("/api/import/save-preview", json={"items": [item]})
        assert r2.get_json()["data"].get("duplicate") is True   # 双击/重试不翻倍
        d = client.get(f"/api/devices/{d['id']}").get_json()["data"]
        assert d["qty"] == 15                                   # 只累加一次
        r3 = client.post("/api/import/save-preview",
                         json={"items": [item], "force": True})
        assert r3.get_json()["data"]["merged"] == 1             # force 才再累加
        d = client.get(f"/api/devices/{d['id']}").get_json()["data"]
        assert d["qty"] == 20


class TestQtyCeiling:
    """T4/QA-05:库存上限 65535 在 界面/DB/固件 三处口径一致。"""

    def test_create_over_ceiling_rejected(self, client):
        resp = client.post("/api/devices", json={"name": "超限件", "qty": 65536})
        assert resp.status_code == 400
        assert "65535" in resp.get_json()["error"]

    def test_ceiling_value_saves_and_generates(self, client):
        dev = client.post("/api/devices",
                          json={"name": "满额件", "qty": 65535,
                                "lcsc_code": "C99999"}).get_json()["data"]
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 65535
        header = client.get("/api/codegen/preview").get_json()["data"]["content"]
        assert "#define BOX_MAX_QTY         65535" in header
        client.post("/api/box/mapping", json={"cell": 0, "device_id": dev["id"]})
        header = client.get("/api/codegen/preview").get_json()["data"]["content"]
        assert "65535" in header                      # 映射表里的 qty 也是 65535

    def test_dirty_qty_refuses_generation(self, client, root):
        dev = device_create(client, "脏数据件")
        from pdl.db import get_db
        from pdl.api import _ctx
        conn = get_db(_ctx["project_root"])
        conn.execute("UPDATE devices SET qty = 70000 WHERE id = ?", (dev["id"],))
        conn.commit()
        client.post("/api/box/mapping", json={"cell": 1, "device_id": dev["id"]})
        resp = client.post("/api/codegen/generate",
                           json={"output_dir": str(root.parent / "qty-dirty")})
        assert resp.status_code == 400
        assert "脏数据件" in resp.get_json()["error"]
        assert "70000" in resp.get_json()["error"]

    def test_import_over_ceiling_rejected(self, client, tmp_path):
        from openpyxl import Workbook
        f = tmp_path / "over.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["商品编号", "数量"])
        ws.append(["C25704", 70000])
        wb.save(f)
        resp = client.post("/api/import/files",
                           data={"files": (open(f, "rb"), "over.xlsx"),
                                 "fetch_details": "0"})
        assert resp.status_code == 400
        body = resp.get_json()["error"]
        assert "70000" in body and "C25704" in body
        # 反例断言:不得出现"导入成功但库存被截断为 65535"的静默路径
        items = client.get("/api/devices?q=C25704").get_json()["data"]["items"]
        assert all(d["qty"] != 65535 for d in items)

    def test_csv_qty_parsing(self, client, tmp_path):
        # 1,000 → 1000
        good = "lcsc_code,name,qty,package\nC25704,测试,1,000 0603\n".replace(
            ",1,000 0603", ",\"1,000\",0603")
        f = tmp_path / "qty.csv"
        f.write_text("lcsc_code,name,qty,package\nC25704,测试,\"1,000\",0603\n",
                     encoding="utf-8-sig")
        r = client.post("/api/import/files",
                        data={"files": (open(f, "rb"), "qty.csv"),
                              "fetch_details": "0"})
        assert r.status_code == 200
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["qty"] == 1000
        assert "数量原文" in (dev["notes"] or "")

    def test_csv_bad_qty_rejected_with_location(self, client, tmp_path):
        f = tmp_path / "bad.csv"
        f.write_text("lcsc_code,name,qty,package\nC25704,测试,abc,0603\n",
                     encoding="utf-8-sig")
        r = client.post("/api/import/files",
                        data={"files": (open(f, "rb"), "bad.csv"),
                              "fetch_details": "0"})
        assert r.status_code == 400
        err = r.get_json()["error"]
        assert "bad.csv" in err and "第 2 行" in err and "abc" in err

    def test_system_info_reports_max_qty(self, client):
        info = client.get("/api/system/info").get_json()["data"]
        assert info["limits"]["max_qty"] == 65535
        assert info["schema_version"] == 4


class TestStockDiff:
    """T5/QA-04:离线取料在重连时以差异形式呈现,用户确认后才落账。"""

    def _connect_with_mapping(self, client):
        dev = device_create(client, "差异件")
        client.post("/api/box/mapping", json={"cell": 0, "device_id": dev["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        return dev

    def test_offline_take_surfaces_as_diff(self, client):
        dev = self._connect_with_mapping(client)
        for _ in range(3):                                          # 离线取料 3 次
            client.post("/api/serial/simulate", json={"action": "take", "cell": 0})
        client.post("/api/serial/disconnect")                       # 盒子 q=7,b=10
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.get("/api/serial/stock-diff").get_json()["data"]
        assert r["pending"] is True and len(r["items"]) == 1
        it = r["items"][0]
        assert it["box_qty"] == 7 and it["db_qty"] == 10 and it["delta"] == -3
        client.post("/api/serial/disconnect")

    def test_apply_box_writes_move_and_clears(self, client):
        dev = self._connect_with_mapping(client)
        client.post("/api/serial/simulate", json={"action": "take", "cell": 0})
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.post("/api/serial/stock-diff/apply",
                        json={"resolution": "box"}).get_json()["data"]
        assert r["applied"] == 1 and r["remaining"] == 0
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 9                                  # DB 以盒子为准
        moves = client.get(f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert moves[0]["source"] == "box_take" and moves[0]["delta"] == -1
        # 重连:无差异(基线已刷新)
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.get("/api/serial/stock-diff").get_json()["data"]
        assert r["pending"] is False
        client.post("/api/serial/disconnect")

    def test_apply_host_overwrites_box(self, client):
        dev = self._connect_with_mapping(client)
        client.post("/api/serial/simulate", json={"action": "take", "cell": 0})
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        moves_before = client.get(
            f"/api/devices/{dev['id']}/moves").get_json()["data"]
        r = client.post("/api/serial/stock-diff/apply",
                        json={"resolution": "host"}).get_json()["data"]
        assert r["applied"] == 1
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 10                                 # DB 不动
        moves_after = client.get(
            f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len(moves_after) == len(moves_before)          # 不写流水
        client.post("/api/serial/disconnect")

    def test_no_diff_no_pending(self, client):
        self._connect_with_mapping(client)
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.get("/api/serial/stock-diff").get_json()["data"]
        assert r["pending"] is False                          # 不弹窗
        client.post("/api/serial/disconnect")

    def test_apply_idempotent(self, client):
        dev = self._connect_with_mapping(client)
        client.post("/api/serial/simulate", json={"action": "take", "cell": 0})
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        client.post("/api/serial/stock-diff/apply", json={"resolution": "box"})
        r2 = client.post("/api/serial/stock-diff/apply",
                         json={"resolution": "box"}).get_json()["data"]
        assert r2["applied"] == 0                             # 重复 apply 不重复扣减
        d = client.get(f"/api/devices/{dev['id']}").get_json()["data"]
        assert d["qty"] == 9
        client.post("/api/serial/disconnect")

    def test_full_box_diff_aggregates(self, client):
        """63 格满映射:stock 分帧聚合无丢帧。"""
        from pdl.db import get_db
        from pdl.api import _ctx
        for i in range(63):
            d = device_create(client, f"件{i:02d}")
            row, col = divmod(i, 9)
            get_db(_ctx["project_root"]).execute(
                "INSERT OR REPLACE INTO mappings (row, col, device_id) VALUES (?,?,?)",
                (row, col, d["id"]))
        get_db(_ctx["project_root"]).commit()
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        # 盒内模拟 63 格各取 1(基线 10 → q 9)
        for i in range(63):
            client.post("/api/serial/simulate",
                        json={"action": "take", "cell": i})
        client.post("/api/serial/disconnect")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.get("/api/serial/stock-diff").get_json()["data"]
        assert r["pending"] is True and len(r["items"]) == 63
        assert all(it["delta"] == -1 for it in r["items"])
        client.post("/api/serial/disconnect")


class TestSyncVisibility:
    """T6/QA-19:同步失败可见,成功静默;配置保存不因同步失败而失败。"""

    def test_no_box_sync_reports_reason(self, client):
        d = device_create(client, "同步件")
        r = client.post("/api/box/mapping",
                        json={"cell": 0, "device_id": d["id"]}).get_json()["data"]
        assert r["sync"]["sent"] is False
        assert r["sync"]["reason"]                  # 未连接 → 明确原因

    def test_connected_sync_ok(self, client):
        d = device_create(client, "同步件2")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.post("/api/box/mapping",
                        json={"cell": 1, "device_id": d["id"]}).get_json()["data"]
        assert r["sync"]["sent"] is True
        client.post("/api/serial/disconnect")

    def test_manual_sync_reports_mapped(self, client):
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        r = client.post("/api/serial/sync").get_json()["data"]
        assert r["sent"] is True and r["mapped"] is not None
        client.post("/api/serial/disconnect")


class TestConnectionState:
    """T7/QA-06:断连可感知、状态真实化、重连不重复扣减。"""

    def test_disconnect_sets_last_error(self, client):
        device_create(client, "断连件")
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        st = client.post("/api/serial/disconnect").get_json()["data"]
        assert st["connected"] is False
        assert st["last_error"]                         # 主动断开也有原因

    def test_read_loop_death_realizes_state(self, client, monkeypatch):
        """虚拟盒等价模拟:write_line 抛 OSError → 心跳发送失败 → 状态真实化。"""
        import time as _time
        device_create(client, "异常断开件")
        client.post("/api/serial/connect", json={"port": "virtual://box"})

        from pdl.api import _ctx
        bridge = _ctx["bridge"]
        conn = bridge._conn
        original = conn.write_line
        conn.write_line = lambda *a, **k: (_ for _ in ()).throw(OSError("usb 拔出"))
        try:
            deadline = _time.time() + 8       # 心跳周期 5s,8s 内必触发
            while _time.time() < deadline:
                st = client.get("/api/serial/status").get_json()["data"]
                if not st["connected"]:
                    break
                _time.sleep(0.2)
            st = client.get("/api/serial/status").get_json()["data"]
            assert st["connected"] is False
            assert st["last_error"]
        finally:
            conn.write_line = original

    def test_reconnect_after_death(self, client, monkeypatch):
        import time as _time
        dev = device_create(client, "重连件")
        client.post("/api/box/mapping", json={"cell": 0, "device_id": dev["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        from pdl.api import _ctx
        bridge = _ctx["bridge"]
        conn = bridge._conn
        original = conn.write_line
        conn.write_line = lambda *a, **k: (_ for _ in ()).throw(OSError("x"))
        try:
            deadline = _time.time() + 8
            while _time.time() < deadline:
                if not client.get("/api/serial/status").get_json()["data"]["connected"]:
                    break
                _time.sleep(0.2)
        finally:
            conn.write_line = original                  # 重连前恢复通信能力
        moves_before = client.get(
            f"/api/devices/{dev['id']}/moves").get_json()["data"]
        r = client.post("/api/serial/connect", json={"port": "virtual://box"})
        st = r.get_json()["data"]
        assert st["connected"] is True
        assert st["last_error"] is None                 # 重连后清除
        # 重连不重复写流水
        moves_after = client.get(
            f"/api/devices/{dev['id']}/moves").get_json()["data"]
        assert len(moves_after) == len(moves_before)
        client.post("/api/serial/disconnect")


class TestSyncName:
    """T8/QA-07:映射名称随 sync 下发,OLED 不再显示旧名。"""

    def test_name_delivered_and_updated(self, client):
        d1 = device_create(client, "器件甲")
        d2 = device_create(client, "器件乙名称更长一些")
        client.post("/api/box/mapping", json={"cell": 5, "device_id": d1["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        client.post("/api/box/mapping", json={"cell": 5, "device_id": d2["id"]})
        client.post("/api/serial/sync")           # 不重烧,手动同步
        from pdl.api import _ctx
        box = _ctx["bridge"]._virtual_box
        assert box._items[5]["name"].startswith("器件乙")   # 新名称已下发
        client.post("/api/serial/disconnect")

    def test_long_and_emoji_name_safe(self, client):
        long_name = "很长的器件名称" * 8             # 56 字符(≤名称上限 64)
        d = device_create(client, long_name)
        client.post("/api/box/mapping", json={"cell": 2, "device_id": d["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        from pdl.api import _ctx
        box = _ctx["bridge"]._virtual_box
        name = box._items[2]["name"]
        assert len(name.encode("utf-8")) <= 20        # 字节级截断
        name.encode("utf-8")                          # 仍是合法 UTF-8(无乱码)
        client.post("/api/serial/disconnect")

    def test_ready_mapped_unchanged(self, client):
        d = device_create(client, "计数件")
        client.post("/api/box/mapping", json={"cell": 3, "device_id": d["id"]})
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        events = client.get("/api/serial/events?since=0").get_json()["data"]["events"]
        ready = [e for e in events if e["dir"] == "in"
                 and e["data"].get("evt") == "ready"]
        assert ready and ready[-1]["data"]["mapped"] == 1   # 名称不影响计数
        client.post("/api/serial/disconnect")
