#include "buttons.h"
#include "box_config.h"

namespace {
constexpr uint8_t kPinCount = 5;
const uint8_t kPins[kPinCount] = {
    BTN_PIN_UP, BTN_PIN_DOWN, BTN_PIN_LEFT, BTN_PIN_RIGHT, BTN_PIN_OK,
};
constexpr uint32_t kDebounceMs = 25;
constexpr uint32_t kRepeatMs = 260;      // 方向键按住自动重复
constexpr uint32_t kLongMs = BTN_LONG_PRESS_MS;

// 状态:0=up 1=down 2=left 3=right 4=ok
bool s_stable[kPinCount] = {false};
bool s_last_read[kPinCount] = {false};
uint32_t s_last_change[kPinCount] = {0};
uint32_t s_pressed_at[kPinCount] = {0};
uint32_t s_last_repeat[kPinCount] = {0};
bool s_long_fired = false;

Event event_for(uint8_t i, bool released_short) {
    switch (i) {
        case 0: return Event::Up;
        case 1: return Event::Down;
        case 2: return Event::Left;
        case 3: return Event::Right;
        default: return released_short ? Event::OkShort : Event::OkLong;
    }
}
}  // namespace

void buttons::begin() {
    for (uint8_t i = 0; i < kPinCount; i++) {
        pinMode(kPins[i], INPUT_PULLUP);
        s_last_read[i] = digitalRead(kPins[i]) == LOW;
        s_stable[i] = s_last_read[i];
        s_last_change[i] = millis();
    }
}

buttons::Event buttons::poll() {
    const uint32_t now = millis();
    for (uint8_t i = 0; i < kPinCount; i++) {
        const bool raw = digitalRead(kPins[i]) == LOW;
        if (raw != s_last_read[i]) {
            s_last_read[i] = raw;
            s_last_change[i] = now;
            continue;
        }
        if (raw != s_stable[i] && now - s_last_change[i] >= kDebounceMs) {
            s_stable[i] = raw;
            if (raw) {  // 按下
                s_pressed_at[i] = now;
                s_last_repeat[i] = now;
                s_long_fired = false;
                if (i != 4) return event_for(i, false);  // 方向键按下即触发
            } else {    // 释放
                if (i == 4 && !s_long_fired && now - s_pressed_at[i] < kLongMs)
                    return Event::OkShort;
            }
        }
        // 方向键长按自动重复 / OK 键长按触发
        if (s_stable[i]) {
            if (i == 4) {
                if (!s_long_fired && now - s_pressed_at[i] >= kLongMs) {
                    s_long_fired = true;
                    return Event::OkLong;
                }
            } else if (now - s_pressed_at[i] > kRepeatMs &&
                       now - s_last_repeat[i] >= kRepeatMs) {
                s_last_repeat[i] = now;
                return event_for(i, false);
            }
        }
    }
    return Event::None;
}
