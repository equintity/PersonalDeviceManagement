"""导入服务测试:订单 xlsx/csv 解析、合并累加、C编号导入。"""

import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from pdl.services import devices as device_service
from pdl.services import importer


def _make_order_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "订单明细"
    ws.append(["序号", "订单号", "商品编号", "商品名称", "数量", "单价"])
    ws.append(["1", "202561", "C25704", "100nF 0603", 3, 0.01])
    ws.append(["2", "202561", "C81588", "1uF 0603", 2, 0.02])
    ws.append(["3", "202561", "C25704", "100nF 0603", 1, 0.01])  # 同器件两行
    wb.save(path)


class TestOrderXlsx:
    def test_parse_and_accumulate(self, tmp_path):
        f = tmp_path / "order.xlsx"
        _make_order_xlsx(f)
        entries = importer._parse_order_file(f)
        d = dict(entries)
        assert d["C25704"] == 4      # 3 + 1 同文件累加
        assert d["C81588"] == 2

    def test_full_import_idempotent(self, root, tmp_path):
        """T-M2-01:同一订单重复导入默认拦截,显式确认后才合并累加。"""
        f = tmp_path / "order.xlsx"
        _make_order_xlsx(f)
        s1 = importer.import_files(root, [{"path": str(f), "filename": "order.xlsx"}])
        assert s1["imported"] == 2
        s2 = importer.import_files(root, [{"path": str(f), "filename": "order.xlsx"}])
        assert s2.get("duplicate") is True          # 默认拦截,不改任何 qty
        dev = device_service.list_devices(root, q="C25704")["items"][0]
        assert dev["qty"] == 4                       # 未翻倍
        s3 = importer.import_files(root, [{"path": str(f), "filename": "order.xlsx"}],
                                   force=True)
        assert s3["merged"] == 2                     # 显式确认后合并
        dev = device_service.list_devices(root, q="C25704")["items"][0]
        assert dev["qty"] == 8                       # 4 + 4


class TestOrderCsv:
    def test_gbk_csv(self, tmp_path):
        f = tmp_path / "order.csv"
        with open(f, "w", newline="", encoding="gbk") as fh:
            w = csv.writer(fh)
            w.writerow(["商品编号", "数量"])
            w.writerow(["C31250", 10])
        entries = importer._parse_order_file(f)
        assert entries == [("C31250", 10)]

    def test_fallback_scan(self, tmp_path):
        # 无表头:全表扫描兜底
        f = tmp_path / "raw.csv"
        f.write_text("some,text,C128300,here\n", encoding="utf-8")
        entries = importer._parse_order_file(f)
        assert entries == [("C128300", 1)]

    def test_reject_unknown_ext(self, tmp_path):
        f = tmp_path / "evil.exe"
        f.write_bytes(b"MZ")
        with pytest.raises(Exception):
            importer._parse_order_file(f)


class TestCodesImport:
    def test_import_codes_no_fetch(self, root):
        s = importer.import_codes(root, ["c25704", "C81588", "bad-code", ""],
                                  fetch_details=False)
        assert s["imported"] == 2
        items = device_service.list_devices(root)["items"]
        assert {d["lcsc_code"] for d in items} == {"C25704", "C81588"}

    def test_too_many_codes(self, root):
        with pytest.raises(Exception):
            importer.import_codes(root, [f"C{i}" for i in range(301)],
                                  fetch_details=False)


class TestCsvRoundtrip:
    def test_export_import(self, root, tmp_path):
        device_service.create_device(root, {"name": "电阻 10k", "qty": 100,
                                            "lcsc_code": "C25804"})
        text = importer.export_csv(root)
        f = tmp_path / "devices_export.csv"
        f.write_text(text, encoding="utf-8")
        s = importer.import_csv(root, [{"path": str(f), "filename": f.name}])
        assert s["merged"] == 1
        dev = device_service.list_devices(root)["items"][0]
        assert dev["qty"] == 200      # 往返导入后数量翻倍
        assert dev["name"] == "电阻 10k"
