/* OLED(SSD1306 I2C, U8g2, 支持中文 UTF-8)状态显示 */
#pragma once
#include <Arduino.h>

namespace box_ui {

void begin();
// 显示某个格位的信息;name 为 UTF-8(chinese2 字体覆盖常用简体字)
void show_cell(uint16_t cell, const char* code, const char* name,
               int32_t qty, bool mapped);
void show_message(const char* line1, const char* line2);

}  // namespace box_ui
