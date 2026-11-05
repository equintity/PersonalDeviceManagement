#include "box_ui.h"
#include <U8g2lib.h>
#include <Wire.h>
#include "box_config.h"

static U8G2_SSD1306_128X64_NONAME_F_HW_I2C s_u8g2(U8G2_R0, U8X8_PIN_NONE,
                                                  OLED_SCL_PIN, OLED_SDA_PIN);

void box_ui::begin() {
    s_u8g2.enableUTF8Print();
    s_u8g2.begin();
}

// 把 UTF-8 串截断到最多 max_chars 个显示字符(中文按 1 个计)
static void utf8_truncate(const char* src, char* dst, size_t dst_size,
                          uint8_t max_chars) {
    uint8_t chars = 0;
    size_t i = 0, o = 0;
    while (src[i] != '\0' && chars < max_chars && o + 5 < dst_size) {
        const uint8_t c = static_cast<uint8_t>(src[i]);
        uint8_t len = 1;
        if (c >= 0xF0) len = 4;
        else if (c >= 0xE0) len = 3;
        else if (c >= 0xC0) len = 2;
        for (uint8_t k = 0; k < len && src[i + k] != '\0'; k++) dst[o++] = src[i + k];
        i += len;
        chars++;
    }
    dst[o] = '\0';
}

void box_ui::show_cell(uint16_t cell, const char* code, const char* name,
                       int32_t qty, bool mapped) {
    char line1[32], line2[64], line3[32];
    snprintf(line1, sizeof(line1), "格位 %u-%u #R%uC%u",
             static_cast<unsigned>(cell / BOX_COLS),
             static_cast<unsigned>(cell % BOX_COLS),
             static_cast<unsigned>(cell / BOX_COLS + 1),
             static_cast<unsigned>(cell % BOX_COLS + 1));
    utf8_truncate(mapped ? name : "(空格位)", line2, sizeof(line2), 8);
    if (mapped) snprintf(line3, sizeof(line3), "库存:%ld", static_cast<long>(qty));
    else snprintf(line3, sizeof(line3), "未映射器件");

    s_u8g2.clearBuffer();
    s_u8g2.setFont(u8g2_font_wqy12_t_gb2312a);   // 小字中文
    s_u8g2.drawUTF8(0, 12, line1);
    s_u8g2.setFont(u8g2_font_unifont_t_chinese2); // 大字中文
    s_u8g2.drawUTF8(0, 34, line2);
    s_u8g2.setFont(u8g2_font_wqy12_t_gb2312a);
    s_u8g2.drawUTF8(0, 52, line3);
    s_u8g2.drawUTF8(0, 63, mapped ? code[0] ? code : "手工器件"
                                   : "取料=短按 退料=长按");
    s_u8g2.sendBuffer();
}

void box_ui::show_message(const char* line1, const char* line2) {
    s_u8g2.clearBuffer();
    s_u8g2.setFont(u8g2_font_wqy12_t_gb2312a);
    s_u8g2.drawUTF8(0, 24, line1);
    s_u8g2.drawUTF8(0, 44, line2);
    s_u8g2.sendBuffer();
}
