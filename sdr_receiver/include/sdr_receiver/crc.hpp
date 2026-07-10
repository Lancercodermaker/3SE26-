#pragma once
#include <cstdint>
#include <vector>
namespace sdr_receiver {
uint8_t get_crc8(const std::vector<uint8_t>& data);
uint16_t get_crc16(const std::vector<uint8_t>& data);
bool verify_crc8(const std::vector<uint8_t>& frame_prefix_with_crc);
bool verify_crc16(const std::vector<uint8_t>& frame_with_crc);
}
