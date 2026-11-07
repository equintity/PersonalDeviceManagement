#include "storage.h"
#include "box_config.h"

static Preferences s_prefs;

void storage::begin() { s_prefs.begin("pdlbox", false); }

uint32_t storage::load_qty(uint16_t cell, uint32_t default_qty) {
    char key[8];
    snprintf(key, sizeof(key), "q%u", static_cast<unsigned>(cell));
    return s_prefs.getUShort(key, static_cast<uint16_t>(min(default_qty, 65535u)));
}

void storage::save_qty(uint16_t cell, uint32_t qty) {
    char key[8];
    snprintf(key, sizeof(key), "q%u", static_cast<unsigned>(cell));
    s_prefs.putUShort(key, static_cast<uint16_t>(min(qty, 65535u)));
}

bool storage::has_base(uint16_t cell) {
    char key[8];
    snprintf(key, sizeof(key), "b%u", static_cast<unsigned>(cell));
    return s_prefs.isKey(key);
}

uint32_t storage::load_base(uint16_t cell, uint32_t default_qty) {
    char key[8];
    snprintf(key, sizeof(key), "b%u", static_cast<unsigned>(cell));
    return s_prefs.getUShort(key, static_cast<uint16_t>(min(default_qty, 65535u)));
}

void storage::save_base(uint16_t cell, uint32_t qty) {
    char key[8];
    snprintf(key, sizeof(key), "b%u", static_cast<unsigned>(cell));
    s_prefs.putUShort(key, static_cast<uint16_t>(min(qty, 65535u)));
}
