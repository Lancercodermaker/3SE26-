#include "sdr_receiver/demodulator.hpp"
#include "sdr_receiver/bit_utils.hpp"
#include "sdr_receiver/config.hpp"
#include "sdr_receiver/crc.hpp"
#include "sdr_receiver/radar_wireless_data.hpp"
#include <algorithm>
#include <cstddef>
#include <cmath>
#include <numeric>
#include <optional>
#include <sstream>

namespace sdr_receiver {
namespace {

constexpr size_t AIR_CHUNK_BYTES = 15;
constexpr size_t AIR_CHUNK_BITS = AIR_CHUNK_BYTES * 8;
constexpr double PI = 3.14159265358979323846;

struct ParsedFrame {
    uint16_t command_id = 0;
    std::vector<uint8_t> payload;
    bool crc8_ok = false;
    bool crc16_ok = false;
    uint8_t sequence = 0;
    std::vector<uint8_t> frame_bytes;
};

std::optional<size_t> expected_payload_len(uint16_t command_id) {
    switch (command_id) {
        case CMD_RADAR_WIRELESS_POSITION: return 24;
        case CMD_RADAR_WIRELESS_HP: return 12;
        case CMD_RADAR_WIRELESS_PROJECTILE: return 10;
        case CMD_RADAR_WIRELESS_GOLD_OCCUPATION: return 8;
        case CMD_RADAR_WIRELESS_BUFF: return 36;
        case CMD_RADAR_WIRELESS_KEY: return 6;
    }
    return std::nullopt;
}

uint16_t read_u16_le_bytes(const std::vector<uint8_t>& bytes, size_t offset) {
    if (offset + 1 >= bytes.size()) return 0;
    return static_cast<uint16_t>(bytes[offset]) | (static_cast<uint16_t>(bytes[offset + 1]) << 8);
}

bool try_single_bit_crc16_fix(std::vector<uint8_t>& frame) {
    if (verify_crc16(frame)) return true;
    if (frame.size() > 64) return false;
    for (size_t byte_index = 0; byte_index + 2 < frame.size(); ++byte_index) {
        for (uint8_t bit = 0; bit < 8; ++bit) {
            frame[byte_index] ^= static_cast<uint8_t>(1u << bit);
            if (verify_crc16(frame)) return true;
            frame[byte_index] ^= static_cast<uint8_t>(1u << bit);
        }
    }
    return false;
}

std::optional<ParsedFrame> scan_byte_pool(std::vector<uint8_t>& pool, Stats& stats) {
    size_t i = 0;
    while (i + 5 <= pool.size()) {
        if (pool[i] != 0xA5) {
            ++i;
            continue;
        }

        std::vector<uint8_t> header(pool.begin() + static_cast<std::ptrdiff_t>(i),
                                    pool.begin() + static_cast<std::ptrdiff_t>(i + 5));
        if (!verify_crc8(header)) {
            ++stats.hdr_drop;
            ++i;
            continue;
        }
        ++stats.sof;

        const size_t payload_len = read_u16_le_bytes(pool, i + 1);
        if (payload_len > 64) {
            ++stats.len_drop;
            ++i;
            continue;
        }

        const size_t frame_len = 5 + 2 + payload_len + 2;
        if (i + frame_len > pool.size()) break;

        std::vector<uint8_t> frame(pool.begin() + static_cast<std::ptrdiff_t>(i),
                                   pool.begin() + static_cast<std::ptrdiff_t>(i + frame_len));
        const uint16_t command_id = read_u16_le_bytes(frame, 5);
        const auto expected_len = expected_payload_len(command_id);
        if (!expected_len.has_value() || *expected_len != payload_len) {
            ++stats.cmd_drop;
            ++i;
            continue;
        }

        bool crc16_ok = verify_crc16(frame);
        bool fixed = false;
        if (!crc16_ok) {
            std::vector<uint8_t> fixed_frame = frame;
            fixed = try_single_bit_crc16_fix(fixed_frame);
            if (fixed) {
                frame = std::move(fixed_frame);
                crc16_ok = true;
                ++stats.crc16_fix;
            }
        }
        if (!crc16_ok) {
            ++stats.crc16_fail;
            ++i;
            continue;
        }

        ParsedFrame parsed;
        parsed.command_id = command_id;
        parsed.crc8_ok = true;
        parsed.crc16_ok = true;
        parsed.sequence = frame[3];
        parsed.frame_bytes = frame;
        parsed.payload.assign(frame.begin() + 7, frame.begin() + static_cast<std::ptrdiff_t>(7 + payload_len));
        pool.erase(pool.begin(), pool.begin() + static_cast<std::ptrdiff_t>(i + frame_len));
        ++stats.crc8;
        ++stats.crc16;
        ++stats.asm_crc16;
        if (fixed) stats.last_data_change = "crc16 single-bit fixed";
        return parsed;
    }

    constexpr size_t MAX_POOL_BYTES = 768;
    constexpr size_t KEEP_POOL_BYTES = 384;
    if (pool.size() > MAX_POOL_BYTES) {
        pool.erase(pool.begin(), pool.end() - static_cast<std::ptrdiff_t>(KEEP_POOL_BYTES));
    } else if (i > 0) {
        const size_t drop = std::min(i, pool.size());
        pool.erase(pool.begin(), pool.begin() + static_cast<std::ptrdiff_t>(drop));
    }
    return std::nullopt;
}

std::string command_hex(uint16_t command_id) {
    std::ostringstream os;
    os << "0x" << std::hex << std::uppercase << command_id;
    return os.str();
}

std::string source_target_for(Target target, int level) {
    switch (target) {
        case Target::L1: return "JAM_L1_KEY";
        case Target::L2: return "JAM_L2_KEY";
        case Target::L3: return "JAM_L3_KEY";
        case Target::Info: return "INFO_UNDER_L" + std::to_string(std::clamp(level, 1, 3));
    }
    return "UNKNOWN";
}

}  // namespace

Demodulator::Demodulator(TuneConfig& tune, ReceiverState& state, SdrController& sdr_controller)
    : tune_(tune), state_(state), sdr_controller_(sdr_controller) {}

double Demodulator::median(std::vector<double> values) {
    if (values.empty()) return 0.0;
    const size_t n = values.size() / 2;
    std::nth_element(values.begin(), values.begin() + n, values.end());
    double m = values[n];
    if (values.size() % 2 == 0) {
        std::nth_element(values.begin(), values.begin() + n - 1, values.end());
        m = 0.5 * (m + values[n - 1]);
    }
    return m;
}

double Demodulator::plan_score(const std::tuple<double, double, int, char>& plan) const {
    const auto [k, threshold, shift, polarity] = plan;
    (void)threshold;
    const std::string key = make_pool_key(to_string(tune_.target), polarity, shift, k);
    auto it = state_.pool_scores.find(key);
    return it == state_.pool_scores.end() ? 0.0 : it->second;
}

std::vector<std::tuple<double, double, int, char>> Demodulator::prioritized_plans(
    const std::vector<std::tuple<double, double, int, char>>& plans) const {
    auto out = plans;
    std::sort(out.begin(), out.end(), [&](const auto& a, const auto& b) { return plan_score(a) > plan_score(b); });
    return out;
}

DemodResult Demodulator::fast_demod(const std::vector<Complex>& input, const std::string& ac_target) {
    DemodResult result;
    if (input.size() < 4) return result;

    const Target target = tune_.target;
    const FilterParams cfg = sdr_controller_.get_effective_filter_params(target);
    const RadioParams p = sdr_controller_.get_effective_radio_params();
    const RescueMode rescue_mode = sdr_controller_.get_info_rescue_mode(target);
    const bool info_rescue = rescue_mode != RescueMode::None;

    std::vector<Complex> rx_data = input;
    Complex mean{0.0f, 0.0f};
    for (const auto& x : rx_data) mean += x;
    mean /= static_cast<float>(rx_data.size());
    for (auto& x : rx_data) x -= mean;

    if (p.digital_shift != 0) {
        for (size_t n = 0; n < rx_data.size(); ++n) {
            const double angle = 2.0 * PI * static_cast<double>(p.digital_shift) * static_cast<double>(n) / config::SDR_FS;
            rx_data[n] *= Complex{static_cast<float>(std::cos(angle)), static_cast<float>(std::sin(angle))};
        }
    }

    auto filtered = dsp_.filter_iq(rx_data, cfg);
    if (filtered.size() < 2) return result;

    std::vector<double> freq(filtered.size() - 1);
    for (size_t i = 1; i < filtered.size(); ++i) {
        const Complex z = filtered[i] * std::conj(filtered[i - 1]);
        freq[i - 1] = std::atan2(z.imag(), z.real());
    }
    const double med = median(freq);
    for (auto& v : freq) v -= med;

    const int trend_len = static_cast<int>(config::SPS * cfg.trend_bits);
    if (freq.size() > static_cast<size_t>(trend_len * 2)) {
        auto trend = dsp_.moving_average(freq, trend_len);
        for (size_t i = 0; i < freq.size(); ++i) freq[i] -= trend[i];
    }

    const int smooth_len = std::max(5, static_cast<int>(config::SPS * cfg.smooth_frac));
    auto smoothed = dsp_.moving_average(freq, smooth_len);

    std::vector<double> threshold_values = info_rescue
        ? sdr_controller_.get_info_rescue_threshold_values(rescue_mode)
        : config::THRESHOLD_K_VALUES;
    auto thresholds = dsp_.threshold_grid(smoothed, threshold_values);

    std::vector<char> polarities = info_rescue ? sdr_controller_.get_info_rescue_polarities(rescue_mode) : std::vector<char>{'+'};
    std::vector<int> shifts = {0, config::SPS / 4, config::SPS / 2, (3 * config::SPS) / 4};

    std::vector<std::tuple<double, double, int, char>> plans;
    for (const auto& [k, threshold] : thresholds) {
        for (int shift : shifts) {
            for (char pol : polarities) plans.emplace_back(k, threshold, shift, pol);
        }
    }
    plans = prioritized_plans(plans);
    if (info_rescue && plans.size() > static_cast<size_t>(config::RESCUE_PLAN_LIMIT)) plans.resize(config::RESCUE_PLAN_LIMIT);

    const int max_errors = info_rescue ? sdr_controller_.get_info_rescue_search_errors(rescue_mode) : cfg.max_ac_errors;

    for (const auto& plan : plans) {
        const auto [k, threshold, shift, polarity] = plan;
        std::string bits;
        for (size_t i = static_cast<size_t>(std::max(0, shift)); i < smoothed.size(); i += config::SPS) {
            bool one = smoothed[i] > threshold;
            if (polarity == '-') one = !one;
            bits.push_back(one ? '1' : '0');
        }
        const auto candidates = find_access_candidates(bits, ac_target, max_errors, 16, false);
        result.candidates += static_cast<int>(candidates.size());
        if (!candidates.empty()) {
            state_.stats.ac += static_cast<int>(candidates.size());
            state_.stats.last_ac_time = now_sec();
            const std::string key = make_pool_key(to_string(tune_.target), polarity, shift, k);
            state_.pool_scores[key] += 1.0;
            state_.bit_pools[key] += bits;
            if (state_.bit_pools[key].size() > config::POOL_MAX_BITS) {
                state_.bit_pools[key].erase(0, state_.bit_pools[key].size() - config::POOL_KEEP_BITS);
            }
            result.locked = true;

            const int header_limit = info_rescue
                ? sdr_controller_.get_info_rescue_header_limit(rescue_mode)
                : (target == Target::Info ? config::INFO_HEADER_MAX_ERRORS : config::JAM_HEADER_MAX_ERRORS);
            for (const auto& [candidate_pos, ac_errors] : candidates) {
                (void)ac_errors;
                const size_t header_start = candidate_pos + ac_target.size();
                const size_t chunk_start = header_start + config::AIR_HEADER.size();
                if (chunk_start + AIR_CHUNK_BITS > bits.size()) {
                    ++state_.stats.frame_pending;
                    continue;
                }
                const std::string header_bits = bits.substr(header_start, config::AIR_HEADER.size());
                if (hamming_distance(header_bits, config::AIR_HEADER, header_limit) > header_limit) {
                    ++state_.stats.hdr_drop;
                    continue;
                }

                auto chunk = bits_to_bytes(bits.substr(chunk_start, AIR_CHUNK_BITS));
                auto& byte_pool = state_.byte_pools[key];
                byte_pool.insert(byte_pool.end(), chunk.begin(), chunk.end());
                ++state_.stats.asm_chunks;

                if (auto parsed = scan_byte_pool(byte_pool, state_.stats)) {
                    int source_level = state_.encrypt_lvl;
                    if (target == Target::Info) {
                        if (rescue_mode == RescueMode::L2) source_level = 2;
                        else if (rescue_mode == RescueMode::L3) source_level = 3;
                        else if (state_.final_key_submitted && state_.submitted_key_level > 0) source_level = state_.submitted_key_level;
                    }
                    result.has_frame = true;
                    result.command_id = parsed->command_id;
                    result.payload = parsed->payload;
                    result.crc8_passed = parsed->crc8_ok;
                    result.crc16_passed = parsed->crc16_ok;
                    result.air_chunk_index = static_cast<uint8_t>(state_.stats.asm_chunks & 0xff);
                    result.rm_sequence = parsed->sequence;
                    result.crc16_ok = 1;
                    result.source_target = source_target_for(target, source_level);
                    state_.stats.last_crc16_time = now_sec();
                    state_.stats.last_crc16_cmd = command_hex(parsed->command_id);
                    state_.stats.last_frame_hex = bytes_to_hex(parsed->frame_bytes);
                    state_.stats.last_frame_source = result.source_target;
                    state_.stats.last_frame_seq = std::to_string(parsed->sequence);
                    return result;
                }
            }
        }
    }
    return result;
}

} // namespace sdr_receiver
