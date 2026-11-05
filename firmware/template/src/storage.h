/* NVS(Preferences)库存持久化:断电不丢 */
#pragma once
#include <Arduino.h>
#include <Preferences.h>

namespace storage {

void begin();
// 读取格位库存;NVS 无记录时返回 default_qty
uint32_t load_qty(uint16_t cell, uint32_t default_qty);
void save_qty(uint16_t cell, uint32_t qty);

}  // namespace storage
