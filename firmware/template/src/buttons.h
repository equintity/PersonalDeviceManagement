/* 五向按键:去抖 + 短按/长按识别 */
#pragma once
#include <Arduino.h>

namespace buttons {

enum class Event : uint8_t {
    None, Up, Down, Left, Right, OkShort, OkLong,
};

void begin();
// 在 loop() 中轮询,返回一次按键事件
Event poll();

}  // namespace buttons
