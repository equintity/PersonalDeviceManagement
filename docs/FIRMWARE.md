# 固件架构与联动协议

## 生成的工程结构

```
<输出目录>/
├── platformio.ini          # ESP32-S3 环境(8MB Flash + Octal PSRAM)
├── include/box_config.h    # ★ 软件自动生成的配置层(唯一会变化的部分)
├── src/
│   ├── main.cpp            # 业务编排:光标、取料/退料、事件分发
│   ├── led_matrix.cpp      # WS2812 格栅:格位↔灯珠映射(逐行/蛇形)、光标、点亮、闪烁
│   ├── buttons.cpp         # 五向键:去抖、方向键长按连发、OK 短/长按
│   ├── box_ui.cpp          # U8g2 OLED:格位/器件名(中文)/库存,UTF-8 字体
│   ├── storage.cpp         # NVS(Preferences):每格库存断电保存
│   └── protocol.cpp        # 串口 JSON 协议:下行命令解析(白名单+边界检查)
└── README.md               # 生成的映射表与烧录步骤
```

**改映射 = 软件里重新生成 + 重新烧录**;库存数量则实时双向同步、NVS 持久化。

## 硬件行为

- 五向键移动光标(长按连发),LED 显示:已占用格微亮、光标白色、联动点亮黄色;
- **短按确认** = 取料:库存-1 → LED 闪烁 3 次 → 上报 `take` → 软件扣库存并回写 `qty`;
- **长按确认(600ms)** = 退料:库存+1 → 上报 `return`;
- OLED:格位坐标、器件名(chinese2 字体)、库存、C编号提示。

## 串口协议(115200,换行分隔 JSON,单条 ≤512B)

### 上位机 → 器件盒(命令)

| cmd | 参数 | 说明 |
|---|---|---|
| `ping` | - | 心跳,回 `pong` |
| `info` | - | 回 `hello`(设备名/行列数/映射数) |
| `light` | cell | 点亮指定格位(联动"点亮测试") |
| `off` | - | 熄灭 |
| `sync` | rows, cols, items[{cell, qty, code}] | 全量映射推送(连接时自动发送) |
| `qty` | cell, qty | 单格库存回写(取料联动后) |
| `take` | cell | 远程取料(软件按钮触发硬件同步) |

### 器件盒 → 上位机(事件)

| evt | 字段 | 说明 |
|---|---|---|
| `hello` | device, rows, cols, cells, mappings, fw | 上线/应答 info |
| `ready` | mapped | sync 完成确认 |
| `take` / `return` | cell, qty, code | 硬件按键取料/退料(软件自动扣减/回补库存) |
| `qty` | cell, qty | 库存变化回报 |
| `cursor` | cell | 光标移动 |
| `lit` | cell | 点亮确认 |
| `pong` | t | 心跳应答 |
| `log` | msg | 提示消息(未知命令、库存为零等) |

### 示例

```json
{"cmd":"light","cell":23}
{"evt":"take","cell":23,"qty":41,"code":"C25704"}
{"cmd":"qty","cell":23,"qty":41}
```

## box_config.h(生成物示例)

```c
#define BOX_ROWS            7
#define BOX_COLS            9
#define BOX_CELLS           63
#define LED_DATA_PIN        48
#define LED_LAYOUT_SERPENTINE 1
#define BTN_PIN_OK          42
typedef struct { uint16_t cell; const char* code; const char* name; uint32_t qty; } BoxMapping;
#define MAPPING_COUNT       2
static const BoxMapping BOX_MAPPINGS[MAPPING_COUNT] = {
    {0, "C25704", "100nF 50V ±10% X7R 0603", 120},
    {5, "", "STM32F103C8T6", 2},
};
```

所有字符串均经白名单校验 + C 转义(见 docs/SECURITY.md),整文件生成后再扫描。

## 依赖(PlatformIO 自动拉取)

- `Adafruit NeoPixel` —— WS2812 驱动
- `U8g2` —— OLED(SSD1306 I2C,含中文字库 unifont_t_chinese2 / wqy12)
- `ArduinoJson` v7 —— 协议解析

## 立创·天际板(ESP32S3R8N8)默认引脚

| 功能 | GPIO | 备注 |
|---|---|---|
| WS2812 数据 | 48 | 板载 5×7,扩展 7×9 需外接灯带 |
| OLED SDA / SCL | 40 / 39 | SSD1306 I2C 0x3C |
| 上/下/左/右/确认 | 46 / 15 / 41 / 45 / 42 | 五向键,内部上拉,按下接地 |
