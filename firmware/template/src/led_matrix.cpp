#include "led_matrix.h"
#include "box_config.h"

static Adafruit_NeoPixel s_strip(LED_COUNT, LED_DATA_PIN, NEO_GRB + NEO_KHZ800);
static int16_t s_cursor = -1;
static int16_t s_lit = -1;
static int16_t s_blink_cell = -1;
static uint8_t s_blink_left = 0;
static bool s_blink_on = false;
static uint32_t s_last_toggle = 0;
constexpr uint32_t kColorCursor = 0x141414;   // 柔和白
constexpr uint32_t kColorMapped = 0x06060d;   // 微亮蓝:已占用格
constexpr uint32_t kColorLit = 0x303000;      // 黄色常亮
constexpr uint32_t kColorBlink = 0x404000;

int16_t led_matrix::led_index(uint16_t cell) {
    if (cell >= BOX_CELLS) return -1;
    const uint16_t row = cell / BOX_COLS;
    uint16_t col = cell % BOX_COLS;
#if LED_LAYOUT_SERPENTINE
    if (row & 1) col = BOX_COLS - 1 - col;  // 蛇形排布:奇数行倒序
#endif
    return static_cast<int16_t>(row * BOX_COLS + col);
}

void led_matrix::begin() {
    s_strip.begin();
    s_strip.setBrightness(LED_BRIGHTNESS);
    s_strip.clear();
    s_strip.show();
}

void led_matrix::set_cursor(int16_t cell) { s_cursor = cell; }

void led_matrix::set_lit(int16_t cell) { s_lit = cell; }

void led_matrix::blink(int16_t cell) {
    s_blink_cell = cell;
    s_blink_left = 6;      // 3 次亮灭
    s_blink_on = false;
    s_last_toggle = 0;     // 立即开始
}

void led_matrix::update() {
    if (s_blink_left > 0 && millis() - s_last_toggle >= 90) {
        s_last_toggle = millis();
        s_blink_on = !s_blink_on;
        s_blink_left--;
    }
    bool dirty = false;
    for (uint16_t cell = 0; cell < BOX_CELLS; cell++) {
        const int16_t idx = led_index(cell);
        if (idx < 0) continue;
        uint32_t color = kColorMapped;
        if (cell == s_cursor) color = kColorCursor;
        if (cell == s_lit) color = kColorLit;
        if (cell == s_blink_cell && s_blink_left > 0)
            color = s_blink_on ? kColorBlink : 0;
        if (s_strip.getPixelColor(idx) != color) {
            s_strip.setPixelColor(idx, color);
            dirty = true;
        }
    }
    if (dirty) s_strip.show();
}
