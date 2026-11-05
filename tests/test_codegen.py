"""代码生成测试(需求 4、5):映射→固件,注入不可逃逸。"""

import pytest

from pdl import security
from pdl.services import boxmap
from pdl.services import devices as device_service
from pdl.services import codegen


def _setup_box(root) -> None:
    d1 = device_service.create_device(root, {
        "name": '100nF 0603 电容', "lcsc_code": "C25704", "qty": 120})
    d2 = device_service.create_device(root, {
        "name": ' STM32F103C8T6 "MCU"', "qty": 5})   # 无C编号 + 引号注入尝试
    boxmap.set_mapping(root, 0, d1["id"])
    boxmap.set_mapping(root, 10, d2["id"])


class TestGenerate:
    def test_generates_project(self, root):
        _setup_box(root)
        out = root.parent / "fw_out"
        result = codegen.generate(root, out)
        names = set(result["files"])
        assert {"platformio.ini", "README.md", ".pdl_firmware",
                "src/main.cpp", "include/box_config.h"} <= names
        header = (out / "include" / "box_config.h").read_text(encoding="utf-8")
        assert "#define BOX_ROWS            7" in header
        assert "#define BOX_COLS            9" in header
        assert '100nF 0603 电容' in header
        # 引号被转义,不会截断字符串
        assert '\\"MCU\\"' in header
        assert header.count("{0, \"C25704\"") == 1

    def test_regen_over_marked_dir(self, root):
        _setup_box(root)
        out = root.parent / "fw_out2"
        codegen.generate(root, out)
        codegen.generate(root, out)     # 二次生成到同一目录(有标记)应成功

    def test_refuses_foreign_nonempty_dir(self, root, tmp_path):
        target = tmp_path / "occupied"
        target.mkdir()
        (target / "x.txt").write_text("data")
        with pytest.raises(security.ValidationError):
            codegen.generate(root, target)

    def test_refuses_inside_app(self, root):
        with pytest.raises(security.ValidationError):
            codegen.generate(root, root / "pdl")

    def test_injection_neutralized(self, root):
        # 纵深防御测试:绕过入口校验,直接把恶意名称写入数据库,
        # 验证代码生成层仍然无法被注入
        from pdl.db import get_db, utc_now
        conn = get_db(root)
        conn.execute(
            "INSERT INTO devices (name, qty, source, created_at, updated_at)"
            " VALUES (?, 1, 'manual', ?, ?)",
            ('x";\n#include "evil.h"\n#define P 1//', utc_now(), utc_now()))
        conn.commit()
        evil_id = conn.execute("SELECT id FROM devices WHERE name LIKE 'x%'").fetchone()["id"]
        boxmap.set_mapping(root, 3, evil_id)
        out = root.parent / "fw_evil"
        header = codegen.render_box_config_header(root)   # 不抛错即通过转义与扫描
        import re
        includes = re.findall(r'^\s*#include\s+"evil\.h"', header, re.M)
        assert includes == []
        codegen.generate(root, out)
        assert "#include \"evil.h\"" not in (out / "include" / "box_config.h").read_text(encoding="utf-8")

    def test_preview_matches_generate(self, root):
        _setup_box(root)
        p = codegen.preview(root)
        assert p["filename"] == "include/box_config.h"
        assert len(p["sha256"]) == 64

    def test_resize_before_generate(self, root):
        _setup_box(root)  # 格位0(C25704) + 格位10(STM32,即第1行第1列)
        # 把 STM32 从格位10挪到格位30(第3行第3列),缩到 2x4 后应被移除
        d2 = device_service.list_devices(root, q="STM32")["items"][0]
        boxmap.set_mapping(root, 10, None)
        boxmap.set_mapping(root, 30, d2["id"])
        boxmap.update_config(root, {"rows": 2, "cols": 4})
        header = codegen.render_box_config_header(root)
        assert "#define BOX_ROWS            2" in header
        assert "#define BOX_COLS            4" in header
        assert "#define MAPPING_COUNT       1" in header
        assert '"C25704"' in header and "STM32" not in header
