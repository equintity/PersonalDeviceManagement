"""API 集成测试:器件 CRUD、盒映射、串口联动(虚拟盒)、安全校验。"""

import json


def _create_device(client, **kw):
    body = {"name": "测试电容 100nF", "lcsc_code": "C25704", "qty": 10, **kw}
    resp = client.post("/api/devices", json=body)
    assert resp.status_code == 201
    return resp.get_json()["data"]


class TestSystem:
    def test_info(self, client):
        resp = client.get("/api/system/info")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["app"] == "PersonalDeviceManagement"
        assert data["limits"]["max_cells"] == 256

    def test_security_headers(self, client):
        resp = client.get("/api/system/info")
        assert "default-src 'self'" in resp.headers["Content-Security-Policy"]
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


class TestDevices:
    def test_crud_flow(self, client):
        dev = _create_device(client)
        resp = client.get("/api/devices?q=100nF")
        assert resp.get_json()["data"]["total"] == 1
        resp = client.put(f"/api/devices/{dev['id']}", json={"qty": 55, "notes": "A2-3"})
        assert resp.get_json()["data"]["qty"] == 55
        resp = client.post("/api/devices/batch-delete", json={"ids": [dev["id"]]})
        assert resp.get_json()["data"]["deleted"] == 1

    def test_validation_rejects_injection(self, client):
        for bad in {"name": "x\x00\x1b[31m"}, {"name": "ok", "lcsc_code": "C12;DROP"}, \
                   {"name": "ok", "qty": -5}, {"name": "a" * 100}:
            resp = client.post("/api/devices", json=bad)
            assert resp.status_code == 400, bad
            assert resp.get_json()["ok"] is False

    def test_duplicate_code_rejected(self, client):
        _create_device(client)
        resp = client.post("/api/devices", json={"name": "另一个", "lcsc_code": "C25704"})
        assert resp.status_code == 400

    def test_non_int_page_param(self, client):
        resp = client.get("/api/devices?page=abc")
        assert resp.status_code == 400


class TestBox:
    def test_default_7x9_and_mapping(self, client):
        dev = _create_device(client)
        resp = client.get("/api/box")
        data = resp.get_json()["data"]
        assert data["config"]["rows"] == 7 and data["config"]["cols"] == 9
        assert data["config"]["cells"] == 63
        resp = client.post("/api/box/mapping",
                           json={"cell": 10, "device_id": dev["id"]})
        assert resp.status_code == 200
        box = client.get("/api/box").get_json()["data"]
        mapped = [m for m in box["mappings"] if m["device"]]
        assert len(mapped) == 1 and mapped[0]["cell"] == 10

    def test_resize_keeps_and_prunes(self, client):
        dev = _create_device(client)
        # cell 30 = 第3行第3列(7x9),缩到 2x4 后越界应被移除
        client.post("/api/box/mapping", json={"cell": 30, "device_id": dev["id"]})
        resp = client.put("/api/box/config", json={"rows": 2, "cols": 4})
        data = resp.get_json()["data"]
        assert data["resized"] is True
        assert len(data["removed_mappings"]) == 1
        box = client.get("/api/box").get_json()["data"]
        assert all(m["device"] is None for m in box["mappings"])

    def test_resize_keeps_inbound_mapping(self, client):
        dev = _create_device(client)
        # cell 10 = 第1行第1列,2x4 内仍有效,应保留(格位号变为 1*4+1=5)
        client.post("/api/box/mapping", json={"cell": 10, "device_id": dev["id"]})
        client.put("/api/box/config", json={"rows": 2, "cols": 4})
        box = client.get("/api/box").get_json()["data"]
        mapped = [m for m in box["mappings"] if m["device"]]
        assert len(mapped) == 1 and mapped[0]["cell"] == 5

    def test_pin_conflict_rejected(self, client):
        resp = client.put("/api/box/config",
                          json={"led_pin": 48, "btn_ok": 48})
        assert resp.status_code == 400
        assert "冲突" in resp.get_json()["error"]

    def test_invalid_dims_rejected(self, client):
        assert client.put("/api/box/config", json={"rows": 0}).status_code == 400
        assert client.put("/api/box/config", json={"rows": 20, "cols": 20}).status_code == 400

    def test_auto_arrange(self, client):
        _create_device(client)
        _create_device(client, name="第二件", lcsc_code="C81588")
        resp = client.post("/api/box/auto-arrange")
        assert resp.get_json()["data"]["assigned"] == 2


class TestSerialVirtual:
    def test_ports_include_virtual(self, client):
        ports = client.get("/api/serial/ports").get_json()["data"]
        assert any(p["port"] == "virtual://box" for p in ports)

    def test_full_linkage_loop(self, client):
        """需求 2 联动闭环:连接 → 自动同步 → 模拟取料 → 软件库存扣减。"""
        dev = _create_device(client)
        client.post("/api/box/mapping", json={"cell": 5, "device_id": dev["id"]})
        st = client.post("/api/serial/connect",
                         json={"port": "virtual://box"}).get_json()["data"]
        assert st["connected"] is True and st["virtual"] is True

        # 模拟硬件按键取料 → 库存 -1
        client.post("/api/serial/simulate", json={"action": "take", "cell": 5})
        box = client.get("/api/box").get_json()["data"]
        mapped = next(m for m in box["mappings"] if m["cell"] == 5)
        assert mapped["device"]["qty"] == 9

        # 模拟退料 → 库存 +1
        client.post("/api/serial/simulate", json={"action": "return", "cell": 5})
        box = client.get("/api/box").get_json()["data"]
        assert next(m for m in box["mappings"] if m["cell"] == 5)["device"]["qty"] == 10

        events = client.get("/api/serial/events?since=0").get_json()["data"]["events"]
        kinds = [(e["dir"], e["data"].get("evt") or e["data"].get("cmd")) for e in events]
        assert ("out", "sync") in kinds           # 连接时自动推送了映射
        assert ("in", "take") in kinds
        client.post("/api/serial/disconnect")

    def test_light_requires_connection(self, client):
        resp = client.post("/api/serial/light", json={"cell": 0})
        assert resp.status_code == 409

    def test_light_on_virtual(self, client):
        client.post("/api/serial/connect", json={"port": "virtual://box"})
        resp = client.post("/api/serial/light", json={"cell": 7})
        assert resp.status_code == 200
        st = client.get("/api/serial/status").get_json()["data"]
        assert st["lit_cell"] == 7
        client.post("/api/serial/disconnect")


class TestCodegenApi:
    def test_generate_via_api(self, client, tmp_path):
        dev = _create_device(client)
        client.post("/api/box/mapping", json={"cell": 1, "device_id": dev["id"]})
        out = tmp_path.parent / "fw_api"   # 必须在项目根之外
        resp = client.post("/api/codegen/generate",
                           json={"output_dir": str(out)})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert (out / "include" / "box_config.h").exists()
        assert "src/main.cpp" in data["files"]

    def test_preview(self, client):
        resp = client.get("/api/codegen/preview")
        assert resp.status_code == 200
        assert "#define BOX_ROWS" in resp.get_json()["data"]["content"]


class TestImportApi:
    def test_codes_import_api(self, client):
        resp = client.post("/api/import/codes",
                           json={"codes": ["C25704"], "fetch_details": False})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["imported"] == 1
        logs = client.get("/api/import/logs").get_json()["data"]
        assert logs[0]["kind"] == "codes"

    def test_bad_body_rejected(self, client):
        resp = client.post("/api/import/codes", json={"codes": "C25704"})
        assert resp.status_code == 400
        resp = client.post("/api/import/codes", data="not json",
                           content_type="text/plain")
        assert resp.status_code == 400
