"""价格更新服务测试:全部使用 mock 数据源,不做真实网络请求。"""

import pytest

from pdl import security
from pdl.services import devices as device_service
from pdl.services import prices


def _lcsc(price=0.0065, cycle="normal", stock=50670):
    return {"price": price, "currency": "USD", "model": "1210W3J0563T5E",
            "stock": stock, "cycle": cycle, "brand": "UNI-ROYAL", "package": "1210"}


def _jlc(model, price=1.4915, code="C8734", stock=100):
    return {"price": price, "currency": "USD", "model": model, "stock": stock,
            "code": code, "library": "expand"}


class TestRefreshByCode:
    def test_ok_updates_price_fields(self, root, monkeypatch):
        dev = device_service.create_device(root, {"name": "电阻", "lcsc_code": "C25704"})
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _lcsc())
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert r["updated"] and r["updated"][0]["price"] == 0.0065
        d = device_service.get_device(root, dev["id"])
        assert d["price_status"] == "ok"
        assert d["price_currency"] == "USD"
        assert d["price_source"] == "lcsc"
        assert d["price_updated_at"]

    def test_discontinued_marked(self, root, monkeypatch):
        dev = device_service.create_device(root, {"name": "旧料", "lcsc_code": "C7763"})
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _lcsc(cycle="sold_out", stock=0))
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert len(r["discontinued"]) == 1
        d = device_service.get_device(root, dev["id"])
        assert d["price_status"] == "discontinued"

    def test_network_failure_marked_error(self, root, monkeypatch):
        dev = device_service.create_device(root, {"name": "断网件", "lcsc_code": "C0001"})
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: None)
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert len(r["failed"]) == 1 and "网络" in r["failed"][0]["error"]
        assert device_service.get_device(root, dev["id"])["price_status"] == "error"

    def test_invalid_price_rejected(self, root, monkeypatch):
        """接口返回非法金额(越界)→ 不落库,标记 no_match。"""
        dev = device_service.create_device(root, {"name": "坏价", "lcsc_code": "C0002"})
        monkeypatch.setattr(prices, "fetch_lcsc_detail",
                            lambda code: _lcsc(price=9e9))
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert len(r["no_match"]) == 1
        d = device_service.get_device(root, dev["id"])
        assert d["price"] is None and d["price_status"] == "no_match"


class TestRefreshByModel:
    def test_exact_match_backfills_code(self, root, monkeypatch):
        dev = device_service.create_device(
            root, {"name": "STM32 主控", "model": "STM32F103C8T6"})
        monkeypatch.setattr(prices, "search_jlc_by_model",
                            lambda model: _jlc("STM32F103C8T6", code="C8734"))
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert r["updated"][0]["matched_code"] == "C8734"
        d = device_service.get_device(root, dev["id"])
        assert d["lcsc_code"] == "C8734"      # 自动回填,便于下次精确刷新
        assert d["price_source"] == "jlcpcb"

    def test_case_insensitive_match(self, root, monkeypatch):
        dev = device_service.create_device(root, {"name": "X", "model": "abc-123"})
        monkeypatch.setattr(prices, "search_jlc_by_model",
                            lambda model: _jlc("ABC-123", code="C1"))
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert r["updated"]

    def test_no_exact_match_marked(self, root, monkeypatch):
        dev = device_service.create_device(root, {"name": "冷门件", "model": "NOPE-404"})
        monkeypatch.setattr(prices, "search_jlc_by_model", lambda model: None)
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert len(r["no_match"]) == 1
        assert device_service.get_device(root, dev["id"])["price_status"] == "no_match"

    def test_jlc_network_error(self, root, monkeypatch):
        dev = device_service.create_device(root, {"name": "X", "model": "ABC-1"})
        monkeypatch.setattr(prices, "search_jlc_by_model",
                            lambda model: {"error": True})
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert len(r["failed"]) == 1


class TestScopesAndGuards:
    def test_skip_without_key(self, root):
        """无 C 编号且无型号:跳过并计入摘要。"""
        dev = device_service.create_device(root, {"name": "纯手工"})
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert r["skipped"] and not r["updated"]

    def test_scope_stale(self, root, monkeypatch):
        d1 = device_service.create_device(root, {"name": "新的", "lcsc_code": "C1"})
        d2 = device_service.create_device(root, {"name": "旧标", "lcsc_code": "C2"})
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _lcsc())
        prices.refresh_prices(root, ids=[d1["id"]], scope="selected")   # d1 → ok
        r = prices.refresh_prices(root, scope="stale")
        # d2 尚未刷新,应被 stale 命中;d1(ok)不重复
        assert [i["id"] for i in r["updated"]] == [d2["id"]]

    def test_invalid_scope_rejected(self, root):
        with pytest.raises(security.ValidationError):
            prices.refresh_prices(root, scope="hack")

    def test_selected_requires_ids(self, root):
        with pytest.raises(security.ValidationError):
            prices.refresh_prices(root, ids=[], scope="selected")

    def test_user_price_noted_in_summary(self, root, monkeypatch):
        """手填价格被覆盖时,摘要中保留 old_price 供核对。"""
        dev = device_service.create_device(root, {"name": "R", "lcsc_code": "C25704",
                                                  "price": 0.05})
        monkeypatch.setattr(prices, "fetch_lcsc_detail", lambda code: _lcsc())
        r = prices.refresh_prices(root, ids=[dev["id"]], scope="selected")
        assert r["updated"][0]["old_price"] == 0.05


class TestPriceApi:
    def test_refresh_endpoint(self, client, monkeypatch):
        from pdl.services import prices as prices_mod
        resp = client.post("/api/devices", json={"name": "API电阻", "lcsc_code": "C25704"})
        monkeypatch.setattr(prices_mod, "fetch_lcsc_detail", lambda code: _lcsc())
        resp = client.post("/api/prices/refresh", json={"scope": "all"})
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["requested"] == 1 and len(data["updated"]) == 1

    def test_refresh_invalid_body(self, client):
        assert client.post("/api/prices/refresh",
                           json={"scope": 123}).status_code == 400

    def test_device_model_crud(self, client):
        resp = client.post("/api/devices",
                           json={"name": "MOSFET", "model": "NTMFS5C430NLT1G"})
        assert resp.status_code == 201
        did = resp.get_json()["data"]["id"]
        assert resp.get_json()["data"]["model"] == "NTMFS5C430NLT1G"
        resp = client.put(f"/api/devices/{did}", json={"model": "bad model!#"})
        assert resp.status_code == 400
        resp = client.put(f"/api/devices/{did}", json={"model": "AO3400A"})
        assert resp.get_json()["data"]["model"] == "AO3400A"
