"""安全模块单元测试(需求 5)。"""

import pytest

from pdl import security


class TestCEscape:
    def test_basic_escape(self):
        assert security.c_escape('say "hi"') == 'say \\"hi\\"'
        assert security.c_escape("a\\b") == "a\\\\b"

    def test_injection_cannot_escape_string(self):
        # 经典注入:试图闭合字符串并注入新语句
        payload = '";\n#include "evil.h"\n#define X 1 //'
        escaped = security.c_escape(payload)
        # 引号全部被转义(包括首字符),控制字符被清除
        assert escaped == '\\";#include \\"evil.h\\"#define X 1 //'
        assert "\n" not in escaped

    def test_control_chars_removed(self):
        assert "\x00" not in security.c_escape("a\x00b\x1bc")
        assert "\x1b[31m" not in security.c_escape("\x1b[31mred")

    def test_chinese_preserved(self):
        assert security.c_escape("多层陶瓷电容器") == "多层陶瓷电容器"


class TestDeviceName:
    def test_accept_chinese(self):
        assert security.validate_device_name("100nF X7R 0603 电容") == "100nF X7R 0603 电容"

    def test_reject_control_char(self):
        with pytest.raises(security.ValidationError):
            security.validate_device_name("bad\x00name")

    def test_reject_bidi_attack(self):
        # 特洛伊源码方向符
        with pytest.raises(security.ValidationError):
            security.validate_device_name("dev\u202Ename")

    def test_reject_empty_and_long(self):
        with pytest.raises(security.ValidationError):
            security.validate_device_name("   ")
        with pytest.raises(security.ValidationError):
            security.validate_device_name("x" * 65)

    def test_reject_non_string(self):
        with pytest.raises(security.ValidationError):
            security.validate_device_name(12345)


class TestLcscCode:
    def test_normalize(self):
        assert security.validate_lcsc_code("c25704") == "C25704"

    def test_reject_bad(self):
        for bad in ("C123456789", "X123", "C12a4", "-C123", "C 123;DROP"):
            with pytest.raises(security.ValidationError):
                security.validate_lcsc_code(bad)

    def test_allow_empty(self):
        assert security.validate_lcsc_code(None) is None
        assert security.validate_lcsc_code("") is None


class TestGpio:
    def test_valid(self):
        assert security.validate_gpio(48, field="LED") == 48

    def test_nonexistent_pin(self):
        with pytest.raises(security.ValidationError):
            security.validate_gpio(22, field="LED")   # S3 上不存在

    def test_risky_pin(self):
        assert security.validate_gpio(27, allow_risky=True) == 27
        with pytest.raises(security.ValidationError):
            security.validate_gpio(27, field="按键", allow_risky=False)

    def test_bool_rejected(self):
        with pytest.raises(security.ValidationError):
            security.validate_gpio(True, field="LED")


class TestOutputDir:
    def test_reject_relative(self, tmp_path):
        with pytest.raises(security.ValidationError):
            security.validate_output_dir("relative/dir", app_root=tmp_path)

    def test_reject_inside_app(self, tmp_path):
        inside = tmp_path / "pdl" / "evil"
        with pytest.raises(security.ValidationError):
            security.validate_output_dir(str(inside), app_root=tmp_path)

    def test_reject_nonempty_foreign_dir(self, tmp_path):
        target = tmp_path.parent / "somewhere"
        target.mkdir()
        (target / "important.txt").write_text("do not overwrite")
        with pytest.raises(security.ValidationError):
            security.validate_output_dir(str(target), app_root=tmp_path)

    def test_accept_empty_or_marked_dir(self, tmp_path):
        empty = tmp_path.parent / "empty_out"
        empty.mkdir(exist_ok=True)
        assert security.validate_output_dir(str(empty), app_root=tmp_path) == empty.resolve()
        (empty / ".pdl_firmware").write_text("{}")
        assert security.validate_output_dir(str(empty), app_root=tmp_path) == empty.resolve()


class TestCodeScan:
    def test_catches_forbidden(self):
        for token in ('system(', "#include <", "asm volatile"):
            with pytest.raises(security.ValidationError):
                security.scan_generated_code(f"const char* s = \"{token}\";")

    def test_allows_normal(self):
        security.scan_generated_code('#define NAME "C25704 0603 电容"')


class TestInts:
    def test_validate_int_rejects_bool_and_str(self):
        with pytest.raises(security.ValidationError):
            security.validate_int(True, field="x")
        with pytest.raises(security.ValidationError):
            security.validate_int("3", field="x")

    def test_validate_int_range(self):
        with pytest.raises(security.ValidationError):
            security.validate_int(17, field="行数", min_val=1, max_val=16)
