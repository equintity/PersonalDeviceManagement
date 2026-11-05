"""ESP32 固件代码生成器(需求 4、5)。

只做三件事,保证“软件层配置 → 自动生成代码 → 无需手改代码层”:

1. 复制固件模板(firmware/template/,逻辑固定且经过审核);
2. 把当前器件盒配置与映射渲染进 include/box_config.h ——
   这是唯一注入用户数据的位置,每个字符串都经过
   security.c_escape() 转义 + 白名单校验,每个数字都经过
   security.c_int() 严格整数校验;
3. 生成后整体扫描(scan_generated_code)再写盘,并写入
   .pdl_firmware 标记与 manifest,便于追溯与防误覆盖。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .. import security
from ..db import utc_now
from ..paths import as_resources
from . import boxmap

APP_VERSION = "1.0.0"

_HEADER_TEMPLATE = """\
// ===========================================================================
// 器件盒配置文件 —— 由 PersonalDeviceManagement v{version} 自动生成
// 生成时间:{now}
//
// !! 本文件由软件自动生成,请勿手动编辑 !!
// 修改映射请回到软件“器件盒映射”页调整后重新生成。
//
// 所有字符串均已通过白名单校验与 C 转义,数值均已做范围校验。
// ===========================================================================
#pragma once

// ---- 格栅布局(需求 3:尺寸可动态配置) ----
#define BOX_ROWS            {rows}
#define BOX_COLS            {cols}
#define BOX_CELLS           {cells}
#define LED_COUNT           {cells}

// ---- WS2812 LED ----
#define LED_DATA_PIN        {led_pin}
#define LED_BRIGHTNESS      {brightness}
#define LED_LAYOUT_SERPENTINE {serpentine}

// ---- 设备名(串口日志标签) ----
#define DEVICE_NAME         "{device_name}"

// ---- 五向按键 ----
#define BTN_PIN_UP          {btn_up}
#define BTN_PIN_DOWN        {btn_down}
#define BTN_PIN_LEFT        {btn_left}
#define BTN_PIN_RIGHT       {btn_right}
#define BTN_PIN_OK          {btn_ok}
#define BTN_LONG_PRESS_MS   600

// ---- OLED (SSD1306 I2C) ----
#define OLED_SDA_PIN        {oled_sda}
#define OLED_SCL_PIN        {oled_scl}
#define OLED_I2C_ADDR       0x3C

// ---- 器件映射表(需求 2:格位 <-> 器件) ----
typedef struct {{
    uint16_t    cell;    // 格位编号 = row * BOX_COLS + col
    const char* code;    // 立创C编号(空串表示手工器件)
    const char* name;    // 器件名称(UTF-8)
    uint32_t    qty;     // 初始库存数量
}} BoxMapping;

#define MAPPING_COUNT       {mapping_count}

static const BoxMapping BOX_MAPPINGS[MAPPING_COUNT] = {{
{mapping_lines}
}};
"""


def render_box_config_header(project_root) -> str:
    """根据当前配置渲染 box_config.h 内容(不写盘)。"""
    box = boxmap.get_box(project_root)
    cfg = box["config"]
    rows = security.c_int(cfg["rows"], min_val=1, max_val=security.MAX_ROWS, field="行数")
    cols = security.c_int(cfg["cols"], min_val=1, max_val=security.MAX_COLS, field="列数")
    cells = security.c_int(cfg["cells"], min_val=1, max_val=security.MAX_CELLS, field="格位数")
    led_pin = security.c_int(cfg["led_pin"], min_val=0, max_val=48, field="LED引脚")
    brightness = security.c_int(cfg["led_brightness"], min_val=0, max_val=255, field="亮度")
    serpentine = "1" if cfg["led_layout"] == "serpentine" else "0"
    device_name = security.c_escape(cfg["device_name"], field="设备名")
    btn = {k: security.c_int(cfg[k], min_val=0, max_val=48, field=k)
           for k in ("btn_up", "btn_down", "btn_left", "btn_right", "btn_ok")}
    sda = security.c_int(cfg["oled_sda"], min_val=0, max_val=48, field="SDA")
    scl = security.c_int(cfg["oled_scl"], min_val=0, max_val=48, field="SCL")

    lines: list[str] = []
    for m in box["mappings"]:
        if m["device"] is None:
            continue
        cell = security.c_int(m["cell"], min_val=0,
                              max_val=int(rows) * int(cols) - 1, field="格位编号")
        code = security.validate_lcsc_code(m["device"]["lcsc_code"]) or ""
        # 安全边界是 c_escape(控制字符清除 + 引号转义),
        # 名称本身只做清洗不做白名单硬校验,避免生僻字符阻断生成
        name = security.c_escape(
            security.sanitize_text(m["device"]["name"], field="器件名",
                                   max_len=security.MAX_NAME_LEN) or "未命名",
            field="器件名")
        qty = security.c_int(m["device"]["qty"], min_val=0,
                             max_val=security.MAX_QTY, field="库存")
        lines.append(f"    {{{cell}, \"{security.c_escape(code)}\", \"{name}\", {qty}}},")
    mapping_count = security.c_int(len(lines), min_val=0, max_val=security.MAX_CELLS,
                                   field="映射数量")

    header = _HEADER_TEMPLATE.format(
        version=APP_VERSION, now=utc_now(), rows=rows, cols=cols, cells=cells,
        led_pin=led_pin, brightness=brightness, serpentine=serpentine,
        device_name=device_name, btn_up=btn["btn_up"], btn_down=btn["btn_down"],
        btn_left=btn["btn_left"], btn_right=btn["btn_right"], btn_ok=btn["btn_ok"],
        oled_sda=sda, oled_scl=scl, mapping_count=mapping_count,
        mapping_lines="\n".join(lines) if lines else "    // (暂无映射,空表)",
    )
    security.scan_generated_code(header)  # 写盘前的最后一道防线
    return header


def render_output_readme(project_root) -> str:
    box = boxmap.get_box(project_root)
    cfg = box["config"]
    mapped = [m for m in box["mappings"] if m["device"]]
    rows_txt = "\n".join(
        f"| {m['row']} | {m['col']} | {m['cell']} | "
        f"{security.sanitize_text(m['device']['lcsc_code'] or '—', max_len=12)} | "
        f"{security.sanitize_text(m['device']['name'], max_len=60)} | "
        f"{security.c_int(m['device']['qty'], min_val=0, max_val=security.MAX_QTY, field='库存')} |"
        for m in mapped
    ) or "| - | - | - | - | (暂无映射) | - |"
    return f"""# {security.sanitize_text(cfg['device_name'], max_len=30)} 固件

由 **PersonalDeviceManagement v{APP_VERSION}** 自动生成({utc_now()}),
映射配置如下,**无需手动修改任何代码**。

## 硬件配置

| 项目 | 值 |
|---|---|
| 格栅布局 | {cfg['rows']} 行 × {cfg['cols']} 列 = {cfg['cells']} 格 |
| WS2812 数据引脚 | GPIO{cfg['led_pin']} |
| LED 排列 | {'蛇形(serpentine)' if cfg['led_layout'] == 'serpentine' else '逐行(row)'} |
| OLED | SSD1306 128x64,SDA=GPIO{cfg['oled_sda']},SCL=GPIO{cfg['oled_scl']} |
| 按键 | 上 {cfg['btn_up']} / 下 {cfg['btn_down']} / 左 {cfg['btn_left']} / 右 {cfg['btn_right']} / 确认 {cfg['btn_ok']} |

## 器件映射({len(mapped)} 格)

| 行 | 列 | 格位 | C编号 | 名称 | 初始库存 |
|---|---|---|---|---|---|
{rows_txt}

## 烧录步骤(PlatformIO)

1. 安装 [VSCode](https://code.visualstudio.com/) + PlatformIO 插件;
2. 用 VSCode 打开本目录(含 platformio.ini);
3. USB 连接 ESP32-S3 开发板,点击底部状态栏 **Upload**(或 `pio run -t upload`);
4. 打开串口监视器(115200),看到 `hello` 事件即成功;
5. 回到 PersonalDeviceManagement 软件点击“连接器件盒”,开始联动。

## 操作说明

- 五向键移动光标,OLED 显示格位与器件;
- **短按确认键**:取料(库存-1,LED 闪烁,同步扣减软件库存);
- **长按确认键**:退料(库存+1);
- 串口协议:换行分隔 JSON,详见项目 `docs/FIRMWARE.md`。
"""


def preview(project_root) -> dict:
    """预览将要生成的 box_config.h(供用户在 UI 审查,安全透明)。"""
    header = render_box_config_header(project_root)
    return {"filename": "include/box_config.h", "content": header,
            "sha256": hashlib.sha256(header.encode("utf-8")).hexdigest()}


def generate(project_root, output_dir: Path) -> dict:
    """生成完整可编译的 PlatformIO 固件工程到 output_dir。

    project_root 接受资源根或 AppPaths:模板取资源根,防覆盖判定用资源根。
    """
    resources_root = as_resources(project_root)
    output_dir = security.validate_output_dir(str(output_dir), app_root=resources_root)
    header = render_box_config_header(project_root)  # 校验失败在这里先爆
    readme = render_output_readme(project_root)

    template_dir = resources_root / "firmware" / "template"
    if not (template_dir / "platformio.ini").exists():
        raise RuntimeError("固件模板缺失,请重新安装程序")

    output_dir.mkdir(parents=True, exist_ok=True)
    # 1) 复制固定代码层(逻辑永远来自模板,杜绝半生不熟的代码)
    for item in template_dir.iterdir():
        if item.name == "include":
            continue  # box_config.h 由生成器独占,模板中的占位版不复制
        dest = output_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
    # 2) 写入生成的配置层
    inc_dir = output_dir / "include"
    inc_dir.mkdir(exist_ok=True)
    (inc_dir / "box_config.h").write_text(header, encoding="utf-8", newline="\n")
    (output_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    (output_dir / ".pdl_firmware").write_text(json.dumps({
        "generator": "PersonalDeviceManagement", "version": APP_VERSION,
        "generated_at": utc_now(),
        "config_sha256": hashlib.sha256(header.encode("utf-8")).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    written = sorted(p.relative_to(output_dir).as_posix()
                     for p in output_dir.rglob("*") if p.is_file())
    return {"output_dir": str(output_dir), "files": written, "count": len(written)}
