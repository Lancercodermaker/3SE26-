#include "sdr_receiver/bit_utils.hpp"
#include <iomanip>
#include <sstream>

namespace sdr_receiver {

std::vector<uint8_t> bits_to_bytes(const std::string& bits) {
    std::vector<uint8_t> out;
    out.reserve(bits.size() / 8);
    for (size_t i = 0; i + 7 < bits.size(); i += 8) {
        uint8_t v = 0;
        for (size_t j = 0; j < 8; ++j) {
            v <<= 1;
            if (bits[i + j] == '1') v |= 1;
        }
        out.push_back(v);
    }
    return out;
}

int hamming_distance(const std::string& a, const std::string& b, int stop_after) {
    int dist = 0;
    const size_t n = std::min(a.size(), b.size());
    for (size_t i = 0; i < n; ++i) {
        if (a[i] != b[i]) {
            ++dist;
            if (stop_after >= 0 && dist > stop_after) return dist;
        }
    }
    dist += static_cast<int>(std::max(a.size(), b.size()) - n);
    return dist;
}

std::vector<std::pair<size_t, int>> find_access_candidates(
    const std::string& bits,
    const std::string& ac_target,
    int max_errors,
    size_t max_candidates,
    bool exact_only) {
    std::vector<std::pair<size_t, int>> candidates;
    size_t pos = bits.find(ac_target);
    while (pos != std::string::npos && candidates.size() < max_candidates) {
        candidates.emplace_back(pos, 0);
        pos = bits.find(ac_target, pos + 1);
    }
    if (!candidates.empty() || exact_only || max_errors <= 0) return candidates;
    if (bits.size() < ac_target.size()) return candidates;
    const size_t limit = bits.size() - ac_target.size();
    for (size_t i = 0; i <= limit && candidates.size() < max_candidates; ++i) {
        const int dist = hamming_distance(bits.substr(i, ac_target.size()), ac_target, max_errors);
        if (dist <= max_errors) candidates.emplace_back(i, dist);
    }
    return candidates;
}

std::string bytes_to_hex(const std::vector<uint8_t>& data) {
    std::ostringstream os;
    os << std::hex << std::setfill('0');
    for (auto b : data) os << std::setw(2) << static_cast<int>(b);
    return os.str();
}

std::string make_pool_key(const std::string& target, char polarity, int shift, double k) {
    std::ostringstream os;
    os << target << ':' << polarity << ':' << shift << ':' << std::fixed << std::setprecision(2) << k;
    return os.str();
}

} // namespace sdr_receiver
