/* 串口 JSON 协议(换行分隔,与 PersonalDeviceManagement 软件联动) */
#pragma once
#include <Arduino.h>

namespace protocol {

void begin(Stream& serial);
void poll();  // loop() 中调用:读取并分发下行命令

// ---- 上行事件 ----
void send_hello();
void send_pong();
void send_ready(uint16_t mapped);
void send_stock(const char* evt, uint16_t cell, uint32_t qty);  // take / return
void send_log(const char* msg);

}  // namespace protocol
