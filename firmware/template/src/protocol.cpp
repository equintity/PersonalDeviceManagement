#include "protocol.h"
#include <ArduinoJson.h>
#include "box_config.h"

static Stream* s_serial = nullptr;

// 由 main.cpp 提供的实现(库存与映射的真正执行者)
namespace app {
int32_t qty_of(uint16_t cell);            // 未映射返回 -1
void set_qty(uint16_t cell, uint32_t qty, bool persist, bool notify_host);
uint16_t first_mapped_cell();
void remote_light(int16_t cell);          // -1 = 熄灭
uint32_t base_of(uint16_t cell);          // T5:sync 基线
void set_base(uint16_t cell, uint32_t qty);
void set_name(uint16_t cell, const char* name);   // T8:名称随 sync 下发
}  // namespace app

void protocol::begin(Stream& serial) { s_serial = &serial; }

static void send_json(const JsonDocument& doc) {
    if (!s_serial) return;
    serializeJson(doc, *s_serial);
    s_serial->print('\n');
}

void protocol::send_hello() {
    JsonDocument doc;
    doc["evt"] = "hello";
    doc["device"] = DEVICE_NAME;
    doc["rows"] = BOX_ROWS;
    doc["cols"] = BOX_COLS;
    doc["cells"] = BOX_CELLS;
    doc["mappings"] = MAPPING_COUNT;
    doc["fw"] = "1.0.0";
    send_json(doc);
}

void protocol::send_pong() {
    JsonDocument doc;
    doc["evt"] = "pong";
    doc["t"] = millis();
    send_json(doc);
}

void protocol::send_ready(uint16_t mapped) {
    JsonDocument doc;
    doc["evt"] = "ready";
    doc["mapped"] = mapped;
    send_json(doc);
}

void protocol::send_stock(const char* evt, uint16_t cell, uint32_t qty) {
    JsonDocument doc;
    doc["evt"] = evt;
    doc["cell"] = cell;
    doc["qty"] = qty;
    for (int i = 0; i < MAPPING_COUNT; i++) {
        if (BOX_MAPPINGS[i].cell == cell) {
            doc["code"] = BOX_MAPPINGS[i].code;
            break;
        }
    }
    send_json(doc);
}

void protocol::send_cursor(uint16_t cell) {
    JsonDocument doc;
    doc["evt"] = "cursor";
    doc["cell"] = cell;
    send_json(doc);
}

void protocol::send_lit(int16_t cell) {
    JsonDocument doc;
    doc["evt"] = "lit";
    doc["cell"] = cell;
    send_json(doc);
}

void protocol::send_log(const char* msg) {
    JsonDocument doc;
    doc["evt"] = "log";
    doc["msg"] = msg;
    send_json(doc);
}

namespace {
constexpr size_t kMaxLine = 1024;   // T-M4-01:512→1024,容纳分帧 sync(每批 ≤8 项)

// 严格取整数:类型不符或越界返回 false
bool get_int(const JsonDocument& doc, const char* key, long min_v, long max_v, long& out) {
    if (!doc[key].is<long>()) return false;
    const long v = doc[key].as<long>();
    if (v < min_v || v > max_v) return false;
    out = v;
    return true;
}

void handle_line(const char* line) {
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, line, kMaxLine);
    if (err) { protocol::send_log("json error"); return; }

    const char* cmd = doc["cmd"] | "";
    if (strcmp(cmd, "ping") == 0) { protocol::send_pong(); return; }
    if (strcmp(cmd, "info") == 0) { protocol::send_hello(); return; }

    if (strcmp(cmd, "light") == 0) {
        long cell;
        if (!get_int(doc, "cell", 0, BOX_CELLS - 1, cell)) {
            protocol::send_log("bad cell"); return;
        }
        app::remote_light(static_cast<int16_t>(cell));
        protocol::send_lit(static_cast<int16_t>(cell));   // 点亮回执
        return;
    }
    if (strcmp(cmd, "off") == 0) {
        app::remote_light(-1);
        protocol::send_lit(-1);
        return;
    }

    if (strcmp(cmd, "qty") == 0) {
        long cell, qty;
        if (!get_int(doc, "cell", 0, BOX_CELLS - 1, cell) ||
            !get_int(doc, "qty", 0, 65535, qty)) {
            protocol::send_log("bad qty cmd"); return;
        }
        app::set_qty(static_cast<uint16_t>(cell), static_cast<uint32_t>(qty),
                     true, false);
        return;
    }

    if (strcmp(cmd, "sync") == 0) {
        // T-M4-01:增量分帧 sync —— seq/total 分批送达,固件累计,
        // 末批(seq >= total)统一回 ready{mapped: 累计};无 seq 字段时按单批兼容
        static uint16_t accum = 0;
        static bool active = false;
        const long seq = doc["seq"] | 1;
        const long total = doc["total"] | 1;
        if (seq <= 1) { accum = 0; active = true; }
        uint16_t mapped = 0;
        JsonArrayConst items = doc["items"].as<JsonArrayConst>();
        if (!items.isNull()) {
            uint16_t count = 0;
            for (JsonObjectConst item : items) {
                if (++count > BOX_CELLS) break;      // 硬上限,防恶意超长
                const long cell = item["cell"] | -1;
                const long qty = item["qty"] | -1;
                if (cell < 0 || cell >= BOX_CELLS || qty < 0 || qty > 65535) continue;
                app::set_qty(static_cast<uint16_t>(cell), static_cast<uint32_t>(qty),
                             true, false);
                app::set_base(static_cast<uint16_t>(cell),
                              static_cast<uint32_t>(qty));   // T5:基线=host 值
                app::set_name(static_cast<uint16_t>(cell),
                              item["name"] | "");            // T8:名称下发
                mapped++;
            }
        }
        accum += mapped;
        if (seq >= total) {
            if (active) protocol::send_ready(accum);
            accum = 0;
            active = false;
        }
        return;
    }

    if (strcmp(cmd, "take") == 0) {
        long cell;
        if (!get_int(doc, "cell", 0, BOX_CELLS - 1, cell)) {
            protocol::send_log("bad cell"); return;
        }
        const int32_t q = app::qty_of(static_cast<uint16_t>(cell));
        if (q > 0) app::set_qty(static_cast<uint16_t>(cell),
                                static_cast<uint32_t>(q - 1), true, true);
        return;
    }

    if (strcmp(cmd, "stock") == 0) {
        // T5/QA-04:返回 q != b 的已映射格,8 项/帧;无差异也回一帧空 items
        static uint16_t diffs[BOX_CELLS];
        uint16_t n = 0;
        for (uint16_t cell = 0; cell < BOX_CELLS && n < BOX_CELLS; cell++) {
            const int32_t q = app::qty_of(cell);
            if (q < 0) continue;                        // 未映射格跳过
            if (static_cast<uint32_t>(q) != app::base_of(cell))
                diffs[n++] = cell;
        }
        const uint16_t total = (n + 7) / 8 > 0 ? (n + 7) / 8 : 1;
        for (uint16_t seq = 1; seq <= total; seq++) {
            JsonDocument doc;
            doc["evt"] = "stock";
            doc["seq"] = seq;
            doc["total"] = total;
            doc["complete"] = (seq == total);
            JsonArray arr = doc["items"].to<JsonArray>();
            const uint16_t start = (seq - 1) * 8;
            for (uint8_t k = 0; k < 8; k++) {
                const uint16_t idx = start + k;
                if (idx >= n) break;
                JsonObject o = arr.add<JsonObject>();
                o["cell"] = diffs[idx];
                o["qty"] = app::qty_of(diffs[idx]);
                for (int i = 0; i < MAPPING_COUNT; i++) {
                    if (BOX_MAPPINGS[i].cell == diffs[idx]) {
                        o["code"] = BOX_MAPPINGS[i].code;
                        break;
                    }
                }
            }
            send_json(doc);
        }
        return;
    }

    protocol::send_log("unknown cmd");
}
}  // namespace

void protocol::poll() {
    if (!s_serial) return;
    static char buf[kMaxLine + 1];
    static size_t len = 0;
    while (s_serial->available()) {
        const char c = static_cast<char>(s_serial->read());
        if (c == '\n') {
            buf[len] = '\0';
            if (len > 0) handle_line(buf);
            len = 0;
            continue;
        }
        if (c == '\r' || c == '\t' || static_cast<uint8_t>(c) < 0x20) continue;
        if (len < kMaxLine) buf[len++] = c;
        else {
            // T-M4-01:超长行不再静默丢弃,回显明确错误事件
            len = 0;
            protocol::send_log("error: line too long, dropped");
        }
    }
}
