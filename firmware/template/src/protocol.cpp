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

void protocol::send_log(const char* msg) {
    JsonDocument doc;
    doc["evt"] = "log";
    doc["msg"] = msg;
    send_json(doc);
}

namespace {
constexpr size_t kMaxLine = 512;

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
        return;
    }
    if (strcmp(cmd, "off") == 0) { app::remote_light(-1); return; }

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
                mapped++;
            }
        }
        protocol::send_ready(mapped);
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
        else { len = 0; protocol::send_log("line too long"); }  // 超长直接丢弃
    }
}
