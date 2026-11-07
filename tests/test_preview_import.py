"""C 编号一键加载(预览-编辑-确认)测试:mock 数据源,无真实网络。"""

import pytest

from pdl import security
from pdl.services import devices as device_service
from pdl.services import importer, prices


def _info(code="C25704", cycle="normal", stock=5000):
    return {"price": 0.0065, "currency": "USD", "model": "1210W3J0563T5E",
            "stock": stock, "cycle": cycle, "brand": "UNI-ROYAL",
            "package": "1210", "name": "UNI-ROYAL 1210W3J0563T5E",
            "category": "Chip Resistor", "description": "RES 56kohm 1210"}


class TestPreview:
    def test_valid_code_filled(self, root, monkeypatch):
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _info())
        r = importer.preview_codes(root, ["c25704"])
        assert r["invalid"] == []
        it = r["items"][0]
        assert it["code"] == "C25704" and it["status"] == "new"
        assert it["name"] == "UNI-ROYAL 1210W3J0563T5E"  # 商品名称=品牌+型号
        assert it["model"] == "1210W3J0563T5E" and it["package"] == "1210"
        assert it["manufacturer"] == "UNI-ROYAL"
        assert it["price"] == 0.0065
        assert it["qty"] == 1 and it["mall_stock"] == 5000  # 默认 1,商城现货仅参考

    def test_invalid_and_dedupe(self, root, monkeypatch):
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _info())
        r = importer.preview_codes(root, ["C25704", "C25704", "bad-code", "", "X999"])
        assert [i["input"] for i in r["invalid"]] == ["bad-code", "", "X999"]
        # 重复编号去重:两个 C25704 只保留一个有效项
        assert [i["code"] for i in r["items"]] == ["C25704"]

    def test_not_found(self, root, monkeypatch):
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: None)
        r = importer.preview_codes(root, ["C99999999"])
        assert r["items"][0]["status"] == "not_found"
        assert "不存在" in r["items"][0]["reason"]

    def test_exists_marked(self, root, monkeypatch):
        device_service.create_device(root, {"name": "已有电阻", "lcsc_code": "C25704",
                                            "qty": 10})
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _info())
        r = importer.preview_codes(root, ["C25704"])
        it = r["items"][0]
        assert it["status"] == "exists" and it["exists_qty"] == 10

    def test_discontinued_flagged(self, root, monkeypatch):
        monkeypatch.setattr(prices, "fetch_lcsc_detail",
                            lambda code: _info(cycle="sold_out", stock=0))
        r = importer.preview_codes(root, ["C25704"])
        it = r["items"][0]
        assert it["cycle"] == "sold_out" and it["qty"] == 1   # 无库存时默认 1

    def test_too_many_rejected(self, root):
        with pytest.raises(security.ValidationError):
            importer.preview_codes(root, [f"C{i}" for i in range(51)])


class TestSavePreview:
    def _item(self, code="C25704", qty=100, **kw):
        base = {"code": code, "status": "new", "cycle": "normal",
                "name": "RES 56kohm 1210", "model": "1210W3J0563T5E",
                "package": "1210", "manufacturer": "UNI-ROYAL",
                "category": "Chip Resistor", "description": "RES",
                "price": 0.0065, "price_currency": "USD", "qty": qty}
        base.update(kw)
        return base

    def test_create_new(self, root):
        r = importer.save_preview(root, [self._item()])
        assert r["created"] == 1 and r["merged"] == 0
        d = device_service.list_devices(root, q="C25704")["items"][0]
        assert d["name"] == "RES 56kohm 1210" and d["qty"] == 100
        assert d["price"] == 0.0065 and d["price_currency"] == "USD"
        assert d["price_status"] == "ok" and d["price_source"] == "lcsc"
        assert d["model"] == "1210W3J0563T5E"

    def test_merge_existing_only_qty_and_price(self, root):
        """exists 行:数量合并,价格刷新,人工字段(名称)不覆盖。"""
        device_service.create_device(root, {"name": "用户改名后的电阻",
                                            "lcsc_code": "C25704", "qty": 10})
        r = importer.save_preview(root, [self._item(qty=5)])
        assert r["merged"] == 1 and r["created"] == 0
        d = device_service.list_devices(root, q="C25704")["items"][0]
        assert d["qty"] == 15                          # 合并
        assert d["name"] == "用户改名后的电阻"          # 不覆盖
        assert d["price"] == 0.0065 and d["price_status"] == "ok"

    def test_discontinued_status_on_save(self, root):
        r = importer.save_preview(root, [self._item(cycle="sold_out")])
        assert r["created"] == 1
        d = device_service.list_devices(root)["items"][0]
        assert d["price_status"] == "discontinued"

    def test_user_edits_respected(self, root):
        """用户在预览中修改过的字段按修改值入库。"""
        item = self._item(qty=7)
        item["name"] = "我改过的名字"
        item["price"] = 0.02
        importer.save_preview(root, [item])
        d = device_service.list_devices(root)["items"][0]
        assert d["name"] == "我改过的名字" and d["price"] == 0.02 and d["qty"] == 7

    def test_invalid_row_reported(self, root):
        r = importer.save_preview(root, [self._item(), {"code": "nope", "qty": 1}])
        assert r["created"] == 1 and len(r["failed"]) == 1
        assert "格式" in r["failed"][0]["error"]

    def test_price_out_of_range_becomes_none(self, root):
        """客户端传入越界价格:不落库该值,器件仍创建(纵深校验)。"""
        r = importer.save_preview(root, [self._item(price=9e9)])
        assert r["created"] == 1
        d = device_service.list_devices(root)["items"][0]
        assert d["price"] is None and d["price_status"] == "no_match"

    def test_too_many_rejected(self, root):
        with pytest.raises(security.ValidationError):
            importer.save_preview(root, [self._item(code=f"C{i}") for i in range(201)])


class TestPreviewApi:
    def test_roundtrip(self, client, monkeypatch):
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _info())
        resp = client.post("/api/import/preview",
                           json={"codes": ["C25704", "bad"]})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert len(data["items"]) == 1 and len(data["invalid"]) == 1
        item = data["items"][0]
        # 模拟用户编辑:改名 + 改数量后保存
        item["name"] = "编辑后的名称"
        item["qty"] = 3
        resp = client.post("/api/import/save-preview", json={"items": [item]})
        assert resp.status_code == 200
        assert resp.get_json()["data"]["created"] == 1
        dev = client.get("/api/devices?q=C25704").get_json()["data"]["items"][0]
        assert dev["name"] == "编辑后的名称" and dev["qty"] == 3

    def test_preview_empty_body(self, client):
        assert client.post("/api/import/preview",
                           json={"codes": []}).status_code == 400
        assert client.post("/api/import/save-preview",
                           json={"items": []}).status_code == 400
