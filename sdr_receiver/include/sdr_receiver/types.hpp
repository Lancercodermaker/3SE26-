#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <complex>
#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

namespace sdr_receiver {

using Complex = std::complex<float>;
using Clock = std::chrono::steady_clock;

inline double now_sec() {
    static const auto start = Clock::now();
    const auto dt = std::chrono::duration<double>(Clock::now() - start).count();
    return dt;
}

enum class Team { Red, Blue };
enum class Target { Info, L1, L2, L3 };
enum class RescueMode { None, L2, L3 };
enum class FilterKind { SymFft, AsymFft };
enum class ReceiverPhase { WaitingContext, DebugManual, CompetitionInit, JamDecode, WaitLevelUpdate, InfoDecode };

std::string to_string(Team team);
std::string to_string(Target target);
std::string to_string(RescueMode mode);
std::string to_string(ReceiverPhase phase);
Team team_from_string(const std::string& value);
Target target_from_string(const std::string& value);

struct FilterParams {
    FilterKind kind = FilterKind::SymFft;
    double cutoff = 0.0;
    double transition = 0.0;
    double pass_low = 0.0;
    double pass_high = 0.0;
    double stop_low = 0.0;
    double stop_high = 0.0;
    double smooth_frac = 0.34;
    int trend_bits = 16;
    int max_ac_errors = 2;
    std::string cache_key() const;
};

struct RadioParams {
    int64_t freq = 0;
    int64_t base_freq = 0;
    int64_t lo_offset = 0;
    int64_t digital_shift = 0;
    int gain = 0;
    int gain_floor = 0;
    int rf_bw = 0;
    std::string ac;
    std::string mode = "normal";
    int64_t rx_lo = 0;
};

struct CalibrationProfile {
    RescueMode rescue = RescueMode::None;
    int offset = 0;
    int gain = 0;
    int rf_bw = 0;
    std::string filter_name;
    FilterParams filter_params;
    std::string label;
};

struct CalibrationResult {
    CalibrationProfile profile;
    std::string stage;
    double dwell_sec = 0.0;
    double score = 0.0;
    int ac = 0;
    int crc8 = 0;
    int crc16 = 0;
    int crc16_fail = 0;
    std::string quality;
};

struct RadioProfile {
    bool valid = false;
    std::string match_slot;
    std::string front_end_id;
    std::string team;
    std::string target_key;
    int64_t rx_lo = 0;
    int64_t digital_shift = 0;
    int rf_bw = 0;
    int gain = 0;
    std::string filter_name;
    FilterParams filter_params;
    bool has_filter_params = false;
};

struct JudgeContext {
    bool received = false;
    bool valid_self_id = false;
    bool valid_level = false;
    uint8_t self_id = 0;
    Team team = Team::Red;
    uint8_t radar_info_raw = 0;
    uint8_t jam_level = 0;
    bool key_mutable = false;
    uint8_t game_progress = 0;
    int16_t match_time = 0;
    bool referee_online = false;
    double last_update_sec = 0.0;
};

struct CalibrationState {
    bool active = false;
    std::vector<CalibrationProfile> queue;
    int index = -1;
    std::vector<CalibrationResult> results;
    std::vector<CalibrationResult> seed_results;
    double step_start = 0.0;
    std::string log_path;
    std::optional<CalibrationResult> best;
    double last_failover = 0.0;
    std::string stage = "idle";
    std::string scope = "ALL";
    bool full = false;
    double dwell_sec = 2.5;
    int validate_top_k = 6;
    int validate_rounds = 2;
    int fallback_index = 0;
};

struct Stats {
    int ac = 0;
    int sof = 0;
    int crc8 = 0;
    int crc16 = 0;
    int ac_raw = 0;
    int hdr_drop = 0;
    int len_drop = 0;
    int cmd_drop = 0;
    int crc16_fail = 0;
    int crc16_fix = 0;
    int asm_chunks = 0;
    int asm_crc16 = 0;
    int frame_reject = 0;
    int frame_pending = 0;
    double last_log = 0.0;
    double last_packet_log = 0.0;
    double last_ac_time = 0.0;
    double last_crc16_time = 0.0;
    double loop_ms = 0.0;
    double rx_ms = 0.0;
    double demod_ms = 0.0;
    double adc_rms = 0.0;
    int rx_gain = 0;
    int gain_ceiling = 0;
    std::string gain_note = "init";
    std::string rf_state = "INIT";
    std::string rf_advice;
    std::string jam_rf_source;
    double jam_rf_conf = 0.0;
    double jam_rf_offset = 0.0;
    double jam_rf_target_offset = 0.0;
    int jam_rf_match_streak = 0;
    double jam_rf_target_changed = 0.0;
    std::string jam_rf_levels;
    std::string last_frame_hex;
    std::string last_frame_source;
    std::string last_frame_seq;
    double last_cfg_time = 0.0;
    std::string dsp_mode = "normal";
    std::string last_error;
    std::string last_crc16_cmd = "none";
    double last_data_update = 0.0;
    std::string last_data_change = "none";
    double last_gain_adjust = 0.0;
    std::string run_mode = "DEBUG";
    bool keyboard_enabled = true;
    std::string receiver_phase = "WaitingContext";
    std::string active_profile = "none";
    uint8_t context_self_id = 0;
    uint8_t context_jam_level = 0;
    uint8_t radar_info_raw = 0;
    bool key_mutable = false;
};

struct TuneConfig {
    Team team = Team::Red;
    Target target = Target::Info;
};

struct DataModel {
    std::map<std::string, std::pair<int, int>> pos;
    std::map<std::string, int> hp;
    std::map<std::string, int> ammo;
    std::map<std::string, std::array<int, 5>> buff;
    int coin_rem = 0;
    int coin_tot = 0;
    uint32_t occupation_raw = 0;
    int sentry_posture = 0;
    std::string id = "0x0000";
    std::string bit_pool;
};

struct ReceiverState {
    bool info_l3_rescue = false;
    bool info_l2_rescue = false;
    int info_l3_rescue_offset_index = 0;
    int info_l2_rescue_offset_index = 0;
    std::optional<CalibrationProfile> cal_profile;
    CalibrationState cal;
    std::map<Target, int> manual_rx_gains;
    std::map<Target, std::string> jam_keys;
    std::map<Target, int> jam_keys_cnt;
    int encrypt_lvl = 1;
    Stats stats;
    std::map<std::string, std::string> bit_pools;
    std::map<std::string, std::vector<uint8_t>> byte_pools;
    std::map<std::string, double> pool_scores;
    std::map<std::string, std::string> pending_frames;
    ReceiverPhase phase = ReceiverPhase::WaitingContext;
    JudgeContext judge_context;
    std::optional<RadioProfile> active_profile;
    int max_jam_break_level = 3;
    int submitted_key_level = 0;
    bool final_key_submitted = false;
    double wait_level_update_since = 0.0;
};

struct DemodResult {
    bool locked = false;
    int candidates = 0;
    int crc16_ok = 0;
    bool has_frame = false;
    uint16_t command_id = 0;
    std::vector<uint8_t> payload;
    bool crc8_passed = false;
    bool crc16_passed = false;
    uint8_t air_chunk_index = 0;
    uint8_t rm_sequence = 0;
    std::string source_target;
};

class ISdrDevice {
public:
    virtual ~ISdrDevice() = default;
    virtual void set_rx_lo(int64_t hz) = 0;
    virtual void set_rx_rf_bandwidth(int hz) = 0;
    virtual void set_rx_gain(int db) = 0;
    virtual std::vector<Complex> receive(size_t samples) = 0;
};

class MockSdrDevice final : public ISdrDevice {
public:
    int64_t rx_lo = 0;
    int rf_bw = 0;
    int gain = 0;
    void set_rx_lo(int64_t hz) override { rx_lo = hz; }
    void set_rx_rf_bandwidth(int hz) override { rf_bw = hz; }
    void set_rx_gain(int db) override { gain = db; }
    std::vector<Complex> receive(size_t samples) override { return std::vector<Complex>(samples, Complex{0.0f, 0.0f}); }
};

} // namespace sdr_receiver
