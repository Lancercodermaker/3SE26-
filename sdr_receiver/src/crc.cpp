#include "sdr_receiver/crc.hpp"

namespace sdr_receiver {

uint8_t get_crc8(const std::vector<uint8_t>& data) {
    uint8_t crc = 0xff;
    for (uint8_t b : data) {
        crc ^= b;
        for (int i = 0; i < 8; ++i) {
            crc = (crc & 0x01) ? static_cast<uint8_t>((crc >> 1) ^ 0x8c) : static_cast<uint8_t>(crc >> 1);
        }
    }
    return crc;
}

uint16_t get_crc16(const std::vector<uint8_t>& data) {
    uint16_t crc = 0xffff;
    for (uint8_t b : data) {
        crc ^= static_cast<uint16_t>(b);
        for (int i = 0; i < 8; ++i) crc = (crc & 1) ? static_cast<uint16_t>((crc >> 1) ^ 0xA001) : static_cast<uint16_t>(crc >> 1);
    }
    return crc;
}

bool verify_crc8(const std::vector<uint8_t>& frame_prefix_with_crc) {
    if (frame_prefix_with_crc.size() < 2) return false;
    std::vector<uint8_t> data(frame_prefix_with_crc.begin(), frame_prefix_with_crc.end() - 1);
    return get_crc8(data) == frame_prefix_with_crc.back();
}

bool verify_crc16(const std::vector<uint8_t>& frame_with_crc) {
    if (frame_with_crc.size() < 3) return false;
    std::vector<uint8_t> data(frame_with_crc.begin(), frame_with_crc.end() - 2);
    const uint16_t expected = static_cast<uint16_t>(frame_with_crc[frame_with_crc.size() - 2]) |
                              (static_cast<uint16_t>(frame_with_crc.back()) << 8);
    return get_crc16(data) == expected;
}

} // namespace sdr_receiver
