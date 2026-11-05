/* WS2812 LED 格栅驱动:格位 (row,col) <-> 灯珠序号,光标/点亮/取料闪烁 */
#pragma once
#include <Arduino.h>
#include <Adafruit_NeoPixel.h>

namespace led_matrix {

// cell = row * BOX_COLS + col;返回 -1 表示该 cell 非法
int16_t led_index(uint16_t cell);

void begin();
// 光标(五向键移动,柔和白光)
void set_cursor(int16_t cell);
// 联动点亮(软件 / 取料,黄色常亮)
void set_lit(int16_t cell);
// 取料闪烁(3 次,非阻塞)
void blink(int16_t cell);
// 需在 loop() 中持续调用以驱动闪烁动画
void update();

}  // namespace led_matrix
