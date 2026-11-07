"""安全模块:输入严格校验 + 代码生成安全转义。

设计原则(对应需求 5 —— 软件层与代码层之间的安全管理机制):

1. 所有来自用户/API 的输入必须经过本模块的校验函数,
   采用“白名单 + 限额”策略,拒绝一切未显式允许的内容。
2. 写入生成代码(C 头文件)的字符串一律经过 c_escape(),
   任何用户文本只可能作为字符串字面量出现,无法逃逸出引号。
3. 数据库访问全部使用参数化查询,禁止字符串拼接 SQL。
4. 过滤 Unicode 危险字符:控制字符、零宽字符、双向文本字符
   (防“特洛伊木马源码”攻击, CVE-2021-42574)。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

__all__ = [
    "ValidationError",
    "validate_str",
    "validate_device_name",
    "validate_lcsc_code",
    "validate_int",
    "validate_gpio",
    "validate_bool",
    "validate_id_list",
    "validate_output_dir",
    "sanitize_text",
    "c_escape",
    "c_int",
    "SAFE_GPIOS",
    "RISKY_GPIOS",
    "MAX_ROWS",
    "MAX_COLS",
    "MAX_CELLS",
]


class ValidationError(ValueError):
    """输入校验失败。message 面向用户,可直接返回给前端。"""


class ConflictError(ValidationError):
    """状态冲突(如重复撤销)。由全局 handler 映射为 409。T2/QA-02。"""


# ---------------------------------------------------------------------------
# 器件盒规模上限:防止生成过大的固件数组或过量的 LED
# ---------------------------------------------------------------------------
MAX_ROWS = 16
MAX_COLS = 24
MAX_CELLS = 256  # rows*cols 硬上限
MAX_DEVICES = 100_000
MAX_QTY = 9_999_999
MAX_NAME_LEN = 64
MAX_TEXT_LEN = 500

# ESP32-S3 实际存在的 GPIO(22~25 物理不存在)
EXISTING_GPIOS = set(range(0, 22)) | set(range(26, 49))
# 与 Flash(26~32)/Octal PSRAM(35~37)复用、需谨慎使用的引脚
RISKY_GPIOS = set(range(26, 33)) | {35, 36, 37}
SAFE_GPIOS = EXISTING_GPIOS - RISKY_GPIOS

# ---------------------------------------------------------------------------
# 危险 Unicode 字符:控制字符 + 零宽字符 + 双向文本控制符
# ---------------------------------------------------------------------------
_CONTROL_RE = re.compile(
    "[\x00-\x1f\x7f-\x9f"          # C0 / C1 控制字符
    "\u200b-\u200f"                 # 零宽字符与方向标记
    "\u202a-\u202e"                 # bidi 嵌入控制(特洛伊源码)
    "\u2066-\u2069"                 # bidi 隔离控制
    "\u2028\u2029"                  # 行/段分隔符
    "\ufeff"                        # BOM / 零宽不换行
    "]"
)

# 器件名允许的字符:中英文、数字、常用标点(白名单)
_NAME_RE = re.compile(
    r"^[\w\u4e00-\u9fff\u3400-\u4dbf "  # 字母数字下划线 + 汉字 + 空格
    r"\[\]\(\)\(\)\[\]【】《》·、,,。.\-—+/\\&%℃Ωμ*×\"'=#:|~!！?？；;;:$@^<>°±≤≥~··"
    r"]+$"
)

_LCSC_CODE_RE = re.compile(r"^[Cc]\d{1,8}$")

# 器件型号(MPN):字母数字开头,可含 . _ / - 与空格,1~50 字符
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./_\- ]{0,49}$")

# 生成代码中绝对不允许出现的词(深度防御,生成后扫描)
_FORBIDDEN_IN_CODE = ("__attribute__", "asm(", "asm volatile", "system(", "#include <")


# ---------------------------------------------------------------------------
# 通用校验
# ---------------------------------------------------------------------------
def sanitize_text(value, *, field: str = "text", max_len: int = MAX_TEXT_LEN) -> str:
    """去除危险 Unicode 字符并裁剪长度。value 必须是 str。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} 必须是字符串")
    cleaned = _CONTROL_RE.sub("", value)
    cleaned = cleaned.strip()
    if len(cleaned) > max_len:
        raise ValidationError(f"{field} 长度不能超过 {max_len} 字符")
    return cleaned


def validate_str(
    value,
    *,
    field: str,
    required: bool = False,
    max_len: int = MAX_TEXT_LEN,
    pattern: re.Pattern | None = None,
    pattern_hint: str = "",
) -> str:
    """校验普通文本字段:类型 → 危险字符清洗 → 长度 → 白名单正则。"""
    if value is None or (isinstance(value, str) and value == ""):
        if required:
            raise ValidationError(f"{field} 不能为空")
        return ""
    text = sanitize_text(value, field=field, max_len=max_len)
    if required and not text:
        raise ValidationError(f"{field} 不能为空")
    if pattern is not None and text and not pattern.match(text):
        hint = f"({pattern_hint})" if pattern_hint else ""
        raise ValidationError(f"{field} 包含不允许的字符{hint}")
    return text


def validate_device_name(value) -> str:
    """器件名称:1~64 字符,白名单字符集。

    严格模式:若清洗过程改变内容(含控制字符/双向控制符等),直接拒绝,
    避免静默改写造成“软件显示与代码生成内容不一致”。
    """
    if not isinstance(value, str):
        raise ValidationError("器件名称必须是字符串")
    if _CONTROL_RE.search(value):
        raise ValidationError("器件名称包含控制字符或不可见字符")
    name = validate_str(value, field="器件名称", required=True, max_len=MAX_NAME_LEN)
    if not _NAME_RE.match(name):
        raise ValidationError("器件名称包含不允许的字符")
    return name


def validate_lcsc_code(value) -> str | None:
    """立创 C 编号:C + 1~8 位数字,统一大写。允许 None/''(纯手工器件)。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("C编号格式错误")
    code = value.strip().upper()
    if code == "":
        return None
    if not _LCSC_CODE_RE.match(code):
        raise ValidationError(f"C编号格式错误:{value!r}(应为 C+数字,如 C25704)")
    return code


def validate_model(value) -> str:
    """器件型号(MPN):用于无 C 编号时按型号匹配价格;允许空串。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("型号必须是字符串")
    text = _CONTROL_RE.sub("", value).strip()
    if not text:
        return ""
    if len(text) > 50 or not _MODEL_RE.match(text):
        raise ValidationError(
            "型号格式错误:应以字母/数字开头,仅含字母数字与 . _ / - 空格,≤50 字符")
    return text


def validate_int(
    value, *, field: str, min_val: int = 0, max_val: int = MAX_QTY
) -> int:
    """整数校验:拒绝 bool/浮点/字符串数字。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} 必须是整数")
    if value < min_val or value > max_val:
        raise ValidationError(f"{field} 必须在 {min_val} ~ {max_val} 之间")
    return value


def validate_gpio(value, *, field: str = "引脚", allow_risky: bool = True) -> int:
    """ESP32-S3 GPIO 白名单校验。"""
    pin = validate_int(value, field=field, min_val=0, max_val=48)
    if pin not in EXISTING_GPIOS:
        raise ValidationError(f"{field} GPIO{pin} 在 ESP32-S3 上不存在")
    if not allow_risky and pin in RISKY_GPIOS:
        raise ValidationError(f"{field} GPIO{pin} 与 Flash/PSRAM 复用,已禁止使用")
    return pin


def validate_bool(value, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field} 必须是布尔值")
    return value


def validate_id_list(value, *, field: str = "ids", max_items: int = 1000) -> list[int]:
    """ID 列表校验(批量删除等)。"""
    if not isinstance(value, list):
        raise ValidationError(f"{field} 必须是数组")
    if len(value) > max_items:
        raise ValidationError(f"{field} 数量不能超过 {max_items}")
    result = []
    for item in value:
        result.append(validate_int(item, field=field, min_val=1, max_val=2**31 - 1))
    if not result:
        raise ValidationError(f"{field} 不能为空")
    return result


def validate_output_dir(raw_path, *, app_root: Path) -> Path:
    """代码生成输出目录校验:
    - 必须是绝对路径
    - 禁止写入程序自身目录(防止覆盖源代码)
    - 目录不存在或为空,或已包含本工具生成的固件(带 .pdl_firmware 标记)
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValidationError("输出目录不能为空")
    path_text = _CONTROL_RE.sub("", raw_path.strip())
    if len(path_text) > 240:
        raise ValidationError("输出目录路径过长")
    if not Path(path_text).is_absolute():   # 在 resolve 之前判断
        raise ValidationError("输出目录必须是绝对路径(如 D:\\firmware\\MyBox)")
    try:
        path = Path(path_text).resolve()
    except (OSError, ValueError) as exc:
        raise ValidationError(f"输出目录路径无效:{exc}") from exc
    app_root = app_root.resolve()
    if path == app_root or app_root in path.parents:
        raise ValidationError("输出目录不能位于程序目录内部,以免覆盖源代码")
    if path.exists():
        if not path.is_dir():
            raise ValidationError("输出路径已存在且不是目录")
        entries = list(path.iterdir())
        marker = path / ".pdl_firmware"
        if entries and not marker.exists():
            raise ValidationError(
                "目标目录非空且不是本工具生成的固件目录,请更换目录以免误覆盖"
            )
    return path


# ---------------------------------------------------------------------------
# C 代码转义(代码生成安全的核心)
# ---------------------------------------------------------------------------
_SIMPLE_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def c_escape(value: str, *, field: str = "文本") -> str:
    """把任意文本安全转义为 C 字符串字面量内容(不含两侧引号)。

    - 反斜杠/双引号/控制符全部转义,用户文本无法逃逸出字符串
    - 非 ASCII(如中文)保留 UTF-8 原始字节,配合 U8g2 UTF-8 字体可正常显示
    - 转义后再次扫描,确保结果中不含控制字符
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} 必须是字符串")
    text = _CONTROL_RE.sub("", value)
    out: list[str] = []
    for ch in text:
        esc = _SIMPLE_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:  # 双保险,理论上已被清洗
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    escaped = "".join(out)
    # 深度防御:转义结果中不允许再有任何原始控制字符
    for ch in escaped:
        if ord(ch) < 0x20:
            raise ValidationError(f"{field} 含有无法安全转义的控制字符")
    return escaped


def c_int(value: int, *, min_val: int = 0, max_val: int = MAX_QTY, field: str = "数值") -> str:
    """整数 → 安全的 C 字面量。先走严格校验,再格式化,杜绝拼接注入。"""
    checked = validate_int(value, field=field, min_val=min_val, max_val=max_val)
    return str(checked)


def scan_generated_code(text: str) -> None:
    """生成代码后的最终防线:扫描已知危险模式。命中即抛错并阻止写盘。"""
    lowered = text.lower()
    for token in _FORBIDDEN_IN_CODE:
        if token.lower() in lowered:
            raise ValidationError(f"生成的代码中检测到禁止出现的片段:{token!r}")
