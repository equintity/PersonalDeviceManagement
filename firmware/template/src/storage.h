/* NVS(Preferences)库存持久化:断电不丢 */
#pragma once
#include <Arduino.h>
#include <Preferences.h>

namespace storage {

void begin();
// 读取格位库存;NVS 无记录时返回 default_qty
uint32_t load_qty(uint16_t cell, uint32_t default_qty);
void save_qty(uint16_t cell, uint32_t qty);

// T5/QA-04:sync 基线(b<cell>)——记录"上次 sync 收到的值",
// 物理按键/qty 命令只改 q 不动 b,离线改动因此表现为 q != b
bool has_base(uint16_t cell);
uint32_t load_base(uint16_t cell, uint32_t default_qty);
void save_base(uint16_t cell, uint32_t qty);

}  // namespace storage
