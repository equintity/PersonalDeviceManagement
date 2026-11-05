/* ===========================================================================
 *  EDA 智能器件盒固件 —— 由 PersonalDeviceManagement 自动生成配套配置
 *
 *  硬件:ESP32-S3 + WS2812 格栅(BOX_ROWS×BOX_COLS)+ SSD1306 OLED + 五向键
 *  映射关系来自 include/box_config.h(软件生成,勿手改);
 *  库存数量存 NVS,断电不丢;串口 115200 换行 JSON 与上位机联动。
 *
 *  操作:五向键移动光标;短按确认=取料(库存-1);长按确认=退料(库存+1)
 * =========================================================================== */
#include <Arduino.h>
#include "box_config.h"
#include "led_matrix.h"
#include "buttons.h"
#include "storage.h"
#include "protocol.h"
#include "box_ui.h"

namespace app {

static int32_t s_qty[BOX_CELLS];      // 每格库存,-1 = 未映射
static uint16_t s_cursor = 0;

int32_t qty_of(uint16_t cell) {
    return cell < BOX_CELLS ? s_qty[cell] : -1;
}

static int mapping_index(uint16_t cell) {
    for (int i = 0; i < MAPPING_COUNT; i++)
        if (BOX_MAPPINGS[i].cell == cell) return i;
    return -1;
}

void set_qty(uint16_t cell, uint32_t qty, bool persist, bool notify_host) {
    if (cell >= BOX_CELLS || s_qty[cell] < 0) return;   // 未映射格不受影响
    s_qty[cell] = static_cast<int32_t>(qty);
    if (persist) storage::save_qty(cell, qty);
    if (notify_host) protocol::send_stock("qty", cell, qty);
}

uint16_t first_mapped_cell() {
    if (MAPPING_COUNT > 0) return BOX_MAPPINGS[0].cell;
    return 0;
}

void remote_light(int16_t cell) {
    led_matrix::set_lit(cell);
}

static void render_current() {
    const uint16_t cell = s_cursor;
    const int idx = mapping_index(cell);
    if (idx < 0) {
        box_ui::show_cell(cell, "", "", 0, false);
        return;
    }
    box_ui::show_cell(cell, BOX_MAPPINGS[idx].code, BOX_MAPPINGS[idx].name,
                      s_qty[cell], true);
}

static void take_current() {
    const int idx = mapping_index(s_cursor);
    if (idx < 0) {
        box_ui::show_message("空格位", "请先在软件中映射器件");
        protocol::send_log("empty cell");
        return;
    }
    if (s_qty[s_cursor] > 0) {
        set_qty(s_cursor, static_cast<uint32_t>(s_qty[s_cursor] - 1), true, false);
        protocol::send_stock("take", s_cursor,
                             static_cast<uint32_t>(s_qty[s_cursor]));
    } else {
        protocol::send_stock("qty", s_cursor, 0);   // 库存为 0 仍回报
        protocol::send_log("out of stock");
    }
    led_matrix::blink(s_cursor);
    render_current();
}

static void return_current() {
    const int idx = mapping_index(s_cursor);
    if (idx < 0) return;
    const uint32_t q = static_cast<uint32_t>(s_qty[s_cursor]) + 1;
    set_qty(s_cursor, q, true, true);
    protocol::send_stock("return", s_cursor, q);
    render_current();
}

static void move_cursor(int8_t dx, int8_t dy) {
    const uint16_t row = s_cursor / BOX_COLS;
    const uint16_t col = s_cursor % BOX_COLS;
    uint16_t nr = row + (dy > 0 ? 1 : dy < 0 ? BOX_ROWS - 1 : 0);
    uint16_t nc = col + (dx > 0 ? 1 : dx < 0 ? BOX_COLS - 1 : 0);
    if (nr >= BOX_ROWS) nr = 0;
    if (nc >= BOX_COLS) nc = 0;
    s_cursor = static_cast<uint16_t>(nr * BOX_COLS + nc);
    led_matrix::set_cursor(s_cursor);
    render_current();
}

}  // namespace app

void setup() {
    Serial.begin(115200);
    delay(300);

    storage::begin();
    led_matrix::begin();
    box_ui::begin();
    buttons::begin();
    protocol::begin(Serial);

    // 初始化:未映射格置 -1;已映射格优先 NVS 记录,否则用生成配置中的初始值
    for (uint16_t cell = 0; cell < BOX_CELLS; cell++) app::s_qty[cell] = -1;
    for (int i = 0; i < MAPPING_COUNT; i++) {
        const uint16_t cell = BOX_MAPPINGS[i].cell;
        if (cell < BOX_CELLS) {
            app::s_qty[cell] = static_cast<int32_t>(
                storage::load_qty(cell, BOX_MAPPINGS[i].qty));
        }
    }

    app::s_cursor = app::first_mapped_cell();
    led_matrix::set_cursor(app::s_cursor);
    box_ui::show_message(DEVICE_NAME, "就绪,等待上位机");
    app::render_current();
    protocol::send_hello();
}

void loop() {
    // 1) 按键事件
    switch (buttons::poll()) {
        case buttons::Event::Up:    app::move_cursor(0, -1); break;
        case buttons::Event::Down:  app::move_cursor(0, 1); break;
        case buttons::Event::Left:  app::move_cursor(-1, 0); break;
        case buttons::Event::Right: app::move_cursor(1, 0); break;
        case buttons::Event::OkShort: app::take_current(); break;
        case buttons::Event::OkLong:  app::return_current(); break;
        default: break;
    }
    // 2) 串口协议
    protocol::poll();
    // 3) LED 动画
    led_matrix::update();
}
