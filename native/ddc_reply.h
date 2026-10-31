/* Minimal Get VCP reply validation. Independent of Apple's private APIs. */
#ifndef MONITOR_SWITCH_DDC_REPLY_H
#define MONITOR_SWITCH_DDC_REPLY_H
#include <stddef.h>
#include <stdint.h>

static inline size_t monitor_switch_packet_length(const uint8_t *data, size_t capacity) {
    if (!capacity) return 0;
    size_t length = (data[0] & 0x7f) + 2;
    return length <= capacity ? length : 0;
}

static inline int monitor_switch_reply(const uint8_t *data, size_t length,
                                uint8_t expected_vcp, int *current, int *maximum) {
    if (length < 11 || data[0] != 0x6e || data[1] != 0x88 || data[2] != 0x02 ||
        data[3] != 0 || data[4] != expected_vcp) return 0;
    uint8_t checksum = 0x50;
    for (size_t i = 0; i < 11; ++i) checksum ^= data[i];
    if (checksum != 0) return 0;
    *maximum = ((int)data[6] << 8) | data[7];
    *current = ((int)data[8] << 8) | data[9];
    return 1;
}
#endif
