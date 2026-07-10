#pragma once
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace sdr_receiver {

std::vector<uint8_t> bits_to_bytes(const std::string& bits);
int hamming_distance(const std::string& a, const std::string& b, int stop_after = -1);
std::vector<std::pair<size_t, int>> find_access_candidates(
    const std::string& bits,
    const std::string& ac_target,
    int max_errors,
    size_t max_candidates = 16,
    bool exact_only = false);
std::string bytes_to_hex(const std::vector<uint8_t>& data);
std::string make_pool_key(const std::string& target, char polarity, int shift, double k);

} // namespace sdr_receiver
